"""Freestanding cross-benchmark audit driver.

Runs the leakage-signature audit between two benchmark partitions, using
only the warehouse and zero hardcoded paths. Drop-in replacement for the
prior ``scripts/pinder_plinder_cross_audit_with_uniref.py`` that fully
honors :class:`proteosphere.config.Config`.

Usage::

    from proteosphere import Config
    from proteosphere.audit import audit_partition_overlap

    config = Config.discover()
    sig = audit_partition_overlap(
        config=config,
        test_label="my_benchmark.test",
        test_accessions={"P00533", "P04637"},
        test_pdb_ids={"4EQ6", "1XYZ"},
        comparison_label="my_other_benchmark.train",
        comparison_accessions={"P00533", "P11111"},
        comparison_pdb_ids={"4EQ6"},
    )
    # sig is a leakage_signature dict ready to write as JSON
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Iterable

from proteosphere.config import Config
from proteosphere.warehouse import Warehouse, _norm_acc

LEAKAGE_SCHEMA = "proteosphere-leakage-signature-v1"
SIX_VERDICTS = (
    "usable",
    "usable_with_caveats",
    "audit_only",
    "blocked_pending_mapping",
    "blocked_pending_cleanup",
    "unsafe_for_training",
)


def _stable_id(parts: list[str]) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _verdict_for(composite: float) -> tuple[str, list[str]]:
    if composite >= 0.30:
        return "unsafe_for_training", [
            "DIRECT_OVERLAP",
            "ACCESSION_ROOT_OVERLAP",
            "UNIREF_CLUSTER_OVERLAP",
        ]
    if composite >= 0.10:
        return "usable_with_caveats", ["UNIREF_CLUSTER_OVERLAP"]
    if composite >= 0.02:
        return "usable_with_caveats", ["UNIREF_CLUSTER_OVERLAP"]
    return "usable", []


def _axis(test_set: set[str], comp_set: set[str]) -> dict[str, object]:
    overlap = test_set & comp_set
    return {
        "overlap_count": len(overlap),
        "overlap_fraction": len(overlap) / max(len(test_set), 1),
        "examples": sorted(overlap)[:20],
    }


# Cache the per-accession Pfam / InterPro lookup tables once per Python
# session so cross-audit + intra-audit runs share the read. The motif
# partition has ~1M Pfam + ~4M InterPro rows; building the reverse
# index takes ~5-10s but lookup is then O(1).
_PFAM_BY_ACCESSION_CACHE: dict[str, set[str]] | None = None
_INTERPRO_BY_ACCESSION_CACHE: dict[str, set[str]] | None = None


def _ensure_motif_index(warehouse: Warehouse, namespace: str) -> dict[str, set[str]]:
    """Build (or fetch from cache) the per-accession motif index for
    ``namespace`` ∈ {"Pfam", "InterPro"}.

    Warehouse rows are keyed by structure-unit id (
    ``structure_unit:pdb:<PDB>:<CHAIN>:<UNIPROT>``), NOT by raw accession,
    so we parse the trailing colon-separated UniProt token to assemble
    the {accession: set(family_id)} reverse index. This is the
    correct way to query because Pfam is annotated per chain/structural
    unit, not per protein in the abstract.
    """
    global _PFAM_BY_ACCESSION_CACHE, _INTERPRO_BY_ACCESSION_CACHE
    cache = (_PFAM_BY_ACCESSION_CACHE if namespace == "Pfam"
             else _INTERPRO_BY_ACCESSION_CACHE)
    if cache is not None:
        return cache
    try:
        import pyarrow.parquet as pq
        p = warehouse.config.family_partition("motif_domain_site_annotations")
        if not p.is_file():
            return {}
        tbl = pq.read_table(
            p,
            columns=["owner_summary_id", "namespace", "identifier"],
            filters=[("namespace", "=", namespace)],
        )
        df = tbl.to_pandas()
        if df.empty:
            return {}
        idx: dict[str, set[str]] = {}
        for owner_id, identifier in zip(
            df["owner_summary_id"].astype(str),
            df["identifier"].astype(str),
        ):
            # owner_id = "structure_unit:pdb:<PDB>:<CHAIN>:<UNIPROT>"
            # The trailing token is the UniProt accession.
            tail = owner_id.rsplit(":", 1)[-1]
            if tail and tail != "nan":
                acc = _norm_acc(tail)
                if acc:
                    idx.setdefault(acc, set()).add(identifier)
        if namespace == "Pfam":
            _PFAM_BY_ACCESSION_CACHE = idx
        else:
            _INTERPRO_BY_ACCESSION_CACHE = idx
        return idx
    except Exception:
        return {}


def _pfam_set(warehouse: Warehouse, accessions: set[str]) -> tuple[set[str], int]:
    """Union of Pfam family identifiers for ``accessions``.

    The motif partition keys by structure-unit id; we use the cached
    reverse index built once per Python session.
    """
    if not accessions:
        return set(), 0
    idx = _ensure_motif_index(warehouse, "Pfam")
    if not idx:
        return set(), len(accessions)
    fams: set[str] = set()
    seen = 0
    for acc in accessions:
        if acc in idx:
            fams |= idx[acc]
            seen += 1
    return fams, len(accessions) - seen


def _interpro_set(warehouse: Warehouse, accessions: set[str]) -> tuple[set[str], int]:
    """Same as _pfam_set but at the broader InterPro superfamily level."""
    if not accessions:
        return set(), 0
    idx = _ensure_motif_index(warehouse, "InterPro")
    if not idx:
        return set(), len(accessions)
    fams: set[str] = set()
    seen = 0
    for acc in accessions:
        if acc in idx:
            fams |= idx[acc]
            seen += 1
    return fams, len(accessions) - seen


def _ligand_chem_axes(warehouse: Warehouse,
                      ligand_refs: Iterable[str]) -> dict[str, set[str]]:
    """Build three ligand-chemistry sets (exact_identity_group +
    chemical_series_group + canonical_smiles_hash) from the warehouse's
    ligand_chemistry_signatures partition. Empty / missing → all empty
    sets and the audit records each as ``axes_unavailable``."""
    refs = {str(r).strip() for r in ligand_refs if str(r).strip()}
    out = {"ligand_exact_identity": set(),
           "ligand_chemical_series": set(),
           "ligand_canonical_smiles_hash": set()}
    if not refs:
        return out
    try:
        sigs = warehouse.lookup_ligand_chemistry(refs)
    except Exception:
        return out
    for sig in sigs.values():
        if sig.get("exact_ligand_identity_group"):
            out["ligand_exact_identity"].add(sig["exact_ligand_identity_group"])
        if sig.get("chemical_series_group"):
            out["ligand_chemical_series"].add(sig["chemical_series_group"])
        if sig.get("canonical_smiles_hash"):
            out["ligand_canonical_smiles_hash"].add(sig["canonical_smiles_hash"])
    return out


def audit_partition_overlap(
    config: Config,
    test_label: str,
    test_accessions: Iterable[str],
    test_pdb_ids: Iterable[str],
    comparison_label: str,
    comparison_accessions: Iterable[str],
    comparison_pdb_ids: Iterable[str],
    *,
    test_ligand_refs: Iterable[str] | None = None,
    comparison_ligand_refs: Iterable[str] | None = None,
    include_expanded_axes: bool = False,
) -> dict[str, object]:
    """Compute a leakage_signature between two benchmark partitions.

    Built-in protein-side axes (always computed):
        direct_pdb, accession_root, uniref100, uniref90, uniref50

    Expanded axes (when ``include_expanded_axes=True``):
        pfam_family, interpro_family, ligand_exact_identity,
        ligand_chemical_series, ligand_canonical_smiles_hash

    Each expanded axis records itself in the result's
    ``axes_unavailable`` list when the underlying partition isn't
    materialised in this warehouse copy, so the verdict stays honest
    about what was actually checked. The composite score is
    ``max_over_axes`` across every axis that DID resolve — so adding
    more axes can only ever raise the composite, never lower it.
    """
    warehouse = Warehouse(config)
    test_accs = {_norm_acc(a) for a in test_accessions} - {""}
    comp_accs = {_norm_acc(a) for a in comparison_accessions} - {""}
    test_pdb = {str(p).upper() for p in test_pdb_ids if p}
    comp_pdb = {str(p).upper() for p in comparison_pdb_ids if p}

    # Resolve UniRef clusters once, look up by level
    test_uref100, unresolved100 = warehouse.cluster_set_at_level(test_accs, "uniref100")
    comp_uref100, _ = warehouse.cluster_set_at_level(comp_accs, "uniref100")
    test_uref90, unresolved90 = warehouse.cluster_set_at_level(test_accs, "uniref90")
    comp_uref90, _ = warehouse.cluster_set_at_level(comp_accs, "uniref90")
    test_uref50, unresolved50 = warehouse.cluster_set_at_level(test_accs, "uniref50")
    comp_uref50, _ = warehouse.cluster_set_at_level(comp_accs, "uniref50")

    axes = {
        "direct_pdb": _axis(test_pdb, comp_pdb),
        "accession_root": _axis(test_accs, comp_accs),
        "uniref100": _axis(test_uref100, comp_uref100),
        "uniref90": _axis(test_uref90, comp_uref90),
        "uniref50": _axis(test_uref50, comp_uref50),
    }
    axes_unavailable: list[str] = []
    expanded_resolution: dict[str, object] = {}

    if include_expanded_axes:
        # Pfam / InterPro (protein side).
        test_pfam, pfam_unresolved = _pfam_set(warehouse, test_accs)
        comp_pfam, _ = _pfam_set(warehouse, comp_accs)
        if test_pfam or comp_pfam:
            axes["pfam_family"] = _axis(test_pfam, comp_pfam)
        else:
            axes_unavailable.append("pfam_family")
        expanded_resolution["pfam_unresolved_test"] = pfam_unresolved

        test_ipr, ipr_unresolved = _interpro_set(warehouse, test_accs)
        comp_ipr, _ = _interpro_set(warehouse, comp_accs)
        if test_ipr or comp_ipr:
            axes["interpro_family"] = _axis(test_ipr, comp_ipr)
        else:
            axes_unavailable.append("interpro_family")
        expanded_resolution["interpro_unresolved_test"] = ipr_unresolved

        # Ligand chemistry (PLINDER side).
        if test_ligand_refs is not None or comparison_ligand_refs is not None:
            test_lig = _ligand_chem_axes(warehouse, test_ligand_refs or [])
            comp_lig = _ligand_chem_axes(warehouse, comparison_ligand_refs or [])
            for axis_name in ("ligand_exact_identity", "ligand_chemical_series",
                              "ligand_canonical_smiles_hash"):
                t_set, c_set = test_lig[axis_name], comp_lig[axis_name]
                if t_set or c_set:
                    axes[axis_name] = _axis(t_set, c_set)
                else:
                    axes_unavailable.append(axis_name)

    composite = max(a["overlap_fraction"] for a in axes.values())
    verdict, codes = _verdict_for(composite)

    return {
        "schema_version": LEAKAGE_SCHEMA,
        "audit_id": _stable_id([test_label, comparison_label]),
        "test_partition_id": test_label,
        "comparison_source_id": comparison_label,
        "row_count_test": len(test_accs),
        "row_count_comparison": len(comp_accs),
        "axes": axes,
        "axes_unavailable": axes_unavailable,
        "uniref_resolution_quality": {
            "test_accessions": len(test_accs),
            "test_unresolved_uniref100": unresolved100,
            "test_unresolved_uniref90": unresolved90,
            "test_unresolved_uniref50": unresolved50,
            "test_resolution_fraction_uniref90": (
                1.0 - unresolved90 / max(len(test_accs), 1)
            ),
        },
        "expanded_axes_resolution_quality": expanded_resolution,
        "verdict": verdict,
        "reason_codes": codes,
        "composite_score": {"method": "max_over_axes", "value": composite},
        "warehouse_root": str(config.warehouse_root),
        "offline_mode": config.offline_mode,
        "generated_at": _now(),
    }
