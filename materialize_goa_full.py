#!/usr/bin/env python3
"""Tier 2 — Complete cross-species GO annotation extraction.

Stream-parses goa_uniprot_all.gaf.gz (GO Annotation File format 2.2) from EBI
and extracts every (uniprot, go_id, aspect, evidence_code, taxon) tuple. This
extends the existing v2_go_membership beyond just Swiss-Prot DR-Mode entries
to cover all curated cross-species annotations.

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
  13 Date
  14 Assigned By
  15 Annotation Extension
  16 Gene Product Form ID

Output: extends v2_go_membership in the CORE warehouse (the row count grows
from ~3.4M to ~10-15M, modest extension; this is core relationship data, not
shard-worthy)
"""
from __future__ import annotations
import gzip
import os
import time
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
SRC = REPO / "data" / "cache" / "goa_uniprot_all.gaf.gz"


def main():
    print("=== GOA cross-species GO extraction ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        return
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB compressed)")

    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # Check existing v2_go_membership schema
    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_go_membership)").fetchall()]
    print(f"  v2_go_membership cols: {cols}")

    # Determine which insert format to use based on existing schema
    needed = ['uniprot', 'go_id', 'aspect']
    has_evidence = 'evidence_code' in cols
    has_taxon = 'taxon' in cols or 'organism_taxid' in cols
    taxon_col = 'taxon' if 'taxon' in cols else ('organism_taxid' if 'organism_taxid' in cols else None)

    # Universe filter: limit to UniProts we care about (Swiss-Prot + bridges)
    universe = {r[0] for r in con.execute("""
      SELECT DISTINCT uniprot FROM v2_protein_entry
      UNION
      SELECT DISTINCT uniprot FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM kiba_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM gtopdb_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM hippie_bridge_uniprot WHERE uniprot IS NOT NULL
      UNION SELECT DISTINCT uniprot FROM huri_bridge_uniprot WHERE uniprot IS NOT NULL
    """).fetchall()}
    print(f"  universe size: {len(universe):,}")

    # Stream parse
    t0 = time.time()
    n_lines = 0
    n_kept = 0
    rows = []
    BATCH = 200_000

    def flush():
        nonlocal rows
        if not rows:
            return
        try:
            if has_evidence and has_taxon:
                # 5-column insert
                fields = "uniprot, go_id, aspect, evidence_code, " + taxon_col
                placeholders = "?, ?, ?, ?, ?"
            elif has_evidence:
                fields = "uniprot, go_id, aspect, evidence_code"
                placeholders = "?, ?, ?, ?"
                rows = [(r[0], r[1], r[2], r[3]) for r in rows]
            else:
                fields = "uniprot, go_id, aspect"
                placeholders = "?, ?, ?"
                rows = [(r[0], r[1], r[2]) for r in rows]
            con.executemany(f"INSERT INTO v2_go_membership ({fields}) VALUES ({placeholders})", rows)
        except Exception as e:
            print(f"    insert failed: {e}; trying minimal 3-col")
            con.executemany("INSERT INTO v2_go_membership (uniprot, go_id, aspect) VALUES (?, ?, ?)",
                            [(r[0], r[1], r[2]) for r in rows])
        rows.clear()

    print(f"\n  parsing…")
    try:
        with gzip.open(SRC, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                n_lines += 1
                if not line or line.startswith("!"):
                    continue
                if n_lines % 10_000_000 == 0:
                    print(f"    {n_lines:,} lines  kept={n_kept:,}  ({time.time()-t0:.1f}s)")
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
                # taxon looks like "taxon:9606"; strip prefix
                taxon = None
                if taxon_field.startswith("taxon:"):
                    try:
                        taxon = int(taxon_field.split(":", 1)[1].split("|")[0])
                    except Exception:
                        taxon = None
                rows.append((uniprot, go_id, aspect, evidence, taxon))
                n_kept += 1
                if len(rows) >= BATCH:
                    flush()
    except EOFError:
        print(f"  WARN: gzip EOF early (common); continuing with {n_kept:,} rows kept.")
    finally:
        flush()

    # Dedupe
    print("\n  deduping v2_go_membership…")
    con.execute("""
      CREATE OR REPLACE TABLE v2_go_membership AS
      SELECT DISTINCT * FROM v2_go_membership
    """)
    n_final = con.execute("SELECT COUNT(*) FROM v2_go_membership").fetchone()[0]
    print(f"  final v2_go_membership: {n_final:,} rows")
    con.execute("CHECKPOINT")
    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
