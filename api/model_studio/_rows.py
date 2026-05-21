"""Pure-data helpers operating on :class:`BenchmarkRow` instances.

Extracted from the original 9000-line ``runtime.py`` (May 2026 review
P2-1). These functions are tiny, pure, and stateless -- they only
read from ``row`` and return a new row or a derived value. Pulling
them out gives the materializer code paths a clean, testable
dependency surface and removes ~50 lines of metadata-stamping
boilerplate from ``runtime.py``.

The more elaborate row-level functions that depend on the label /
measurement / metadata catalogs (``_label_payload``,
``_row_preview_payload``) deliberately stay in ``runtime.py`` because
they reach into the wider catalog state.
"""
from __future__ import annotations

from typing import Any

from api.model_studio._text import clean_text
from api.model_studio._types import BenchmarkRow


def copy_row_with_metadata(
    row: BenchmarkRow,
    *,
    split: str | None = None,
    metadata_updates: dict[str, Any] | None = None,
    source_dataset: str | None = None,
) -> BenchmarkRow:
    """Return a copy of ``row`` with the supplied metadata stamped on top.

    Every other field is preserved by reference (BenchmarkRow's
    metadata is a mutable dict, so we deep-copy that one). ``split``
    and ``source_dataset`` override row-level fields when provided.
    """
    metadata = dict(row.metadata)
    metadata.update(metadata_updates or {})
    return BenchmarkRow(
        split=split or row.split,
        pdb_id=row.pdb_id,
        exp_dg=row.exp_dg,
        source_dataset=source_dataset or row.source_dataset,
        complex_type=row.complex_type,
        protein_accessions=row.protein_accessions,
        ligand_chains=row.ligand_chains,
        receptor_chains=row.receptor_chains,
        structure_file=row.structure_file,
        resolution=row.resolution,
        release_year=row.release_year,
        temperature_k=row.temperature_k,
        metadata=metadata,
    )


def protein_accession_signature(row: BenchmarkRow) -> str:
    """Stable signature for a row's protein content.

    Returns a sorted pipe-joined accession list, or ``pdb:<id>`` when
    no accessions are mapped. Used as a clustering key in the
    governed-subset balancing logic and inside ``BenchmarkRow.example_id``.
    """
    return "|".join(sorted(row.protein_accessions)) or f"pdb:{row.pdb_id}"


def row_description(row: BenchmarkRow) -> str:
    """One-line human-readable description of a row.

    Surfaces in the GUI's row-preview cards and in dropped-row
    diagnostics, so the format is deliberately compact and dataset-
    annotated rather than ID-only.
    """
    chains = "/".join((*row.ligand_chains, *row.receptor_chains)) or "unknown chains"
    accessions = ", ".join(row.protein_accessions[:3]) or "unmapped proteins"
    ligand_component = clean_text(row.metadata.get("Ligand Canonical Component Id"))
    return (
        f"{row.source_dataset} | {chains}"
        + (f" | ligand {ligand_component}" if ligand_component else "")
        + " | "
        f"{len(row.protein_accessions)} protein accession(s): {accessions}"
    )


def measurement_type(row: BenchmarkRow) -> str:
    """Extract the assay measurement type tag from row metadata.

    Defaults to ``"unknown"`` when no measurement type was recorded,
    which is the expected fallback for the older robust/expanded rows
    that predate the assay-family annotation pass.
    """
    return clean_text(row.metadata.get("Measurement Type")) or "unknown"
