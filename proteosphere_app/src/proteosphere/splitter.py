from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .evaluator import _record_accessions, _record_chain_requests
from .model import DatasetManifest, DatasetRecord, clean_text, normalize_accession_root, normalize_ligand_id
from .schema import validate_manifest
from .warehouse import Warehouse

SUPPORTED_POLICIES = {
    # Group-keyed policies (original ProteoSphere set) — these route through
    # the union-find _group_keys mechanism below.
    "accession_grouped",
    "uniref_grouped",
    "ligand_identity_grouped",
    "protein_ligand_component_grouped",
    "scaffold_grouped",
    # v2-trainer-compatible aliases (api/model_studio/v2/dataset_warehouse.py).
    # These map onto an existing group key OR onto the non-group branches
    # below (random / stratified / time-split). The mapping is applied at
    # the very top of split_dataset() and then the canonical name flows
    # through the rest of the pipeline so logs / diagnostics stay stable.
    "random",
    "cold-target",
    "cold-drug",
    "cold-pair",
    "cluster",
    "leakage-aware",
    "scaffold",
    "stratified",
    "time-split",
}

# Aliases from the v2 trainer's policy names → canonical splitter names.
# Applied once at the top of split_dataset(). When the mapped policy is
# itself a sentinel for a non-group code path (random/stratified/time-split),
# the right-hand side is the literal string the rest of the pipeline checks.
_V2_POLICY_ALIASES: dict[str, str] = {
    "cold-target":     "accession_grouped",
    "cold-drug":       "ligand_identity_grouped",
    "cold-pair":       "protein_ligand_component_grouped",
    "cluster":         "uniref_grouped",
    "leakage-aware":   "uniref_grouped",
    "scaffold":        "scaffold_grouped",
    # ``random``, ``stratified`` and ``time-split`` are handled directly
    # below — they don't translate to a group-keyed policy.
}


def _record_field_value(record: DatasetRecord, column_name: str) -> str:
    """Look up a column on a DatasetRecord, checking built-in fields and
    ``extra_metadata`` (case-insensitive). Empty string when absent.

    Used by ``--row-id-column`` / ``--subgroup-column`` so users can
    refer to any column from their original CSV / JSON, regardless of
    whether it landed in a typed attribute or in ``extra_metadata``.
    """
    if not column_name:
        return ""
    direct = getattr(record, column_name, None)
    if isinstance(direct, str) and direct:
        return clean_text(direct)
    extra = getattr(record, "extra_metadata", None) or {}
    if not isinstance(extra, dict):
        return ""
    if column_name in extra:
        return clean_text(extra[column_name])
    # Case-insensitive fallback
    lowered = column_name.lower()
    for key, val in extra.items():
        if isinstance(key, str) and key.lower() == lowered:
            return clean_text(val)
    return ""


def load_leakage_manifest(path: str | Path) -> dict[str, str]:
    """Read a leakage-cluster manifest and return {accession -> cluster_id}.

    The manifest is the JSON written by ``proteosphere overlap-cluster``;
    each clustered accession gets a stable cluster_id. Singletons (which
    aren't in any cluster) don't appear in the output dict.

    Accessions are normalised to uppercase to match how the splitter
    deduplicates IDs elsewhere.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for cluster in payload.get("clusters", []):
        cid = cluster.get("cluster_id")
        if not cid:
            continue
        for member in cluster.get("members", []):
            if not member:
                continue
            out[str(member).strip().upper()] = cid
    return out


def parse_fractions(text: str) -> tuple[float, float, float]:
    parts = [float(item.strip()) for item in text.split(",")]
    if len(parts) != 3:
        raise ValueError("--fractions must contain train,val,test values.")
    total = sum(parts)
    if total <= 0:
        raise ValueError("--fractions must sum to a positive value.")
    return tuple(item / total for item in parts)  # type: ignore[return-value]


def _record_with_split(record: DatasetRecord, split: str) -> DatasetRecord:
    payload = record.to_dict()
    payload["split"] = split
    return DatasetRecord.from_dict(payload)


def _canonical_ligand_keys(record: DatasetRecord, ligand_resolution: dict[str, Any]) -> tuple[str, str]:
    ligand = ligand_resolution.get(record.ligand_id)
    identity = (
        clean_text(getattr(ligand, "exact_identity_group", ""))
        or normalize_ligand_id(record.ligand_id).lower()
    )
    series = (
        clean_text(getattr(ligand, "chemical_series_group", ""))
        or clean_text(record.ligand_chemical_series).lower()
    )
    return identity.lower(), series.lower()


def _record_accession_roots(
    manifest: DatasetManifest,
    record: DatasetRecord,
    chain_resolution: dict[tuple[str, str], Any],
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    accessions = _record_accessions(manifest, record)
    if manifest.entity_kind == "structure_pair":
        for request in _record_chain_requests(manifest, record):
            resolved = chain_resolution.get((request[0].upper(), request[1].upper()))
            if resolved and resolved.resolved and resolved.accession:
                accessions.append(resolved.accession)
            else:
                warnings.append(f"{record.record_id}: unresolved chain {request[0].upper()}:{request[1].upper()}")
    roots = sorted({normalize_accession_root(item) for item in accessions if clean_text(item)})
    return roots, warnings


def _record_uniref_groups(
    manifest: DatasetManifest,
    record: DatasetRecord,
    protein_resolution: dict[str, Any],
    chain_resolution: dict[tuple[str, str], Any],
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    accessions = _record_accessions(manifest, record)
    if manifest.entity_kind == "structure_pair":
        for request in _record_chain_requests(manifest, record):
            resolved = chain_resolution.get((request[0].upper(), request[1].upper()))
            if resolved and resolved.resolved and resolved.accession:
                accessions.append(resolved.accession)
            else:
                warnings.append(f"{record.record_id}: unresolved chain {request[0].upper()}:{request[1].upper()}")
    groups: set[str] = set()
    for accession in accessions:
        resolved = protein_resolution.get(accession)
        if resolved and (resolved.uniref90 or resolved.uniref100):
            groups.add(resolved.uniref90 or resolved.uniref100)
        else:
            warnings.append(f"{record.record_id}: UniRef grouping key could not be resolved for {accession}.")
    return sorted(groups), warnings


def _record_leakage_cluster_keys(
    manifest: DatasetManifest,
    record: DatasetRecord,
    chain_resolution: dict[tuple[str, str], Any],
    accession_to_cluster: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Emit ``leakage_cluster:<id>`` group keys for each clustered accession.

    Reuses the same accession-extraction path the other policies use, so
    leakage-manifest constraints stack cleanly on top of an underlying
    policy (e.g. ``accession_grouped + leakage_manifest`` means "split by
    accession AND by cluster, whichever is coarser per record").

    Returns ``(keys, warnings)``.
    """
    warnings: list[str] = []
    accessions = _record_accessions(manifest, record)
    if manifest.entity_kind == "structure_pair":
        for request in _record_chain_requests(manifest, record):
            resolved = chain_resolution.get((request[0].upper(), request[1].upper()))
            if resolved and resolved.resolved and resolved.accession:
                accessions.append(resolved.accession)
    keys: list[str] = []
    for accession in accessions:
        clean = clean_text(accession)
        if not clean:
            continue
        cid = accession_to_cluster.get(clean.upper())
        if cid:
            keys.append(f"leakage_cluster:{cid}")
    return sorted(set(keys)), warnings


def _murcko_scaffold_for(
    record: DatasetRecord,
    ligand_resolution: dict[str, Any],
) -> str:
    """Return the Bemis-Murcko scaffold SMILES for a ligand, or ``""``
    when it can't be computed.

    Tries each of these SMILES sources, first hit wins:
      1. the warehouse-resolved canonical SMILES (preferred — already
         standardised / canonicalised by the warehouse layer)
      2. record.ligand_smiles (the raw column the dataset shipped)
      3. record.extra_metadata['smiles'] case-insensitive

    Importing RDKit lazily keeps the module importable on minimal envs.
    Failures degrade silently to ``""`` and the caller decides what to do
    (typically: fall back to ligand identity grouping + emit a warning).
    """
    smiles = ""
    res = ligand_resolution.get(record.ligand_id) if record.ligand_id else None
    if res is not None:
        smiles = getattr(res, "canonical_smiles", "") or getattr(res, "smiles", "") or ""
    if not smiles:
        smiles = getattr(record, "ligand_smiles", "") or ""
    if not smiles:
        extra = getattr(record, "extra_metadata", None) or {}
        if isinstance(extra, dict):
            for k, v in extra.items():
                if isinstance(k, str) and k.lower() == "smiles" and isinstance(v, str):
                    smiles = v
                    break
    if not smiles:
        return ""
    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except Exception:
        return ""
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return ""
        scaf = MurckoScaffold.GetScaffoldForMol(mol)
        return Chem.MolToSmiles(scaf) if scaf is not None else ""
    except Exception:
        return ""


def _record_timestamp(record: DatasetRecord) -> str:
    """Pull a sort key for time-split policy from a DatasetRecord.

    Order of precedence (first non-empty wins):
      1. record.extra_metadata['deposit_date'] / 'release_date' / 'year'
      2. record.deposit_date / record.release_date / record.year (typed fields)
      3. record.record_id (deterministic but content-free fallback so
         every record has a stable sort key)
    """
    candidates = ("deposit_date", "release_date", "year", "date")
    extra = getattr(record, "extra_metadata", None) or {}
    if isinstance(extra, dict):
        for k in candidates:
            v = extra.get(k) or extra.get(k.lower()) or extra.get(k.upper())
            if isinstance(v, (str, int, float)) and str(v).strip():
                return str(v)
    for k in candidates:
        v = getattr(record, k, None)
        if v not in (None, ""):
            return str(v)
    return record.record_id or ""


def _split_non_group(
    manifest: DatasetManifest,
    *,
    policy: str,
    fractions: tuple[float, float, float],
    seed: int,
    stratify_column: str | None = None,
) -> dict[str, Any]:
    """Handle the non-group-keyed policies: ``random``, ``stratified``,
    and ``time-split``. These don't run the union-find pipeline because
    they operate on individual rows, not on protein/ligand groups.

    Returns the same {status, manifest, diagnostics} envelope that the
    main split_dataset() does, so the caller can short-circuit.
    """
    records = list(manifest.records)
    n = len(records)
    if n == 0:
        return {
            "status": "blocked",
            "manifest_id": manifest.manifest_id,
            "policy": policy,
            "blockers": ["No records to split."],
            "warnings": [],
        }
    rng = random.Random(seed)
    targets = {
        "train": max(0, round(n * fractions[0])),
        "val":   max(0, round(n * fractions[1])),
        "test":  max(0, round(n * fractions[2])),
    }
    # Tiny-dataset fixup: if val rounded to zero but we have ≥5 rows, pull
    # one row out of train to seed val so downstream consumers always see
    # all three lanes populated.
    if n >= 5 and targets["val"] == 0 and targets["train"] > 1:
        targets["val"] = 1
        targets["train"] -= 1

    warnings: list[str] = []
    ordered: list[tuple[int, DatasetRecord]] = list(enumerate(records))
    if policy == "random":
        rng.shuffle(ordered)
    elif policy == "stratified":
        # Stratify by the named binary label column (or `label` /
        # `interaction_label` / `interacts` by default). Group rows by
        # class label, shuffle within class, then interleave so train/
        # val/test all preserve the original class fraction.
        col = stratify_column or "label"
        by_class: dict[str, list[tuple[int, DatasetRecord]]] = defaultdict(list)
        for idx, rec in ordered:
            cls = _record_field_value(rec, col) or getattr(rec, col, "") or "_unlabelled"
            by_class[str(cls)].append((idx, rec))
        for cls in by_class:
            rng.shuffle(by_class[cls])
        # Round-robin interleave so the per-class proportions are stable
        # across train/val/test slices.
        interleaved: list[tuple[int, DatasetRecord]] = []
        cursors = {cls: 0 for cls in by_class}
        cls_keys = sorted(by_class.keys())
        while any(cursors[c] < len(by_class[c]) for c in cls_keys):
            for c in cls_keys:
                if cursors[c] < len(by_class[c]):
                    interleaved.append(by_class[c][cursors[c]])
                    cursors[c] += 1
        ordered = interleaved
        if len(by_class) < 2:
            warnings.append(
                f"stratified split saw only {len(by_class)} class(es) in "
                f"column {col!r}; behaves like random."
            )
    elif policy == "time-split":
        # Sort ascending by timestamp so the OLDEST rows become train and
        # the NEWEST become test — the realistic deployment-time generalization
        # check that v2's time-split lane is named for.
        ordered.sort(key=lambda kv: _record_timestamp(kv[1]))
        warnings.append(
            "time-split: rows are partitioned in ascending order of the "
            "first non-empty of {deposit_date, release_date, year, date}; "
            "rows without a timestamp sort by record_id and land in the "
            "oldest bucket."
        )
    else:
        raise ValueError(f"_split_non_group called with unsupported policy {policy!r}")

    assigned: dict[str, list[DatasetRecord]] = {"train": [], "val": [], "test": []}
    # Take train, then val, then test, in order from the shuffled / sorted list.
    cursor = 0
    for lane in ("train", "val", "test"):
        end = cursor + targets[lane]
        for _, rec in ordered[cursor:end]:
            assigned[lane].append(_record_with_split(rec, lane))
        cursor = end
    # Any leftover rows (rounding remainder) go to test.
    for _, rec in ordered[cursor:]:
        assigned["test"].append(_record_with_split(rec, "test"))

    output_records = [rec for lane in ("train", "val", "test") for rec in assigned[lane]]
    split_manifest = DatasetManifest(
        manifest_id=f"{manifest.manifest_id}-{policy}-seed{seed}",
        title=manifest.title,
        task_type=manifest.task_type,
        label_type=manifest.label_type,
        entity_kind=manifest.entity_kind,
        split_membership_mode=f"proteosphere_{policy}",
        records=tuple(output_records),
        notes=tuple([*manifest.notes,
                     f"Generated by ProteoSphere policy {policy} with seed {seed}."]),
    )
    diagnostics: dict[str, Any] = {
        "status": "ready",
        "generated_at": datetime.now(UTC).isoformat(),
        "policy": policy,
        "seed": seed,
        "split_counts": {lane: len(assigned[lane]) for lane in ("train", "val", "test")},
        "group_count": n,           # one row == one "group" for these policies
        "rows_per_group": 1.0,
        "underpowered_warning": False,
        "group_crossing_count": 0,
        "crossing_groups": [],
        "group_assignments": [],
        "warnings": warnings,
        "blockers": [],
        "fractions": {"train": fractions[0], "val": fractions[1], "test": fractions[2]},
    }
    return {
        "status": "ready",
        "manifest": split_manifest.to_dict(),
        "diagnostics": diagnostics,
    }


def _group_keys(
    manifest: DatasetManifest,
    record: DatasetRecord,
    warehouse: Warehouse,
    protein_resolution: dict[str, Any],
    ligand_resolution: dict[str, Any],
    chain_resolution: dict[tuple[str, str], Any],
    policy: str,
    *,
    accession_to_cluster: dict[str, str] | None = None,
    row_id_column: str | None = None,
    row_index: int = 0,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    keys: list[str] = []

    # If the user opted into row-level keying (e.g. SKEMPI ddG splits where
    # mutations of the same PDB should split independently), DO NOT emit
    # the policy's accession / ligand keys -- they would force every row
    # sharing a PDB into the same union-find group. Each row instead
    # gets a unique row_id key, so it stands alone unless an explicit
    # leakage-cluster edge bridges it to another row.
    if row_id_column:
        row_id = _record_field_value(record, row_id_column)
        if not row_id:
            row_id = record.record_id or f"row_{row_index}"
            warnings.append(
                f"{record.record_id}: column {row_id_column!r} is empty; "
                f"falling back to record_id."
            )
        keys.append(f"row_id:{row_id}")
    elif policy == "accession_grouped":
        roots, root_warnings = _record_accession_roots(manifest, record, chain_resolution)
        warnings.extend(root_warnings)
        keys.extend(f"accession:{root}" for root in roots)
    elif policy == "uniref_grouped":
        groups, group_warnings = _record_uniref_groups(
            manifest,
            record,
            protein_resolution,
            chain_resolution,
        )
        warnings.extend(group_warnings)
        keys.extend(f"uniref:{group}" for group in groups)
    elif policy == "ligand_identity_grouped":
        identity, _series = _canonical_ligand_keys(record, ligand_resolution)
        if identity:
            keys.append(f"ligand_identity:{identity}")
    elif policy == "protein_ligand_component_grouped":
        roots, root_warnings = _record_accession_roots(manifest, record, chain_resolution)
        warnings.extend(root_warnings)
        identity, series = _canonical_ligand_keys(record, ligand_resolution)
        keys.extend(f"accession:{root}" for root in roots)
        if identity:
            keys.append(f"ligand_identity:{identity}")
        if series:
            keys.append(f"ligand_series:{series}")
    elif policy == "scaffold_grouped":
        # Murcko-scaffold grouping: every ligand sharing a Bemis-Murcko
        # scaffold lands in the same union-find group. Mirrors the v2
        # trainer's ``scaffold`` lane (which uses RDKit's MurckoScaffold
        # under the hood; see api/model_studio/v2/dataset_warehouse.py
        # `_scaffold_key_map`). When RDKit isn't importable or the SMILES
        # can't be parsed we fall back to the ligand's identity key so the
        # row still groups deterministically — just at a coarser bucket
        # than the user asked for. A warning is appended in that case so
        # downstream consumers can flag it.
        identity, _series = _canonical_ligand_keys(record, ligand_resolution)
        scaffold = _murcko_scaffold_for(record, ligand_resolution)
        if scaffold:
            keys.append(f"scaffold:{scaffold}")
        elif identity:
            keys.append(f"ligand_identity:{identity}")
            warnings.append(
                f"{record.record_id}: Murcko scaffold unavailable "
                f"(no SMILES / RDKit missing); falling back to ligand identity."
            )
    else:
        raise ValueError(f"Unsupported policy: {policy}")
    # Append leakage-cluster keys if a manifest was provided. These are
    # additive: two records sharing a leakage cluster end up unioned
    # together by the same key, regardless of whether the underlying
    # policy would have merged them. With ``--row-id-column``, leakage
    # clusters are usually the ONLY thing that bridges rows -- a great
    # combination when you want row-level granularity except where
    # cluster constraints forbid it.
    if accession_to_cluster:
        cluster_keys, cluster_warnings = _record_leakage_cluster_keys(
            manifest, record, chain_resolution, accession_to_cluster,
        )
        keys.extend(cluster_keys)
        warnings.extend(cluster_warnings)
    keys = sorted({key for key in keys if clean_text(key)})
    if not keys:
        warnings.append(f"{record.record_id}: required grouping key is missing for {policy}.")
    return "|".join(keys), warnings


def split_dataset(
    manifest: DatasetManifest,
    warehouse: Warehouse,
    *,
    policy: str,
    fractions: tuple[float, float, float],
    seed: int,
    resplit: bool = False,
    leakage_manifest_path: str | Path | None = None,
    kfold: int | None = None,
    row_id_column: str | None = None,
    subgroup_column: str | None = None,
) -> dict[str, Any]:
    """Split a dataset manifest with the given grouping policy.

    When ``leakage_manifest_path`` is provided, the JSON file produced
    by ``proteosphere overlap-cluster`` is loaded and its clusters are
    layered onto the chosen policy as additional union-find edges. This
    lets the splitter constrain on tier-aware leakage (paralog family,
    convergent function, etc.) on top of the coarse accession / UniRef
    grouping the built-in policies offer.

    When ``kfold`` is provided (>= 2), the splitter produces a k-fold
    cross-validation assignment instead of a single train/val/test
    split. Records get a ``split`` value of ``fold_0`` ... ``fold_{k-1}``
    and the diagnostics list per-fold sizes. ``fractions`` is ignored in
    k-fold mode. Leakage clusters are respected: whole clusters always
    land in a single fold.

    When ``row_id_column`` is set, the policy's accession-based grouping
    is replaced by a per-row key drawn from the named column. Each row
    becomes its own group unless a leakage manifest bridges it to
    others. Useful for SKEMPI-style mutation-prediction benchmarks
    where the same PDB legitimately appears across train and test as
    long as the mutation differs.

    When ``subgroup_column`` is set, group-to-split assignment is done
    independently within each subgroup so the train/val/test fractions
    are honoured per subgroup. Mixed-subgroup groups (a leakage cluster
    spanning multiple subgroups) are assigned to the dominant subgroup
    with a warning.
    """
    accession_to_cluster: dict[str, str] = {}
    if leakage_manifest_path is not None:
        accession_to_cluster = load_leakage_manifest(leakage_manifest_path)
    if kfold is not None and kfold < 2:
        return {
            "status": "blocked",
            "manifest_id": manifest.manifest_id,
            "policy": policy,
            "blockers": [f"--kfold must be >= 2 (got {kfold})."],
            "warnings": [],
        }
    if policy not in SUPPORTED_POLICIES:
        raise ValueError(f"policy must be one of {sorted(SUPPORTED_POLICIES)}")
    # Resolve v2 trainer aliases to the splitter's canonical policy name
    # so the rest of the pipeline sees the same vocabulary it always has.
    # The original alias is preserved in diagnostics["requested_policy"]
    # below for traceability.
    requested_policy = policy
    if policy in _V2_POLICY_ALIASES:
        policy = _V2_POLICY_ALIASES[policy]

    # Non-group policies (random / stratified / time-split) bypass the
    # union-find pipeline entirely — they operate on rows, not on
    # protein/ligand groups. Short-circuit here so the row-level branch
    # doesn't have to fight the group-key machinery downstream.
    if requested_policy in ("random", "stratified", "time-split"):
        if kfold is not None:
            return {
                "status": "blocked",
                "manifest_id": manifest.manifest_id,
                "policy": requested_policy,
                "blockers": [f"--kfold is not supported with policy "
                             f"{requested_policy!r}; use a group policy."],
                "warnings": [],
            }
        schema_blockers, schema_warnings = validate_manifest(
            manifest, require_splits=False,
        )
        if schema_blockers:
            return {
                "status": "blocked",
                "manifest_id": manifest.manifest_id,
                "policy": requested_policy,
                "blockers": schema_blockers,
                "warnings": schema_warnings,
            }
        if (not resplit
                and any(r.split in {"train", "val", "test"} for r in manifest.records)):
            return {
                "status": "blocked",
                "manifest_id": manifest.manifest_id,
                "policy": requested_policy,
                "blockers": ["Input already contains split labels; pass "
                             "--resplit to overwrite them."],
                "warnings": schema_warnings,
            }
        out = _split_non_group(
            manifest,
            policy=requested_policy,
            fractions=fractions,
            seed=seed,
            stratify_column=subgroup_column,
        )
        out["diagnostics"]["requested_policy"] = requested_policy
        return out
    schema_blockers, schema_warnings = validate_manifest(manifest, require_splits=False)
    if schema_blockers:
        return {
            "status": "blocked",
            "manifest_id": manifest.manifest_id,
            "blockers": schema_blockers,
            "warnings": schema_warnings,
        }
    if not resplit and any(record.split in {"train", "val", "test"} for record in manifest.records):
        return {
            "status": "blocked",
            "manifest_id": manifest.manifest_id,
            "policy": policy,
            "blockers": ["Input already contains split labels; pass --resplit to overwrite them."],
            "warnings": schema_warnings,
        }

    accessions: list[str] = []
    ligands: list[str] = []
    chain_requests: list[tuple[str, str]] = []
    for record in manifest.records:
        accessions.extend(_record_accessions(manifest, record))
        ligands.append(record.ligand_id)
        chain_requests.extend(_record_chain_requests(manifest, record))
    protein_resolution = warehouse.resolve_proteins(accessions)
    ligand_resolution = warehouse.resolve_ligands(ligands)
    chain_resolution = warehouse.resolve_structure_chains(chain_requests)

    row_keys: list[list[str]] = []
    warnings = list(schema_warnings)
    blockers: list[str] = []
    for record_index, record in enumerate(manifest.records):
        joined_key, key_warnings = _group_keys(
            manifest,
            record,
            warehouse,
            protein_resolution,
            ligand_resolution,
            chain_resolution,
            policy,
            accession_to_cluster=accession_to_cluster,
            row_id_column=row_id_column,
            row_index=record_index,
        )
        warnings.extend(key_warnings)
        keys = [item for item in joined_key.split("|") if item]
        if not keys:
            blockers.append(f"{record.record_id}: missing grouping key for {policy}.")
            continue
        row_keys.append(keys)
    if blockers:
        return {
            "status": "blocked",
            "manifest_id": manifest.manifest_id,
            "policy": policy,
            "blockers": blockers,
            "warnings": list(dict.fromkeys(warnings)),
        }
    parent = list(range(len(manifest.records)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    key_to_first_row: dict[str, int] = {}
    for row_index, keys in enumerate(row_keys):
        for key in keys:
            if key in key_to_first_row:
                union(key_to_first_row[key], row_index)
            else:
                key_to_first_row[key] = row_index

    groups: dict[str, list[DatasetRecord]] = defaultdict(list)
    group_key_parts: dict[str, set[str]] = defaultdict(set)
    for row_index, record in enumerate(manifest.records):
        root = str(find(row_index))
        groups[root].append(record)
        group_key_parts[root].update(row_keys[row_index])
    rng = random.Random(seed)
    group_items = [
        (group_id, records, rng.random()) for group_id, records in groups.items()
    ]
    group_items.sort(key=lambda item: (-len(item[1]), item[2], item[0]))

    if kfold is not None:
        # K-fold mode: produce N fold buckets, each named ``fold_<i>``.
        # Use the same balance-minimisation logic as the 3-way path: the
        # next group goes into whichever fold currently has the lowest
        # (size / target) ratio.
        target_names = tuple(f"fold_{i}" for i in range(kfold))
        per_fold = max(1, round(len(manifest.records) / kfold))
        targets = {name: per_fold for name in target_names}
    else:
        target_names = ("train", "val", "test")
        targets = {name: max(1, round(len(manifest.records) * fraction))
                   for name, fraction in zip(target_names, fractions, strict=True)}
        if len(manifest.records) < 5:
            targets["val"] = 0

    # ------ Per-subgroup stratification (optional) ------
    # When --subgroup-column is set, compute each group's dominant
    # subgroup label, then run the assignment loop INDEPENDENTLY within
    # each subgroup so the fractions get honoured per subgroup.
    # Groups whose records span multiple subgroups go to the majority
    # subgroup and produce a warning.
    record_subgroup: dict[int, str] = {}
    if subgroup_column:
        for idx, record in enumerate(manifest.records):
            record_subgroup[idx] = (
                _record_field_value(record, subgroup_column) or "_unlabelled"
            )

    def _dominant_subgroup(records_in_group: list[DatasetRecord]) -> str:
        if not subgroup_column:
            return "_all"
        labels: list[str] = []
        for r in records_in_group:
            # Each record's index in the original manifest matches its
            # position in record_subgroup, since we built that dict in
            # the same order. Walk back via record_id when needed.
            for idx, orig in enumerate(manifest.records):
                if orig is r:
                    labels.append(record_subgroup.get(idx, "_unlabelled"))
                    break
        if not labels:
            return "_unlabelled"
        # Mode label; ties broken alphabetically for determinism.
        counts = defaultdict(int)
        for lbl in labels:
            counts[lbl] += 1
        max_count = max(counts.values())
        winners = sorted(k for k, v in counts.items() if v == max_count)
        if len(set(labels)) > 1:
            warnings.append(
                f"group spans subgroups {sorted(set(labels))!r}; "
                f"assigning to majority {winners[0]!r}"
            )
        return winners[0]

    # Per-subgroup buckets of (group_key, records, tie_breaker) tuples.
    per_subgroup_items: dict[str, list[tuple[Any, list[DatasetRecord], float]]] = defaultdict(list)
    group_dominant: dict[str, str] = {}
    for key, records, tb in group_items:
        sg = _dominant_subgroup(records)
        group_dominant[str(key)] = sg
        per_subgroup_items[sg].append((key, records, tb))

    # Per-subgroup targets: total target * (subgroup_rows / total_rows).
    n_total = len(manifest.records)
    per_subgroup_targets: dict[str, dict[str, int]] = {}
    if subgroup_column:
        subgroup_sizes = Counter(record_subgroup.values())
        for sg, n_sg in subgroup_sizes.items():
            if kfold is not None:
                per = max(1, round(n_sg / kfold))
                per_subgroup_targets[sg] = {name: per for name in target_names}
            else:
                per_subgroup_targets[sg] = {
                    name: max(1, round(n_sg * fraction))
                    for name, fraction in zip(target_names, fractions, strict=True)
                }
                if n_sg < 5:
                    per_subgroup_targets[sg]["val"] = 0
    else:
        per_subgroup_targets["_all"] = dict(targets)

    assigned: dict[str, list[DatasetRecord]] = {name: [] for name in target_names}
    group_assignments: list[dict[str, Any]] = []
    for sg, sg_items in per_subgroup_items.items():
        sg_targets = per_subgroup_targets.get(sg, targets)
        # Track per-subgroup running totals so the balance loop
        # honours the subgroup's targets, not the global ones.
        sg_assigned_count: dict[str, int] = {name: 0 for name in target_names}
        for key, records, _tb in sg_items:
            best = min(
                target_names,
                key=lambda name: (
                    (sg_assigned_count[name] + len(records)) / max(sg_targets[name], 1),
                    sg_assigned_count[name],
                ),
            )
            assigned[best].extend(_record_with_split(record, best) for record in records)
            sg_assigned_count[best] += len(records)
            group_assignments.append(
                {
                    "group_key": key,
                    "component_keys": sorted(group_key_parts[key]),
                    "split": best,
                    "row_count": len(records),
                    "subgroup": sg if subgroup_column else None,
                }
            )

    if kfold is None:
        # Tiny-dataset fixup only applies to the 3-way path. K-fold
        # always preserves group integrity even when some folds end up
        # empty (the user can re-pick N or drop low-quality folds).
        if not assigned["test"] and assigned["train"]:
            moved = assigned["train"].pop()
            assigned["test"].append(_record_with_split(moved, "test"))
        if not assigned["val"] and len(assigned["train"]) > 1:
            moved = assigned["train"].pop()
            assigned["val"].append(_record_with_split(moved, "val"))

    output_records = [record for name in target_names for record in assigned[name]]
    split_manifest = DatasetManifest(
        manifest_id=f"{manifest.manifest_id}-{policy}-seed{seed}",
        title=manifest.title,
        task_type=manifest.task_type,
        label_type=manifest.label_type,
        entity_kind=manifest.entity_kind,
        split_membership_mode=f"proteosphere_{policy}",
        records=tuple(output_records),
        notes=tuple([*manifest.notes, f"Generated by ProteoSphere policy {policy} with seed {seed}."]),
    )
    group_to_splits: dict[str, set[str]] = defaultdict(set)
    for assignment in group_assignments:
        group_to_splits[str(assignment["group_key"])].add(str(assignment["split"]))
    crossing = sorted(key for key, splits in group_to_splits.items() if len(splits) > 1)
    row_group_ratio = len(manifest.records) / max(len(groups), 1)
    diagnostics: dict[str, Any] = {
        "status": "ready" if not crossing else "blocked",
        "generated_at": datetime.now(UTC).isoformat(),
        "policy": policy,
        "requested_policy": requested_policy,
        "seed": seed,
        "split_counts": {name: len(assigned[name]) for name in target_names},
        "group_count": len(groups),
        "rows_per_group": row_group_ratio,
        "underpowered_warning": row_group_ratio < 2.0,
        "group_crossing_count": len(crossing),
        "crossing_groups": crossing,
        "group_assignments": group_assignments,
        "warnings": list(dict.fromkeys(warnings)),
        "blockers": [f"group {key} crosses split boundaries" for key in crossing],
    }
    # Only emit the leakage_manifest field when one was actually used, so
    # the diagnostics shape matches the pre-feature output byte-for-byte
    # for invocations that don't opt in.
    if leakage_manifest_path is not None:
        diagnostics["leakage_manifest"] = {
            "path": str(leakage_manifest_path),
            "clustered_accessions": len(accession_to_cluster),
            "distinct_clusters": len(set(accession_to_cluster.values())),
        }
    # 3-way mode keeps the historical ``fractions`` field for backward
    # compatibility; k-fold mode emits ``kfold`` instead.
    if kfold is not None:
        diagnostics["kfold"] = kfold
    else:
        diagnostics["fractions"] = {
            "train": fractions[0], "val": fractions[1], "test": fractions[2],
        }
    # Surface row-id / subgroup options so consumers can see which axes
    # were active.
    if row_id_column:
        diagnostics["row_id_column"] = row_id_column
    if subgroup_column:
        # Per-subgroup split counts -- the headline metric users want
        # when stratifying.
        per_sg_counts: dict[str, dict[str, int]] = {}
        for assignment in group_assignments:
            sg = assignment.get("subgroup") or "_unlabelled"
            sp = assignment["split"]
            per_sg_counts.setdefault(sg, {n: 0 for n in target_names})
            per_sg_counts[sg][sp] = per_sg_counts[sg].get(sp, 0) + assignment["row_count"]
        diagnostics["subgroup_column"] = subgroup_column
        diagnostics["split_counts_by_subgroup"] = per_sg_counts
    return {
        "status": diagnostics["status"],
        "manifest": split_manifest.to_dict(),
        "diagnostics": diagnostics,
    }
