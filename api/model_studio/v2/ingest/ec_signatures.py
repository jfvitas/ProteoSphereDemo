"""EC class signatures via legacy ``function_class_index``.

Enzyme Commission numbers (`EC x.y.z.w`) partition proteins by catalytic
function. Two proteins sharing an EC class are functional analogues and
constitute leakage edges for tasks that train on function-conditioned
features (kinase-vs-kinase splits, enzyme-pocket DTI, etc.).

Source: legacy ``function_class_index`` has 305,721 rows with
namespace='EC'. Each row is (accession, identifier=EC number, label).

Three classes of EC string are emitted as separate columns so the
Splits screen can pick its leakage granularity:
    ec4              full "2.7.11.1"
    ec3              "2.7.11"   (drop sub-sub-class)
    ec2              "2.7"      (drop sub-class)
Partial EC numbers like "3.6.4.-" produce a NULL at the more-specific
levels and a real value at the shorter levels (we just truncate on '.').

Output (one parquet, registered as ``v2_ec_class_membership``):
    uniprot          UniProt accession
    source           v2 source that introduced the UniProt
    ec4              full EC number ("2.7.11.1")
    ec3              "2.7.11"
    ec2              "2.7"
    label            description text (often empty)
    snapshot_id

Leakage edge example (JIT):
    SELECT a.uniprot, b.uniprot
    FROM v2_ec_class_membership a
    JOIN v2_ec_class_membership b
      ON a.ec3 = b.ec3 AND a.uniprot < b.uniprot AND a.ec3 IS NOT NULL
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


def _ec_levels(ec: str) -> tuple[str | None, str | None, str | None]:
    """Returns (ec4, ec3, ec2). Components ending in '-' are dropped from
    that level and shorter levels are kept only if their digits are real.
    """
    if not ec:
        return None, None, None
    parts = ec.split(".")
    # ec4 — only if all 4 parts are digits
    ec4 = ec if len(parts) == 4 and all(p.isdigit() for p in parts) else None
    # ec3 — first 3 parts all digits
    ec3 = ".".join(parts[:3]) if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]) else None
    # ec2 — first 2 parts all digits
    ec2 = ".".join(parts[:2]) if len(parts) >= 2 and all(p.isdigit() for p in parts[:2]) else None
    return ec4, ec3, ec2


def build_ec_membership(snapshot_id: str | None = None,
                        batch_size: int = 5_000) -> dict:
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    universe = collect_uniprot_universe()
    if not universe:
        return {"error": "no v2 UniProts; run bridges first"}
    uniprots = list(universe.keys())

    legacy = _legacy()
    rows: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    stats = {"universe_size": len(uniprots), "uniprots_with_ec": 0, "total_memberships": 0}
    try:
        for start in range(0, len(uniprots), batch_size):
            batch = uniprots[start:start + batch_size]
            placeholders = ", ".join(["?"] * len(batch))
            sql = (
                "SELECT accession, identifier, label FROM function_class_index "
                f"WHERE namespace = 'EC' AND accession IN ({placeholders})"
            )
            for acc, ec, label in legacy.execute(sql, batch).fetchall():
                if not ec:
                    continue
                ec4, ec3, ec2 = _ec_levels(ec)
                sources = universe.get(acc, [])
                for src in sources or [None]:
                    key = (acc, src or "", ec)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append({
                        "uniprot": acc,
                        "source": src,
                        "ec4": ec4,
                        "ec3": ec3,
                        "ec2": ec2,
                        "label": label or "",
                        "snapshot_id": snapshot_id,
                    })
        stats["total_memberships"] = len(rows)
        stats["uniprots_with_ec"] = len({r["uniprot"] for r in rows})
    finally:
        legacy.close()

    out_dir = (INGEST_ROOT / "normalized" / "similarity_signatures"
               / "v2_ec_class_membership" / snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _write_parquet(rows, out_dir / "membership.parquet")

    view_name = _safe_view_name("v2", "ec_class_membership")
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
