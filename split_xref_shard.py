#!/usr/bin/env python3
"""Split v2_entrez_uniprot_xref: keep Swiss-Prot mappings in core warehouse,
move TrEMBL-only mappings to entrez_xref.duckdb extension shard.

Rationale: the 24M-row xref is dominated by 23.7M TrEMBL-only entries, which
are bloat for the core warehouse but valuable for future BioGRID re-ingestion.
Per the user's directive ("extracted info + relationships, not full database
package"), the heavyweight TrEMBL portion moves to an optional extension.

Result:
  core v2_entrez_uniprot_xref:           ~315K rows (Swiss-Prot only)
  extension trembl_entrez_uniprot_xref:  ~23.7M rows (TrEMBL-only)
"""
from __future__ import annotations
import os
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
SHARD = REPO / "demo_warehouse" / "catalog" / "v2_extensions" / "entrez_xref.duckdb"


def main():
    print("=== Splitting v2_entrez_uniprot_xref ===")
    SHARD.parent.mkdir(parents=True, exist_ok=True)

    src = duckdb.connect(str(DB))
    try:
        src.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    n_total = src.execute("SELECT COUNT(*) FROM v2_entrez_uniprot_xref").fetchone()[0]
    n_sp = src.execute("SELECT COUNT(*) FROM v2_entrez_uniprot_xref WHERE is_swissprot").fetchone()[0]
    n_tr = n_total - n_sp
    print(f"  total: {n_total:,}  swissprot: {n_sp:,}  trembl-only: {n_tr:,}")

    # Build extension shard by EXPORTING the TrEMBL-only rows from the same
    # connection (DuckDB doesn't allow two simultaneous handles to one file).
    print(f"\n  building extension shard at {SHARD}…")
    if SHARD.exists():
        SHARD.unlink()
    # 1) Export TrEMBL-only rows from core to a parquet file
    tmp_pq = SHARD.parent / "_trembl_xref.parquet"
    src.execute(f"""
      COPY (SELECT entrez_id, uniprot, is_swissprot, organism_taxid
            FROM v2_entrez_uniprot_xref WHERE NOT is_swissprot)
      TO '{tmp_pq.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    pq_mb = tmp_pq.stat().st_size / (1024**2)
    print(f"  exported parquet: {pq_mb:.1f} MB")
    # Close the core connection so we can also write to the shard
    src.close()

    # 2) Open shard, load from parquet
    dst = duckdb.connect(str(SHARD))
    try:
        dst.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass
    dst.execute(f"""
      CREATE TABLE trembl_entrez_uniprot_xref AS
      SELECT * FROM read_parquet('{tmp_pq.as_posix()}')
    """)
    n_shard = dst.execute("SELECT COUNT(*) FROM trembl_entrez_uniprot_xref").fetchone()[0]
    print(f"  shard rows: {n_shard:,}")
    dst.execute("CHECKPOINT")
    dst.close()
    tmp_pq.unlink()
    shard_mb = SHARD.stat().st_size / (1024**2)
    print(f"  shard size: {shard_mb:.1f} MB")

    # 3) Reopen core and slim
    src = duckdb.connect(str(DB))
    try:
        src.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # Slim core to Swiss-Prot only
    print(f"\n  slimming core v2_entrez_uniprot_xref to Swiss-Prot only…")
    src.execute("""
      CREATE OR REPLACE TABLE v2_entrez_uniprot_xref AS
      SELECT entrez_id, uniprot, is_swissprot, organism_taxid
      FROM v2_entrez_uniprot_xref
      WHERE is_swissprot
    """)
    n_after = src.execute("SELECT COUNT(*) FROM v2_entrez_uniprot_xref").fetchone()[0]
    print(f"  core v2_entrez_uniprot_xref: {n_after:,} rows (was {n_total:,})")
    src.execute("CHECKPOINT")
    src.close()

    print("\n=== done ===")


if __name__ == "__main__":
    main()
