"""HuRI (Human Reference Interactome) parser.

HuRI v1 is published as a single TSV with two columns: Ensembl gene id A
and Ensembl gene id B. Every row is a binary PPI from systematic Y2H screens.

We need UniProt accessions for the warehouse, so the parser does NOT try to
join to UniProt at parse time — it preserves the raw Ensembl ids and adds a
``ref`` field flagged ``ensembl_gene:<ENSG…>``. Catalog consolidation /
cross-relationship pipelines do the UniProt join later.

Output (1 parquet fragment):
    interactions.parquet
        edge_id              "protein_protein:huri:{a}_{b}"  (canonical, A < B)
        a_ref                "ensembl_gene:<id>"
        b_ref                "ensembl_gene:<id>"
        a_ensembl_gene       raw column A
        b_ensembl_gene       raw column B
        edge_type            "binary_ppi"
        method               "y2h"
        evidence_source      "HuRI"
        snapshot_id          ingest snapshot timestamp
        source               "huri"
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from ..state import SourceState, INGEST_ROOT
from . import register_parser, ParseResult

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:
    _HAS_ARROW = False

SOURCE_ID = "huri"
SOURCE_LABEL = "HuRI (Human Reference Interactome)"


def _canonical_edge_id(a: str, b: str) -> str:
    """Order-independent edge id so A→B and B→A collapse to one row."""
    lo, hi = sorted([a, b])
    return f"protein_protein:huri:{lo}_{hi}"


def _read_huri_tsv(path: Path):
    """HuRI.tsv is a plain 2-column TSV. No header line in some versions;
    in newer versions the first row is `Ensembl_gene_id_A\tEnsembl_gene_id_B`."""
    with open(path, encoding="utf-8", newline="") as f:
        # Peek the first line — if it starts with "ENSG", there's no header.
        first = f.readline()
        if first.startswith("ENSG"):
            yield first.strip().split("\t")
        # otherwise consume the header
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield line.split("\t")


def parse_interactions(path: Path, snapshot_id: str) -> list[dict]:
    """Returns canonicalised binary PPIs from HuRI.tsv. Deduplicated by
    canonical edge id (A,B == B,A == same row)."""
    seen: set[str] = set()
    out: list[dict] = []
    for cols in _read_huri_tsv(path):
        if len(cols) < 2:
            continue
        a, b = (cols[0] or "").strip(), (cols[1] or "").strip()
        if not a or not b or a == b:
            continue
        eid = _canonical_edge_id(a, b)
        if eid in seen:
            continue
        seen.add(eid)
        lo, hi = sorted([a, b])
        out.append({
            "edge_id": eid,
            "a_ref": f"ensembl_gene:{lo}",
            "b_ref": f"ensembl_gene:{hi}",
            "a_ensembl_gene": lo,
            "b_ensembl_gene": hi,
            "edge_type": "binary_ppi",
            "method": "y2h",
            "evidence_source": "HuRI",
            "snapshot_id": snapshot_id,
            "source": SOURCE_ID,
        })
    return out


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
    else:
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
    tsv = src_dir / "HuRI.tsv"
    if not tsv.exists():
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {},
                           errors=[f"HuRI.tsv not found under {src_dir}"])
    out_dir = snapshot_dir or (INGEST_ROOT / "normalized" / "interaction_network" / SOURCE_ID / state.snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = parse_interactions(tsv, state.snapshot_id)
    out_path = _write_parquet(rows, out_dir / "interactions.parquet")
    provenance = {
        "claim_type": "ingest",
        "source_id": SOURCE_ID,
        "source_label": SOURCE_LABEL,
        "snapshot_id": state.snapshot_id,
        "sha256": state.sha256,
        "input_paths": [str(tsv)],
        "output_paths": [str(out_path)],
        "row_counts": {"interactions": len(rows)},
        "license": "Open (Lab of Marc Vidal, Dana-Farber)",
        "url_base": "http://www.interactome-atlas.org/",
        "notes": "Binary PPIs from systematic Y2H screens. Edges canonicalised by sorted Ensembl gene id pair.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return ParseResult(
        source_id=SOURCE_ID,
        snapshot_id=state.snapshot_id,
        row_counts={"interactions": len(rows)},
        output_files={"interactions": str(out_path)},
        provenance=provenance,
        warnings=([] if _HAS_ARROW else ["pyarrow not installed; emitted JSONL"]),
    )


register_parser(SOURCE_ID, _parse_impl)
