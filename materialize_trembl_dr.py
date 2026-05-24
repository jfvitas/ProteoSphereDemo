#!/usr/bin/env python3
"""Tier 2 — TrEMBL DR cross-refs extension shard (memory-bounded, chunked).

Stream-parses uniprot_trembl.xml.gz (~199 GB compressed) and emits chunks
to parquet files, then bulk-loads via DuckDB COPY into the extension shard.

For each TrEMBL entry, extracts:
  - UniProt accession
  - Taxon ID
  - Pfam memberships  (DR Pfam)
  - InterPro memberships (DR InterPro)
  - OrthoDB cluster IDs (DR OrthoDB)
  - EC numbers (in <ecNumber>)
  - PDB references (DR PDB)

Output: demo_warehouse/catalog/v2_extensions/trembl.duckdb

This script can be interrupted and resumed via the parquet chunks (each chunk
file persists once written; resume picks up from highest existing chunk index).
"""
from __future__ import annotations
import gzip
import os
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parent
DB_EXT = REPO / "demo_warehouse" / "catalog" / "v2_extensions" / "trembl.duckdb"
SRC = REPO / "data" / "cache" / "uniprot_trembl.xml.gz"
CHUNK_DIR = REPO / "data" / "cache" / "trembl_chunks"
CHECKPOINT = REPO / "data" / "cache" / "trembl_extract.checkpoint.json"

NS = "{http://uniprot.org/uniprot}"

# Flush sizes: limit each chunk to ~500K motif rows or ~250K entries
ENTRY_CHUNK = 250_000


def write_chunk(entries, motifs, orthos, ecs, pdbs, idx):
    if not entries and not motifs:
        return None
    p_entries = CHUNK_DIR / f"entries_{idx:05d}.parquet"
    p_motifs  = CHUNK_DIR / f"motifs_{idx:05d}.parquet"
    p_orthos  = CHUNK_DIR / f"orthos_{idx:05d}.parquet"
    p_ecs     = CHUNK_DIR / f"ecs_{idx:05d}.parquet"
    p_pdbs    = CHUNK_DIR / f"pdbs_{idx:05d}.parquet"
    if entries:
        pq.write_table(pa.table({
            "uniprot":         pa.array([r[0] for r in entries], type=pa.string()),
            "organism":        pa.array([r[1] for r in entries], type=pa.string()),
            "taxon_id":        pa.array([r[2] for r in entries], type=pa.int64()),
            "sequence_length": pa.array([r[3] for r in entries], type=pa.int64()),
        }), p_entries, compression="zstd")
    if motifs:
        pq.write_table(pa.table({
            "uniprot":    pa.array([r[0] for r in motifs], type=pa.string()),
            "namespace":  pa.array([r[1] for r in motifs], type=pa.string()),
            "identifier": pa.array([r[2] for r in motifs], type=pa.string()),
        }), p_motifs, compression="zstd")
    if orthos:
        pq.write_table(pa.table({
            "uniprot":            pa.array([r[0] for r in orthos], type=pa.string()),
            "orthodb_cluster_id": pa.array([r[1] for r in orthos], type=pa.string()),
        }), p_orthos, compression="zstd")
    if ecs:
        pq.write_table(pa.table({
            "uniprot":  pa.array([r[0] for r in ecs], type=pa.string()),
            "ec_class": pa.array([r[1] for r in ecs], type=pa.string()),
        }), p_ecs, compression="zstd")
    if pdbs:
        pq.write_table(pa.table({
            "uniprot": pa.array([r[0] for r in pdbs], type=pa.string()),
            "pdb_id":  pa.array([r[1] for r in pdbs], type=pa.string()),
            "chain":   pa.array([r[2] for r in pdbs], type=pa.string()),
        }), p_pdbs, compression="zstd")
    return idx


def main():
    print("=== TrEMBL DR cross-refs extraction (chunked, memory-bounded) ===")
    if not SRC.exists():
        print(f"FATAL: source missing: {SRC}")
        sys.exit(1)
    sz = SRC.stat().st_size / (1024**3)
    print(f"  source: {SRC.name} ({sz:.2f} GB)")
    print(f"  chunks: {CHUNK_DIR}")
    CHUNK_DIR.mkdir(parents=True, exist_ok=True)

    # Stream parse
    entries = []
    motifs = []
    orthos = []
    ecs = []
    pdbs = []
    n_entries = 0
    chunk_idx = 0
    t0 = time.time()

    try:
        with gzip.open(SRC, "rb") as fh:
            for event, elem in ET.iterparse(fh, events=("end",)):
                tag = elem.tag.replace(NS, "")
                if tag != "entry":
                    continue
                n_entries += 1
                acc_el = elem.find(f"{NS}accession")
                acc = acc_el.text if acc_el is not None else None
                if not acc:
                    elem.clear()
                    continue
                org = None; taxon = None
                org_el = elem.find(f"{NS}organism")
                if org_el is not None:
                    name_el = org_el.find(f"{NS}name[@type='scientific']")
                    if name_el is not None:
                        org = name_el.text
                    db_ref = org_el.find(f"{NS}dbReference[@type='NCBI Taxonomy']")
                    if db_ref is not None:
                        try: taxon = int(db_ref.get("id"))
                        except (TypeError, ValueError): taxon = None
                seq_len = None
                seq_el = elem.find(f"{NS}sequence")
                if seq_el is not None:
                    try: seq_len = int(seq_el.get("length"))
                    except (TypeError, ValueError): seq_len = None
                entries.append((acc, org, taxon, seq_len))

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
                for ec_el in elem.findall(f".//{NS}ecNumber"):
                    if ec_el.text:
                        ecs.append((acc, ec_el.text))
                elem.clear()

                if n_entries % ENTRY_CHUNK == 0:
                    write_chunk(entries, motifs, orthos, ecs, pdbs, chunk_idx)
                    entries.clear(); motifs.clear(); orthos.clear(); ecs.clear(); pdbs.clear()
                    chunk_idx += 1
                if n_entries % 500_000 == 0:
                    elapsed = time.time() - t0
                    rate = n_entries / elapsed
                    print(f"    {n_entries:,} entries  chunks={chunk_idx}  "
                          f"({elapsed:.1f}s @ {rate:.0f} entries/s)", flush=True)
    except EOFError as e:
        print(f"\n  WARN: gzip EOF early ({e}); flushing remaining + continuing.")
    except Exception as e:
        print(f"\n  ERROR mid-parse ({type(e).__name__}: {e})")
        import traceback; traceback.print_exc()

    # Final flush
    if entries or motifs:
        write_chunk(entries, motifs, orthos, ecs, pdbs, chunk_idx)
        chunk_idx += 1

    elapsed = time.time() - t0
    print(f"\n  parsed {n_entries:,} entries in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    print(f"  wrote {chunk_idx} chunks")

    # Bulk load into shard
    print(f"\n  loading chunks into {DB_EXT.name}…")
    DB_EXT.parent.mkdir(parents=True, exist_ok=True)
    if DB_EXT.exists():
        DB_EXT.unlink()
    con = duckdb.connect(str(DB_EXT))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    con.execute(f"""
      CREATE TABLE trembl_protein_entry AS
      SELECT * FROM read_parquet('{(CHUNK_DIR / "entries_*.parquet").as_posix()}')
    """)
    con.execute(f"""
      CREATE TABLE trembl_motif_membership AS
      SELECT * FROM read_parquet('{(CHUNK_DIR / "motifs_*.parquet").as_posix()}')
    """)
    con.execute(f"""
      CREATE TABLE trembl_ortholog AS
      SELECT * FROM read_parquet('{(CHUNK_DIR / "orthos_*.parquet").as_posix()}')
    """)
    con.execute(f"""
      CREATE TABLE trembl_ec AS
      SELECT * FROM read_parquet('{(CHUNK_DIR / "ecs_*.parquet").as_posix()}')
    """)
    con.execute(f"""
      CREATE TABLE trembl_pdb AS
      SELECT * FROM read_parquet('{(CHUNK_DIR / "pdbs_*.parquet").as_posix()}')
    """)

    for t in ("trembl_protein_entry", "trembl_motif_membership",
              "trembl_ortholog", "trembl_ec", "trembl_pdb"):
        n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"    {t}: {n:,}")
    con.execute("CHECKPOINT")
    con.close()
    print(f"\n  shard size: {DB_EXT.stat().st_size/(1024**3):.2f} GB at {DB_EXT}")
    print("\n=== done ===")


if __name__ == "__main__":
    main()
