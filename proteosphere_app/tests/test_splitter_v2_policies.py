"""Smoke test for the v2-policy-compatible split lanes added to
``proteosphere.splitter.split_dataset``.

This test ensures every policy in ``SUPPORTED_POLICIES`` produces a
non-blocked split on a small synthetic manifest, and that group policies
keep groups disjoint across train/val/test. It is intentionally cheap to
run (no network, no warehouse downloads) so CI can include it in the
default test sweep.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python tests/test_splitter_v2_policies.py` from the repo root
# without an editable install: prepend the package source to sys.path.
HERE = Path(__file__).resolve()
SRC = HERE.parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from proteosphere.model import DatasetManifest, DatasetRecord  # noqa: E402
from proteosphere.splitter import (  # noqa: E402
    SUPPORTED_POLICIES,
    _V2_POLICY_ALIASES,
    split_dataset,
)
from proteosphere.warehouse import ProteinResolution  # noqa: E402


class _MiniWarehouse:
    """Tiny stub that satisfies the splitter's Warehouse interface.

    For the UniRef-grouped + cluster + leakage-aware policies, we need
    to provide a non-empty ProteinResolution with a uniref50 cluster id,
    or the splitter's group-key builder will short-circuit to a missing
    key and block the split. The mapping is deterministic from the
    accession's last digit so the test stays reproducible.
    """

    def resolve_proteins(self, accessions):
        out = {}
        for a in accessions:
            if not a:
                continue
            # Bucket 10 synthetic accessions into 3 uniref clusters so
            # the splitter has something to chew on AND so the union-find
            # produces multiple distinct groups.
            try:
                last = int(a[-1])
                bucket = last % 3
            except ValueError:
                bucket = 0
            out[a] = ProteinResolution(
                accession=a,
                protein_ref=a,
                uniref50=f"UR50_{bucket}",
                uniref90=f"UR90_{bucket}",
                uniref100=f"UR100_{bucket}",
                resolved=True,
            )
        return out

    def resolve_ligands(self, _ligands):
        return {}

    def resolve_structure_chains(self, _requests):
        return {}


def _make_manifest(n: int = 60) -> DatasetManifest:
    """Build a 60-record manifest with controlled overlap structure:
      - protein_accession cycles every 6 rows  (10 distinct proteins)
      - ligand_id cycles every 4 rows           (15 distinct ligands)
      - label alternates 0/1                    (clean binary stratify)
      - year ranges 2018..2024 (oldest first)   (clean time-split)
      - ligand_smiles is a tiny aromatic series so RDKit can derive a
        Murcko scaffold when available.
    """
    smiles_pool = [
        "c1ccccc1",         # benzene
        "c1ccncc1",         # pyridine
        "c1ccc2ccccc2c1",   # naphthalene
        "c1ccc(O)cc1",      # phenol
    ]
    records = []
    for i in range(n):
        protein = f"P{i % 10:05d}"
        ligand_id = f"L{i % 15:03d}"
        records.append(DatasetRecord(
            record_id=f"r{i:03d}",
            protein_accessions=(protein,),
            ligand_id=ligand_id,
            ligand_smiles=smiles_pool[i % len(smiles_pool)],
            label_value=float(i % 7),
            split="",
            extra_metadata={
                "label": str(i % 2),
                "year": 2018 + (i // 10),
            },
        ))
    return DatasetManifest(
        manifest_id="smoke",
        title="Synthetic smoke-test manifest",
        task_type="regression",
        label_type="continuous",
        entity_kind="protein_ligand",
        split_membership_mode="proteosphere_random",
        records=tuple(records),
        notes=(),
    )


def _assert_groups_disjoint(result: dict, policy: str) -> None:
    """Assert that no group key crosses train/val/test boundaries."""
    diag = result["diagnostics"]
    crossing = diag.get("crossing_groups", []) or []
    assert not crossing, (
        f"policy={policy} produced groups that cross splits: {crossing[:5]}"
    )


def test_every_supported_policy_runs() -> None:
    """Every entry in SUPPORTED_POLICIES must produce status='ready' on
    the synthetic manifest. Group policies additionally must keep groups
    disjoint across train/val/test."""
    manifest = _make_manifest()
    warehouse = _MiniWarehouse()
    for policy in sorted(SUPPORTED_POLICIES):
        result = split_dataset(
            manifest,
            warehouse,
            policy=policy,
            fractions=(0.7, 0.1, 0.2),
            seed=42,
            # stratified needs a column hint via subgroup_column
            subgroup_column=("label" if policy == "stratified" else None),
        )
        assert result["status"] == "ready", (
            f"policy={policy} did not produce a ready split: "
            f"{result.get('diagnostics', {}).get('blockers', result)}"
        )
        counts = result["diagnostics"]["split_counts"]
        assert sum(counts.values()) == len(manifest.records), (
            f"policy={policy}: split_counts {counts} don't sum to "
            f"{len(manifest.records)}"
        )
        # Group policies must be group-disjoint.
        group_policies = {
            "accession_grouped", "uniref_grouped", "ligand_identity_grouped",
            "protein_ligand_component_grouped", "scaffold_grouped",
            "cold-target", "cold-drug", "cold-pair", "cluster", "leakage-aware",
            "scaffold",
        }
        if policy in group_policies:
            _assert_groups_disjoint(result, policy)


def test_v2_aliases_map_to_canonical_policies() -> None:
    """The v2-trainer alias names must resolve to the canonical splitter
    policy in the diagnostics["policy"] field while preserving the
    requested alias under ["requested_policy"]."""
    manifest = _make_manifest()
    warehouse = _MiniWarehouse()
    for alias, canonical in _V2_POLICY_ALIASES.items():
        result = split_dataset(
            manifest, warehouse,
            policy=alias,
            fractions=(0.7, 0.1, 0.2),
            seed=7,
        )
        assert result["status"] == "ready", (
            f"alias={alias} → blocked: {result['diagnostics']['blockers']}"
        )
        diag = result["diagnostics"]
        assert diag["requested_policy"] == alias
        # The non-group aliases (random/stratified/time-split) don't pass
        # through _V2_POLICY_ALIASES at all, so they don't appear here.
        assert diag["policy"] == canonical, (
            f"alias={alias} expected canonical={canonical}, "
            f"got {diag['policy']}"
        )


def test_time_split_orders_by_year() -> None:
    """time-split must put older rows in train and newer in test."""
    manifest = _make_manifest()
    warehouse = _MiniWarehouse()
    result = split_dataset(
        manifest, warehouse,
        policy="time-split",
        fractions=(0.7, 0.1, 0.2),
        seed=0,
    )
    assert result["status"] == "ready"
    out_records = result["manifest"]["records"]
    train_years = [
        int(r.get("extra_metadata", {}).get("year", 0))
        for r in out_records if r["split"] == "train"
    ]
    test_years = [
        int(r.get("extra_metadata", {}).get("year", 0))
        for r in out_records if r["split"] == "test"
    ]
    # The newest train year must not exceed the oldest test year by more
    # than rounding noise (within 1 because val sits between them and may
    # span a year boundary).
    if train_years and test_years:
        assert max(train_years) <= min(test_years) + 1, (
            f"time-split inverted: max(train)={max(train_years)} "
            f"vs min(test)={min(test_years)}"
        )


if __name__ == "__main__":
    test_every_supported_policy_runs()
    print("OK  test_every_supported_policy_runs")
    test_v2_aliases_map_to_canonical_policies()
    print("OK  test_v2_aliases_map_to_canonical_policies")
    test_time_split_orders_by_year()
    print("OK  test_time_split_orders_by_year")
    print("\nAll splitter v2-policy smoke tests PASSED.")
