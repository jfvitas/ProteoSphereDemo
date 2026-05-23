#!/usr/bin/env python3
"""Extend BioGRID Entrez→UniProt bridge to recover the 16% loss from TrEMBL-only proteins.

The existing v2_biogrid_interactions table dropped 16% of upstream BioGRID rows
because the Entrez Gene ID didn't map to a Swiss-Prot UniProt accession. Many
of those entries actually map to a TrEMBL UniProt — which we now want.

Source: UniProt's idmapping_selected.tab.gz (~2 GB compressed, ~10 GB unpacked).
Schema: UniProt-AC | UniProt-Name | GeneID(EntrezGene) | RefSeq | GI | PDB | GO | UniRef100 | UniRef90 | UniRef50 | UniParc | PIR | NCBI-taxon | MIM | UniGene | PubMed | EMBL | EMBL-CDS | Ensembl | Ensembl_TRS | Ensembl_PRO | Additional PubMed

We only need columns 0 (UniProt-AC) and 2 (GeneID).

Output:
  v2_entrez_uniprot_xref(entrez_id INT, uniprot VARCHAR, is_swissprot BOOLEAN,
                          organism_taxid INT)
"""
from __future__ import annotations
import gzip
import os
import sys
import time
import urllib.request
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = REPO / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

IDMAP_URL = "https://ftp.uniprot.org/pub/databases/uniprot/current_release/knowledgebase/idmapping/idmapping_selected.tab.gz"
IDMAP_PATH = CACHE / "idmapping_selected.tab.gz"


def _download(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 0:
        print(f"  cached: {dst.name} ({dst.stat().st_size/(1024**3):.2f} GB)")
        return
    print(f"  downloading {url}")
    print(f"  (~2 GB, this will take a few minutes…)")
    t0 = time.time()
    urllib.request.urlretrieve(url, dst)
    print(f"  wrote {dst.name} ({dst.stat().st_size/(1024**3):.2f} GB, {time.time()-t0:.1f} s)")


def main():
    print("=== BioGRID-TrEMBL bridge ===")
    _download(IDMAP_URL, IDMAP_PATH)

    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # Build set of Swiss-Prot accessions we already have (so we can flag which xref rows are TrEMBL-only)
    swissprot = set()
    if "v2_protein_entry" in {r[0] for r in con.execute("SHOW TABLES").fetchall()}:
        rows = con.execute("SELECT uniprot FROM v2_protein_entry").fetchall()
        swissprot = {r[0] for r in rows}
        print(f"  Swiss-Prot universe: {len(swissprot):,}")

    # Set of Entrez IDs we actually care about — only those referenced in BioGRID
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "v2_biogrid_interactions" in tabs:
        cols = [r[1] for r in con.execute("PRAGMA table_info(v2_biogrid_interactions)").fetchall()]
        entrez_cols = [c for c in cols if "entrez" in c.lower() or c.lower() in ("a_id", "b_id", "interactor_a_id", "interactor_b_id")]
        if entrez_cols:
            sql = f"SELECT DISTINCT {entrez_cols[0]} FROM v2_biogrid_interactions WHERE {entrez_cols[0]} IS NOT NULL"
            for col in entrez_cols[1:]:
                sql += f" UNION SELECT DISTINCT {col} FROM v2_biogrid_interactions WHERE {col} IS NOT NULL"
            try:
                rows = con.execute(sql).fetchall()
                biogrid_entrez = {int(r[0]) for r in rows if r[0] is not None and str(r[0]).isdigit()}
                print(f"  BioGRID-referenced Entrez IDs: {len(biogrid_entrez):,} (from cols {entrez_cols})")
            except Exception as exc:
                print(f"  WARN: couldn't pull entrez from BioGRID: {exc}")
                biogrid_entrez = None
        else:
            print(f"  WARN: no entrez-like columns in v2_biogrid_interactions ({cols})")
            biogrid_entrez = None
    else:
        biogrid_entrez = None

    # Stream-parse idmapping_selected.tab.gz, keep rows where Entrez is non-empty.
    # When we have biogrid_entrez, filter to only those — keeps memory & rowcount down.
    print(f"\n  parsing {IDMAP_PATH.name}…")
    t0 = time.time()
    n_lines = 0
    rows_to_insert = []
    with gzip.open(IDMAP_PATH, "rt", encoding="utf-8") as fh:
        for line in fh:
            n_lines += 1
            if n_lines % 2_000_000 == 0:
                print(f"    {n_lines:,} lines  pairs_kept={len(rows_to_insert):,}  ({time.time()-t0:.1f} s)")
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 13:
                continue
            uniprot = cols[0]
            entrez_field = cols[2]
            taxid_field = cols[12]
            if not entrez_field or not uniprot:
                continue
            # Multiple Entrez can appear, separated by ; or whitespace
            for ent in entrez_field.replace(";", " ").split():
                ent = ent.strip()
                if not ent.isdigit():
                    continue
                ent_int = int(ent)
                if biogrid_entrez is not None and ent_int not in biogrid_entrez:
                    continue
                is_sp = uniprot in swissprot
                try:
                    tx = int(taxid_field) if taxid_field.isdigit() else None
                except Exception:
                    tx = None
                rows_to_insert.append((ent_int, uniprot, is_sp, tx))
    print(f"  parsed {n_lines:,} lines, kept {len(rows_to_insert):,} Entrez→UniProt pairs")
    print(f"  parse time: {time.time()-t0:.1f} s")

    # Materialize
    con.execute("DROP TABLE IF EXISTS v2_entrez_uniprot_xref")
    con.execute("""
        CREATE TABLE v2_entrez_uniprot_xref (
          entrez_id      INT,
          uniprot        VARCHAR,
          is_swissprot   BOOLEAN,
          organism_taxid INT,
          PRIMARY KEY (entrez_id, uniprot)
        )
    """)
    BATCH = 100_000
    for i in range(0, len(rows_to_insert), BATCH):
        try:
            con.executemany("INSERT OR IGNORE INTO v2_entrez_uniprot_xref VALUES (?,?,?,?)",
                             rows_to_insert[i:i+BATCH])
        except Exception:
            # fall back per-row
            for r in rows_to_insert[i:i+BATCH]:
                try:
                    con.execute("INSERT OR IGNORE INTO v2_entrez_uniprot_xref VALUES (?,?,?,?)", r)
                except Exception:
                    pass

    n_xref = con.execute("SELECT COUNT(*) FROM v2_entrez_uniprot_xref").fetchone()[0]
    n_sp = con.execute("SELECT COUNT(*) FROM v2_entrez_uniprot_xref WHERE is_swissprot").fetchone()[0]
    n_tr = con.execute("SELECT COUNT(*) FROM v2_entrez_uniprot_xref WHERE NOT is_swissprot").fetchone()[0]
    print(f"\n  v2_entrez_uniprot_xref: {n_xref:,} rows ({n_sp:,} Swiss-Prot, {n_tr:,} TrEMBL-only)")

    # Now augment v2_biogrid_interactions: any row whose interactor_a or interactor_b
    # is null but the Entrez has a mapping in v2_entrez_uniprot_xref gets backfilled.
    if "v2_biogrid_interactions" in tabs:
        cols = [r[1] for r in con.execute("PRAGMA table_info(v2_biogrid_interactions)").fetchall()]
        print(f"\n  v2_biogrid_interactions columns: {cols}")

        # Find columns that look like (entrez_a, entrez_b, uniprot_a, uniprot_b)
        uniprot_a = next((c for c in cols if c.lower() in ("uniprot_a", "interactor_a", "a_uniprot", "uniprot_1")), None)
        uniprot_b = next((c for c in cols if c.lower() in ("uniprot_b", "interactor_b", "b_uniprot", "uniprot_2")), None)
        entrez_a  = next((c for c in cols if c.lower() in ("entrez_a", "a_id", "interactor_a_id", "a_entrez", "entrez_id_a")), None)
        entrez_b  = next((c for c in cols if c.lower() in ("entrez_b", "b_id", "interactor_b_id", "b_entrez", "entrez_id_b")), None)
        print(f"  identified cols: uniprot_a={uniprot_a}, uniprot_b={uniprot_b}, entrez_a={entrez_a}, entrez_b={entrez_b}")

        n_before = con.execute("SELECT COUNT(*) FROM v2_biogrid_interactions").fetchone()[0]
        if uniprot_a and uniprot_b and entrez_a and entrez_b:
            # Backfill null uniprots via xref (prefer Swiss-Prot mappings)
            con.execute(f"""
                UPDATE v2_biogrid_interactions AS b
                SET {uniprot_a} = x.uniprot
                FROM (
                  SELECT entrez_id, ARG_MAX(uniprot, is_swissprot::INT) AS uniprot
                  FROM v2_entrez_uniprot_xref GROUP BY entrez_id
                ) AS x
                WHERE b.{entrez_a} = x.entrez_id AND b.{uniprot_a} IS NULL
            """)
            con.execute(f"""
                UPDATE v2_biogrid_interactions AS b
                SET {uniprot_b} = x.uniprot
                FROM (
                  SELECT entrez_id, ARG_MAX(uniprot, is_swissprot::INT) AS uniprot
                  FROM v2_entrez_uniprot_xref GROUP BY entrez_id
                ) AS x
                WHERE b.{entrez_b} = x.entrez_id AND b.{uniprot_b} IS NULL
            """)
            n_a_filled = con.execute(f"SELECT COUNT(*) FROM v2_biogrid_interactions WHERE {uniprot_a} IS NOT NULL").fetchone()[0]
            n_b_filled = con.execute(f"SELECT COUNT(*) FROM v2_biogrid_interactions WHERE {uniprot_b} IS NOT NULL").fetchone()[0]
            both_filled = con.execute(f"SELECT COUNT(*) FROM v2_biogrid_interactions WHERE {uniprot_a} IS NOT NULL AND {uniprot_b} IS NOT NULL").fetchone()[0]
            print(f"  after backfill: {n_a_filled:,}/{n_before:,} have uniprot_a, "
                  f"{n_b_filled:,}/{n_before:,} have uniprot_b, "
                  f"{both_filled:,} have both")
        else:
            print("  could not identify columns to backfill — skipping")

    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
