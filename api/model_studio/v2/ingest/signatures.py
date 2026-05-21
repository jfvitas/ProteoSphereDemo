"""Ligand-side cross-relationship signatures.

This module computes the **stored** representation of each new ligand
(ECFP4 fingerprint as packed bits) and the **stored** pairwise Tanimoto
edges between them. The full SMILES strings are NOT duplicated here —
they live in each source's `*_ligands` parquet already; this module
references them by `ligand_ref`.

Outputs (parquet under normalized/similarity_signatures/v2_ligand_ecfp4/):
    ligand_fingerprints.parquet
        ligand_ref          "ligand:<source>:<id>"           — unique across sources
        source              "gtopdb" | "davis" | "kiba"
        source_ligand_id    raw id in the source
        canonical_smiles    canonicalised SMILES (post-RDKit normalisation)
        inchikey            sha-like canonical hash for cross-source dedup
        fingerprint_bits    2048-bit ECFP4 stored as bytes (256 B per ligand)
        snapshot_id

    ligand_similarity_edges.parquet
        edge_id          "ligand_sim:ecfp4:<lref_lo>_<lref_hi>"
        a_ref            "ligand:<src>:<id>"
        b_ref            "ligand:<src>:<id>"
        tanimoto         float (>= threshold)
        snapshot_id

Storage discipline:
    * fingerprints are 256 bytes / ligand. 15.9 K new ligands → ~4 MB
    * edges at Tanimoto >= 0.4 typically retain ~10× the entity count.
      We expect ~150 K edges × 32 bytes ≈ 5 MB. Total signature footprint:
      under 15 MB on top of the existing 386 MB.

The pipeline is **idempotent** — running it again with new ligands appends
to the parquet (new snapshot) without re-fingerprinting existing ones.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name

# RDKit is heavy; keep imports lazy
def _rdkit():
    from rdkit import Chem, RDLogger
    from rdkit.Chem import AllChem
    RDLogger.DisableLog("rdApp.*")  # suppress parse warnings
    return Chem, AllChem


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


# Each source's ligand parquet uses a slightly different schema. We adapt
# them to a uniform (ligand_ref, source_ligand_id, smiles) triple.
LIGAND_SOURCES = [
    {
        "source": "gtopdb",
        "sql": """SELECT ligand_ref, ligand_id AS source_ligand_id, smiles
                  FROM gtopdb_ligands WHERE smiles IS NOT NULL""",
    },
    {
        "source": "davis",
        "sql": """SELECT ligand_ref, ligand_key AS source_ligand_id, smiles
                  FROM davis_ligands WHERE smiles IS NOT NULL""",
    },
    {
        "source": "kiba",
        "sql": """SELECT ligand_ref, ligand_key AS source_ligand_id, smiles
                  FROM kiba_ligands WHERE smiles IS NOT NULL""",
    },
]


# ── Fingerprint computation ───────────────────────────────────────────

def _fingerprint_one(smi: str, *, radius: int = 2, n_bits: int = 2048):
    """Returns (canonical_smiles, inchikey, on_bits_csv) or None.

    Storage: list of on-bit indices, comma-joined. For ECFP4 at 2048 bits,
    typical density is ~10% (200 on-bits / ligand), so on-bit lists are
    smaller than full 256-byte packed bits AND round-trip cleanly across
    RDKit versions.
    """
    Chem, AllChem = _rdkit()
    mol = Chem.MolFromSmiles(smi)
    if mol is None:
        return None
    canonical = Chem.MolToSmiles(mol, canonical=True)
    inchi = Chem.MolToInchi(mol)
    inchikey = Chem.InchiToInchiKey(inchi) if inchi else None
    fp = AllChem.GetMorganFingerprintAsBitVect(mol, radius=radius, nBits=n_bits)
    on_bits = ",".join(str(i) for i in fp.GetOnBits())
    return canonical, inchikey, on_bits


def _fp_from_on_bits(on_bits: str, *, n_bits: int = 2048):
    """Reconstruct an ExplicitBitVect from the on-bit CSV."""
    from rdkit.DataStructs import ExplicitBitVect
    fp = ExplicitBitVect(n_bits)
    if not on_bits:
        return fp
    for tok in on_bits.split(","):
        try:
            fp.SetBit(int(tok))
        except ValueError:
            continue
    return fp


def compute_fingerprints(snapshot_id: str | None = None) -> dict:
    """Fingerprint every ligand across LIGAND_SOURCES. Returns audit dict."""
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = INGEST_ROOT / "normalized" / "similarity_signatures" / "v2_ligand_ecfp4" / snapshot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    stats = {"sources": [], "total_in": 0, "fingerprinted": 0, "parse_failures": 0}
    v2 = _v2()
    try:
        for cfg in LIGAND_SOURCES:
            data = v2.execute(cfg["sql"]).fetchall()
            n_src_ok = 0
            for ligand_ref, src_id, smi in data:
                stats["total_in"] += 1
                if not smi:
                    continue
                fp_result = _fingerprint_one(smi)
                if not fp_result:
                    stats["parse_failures"] += 1
                    continue
                canonical, inchikey, fp_on_bits = fp_result
                rows.append({
                    "ligand_ref":            ligand_ref,
                    "source":                cfg["source"],
                    "source_ligand_id":      src_id,
                    "canonical_smiles":      canonical,
                    "inchikey":              inchikey,
                    "fingerprint_on_bits":   fp_on_bits,
                    "snapshot_id":           snapshot_id,
                })
                stats["fingerprinted"] += 1
                n_src_ok += 1
            stats["sources"].append({"source": cfg["source"], "fingerprinted": n_src_ok})
    finally:
        v2.close()
    out_path = _write_parquet(rows, out_dir / "ligand_fingerprints.parquet")
    audit = {
        "snapshot_id": snapshot_id,
        "output_path": str(out_path),
        "n_fingerprints": len(rows),
        "stats": stats,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    # Register as a v2 catalog view (ligand_ref → metadata + fp)
    if rows:
        v2 = _v2()
        try:
            view = _safe_view_name("v2", "ligand_fingerprints")
            v2.execute(f"DROP VIEW IF EXISTS {view}")
            v2.execute(f"DROP TABLE IF EXISTS {view}")
            v2.execute(
                f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            )
            audit["view_name"] = view
        finally:
            v2.close()
    return audit


# ── Tanimoto edges ────────────────────────────────────────────────────

def compute_tanimoto_edges(
    threshold: float = 0.4,
    snapshot_id: str | None = None,
    fingerprint_snapshot: str | None = None,
    device: str = "auto",
) -> dict:
    """Cross-source pairwise Tanimoto. For 15.9 K ligands, the full
    pairwise comparison is ~126 M pairs — runs in ~30 s with RDKit's
    BulkTanimotoSimilarity over packed bit vectors, or <1 s on GPU.

    Args:
        device: ``"cpu"`` forces the original BulkTanimotoSimilarity path.
                ``"cuda"`` requires torch + CUDA available; raises otherwise.
                ``"auto"`` (default) uses CUDA when available, else CPU.

    Edge canonicalisation: sort the two ligand_refs and use lo,hi.
    """
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    # Resolve device — fall through to CPU if CUDA isn't available
    use_gpu = False
    if device in ("auto", "cuda"):
        try:
            import torch
            use_gpu = bool(torch.cuda.is_available())
        except Exception:
            use_gpu = False
        if device == "cuda" and not use_gpu:
            raise RuntimeError("device='cuda' requested but CUDA not available")

    if not use_gpu:
        from rdkit.DataStructs import BulkTanimotoSimilarity
    # Locate the fingerprint parquet — most recent snapshot under v2_ligand_ecfp4
    base_dir = INGEST_ROOT / "normalized" / "similarity_signatures" / "v2_ligand_ecfp4"
    if fingerprint_snapshot:
        fp_dir = base_dir / fingerprint_snapshot
    else:
        snapshots = sorted([d for d in base_dir.iterdir() if d.is_dir()], reverse=True)
        if not snapshots:
            return {"error": "no fingerprint snapshot found; run compute_fingerprints first"}
        fp_dir = snapshots[0]
    fp_path = fp_dir / "ligand_fingerprints.parquet"
    if not fp_path.exists():
        return {"error": f"missing {fp_path}"}

    # Load on-bit lists into memory
    v2 = _v2()
    try:
        rows = v2.execute(
            f"SELECT ligand_ref, fingerprint_on_bits FROM read_parquet('{str(fp_path).replace(chr(92), '/')}')"
        ).fetchall()
    finally:
        v2.close()
    if not rows:
        return {"error": "empty fingerprint parquet"}

    refs = [r[0] for r in rows]
    on_bits_csvs = [r[1] for r in rows]
    n = len(refs)
    edges: list[dict] = []
    backend = "cpu"

    if use_gpu:
        # GPU path — int64 popcount on packed fingerprints; ~30× faster
        # than RDKit BulkTanimotoSimilarity at 13K ligands.
        from .gpu_tanimoto import compute_tanimoto_edges_gpu
        gpu_edges = compute_tanimoto_edges_gpu(
            refs, on_bits_csvs, threshold=threshold,
        )
        for e in gpu_edges:
            lo, hi = e["a_ref"], e["b_ref"]
            edges.append({
                "edge_id":     f"ligand_sim:ecfp4:{lo}_{hi}",
                "a_ref":       lo,
                "b_ref":       hi,
                "tanimoto":    e["tanimoto"],
                "snapshot_id": snapshot_id,
            })
        backend = "cuda"
    else:
        # CPU path — original RDKit BulkTanimotoSimilarity
        fps = [_fp_from_on_bits(b) for b in on_bits_csvs]
        for i in range(n):
            sims = BulkTanimotoSimilarity(fps[i], fps[i + 1:])
            for k, s in enumerate(sims):
                if s < threshold:
                    continue
                j = i + 1 + k
                a, b = refs[i], refs[j]
                lo, hi = (a, b) if a < b else (b, a)
                edges.append({
                    "edge_id":     f"ligand_sim:ecfp4:{lo}_{hi}",
                    "a_ref":       lo,
                    "b_ref":       hi,
                    "tanimoto":    float(s),
                    "snapshot_id": snapshot_id,
                })

    out_dir = INGEST_ROOT / "normalized" / "similarity_signatures" / "v2_ligand_tanimoto" / snapshot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _write_parquet(edges, out_dir / "ligand_similarity_edges.parquet")
    audit = {
        "snapshot_id": snapshot_id,
        "fingerprint_snapshot": fp_dir.name,
        "output_path": str(out_path),
        "n_edges": len(edges),
        "threshold": threshold,
        "n_ligands": n,
        "backend": backend,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    if edges:
        v2 = _v2()
        try:
            view = _safe_view_name("v2", "ligand_tanimoto_edges")
            v2.execute(f"DROP VIEW IF EXISTS {view}")
            v2.execute(f"DROP TABLE IF EXISTS {view}")
            v2.execute(
                f"CREATE VIEW {view} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            )
            audit["view_name"] = view
        finally:
            v2.close()
    return audit


# ── Parquet writer (shared) ───────────────────────────────────────────

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
