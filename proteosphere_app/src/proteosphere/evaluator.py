from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .model import DatasetManifest, DatasetRecord, clean_text, normalize_accession_root, normalize_ligand_id
from .schema import validate_manifest
from .warehouse import Warehouse


BLOCKING_CODES = {
    "DIRECT_OVERLAP",
    "ACCESSION_ROOT_OVERLAP",
    "UNIREF_CLUSTER_OVERLAP",
    "SHARED_PARTNER_LEAKAGE",
    "LIGAND_IDENTITY_OVERLAP",
    "LIGAND_CHEMICAL_SERIES_OVERLAP",
    "MOTIF_DOMAIN_OVERLAP",
    "UNRESOLVED_ENTITY_MAPPING",
}


def _record_accessions(manifest: DatasetManifest, record: DatasetRecord) -> list[str]:
    if manifest.entity_kind == "structure_pair":
        return []
    values = list(record.protein_accessions)
    if manifest.entity_kind == "protein_pair":
        values.extend([record.protein_a, record.protein_b])
    elif manifest.entity_kind == "protein_ligand":
        values.extend([record.protein_a, record.protein_b])
    return [clean_text(item) for item in values if clean_text(item)]


def _record_chain_requests(manifest: DatasetManifest, record: DatasetRecord) -> list[tuple[str, str]]:
    if manifest.entity_kind != "structure_pair":
        return []
    return [
        (record.pdb_id, clean_text(record.protein_a).upper()),
        (record.pdb_id, clean_text(record.protein_b).upper()),
    ]


def _record_source_family(record: DatasetRecord) -> str:
    return clean_text(record.source_family or record.source_dataset)


def _record_assay_family(record: DatasetRecord) -> str:
    return clean_text(record.assay_family or record.measurement_type)


def _pair_key(values: list[str]) -> str:
    return "|".join(sorted({clean_text(item) for item in values if clean_text(item)}))


def _split_sets() -> dict[str, dict[str, set[str]]]:
    return {
        split: {
            "direct_entities": set(),
            "accession_roots": set(),
            "uniref": set(),
            "partners": set(),
            "ligand_identity": set(),
            "ligand_series": set(),
            "motif_domain": set(),
            "source_family": set(),
            "assay_family": set(),
            "structure_pairs": set(),
        }
        for split in ("train", "val", "test")
    }


def _overlap(train: set[str], holdout: set[str]) -> list[str]:
    return sorted(item for item in train & holdout if item)


def evaluate_dataset(manifest: DatasetManifest, warehouse: Warehouse) -> dict[str, Any]:
    blockers, warnings = validate_manifest(manifest, require_splits=True)
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

    split_signatures = _split_sets()
    unresolved_entities: set[str] = set()
    unresolved_record_ids: set[str] = set()
    record_diagnostics: list[dict[str, Any]] = []

    for record in manifest.records:
        if record.split not in split_signatures:
            continue
        split = record.split
        record_accessions = _record_accessions(manifest, record)
        resolved_accessions = list(record_accessions)
        if manifest.entity_kind == "structure_pair":
            chain_values = _record_chain_requests(manifest, record)
            split_signatures[split]["direct_entities"].add(record.pdb_id)
            sorted_chains = sorted(
                [clean_text(record.protein_a).upper(), clean_text(record.protein_b).upper()]
            )
            split_signatures[split]["structure_pairs"].add(
                f"{record.pdb_id}:{sorted_chains[0]}:{sorted_chains[1]}"
            )
            for key in chain_values:
                resolved = chain_resolution.get((key[0].upper(), key[1].upper()))
                if resolved and resolved.resolved:
                    resolved_accessions.append(resolved.accession)
                    split_signatures[split]["direct_entities"].add(f"{resolved.pdb_id}:{resolved.chain_id}")
                    split_signatures[split]["direct_entities"].add(resolved.accession)
                    if resolved.uniref90 or resolved.uniref100:
                        split_signatures[split]["uniref"].add(resolved.uniref90 or resolved.uniref100)
                elif warehouse.catalog_available:
                    unresolved_entities.add(f"{key[0].upper()}:{key[1].upper()}")
                    unresolved_record_ids.add(record.record_id)
        for accession in record_accessions:
            resolved = protein_resolution.get(accession)
            split_signatures[split]["direct_entities"].add(accession)
            split_signatures[split]["accession_roots"].add(normalize_accession_root(accession))
            if resolved and resolved.resolved:
                if resolved.uniref90 or resolved.uniref100:
                    split_signatures[split]["uniref"].add(resolved.uniref90 or resolved.uniref100)
            elif warehouse.catalog_available:
                unresolved_entities.add(accession)
                unresolved_record_ids.add(record.record_id)
        if resolved_accessions:
            for accession in resolved_accessions:
                split_signatures[split]["partners"].add(accession)
        if record.ligand_id:
            normalized_ligand = normalize_ligand_id(record.ligand_id)
            split_signatures[split]["direct_entities"].add(f"ligand:{normalized_ligand}")
            split_signatures[split]["ligand_identity"].add(normalized_ligand.lower())
            ligand = ligand_resolution.get(record.ligand_id)
            if ligand and ligand.resolved:
                if ligand.exact_identity_group:
                    split_signatures[split]["ligand_identity"].add(ligand.exact_identity_group.lower())
                if ligand.chemical_series_group:
                    split_signatures[split]["ligand_series"].add(ligand.chemical_series_group.lower())
            elif warehouse.catalog_available and manifest.entity_kind == "protein_ligand":
                unresolved_entities.add(record.ligand_id)
                unresolved_record_ids.add(record.record_id)
        if record.ligand_chemical_series:
            split_signatures[split]["ligand_series"].add(record.ligand_chemical_series.lower())
        if record.ligand_smiles:
            split_signatures[split]["ligand_identity"].add(f"smiles:{record.ligand_smiles}".lower())
        for signature in record.motif_domain_signatures:
            split_signatures[split]["motif_domain"].add(signature.lower())
        source_family = _record_source_family(record)
        if source_family:
            split_signatures[split]["source_family"].add(source_family.lower())
        assay_family = _record_assay_family(record)
        if assay_family:
            split_signatures[split]["assay_family"].add(assay_family.lower())
        record_diagnostics.append(
            {
                "record_id": record.record_id,
                "split": record.split,
                "resolved_accessions": sorted(set(resolved_accessions)),
                "grouping_keys": {
                    "accession_grouped": _pair_key([normalize_accession_root(item) for item in resolved_accessions]),
                    "uniref_grouped": _pair_key(
                        [
                            (protein_resolution.get(item).uniref90 or protein_resolution.get(item).uniref100)
                            for item in record_accessions
                            if protein_resolution.get(item)
                        ]
                    ),
                    "ligand_identity_grouped": normalize_ligand_id(record.ligand_id).lower(),
                    "protein_ligand_component_grouped": _pair_key(
                        [*resolved_accessions, normalize_ligand_id(record.ligand_id).lower()]
                    ),
                },
            }
        )

    holdout_names = ("val", "test")
    train = split_signatures["train"]
    holdout = {
        key: set().union(*(split_signatures[name][key] for name in holdout_names))
        for key in train
    }
    findings = {
        "direct_overlap": _overlap(train["direct_entities"], holdout["direct_entities"]),
        "accession_root_overlap": _overlap(train["accession_roots"], holdout["accession_roots"]),
        "uniref_overlap": _overlap(train["uniref"], holdout["uniref"]),
        "shared_partner_overlap": _overlap(train["partners"], holdout["partners"]),
        "ligand_identity_overlap": _overlap(train["ligand_identity"], holdout["ligand_identity"]),
        "ligand_chemical_series_overlap": _overlap(train["ligand_series"], holdout["ligand_series"]),
        "motif_domain_overlap": _overlap(train["motif_domain"], holdout["motif_domain"]),
        "source_family_overlap": _overlap(train["source_family"], holdout["source_family"]),
        "assay_family_overlap": _overlap(train["assay_family"], holdout["assay_family"]),
        "structure_pair_overlap": _overlap(train["structure_pairs"], holdout["structure_pairs"]),
    }
    metrics = {f"{key}_count": len(value) for key, value in findings.items()}
    metrics.update(
        {
            "total_row_count": len(manifest.records),
            "train_count": sum(1 for record in manifest.records if record.split == "train"),
            "val_count": sum(1 for record in manifest.records if record.split == "val"),
            "test_count": sum(1 for record in manifest.records if record.split == "test"),
            "unresolved_entity_count": len(unresolved_entities),
            "unresolved_record_count": len(unresolved_record_ids),
        }
    )

    reason_codes: list[str] = []
    if blockers:
        reason_codes.append("MANIFEST_SCHEMA_BLOCKER")
    if unresolved_entities:
        reason_codes.append("UNRESOLVED_ENTITY_MAPPING")
    if findings["direct_overlap"] or findings["structure_pair_overlap"]:
        reason_codes.append("DIRECT_OVERLAP")
    if findings["accession_root_overlap"]:
        reason_codes.append("ACCESSION_ROOT_OVERLAP")
    if findings["uniref_overlap"]:
        reason_codes.append("UNIREF_CLUSTER_OVERLAP")
    if findings["shared_partner_overlap"]:
        reason_codes.append("SHARED_PARTNER_LEAKAGE")
    if findings["ligand_identity_overlap"]:
        reason_codes.append("LIGAND_IDENTITY_OVERLAP")
    if findings["ligand_chemical_series_overlap"]:
        reason_codes.append("LIGAND_CHEMICAL_SERIES_OVERLAP")
    if findings["motif_domain_overlap"]:
        reason_codes.append("MOTIF_DOMAIN_OVERLAP")
    if findings["source_family_overlap"]:
        reason_codes.append("SOURCE_FAMILY_SPLIT_OVERLAP")
    if findings["assay_family_overlap"]:
        reason_codes.append("ASSAY_FAMILY_SPLIT_OVERLAP")
    if manifest.entity_kind == "protein_ligand" and not any(
        split_signatures[split]["ligand_series"] for split in ("train", "val", "test")
    ):
        warnings.append("No ligand chemical-series signatures were supplied or resolved; scaffold leakage review is limited to exact ligand identity.")
        reason_codes.append("LIGAND_SIGNATURE_COVERAGE_GAP")
    if manifest.entity_kind in {"protein_pair", "structure_pair"} and not any(
        split_signatures[split]["motif_domain"] for split in ("train", "val", "test")
    ):
        warnings.append("No motif/domain signatures were supplied by the manifest; motif-domain leakage review is limited to warehouse-resolved UniRef/family information.")
    if not warehouse.catalog_available:
        warnings.append("DuckDB catalog was not available; review used only manifest-supplied identifiers and signatures.")
        reason_codes.append("WAREHOUSE_COVERAGE_GAP")

    deduped_codes = list(dict.fromkeys(reason_codes))
    verdict = (
        "blocked_pending_mapping"
        if "UNRESOLVED_ENTITY_MAPPING" in deduped_codes or "MANIFEST_SCHEMA_BLOCKER" in deduped_codes
        else "unsafe_for_training"
        if BLOCKING_CODES & set(deduped_codes)
        else "usable_with_caveats"
        if "WAREHOUSE_COVERAGE_GAP" in deduped_codes or warnings
        else "usable"
    )
    recommended = (
        "Fix unresolved identifiers or schema blockers, then rerun review."
        if verdict == "blocked_pending_mapping"
        else "Regenerate the split with accession/UniRef/ligand/component grouping and rerun review."
        if verdict == "unsafe_for_training"
        else "Proceed only with the listed caveats; add missing provenance/signature fields where possible."
        if verdict == "usable_with_caveats"
        else "Split is eligible for downstream use under the evaluated policy."
    )
    return {
        "artifact_id": "proteosphere_split_assessment",
        "schema_id": "proteosphere-split-assessment-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "manifest_id": manifest.manifest_id,
        "title": manifest.title,
        "entity_kind": manifest.entity_kind,
        "warehouse": {
            "root": str(warehouse.root),
            "catalog_available": warehouse.catalog_available,
            "duckdb_available": warehouse.duckdb_available,
            "table_count": len(warehouse.table_names),
        },
        "verdict": verdict,
        "reason_codes": deduped_codes,
        "blockers": blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "overlap_metrics": metrics,
        "overlap_findings": findings,
        "unresolved_entities": sorted(unresolved_entities),
        "unresolved_record_ids": sorted(unresolved_record_ids),
        "deterministic_signature_checks": {
            "split_signature_counts": {
                split: {name: len(values) for name, values in groups.items()}
                for split, groups in split_signatures.items()
            },
            "record_diagnostics": record_diagnostics,
        },
        "recommended_action": recommended,
    }
