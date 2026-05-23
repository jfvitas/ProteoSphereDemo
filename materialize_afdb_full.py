#!/usr/bin/env python3
"""Tier 2 — AlphaFold DB model metadata for every Swiss-Prot UniProt.

Source: AlphaFold DB v4 publishes deterministic per-entry URLs and a global
"accession_ids.csv" file listing every model. For our purposes (Swiss-Prot
already covered), we don't need to enumerate every one of the 200M+ models;
we can derive every URL from the UniProt accessions we already have in
v2_protein_entry (574,627 reviewed proteins).

For each Swiss-Prot UniProt in our universe, write a row with:
  - uniprot
  - pdb_url:  https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-model_v4.pdb
  - cif_url:  https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-model_v4.cif
  - pae_url:  https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-predicted_aligned_error_v4.json
  - confidence_url: https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-confidence_v4.json

We do NOT fetch pLDDT summaries per-entry (would require 574K REST calls).
Instead, we rely on the URLs being valid for any entry that's been folded.
AlphaFold has folded all of Swiss-Prot, so this approach is exhaustive.

This extends/replaces the existing v2_alphafold_models table (574,627 rows
already; we ensure URL columns are present).
"""
from __future__ import annotations
import os
from pathlib import Path
import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"


def main():
    print("=== AlphaFold DB metadata expansion ===")
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_alphafold_models)").fetchall()]
    print(f"  v2_alphafold_models existing cols: {cols}")
    n_before = con.execute("SELECT COUNT(*) FROM v2_alphafold_models").fetchone()[0]
    print(f"  v2_alphafold_models existing rows: {n_before:,}")

    # Ensure URL columns are present
    needed_cols = {
        "pdb_url": "VARCHAR",
        "cif_url": "VARCHAR",
        "pae_url": "VARCHAR",
        "confidence_url": "VARCHAR",
    }
    for col, ctype in needed_cols.items():
        if col not in cols:
            try:
                con.execute(f"ALTER TABLE v2_alphafold_models ADD COLUMN {col} {ctype}")
                print(f"  added column {col}")
            except Exception as e:
                print(f"  could not add {col}: {e}")

    # Populate URLs deterministically from uniprot column.
    con.execute("""
      UPDATE v2_alphafold_models
      SET pdb_url = 'https://alphafold.ebi.ac.uk/files/AF-' || uniprot || '-F1-model_v4.pdb',
          cif_url = 'https://alphafold.ebi.ac.uk/files/AF-' || uniprot || '-F1-model_v4.cif',
          pae_url = 'https://alphafold.ebi.ac.uk/files/AF-' || uniprot || '-F1-predicted_aligned_error_v4.json',
          confidence_url = 'https://alphafold.ebi.ac.uk/files/AF-' || uniprot || '-F1-confidence_v4.json'
    """)

    # Make sure every Swiss-Prot entry has an AFDB row (insert any that are missing)
    n_added = con.execute("""
      INSERT INTO v2_alphafold_models (uniprot, pdb_url, cif_url, pae_url, confidence_url)
      SELECT p.uniprot,
             'https://alphafold.ebi.ac.uk/files/AF-' || p.uniprot || '-F1-model_v4.pdb',
             'https://alphafold.ebi.ac.uk/files/AF-' || p.uniprot || '-F1-model_v4.cif',
             'https://alphafold.ebi.ac.uk/files/AF-' || p.uniprot || '-F1-predicted_aligned_error_v4.json',
             'https://alphafold.ebi.ac.uk/files/AF-' || p.uniprot || '-F1-confidence_v4.json'
      FROM v2_protein_entry p
      WHERE NOT EXISTS (SELECT 1 FROM v2_alphafold_models a WHERE a.uniprot = p.uniprot)
    """).fetchall()
    print(f"  inserted: {n_added}")

    n_after = con.execute("SELECT COUNT(*) FROM v2_alphafold_models").fetchone()[0]
    print(f"  v2_alphafold_models final rows: {n_after:,}")

    # Sanity check
    rows = con.execute("""
      SELECT uniprot, pdb_url
      FROM v2_alphafold_models WHERE uniprot IN ('P00519', 'P32680', 'P60010', 'P60709')
      ORDER BY uniprot
    """).fetchall()
    print("\n  sample URLs:")
    for r in rows:
        print(f"    {r[0]}: {r[1]}")

    con.execute("CHECKPOINT")
    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
