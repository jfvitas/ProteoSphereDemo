"""Davis + KIBA DTA benchmark parser.

Davis (Davis et al. 2011)  → 442 kinases × 68 inhibitors, Kd in nM
KIBA  (Tang et al. 2014)   → 229 proteins × 2,111 ligands, KIBA score

Both datasets share the DeepDTA layout:
    proteins.txt      JSON: gene_or_uniprot -> sequence
    ligands_iso.txt   JSON: chembl_or_pubchem_id -> SMILES
    Y                 pickled np.float32 matrix (n_ligands, n_proteins)

For Davis, Y entries are Kd in nM (10000 = "no measurable binding").
We convert to pKd = 9 - log10(Kd_nM).
For KIBA, Y is already a transformed "KIBA score" — keep as-is.

Output (3 parquet fragments):
    interactions.parquet     one row per (protein, ligand) pair with a label
        edge_id           "protein_ligand:<benchmark>:<p>:<l>"
        protein_ref       "protein_name:<benchmark>:<key>" — names not uniprot
        ligand_ref        "ligand:<benchmark>:<key>"
        protein_key       Davis: gene symbol; KIBA: UniProt
        ligand_key        Davis: PubChem CID; KIBA: ChEMBL ID
        benchmark         "davis" | "kiba"
        label_kind        "pKd_from_Kd_nM" (davis) | "kiba_score" (kiba)
        label_value       float
        raw_value         original Kd in nM (davis) or raw KIBA score
        snapshot_id, source
    proteins.parquet         one row per unique protein
    ligands.parquet          one row per unique ligand
"""

from __future__ import annotations

import json
import math
import pickle
from pathlib import Path

from ..state import SourceState, INGEST_ROOT
from . import register_parser, ParseResult

try:
    import numpy as np
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:
    _HAS_ARROW = False

SOURCE_ID = "davis_kiba"
SOURCE_LABEL = "Davis + KIBA DTA benchmarks"


def _read_y_matrix(path: Path):
    """The Y file is pickled latin1-encoded numpy array (Python 2 pickle)."""
    with open(path, "rb") as f:
        return pickle.load(f, encoding="latin1")


def _parse_benchmark(proteins_path: Path, ligands_path: Path, y_path: Path,
                     benchmark: str, snapshot_id: str) -> tuple[list[dict], list[dict], list[dict], dict]:
    """Returns (interactions, proteins, ligands, stats)."""
    with open(proteins_path, encoding="utf-8") as f:
        proteins = json.load(f)
    with open(ligands_path, encoding="utf-8") as f:
        ligands = json.load(f)
    Y = _read_y_matrix(y_path) if _HAS_NUMPY else None

    prot_names = list(proteins.keys())
    lig_keys = list(ligands.keys())
    prot_rows = [{
        "protein_key": pn,
        "protein_ref": f"protein_name:{benchmark}:{pn}",
        "sequence": proteins[pn],
        "sequence_length": len(proteins[pn]),
        "benchmark": benchmark,
        "snapshot_id": snapshot_id,
        "source": SOURCE_ID,
    } for pn in prot_names]
    lig_rows = [{
        "ligand_key": lk,
        "ligand_ref": f"ligand:{benchmark}:{lk}",
        "smiles": ligands[lk],
        "smiles_length": len(ligands[lk]),
        "benchmark": benchmark,
        "snapshot_id": snapshot_id,
        "source": SOURCE_ID,
    } for lk in lig_keys]

    interactions: list[dict] = []
    stats = {"benchmark": benchmark, "n_proteins": len(prot_names),
             "n_ligands": len(lig_keys), "n_cells": 0,
             "n_defined_labels": 0, "n_saturated": 0}
    if Y is None or not _HAS_NUMPY:
        return interactions, prot_rows, lig_rows, stats

    n_lig, n_prot = Y.shape
    stats["n_cells"] = int(n_lig * n_prot)
    for li, lk in enumerate(lig_keys):
        for pi, pn in enumerate(prot_names):
            raw = float(Y[li, pi])
            if math.isnan(raw) or raw <= 0:
                continue
            if benchmark == "davis":
                # Convert Kd (nM) → pKd; saturation at 10000 → pKd 5.0
                is_saturated = raw >= 9999.0
                if is_saturated:
                    stats["n_saturated"] += 1
                label_kind = "pKd_from_Kd_nM"
                label_value = 9.0 - math.log10(raw)
            else:  # kiba
                is_saturated = False
                label_kind = "kiba_score"
                label_value = raw
            interactions.append({
                "edge_id": f"protein_ligand:{benchmark}:{pn}:{lk}",
                "protein_ref": f"protein_name:{benchmark}:{pn}",
                "ligand_ref":  f"ligand:{benchmark}:{lk}",
                "protein_key": pn,
                "ligand_key":  lk,
                "benchmark":   benchmark,
                "label_kind":  label_kind,
                "label_value": label_value,
                "raw_value":   raw,
                "is_saturated": is_saturated,
                "snapshot_id": snapshot_id,
                "source":      SOURCE_ID,
            })
            stats["n_defined_labels"] += 1
    return interactions, prot_rows, lig_rows, stats


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    if _HAS_ARROW:
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    jsonl = path.with_suffix(".jsonl")
    with open(jsonl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return jsonl


def _parse_impl(state: SourceState, *, snapshot_dir: Path | None = None) -> ParseResult:
    src_dir = Path(state.local_path)
    if not src_dir.exists():
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {},
                           errors=[f"local_path missing: {src_dir}"])

    # Files arrive flat (downloader doesn't preserve subdir). Davis + KIBA
    # both ship proteins.txt / ligands_iso.txt / Y — they'd collide. The
    # downloader uses last-segment-of-URL as filename, so we get one set;
    # subsequent files OVERWRITE. To handle both, we detect which set is
    # present by row count after pickle-loading Y.
    files = {p.name: p for p in src_dir.iterdir()}
    proteins_p = files.get("proteins.txt")
    ligands_p  = files.get("ligands_iso.txt")
    y_p        = files.get("Y")
    if not (proteins_p and ligands_p and y_p):
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {},
                           errors=[f"missing one of proteins.txt / ligands_iso.txt / Y under {src_dir}"])

    # Detect benchmark by row counts after a quick pickle peek
    Y = _read_y_matrix(y_p)
    if Y is not None and hasattr(Y, "shape"):
        n_lig, n_prot = Y.shape
        if n_prot == 442 and n_lig == 68:
            benchmark = "davis"
        elif n_prot == 229 and n_lig == 2111:
            benchmark = "kiba"
        else:
            # Best-effort heuristic for non-canonical mirrors
            benchmark = "davis" if n_prot < 1000 else "kiba"
    else:
        benchmark = "davis"

    # Use the caller's source_id (davis or kiba) so catalog view names are
    # benchmark-specific, not the shared parser module's name.
    sid = state.source_id
    out_dir = snapshot_dir or (INGEST_ROOT / "normalized" / "ligand_assay" / sid / state.snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    interactions, prot_rows, lig_rows, stats = _parse_benchmark(
        proteins_p, ligands_p, y_p, benchmark, state.snapshot_id,
    )
    paths = {
        "interactions": str(_write_parquet(interactions, out_dir / "interactions.parquet")),
        "proteins":     str(_write_parquet(prot_rows,    out_dir / "proteins.parquet")),
        "ligands":      str(_write_parquet(lig_rows,     out_dir / "ligands.parquet")),
    }
    provenance = {
        "claim_type": "ingest",
        "source_id": sid,
        "source_label": SOURCE_LABEL,
        "benchmark": benchmark,
        "snapshot_id": state.snapshot_id,
        "sha256": state.sha256,
        "input_paths": [str(proteins_p), str(ligands_p), str(y_p)],
        "output_paths": list(paths.values()),
        "row_counts": {"interactions": len(interactions), "proteins": len(prot_rows), "ligands": len(lig_rows)},
        "parse_stats": stats,
        "license": "Open (re-published from primary sources)",
        "url_base": "https://github.com/hkmztrk/DeepDTA",
        "notes": f"Detected benchmark='{benchmark}' from Y matrix shape. Davis Kd→pKd; KIBA keeps raw score.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return ParseResult(
        source_id=sid,
        snapshot_id=state.snapshot_id,
        row_counts={"interactions": len(interactions), "proteins": len(prot_rows), "ligands": len(lig_rows)},
        output_files=paths,
        provenance=provenance,
        warnings=([] if _HAS_ARROW else ["pyarrow not installed; emitted JSONL"]),
    )


# Same parser handles both benchmarks; detected from Y matrix shape.
register_parser("davis", _parse_impl)
register_parser("kiba",  _parse_impl)
