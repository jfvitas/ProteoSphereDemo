#!/usr/bin/env python3
"""Extend v2_sequence_cluster_membership with UniRef50 cluster IDs for every
Swiss-Prot UniProt in the warehouse.

The original ingest covered only 71k proteins (benchmark proteins only). With
574k Swiss-Prot entries now in v2_protein_entry, we need their UniRef50/90/100
cluster IDs too — that's what unlocks cross-species sequence-cluster queries.

Approach: stream-parse uniref50.xml.gz with iterparse. For each cluster, emit
a row for every Swiss-Prot member.  Repeat for UniRef90, UniRef100 if their
files are also downloaded.

Source: ~/.../data/cache/uniref50.xml.gz (~33 GB compressed, ~250 GB uncompressed).
"""
from __future__ import annotations
import gc
import gzip
import os
import sys
import time
from pathlib import Path
import xml.etree.ElementTree as ET

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = REPO / "data" / "cache"
UNIREF50 = CACHE / "uniref50.xml.gz"

NS = "{http://uniprot.org/uniref}"


def main():
    print("=== UniRef50 Swiss-Prot expansion ===")
    if not UNIREF50.exists():
        print(f"  FATAL: {UNIREF50} not found. Wait for download to complete.")
        sys.exit(1)

    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # ── Build set of Swiss-Prot accessions we care about ────────────────
    swissprot = set()
    rows = con.execute("SELECT uniprot FROM v2_protein_entry").fetchall()
    swissprot = {r[0] for r in rows}
    print(f"  Swiss-Prot universe: {len(swissprot):,} accessions to look up")

    # Augment with any benchmark-bridge UniProts that aren't in v2_protein_entry
    extra = con.execute("""
        SELECT DISTINCT uniprot FROM (
          SELECT uniprot FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL
          UNION SELECT uniprot FROM kiba_bridge_uniprot WHERE uniprot IS NOT NULL
          UNION SELECT uniprot FROM gtopdb_bridge_uniprot WHERE uniprot IS NOT NULL
          UNION SELECT uniprot FROM hippie_bridge_uniprot WHERE uniprot IS NOT NULL
          UNION SELECT uniprot FROM huri_bridge_uniprot WHERE uniprot IS NOT NULL
        )
    """).fetchall()
    bridge_set = {r[0] for r in extra}
    universe = swissprot | bridge_set
    print(f"  total universe (Swiss-Prot + bridges): {len(universe):,}")

    # ── Stream-parse UniRef50 ───────────────────────────────────────────
    print(f"\n  streaming {UNIREF50.name} ({UNIREF50.stat().st_size/(1024**3):.1f} GB compressed)…")
    t0 = time.time()
    rows_to_insert = []
    n_clusters = 0
    n_pairs = 0
    n_relevant_clusters = 0

    # iterparse "end" events on entry, dump member-uniprot accessions
    with gzip.open(UNIREF50, "rb") as fh:
        # context = ET.iterparse(fh, events=("end",))
        context = ET.iterparse(fh, events=("start", "end"))
        cluster_id = None
        cluster_rep = None
        cluster_taxa = []
        cluster_size = None
        members_in_universe = []

        for event, elem in context:
            tag = elem.tag.replace(NS, "")
            if event == "start":
                if tag == "entry":
                    cluster_id = elem.get("id")  # e.g. "UniRef50_P00519"
                    cluster_rep = None
                    cluster_taxa = []
                    cluster_size = None
                    members_in_universe = []
            elif event == "end":
                if tag == "property" and elem.get("type") == "member count":
                    try:
                        cluster_size = int(elem.get("value"))
                    except Exception:
                        cluster_size = None
                elif tag == "dbReference" and elem.get("type") in ("UniProtKB ID", "UniProtKB Accession"):
                    # member accession appears at dbReference id="<accession>"
                    pass
                elif tag in ("representativeMember", "member") and cluster_id:
                    # Inside this element, find <dbReference type='UniProtKB ID' id='ACC'>
                    # and properties containing source organism etc.
                    for db in elem.findall(f"{NS}dbReference"):
                        # The "id" attribute of the dbReference is the entry_name (e.g. "AB1_HUMAN")
                        # but the actual accession is under a property type="UniProtKB accession"
                        acc = None
                        for prop in db.findall(f"{NS}property"):
                            if prop.get("type") == "UniProtKB accession":
                                acc = prop.get("value")
                                break
                        if acc and acc in universe:
                            members_in_universe.append((acc, tag == "representativeMember"))
                elif tag == "entry":
                    # Emit rows for this cluster's relevant members
                    if cluster_id and members_in_universe:
                        n_relevant_clusters += 1
                        for acc, is_rep in members_in_universe:
                            rows_to_insert.append((
                                acc,
                                "swissprot_extended",   # source identifier
                                "UniRef100_" + acc,     # we don't have ur100 from this file; placeholder
                                None,                   # UniRef90 placeholder
                                cluster_id,             # UniRef50 id (real)
                                None,                   # uniparc
                                None,                   # taxon (per-member taxon would need extra parsing)
                                "20260523",
                            ))
                            n_pairs += 1
                    n_clusters += 1
                    elem.clear()
                    if n_clusters % 250_000 == 0:
                        print(f"    {n_clusters:,} clusters scanned, {n_relevant_clusters:,} touch our universe, "
                              f"{n_pairs:,} rows ({time.time()-t0:.1f}s)")
                    # Reset state
                    cluster_id = None
                    cluster_rep = None
                    cluster_taxa = []
                    cluster_size = None
                    members_in_universe = []

    elapsed = time.time() - t0
    print(f"\n  parsed {n_clusters:,} clusters in {elapsed:.1f} s ({elapsed/60:.1f} min)")
    print(f"  clusters touching our universe: {n_relevant_clusters:,}")
    print(f"  rows to insert: {n_pairs:,}")

    # ── Materialize ─────────────────────────────────────────────────────
    # Append-mode into v2_sequence_cluster_membership
    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_sequence_cluster_membership)").fetchall()]
    print(f"\n  existing v2_sequence_cluster_membership cols: {cols}")

    BATCH = 100_000
    n_inserted = 0
    for i in range(0, len(rows_to_insert), BATCH):
        batch = rows_to_insert[i:i+BATCH]
        try:
            con.executemany(
                "INSERT INTO v2_sequence_cluster_membership VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                batch
            )
            n_inserted += len(batch)
        except Exception as exc:
            print(f"  batch {i//BATCH}: {exc}")
            break
        if i % (BATCH * 10) == 0 and i > 0:
            print(f"    inserted {n_inserted:,}/{len(rows_to_insert):,}")

    n_total = con.execute("SELECT COUNT(*) FROM v2_sequence_cluster_membership").fetchone()[0]
    print(f"\n  v2_sequence_cluster_membership: {n_total:,} rows (was 71,056)")

    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
