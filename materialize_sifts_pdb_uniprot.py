"""Materialize SIFTS PDB->UniProt mapping into v2_pdb_uniprot.

Source: ftp.ebi.ac.uk/pub/databases/msd/sifts/flatfiles/csv/pdb_chain_uniprot.csv.gz
License: PDBe SIFTS, CC-BY 4.0.

Strategy
--------
Replace the current legacy v2_pdb_uniprot (~127k rows) with the canonical
SIFTS mapping (~1.0M chain-level rows) so PDB coverage on PDBbind rises
from 56% to >95% and the actin "classic" PDBs (1ATN/3HBT/1J6Z) resolve.

Schema preserved as (pdb_id, uniprot, snapshot_id) with chain & residue
range added as extra columns. Existing dependent joins keep working.

Re-runnable. Pulls from cache/sifts/pdb_chain_uniprot.csv.gz (downloaded
on first run; cached for re-runs).
"""

from __future__ import annotations

import csv
import gzip
import os
import time
import urllib.request
from pathlib import Path

import duckdb


HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = Path("D:/documents/ProteoSphereV2/cache/sifts")
SIFTS_FILE = CACHE / "pdb_chain_uniprot.csv.gz"
SIFTS_FILE_UNGZ = CACHE / "pdb_chain_uniprot.csv"
SIFTS_FILE_CLEAN = CACHE / "pdb_chain_uniprot_clean.csv"
SIFTS_URL = "https://ftp.ebi.ac.uk/pub/databases/msd/sifts/flatfiles/csv/pdb_chain_uniprot.csv.gz"
SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _fetch_if_missing() -> None:
    if SIFTS_FILE.exists() and SIFTS_FILE.stat().st_size > 1_000_000:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"[sifts] downloading {SIFTS_URL}")
    urllib.request.urlretrieve(SIFTS_URL, str(SIFTS_FILE))


def main() -> None:
    _fetch_if_missing()
    if not WAREHOUSE.exists():
        raise SystemExit(f"warehouse missing: {WAREHOUSE}")

    con = duckdb.connect(str(WAREHOUSE))

    # Pre-flight
    before = con.execute("select count(*) from v2_pdb_uniprot").fetchone()[0]
    distinct_pdb_before = con.execute("select count(distinct pdb_id) from v2_pdb_uniprot").fetchone()[0]
    print(f"[sifts] before: {before:,} rows / {distinct_pdb_before:,} distinct PDB IDs", flush=True)

    con.execute("DROP TABLE IF EXISTS v2_pdb_uniprot")
    con.execute("""
        CREATE TABLE v2_pdb_uniprot (
            pdb_id      VARCHAR,
            chain       VARCHAR,
            uniprot     VARCHAR,
            sp_beg      INTEGER,
            sp_end      INTEGER,
            source      VARCHAR,
            snapshot_id VARCHAR
        )
    """)

    # Use DuckDB's native CSV reader (handles gzip + comments natively).
    # Decompress to plain CSV (much easier for DuckDB to sniff with comment header).
    if not SIFTS_FILE_UNGZ.exists():
        print(f"[sifts] decompressing to {SIFTS_FILE_UNGZ}", flush=True)
        import shutil
        with gzip.open(str(SIFTS_FILE), "rb") as src:
            with open(SIFTS_FILE_UNGZ, "wb") as dst:
                shutil.copyfileobj(src, dst, length=1 << 20)
    # Strip leading "#"-comment header line from SIFTS (DuckDB 1.5.x sniffer can't combine comment + header).
    if not SIFTS_FILE_CLEAN.exists():
        print(f"[sifts] stripping comment header -> {SIFTS_FILE_CLEAN}", flush=True)
        with open(SIFTS_FILE_UNGZ, "r", encoding="utf-8") as src, \
             open(SIFTS_FILE_CLEAN, "w", encoding="utf-8", newline="") as dst:
            for i, line in enumerate(src):
                if i == 0 and line.startswith("#"):
                    continue
                dst.write(line)
    sifts_path = str(SIFTS_FILE_CLEAN).replace("\\", "/")
    print(f"[sifts] loading {sifts_path} via DuckDB read_csv", flush=True)
    sql = """
        INSERT INTO v2_pdb_uniprot
        SELECT lower(PDB) AS pdb_id,
               CHAIN AS chain,
               upper(SP_PRIMARY) AS uniprot,
               TRY_CAST(SP_BEG AS INTEGER) AS sp_beg,
               TRY_CAST(SP_END AS INTEGER) AS sp_end,
               'SIFTS' AS source,
               '__SNAPSHOT__' AS snapshot_id
        FROM read_csv('__PATH__',
                      delim=',', header=true,
                      all_varchar=true)
        WHERE PDB IS NOT NULL AND SP_PRIMARY IS NOT NULL
    """.replace("__SNAPSHOT__", SNAPSHOT_ID).replace("__PATH__", sifts_path)
    con.execute(sql)

    after = con.execute("select count(*) from v2_pdb_uniprot").fetchone()[0]
    distinct_pdb_after = con.execute("select count(distinct pdb_id) from v2_pdb_uniprot").fetchone()[0]
    distinct_uni_after = con.execute("select count(distinct uniprot) from v2_pdb_uniprot").fetchone()[0]
    print(f"[sifts] after:  {after:,} rows / {distinct_pdb_after:,} distinct PDBs / {distinct_uni_after:,} distinct UniProts")

    # PDBbind coverage check
    cov = con.execute("""
        select
          count(distinct p.pdb_id) total,
          count(distinct case when m.pdb_id is not null then p.pdb_id end) mapped
        from pdbbind_interactions p
        left join (select distinct pdb_id from v2_pdb_uniprot) m on m.pdb_id = lower(p.pdb_id)
    """).fetchone()
    print(f"[sifts] PDBbind coverage: {cov[1]:,} / {cov[0]:,} = {100*cov[1]/cov[0]:.1f}%")

    # Classic actin PDBs
    classics = con.execute("""
        select pdb_id, uniprot
        from v2_pdb_uniprot
        where pdb_id in ('1atn','2a40','3hbt','1j6z')
        order by pdb_id
    """).fetchall()
    print(f"[sifts] actin classics resolved:")
    for row in classics:
        print(f"          {row[0]} -> {row[1]}")

    con.close()


if __name__ == "__main__":
    main()
