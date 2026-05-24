#!/usr/bin/env python3
"""Tier 2 — Pfam + InterPro on the full UniProt universe via protein2ipr.dat.gz.

Closes the Pfam/InterPro coverage gap that idmapping.dat.gz leaves on TrEMBL.

Source: https://ftp.ebi.ac.uk/pub/databases/interpro/current_release/
        protein2ipr.dat.gz  (~25 GB compressed)

Format: TSV, one line per signature match
  UniProt_AC <TAB> InterPro_AC <TAB> InterPro_Name <TAB> Signature_AC <TAB>
  Match_Start <TAB> Match_End

Signature_AC may be Pfam (PFnnnnn), SMART (SMnnnnn), PROSITE_PATTERNS,
PROSITE_PROFILES, PRINTS (PRnnnnnn), PIRSF, PANTHER, etc.

Output:
  demo_warehouse/catalog/v2_extensions/trembl_pfam_interpro.duckdb

Tables:
  trembl_interpro(uniprot, interpro_id, signature_id, signature_db,
                  start_pos, end_pos)
  trembl_pfam(uniprot, pfam_id, start_pos, end_pos)

Same chunked-parquet + COPY pattern as the prior parsers. Bounded RAM.
"""
from __future__ import annotations
import gzip
import os
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent
DB_EXT = REPO / "demo_warehouse" / "catalog" / "v2_extensions" / "trembl_pfam_interpro.duckdb"
SRC = REPO / "data" / "cache" / "protein2ipr.dat.gz"
CHUNK_DIR = REPO / "data" / "cache" / "protein2ipr_chunks"

CHUNK_SIZE = 2_000_000


def signature_db_of(sig_id: str) -> str:
    """Infer source DB from signature ID prefix."""
    if not sig_id:
        return "unknown"
    s = sig_id.upper()
    if s.startswith("PF"):
        return "Pfam"
    if s.startswith("SM"):
        return "SMART"
    if s.startswith("PS"):
        return "PROSITE"
    if s.startswith("PR"):
        return "PRINTS"
    if s.startswith("SSF"):
        return "SUPERFAMILY"
    if s.startswith("G3DSA"):
        return "Gene3D"
    if s.startswith("PIRSF"):
        return "PIRSF"
    if s.startswith("PTHR"):
        return "PANTHER"
    if s.startswith("TIGR"):
        return "TIGRFAMs"
    if s.startswith("NF"):
        return "NCBIfam"
    if s.startswith("CDD"):
        return "CDD"
    if s.startswith("MF_"):
        return "HAMAP"
    return "other"


def write_chunks(interpro_rows, pfam_rows, idx):
    if interpro_rows:
        p = CHUNK_DIR / f"interpro_{idx:05d}.parquet"
        pq.write_table(pa.table({
            "uniprot":      pa.array([r[0] for r in interpro_rows], type=pa.string()),
            "interpro_id":  pa.array([r[1] for r in interpro_rows], type=pa.string()),
            "signature_id": pa.array([r[2] for r in interpro_rows], type=pa.string()),
            "signature_db": pa.array([r[3] for r in interpro_rows], type=pa.string()),
            "start_pos":    pa.array([r[4] for r in interpro_rows], type=pa.int32()),
            "end_pos":      pa.array([r[5] for r in interpro_rows], type=pa.int32()),
        }), p, compression="zstd")
    if pfam_rows:
        p = CHUNK_DIR / f"pfam_{idx:05d}.parquet"
        pq.write_table(pa.table({
            "uniprot":   pa.array([r[0] for r in pfam_rows], type=pa.string()),
            "pfam_id":   pa.array([r[1] for r in pfam_rows], type=pa.string()),
            "start_pos": pa.array([r[2] for r in pfam_rows], type=pa.int32()),
            "end_pos":   pa.array([r[3] for r in pfam_rows], type=pa.int32()),
        }), p, compression="zstd")


def main():
    print("=== protein2ipr.dat.gz line-streaming materializer ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        sys.exit(1)
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB compressed)")

    if CHUNK_DIR.exists():
        shutil.rmtree(CHUNK_DIR)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  chunks: {CHUNK_DIR}")

    interpro_rows = []
    pfam_rows = []
    chunk_idx = 0
    n_lines = 0
    t0 = time.time()

    def total_buffered():
        return len(interpro_rows) + len(pfam_rows)

    print("\n  streaming…")
    try:
        with gzip.open(SRC, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                n_lines += 1
                if n_lines % 20_000_000 == 0:
                    print(f"    {n_lines:,} lines  chunks={chunk_idx}  "
                          f"buf={total_buffered():,}  ({time.time()-t0:.1f}s)", flush=True)
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 6:
                    continue
                acc = parts[0]
                ipr = parts[1]
                sig = parts[3]  # parts[2] is the InterPro_Name
                try:
                    s_pos = int(parts[4])
                    e_pos = int(parts[5])
                except (ValueError, TypeError):
                    s_pos = 0; e_pos = 0
                db = signature_db_of(sig)
                interpro_rows.append((acc, ipr, sig, db, s_pos, e_pos))
                if db == "Pfam":
                    pfam_rows.append((acc, sig, s_pos, e_pos))
                if total_buffered() >= CHUNK_SIZE:
                    write_chunks(interpro_rows, pfam_rows, chunk_idx)
                    interpro_rows.clear()
                    pfam_rows.clear()
                    chunk_idx += 1
    except EOFError as e:
        print(f"\n  WARN: gzip EOF early ({e}); flushing what we have.")

    if total_buffered() > 0:
        write_chunks(interpro_rows, pfam_rows, chunk_idx)
        chunk_idx += 1

    elapsed = time.time() - t0
    print(f"\n  parsed {n_lines:,} lines in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  wrote {chunk_idx} chunk batches")

    interpro_chunks = sorted(CHUNK_DIR.glob("interpro_*.parquet"))
    pfam_chunks = sorted(CHUNK_DIR.glob("pfam_*.parquet"))
    print(f"  chunk inventory: {len(interpro_chunks)} interpro, {len(pfam_chunks)} pfam")

    # Bulk-load into shard
    print(f"\n  loading into shard: {DB_EXT.name}…")
    DB_EXT.parent.mkdir(parents=True, exist_ok=True)
    if DB_EXT.exists():
        DB_EXT.unlink()
    con = duckdb.connect(str(DB_EXT))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    if interpro_chunks:
        con.execute(f"""
          CREATE TABLE trembl_interpro AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "interpro_*.parquet").as_posix()}')
        """)
    else:
        con.execute("""CREATE TABLE trembl_interpro
                       (uniprot VARCHAR, interpro_id VARCHAR, signature_id VARCHAR,
                        signature_db VARCHAR, start_pos INTEGER, end_pos INTEGER)""")

    if pfam_chunks:
        con.execute(f"""
          CREATE TABLE trembl_pfam AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "pfam_*.parquet").as_posix()}')
        """)
    else:
        con.execute("""CREATE TABLE trembl_pfam
                       (uniprot VARCHAR, pfam_id VARCHAR,
                        start_pos INTEGER, end_pos INTEGER)""")

    n_ipr = con.execute("SELECT COUNT(*) FROM trembl_interpro").fetchone()[0]
    n_pfam = con.execute("SELECT COUNT(*) FROM trembl_pfam").fetchone()[0]
    n_uniq_pfam = con.execute("SELECT COUNT(DISTINCT uniprot) FROM trembl_pfam").fetchone()[0]
    n_uniq_pfam_families = con.execute("SELECT COUNT(DISTINCT pfam_id) FROM trembl_pfam").fetchone()[0]
    print(f"\n    trembl_interpro:    {n_ipr:,}")
    print(f"    trembl_pfam:        {n_pfam:,}")
    print(f"      distinct uniprots:        {n_uniq_pfam:,}")
    print(f"      distinct pfam families:   {n_uniq_pfam_families:,}")

    print("\n    signature_db distribution in trembl_interpro:")
    for db, n in con.execute(
        "SELECT signature_db, COUNT(*) FROM trembl_interpro GROUP BY 1 ORDER BY 2 DESC LIMIT 12"
    ).fetchall():
        print(f"      {db}: {n:,}")

    con.execute("CHECKPOINT")
    con.close()
    print(f"\n  shard size: {DB_EXT.stat().st_size/(1024**3):.2f} GB at {DB_EXT}")
    print("\n=== done ===")


if __name__ == "__main__":
    main()
