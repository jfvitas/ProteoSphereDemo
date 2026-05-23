#!/usr/bin/env python3
"""Cleanup pass on the warehouse: dedupe + slim down per user directive.

User wants 'extracted information + relationships', not 'the whole database
packaged'. This script:

1. Deduplicates v2_pdb_uniprot (was 1.34M rows, 859K duplicates — 64% bloat)
2. Deduplicates v2_motif_membership (58K duplicates)
3. Deduplicates v2_ec_class_membership (1.4K duplicates)
4. Reports per-table size before/after

Does NOT touch:
- papers_rows / papers_metadata (paper-provenance audit table, retained for
  reproducibility per the manuscript)
- v2_residue_annotations / v2_go_membership (genuine relationship axes for
  catalytic-residue & functional-annotation splits)

Reads/writes:
  demo_warehouse/catalog/v2.duckdb
"""
from __future__ import annotations
import os
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"


def size_mb(p: Path) -> float:
    return p.stat().st_size / (1024**2)


def main():
    before = size_mb(DB)
    print(f"=== Warehouse cleanup ===")
    print(f"DB file before: {before:.1f} MB")

    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # ── Dedupe v2_pdb_uniprot (largest cleanup target) ──────────────────
    print("\n1) Dedupe v2_pdb_uniprot…")
    before_n = con.execute("select count(*) from v2_pdb_uniprot").fetchone()[0]
    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_pdb_uniprot)").fetchall()]
    print(f"  cols: {cols}")
    print(f"  before: {before_n:,} rows")

    # Lowercase pdb_id while we're at it for case-consistency (we saw the pdbbind/v2_pdb_uniprot mismatch earlier)
    con.execute("""
      CREATE OR REPLACE TABLE v2_pdb_uniprot AS
      SELECT DISTINCT upper(pdb_id) AS pdb_id, uniprot,
                      MAX(chain) AS chain,
                      MAX(start) AS start, MAX(end) AS end,
                      MAX(coverage) AS coverage
      FROM v2_pdb_uniprot
      GROUP BY upper(pdb_id), uniprot
    """) if all(c in cols for c in ['chain','start','end','coverage']) else con.execute("""
      CREATE OR REPLACE TABLE v2_pdb_uniprot AS
      SELECT DISTINCT upper(pdb_id) AS pdb_id, uniprot
      FROM v2_pdb_uniprot
    """)
    after_n = con.execute("select count(*) from v2_pdb_uniprot").fetchone()[0]
    print(f"  after:  {after_n:,} rows (removed {before_n - after_n:,})")

    # ── Dedupe v2_motif_membership ─────────────────────────────────────
    print("\n2) Dedupe v2_motif_membership…")
    before_n = con.execute("select count(*) from v2_motif_membership").fetchone()[0]
    print(f"  before: {before_n:,} rows")
    con.execute("""
      CREATE OR REPLACE TABLE v2_motif_membership AS
      SELECT uniprot, namespace, identifier,
             MAX(label) AS label,
             MAX(snapshot_id) AS snapshot_id
      FROM v2_motif_membership
      GROUP BY uniprot, namespace, identifier
    """)
    after_n = con.execute("select count(*) from v2_motif_membership").fetchone()[0]
    print(f"  after:  {after_n:,} rows (removed {before_n - after_n:,})")

    # ── Dedupe v2_ec_class_membership ──────────────────────────────────
    print("\n3) Dedupe v2_ec_class_membership…")
    before_n = con.execute("select count(*) from v2_ec_class_membership").fetchone()[0]
    print(f"  before: {before_n:,} rows")
    con.execute("""
      CREATE OR REPLACE TABLE v2_ec_class_membership AS
      SELECT uniprot, source,
             ec4, ec3, ec2,
             MAX(label) AS label,
             MAX(snapshot_id) AS snapshot_id
      FROM v2_ec_class_membership
      GROUP BY uniprot, source, ec4, ec3, ec2
    """)
    after_n = con.execute("select count(*) from v2_ec_class_membership").fetchone()[0]
    print(f"  after:  {after_n:,} rows (removed {before_n - after_n:,})")

    # ── VACUUM via CHECKPOINT (DuckDB reclaims free space on close) ────
    print("\n4) Forcing checkpoint to reclaim disk space…")
    con.execute("CHECKPOINT")
    con.close()

    after_total = size_mb(DB)
    print(f"\nDB file after: {after_total:.1f} MB  (saved {before - after_total:.1f} MB)")


if __name__ == "__main__":
    main()
