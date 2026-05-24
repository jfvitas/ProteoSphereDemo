#!/usr/bin/env python3
"""Tier 2 — SCOP + CATH fold classifications.

Sources:
  - SCOP via EBI PDBe:
    https://www.ebi.ac.uk/pdbe/scop/files/scop-cla-latest.txt
  - CATH classification list:
    https://download.cathdb.info/cath/releases/latest-release/
    cath-classification-data/cath-domain-list.txt

Both are small (<50 MB total), text-based, written directly into the
core warehouse (not an extension shard) as:
  v2_scop_membership(uniprot, scop_class, scop_fold,
                     scop_superfamily, scop_family,
                     pdb_id, pdb_region, scop_fa_domid)
  v2_cath_membership(pdb_id, chain, cath_domain_id,
                     cath_class, cath_architecture,
                     cath_topology, cath_homol_superfamily)

CATH is keyed by (PDB, chain) rather than UniProt; join to UniProt
via v2_pdb_uniprot when needed.
"""
from __future__ import annotations
import re
from pathlib import Path
import duckdb
import pyarrow as pa

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
SCOP_SRC = REPO / "data" / "cache" / "scop-cla-latest.txt"
CATH_SRC = REPO / "data" / "cache" / "cath-domain-list.txt"


def parse_scop():
    """Yield (uniprot, scop_class, scop_fold, scop_superfamily, scop_family, pdb_id, pdb_region, scop_fa_domid)."""
    if not SCOP_SRC.exists():
        print(f"  WARN: {SCOP_SRC} not found, skipping SCOP")
        return
    cla_re = re.compile(r"TP=(\d+),CL=(\d+),CF=(\d+),SF=(\d+),FA=(\d+)")
    n_lines = n_emitted = 0
    with open(SCOP_SRC, "r", encoding="utf-8") as fh:
        for line in fh:
            n_lines += 1
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            # Columns: FA-DOMID FA-PDBID FA-PDBREG FA-UNIID FA-UNIREG SF-DOMID SF-PDBID SF-PDBREG SF-UNIID SF-UNIREG SCOPCLA
            if len(parts) < 11:
                continue
            fa_domid = parts[0]
            fa_pdbid = parts[1]
            fa_pdbreg = parts[2]
            fa_uniid = parts[3]
            scopcla = parts[10]
            m = cla_re.search(scopcla)
            if not m:
                continue
            tp, cl, cf, sf, fa = m.groups()
            yield (fa_uniid, cl, cf, sf, fa, fa_pdbid, fa_pdbreg, fa_domid)
            n_emitted += 1
    print(f"  SCOP: {n_emitted:,} rows emitted from {n_lines:,} lines")


def parse_cath():
    """Yield (pdb_id, chain, cath_domain_id, cath_class, arch, topology, homol_sf)."""
    if not CATH_SRC.exists():
        print(f"  WARN: {CATH_SRC} not found, skipping CATH")
        return
    n_lines = n_emitted = 0
    with open(CATH_SRC, "r", encoding="utf-8") as fh:
        for line in fh:
            n_lines += 1
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            # Standard CLF format: DOMAIN_ID CLASS ARCH TOPOL HOMOL S35 S60 S95 S100 RES LENGTH RESOLUTION
            domain_id = parts[0]
            try:
                cls = int(parts[1])
                arch = int(parts[2])
                topol = int(parts[3])
                homol = int(parts[4])
            except (ValueError, IndexError):
                continue
            # Domain ID format: <pdb_id><chain><domain> e.g. 1abcA01
            pdb_id = domain_id[:4].upper()
            chain = domain_id[4:5] if len(domain_id) > 4 else None
            yield (pdb_id, chain, domain_id, cls, arch, topol, homol)
            n_emitted += 1
    print(f"  CATH: {n_emitted:,} rows emitted from {n_lines:,} lines")


def main():
    print("=== SCOP + CATH fold classification materializer ===")
    scop_rows = list(parse_scop())
    cath_rows = list(parse_cath())

    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # SCOP
    print("\n  writing v2_scop_membership…")
    con.execute("DROP TABLE IF EXISTS v2_scop_membership")
    if scop_rows:
        table = pa.table({
            "uniprot":          pa.array([r[0] for r in scop_rows], type=pa.string()),
            "scop_class":       pa.array([r[1] for r in scop_rows], type=pa.string()),
            "scop_fold":        pa.array([r[2] for r in scop_rows], type=pa.string()),
            "scop_superfamily": pa.array([r[3] for r in scop_rows], type=pa.string()),
            "scop_family":      pa.array([r[4] for r in scop_rows], type=pa.string()),
            "pdb_id":           pa.array([r[5] for r in scop_rows], type=pa.string()),
            "pdb_region":       pa.array([r[6] for r in scop_rows], type=pa.string()),
            "scop_fa_domid":    pa.array([r[7] for r in scop_rows], type=pa.string()),
        })
        con.register("scop_arrow", table)
        con.execute("CREATE TABLE v2_scop_membership AS SELECT * FROM scop_arrow")
        con.unregister("scop_arrow")
    else:
        con.execute("""CREATE TABLE v2_scop_membership
                       (uniprot VARCHAR, scop_class VARCHAR, scop_fold VARCHAR,
                        scop_superfamily VARCHAR, scop_family VARCHAR,
                        pdb_id VARCHAR, pdb_region VARCHAR, scop_fa_domid VARCHAR)""")
    n_scop = con.execute("SELECT COUNT(*) FROM v2_scop_membership").fetchone()[0]
    n_scop_uniprots = con.execute("SELECT COUNT(DISTINCT uniprot) FROM v2_scop_membership").fetchone()[0]
    print(f"    v2_scop_membership: {n_scop:,} rows ({n_scop_uniprots:,} distinct UniProts)")

    # CATH
    print("\n  writing v2_cath_membership…")
    con.execute("DROP TABLE IF EXISTS v2_cath_membership")
    if cath_rows:
        table = pa.table({
            "pdb_id":                  pa.array([r[0] for r in cath_rows], type=pa.string()),
            "chain":                   pa.array([r[1] for r in cath_rows], type=pa.string()),
            "cath_domain_id":          pa.array([r[2] for r in cath_rows], type=pa.string()),
            "cath_class":              pa.array([r[3] for r in cath_rows], type=pa.int32()),
            "cath_architecture":       pa.array([r[4] for r in cath_rows], type=pa.int32()),
            "cath_topology":           pa.array([r[5] for r in cath_rows], type=pa.int32()),
            "cath_homol_superfamily":  pa.array([r[6] for r in cath_rows], type=pa.int32()),
        })
        con.register("cath_arrow", table)
        con.execute("CREATE TABLE v2_cath_membership AS SELECT * FROM cath_arrow")
        con.unregister("cath_arrow")
    else:
        con.execute("""CREATE TABLE v2_cath_membership
                       (pdb_id VARCHAR, chain VARCHAR, cath_domain_id VARCHAR,
                        cath_class INTEGER, cath_architecture INTEGER,
                        cath_topology INTEGER, cath_homol_superfamily INTEGER)""")
    n_cath = con.execute("SELECT COUNT(*) FROM v2_cath_membership").fetchone()[0]
    n_cath_pdbs = con.execute("SELECT COUNT(DISTINCT pdb_id) FROM v2_cath_membership").fetchone()[0]
    print(f"    v2_cath_membership: {n_cath:,} rows ({n_cath_pdbs:,} distinct PDB IDs)")

    # Quick sample
    if n_scop > 0:
        print("\n  spot-check SCOP for actin (UniProts P60709, P68133):")
        for r in con.execute(
            "SELECT uniprot, scop_class, scop_fold, scop_superfamily, scop_family "
            "FROM v2_scop_membership WHERE uniprot IN ('P60709','P68133') LIMIT 5"
        ).fetchall():
            print(f"    {r}")

    con.execute("CHECKPOINT")
    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
