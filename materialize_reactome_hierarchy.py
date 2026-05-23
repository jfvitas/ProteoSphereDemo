#!/usr/bin/env python3
"""Backfill the empty top_level_pathway column in v2_reactome_pathway_membership.

The original materialize_interactions.py loaded UniProt2Reactome_PE_Pathway.txt
which gives (uniprot, pathway_id, pathway_name) but no hierarchy. This script
downloads ReactomePathwaysRelation.txt (parent->child edges), traces each
pathway up to its top-level ancestor, and UPDATEs the column.

Source: https://reactome.org/download/current/ReactomePathwaysRelation.txt
Run:    python materialize_reactome_hierarchy.py
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import duckdb
import urllib.request

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = REPO / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

REL_URL = "https://reactome.org/download/current/ReactomePathwaysRelation.txt"
NAMES_URL = "https://reactome.org/download/current/ReactomePathways.txt"
REL_PATH = CACHE / "ReactomePathwaysRelation.txt"
NAMES_PATH = CACHE / "ReactomePathways.txt"


def _download(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 0:
        print(f"  cached: {dst.name} ({dst.stat().st_size/1024:.1f} KB)")
        return
    print(f"  downloading: {url}")
    urllib.request.urlretrieve(url, dst)
    print(f"  wrote {dst.name} ({dst.stat().st_size/1024:.1f} KB)")


def main() -> None:
    print("=== Reactome hierarchy backfill ===")
    _download(REL_URL, REL_PATH)
    _download(NAMES_URL, NAMES_PATH)

    # Parse: ReactomePathwaysRelation.txt has lines "PARENT\tCHILD"
    parent_of: dict[str, str] = {}
    with open(REL_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2:
                continue
            parent, child = parts
            # Only keep human pathways for now (R-HSA-*)? No — Reactome's
            # relation file is multi-species; we keep all so the column
            # works for non-human entries too.
            parent_of[child] = parent
    print(f"  parsed {len(parent_of):,} parent-child relations")

    # Parse names file: PATHWAY_ID\tNAME\tSPECIES
    name_of: dict[str, str] = {}
    with open(NAMES_PATH, "r", encoding="utf-8") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 2:
                continue
            name_of[parts[0]] = parts[1]
    print(f"  parsed {len(name_of):,} pathway names")

    # Traverse upward from each pathway to find its top-level ancestor.
    def root_of(pid: str) -> str:
        seen = set()
        cur = pid
        while cur in parent_of and cur not in seen:
            seen.add(cur)
            cur = parent_of[cur]
        return cur

    # Connect to warehouse + collect distinct pathway IDs.
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    rows = con.execute(
        "SELECT DISTINCT pathway_id FROM v2_reactome_pathway_membership"
    ).fetchall()
    distinct_pathways = [r[0] for r in rows]
    print(f"  warehouse has {len(distinct_pathways):,} distinct pathway IDs")

    # Build mapping: pathway_id -> (root_id, root_name)
    mapping = {}
    for pid in distinct_pathways:
        root = root_of(pid)
        mapping[pid] = (root, name_of.get(root, root))

    n_resolved = sum(1 for p, _ in mapping.values() if _)
    print(f"  resolved root for {n_resolved:,}/{len(distinct_pathways):,} pathways")

    # Create a temp table and join-update via DuckDB's UPDATE FROM syntax.
    print("  applying UPDATE…")
    con.execute("DROP TABLE IF EXISTS _reactome_root_map")
    con.execute(
        "CREATE TEMP TABLE _reactome_root_map ("
        " pathway_id VARCHAR PRIMARY KEY, "
        " top_level_pathway_id VARCHAR, "
        " top_level_pathway_name VARCHAR)"
    )
    con.executemany(
        "INSERT INTO _reactome_root_map VALUES (?, ?, ?)",
        [(pid, r, n) for pid, (r, n) in mapping.items()],
    )

    cols_pre = con.execute(
        "PRAGMA table_info(v2_reactome_pathway_membership)"
    ).fetchall()
    have_col = any(r[1] == "top_level_pathway" for r in cols_pre)
    have_id_col = any(r[1] == "top_level_pathway_id" for r in cols_pre)
    if not have_id_col:
        con.execute(
            "ALTER TABLE v2_reactome_pathway_membership "
            "ADD COLUMN top_level_pathway_id VARCHAR"
        )

    con.execute(
        "UPDATE v2_reactome_pathway_membership AS m "
        "SET top_level_pathway = r.top_level_pathway_name, "
        "    top_level_pathway_id = r.top_level_pathway_id "
        "FROM _reactome_root_map AS r WHERE m.pathway_id = r.pathway_id"
    )

    # Verify
    n_filled = con.execute(
        "SELECT COUNT(*) FROM v2_reactome_pathway_membership "
        "WHERE top_level_pathway IS NOT NULL AND top_level_pathway != ''"
    ).fetchone()[0]
    n_total = con.execute(
        "SELECT COUNT(*) FROM v2_reactome_pathway_membership"
    ).fetchone()[0]
    print(f"  v2_reactome_pathway_membership.top_level_pathway: "
          f"{n_filled:,}/{n_total:,} non-null after backfill")

    # Spot-check
    print("\n  spot-check (5 most common top-level pathways):")
    rows = con.execute(
        "SELECT top_level_pathway, COUNT(*) AS n "
        "FROM v2_reactome_pathway_membership "
        "WHERE top_level_pathway IS NOT NULL "
        "GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
    ).fetchall()
    for r in rows:
        print(f"    {r[0]}: {r[1]:,}")

    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
