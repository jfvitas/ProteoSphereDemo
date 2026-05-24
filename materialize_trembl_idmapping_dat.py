#!/usr/bin/env python3
"""Tier 2 — TrEMBL DR cross-refs shard built from idmapping.dat.gz.

This replaces the abandoned materialize_trembl_dr.py XML-parse approach
(which crashed the machine twice on 199 GB of XML).

Source: https://ftp.uniprot.org/pub/databases/uniprot/current_release/
        knowledgebase/idmapping/idmapping.dat.gz  (~10 GB compressed)

Format: TSV with 3 columns (uniprot_acc, db_type, db_id). One row per
cross-reference. Line-by-line streaming = bounded memory. Pure stdlib.

We chunk to parquet every 2M rows and bulk-COPY into the shard at the
end, mirroring the chunked-parquet pattern that worked for GOA.

Output: demo_warehouse/catalog/v2_extensions/trembl.duckdb

Tables in the shard:
  trembl_motif_membership(uniprot, namespace, identifier)
    -- Pfam + InterPro
  trembl_ortholog(uniprot, orthodb_cluster_id)
  trembl_ec(uniprot, ec_class)
  trembl_pdb(uniprot, pdb_id)
  trembl_taxon(uniprot, taxon_id)

What we LOSE vs. the XML approach: organism scientific name,
sequence length, recommended protein name. The split-detection use
case (cross-axis leakage on Pfam/InterPro/OrthoDB/EC/PDB families)
doesn't need any of those — those are display fields.
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
DB_EXT = REPO / "demo_warehouse" / "catalog" / "v2_extensions" / "trembl.duckdb"
SRC = REPO / "data" / "cache" / "idmapping.dat.gz"
CHUNK_DIR = REPO / "data" / "cache" / "trembl_idmap_chunks"

CHUNK_SIZE = 2_000_000  # rows per parquet chunk

# Map upstream db_type values to our internal namespace conventions.
DB_OF_INTEREST = {
    "Pfam": "motif:pfam",
    "InterPro": "motif:interpro",
    "OrthoDB": "ortholog:orthodb",
    "EC": "ec",
    "PDB": "pdb",
    "NCBI_TaxID": "taxon",
}


def write_chunk(buckets: dict, idx: int) -> None:
    """Write all 5 buckets to parquet files keyed by (kind, idx)."""
    for kind, rows in buckets.items():
        if not rows:
            continue
        p = CHUNK_DIR / f"{kind}_{idx:05d}.parquet"
        if kind == "motif":
            table = pa.table({
                "uniprot":    pa.array([r[0] for r in rows], type=pa.string()),
                "namespace":  pa.array([r[1] for r in rows], type=pa.string()),
                "identifier": pa.array([r[2] for r in rows], type=pa.string()),
            })
        elif kind == "ortholog":
            table = pa.table({
                "uniprot":            pa.array([r[0] for r in rows], type=pa.string()),
                "orthodb_cluster_id": pa.array([r[1] for r in rows], type=pa.string()),
            })
        elif kind == "ec":
            table = pa.table({
                "uniprot":  pa.array([r[0] for r in rows], type=pa.string()),
                "ec_class": pa.array([r[1] for r in rows], type=pa.string()),
            })
        elif kind == "pdb":
            table = pa.table({
                "uniprot": pa.array([r[0] for r in rows], type=pa.string()),
                "pdb_id":  pa.array([r[1] for r in rows], type=pa.string()),
            })
        elif kind == "taxon":
            table = pa.table({
                "uniprot":  pa.array([r[0] for r in rows], type=pa.string()),
                "taxon_id": pa.array([int(r[1]) if r[1].isdigit() else None for r in rows], type=pa.int64()),
            })
        else:
            continue
        pq.write_table(table, p, compression="zstd")


def main():
    print("=== TrEMBL DR via idmapping.dat.gz (line-streaming) ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        print(f"Run: curl --location --output {SRC} https://ftp.uniprot.org/.../idmapping.dat.gz")
        sys.exit(1)
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB compressed)")

    if CHUNK_DIR.exists():
        shutil.rmtree(CHUNK_DIR)
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)
    print(f"  chunks: {CHUNK_DIR}")

    buckets = {
        "motif":    [],
        "ortholog": [],
        "ec":       [],
        "pdb":      [],
        "taxon":    [],
    }
    chunk_idx = 0
    n_lines = 0
    t0 = time.time()

    def total_buffered():
        return sum(len(v) for v in buckets.values())

    print(f"\n  streaming…")
    try:
        with gzip.open(SRC, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                n_lines += 1
                if n_lines % 20_000_000 == 0:
                    print(f"    {n_lines:,} lines  "
                          f"chunks={chunk_idx}  buf={total_buffered():,}  "
                          f"({time.time()-t0:.1f}s)", flush=True)
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                acc, db, db_id = parts
                if db == "Pfam":
                    buckets["motif"].append((acc, "pfam", db_id))
                elif db == "InterPro":
                    buckets["motif"].append((acc, "interpro", db_id))
                elif db == "OrthoDB":
                    buckets["ortholog"].append((acc, db_id))
                elif db == "EC":
                    buckets["ec"].append((acc, db_id))
                elif db == "PDB":
                    buckets["pdb"].append((acc, db_id))
                elif db == "NCBI_TaxID":
                    buckets["taxon"].append((acc, db_id))

                if total_buffered() >= CHUNK_SIZE:
                    write_chunk(buckets, chunk_idx)
                    for k in buckets:
                        buckets[k].clear()
                    chunk_idx += 1
    except EOFError as e:
        print(f"\n  WARN: gzip EOF early ({e}); flushing remaining + continuing.")

    if total_buffered() > 0:
        write_chunk(buckets, chunk_idx)
        for k in buckets:
            buckets[k].clear()
        chunk_idx += 1

    elapsed = time.time() - t0
    print(f"\n  parsed {n_lines:,} lines in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  wrote {chunk_idx} chunk batches")

    # Inventory chunks
    motif_chunks = sorted(CHUNK_DIR.glob("motif_*.parquet"))
    ortho_chunks = sorted(CHUNK_DIR.glob("ortholog_*.parquet"))
    ec_chunks = sorted(CHUNK_DIR.glob("ec_*.parquet"))
    pdb_chunks = sorted(CHUNK_DIR.glob("pdb_*.parquet"))
    taxon_chunks = sorted(CHUNK_DIR.glob("taxon_*.parquet"))
    print(f"  chunk inventory: {len(motif_chunks)} motif, {len(ortho_chunks)} ortholog, "
          f"{len(ec_chunks)} ec, {len(pdb_chunks)} pdb, {len(taxon_chunks)} taxon")

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

    if motif_chunks:
        con.execute(f"""
          CREATE TABLE trembl_motif_membership AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "motif_*.parquet").as_posix()}')
        """)
    else:
        con.execute("CREATE TABLE trembl_motif_membership (uniprot VARCHAR, namespace VARCHAR, identifier VARCHAR)")

    if ortho_chunks:
        con.execute(f"""
          CREATE TABLE trembl_ortholog AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "ortholog_*.parquet").as_posix()}')
        """)
    else:
        con.execute("CREATE TABLE trembl_ortholog (uniprot VARCHAR, orthodb_cluster_id VARCHAR)")

    if ec_chunks:
        con.execute(f"""
          CREATE TABLE trembl_ec AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "ec_*.parquet").as_posix()}')
        """)
    else:
        con.execute("CREATE TABLE trembl_ec (uniprot VARCHAR, ec_class VARCHAR)")

    if pdb_chunks:
        con.execute(f"""
          CREATE TABLE trembl_pdb AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "pdb_*.parquet").as_posix()}')
        """)
    else:
        con.execute("CREATE TABLE trembl_pdb (uniprot VARCHAR, pdb_id VARCHAR)")

    if taxon_chunks:
        con.execute(f"""
          CREATE TABLE trembl_taxon AS
          SELECT * FROM read_parquet('{(CHUNK_DIR / "taxon_*.parquet").as_posix()}')
        """)
    else:
        con.execute("CREATE TABLE trembl_taxon (uniprot VARCHAR, taxon_id INTEGER)")

    # Stats
    for t in ("trembl_motif_membership", "trembl_ortholog", "trembl_ec", "trembl_pdb", "trembl_taxon"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t}: {n:,}")

    con.execute("CHECKPOINT")
    con.close()
    sz = DB_EXT.stat().st_size / (1024**3)
    print(f"\n  shard size: {sz:.2f} GB at {DB_EXT}")
    print("\n=== done ===")


if __name__ == "__main__":
    main()
