from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


VALID_SPLITS = {"train", "val", "test"}
VALID_ENTITY_KINDS = {"protein_pair", "protein_ligand", "structure_pair"}


def clean_text(value: Any) -> str:
    return str(value or "").strip()


def normalize_accession_root(accession: str) -> str:
    text = clean_text(accession)
    if not text:
        return ""
    return text.split("-", 1)[0].split(".", 1)[0].upper()


def normalize_ligand_id(ligand_id: str) -> str:
    return clean_text(ligand_id).strip()


@dataclass(frozen=True)
class DatasetRecord:
    record_id: str
    split: str = ""
    protein_a: str = ""
    protein_b: str = ""
    protein_accessions: tuple[str, ...] = ()
    ligand_id: str = ""
    ligand_smiles: str = ""
    ligand_chemical_series: str = ""
    pdb_id: str = ""
    motif_domain_signatures: tuple[str, ...] = ()
    source_dataset: str = ""
    source_family: str = ""
    assay_family: str = ""
    measurement_type: str = ""
    label_value: Any = None
    provenance_note: str = ""
    extra_metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DatasetRecord":
        extra = payload.get("extra_metadata") or {}
        if not isinstance(extra, dict):
            extra = {}
        motif_values = (
            payload.get("motif_domain_signatures")
            or payload.get("motif_signatures")
            or payload.get("domain_signatures")
            or extra.get("motif_domain_signatures")
            or ()
        )
        if isinstance(motif_values, str):
            motif_values = [item.strip() for item in motif_values.split(";") if item.strip()]
        return cls(
            record_id=clean_text(payload.get("record_id") or payload.get("row_id")),
            split=clean_text(payload.get("split")).lower(),
            protein_a=clean_text(payload.get("protein_a")),
            protein_b=clean_text(payload.get("protein_b")),
            protein_accessions=tuple(
                clean_text(item)
                for item in payload.get("protein_accessions", ())
                if clean_text(item)
            ),
            ligand_id=clean_text(payload.get("ligand_id")),
            ligand_smiles=clean_text(payload.get("ligand_smiles") or payload.get("smiles")),
            ligand_chemical_series=clean_text(
                payload.get("ligand_chemical_series") or payload.get("chemical_series")
            ),
            pdb_id=clean_text(payload.get("pdb_id")).upper(),
            motif_domain_signatures=tuple(clean_text(item) for item in motif_values if clean_text(item)),
            source_dataset=clean_text(payload.get("source_dataset") or payload.get("dataset")),
            source_family=clean_text(payload.get("source_family")),
            assay_family=clean_text(payload.get("assay_family") or payload.get("assay")),
            measurement_type=clean_text(payload.get("measurement_type")),
            label_value=payload.get("label_value"),
            provenance_note=clean_text(payload.get("provenance_note") or payload.get("provenance")),
            extra_metadata=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "split": self.split,
            "protein_a": self.protein_a,
            "protein_b": self.protein_b,
            "protein_accessions": list(self.protein_accessions),
            "ligand_id": self.ligand_id,
            "ligand_smiles": self.ligand_smiles,
            "ligand_chemical_series": self.ligand_chemical_series,
            "pdb_id": self.pdb_id,
            "motif_domain_signatures": list(self.motif_domain_signatures),
            "source_dataset": self.source_dataset,
            "source_family": self.source_family,
            "assay_family": self.assay_family,
            "measurement_type": self.measurement_type,
            "label_value": self.label_value,
            "provenance_note": self.provenance_note,
            "extra_metadata": self.extra_metadata,
        }


@dataclass(frozen=True)
class DatasetManifest:
    manifest_id: str
    title: str
    task_type: str
    label_type: str
    entity_kind: str
    split_membership_mode: str
    records: tuple[DatasetRecord, ...]
    notes: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DatasetManifest":
        notes = payload.get("notes") or ()
        if isinstance(notes, str):
            notes = [notes]
        return cls(
            manifest_id=clean_text(payload.get("manifest_id")),
            title=clean_text(payload.get("title") or payload.get("dataset_name")),
            task_type=clean_text(payload.get("task_type")),
            label_type=clean_text(payload.get("label_type")),
            entity_kind=clean_text(payload.get("entity_kind")).replace("-", "_"),
            split_membership_mode=clean_text(payload.get("split_membership_mode") or "explicit_manifest"),
            records=tuple(DatasetRecord.from_dict(item) for item in payload.get("records", ())),
            notes=tuple(clean_text(item) for item in notes if clean_text(item)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "title": self.title,
            "task_type": self.task_type,
            "label_type": self.label_type,
            "entity_kind": self.entity_kind,
            "split_membership_mode": self.split_membership_mode,
            "records": [record.to_dict() for record in self.records],
            "notes": list(self.notes),
        }
