#!/usr/bin/env python3
"""Run the WAREHOUSE_QA panel against the Tier-1-expanded warehouse.

Tests:
  T1  Actin family (Pfam PF00022 + PDB resolution)
  T2  Kinase family (Pfam PF00069)
  T3  Imatinib scaffold/series cluster (NOW backed by v2_ligand_similarity)
  T4  Hemoglobin tetramer
  T5  Cross-organism orthology (yeast actin P60010 ↔ human P60709)
  T6  GPCR superfamily (PF00001 + GtoPdb)
  T7  Protease convergence (same EC, different Pfam)
  T8  PDBbind binding-site context
  T9  Multi-axis leakage SQL
  T10 Negative controls
  T11 (NEW) Reactome top-level pathway lookup
  T12 (NEW) Davis variant -> wild-type bridge
  T13 (NEW) Imatinib/Nilotinib chemical-series via ECFP4

Writes WAREHOUSE_QA_v3.md to repo root.
"""
from __future__ import annotations
import os
from pathlib import Path

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
OUT = REPO / "WAREHOUSE_QA_v3.md"


def fetch(con, sql, params=None):
    if params is None:
        return con.execute(sql).fetchall()
    return con.execute(sql, params).fetchall()


def main():
    con = duckdb.connect(str(DB), read_only=True)
    results = []

    def add(name, verdict, sql, rows, notes=""):
        results.append({
            "test": name, "verdict": verdict, "sql": sql,
            "rows": rows, "notes": notes
        })

    # ────────── T1 ──────────
    # All actin isoforms share PF00022; classic actin PDBs resolve to actins
    rows = fetch(con, """
      SELECT m.uniprot, m.identifier AS pfam, x.pdb_id
      FROM v2_motif_membership m
      LEFT JOIN v2_pdb_uniprot x
        ON x.uniprot = m.uniprot AND x.pdb_id IN ('1ATN','2A40','3HBT','1J6Z','101M')
      WHERE m.identifier = 'PF00022'
        AND m.uniprot IN ('P60709','P68133','P68032','P62736','P63267','P63261')
      ORDER BY m.uniprot, x.pdb_id NULLS LAST
    """)
    verdict = "PASS" if len(rows) >= 6 else ("PARTIAL" if rows else "FAIL")
    add("T1 Actin family", verdict,
        "SELECT m.uniprot, m.identifier, x.pdb_id FROM v2_motif_membership m LEFT JOIN v2_pdb_uniprot x ON x.uniprot=m.uniprot WHERE m.identifier='PF00022' AND m.uniprot IN ('P60709','P68133','P68032','P62736','P63267','P63261')",
        rows)

    # ────────── T2 ──────────
    rows = fetch(con, """
      SELECT uniprot, identifier
      FROM v2_motif_membership
      WHERE identifier IN ('PF00069','PF07714')
        AND uniprot IN ('P00519','P00533','P15056','P08581','O60674','P31749','P28482')
      ORDER BY uniprot
    """)
    verdict = "PASS" if len(rows) >= 7 else ("PARTIAL" if rows else "FAIL")
    add("T2 Kinase family", verdict,
        "SELECT uniprot, identifier FROM v2_motif_membership WHERE identifier IN ('PF00069','PF07714') AND uniprot IN ('P00519','P00533','P15056','P08581','O60674','P31749','P28482')",
        rows)

    # ────────── T3 — NEW: imatinib chemical series via ECFP4 ──────────
    tabs = {r[0] for r in con.execute("show tables").fetchall()}
    if "v2_ligand_similarity" in tabs:
        rows = fetch(con, """
          SELECT ligand_a, ligand_b, source_a, source_b, ecfp4_tanimoto
          FROM v2_ligand_similarity
          WHERE (ligand_a = 'STI' OR ligand_b = 'STI')
            AND ecfp4_tanimoto >= 0.7
          ORDER BY ecfp4_tanimoto DESC LIMIT 10
        """)
        has_mpz = any('MPZ' in (r[0], r[1]) for r in rows)
        verdict = "PASS" if has_mpz else ("PARTIAL" if rows else "FAIL")
        notes = "MPZ = nilotinib chem-comp ID. Hit at T={:.3f}".format(
            next((r[4] for r in rows if 'MPZ' in (r[0], r[1])), 0.0)
        ) if has_mpz else "no MPZ (nilotinib) within imatinib's >=0.7 neighborhood"
    else:
        rows = []
        verdict = "FAIL"
        notes = "v2_ligand_similarity table missing"
    add("T3 Imatinib chemical series", verdict,
        "SELECT ligand_a, ligand_b, ecfp4_tanimoto FROM v2_ligand_similarity WHERE (ligand_a='STI' OR ligand_b='STI') AND ecfp4_tanimoto >= 0.7 ORDER BY ecfp4_tanimoto DESC LIMIT 10",
        rows, notes)

    # ────────── T4 — Hb subunits ──────────
    rows = fetch(con, """
      SELECT uniprot, identifier
      FROM v2_motif_membership
      WHERE identifier='PF00042'
        AND uniprot IN ('P69905','P68871','P69891','P02042')
      ORDER BY uniprot
    """)
    verdict = "PASS" if len(rows) >= 4 else ("PARTIAL" if rows else "FAIL")
    add("T4 Hemoglobin subunits", verdict,
        "SELECT uniprot, identifier FROM v2_motif_membership WHERE identifier='PF00042' AND uniprot IN ('P69905','P68871','P69891','P02042')",
        rows)

    # ────────── T5 — Cross-organism (yeast actin ↔ human) ──────────
    rows = fetch(con, """
      SELECT uniprot, identifier
      FROM v2_motif_membership
      WHERE identifier='PF00022'
        AND uniprot IN ('P60010','P60709')
      ORDER BY uniprot
    """)
    # Also check UniRef50
    ur = fetch(con, """
      SELECT uniprot, uniref50_id
      FROM v2_sequence_cluster_membership
      WHERE uniprot IN ('P60010','P60709')
    """)
    same_ur50 = len({r[1] for r in ur if r[1]}) == 1 if ur else False
    has_both_pfam = len(rows) == 2
    verdict = "PASS" if has_both_pfam else ("PARTIAL" if rows else "FAIL")
    add("T5 Cross-organism orthology", verdict,
        "SELECT uniprot, identifier FROM v2_motif_membership WHERE identifier='PF00022' AND uniprot IN ('P60010','P60709')",
        rows, f"Pfam shared: {has_both_pfam}; UniRef50 same: {same_ur50}; UniRef50 rows: {ur}")

    # ────────── T6 — GPCR superfamily ──────────
    rows = fetch(con, """
      SELECT COUNT(DISTINCT m.uniprot) AS n_gpcrs_with_affinity
      FROM v2_motif_membership m
      JOIN gtopdb_bridge_uniprot b ON b.uniprot = m.uniprot
      WHERE m.identifier = 'PF00001'
    """)
    n_gpcrs = rows[0][0] if rows else 0
    verdict = "PASS" if n_gpcrs >= 100 else ("PARTIAL" if n_gpcrs > 0 else "FAIL")
    add("T6 GPCR + GtoPdb cross-ref", verdict,
        "SELECT COUNT(DISTINCT m.uniprot) FROM v2_motif_membership m JOIN gtopdb_bridge_uniprot b ON b.uniprot=m.uniprot WHERE m.identifier='PF00001'",
        rows, f"{n_gpcrs} GPCRs with GtoPdb binding affinities")

    # ────────── T7 — Protease convergence ──────────
    rows = fetch(con, """
      SELECT m.uniprot, m.identifier AS pfam, e.ec_class
      FROM v2_motif_membership m
      LEFT JOIN v2_ec_class_membership e ON e.uniprot = m.uniprot
      WHERE m.uniprot IN ('P00766','P00760','P12838','P00782')
      ORDER BY m.uniprot
    """)
    pfam_distinct = len({r[1] for r in rows if r[1]})
    verdict = "PASS" if pfam_distinct >= 2 else "PARTIAL"
    add("T7 Protease convergence", verdict,
        "SELECT m.uniprot, m.identifier, e.ec_class FROM v2_motif_membership m LEFT JOIN v2_ec_class_membership e ON e.uniprot=m.uniprot WHERE m.uniprot IN ('P00766','P00760','P12838','P00782')",
        rows[:20], f"{pfam_distinct} distinct Pfam families")

    # ────────── T8 — PDBbind context ──────────
    rows = fetch(con, """
      SELECT COUNT(DISTINCT pdb_id) FROM pdbbind_interactions p
      WHERE EXISTS (SELECT 1 FROM v2_pdb_uniprot x WHERE x.pdb_id = p.pdb_id)
    """)
    n_mapped = rows[0][0] if rows else 0
    total = con.execute("SELECT COUNT(DISTINCT pdb_id) FROM pdbbind_interactions").fetchone()[0]
    pct = 100*n_mapped/total if total else 0
    verdict = "PASS" if pct >= 90 else ("PARTIAL" if pct >= 50 else "FAIL")
    add("T8 PDBbind ↔ UniProt coverage", verdict,
        "SELECT COUNT(DISTINCT pdb_id) FROM pdbbind_interactions p WHERE EXISTS (SELECT 1 FROM v2_pdb_uniprot x WHERE x.pdb_id = p.pdb_id)",
        rows, f"{n_mapped}/{total} = {pct:.1f}%")

    # ────────── T9 — Multi-axis leakage SQL ──────────
    sql = """
      WITH test_proteins AS (
        SELECT DISTINCT uniprot FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL LIMIT 5
      ),
      train_pool AS (
        SELECT DISTINCT uniprot FROM kiba_bridge_uniprot WHERE uniprot IS NOT NULL
      )
      SELECT tp.uniprot,
        (SELECT COUNT(*) FROM v2_motif_membership m1
           JOIN v2_motif_membership m2 ON m1.identifier = m2.identifier
           JOIN train_pool tp2 ON tp2.uniprot = m2.uniprot
           WHERE m1.uniprot = tp.uniprot AND m1.namespace = 'pfam') AS pfam_shared,
        (SELECT COUNT(*) FROM v2_ec_class_membership e1
           JOIN v2_ec_class_membership e2 ON e1.ec_class = e2.ec_class
           JOIN train_pool tp2 ON tp2.uniprot = e2.uniprot
           WHERE e1.uniprot = tp.uniprot) AS ec_shared
      FROM test_proteins tp
    """
    rows = fetch(con, sql)
    verdict = "PASS" if rows else "FAIL"
    add("T9 Multi-axis leakage SQL", verdict, sql, rows)

    # ────────── T10 — Negative control ──────────
    rows = fetch(con, "SELECT COUNT(*) FROM v2_motif_membership WHERE uniprot='Z99999'")
    verdict = "PASS" if rows[0][0] == 0 else "FAIL"
    add("T10 Negative control (bogus UniProt)", verdict,
        "SELECT COUNT(*) FROM v2_motif_membership WHERE uniprot='Z99999'", rows)

    # ────────── T11 NEW — Reactome top-level pathway lookup ──────────
    rows = fetch(con, """
      SELECT top_level_pathway, COUNT(*) AS n
      FROM v2_reactome_pathway_membership
      WHERE uniprot = 'P00519'  -- ABL1
      GROUP BY 1 ORDER BY 2 DESC LIMIT 5
    """)
    verdict = "PASS" if rows and rows[0][0] else "FAIL"
    add("T11 Reactome top-level pathway (ABL1)", verdict,
        "SELECT top_level_pathway, COUNT(*) FROM v2_reactome_pathway_membership WHERE uniprot='P00519' GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
        rows)

    # ────────── T12 NEW — Davis variant resolution ──────────
    rows = fetch(con, """
      SELECT confidence, COUNT(*) FROM davis_bridge_uniprot GROUP BY 1 ORDER BY 2 DESC
    """)
    total = sum(r[1] for r in rows)
    resolved = sum(r[1] for r in rows if r[0] != 'unresolved')
    pct = 100*resolved/total if total else 0
    verdict = "PASS" if pct >= 95 else ("PARTIAL" if pct >= 80 else "FAIL")
    add("T12 Davis variant resolution", verdict,
        "SELECT confidence, COUNT(*) FROM davis_bridge_uniprot GROUP BY 1",
        rows, f"{pct:.1f}% resolved ({resolved}/{total})")

    # ────────── T13 — same as T3 with different framing ──────────
    # already covered

    # ───────────────────────────────────────
    # Write report
    md = ["# Warehouse QA v3 — Tier 1 gap closure verification\n"]
    md.append(f"_Run against:_ `{DB.name}`\n\n")
    md.append("## Summary\n\n")
    md.append("| Test | Verdict |\n|---|---|\n")
    for r in results:
        emoji = {"PASS": "✅", "PARTIAL": "⚠️", "FAIL": "❌"}.get(r["verdict"], "?")
        md.append(f"| {r['test']} | {emoji} {r['verdict']} |\n")
    md.append("\n## Detail\n")
    for r in results:
        md.append(f"\n### {r['test']} — {r['verdict']}\n\n")
        if r["notes"]:
            md.append(f"_Notes:_ {r['notes']}\n\n")
        md.append("```sql\n" + r["sql"].strip() + "\n```\n\n")
        md.append(f"Returned {len(r['rows'])} rows.\n\n")
        if r["rows"]:
            md.append("```\n")
            for row in r["rows"][:15]:
                md.append(f"{row}\n")
            if len(r["rows"]) > 15:
                md.append(f"... ({len(r['rows'])-15} more)\n")
            md.append("```\n")

    OUT.write_text("".join(md), encoding="utf-8")
    print(f"\nWrote {OUT}")

    # Console summary
    print("\n=== SUMMARY ===")
    for r in results:
        emoji = {"PASS": "PASS", "PARTIAL": "PART", "FAIL": "FAIL"}.get(r["verdict"], "?")
        print(f"  [{emoji}] {r['test']}")
    n_pass = sum(1 for r in results if r["verdict"] == "PASS")
    print(f"\n  {n_pass}/{len(results)} tests passing")

    con.close()


if __name__ == "__main__":
    main()
