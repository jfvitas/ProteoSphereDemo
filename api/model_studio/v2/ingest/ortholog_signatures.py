"""Ortholog cluster signatures via OrthoDB cross-references.

OrthoDB groups proteins by evolutionary descent — orthologs and paralogs
share a cluster id. Used as a sequence-similarity-equivalent for the
Splits screen: two proteins in the same OrthoDB cluster are functionally
analogous and create a leakage edge for many tasks (especially DTI).

Source: legacy ``cross_references`` table has 57,115,256 OrthoDB rows
(database = 'OrthoDB'). Each row is (accession, identifier=cluster_id).

Output (one parquet, registered as ``v2_ortholog_cluster_membership``):
    uniprot          UniProt accession
    source           "gtopdb" | "davis" | "kiba" | "huri" | "hippie"
    orthodb_cluster  e.g. "1334at2759"  (OrthoDB v11 group at a taxonomic level)
    snapshot_id

Leakage edge computation (JIT, not stored):
    SELECT a.uniprot, b.uniprot
    FROM v2_ortholog_cluster_membership a
    JOIN v2_ortholog_cluster_membership b
      ON a.orthodb_cluster = b.orthodb_cluster AND a.uniprot < b.uniprot

Storage budget: 57 K v2 UniProts × ~10 OrthoDB groups each × ~120 B
≈ 70 MB max — typically ~5-10 MB once restricted to the v2 universe.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name
from .bridges import LEGACY_CATALOG
from .sequence_signatures import collect_uniprot_universe


def _legacy() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(LEGACY_CATALOG), read_only=True)


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


def build_ortholog_membership(snapshot_id: str | None = None,
                              batch_size: int = 5_000) -> dict:
    """Resolve every v2 UniProt to its OrthoDB cluster set."""
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    universe = collect_uniprot_universe()
    if not universe:
        return {"error": "no v2 UniProts; run bridges first"}
    uniprots = list(universe.keys())

    legacy = _legacy()
    rows: list[dict] = []
    seen_pairs: set[tuple[str, str, str]] = set()
    stats = {"universe_size": len(uniprots), "uniprots_with_ortholog": 0, "total_memberships": 0}
    try:
        for start in range(0, len(uniprots), batch_size):
            batch = uniprots[start:start + batch_size]
            placeholders = ", ".join(["?"] * len(batch))
            sql = (
                "SELECT accession, external_id FROM cross_references "
                f"WHERE database = 'OrthoDB' AND accession IN ({placeholders})"
            )
            for acc, cluster in legacy.execute(sql, batch).fetchall():
                if not cluster:
                    continue
                sources = universe.get(acc, [])
                for src in sources or [None]:
                    key = (acc, src or "", cluster)
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    rows.append({
                        "uniprot":         acc,
                        "source":          src,
                        "orthodb_cluster": cluster,
                        "snapshot_id":     snapshot_id,
                    })
        stats["total_memberships"] = len(rows)
        stats["uniprots_with_ortholog"] = len({r["uniprot"] for r in rows})
    finally:
        legacy.close()

    out_dir = (INGEST_ROOT / "normalized" / "similarity_signatures"
               / "v2_ortholog_membership" / snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _write_parquet(rows, out_dir / "membership.parquet")

    view_name = _safe_view_name("v2", "ortholog_cluster_membership")
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
