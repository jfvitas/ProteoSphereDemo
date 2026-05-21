"""ESM-2 protein embedding cache.

Provides mean-pool embeddings for the v2 protein universe using the
``fair-esm`` package. Outputs are persisted to disk + memoised in RAM
so repeated training runs don't re-embed.

Models exposed:
    esm2_t6_8M_UR50D       8M params,    320-dim, ~30 MB weights
    esm2_t12_35M_UR50D     35M params,   480-dim, ~140 MB weights
    esm2_t30_150M_UR50D   150M params,   640-dim, ~600 MB weights

The 8M variant is the default — small enough to keep the warehouse
embedding cache under 100 MB for ~57K v2 UniProts while still capturing
useful evolutionary signal.

Cache layout:
    E:\\ProteoSphere\\reference_library_v2\\normalized\\embeddings\\esm2_t6_8M_UR50D\\
        embeddings.parquet   uniprot, sequence_md5, embedding (float32[D])
        manifest.json
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import duckdb
import numpy as np

from .ingest.state import INGEST_ROOT
from .ingest.catalog import _CATALOG_PATH, _safe_view_name


_MODEL_DIMS: dict[str, int] = {
    "esm2_t6_8M_UR50D":   320,
    "esm2_t12_35M_UR50D": 480,
    "esm2_t30_150M_UR50D": 640,
}

_loaded_model = None
_loaded_model_name: str | None = None
_loaded_batch_converter = None


def _load_esm(model_name: str):
    """Load fair-esm model + tokenizer; cache the result in process."""
    global _loaded_model, _loaded_model_name, _loaded_batch_converter
    if _loaded_model is not None and _loaded_model_name == model_name:
        return _loaded_model, _loaded_batch_converter
    import esm  # type: ignore
    factory = getattr(esm.pretrained, model_name, None)
    if factory is None:
        raise ValueError(f"Unknown ESM-2 model '{model_name}'. "
                         f"Choices: {sorted(_MODEL_DIMS)}")
    model, alphabet = factory()
    model.eval()
    _loaded_model = model
    _loaded_model_name = model_name
    _loaded_batch_converter = alphabet.get_batch_converter()
    return model, _loaded_batch_converter


def embed_sequences(
    sequences: list[tuple[str, str]],
    *,
    model_name: str = "esm2_t6_8M_UR50D",
    device: str = "auto",
    batch_size: int = 16,
    max_len: int = 1024,
) -> np.ndarray:
    """Compute mean-pool embeddings for a list of (id, sequence) tuples.

    Returns an (N, D) numpy float32 array in the same order as the input.
    """
    import torch
    from .gpu_runtime import select_device
    dev = select_device(device)
    model, batch_converter = _load_esm(model_name)
    model = model.to(dev)
    dim = _MODEL_DIMS[model_name]

    out = np.zeros((len(sequences), dim), dtype=np.float32)
    last_layer = sum(1 for _ in model.layers) if hasattr(model, "layers") else (
        len(model.encoder.layers) if hasattr(model, "encoder") else 6
    )

    with torch.no_grad():
        for start in range(0, len(sequences), batch_size):
            batch = sequences[start:start + batch_size]
            # Truncate AA sequences to max_len for memory safety
            batch_trunc = [(name, seq[:max_len]) for name, seq in batch]
            _, _, tokens = batch_converter(batch_trunc)
            tokens = tokens.to(dev)
            results = model(tokens, repr_layers=[last_layer], return_contacts=False)
            reps = results["representations"][last_layer]   # (B, L, D)
            # Mean-pool over non-pad positions. fair-esm uses 1 (cls) and 2 (eos)
            # as special tokens; mask those out too.
            attn_mask = (tokens != 1) & (tokens != 2) & (tokens != 0)
            attn_mask = attn_mask.unsqueeze(-1).float()
            pooled = (reps * attn_mask).sum(dim=1) / attn_mask.sum(dim=1).clamp(min=1)
            out[start:start + len(batch)] = pooled.cpu().numpy().astype(np.float32)
    return out


def build_v2_embedding_cache(
    *,
    model_name: str = "esm2_t6_8M_UR50D",
    snapshot_id: str | None = None,
    batch_size: int = 16,
    max_len: int = 1024,
    limit: int | None = None,
    resume: bool = True,
) -> dict:
    """Embed every UniProt in ``v2_protein_sequences`` with ESM-2.

    Writes results to ``normalized/embeddings/<model>/<snapshot>/`` and
    registers the v2 catalog view ``v2_protein_embeddings_<model>``.
    """
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out_dir = (INGEST_ROOT / "normalized" / "embeddings" / model_name / snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "embeddings.parquet"

    # Load (uniprot, sequence, md5) from the catalog
    con = duckdb.connect(str(_CATALOG_PATH), read_only=True)
    try:
        rows = con.execute(
            "SELECT uniprot, sequence, sequence_md5, sequence_length "
            "FROM v2_protein_sequences WHERE sequence IS NOT NULL "
            "ORDER BY uniprot"
        ).fetchall()
    finally:
        con.close()
    if limit:
        rows = rows[:limit]
    if not rows:
        return {"error": "no v2_protein_sequences rows; materialise sequences first"}

    # Resume: load any already-cached embeddings from a previous run
    cached_md5: dict[str, np.ndarray] = {}
    if resume and out_path.exists():
        try:
            con = duckdb.connect(":memory:")
            try:
                pa_path = str(out_path).replace("\\", "/")
                cached = con.execute(
                    f"SELECT uniprot, sequence_md5, embedding "
                    f"FROM read_parquet('{pa_path}')"
                ).fetchall()
            finally:
                con.close()
            for u, md5, vec in cached:
                cached_md5[md5] = np.array(vec, dtype=np.float32)
        except Exception:
            cached_md5 = {}

    dim = _MODEL_DIMS[model_name]
    out_rows: list[dict] = []
    to_embed: list[tuple[str, str, str]] = []  # (uniprot, sequence, md5)
    stats = {
        "total":         len(rows),
        "cached_reused": 0,
        "embedded":      0,
        "skipped":       0,
    }
    for u, seq, md5, slen in rows:
        if not seq:
            stats["skipped"] += 1
            continue
        if md5 in cached_md5:
            out_rows.append({"uniprot": u, "sequence_md5": md5, "embedding": cached_md5[md5].tolist()})
            stats["cached_reused"] += 1
            continue
        to_embed.append((u, seq, md5))

    if to_embed:
        seqs = [(u, s) for u, s, _md5 in to_embed]
        vectors = embed_sequences(
            seqs, model_name=model_name,
            batch_size=batch_size, max_len=max_len,
        )
        for (u, _seq, md5), vec in zip(to_embed, vectors):
            out_rows.append({"uniprot": u, "sequence_md5": md5, "embedding": vec.tolist()})
            stats["embedded"] += 1
            cached_md5[md5] = vec

    _write_parquet(out_rows, out_path)

    view_name = _safe_view_name("v2", f"protein_embeddings_{model_name.replace('-', '_')}")
    con = duckdb.connect(str(_CATALOG_PATH))
    try:
        con.execute(f"DROP VIEW IF EXISTS {view_name}")
        con.execute(f"DROP TABLE IF EXISTS {view_name}")
        con.execute(
            f"CREATE VIEW {view_name} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
        )
    finally:
        con.close()

    audit = {
        "snapshot_id": snapshot_id,
        "model_name":  model_name,
        "embed_dim":   dim,
        "output_path": str(out_path),
        "view_name":   view_name,
        "n_embeddings": len(out_rows),
        "stats":        stats,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        # Convert embedding lists to fixed-size float arrays so DuckDB
        # treats them as proper vectors.
        cols = {
            "uniprot":      [r["uniprot"] for r in rows],
            "sequence_md5": [r["sequence_md5"] for r in rows],
            "embedding":    [r["embedding"] for r in rows],
        }
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    except Exception:
        jsonl = path.with_suffix(".jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return jsonl
