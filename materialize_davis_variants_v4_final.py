#!/usr/bin/env python3
"""Final 13 Davis stragglers — complexes + non-human pathogens.

These can't be resolved by simple human-Swiss-Prot lookup:
  - PKAC-alpha/beta: Greek-letter notation
  - CDK4-cyclinD1/D3: kinase + activator complexes (use the kinase)
  - MRCKA/B, IKK-{alpha,beta,epsilon}, PFTAIRE2: alternative gene names
  - P. falciparum and M. tuberculosis kinases (non-human pathogens)

Hand-curated from UniProt direct lookup.
"""
import duckdb
from pathlib import Path

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"

FINAL_BATCH = {
    "PKAC-alpha":   ("PRKACA",   "P17612"),
    "PKAC-beta":    ("PRKACB",   "P22694"),
    "CDK4-cyclinD1":("CDK4",     "P11802"),
    "CDK4-cyclinD3":("CDK4",     "P11802"),
    "MRCKA":        ("CDC42BPA", "Q5VT25"),
    "MRCKB":        ("CDC42BPB", "Q9Y5S2"),
    "IKK-alpha":    ("CHUK",     "O15111"),
    "IKK-beta":     ("IKBKB",    "O14920"),
    "IKK-epsilon":  ("IKBKE",    "Q14164"),
    "PFTAIRE2":     ("CDK15",    "Q96Q40"),
    "PFCDPK1(Pfalciparum)": ("PfCDPK1", "Q8IL00"),  # Plasmodium falciparum
    "PFPK5(Pfalciparum)":   ("PfPK5",   "Q8IIP1"),  # Plasmodium falciparum
    "PKNB(Mtuberculosis)":  ("PknB",    "P9WI81"),  # Mycobacterium tuberculosis
}


def main():
    print("=== Davis variant resolution final batch (13 stragglers) ===")
    con = duckdb.connect(str(DB))

    n_ins = 0; n_up = 0
    for davis_key, (base_gene, uniprot) in FINAL_BATCH.items():
        # Variant resolution table
        existing = con.execute(
            "SELECT davis_key FROM v2_davis_variant_resolution WHERE davis_key = ?",
            [davis_key]
        ).fetchall()
        complex_partners = None
        if "-cyclinD" in davis_key:
            complex_partners = davis_key.split("-")[1]
        if existing:
            con.execute(
                "UPDATE v2_davis_variant_resolution SET "
                " base_gene=?, uniprot=?, complex_partners=? "
                "WHERE davis_key=?",
                [base_gene, uniprot, complex_partners, davis_key]
            )
            n_up += 1
        else:
            con.execute(
                "INSERT INTO v2_davis_variant_resolution "
                "(davis_key, base_gene, uniprot, complex_partners) "
                "VALUES (?, ?, ?, ?)",
                [davis_key, base_gene, uniprot, complex_partners]
            )
            n_ins += 1

        # Bridge confidence
        con.execute(
            "UPDATE davis_bridge_uniprot SET uniprot = ?, confidence = 'wt_fallback' "
            "WHERE source_key = ? AND confidence = 'unresolved'",
            [uniprot, davis_key]
        )

    print(f"  {n_ins} inserts + {n_up} updates")
    print("\n  final davis_bridge_uniprot confidence counts:")
    for r in con.execute(
        "SELECT confidence, COUNT(*) FROM davis_bridge_uniprot GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall():
        print(f"    {r[0]}: {r[1]}")
    n_with = con.execute(
        "SELECT COUNT(*) FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL"
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM davis_bridge_uniprot").fetchone()[0]
    print(f"  resolved: {n_with}/{total} ({100*n_with/total:.1f}%)")
    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
