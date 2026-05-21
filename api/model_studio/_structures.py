"""PDB and mmCIF atom-record parsers.

Extracted from the original 9000-line ``runtime.py`` (May 2026 review
P2-1). Each parser returns a list of plain ``dict`` records with the
following uniform shape:

    {
        "record_type":  "ATOM" | "HETATM",
        "resname":      str,
        "chain_id":     str,
        "resseq":       str,
        "icode":        str,
        "coord":        tuple[float, float, float],
        "atom_name":    str,
        "element":      str,
    }

The dicts are deliberately not typed as a dataclass: every downstream
consumer (the structure-feature builders in ``runtime.py``) expects
to consume them dict-style, and re-typing them would force changes
across hundreds of call sites for no semantic gain.

These parsers are intentionally hand-rolled rather than going through
``biopython``/``gemmi`` -- the runtime ships without those optional
dependencies and the studio's structure-pipeline only needs the small
subset of records modelled above. Anything else (alt-locs, occupancy,
bfactor, multi-model files) is silently ignored.
"""
from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from api.model_studio._text import clean_text


def iter_pdb_atoms(path: Path) -> list[dict[str, Any]]:
    """Parse fixed-column PDB ATOM/HETATM lines from ``path``.

    Bad coordinates (non-floats) silently skip the line; the studio's
    structure pipeline already treats partial structures as a soft
    warning rather than a hard failure.
    """
    atoms: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue
            try:
                coord = (
                    float(line[30:38]),
                    float(line[38:46]),
                    float(line[46:54]),
                )
            except ValueError:
                continue
            atom_name = line[12:16].strip().upper() or "UNK"
            atoms.append(
                {
                    "record_type": line[:6].strip(),
                    "resname": line[17:20].strip().upper(),
                    "chain_id": line[21].strip() or "_",
                    "resseq": line[22:26].strip(),
                    "icode": line[26].strip() or "_",
                    "coord": coord,
                    "atom_name": atom_name,
                    "element": (line[76:78].strip().upper() or atom_name[:1] or "OTHER").replace(
                        " ", ""
                    ),
                }
            )
    return atoms


def iter_mmcif_atoms(path: Path) -> list[dict[str, Any]]:
    """Parse mmCIF ``_atom_site`` loop records from ``path``.

    Single-block, single-model only. Multi-model files take the first
    model implicitly because we don't filter on ``pdbx_PDB_model_num``.
    """
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    headers: list[str] = []
    atoms: list[dict[str, Any]] = []
    in_atom_loop = False
    data_started = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped == "loop_" and not in_atom_loop:
            headers = []
            data_started = False
            continue
        if stripped.startswith("_atom_site."):
            in_atom_loop = True
            headers.append(stripped)
            continue
        if in_atom_loop and stripped.startswith("_") and not data_started:
            headers.append(stripped)
            continue
        if in_atom_loop and stripped == "#":
            break
        if in_atom_loop:
            data_started = True
            tokens = shlex.split(stripped, posix=True)
            if len(tokens) != len(headers):
                tokens = stripped.split()
            if len(tokens) != len(headers):
                continue
            record = dict(zip(headers, tokens, strict=False))
            record_type = clean_text(record.get("_atom_site.group_PDB"))
            if record_type not in {"ATOM", "HETATM"}:
                continue
            try:
                coord = (
                    float(record.get("_atom_site.Cartn_x") or 0.0),
                    float(record.get("_atom_site.Cartn_y") or 0.0),
                    float(record.get("_atom_site.Cartn_z") or 0.0),
                )
            except ValueError:
                continue
            atoms.append(
                {
                    "record_type": record_type,
                    "resname": (
                        clean_text(record.get("_atom_site.auth_comp_id"))
                        or clean_text(record.get("_atom_site.label_comp_id"))
                    ).upper(),
                    "chain_id": (
                        clean_text(record.get("_atom_site.auth_asym_id"))
                        or clean_text(record.get("_atom_site.label_asym_id"))
                        or "_"
                    ),
                    "resseq": clean_text(record.get("_atom_site.auth_seq_id"))
                    or clean_text(record.get("_atom_site.label_seq_id")),
                    "icode": clean_text(record.get("_atom_site.pdbx_PDB_ins_code")) or "_",
                    "coord": coord,
                    "atom_name": (
                        clean_text(record.get("_atom_site.auth_atom_id"))
                        or clean_text(record.get("_atom_site.label_atom_id"))
                        or "UNK"
                    ).upper(),
                    "element": (
                        clean_text(record.get("_atom_site.type_symbol")) or "OTHER"
                    ).upper(),
                }
            )
    return atoms


def iter_structure_atoms(path: Path) -> list[dict[str, Any]]:
    """Dispatch to the appropriate parser by file extension."""
    if path.suffix.lower() == ".cif":
        return iter_mmcif_atoms(path)
    return iter_pdb_atoms(path)
