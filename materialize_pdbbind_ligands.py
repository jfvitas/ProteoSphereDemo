"""Normalize PDBbind ligand_ref via RCSB Ligand Expo.

Source: components.cif.gz from wwPDB (CC0).

Cleans pdbbind_interactions.ligand_ref free-text contamination, resolves
3-letter PDB chem-comp IDs to canonical SMILES, then materializes
``v2_pdbbind_ligand_xref`` so the chem joins work end-to-end. Then
recomputes Bemis-Murcko scaffolds across the union of Davis + KIBA +
GtoPdb + PDBbind cleaned ligands.

Re-runnable.
"""
from __future__ import annotations

import gzip
import hashlib
import re
import sys
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = Path("D:/documents/ProteoSphereV2/cache/ligandexpo")
COMPONENTS_CIF = CACHE / "components.cif.gz"
SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

# Free-text contamination patterns to strip
JUNK_RE = re.compile(
    r"(?i)^(unknown|none|by itc|n-mer|\d+-mer|nan|n/a|null|substrate.*|.*kinasefiltration.*)\s*$"
)


def parse_components_cif(path: Path) -> dict[str, str]:
    """Parse mmCIF components dictionary to extract chem_comp.id -> SMILES.

    The CCD ships per-component data items with _chem_comp.id and
    _pdbx_chem_comp_descriptor entries. We pull the SMILES with
    type=SMILES_CANONICAL preferring OpenEye, falling back to CACTVS.
    """
    smiles: dict[str, str] = {}
    cur_id: str | None = None
    cur_smiles: str | None = None
    cur_pref: int = 99  # lower is better
    in_descriptor_loop = False
    loop_columns: list[str] = []

    def commit():
        nonlocal cur_id, cur_smiles, cur_pref
        if cur_id and cur_smiles:
            smiles[cur_id] = cur_smiles
        cur_id = None
        cur_smiles = None
        cur_pref = 99

    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.rstrip("\n")
            if s.startswith("data_"):
                commit()
                cur_id = s[5:].strip()
                in_descriptor_loop = False
                loop_columns = []
                continue
            if s.startswith("_chem_comp.id"):
                # may be inline value
                parts = s.split(None, 1)
                if len(parts) == 2 and cur_id is None:
                    cur_id = parts[1].strip().strip("'\"")
                continue
            if s.strip() == "loop_":
                in_descriptor_loop = False
                loop_columns = []
                continue
            if s.startswith("_pdbx_chem_comp_descriptor."):
                loop_columns.append(s.strip())
                if "_pdbx_chem_comp_descriptor.descriptor" in loop_columns and \
                   "_pdbx_chem_comp_descriptor.type" in loop_columns:
                    in_descriptor_loop = True
                continue
            if in_descriptor_loop:
                if s.startswith("#") or s.startswith("_") or s.strip() == "loop_":
                    in_descriptor_loop = False
                    loop_columns = []
                    continue
                # Parse a descriptor row
                # Format: comp_id type program program_version descriptor
                # Tokens may be quoted; the descriptor is last and may contain spaces in quotes
                tokens = _tokenize_cif_row(s)
                if len(tokens) >= 5:
                    comp_id = tokens[0]
                    desc_type = tokens[1]
                    program = tokens[2]
                    descriptor = tokens[-1]
                    if desc_type in ("SMILES_CANONICAL", "SMILES"):
                        # prefer OpenEye over CACTVS over RDKit
                        pref = {"OpenEye": 0, "CACTVS": 1, "RDKit": 2}.get(program, 3)
                        if desc_type == "SMILES":
                            pref += 10  # canonical preferred
                        if comp_id == cur_id and pref < cur_pref:
                            cur_smiles = descriptor
                            cur_pref = pref
                        elif comp_id != cur_id and comp_id not in smiles and descriptor:
                            # row references a different comp; record directly
                            smiles.setdefault(comp_id, descriptor)
    commit()
    return smiles


def _tokenize_cif_row(s: str) -> list[str]:
    """Tokenize a CIF data row honoring single/double quotes."""
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch in ("'", '"'):
            end = s.find(ch, i + 1)
            if end < 0:
                out.append(s[i + 1 :])
                break
            out.append(s[i + 1 : end])
            i = end + 1
        else:
            j = i
            while j < len(s) and not s[j].isspace():
                j += 1
            out.append(s[i:j])
            i = j
    return out


def murcko_scaffold(smi: str) -> str | None:
    try:
        from rdkit import Chem
        from rdkit.Chem.Scaffolds import MurckoScaffold
    except ImportError:
        return None
    try:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            return None
        scaffold = MurckoScaffold.GetScaffoldForMol(mol)
        if scaffold is None:
            return None
        return Chem.MolToSmiles(scaffold, canonical=True)
    except Exception:
        return None


def main() -> None:
    if not WAREHOUSE.exists():
        raise SystemExit(f"warehouse missing: {WAREHOUSE}")
    if not COMPONENTS_CIF.exists():
        raise SystemExit(f"components.cif.gz missing at {COMPONENTS_CIF}; fetch first")

    con = duckdb.connect(str(WAREHOUSE))

    # Quick parse of components.cif.gz
    print(f"[ligand] parsing {COMPONENTS_CIF}")
    t0 = time.time()
    chem_smiles = parse_components_cif(COMPONENTS_CIF)
    print(f"[ligand] parsed {len(chem_smiles):,} chem-comp SMILES in {time.time()-t0:.1f}s")

    # Pull unique ligand_refs from pdbbind, plus the per-row pdb_id
    rows = con.execute(
        "SELECT DISTINCT pdb_id, ligand_ref FROM pdbbind_interactions"
    ).fetchall()
    print(f"[ligand] {len(rows):,} distinct (pdb_id, ligand_ref) pairs in pdbbind")

    cleaned = []
    junk_skipped = 0
    for pdb_id, ligand_ref in rows:
        if not ligand_ref:
            junk_skipped += 1
            continue
        lr = str(ligand_ref).strip()
        if JUNK_RE.match(lr):
            junk_skipped += 1
            continue
        # extract candidate chem-comp id: usually first token, often 3 letters
        cand = re.split(r"[\s,;()/]+", lr)[0].upper()
        # accept 1-5 char alphanumeric token (PDB chem comp ids can be e.g. "1A", "ATP", "0G1")
        if not re.fullmatch(r"[A-Z0-9]{1,5}", cand):
            junk_skipped += 1
            continue
        smi = chem_smiles.get(cand)
        if not smi:
            junk_skipped += 1
            continue
        scaffold = murcko_scaffold(smi)
        scaffold_id = hashlib.sha1(scaffold.encode()).hexdigest() if scaffold else None
        cleaned.append((pdb_id.lower(), cand, smi, scaffold, scaffold_id))

    print(f"[ligand] {len(cleaned):,} resolved, {junk_skipped:,} skipped (junk/unresolved)")

    con.execute("DROP TABLE IF EXISTS v2_pdbbind_ligand_xref")
    con.execute("""
        CREATE TABLE v2_pdbbind_ligand_xref (
            pdb_id           VARCHAR,
            chem_comp_id     VARCHAR,
            smiles           VARCHAR,
            normalized_smiles VARCHAR,
            scaffold_id      VARCHAR,
            snapshot_id      VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO v2_pdbbind_ligand_xref VALUES (?,?,?,?,?,?)",
        [(p, c, s, sc, sid, SNAPSHOT_ID) for (p, c, s, sc, sid) in cleaned],
    )
    print(f"[ligand] v2_pdbbind_ligand_xref: {len(cleaned):,} rows")

    # Now extend v2_scaffold_membership to include pdbbind ligands
    # Build new pdbbind scaffold rows keyed by ligand_ref = "ligand:pdbbind:<chem_comp_id>"
    distinct_lig = {}
    for (pdb_id, cand, smi, scaffold, scaffold_id) in cleaned:
        if cand not in distinct_lig and scaffold_id:
            distinct_lig[cand] = (smi, scaffold, scaffold_id)
    print(f"[ligand] {len(distinct_lig):,} unique chem comp IDs with scaffolds for v2_scaffold_membership")

    # Existing v2_scaffold_membership schema (preserve)
    cols = con.execute("DESCRIBE v2_scaffold_membership").fetchdf()
    print(f"[ligand] existing v2_scaffold_membership cols: {list(cols['column_name'])}")

    # Append new rows tagged source='pdbbind'
    # Schema is: ligand_ref, ligand_smiles, scaffold_smiles, scaffold_id, source, snapshot_id
    existing_count = con.execute("select count(*) from v2_scaffold_membership").fetchone()[0]
    con.executemany(
        "INSERT INTO v2_scaffold_membership (ligand_ref, source, canonical_smiles, scaffold_smiles, scaffold_id, snapshot_id) VALUES (?,?,?,?,?,?)",
        [(f"ligand:pdbbind:{cand}", "pdbbind", smi, sc, sid, SNAPSHOT_ID)
         for cand, (smi, sc, sid) in distinct_lig.items()],
    )
    after_count = con.execute("select count(*) from v2_scaffold_membership").fetchone()[0]
    print(f"[ligand] v2_scaffold_membership: {existing_count:,} -> {after_count:,} (+{after_count-existing_count:,})")

    # Now check PDBbind ligand <-> scaffold overlap closure
    overlap = con.execute("""
        SELECT COUNT(DISTINCT p.ligand_ref)
        FROM pdbbind_interactions p
        JOIN v2_pdbbind_ligand_xref x ON x.pdb_id = lower(p.pdb_id)
        JOIN v2_scaffold_membership s
          ON s.ligand_ref = 'ligand:pdbbind:' || x.chem_comp_id
    """).fetchone()[0]
    print(f"[ligand] PDBbind ligand_refs now reachable via scaffolds: {overlap:,}")

    con.close()


if __name__ == "__main__":
    main()
