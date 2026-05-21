"""Core dataclasses shared across the runtime modules.

Extracted from the original 9000-line ``runtime.py`` (May 2026 review
P2-1). Owning these in a small standalone module breaks the circular
import that would otherwise exist between ``runtime.py`` and the
helper modules that operate on these types (``_rows.py`` etc.).

Kept deliberately small: no module-level state, no I/O, only the
data shapes plus their ``to_dict()`` / ``example_id`` conveniences.
``example_id`` inlines the small helpers it needs (measurement-type
extraction, accession signature) rather than importing from
``runtime`` to avoid a circular dependency.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from api.model_studio._text import clean_text


@dataclass(slots=True)
class DatasetDescriptor:
    """A registered dataset: its splits on disk and beta-lane status."""

    dataset_ref: str
    label: str
    task_type: str
    split_strategy: str
    train_csv: Path
    val_csv: Path | None
    test_csv: Path
    source_manifest: Path
    row_count: int
    tags: tuple[str, ...]
    maturity: str
    catalog_status: str = "lab"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_ref": self.dataset_ref,
            "label": self.label,
            "task_type": self.task_type,
            "split_strategy": self.split_strategy,
            "train_csv": str(self.train_csv),
            "val_csv": str(self.val_csv) if self.val_csv else None,
            "test_csv": str(self.test_csv),
            "source_manifest": str(self.source_manifest),
            "row_count": self.row_count,
            "tags": list(self.tags),
            "maturity": self.maturity,
            "catalog_status": self.catalog_status,
        }


@dataclass(slots=True)
class BenchmarkRow:
    """A single labelled example: PDB, label, mapped accessions, structure file.

    ``example_id`` deliberately inlines its dependencies (measurement
    type from metadata, sorted accession signature) so this class can
    live in a leaf module without depending on any runtime helpers.
    """

    split: str
    pdb_id: str
    exp_dg: float
    source_dataset: str
    complex_type: str
    protein_accessions: tuple[str, ...]
    ligand_chains: tuple[str, ...]
    receptor_chains: tuple[str, ...]
    structure_file: Path
    resolution: float
    release_year: int
    temperature_k: float
    metadata: dict[str, Any]

    @property
    def example_id(self) -> str:
        # Inline the previously-external helpers
        # ``_measurement_type`` and ``_protein_accession_signature``
        # so ``BenchmarkRow`` has no runtime-side import dependency.
        measurement = clean_text(self.metadata.get("Measurement Type")) or "unknown"
        protein_sig = (
            "|".join(sorted(self.protein_accessions)) or f"pdb:{self.pdb_id}"
        )
        payload = "|".join(
            (
                self.split,
                self.pdb_id,
                self.source_dataset,
                measurement,
                protein_sig,
                f"{self.exp_dg:.4f}",
            )
        )
        fingerprint = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
        return f"{self.split}:{self.pdb_id}:{fingerprint}"


@dataclass(slots=True)
class ResidueRecord:
    """A single residue extracted from a structure file."""

    residue_id: str
    chain_id: str
    resname: str
    coord: tuple[float, float, float]
    atom_count: int
    partner: str
    water_contact: bool = False


@dataclass(slots=True)
class AtomRecord:
    """A single atom extracted from a structure file."""

    atom_id: str
    residue_id: str
    atom_name: str
    element: str
    chain_id: str
    partner: str
    coord: tuple[float, float, float]
    water_contact: bool = False
