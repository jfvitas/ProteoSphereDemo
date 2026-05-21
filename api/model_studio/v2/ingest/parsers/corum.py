"""CORUM (mammalian protein complexes) parser.

CORUM publishes a ZIP archive containing `allComplexes.json`. Each record is
a curated multi-subunit complex, e.g.:
    {
      "ComplexID": 1,
      "ComplexName": "BCL6-HDAC4 complex",
      "Organism": "Human",
      "subunits(UniProt IDs)": "P41182;Q9P0J7",
      ...
    }

Two outputs:
    complexes.parquet    one row per complex (metadata + subunit list)
        complex_id      INTEGER
        complex_ref     "complex:corum:<id>"
        complex_name    text
        organism        text
        cell_line       text
        purification    text
        method          text
        evidence_pubmed text  (semicolon-joined where multiple)
        functional_class text
        n_subunits      INTEGER
        subunit_uniprots TEXT[] (json array as string for cross-DB compat)
        snapshot_id, source

    interactions.parquet   ALL pairs (i, j) within each complex (i<j)
        edge_id         "protein_protein:corum:{a}_{b}"  (canonical)
        a_ref, b_ref    "protein:<UniProt>"
        a_uniprot, b_uniprot
        complex_id, complex_ref, complex_name
        edge_type       "complex_membership"
        method          purification method
        evidence_source "CORUM"
        organism
        snapshot_id, source

Edges are deduplicated globally — if subunits P and Q appear in 5 complexes
together, we emit 5 rows (each with the complex context). Downstream
clustering / consolidation can collapse if needed.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..state import SourceState, INGEST_ROOT
from . import register_parser, ParseResult

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:
    _HAS_ARROW = False

SOURCE_ID = "corum"
SOURCE_LABEL = "CORUM (mammalian protein complexes)"


def _canonical_edge_id(a: str, b: str) -> str:
    lo, hi = sorted([a, b])
    return f"protein_protein:corum:{lo}_{hi}"


def _parse_subunit_list(raw: str) -> list[str]:
    """Subunit fields are semicolon-separated UniProt accessions, sometimes
    with extra whitespace. Some entries use sublist syntax "[P12345; Q67890]"
    indicating alternative isoforms — we treat them all as members."""
    if not raw:
        return []
    cleaned = (raw or "").replace("[", "").replace("]", "")
    parts = [p.strip() for p in cleaned.split(";")]
    # Filter to plausible UniProt accessions (6 or 10 chars, alphanumeric)
    out = []
    for p in parts:
        if not p:
            continue
        # Allow isoform suffix (e.g. P12345-2)
        if len(p) in (6, 10) or (10 < len(p) <= 12 and "-" in p):
            out.append(p)
    return out


def _find_complexes_json(src_dir: Path) -> tuple[Path, bytes]:
    """Locate the allComplexes.json file. Could be a plain JSON, JSON-in-ZIP,
    or the ZIP file itself with allComplexes.json inside."""
    # Direct JSON
    direct = src_dir / "allComplexes.json"
    if direct.exists():
        return direct, direct.read_bytes()
    # ZIP
    zips = list(src_dir.glob("*.zip"))
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                if name.endswith("allComplexes.json"):
                    return z / name, zf.read(name)
                # Some snapshots use .txt or .tsv flat dumps
                if name.endswith("allComplexes.txt") or name.endswith("coreComplexes.txt"):
                    return z / name, zf.read(name)
    raise FileNotFoundError(f"allComplexes.json not found in {src_dir}")


def parse_complexes(payload: bytes, snapshot_id: str) -> tuple[list[dict], list[dict], dict]:
    """Returns (complexes, pair_edges, stats)."""
    # CORUM ships as JSON in newer releases; some older snapshots are TSV.
    txt = payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(txt)
        records = data if isinstance(data, list) else (data.get("complexes") or data.get("records") or [])
    except json.JSONDecodeError:
        # Fallback: TSV with a header row
        records = _records_from_tsv(txt)

    complexes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[str] = set()
    stats = {"complexes_seen": 0, "complexes_kept": 0, "pairs_expanded": 0,
             "pairs_dropped_duplicate": 0}
    for rec in records:
        stats["complexes_seen"] += 1
        cid = rec.get("ComplexID") or rec.get("complex_id") or rec.get("id")
        if cid is None:
            continue
        try:
            cid = int(cid)
        except (ValueError, TypeError):
            continue
        cname = (rec.get("ComplexName") or rec.get("complex_name") or rec.get("Complex name") or "").strip()
        subunits_raw = (rec.get("subunits(UniProt IDs)") or rec.get("subunits_uniprot") or
                         rec.get("subunits") or "")
        subunits = _parse_subunit_list(subunits_raw)
        organism = rec.get("Organism") or rec.get("organism")
        purification = rec.get("Protein complex purification method") or rec.get("purification_method")
        cell_line = rec.get("Cell line") or rec.get("cell_line")
        method = rec.get("Method") or rec.get("Detection method")
        functional_class = rec.get("Complex comment") or rec.get("Functional Comment") or rec.get("FunctionalClass")
        pubmed = rec.get("PubMed ID") or rec.get("PubMed id") or rec.get("pubmed_id")
        if pubmed and isinstance(pubmed, list):
            pubmed = ";".join(str(x) for x in pubmed)

        complexes.append({
            "complex_id": cid,
            "complex_ref": f"complex:corum:{cid}",
            "complex_name": cname or None,
            "organism": organism,
            "cell_line": cell_line,
            "purification": purification,
            "method": method,
            "evidence_pubmed": str(pubmed) if pubmed else None,
            "functional_class": functional_class,
            "n_subunits": len(subunits),
            "subunit_uniprots": json.dumps(subunits),
            "snapshot_id": snapshot_id,
            "source": SOURCE_ID,
        })
        stats["complexes_kept"] += 1

        # Expand to all unordered pairs within the complex
        for i in range(len(subunits)):
            for j in range(i + 1, len(subunits)):
                a, b = subunits[i], subunits[j]
                if a == b:
                    continue
                eid = _canonical_edge_id(a, b) + f":c{cid}"  # disambiguate per-complex
                if eid in seen_edges:
                    stats["pairs_dropped_duplicate"] += 1
                    continue
                seen_edges.add(eid)
                lo, hi = sorted([a, b])
                edges.append({
                    "edge_id": eid,
                    "a_ref": f"protein:{lo}",
                    "b_ref": f"protein:{hi}",
                    "a_uniprot": lo,
                    "b_uniprot": hi,
                    "complex_id": cid,
                    "complex_ref": f"complex:corum:{cid}",
                    "complex_name": cname or None,
                    "edge_type": "complex_membership",
                    "method": purification or method,
                    "evidence_source": "CORUM",
                    "organism": organism,
                    "snapshot_id": snapshot_id,
                    "source": SOURCE_ID,
                })
                stats["pairs_expanded"] += 1
    return complexes, edges, stats


def _records_from_tsv(txt: str) -> list[dict]:
    """Best-effort fallback when the archive ships a TSV instead of JSON."""
    lines = txt.splitlines()
    if not lines:
        return []
    header = lines[0].split("\t")
    out = []
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) < len(header):
            cols = cols + [""] * (len(header) - len(cols))
        out.append({h: c for h, c in zip(header, cols)})
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
    try:
        json_path, payload = _find_complexes_json(src_dir)
    except FileNotFoundError as exc:
        return ParseResult(SOURCE_ID, state.snapshot_id, {}, {}, {}, errors=[str(exc)])

    out_dir = snapshot_dir or (INGEST_ROOT / "normalized" / "interaction_network" / SOURCE_ID / state.snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    complexes, edges, stats = parse_complexes(payload, state.snapshot_id)
    out_paths = {
        "complexes": str(_write_parquet(complexes, out_dir / "complexes.parquet")),
        "interactions": str(_write_parquet(edges, out_dir / "interactions.parquet")),
    }
    provenance = {
        "claim_type": "ingest",
        "source_id": SOURCE_ID,
        "source_label": SOURCE_LABEL,
        "snapshot_id": state.snapshot_id,
        "sha256": state.sha256,
        "input_paths": [str(json_path)],
        "output_paths": list(out_paths.values()),
        "row_counts": {"complexes": len(complexes), "interactions": len(edges)},
        "parse_stats": stats,
        "license": "Open (academic; Helmholtz Munich)",
        "url_base": "https://mips.helmholtz-muenchen.de/corum/",
        "notes": "Pair edges expanded from complex subunits; per-complex disambiguation in edge_id.",
    }
    (out_dir / "manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    return ParseResult(
        source_id=SOURCE_ID,
        snapshot_id=state.snapshot_id,
        row_counts={"complexes": len(complexes), "interactions": len(edges)},
        output_files=out_paths,
        provenance=provenance,
        warnings=([] if _HAS_ARROW else ["pyarrow not installed; emitted JSONL"]),
    )


register_parser(SOURCE_ID, _parse_impl)
