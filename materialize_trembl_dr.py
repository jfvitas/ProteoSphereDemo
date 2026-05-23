#!/usr/bin/env python3
"""Tier 2 — TrEMBL DR cross-refs extension shard.

Stream-parses uniprot_trembl.xml.gz (~80 GB compressed) and extracts the
minimal cross-references for every unreviewed TrEMBL entry:
  - UniProt accession
  - Taxon ID
  - Pfam memberships  (DR Pfam)
  - InterPro memberships (DR InterPro)
  - OrthoDB cluster IDs (DR OrthoDB)
  - EC numbers (in <ecNumber>)
  - PDB references (DR PDB)

We do NOT extract sequences, full feature tables, comments, or evidence — the
goal is "every protein, every relationship" not "every byte of UniProt".

Output: demo_warehouse/catalog/v2_extensions/trembl.duckdb (~25 GB extension shard,
NOT committed to git; documented in extensions_manifest.json with SHA256).

Schema in the shard:
  trembl_protein_entry(uniprot, organism, taxon_id, sequence_length)
  trembl_motif_membership(uniprot, namespace, identifier)
  trembl_ortholog(uniprot, orthodb_cluster_id)
  trembl_ec(uniprot, ec_class)
  trembl_pdb(uniprot, pdb_id, chain, start, end)

Memory strategy: stream-parse via iterparse and flush rows in batches to keep
RAM under 4 GB. The XML uncompressed is ~700 GB — never load it all.
"""
from __future__ import annotations
import gzip
import os
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import duckdb

REPO = Path(__file__).resolve().parent
DB_EXT = REPO / "demo_warehouse" / "catalog" / "v2_extensions" / "trembl.duckdb"
SRC = REPO / "data" / "cache" / "uniprot_trembl.xml.gz"
CHECKPOINT = REPO / "data" / "cache" / "trembl_extract.checkpoint.json"

NS = "{http://uniprot.org/uniprot}"


def main():
    print("=== TrEMBL DR cross-refs extraction (Tier 2 shard) ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        sys.exit(1)
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB)")

    DB_EXT.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB_EXT))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # Schema
    con.execute("DROP TABLE IF EXISTS trembl_protein_entry")
    con.execute("""
      CREATE TABLE trembl_protein_entry (
        uniprot VARCHAR PRIMARY KEY,
        organism VARCHAR,
        taxon_id INT,
        sequence_length INT
      )
    """)
    con.execute("DROP TABLE IF EXISTS trembl_motif_membership")
    con.execute("""
      CREATE TABLE trembl_motif_membership (
        uniprot VARCHAR,
        namespace VARCHAR,
        identifier VARCHAR
      )
    """)
    con.execute("DROP TABLE IF EXISTS trembl_ortholog")
    con.execute("""
      CREATE TABLE trembl_ortholog (
        uniprot VARCHAR,
        orthodb_cluster_id VARCHAR
      )
    """)
    con.execute("DROP TABLE IF EXISTS trembl_ec")
    con.execute("""
      CREATE TABLE trembl_ec (
        uniprot VARCHAR,
        ec_class VARCHAR
      )
    """)
    con.execute("DROP TABLE IF EXISTS trembl_pdb")
    con.execute("""
      CREATE TABLE trembl_pdb (
        uniprot VARCHAR,
        pdb_id VARCHAR,
        chain VARCHAR
      )
    """)

    # Stream parse
    entries = []      # (uniprot, organism, taxon_id, seq_len)
    motifs = []
    orthos = []
    ecs = []
    pdbs = []
    n_entries = 0
    t0 = time.time()
    BATCH = 50_000

    def flush():
        nonlocal entries, motifs, orthos, ecs, pdbs
        if entries:
            con.executemany("INSERT OR IGNORE INTO trembl_protein_entry VALUES (?,?,?,?)", entries)
        if motifs:
            con.executemany("INSERT INTO trembl_motif_membership VALUES (?,?,?)", motifs)
        if orthos:
            con.executemany("INSERT INTO trembl_ortholog VALUES (?,?)", orthos)
        if ecs:
            con.executemany("INSERT INTO trembl_ec VALUES (?,?)", ecs)
        if pdbs:
            con.executemany("INSERT INTO trembl_pdb VALUES (?,?,?)", pdbs)
        entries.clear(); motifs.clear(); orthos.clear(); ecs.clear(); pdbs.clear()

    print(f"\n  streaming…")
    try:
        with gzip.open(SRC, "rb") as fh:
            for event, elem in ET.iterparse(fh, events=("end",)):
                tag = elem.tag.replace(NS, "")
                if tag != "entry":
                    continue
                n_entries += 1
                # Accession
                acc_el = elem.find(f"{NS}accession")
                acc = acc_el.text if acc_el is not None else None
                if not acc:
                    elem.clear()
                    continue
                # Organism + taxon
                org = None; taxon = None
                org_el = elem.find(f"{NS}organism")
                if org_el is not None:
                    name_el = org_el.find(f"{NS}name[@type='scientific']")
                    if name_el is not None:
                        org = name_el.text
                    db_ref = org_el.find(f"{NS}dbReference[@type='NCBI Taxonomy']")
                    if db_ref is not None:
                        try:
                            taxon = int(db_ref.get("id"))
                        except (TypeError, ValueError):
                            taxon = None
                # Sequence length
                seq_len = None
                seq_el = elem.find(f"{NS}sequence")
                if seq_el is not None:
                    try:
                        seq_len = int(seq_el.get("length"))
                    except (TypeError, ValueError):
                        seq_len = None
                entries.append((acc, org, taxon, seq_len))

                # DR cross-refs
                for db in elem.findall(f"{NS}dbReference"):
                    db_type = db.get("type")
                    db_id = db.get("id")
                    if not db_type or not db_id:
                        continue
                    if db_type == "Pfam":
                        motifs.append((acc, "pfam", db_id))
                    elif db_type == "InterPro":
                        motifs.append((acc, "interpro", db_id))
                    elif db_type == "OrthoDB":
                        orthos.append((acc, db_id))
                    elif db_type == "PDB":
                        chains_el = db.find(f"{NS}property[@type='chains']")
                        chain = chains_el.get("value") if chains_el is not None else None
                        pdbs.append((acc, db_id, chain))

                # EC numbers from <protein><recommendedName><ecNumber>
                for ec_el in elem.findall(f".//{NS}ecNumber"):
                    if ec_el.text:
                        ecs.append((acc, ec_el.text))

                elem.clear()
                if n_entries % BATCH == 0:
                    flush()
                if n_entries % 1_000_000 == 0:
                    elapsed = time.time() - t0
                    rate = n_entries / elapsed
                    n_motifs = con.execute("SELECT COUNT(*) FROM trembl_motif_membership").fetchone()[0]
                    print(f"    {n_entries:,} entries  motifs={n_motifs:,}  "
                          f"({elapsed:.1f}s @ {rate:.0f} entries/s)")
    except EOFError as e:
        print(f"\n  WARN: gzip EOF before end-of-stream marker ({e}). "
              f"Continuing with {n_entries:,} entries parsed.")
    except Exception as e:
        print(f"\n  ERROR mid-parse ({type(e).__name__}: {e})")
        print(f"  Flushing {len(entries) + len(motifs):,} pending rows and continuing")
    finally:
        flush()

    # Stats
    n_e = con.execute("SELECT COUNT(*) FROM trembl_protein_entry").fetchone()[0]
    n_m = con.execute("SELECT COUNT(*) FROM trembl_motif_membership").fetchone()[0]
    n_o = con.execute("SELECT COUNT(*) FROM trembl_ortholog").fetchone()[0]
    n_x = con.execute("SELECT COUNT(*) FROM trembl_ec").fetchone()[0]
    n_p = con.execute("SELECT COUNT(*) FROM trembl_pdb").fetchone()[0]
    print(f"\n  parsed {n_entries:,} entries in {time.time()-t0:.1f}s")
    print(f"    trembl_protein_entry:    {n_e:,}")
    print(f"    trembl_motif_membership: {n_m:,}")
    print(f"    trembl_ortholog:         {n_o:,}")
    print(f"    trembl_ec:               {n_x:,}")
    print(f"    trembl_pdb:              {n_p:,}")
    con.execute("CHECKPOINT")
    con.close()

    sz = DB_EXT.stat().st_size / (1024**3)
    print(f"\n  shard size: {sz:.2f} GB at {DB_EXT}")
    print("\n=== done ===")


if __name__ == "__main__":
    main()
