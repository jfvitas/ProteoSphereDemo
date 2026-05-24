#!/usr/bin/env python3
"""Tier 2 — Full UniRef50/90/100 across ALL UniProt via idmapping.dat.gz.

Closes the gap that materialize_uniref_swissprot.py left: cluster
memberships were only extended to Swiss-Prot's 574K entries, not the
full 200M+ TrEMBL universe.

idmapping.dat.gz has db_type values 'UniRef100', 'UniRef90', 'UniRef50'
for every UniProt entry — we just filtered them out in the first
pass. Re-parse with those included, write to an extension shard.

Output: demo_warehouse/catalog/v2_extensions/uniref_full.duckdb
Tables:
  uniref100_member(uniprot, uniref100_id)
  uniref90_member(uniprot,  uniref90_id)
  uniref50_member(uniprot,  uniref50_id)
"""
from __future__ import annotations
import gzip
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent
DB_EXT = REPO / "demo_warehouse" / "catalog" / "v2_extensions" / "uniref_full.duckdb"
SRC = REPO / "data" / "cache" / "idmapping.dat.gz"
CHUNK_DIR = REPO / "data" / "cache" / "uniref_full_chunks"

CHUNK_SIZE = 2_000_000


def write_chunks(u100, u90, u50, idx):
    if u100:
        pq.write_table(pa.table({
            "uniprot":     pa.array([r[0] for r in u100], type=pa.string()),
            "uniref100_id":pa.array([r[1] for r in u100], type=pa.string()),
        }), CHUNK_DIR / f"u100_{idx:05d}.parquet", compression="zstd")
    if u90:
        pq.write_table(pa.table({
            "uniprot":    pa.array([r[0] for r in u90], type=pa.string()),
            "uniref90_id":pa.array([r[1] for r in u90], type=pa.string()),
        }), CHUNK_DIR / f"u90_{idx:05d}.parquet", compression="zstd")
    if u50:
        pq.write_table(pa.table({
            "uniprot":    pa.array([r[0] for r in u50], type=pa.string()),
            "uniref50_id":pa.array([r[1] for r in u50], type=pa.string()),
        }), CHUNK_DIR / f"u50_{idx:05d}.parquet", compression="zstd")


def main():
    print("=== UniRef full from idmapping.dat.gz ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        sys.exit(1)
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB compressed)")

    if CHUNK_DIR.exists():
        shutil.rmtree(CHUNK_DIR)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)

    u100, u90, u50 = [], [], []
    chunk_idx = 0
    n_lines = 0
    t0 = time.time()

    def buf_total():
        return len(u100) + len(u90) + len(u50)

    print("\n  streaming…")
    try:
        with gzip.open(SRC, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                n_lines += 1
                if n_lines % 50_000_000 == 0:
                    print(f"    {n_lines:,} lines  chunks={chunk_idx}  "
                          f"buf={buf_total():,}  ({time.time()-t0:.1f}s)", flush=True)
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                acc, db, db_id = parts
                if db == "UniRef100":
                    u100.append((acc, db_id))
                elif db == "UniRef90":
                    u90.append((acc, db_id))
                elif db == "UniRef50":
                    u50.append((acc, db_id))
                if buf_total() >= CHUNK_SIZE:
                    write_chunks(u100, u90, u50, chunk_idx)
                    u100.clear(); u90.clear(); u50.clear()
                    chunk_idx += 1
    except EOFError as e:
        print(f"\n  WARN: gzip EOF early ({e}); flushing what we have.")

    if buf_total() > 0:
        write_chunks(u100, u90, u50, chunk_idx)
        chunk_idx += 1

    elapsed = time.time() - t0
    print(f"\n  parsed {n_lines:,} lines in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  wrote {chunk_idx} chunk batches")

    # Bulk load into shard
    print(f"\n  loading into shard: {DB_EXT.name}…")
    DB_EXT.parent.mkdir(parents=True, exist_ok=True)
    if DB_EXT.exists():
        DB_EXT.unlink()
    con = duckdb.connect(str(DB_EXT))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    for prefix, table_name, col_name in [
        ("u100", "uniref100_member", "uniref100_id"),
        ("u90",  "uniref90_member",  "uniref90_id"),
        ("u50",  "uniref50_member",  "uniref50_id"),
    ]:
        chunks = sorted(CHUNK_DIR.glob(f"{prefix}_*.parquet"))
        if chunks:
            con.execute(f"""
              CREATE TABLE {table_name} AS
              SELECT * FROM read_parquet('{(CHUNK_DIR / f"{prefix}_*.parquet").as_posix()}')
            """)
        else:
            con.execute(f"CREATE TABLE {table_name} (uniprot VARCHAR, {col_name} VARCHAR)")
        n = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"    {table_name}: {n:,}")

    con.execute("CHECKPOINT")
    con.close()
    print(f"\n  shard size: {DB_EXT.stat().st_size/(1024**3):.2f} GB at {DB_EXT}")
    print("\n=== done ===")


if __name__ == "__main__":
    main()
