from __future__ import annotations

from .model import DatasetManifest, VALID_ENTITY_KINDS, VALID_SPLITS, clean_text


def validate_manifest(manifest: DatasetManifest, *, require_splits: bool = True) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    if not manifest.manifest_id:
        blockers.append("manifest_id is required.")
    if manifest.entity_kind not in VALID_ENTITY_KINDS:
        blockers.append("entity_kind must be one of protein_pair, protein_ligand, or structure_pair.")
    if not manifest.records:
        blockers.append("records must contain at least one row.")
    seen_ids: set[str] = set()
    split_counts = {"train": 0, "val": 0, "test": 0, "unsplit": 0}
    for index, record in enumerate(manifest.records, start=1):
        if not record.record_id:
            blockers.append(f"record {index} is missing record_id.")
        elif record.record_id in seen_ids:
            blockers.append(f"record_id {record.record_id!r} is duplicated.")
        seen_ids.add(record.record_id)
        if record.split:
            if record.split not in VALID_SPLITS:
                blockers.append(f"{record.record_id} has invalid split {record.split!r}.")
            else:
                split_counts[record.split] += 1
        else:
            split_counts["unsplit"] += 1
        if manifest.entity_kind == "protein_pair":
            accessions = [item for item in (record.protein_a, record.protein_b, *record.protein_accessions) if clean_text(item)]
            if len(set(accessions)) < 2:
                blockers.append(f"{record.record_id} must provide two protein accessions.")
        elif manifest.entity_kind == "protein_ligand":
            accessions = [item for item in (*record.protein_accessions, record.protein_a, record.protein_b) if clean_text(item)]
            if not accessions:
                blockers.append(f"{record.record_id} must provide at least one protein accession.")
            if not record.ligand_id:
                blockers.append(f"{record.record_id} must provide ligand_id.")
        elif manifest.entity_kind == "structure_pair":
            if not record.pdb_id:
                blockers.append(f"{record.record_id} must provide pdb_id.")
            chains = {clean_text(record.protein_a).upper(), clean_text(record.protein_b).upper()} - {""}
            if len(chains) != 2:
                blockers.append(f"{record.record_id} must provide two distinct chain IDs in protein_a/protein_b.")
    if require_splits:
        for split_name in ("train", "test"):
            if split_counts[split_name] <= 0:
                blockers.append(f"at least one {split_name} record is required for split review.")
        if split_counts["unsplit"]:
            blockers.append("all records must include split for review; use proteosphere-split for unsplit data.")
    else:
        if split_counts["unsplit"] == 0 and split_counts["train"] and split_counts["test"]:
            warnings.append("input already has splits; proteosphere-split will preserve them unless --resplit is used.")
    return blockers, warnings
