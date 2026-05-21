"""v2 ingest catalog — a DuckDB database that views every parsed source.

Lives at ``INGEST_ROOT / "catalog" / "v2.duckdb"`` and is **separate from
the legacy reference_library.duckdb** so v2 ingest can land without
disturbing the existing warehouse. Once a source proves out here, the
legacy consolidation pipeline can absorb its parquet fragments.

Per-source tables follow the convention ``<source_id>_<table>``:
    gtopdb_interactions
    gtopdb_ligands
    gtopdb_targets
    huri_interactions
    ...

Each is a **view over parquet** (no copy), so refreshing means re-pointing
the view at the new snapshot. The catalog also records a row in
``ingest_runs`` per (source × snapshot) for audit.

Public API:
    register_parquet_view(source_id, table_name, parquet_path)
    refresh_source(source_id, parse_result)  one-shot: drop+recreate views
    summary()                                row counts across the catalog
    query(sql)                               read-only DuckDB exec
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .parsers import ParseResult


_CATALOG_PATH = INGEST_ROOT / "catalog" / "v2.duckdb"


def _connect() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(_CATALOG_PATH))
    con.execute("""
        CREATE TABLE IF NOT EXISTS ingest_runs (
            run_id        VARCHAR PRIMARY KEY,
            source_id     VARCHAR NOT NULL,
            snapshot_id   VARCHAR NOT NULL,
            registered_at DOUBLE NOT NULL,
            row_counts    VARCHAR NOT NULL,     -- JSON
            output_files  VARCHAR NOT NULL,     -- JSON
            sha256        VARCHAR,
            license       VARCHAR
        )
    """)
    return con


def _normalize_path_for_duckdb(p: str | Path) -> str:
    """DuckDB on Windows accepts backslashes in `read_parquet` paths, but
    embedding them inside a SQL literal requires escaping. Use forward
    slashes uniformly."""
    return str(p).replace("\\", "/")


def _safe_view_name(source_id: str, table_name: str) -> str:
    """DuckDB identifiers can't start with a digit. Source ids like "3did"
    get prefixed with `s_` for the view name only; source_id itself stays
    as-is everywhere else (state, manifest, provenance)."""
    name = f"{source_id}_{table_name}"
    return f"s_{name}" if name[:1].isdigit() else name


def register_parquet_view(con, *, source_id: str, table_name: str, parquet_path: str | Path) -> None:
    """Drop + recreate a view named `<source_id>_<table_name>` pointing at
    a single parquet fragment. Idempotent."""
    view_name = _safe_view_name(source_id, table_name)
    pq = _normalize_path_for_duckdb(parquet_path)
    con.execute(f"DROP VIEW IF EXISTS {view_name}")
    con.execute(f"DROP TABLE IF EXISTS {view_name}")
    con.execute(f"CREATE VIEW {view_name} AS SELECT * FROM read_parquet('{pq}')")


def refresh_source(parse_result: ParseResult) -> dict:
    """Register / refresh every output file from a parse result. Records
    one row in ``ingest_runs`` and returns a small summary."""
    if not parse_result.ok:
        raise ValueError(f"ParseResult has errors: {parse_result.errors}")
    con = _connect()
    try:
        # Drop any prior views for this source — fresh snapshot wins.
        # Restrict to our schema; DuckDB's information_schema view enumerator
        # would otherwise pick up system catalogs too. Match both the plain
        # and `s_`-prefixed view-name conventions (the prefix is added for
        # source ids that begin with a digit).
        existing = con.execute(
            "SELECT view_name FROM duckdb_views() "
            "WHERE internal = false AND (view_name LIKE ? OR view_name LIKE ?)",
            (f"{parse_result.source_id}_%", f"s_{parse_result.source_id}_%"),
        ).fetchall()
        for (v,) in existing:
            con.execute(f"DROP VIEW IF EXISTS {v}")

        for table_name, path in parse_result.output_files.items():
            if not Path(path).exists() or Path(path).stat().st_size == 0:
                continue
            register_parquet_view(
                con, source_id=parse_result.source_id,
                table_name=table_name, parquet_path=path,
            )

        run_id = f"ingest_{parse_result.source_id}_{parse_result.snapshot_id}"
        con.execute(
            "INSERT OR REPLACE INTO ingest_runs "
            "(run_id, source_id, snapshot_id, registered_at, row_counts, output_files, sha256, license) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                parse_result.source_id,
                parse_result.snapshot_id,
                time.time(),
                json.dumps(parse_result.row_counts),
                json.dumps(parse_result.output_files),
                (parse_result.provenance or {}).get("sha256"),
                (parse_result.provenance or {}).get("license"),
            ),
        )
        return {
            "registered_views": [
                _safe_view_name(parse_result.source_id, t)
                for t in parse_result.output_files
                if Path(parse_result.output_files[t]).exists()
                and Path(parse_result.output_files[t]).stat().st_size > 0
            ],
            "row_counts": parse_result.row_counts,
            "snapshot_id": parse_result.snapshot_id,
            "catalog_path": str(_CATALOG_PATH),
        }
    finally:
        con.close()


def summary() -> dict:
    """Total rows per source per table, for the audit / status CLI."""
    con = _connect()
    try:
        runs = con.execute(
            "SELECT source_id, snapshot_id, row_counts, registered_at "
            "FROM ingest_runs ORDER BY registered_at DESC"
        ).fetchall()
        out_runs = [
            {
                "source_id": r[0],
                "snapshot_id": r[1],
                "row_counts": json.loads(r[2]),
                "registered_at": r[3],
            }
            for r in runs
        ]
        # Get live row counts via the views (useful when parquet was rebuilt outside this path)
        views = con.execute(
            "SELECT view_name FROM duckdb_views() WHERE internal = false"
        ).fetchall()
        live = {}
        for (v,) in views:
            try:
                n = con.execute(f"SELECT count(*) FROM {v}").fetchone()[0]
                live[v] = int(n)
            except Exception as exc:
                live[v] = f"error: {exc}"
        return {
            "catalog_path": str(_CATALOG_PATH),
            "ingest_runs": out_runs,
            "live_row_counts": live,
        }
    finally:
        con.close()


def query(sql: str, *, params: tuple = ()) -> list[dict]:
    """Execute a read-only query and return rows as dicts.

    Useful for ad-hoc smoke checks (e.g. distinct UniProt accessions in
    gtopdb_interactions). Doesn't enforce read-only at the connection
    level — callers should only pass SELECT statements.
    """
    con = _connect()
    try:
        cur = con.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        con.close()
