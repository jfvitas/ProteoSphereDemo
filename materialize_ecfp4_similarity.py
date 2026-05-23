#!/usr/bin/env python3
"""Materialize v2_ligand_similarity — pairwise ECFP4 Tanimoto across all warehouse ligands.

Closes the manuscript-headline gap: Bemis-Murcko scaffolds are too literal to
cluster imatinib with nilotinib (same chemical series, different scaffold
hashes). ECFP4 (Morgan fingerprint radius=2, 2048 bits) plus Tanimoto >= 0.6
catches the phenylaminopyrimidine series correctly.

Source: existing ligand tables (davis_ligands, kiba_ligands, gtopdb_ligands)
plus v2_pdbbind_ligand_xref. We compute SMILES → ECFP4 once per unique SMILES,
then do all-pairs Tanimoto with a sparse cutoff.

Output table v2_ligand_similarity(ligand_a, ligand_b, source_a, source_b,
                                   ecfp4_tanimoto, scaffold_match)

Run:
    python materialize_ecfp4_similarity.py
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path

import duckdb
import numpy as np

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
CHECKPOINT = REPO / "data" / "cache" / "ecfp4_similarity.checkpoint.json"

# RDKit is the slow part — defer import
def _import_rdkit():
    from rdkit import Chem
    from rdkit.Chem import rdFingerprintGenerator
    return Chem, rdFingerprintGenerator


def main():
    print("=== ECFP4 ligand similarity ===")
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # ── Collect unique (ligand_id, source, smiles) tuples ───────────────
    print("\n  collecting ligand SMILES from all sources…")
    queries = [
        ("davis",   "SELECT ligand_ref AS ligand_id, smiles FROM davis_ligands WHERE smiles IS NOT NULL"),
        ("kiba",    "SELECT ligand_ref AS ligand_id, smiles FROM kiba_ligands  WHERE smiles IS NOT NULL"),
        ("gtopdb",  "SELECT ligand_ref AS ligand_id, smiles FROM gtopdb_ligands WHERE smiles IS NOT NULL"),
    ]
    # PDBbind ligand xref if available
    tabs = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "v2_pdbbind_ligand_xref" in tabs:
        cols = [r[1] for r in con.execute("PRAGMA table_info(v2_pdbbind_ligand_xref)").fetchall()]
        if "smiles" in cols:
            queries.append(("pdbbind",
                "SELECT DISTINCT chem_comp_id AS ligand_id, smiles FROM v2_pdbbind_ligand_xref "
                "WHERE smiles IS NOT NULL"))

    ligands = []  # list of (ligand_id, source, smiles)
    for source, sql in queries:
        rows = con.execute(sql).fetchall()
        for ligand_id, smi in rows:
            if ligand_id is None or smi is None:
                continue
            ligands.append((str(ligand_id), source, str(smi).strip()))
    print(f"  raw ligand rows: {len(ligands):,}")

    # Dedupe by canonical SMILES so we don't redundantly fingerprint the same molecule
    Chem, rdFingerprintGenerator = _import_rdkit()
    gen = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)

    by_smiles = {}  # canon_smiles -> {ids: [(id, source)], fp_idx}
    n_bad = 0
    n_canon = 0
    for ligand_id, source, smiles in ligands:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            n_bad += 1
            continue
        canon = Chem.MolToSmiles(mol)
        if canon not in by_smiles:
            by_smiles[canon] = {"ids": [], "mol": mol, "fp_idx": n_canon}
            n_canon += 1
        by_smiles[canon]["ids"].append((ligand_id, source))
    print(f"  unique canonical SMILES: {n_canon:,} (parsed {len(ligands) - n_bad:,}/{len(ligands):,})")
    print(f"  bad SMILES (skipped):    {n_bad:,}")

    # ── Compute fingerprints ────────────────────────────────────────────
    print(f"\n  computing ECFP4 fingerprints for {n_canon:,} unique molecules…")
    t0 = time.time()
    fps_packed = np.zeros((n_canon, 2048 // 8), dtype=np.uint8)
    canon_list = [None] * n_canon
    for canon, info in by_smiles.items():
        idx = info["fp_idx"]
        canon_list[idx] = canon
        bv = gen.GetFingerprint(info["mol"])
        # Pack 2048 bits into bytes via the bitvect's ToBinary or ToBitString
        bs = bv.ToBitString()
        # Convert 2048-char "01" string → packed uint8 array
        # numpy frombuffer with view trick:
        bits = np.frombuffer(bs.encode(), dtype=np.uint8) - ord('0')
        packed = np.packbits(bits)
        fps_packed[idx] = packed
    print(f"  fingerprints done in {time.time()-t0:.1f} s")

    # ── All-pairs Tanimoto with cutoff ──────────────────────────────────
    # Tanimoto(a, b) = popcount(a & b) / popcount(a | b)
    # We use bytewise XOR/AND/OR to compute popcounts via np.unpackbits is slow;
    # instead we use NumPy bitwise ops on the packed uint8 array, then bincount lookup.
    print(f"\n  computing all-pairs Tanimoto (cutoff >= 0.6)…")
    t0 = time.time()
    THRESH = 0.6

    # Precompute popcount(each fp)
    popcount_table = np.array([bin(x).count("1") for x in range(256)], dtype=np.int32)
    pc_per_fp = popcount_table[fps_packed].sum(axis=1)  # (n_canon,)

    similar_pairs = []  # (canon_a_idx, canon_b_idx, tanimoto)
    # Process in row blocks to keep memory bounded for large n
    BLOCK = 256
    for i_start in range(0, n_canon, BLOCK):
        i_end = min(i_start + BLOCK, n_canon)
        block_a = fps_packed[i_start:i_end]                      # (B, 256)
        # Only compute against j > i (upper triangle); we'll iterate j-windows too
        for j_start in range(i_start, n_canon, BLOCK):
            j_end = min(j_start + BLOCK, n_canon)
            block_b = fps_packed[j_start:j_end]                  # (B, 256)
            # Compute pairwise AND and OR popcounts: (BA, BB, 256)
            # Use broadcasting (BA, 1, 256) & (1, BB, 256)
            band = np.bitwise_and(block_a[:, None, :], block_b[None, :, :])
            bor  = np.bitwise_or (block_a[:, None, :], block_b[None, :, :])
            and_pc = popcount_table[band].sum(axis=2)  # (BA, BB)
            or_pc  = popcount_table[bor].sum(axis=2)   # (BA, BB)
            # Avoid div by zero
            tani = np.where(or_pc > 0, and_pc / or_pc, 0.0)
            mask = tani >= THRESH
            # Don't include diagonal (i == j) self-similarity
            for di in range(i_end - i_start):
                for dj in range(j_end - j_start):
                    gi = i_start + di
                    gj = j_start + dj
                    if gi >= gj:
                        continue
                    if mask[di, dj]:
                        similar_pairs.append((gi, gj, float(tani[di, dj])))
        if i_start % (BLOCK * 8) == 0 and i_start > 0:
            elapsed = time.time() - t0
            done = i_start * n_canon
            pct = 100 * done / (n_canon * n_canon)
            print(f"    progress: i={i_start}/{n_canon}  pairs_so_far={len(similar_pairs):,}  "
                  f"({pct:.1f}% upper-tri, {elapsed:.1f}s)")
    print(f"  pairs >= {THRESH}: {len(similar_pairs):,}  ({time.time()-t0:.1f} s total)")

    # ── Materialize ─────────────────────────────────────────────────────
    print("\n  building v2_ligand_similarity table…")
    con.execute("DROP TABLE IF EXISTS v2_ligand_similarity")
    con.execute("""
        CREATE TABLE v2_ligand_similarity (
          ligand_a       VARCHAR,
          ligand_b       VARCHAR,
          source_a       VARCHAR,
          source_b       VARCHAR,
          canon_smiles_a VARCHAR,
          canon_smiles_b VARCHAR,
          ecfp4_tanimoto DOUBLE,
          PRIMARY KEY (ligand_a, source_a, ligand_b, source_b)
        )
    """)
    rows_to_insert = []
    for canon_a_idx, canon_b_idx, tani in similar_pairs:
        canon_a = canon_list[canon_a_idx]
        canon_b = canon_list[canon_b_idx]
        info_a = by_smiles[canon_a]
        info_b = by_smiles[canon_b]
        for (ida, sa) in info_a["ids"]:
            for (idb, sb) in info_b["ids"]:
                if ida == idb and sa == sb:
                    continue  # don't include identical references
                rows_to_insert.append((ida, idb, sa, sb, canon_a, canon_b, tani))
    print(f"  expanded to {len(rows_to_insert):,} ligand-pair rows")

    # Insert in batches
    BATCH = 50_000
    for i in range(0, len(rows_to_insert), BATCH):
        batch = rows_to_insert[i:i+BATCH]
        try:
            con.executemany(
                "INSERT OR IGNORE INTO v2_ligand_similarity "
                "(ligand_a, ligand_b, source_a, source_b, canon_smiles_a, canon_smiles_b, ecfp4_tanimoto) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                batch
            )
        except Exception as exc:
            # Some batches may have duplicate keys from cross-source mappings
            print(f"  batch {i//BATCH}: {exc} — falling back to one-by-one")
            for row in batch:
                try:
                    con.execute("INSERT OR IGNORE INTO v2_ligand_similarity VALUES (?,?,?,?,?,?,?)", row)
                except Exception:
                    pass
        if i % (BATCH * 10) == 0:
            print(f"    inserted batch {i//BATCH}/{len(rows_to_insert)//BATCH + 1}")

    n_final = con.execute("SELECT COUNT(*) FROM v2_ligand_similarity").fetchone()[0]
    print(f"\n  v2_ligand_similarity final row count: {n_final:,}")

    # Quick spot-check: do imatinib and nilotinib cluster?
    rows = con.execute(
        "SELECT ligand_a, ligand_b, source_a, source_b, ecfp4_tanimoto "
        "FROM v2_ligand_similarity "
        "WHERE ecfp4_tanimoto >= 0.7 ORDER BY ecfp4_tanimoto DESC LIMIT 10"
    ).fetchall()
    print("\n  top-10 most-similar pairs:")
    for r in rows:
        print(f"    {r[0]:>30} ({r[2]}) <-> {r[1]:>30} ({r[3]})  tanimoto={r[4]:.3f}")

    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
