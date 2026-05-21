"""Cached protein embeddings — the loader path for `in.protein_emb` blocks.

Architecture:

    On-disk cache at ~/.proteosphere_v2/embeddings/<checkpoint>/<UniProt>.npy

    Each file is a 1-D float32 numpy array (e.g. 1280-d for esm2_t33_650M).

Three access paths:

    1. **Manual / pre-computed**: a teammate runs an external embed pass
       (with whatever resources they have) and drops the resulting .npy
       files into the cache directory. The flow builder's `in.protein_emb`
       block then reads them at training time.
    2. **Auto-compute on first use**: when `fair-esm` is installed and the
       user picks `in.protein_emb`, this module will compute the
       embedding lazily and cache it on disk. Subsequent runs reuse the
       cache.
    3. **Fallback to zeros + warning**: when neither the cache nor an
       ESM-2 install is available, return zeros so training proceeds.
       The trainer logs a warning so the user understands the input
       block is producing dummy data.

We deliberately keep this module light — the heavy ESM-2 model import
only happens inside the auto-compute path, so the rest of the v2 stack
doesn't pay the import cost.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional

import numpy as np


# Embedding dims per common ESM-2 checkpoint
_ESM2_DIMS = {
    "esm2_t6_8M":     320,
    "esm2_t12_35M":   480,
    "esm2_t30_150M":  640,
    "esm2_t33_650M":  1280,
    "esm2_t36_3B":    2560,
    "esm2_t48_15B":   5120,
}


def cache_root(checkpoint: str = "esm2_t33_650M") -> Path:
    """Return the on-disk cache directory for a given checkpoint."""
    home = os.environ.get("PROTEOSPHERE_CACHE_HOME") or os.path.expanduser("~/.proteosphere_v2")
    return Path(home) / "embeddings" / checkpoint


def _safe_uniprot(s: str) -> str:
    """Sanitize a UniProt accession for use as a filename. UniProt
    accessions are already alphanumeric but Davis stores kinase symbols
    like ``ABL1(F317I)p`` — strip every non-alphanumeric / non-underscore
    character so the cache filenames are filesystem-safe."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", s)


def load_cached(uniprot: str, checkpoint: str = "esm2_t33_650M") -> Optional[np.ndarray]:
    """Return the cached embedding for a UniProt or None if absent."""
    p = cache_root(checkpoint) / f"{_safe_uniprot(uniprot)}.npy"
    if not p.is_file():
        return None
    try:
        return np.load(p)
    except Exception:
        return None


def save_cached(uniprot: str, embedding: np.ndarray,
                checkpoint: str = "esm2_t33_650M") -> None:
    """Write an embedding to the cache. Parent dir is created lazily."""
    root = cache_root(checkpoint)
    root.mkdir(parents=True, exist_ok=True)
    p = root / f"{_safe_uniprot(uniprot)}.npy"
    np.save(p, embedding.astype(np.float32))


def _try_load_esm(checkpoint: str):
    """Lazily import fair-esm. Returns (model, alphabet) or None.

    The first call downloads ~2.5 GB for the 650M checkpoint to PyTorch
    Hub's cache. Subsequent calls reuse the in-memory model.
    """
    if not hasattr(_try_load_esm, "_cached"):
        _try_load_esm._cached = {}
    if checkpoint in _try_load_esm._cached:
        return _try_load_esm._cached[checkpoint]
    try:
        import torch
        import esm  # fair-esm
    except Exception as exc:
        print(f"[embeddings] fair-esm not installed ({exc}); "
              f"auto-compute disabled. Install with `pip install fair-esm` "
              f"to enable lazy embedding computation, or drop pre-computed "
              f".npy files into {cache_root(checkpoint)}/.", flush=True)
        _try_load_esm._cached[checkpoint] = None
        return None
    # fair-esm exposes the 650M checkpoint as ``esm2_t33_650M_UR50D``
    # (and similar `_UR50D` suffixes for the other sizes). Our cache key
    # stays the shorter ``esm2_t33_650M`` for filesystem brevity, so we
    # map the canonical names to fair-esm's attribute names here.
    _FAIR_ESM_NAMES = {
        "esm2_t6_8M":    "esm2_t6_8M_UR50D",
        "esm2_t12_35M":  "esm2_t12_35M_UR50D",
        "esm2_t30_150M": "esm2_t30_150M_UR50D",
        "esm2_t33_650M": "esm2_t33_650M_UR50D",
        "esm2_t36_3B":   "esm2_t36_3B_UR50D",
        "esm2_t48_15B":  "esm2_t48_15B_UR50D",
    }
    attr_name = _FAIR_ESM_NAMES.get(checkpoint, checkpoint)
    fn = getattr(esm.pretrained, attr_name, None)
    if fn is None:
        # Fall back to the literal checkpoint name in case the user
        # passed an already-suffixed name directly.
        fn = getattr(esm.pretrained, checkpoint, None)
    if fn is None:
        print(f"[embeddings] unknown ESM-2 checkpoint '{checkpoint}' "
              f"(also tried '{attr_name}').", flush=True)
        _try_load_esm._cached[checkpoint] = None
        return None
    model, alphabet = fn()
    model.eval()
    # Robust CUDA detection — see gpu_runtime.gpu_available for context.
    _cuda_ok = bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
    device = torch.device("cuda" if _cuda_ok else "cpu")
    model = model.to(device)
    _try_load_esm._cached[checkpoint] = (model, alphabet, device)
    return _try_load_esm._cached[checkpoint]


def compute_one(uniprot: str, sequence: str,
                checkpoint: str = "esm2_t33_650M",
                pool: str = "mean") -> Optional[np.ndarray]:
    """Compute one ESM-2 embedding via fair-esm. Returns None on failure.

    ``pool`` ∈ {"mean", "cls"}. Mean is the standard choice for downstream
    DTA/PPI tasks; cls uses the BOS token's embedding only.
    """
    loaded = _try_load_esm(checkpoint)
    if loaded is None:
        return None
    model, alphabet, device = loaded
    import torch
    batch_converter = alphabet.get_batch_converter()
    # ESM-2 has a max-length cap around 1024–2048; truncate long proteins.
    seq = sequence[:1024] if len(sequence) > 1024 else sequence
    _, _, tokens = batch_converter([(uniprot, seq)])
    tokens = tokens.to(device)
    layer = getattr(model, "num_layers", 33)
    with torch.no_grad():
        out = model(tokens, repr_layers=[layer], return_contacts=False)
        reps = out["representations"][layer][0]   # (L+2, D) — BOS/EOS pad on edges
    if pool == "cls":
        emb = reps[0]
    else:
        # Skip BOS (idx 0) and EOS (last); mean over residues
        emb = reps[1:-1].mean(dim=0) if reps.shape[0] > 2 else reps.mean(dim=0)
    return emb.cpu().numpy().astype(np.float32)


def get_or_compute(uniprot: str, sequence: str,
                   checkpoint: str = "esm2_t33_650M",
                   auto_compute: bool = True) -> Optional[np.ndarray]:
    """Return the embedding for one UniProt — from cache or by computing it."""
    cached = load_cached(uniprot, checkpoint)
    if cached is not None:
        return cached
    if not auto_compute:
        return None
    emb = compute_one(uniprot, sequence, checkpoint=checkpoint)
    if emb is not None:
        save_cached(uniprot, emb, checkpoint=checkpoint)
    return emb


def batch_get_or_compute(records: Iterable,
                         checkpoint: str = "esm2_t33_650M",
                         auto_compute: bool = True) -> tuple[np.ndarray, dict]:
    """Vectorised version. Returns ``(embeddings_array, meta_dict)``.

    embeddings_array has shape ``(N, dim)`` (dim from _ESM2_DIMS); rows for
    proteins that couldn't be embedded are zeros.

    meta_dict contains:
        cache_hits:   number of records served from the disk cache
        cache_misses: number of records that needed computation (or
                      returned zeros when auto_compute=False)
        computed:     number of records embedded on this call (0 when
                      fair-esm isn't installed)
        zeros:        number of records that ended up as zero vectors
    """
    records = list(records)
    dim = _ESM2_DIMS.get(checkpoint, 1280)
    out = np.zeros((len(records), dim), dtype=np.float32)
    meta = {"cache_hits": 0, "cache_misses": 0, "computed": 0, "zeros": 0}
    # Dedupe by uniprot so we don't compute the same one multiple times
    # per epoch (a single record list usually has many duplicates).
    seen: dict[str, np.ndarray] = {}
    for i, rec in enumerate(records):
        uniprot = getattr(rec, "uniprot", None) or (rec.get("uniprot") if isinstance(rec, dict) else None)
        sequence = getattr(rec, "sequence", None) or (rec.get("sequence") if isinstance(rec, dict) else None)
        if not uniprot:
            meta["zeros"] += 1
            continue
        if uniprot in seen:
            out[i] = seen[uniprot]
            continue
        cached = load_cached(uniprot, checkpoint)
        if cached is not None:
            meta["cache_hits"] += 1
            seen[uniprot] = cached
            out[i] = cached
            continue
        meta["cache_misses"] += 1
        if auto_compute and sequence:
            emb = compute_one(uniprot, sequence, checkpoint=checkpoint)
            if emb is not None:
                meta["computed"] += 1
                save_cached(uniprot, emb, checkpoint=checkpoint)
                seen[uniprot] = emb
                out[i] = emb
                continue
        meta["zeros"] += 1
        seen[uniprot] = np.zeros(dim, dtype=np.float32)
    return out, meta


# Register a featurizer entry for the cache. It's "integrated" iff the
# cache directory has at least one .npy file OR fair-esm is installed.
# That gives users a way to see the state on the Features screen.

def _esm2_cache_compute(records):
    """Featurizer-style compute() — returns just the embeddings array."""
    embs, _ = batch_get_or_compute(records, checkpoint="esm2_t33_650M")
    return embs


def _esm2_cache_available() -> bool:
    """True iff the cache has any files or fair-esm is installed."""
    root = cache_root("esm2_t33_650M")
    if root.is_dir() and any(root.glob("*.npy")):
        return True
    try:
        import esm  # noqa: F401
        return True
    except Exception:
        return False


# Side-effect: register at import time via the featurizers package so
# the GUI catalog reports the cache state. Wrapped in a try block so an
# import-order glitch doesn't break the rest of the v2 stack.
try:
    from .featurizers import FeaturizerSpec, register
    _CACHE_OK = _esm2_cache_available()
    _CACHE_FILES = (
        len(list(cache_root("esm2_t33_650M").glob("*.npy")))
        if cache_root("esm2_t33_650M").is_dir() else 0
    )
    _LONG_DESC = (
        "Pre-computes (or reads pre-computed) ESM-2 650M embeddings for "
        "every protein in the dataset, stores them in "
        f"{cache_root('esm2_t33_650M')}/<UniProt>.npy. The flow builder's "
        "`in.protein_emb` input block reads from this cache.\n\n"
        f"Current cache state: {_CACHE_FILES} embeddings on disk. "
        + (
            "Auto-compute IS available (fair-esm is installed) — "
            "training will lazily embed missing proteins."
            if _CACHE_OK and _CACHE_FILES == 0
            else
            "Auto-compute is NOT available (fair-esm not installed). To "
            "enable, `pip install fair-esm`. Or drop pre-computed .npy "
            "files into the cache directory."
            if not _CACHE_OK
            else
            "Cache pre-populated — training will read directly from disk."
        )
    )
    register(FeaturizerSpec(
        id="protein_esm2_650m_cache",
        label="ESM-2 650M embedding (cached)",
        axis="protein",
        dim=_ESM2_DIMS["esm2_t33_650M"],
        short_desc="1280-d frozen ESM-2 embedding per protein; disk-cached.",
        long_desc=_LONG_DESC,
        requires=["fair-esm or pre-computed cache"],
        cost="moderate",
        compute=_esm2_cache_compute if _CACHE_OK else None,
        integrated=_CACHE_OK,
    ))
except Exception:
    pass
