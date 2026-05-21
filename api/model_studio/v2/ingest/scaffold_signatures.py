"""Bemis-Murcko scaffold signatures for v2 ligands.

The Bemis-Murcko scaffold of a molecule is the union of its ring systems
plus the linkers between them, with all side-chains stripped. Two ligands
sharing a scaffold are "chemical analogues" — a leakage edge in DTI splits
that is *complementary* to Tanimoto: scaffold equivalence catches series
that share a core but differ in substituents (scaffold ≡, Tanimoto can be
quite low if R-groups are large).

Output (one parquet, registered as ``v2_ligand_scaffolds``):
    ligand_ref               "ligand:<source>:<id>"
    source                   "gtopdb" | "davis" | "kiba"
    canonical_smiles         original ligand canonical smiles
    scaffold_smiles          Bemis-Murcko scaffold SMILES (canonical)
    scaffold_id              short hash of scaffold_smiles (for joining)
    snapshot_id

Edges view (``v2_scaffold_edges``):
    Computed JIT from the membership table — same-scaffold ligand pairs.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


def _rdkit():
    from rdkit import Chem, RDLogger
    from rdkit.Chem.Scaffolds import MurckoScaffold
    RDLogger.DisableLog("rdApp.*")
    return Chem, MurckoScaffold


def _scaffold_for(smi: str) -> tuple[str, str] | None:
    """Returns (scaffold_smiles, scaffold_id) or None if RDKit can't parse."""
    Chem, MurckoScaffold = _rdkit()
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return None
    scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    # Acyclic molecules produce an empty scaffold ('' SMILES) — keep it
    # explicit as 'acyclic' so we don't conflate them.
    s = Chem.MolToSmiles(scaffold, canonical=True) if scaffold is not None else ""
    if not s:
        s = "acyclic"
    sid = hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]
    return s, sid


def build_scaffolds(snapshot_id: str | None = None,
                    fingerprint_snapshot: str | None = None) -> dict:
    """Read v2_ligand_fingerprints, compute Bemis-Murcko scaffolds."""
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    v2 = _v2()
    try:
        fp_view = _safe_view_name("v2", "ligand_fingerprints")
        rows_in = v2.execute(
            f"SELECT ligand_ref, source, canonical_smiles FROM {fp_view}"
        ).fetchall()
    finally:
        v2.close()
    if not rows_in:
        return {"error": "no v2_ligand_fingerprints view; run compute_fingerprints first"}

    rows: list[dict] = []
    failures = 0
    scaffold_counts: dict[str, int] = {}
    for lref, src, smi in rows_in:
        if not smi:
            continue
        sc = _scaffold_for(smi)
        if not sc:
            failures += 1
            continue
        ss, sid = sc
        rows.append({
            "ligand_ref":       lref,
            "source":           src,
            "canonical_smiles": smi,
            "scaffold_smiles":  ss,
            "scaffold_id":      sid,
            "snapshot_id":      snapshot_id,
        })
        scaffold_counts[sid] = scaffold_counts.get(sid, 0) + 1

    out_dir = (INGEST_ROOT / "normalized" / "similarity_signatures"
               / "v2_ligand_scaffolds" / snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _write_parquet(rows, out_dir / "scaffolds.parquet")

    view_name = _safe_view_name("v2", "ligand_scaffolds")
    if rows:
        v2 = _v2()
        try:
            v2.execute(f"DROP VIEW IF EXISTS {view_name}")
            v2.execute(f"DROP TABLE IF EXISTS {view_name}")
            v2.execute(
                f"CREATE VIEW {view_name} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            )
        finally:
            v2.close()

    # Summary: how many ligands per unique scaffold (long tail expected)
    multi_scaffold = sum(1 for c in scaffold_counts.values() if c >= 2)
    audit = {
        "snapshot_id": snapshot_id,
        "output_path": str(out_path),
        "n_ligands": len(rows),
        "n_parse_failures": failures,
        "n_unique_scaffolds": len(scaffold_counts),
        "n_scaffolds_shared_by_2plus": multi_scaffold,
        "view_name": view_name if rows else None,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def scaffold_edge_count() -> dict:
    """Count scaffold-shared ligand pairs without materialising them."""
    view = _safe_view_name("v2", "ligand_scaffolds")
    v2 = _v2()
    try:
        total = v2.execute(
            f"SELECT count(*) FROM {view} a JOIN {view} b "
            f"ON a.scaffold_id = b.scaffold_id AND a.ligand_ref < b.ligand_ref "
            f"WHERE a.scaffold_smiles <> 'acyclic'"
        ).fetchone()[0]
        cross = v2.execute(
            f"SELECT count(*) FROM {view} a JOIN {view} b "
            f"ON a.scaffold_id = b.scaffold_id AND a.ligand_ref < b.ligand_ref "
            f"AND a.source <> b.source "
            f"WHERE a.scaffold_smiles <> 'acyclic'"
        ).fetchone()[0]
    finally:
        v2.close()
    return {
        "total_scaffold_edges": int(total),
        "cross_source_scaffold_edges": int(cross),
    }


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    except Exception:
        jsonl = path.with_suffix(".jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return jsonl
