"""Protein-side sequence-similarity signatures via UniRef cluster membership.

UniProt publishes official UniRef clusters at three identity thresholds:
    UniRef100 — sequence-identical (modulo isoforms)
    UniRef90  — ≥90 % identity to representative
    UniRef50  — ≥50 % identity to representative

The legacy warehouse stores these on every UniProt accession in the
``similarity_signatures`` table (one row per UniProt × 262 M rows).
We **don't recompute these with MMseqs2** — that would replace a
pipeline-maintained authoritative dataset with our own. Instead we
**materialise a cluster-membership table** restricted to the UniProts
that appear in our new sources (or their resolved bridges), which lets
the Splits screen compute leakage edges JIT via a self-join.

Output (one parquet, registered as ``v2_sequence_cluster_membership``):
    uniprot          UniProt accession
    source           which new source brought this UniProt in
                     ("gtopdb" | "davis" | "kiba" | "huri" | "hippie" | "3did")
    uniref100        e.g. "UniRef100_P00533"
    uniref90         e.g. "UniRef90_P00533"
    uniref50         e.g. "UniRef50_Q03135"
    uniparc          UniParc id (for absolute identity within UniProt)
    taxon            "taxon:9606" (or similar)
    snapshot_id

Leakage edges (computed JIT, not stored):
    SELECT a.uniprot AS a, b.uniprot AS b, 'uniref50' AS threshold
    FROM v2_sequence_cluster_membership a
    JOIN v2_sequence_cluster_membership b
      ON a.uniref50 = b.uniref50 AND a.uniprot < b.uniprot
    WHERE a.source IN ('davis','kiba') OR b.source IN ('davis','kiba')

Storage: ~20 K rows × ~120 B ≈ 2.5 MB. Trivial vs. full edge expansion
(which can blow out to millions of pairs for densely-clustered families).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name
from .bridges import LEGACY_CATALOG


def _legacy() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(LEGACY_CATALOG), read_only=True)


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


# Which v2 views to harvest UniProt accessions from. Each entry maps a
# (source_id, sql) — the sql returns one row per (uniprot, …) we want
# to put into the membership table.
SOURCE_PROBES = [
    {
        "source": "gtopdb",
        "sql": "SELECT DISTINCT uniprot FROM gtopdb_interactions WHERE uniprot IS NOT NULL",
    },
    {
        "source": "davis",
        "sql": "SELECT DISTINCT uniprot FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL",
    },
    {
        "source": "kiba",
        "sql": "SELECT DISTINCT uniprot FROM kiba_bridge_uniprot WHERE uniprot IS NOT NULL",
    },
    {
        "source": "huri",
        "sql": "SELECT DISTINCT uniprot FROM huri_bridge_uniprot WHERE uniprot IS NOT NULL",
    },
    {
        "source": "hippie",
        "sql": "SELECT DISTINCT uniprot FROM hippie_bridge_uniprot WHERE uniprot IS NOT NULL",
    },
    # 3did bridges to Pfam not UniProt; skipped here. Cross-Pfam leakage is
    # a separate signature handled in a follow-up.
]


def collect_uniprot_universe() -> dict:
    """Returns {uniprot: [list of sources that introduced it]}."""
    out: dict[str, list[str]] = {}
    v2 = _v2()
    try:
        for cfg in SOURCE_PROBES:
            try:
                rows = v2.execute(cfg["sql"]).fetchall()
            except Exception:
                continue
            for (u,) in rows:
                if not u:
                    continue
                out.setdefault(u, []).append(cfg["source"])
    finally:
        v2.close()
    return out


def build_membership(snapshot_id: str | None = None,
                     batch_size: int = 50_000) -> dict:
    """Resolve every new-source UniProt to its UniRef50/90/100 cluster
    via the legacy ``similarity_signatures`` table. Emit a parquet and
    register the v2 view ``v2_sequence_cluster_membership``."""
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    universe = collect_uniprot_universe()
    if not universe:
        return {"error": "no new-source UniProts found; run bridges first"}
    uniprots = list(universe.keys())

    legacy = _legacy()
    rows: list[dict] = []
    stats = {"universe_size": len(uniprots), "resolved": 0, "unresolved": 0}
    try:
        for start in range(0, len(uniprots), batch_size):
            batch = uniprots[start:start + batch_size]
            placeholders = ", ".join(["?"] * len(batch))
            sql = (
                "SELECT accession, uniref100_signature, uniref90_signature, "
                "  uniref50_signature, uniparc_signature, taxon_signature "
                "FROM similarity_signatures "
                f"WHERE accession IN ({placeholders})"
            )
            for r in legacy.execute(sql, batch).fetchall():
                acc, u100, u90, u50, upi, taxon = r
                sources = universe.get(acc, [])
                # One row per (uniprot, source) so the membership table is
                # easy to filter on `source` AND joinable by uniprot.
                for src in sources or [None]:
                    rows.append({
                        "uniprot":    acc,
                        "source":     src,
                        "uniref100":  u100,
                        "uniref90":   u90,
                        "uniref50":   u50,
                        "uniparc":    upi,
                        "taxon":      taxon,
                        "snapshot_id": snapshot_id,
                    })
                stats["resolved"] += 1
    finally:
        legacy.close()
    stats["unresolved"] = stats["universe_size"] - stats["resolved"]

    out_dir = INGEST_ROOT / "normalized" / "similarity_signatures" / "v2_uniref_membership" / snapshot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _write_parquet(rows, out_dir / "membership.parquet")

    # Register the view
    view_name = _safe_view_name("v2", "sequence_cluster_membership")
    if rows:
        v2 = _v2()
        try:
            v2.execute(f"DROP VIEW IF EXISTS {view_name}")
            v2.execute(f"DROP TABLE IF EXISTS {view_name}")
            v2.execute(
                f"CREATE VIEW {view_name} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            )
        finally:
            v2.close()

    audit = {
        "snapshot_id": snapshot_id,
        "output_path": str(out_path),
        "n_membership_rows": len(rows),
        "stats": stats,
        "view_name": view_name if rows else None,
        "leakage_edge_query_template": (
            "-- All UniRef50-level leakage edges touching a v2 source:\n"
            "SELECT a.uniprot AS a, b.uniprot AS b\n"
            f"FROM {view_name} a JOIN {view_name} b\n"
            "  ON a.uniref50 = b.uniref50 AND a.uniprot < b.uniprot"
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    except Exception:
        jsonl = path.with_suffix(".jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return jsonl


# ── Edge-summary helpers (compute JIT, don't store) ───────────────────

def leakage_edge_count(threshold: str = "uniref50") -> dict:
    """Compute the count of leakage edges at a threshold WITHOUT materialising
    them. Useful for the Splits screen's pre-flight."""
    if threshold not in ("uniref100", "uniref90", "uniref50"):
        raise ValueError(f"unknown threshold: {threshold}")
    view = _safe_view_name("v2", "sequence_cluster_membership")
    v2 = _v2()
    try:
        total = v2.execute(
            f"SELECT count(*) FROM {view} a JOIN {view} b "
            f"ON a.{threshold} = b.{threshold} AND a.uniprot < b.uniprot"
        ).fetchone()[0]
        cross = v2.execute(
            f"SELECT count(*) FROM {view} a JOIN {view} b "
            f"ON a.{threshold} = b.{threshold} AND a.uniprot < b.uniprot "
            f"AND a.source <> b.source"
        ).fetchone()[0]
        per_source = {}
        for src in ('gtopdb','davis','kiba','huri','hippie'):
            n = v2.execute(
                f"SELECT count(*) FROM {view} a JOIN {view} b "
                f"ON a.{threshold} = b.{threshold} AND a.uniprot < b.uniprot "
                f"AND (a.source = ? OR b.source = ?)", (src, src)
            ).fetchone()[0]
            per_source[src] = int(n)
    finally:
        v2.close()
    return {
        "threshold": threshold,
        "total_edges": int(total),
        "cross_source_edges": int(cross),
        "edges_touching_each_source": per_source,
    }
