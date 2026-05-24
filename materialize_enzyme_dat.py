#!/usr/bin/env python3
"""Tier 2 — EC numbers (Enzyme Nomenclature) for ALL UniProt via ExPASy's
enzyme.dat.

Closes the EC-on-TrEMBL gap: idmapping.dat.gz doesn't carry EC, but
ExPASy's enzyme.dat is the canonical EC catalog and lists every UniProt
member of every EC class. Small file (~25 MB), trivial to parse.

Source: https://ftp.expasy.org/databases/enzyme/enzyme.dat
Format: text-based "flat" records separated by '//', e.g.

  ID   1.1.1.1
  DE   Alcohol dehydrogenase.
  DR   P00325, ADH1A_HUMAN;  P00326, ADH2_HUMAN;  ...
  //

Output: extends the existing v2_ec_class_membership in the CORE
warehouse (we add a new source='enzyme_dat' so they're traceable
back).
"""
from __future__ import annotations
import re
import sys
import time
import urllib.request
from pathlib import Path

import duckdb
import pyarrow as pa

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
SRC = REPO / "data" / "cache" / "enzyme.dat"
URL = "https://ftp.expasy.org/databases/enzyme/enzyme.dat"

DR_PAIR = re.compile(r"([A-Z][A-Z0-9]{5,9}),\s*([A-Z0-9_]+);")


def fetch():
    if SRC.exists() and SRC.stat().st_size > 0:
        print(f"  cached: {SRC.name} ({SRC.stat().st_size/1024:.1f} KB)")
        return
    print(f"  downloading: {URL}")
    SRC.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(URL, SRC)
    print(f"  wrote {SRC.name} ({SRC.stat().st_size/1024:.1f} KB)")


def main():
    print("=== EC numbers via enzyme.dat ===")
    fetch()

    rows = []  # (uniprot, ec_full, ec3, ec2, source)
    current_ec = None
    n_entries = 0
    t0 = time.time()
    with open(SRC, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("ID   "):
                current_ec = line[5:].strip()
                n_entries += 1
            elif line.startswith("DR   ") and current_ec:
                # Parse all "UNIPROT, NAME;" pairs on the line
                for acc, _name in DR_PAIR.findall(line):
                    parts = current_ec.split(".")
                    ec3 = ".".join(parts[:3]) if len(parts) >= 3 else current_ec
                    ec2 = ".".join(parts[:2]) if len(parts) >= 2 else current_ec
                    rows.append((acc, current_ec, ec3, ec2, "enzyme_dat"))
            elif line.startswith("//"):
                current_ec = None

    elapsed = time.time() - t0
    print(f"\n  parsed {n_entries:,} EC entries in {elapsed:.1f}s")
    print(f"  total UniProt-EC pairs extracted: {len(rows):,}")

    # Inject into v2_ec_class_membership (existing table, just add 'source=enzyme_dat' rows)
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_ec_class_membership)").fetchall()]
    print(f"  v2_ec_class_membership cols: {cols}")
    # cols: uniprot, source, ec4, ec3, ec2, label, snapshot_id

    n_before = con.execute("SELECT COUNT(*) FROM v2_ec_class_membership").fetchone()[0]
    print(f"  before: {n_before:,}")

    # Convert to Arrow and INSERT
    table = pa.table({
        "uniprot":     pa.array([r[0] for r in rows], type=pa.string()),
        "source":      pa.array([r[4] for r in rows], type=pa.string()),
        "ec4":         pa.array([r[1] for r in rows], type=pa.string()),
        "ec3":         pa.array([r[2] for r in rows], type=pa.string()),
        "ec2":         pa.array([r[3] for r in rows], type=pa.string()),
        "label":       pa.array([""] * len(rows), type=pa.string()),
        "snapshot_id": pa.array(["20260524"] * len(rows), type=pa.string()),
    })
    con.register("ec_arrow", table)
    con.execute("INSERT INTO v2_ec_class_membership SELECT * FROM ec_arrow")
    con.unregister("ec_arrow")

    # Dedupe
    con.execute("""
      CREATE OR REPLACE TABLE v2_ec_class_membership AS
      SELECT DISTINCT uniprot, source, ec4, ec3, ec2,
                      ANY_VALUE(label) AS label,
                      ANY_VALUE(snapshot_id) AS snapshot_id
      FROM v2_ec_class_membership
      GROUP BY uniprot, source, ec4, ec3, ec2
    """)
    n_after = con.execute("SELECT COUNT(*) FROM v2_ec_class_membership").fetchone()[0]
    print(f"  after:  {n_after:,}  (delta {n_after - n_before:+,})")

    # Stats
    n_enzymedat = con.execute(
        "SELECT COUNT(*) FROM v2_ec_class_membership WHERE source='enzyme_dat'"
    ).fetchone()[0]
    n_uniprots = con.execute(
        "SELECT COUNT(DISTINCT uniprot) FROM v2_ec_class_membership WHERE source='enzyme_dat'"
    ).fetchone()[0]
    print(f"  enzyme_dat-sourced rows: {n_enzymedat:,} ({n_uniprots:,} distinct UniProts)")

    con.execute("CHECKPOINT")
    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
