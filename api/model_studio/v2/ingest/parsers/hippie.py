"""HIPPIE (Human Integrated Protein-Protein Interaction rEference) parser.

HIPPIE-current.mitab.txt is 18-column tab-separated. Real schema (sampled):

    [0]  ID Interactor A            "entrez gene:NNN"
    [1]  ID Interactor B            "entrez gene:NNN"
    [2]  Alt IDs A                  "uniprotkb:NAME_HUMAN"  (entry name, not accession)
    [3]  Alt IDs B                  "uniprotkb:NAME_HUMAN"
    [4]  Aliases A                  (often "-")
    [5]  Aliases B                  (often "-")
    [6]  Interaction Detection      "MI:NNNN(method)|MI:NNNN(method)|..."
    [7]  First Author               (often "-")
    [8]  Publication identifiers    "pubmed:NNNN|pubmed:MMMM|..."
    [9]  Taxid A                    "taxid:9606(Homo sapiens)"
    [10] Taxid B                    "taxid:9606(Homo sapiens)"
    [11] Interaction Types          MI codes (often "-")
    [12] Source Databases           "MI:NNNN(name)|biogrid|i2d|..."
    [13] Interaction Identifiers    (often "-")
    [14] Confidence Value           plain float in [0,1]
    [15] Presence In Other Species  (often empty)
    [16] Gene Name A
    [17] Gene Name B

Output (1 parquet):
    interactions.parquet
        edge_id              canonical "protein_protein:hippie:{a}_{b}"
        a_ref / b_ref        "entrez_gene:<id>" (HIPPIE's primary ID column)
        a_entrez_gene
        b_entrez_gene
        a_uniprot_entry_name "AL1A1_HUMAN" if available (col 2-3)
        b_uniprot_entry_name
        a_gene_name
        b_gene_name
        confidence           float
        detection_methods    pipe-joined MI codes
        evidence_source      pipe-joined source DBs
        pubmed_ids           pipe-joined "pubmed:N|pubmed:M"
        edge_type            "binary_ppi"
        snapshot_id, source
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from ..state import SourceState, INGEST_ROOT
from . import register_parser, ParseResult

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:
    _HAS_ARROW = False

SOURCE_ID = "hippie"
SOURCE_LABEL = "HIPPIE (Human Integrated PPI rEference)"

_ENTREZ_RE = re.compile(r"entrez\s*gene:(\d+)")
_UNIPROT_NAME_RE = re.compile(r"uniprotkb:([A-Z0-9]+_[A-Z]+)")


def _canonical_edge_id(a: str, b: str) -> str:
    lo, hi = sorted([a, b])
    return f"protein_protein:hippie:{lo}_{hi}"


def _extract_entrez(raw: str) -> str | None:
    m = _ENTREZ_RE.search(raw or "")
    return m.group(1) if m else None


def _extract_uniprot_name(raw: str) -> str | None:
    """Returns the first uniprotkb:NAME_SPECIES entry name encountered."""
    m = _UNIPROT_NAME_RE.search(raw or "")
    return m.group(1) if m else None


def _extract_float(raw: str) -> float | None:
    if raw is None or raw == "" or raw == "-":
        return None
    try:
        return float((raw or "").strip())
    except ValueError:
        return None


def parse_interactions(path: Path, snapshot_id: str) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    stats = {"total_lines": 0, "dropped_header": 0, "dropped_self": 0,
             "dropped_no_id": 0, "dropped_duplicate": 0, "kept": 0}
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter="\t")
        for cols in reader:
            stats["total_lines"] += 1
            if not cols or cols[0].startswith("#"):
                continue
            # Skip the MITAB header line (col[0] usually "ID Interactor A")
            if not cols[0].lower().startswith("entrez"):
                stats["dropped_header"] += 1
                continue
            if len(cols) < 15:
                continue
            a = _extract_entrez(cols[0])
            b = _extract_entrez(cols[1])
            if not a or not b:
                stats["dropped_no_id"] += 1
                continue
            if a == b:
                stats["dropped_self"] += 1
                continue
            eid = _canonical_edge_id(a, b)
            if eid in seen:
                stats["dropped_duplicate"] += 1
                continue
            seen.add(eid)
            lo, hi = sorted([a, b])
            # Keep entry-name + gene-name + method/pubmed pipe-joined as-is.
            a_name = _extract_uniprot_name(cols[2])
            b_name = _extract_uniprot_name(cols[3])
            methods    = (cols[6]  or "").strip() or None
            pubmed_ids = (cols[8]  or "").strip() or None
            source_dbs = (cols[12] or "").strip() or None
            confidence = _extract_float(cols[14])
            gene_a = (cols[16] if len(cols) > 16 else "").strip() or None
            gene_b = (cols[17] if len(cols) > 17 else "").strip() or None
            # Preserve A↔B mapping after canonicalisation
            if a == lo:
                a_keep, b_keep = a, b
                a_name_keep, b_name_keep = a_name, b_name
                gene_a_keep, gene_b_keep = gene_a, gene_b
            else:
                a_keep, b_keep = b, a
                a_name_keep, b_name_keep = b_name, a_name
                gene_a_keep, gene_b_keep = gene_b, gene_a
            rows.append({
                "edge_id": eid,
                "a_ref": f"entrez_gene:{a_keep}",
                "b_ref": f"entrez_gene:{b_keep}",
                "a_entrez_gene": a_keep,
                "b_entrez_gene": b_keep,
                "a_uniprot_entry_name": a_name_keep,
                "b_uniprot_entry_name": b_name_keep,
                "a_gene_name": gene_a_keep,
                "b_gene_name": gene_b_keep,
                "confidence": confidence,
                "detection_methods": methods,
                "evidence_source": source_dbs,
                "pubmed_ids": pubmed_ids,
                "edge_type": "binary_ppi",
                "snapshot_id": snapshot_id,
                "source": SOURCE_ID,
            })
            stats["kept"] += 1
    return rows, stats


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
    # The downloader saves as "HIPPIE-current.mitab.txt" (last URL segment)
    candidates = list(src_dir.glob("*.mitab*")) + list(src_dir.glob("*.txt"))
    if not candidates:
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {},
                           errors=[f"no mitab/txt file under {src_dir}"])
    tsv = candidates[0]
    out_dir = snapshot_dir or (INGEST_ROOT / "normalized" / "interaction_network" / SOURCE_ID / state.snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, stats = parse_interactions(tsv, state.snapshot_id)
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
        "parse_stats": stats,
        "license": "Open (academic; SoftBerry / Uni-Mainz)",
        "url_base": "http://cbdm-01.zdv.uni-mainz.de/~mschaefer/hippie/",
        "notes": "MITAB-format. Confidence is HIPPIE's integrated reliability score in [0,1].",
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
