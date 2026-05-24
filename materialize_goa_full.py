#!/usr/bin/env python3
"""Tier 2 — Complete cross-species GO annotation extraction (memory-bounded).

Previous version crashed with MemoryError after accumulating ~600K rows in a
Python list. This rewrite:
  - Parses in chunks
  - Flushes each chunk to a parquet file under data/cache/goa_chunks/
  - At the end, does a single COPY into the warehouse from those parquet
    files (DuckDB native bulk path)

GAF 2.2 columns:
  0  DB
  1  DB Object ID (UniProt accession)
  2  DB Object Symbol (gene name)
  3  Qualifier
  4  GO ID
  5  DB Reference
  6  Evidence Code
  7  With (or) From
  8  Aspect (F/P/C)
  9  DB Object Name
  10 DB Object Synonym
  11 DB Object Type
  12 Taxon
"""
from __future__ import annotations
import gzip
import os
import shutil
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
SRC = REPO / "data" / "cache" / "goa_uniprot_all.gaf.gz"
CHUNK_DIR = REPO / "data" / "cache" / "goa_chunks"
CHUNK_SIZE = 500_000  # rows per parquet chunk file


def write_chunk(rows, idx):
    if not rows:
        return None
    pq_path = CHUNK_DIR / f"chunk_{idx:05d}.parquet"
    table = pa.table({
        "uniprot":       pa.array([r[0] for r in rows], type=pa.string()),
        "go_id":         pa.array([r[1] for r in rows], type=pa.string()),
        "aspect":        pa.array([r[2] for r in rows], type=pa.string()),
        "evidence_code": pa.array([r[3] for r in rows], type=pa.string()),
        "taxon":         pa.array([r[4] for r in rows], type=pa.int64()),
    })
    pq.write_table(table, pq_path, compression="zstd")
    return pq_path


def main():
    print("=== GOA cross-species GO extraction (chunked) ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        return
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB)")

    # Clean chunk dir
    if CHUNK_DIR.exists():
        shutil.rmtree(CHUNK_DIR)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)

    # Universe filter
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass
    universe = {r[0] for r in con.execute("""
      SELECT DISTINCT uniprot FROM v2_protein_entry
      UNION
      SELECT DISTINCT uniprot FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM kiba_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM gtopdb_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM hippie_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM huri_bridge_uniprot WHERE uniprot IS NOT NULL
    """).fetchall()}
    print(f"  universe: {len(universe):,}")
    # Release the connection while we parse — avoid holding the lock
    con.close()

    # Stream parse, flushing chunks
    t0 = time.time()
    n_lines = 0
    n_kept = 0
    rows = []
    chunk_idx = 0
    chunk_files = []

    print("\n  parsing…")
    try:
        with gzip.open(SRC, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                n_lines += 1
                if not line or line.startswith("!"):
                    continue
                if n_lines % 5_000_000 == 0:
                    print(f"    {n_lines:,} lines  kept={n_kept:,}  chunks={chunk_idx}  ({time.time()-t0:.1f}s)", flush=True)
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 9:
                    continue
                uniprot = parts[1]
                if uniprot not in universe:
                    continue
                go_id = parts[4]
                evidence = parts[6]
                aspect = parts[8]
                taxon_field = parts[12] if len(parts) > 12 else ""
                taxon = None
                if taxon_field.startswith("taxon:"):
                    try:
                        taxon = int(taxon_field.split(":", 1)[1].split("|")[0])
                    except Exception:
                        taxon = None
                rows.append((uniprot, go_id, aspect, evidence, taxon))
                n_kept += 1
                if len(rows) >= CHUNK_SIZE:
                    pq_path = write_chunk(rows, chunk_idx)
                    if pq_path:
                        chunk_files.append(pq_path)
                    rows.clear()
                    chunk_idx += 1
    except EOFError:
        print(f"  WARN: gzip EOF early; continuing with {n_kept:,} rows kept.")

    # Final chunk
    if rows:
        pq_path = write_chunk(rows, chunk_idx)
        if pq_path:
            chunk_files.append(pq_path)
        rows.clear()
        chunk_idx += 1

    elapsed = time.time() - t0
    print(f"\n  parsed {n_lines:,} lines in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  rows kept: {n_kept:,} across {len(chunk_files)} chunks")

    # Bulk load via DuckDB COPY
    print("\n  reopening DB + loading chunks via COPY…")
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_go_membership)").fetchall()]
    print(f"  v2_go_membership cols: {cols}")
    has_evidence = 'evidence_code' in cols
    has_taxon = 'taxon' in cols

    if has_evidence and has_taxon:
        insert_cols = "uniprot, go_id, aspect, evidence_code, taxon"
        select_cols = "uniprot, go_id, aspect, evidence_code, taxon"
    elif has_evidence:
        insert_cols = "uniprot, go_id, aspect, evidence_code"
        select_cols = "uniprot, go_id, aspect, evidence_code"
    else:
        insert_cols = "uniprot, go_id, aspect"
        select_cols = "uniprot, go_id, aspect"

    # All chunks have the same parquet schema (uniprot,go_id,aspect,evidence_code,taxon)
    pq_glob = (CHUNK_DIR / "*.parquet").as_posix()
    print(f"  loading: {pq_glob}")
    con.execute(f"""
      INSERT INTO v2_go_membership ({insert_cols})
      SELECT {select_cols} FROM read_parquet('{pq_glob}')
    """)

    # Dedupe
    print("\n  deduping…")
    con.execute("""
      CREATE OR REPLACE TABLE v2_go_membership AS
      SELECT DISTINCT * FROM v2_go_membership
    """)
    n_final = con.execute("SELECT COUNT(*) FROM v2_go_membership").fetchone()[0]
    print(f"  final v2_go_membership: {n_final:,} rows")
    con.execute("CHECKPOINT")
    con.close()

    # Clean up chunks (optional - keep for re-runs)
    print(f"\n  parquet chunks kept at {CHUNK_DIR} ({sum(p.stat().st_size for p in chunk_files)/(1024**2):.1f} MB)")
    print("=== done ===")


if __name__ == "__main__":
    main()
