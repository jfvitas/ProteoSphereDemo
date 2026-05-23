#!/usr/bin/env python3
"""Compact the DuckDB file by ATTACH-copying every table to a fresh DB,
then swapping the file.

DuckDB doesn't reclaim disk space on DELETE/UPDATE alone; the file
grows monotonically until you do a clean rebuild. This script:
  1. ATTACHes the existing v2.duckdb
  2. CREATEs a fresh v2.duckdb.compact
  3. COPYs every table over with CTAS
  4. DETACHes, replaces v2.duckdb with v2.duckdb.compact
"""
from __future__ import annotations
import os
import shutil
import sys
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
TMP = REPO / "demo_warehouse" / "catalog" / "v2.duckdb.compact"
BAK = REPO / "demo_warehouse" / "catalog" / "v2.duckdb.pre_compact.bak"


def main():
    if TMP.exists():
        TMP.unlink()

    before = DB.stat().st_size / (1024**2)
    print(f"DB before compact: {before:.1f} MB")

    # Open a fresh DB and attach the old one read-only
    con = duckdb.connect(str(TMP))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass
    con.execute(f"ATTACH '{DB.as_posix()}' AS src (READ_ONLY)")

    # Enumerate tables in the source and CTAS each one into the fresh DB
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name='src' ORDER BY table_name"
    ).fetchall()]
    print(f"copying {len(tables)} tables…")
    for t in tables:
        n = con.execute(f"SELECT COUNT(*) FROM src.{t}").fetchone()[0]
        print(f"  {t}: {n:,} rows", flush=True)
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")

    con.execute("DETACH src")
    con.execute("CHECKPOINT")
    con.close()

    after = TMP.stat().st_size / (1024**2)
    print(f"\nNew DB size: {after:.1f} MB")
    print(f"  delta: {after - before:+.1f} MB")

    # Atomic swap: rename old -> .bak, new -> v2.duckdb
    if BAK.exists():
        BAK.unlink()
    print(f"\nSwapping files…")
    shutil.move(str(DB), str(BAK))
    shutil.move(str(TMP), str(DB))
    print(f"  done. backup at {BAK.name} (delete after verifying)")


if __name__ == "__main__":
    main()
