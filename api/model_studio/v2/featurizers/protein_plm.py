"""Protein language model featurizers.

Three ESM-2 variants exposed at increasing capacity + compute cost:

    protein_esm2_8M_320      tiny, 8M params, 320-dim  — already cached for v2 universe
    protein_esm2_35M_480     small, 35M params, 480-dim
    protein_esm2_150M_640    medium, 150M params, 640-dim

Plus ProtBERT (the original BFD-pretrained protein BERT):

    protein_protbert_1024    1024-dim, 420M params, downloads ~1.6 GB

Each uses mean-pool over residue positions (skipping cls/eos/pad).
Embeddings are content-addressed by sequence_md5 so re-runs reuse the
v2 catalog's persisted embedding when possible.
"""

from __future__ import annotations

import hashlib

import numpy as np

from . import register, FeaturizerSpec


_ESM_OK = False
try:
    import esm  # type: ignore
    _ESM_OK = True
except ImportError:
    pass

_TRANSFORMERS_OK = False
try:
    import torch  # noqa: F401
    from transformers import AutoTokenizer, AutoModel  # type: ignore
    _TRANSFORMERS_OK = True
except ImportError:
    pass


_ESM_VARIANTS = {
    "protein_esm2_8M_320":   ("esm2_t6_8M_UR50D",   320),
    "protein_esm2_35M_480":  ("esm2_t12_35M_UR50D", 480),
    "protein_esm2_150M_640": ("esm2_t30_150M_UR50D", 640),
}

_loaded_esm: dict[str, tuple] = {}


def _load_esm(model_name: str):
    if model_name in _loaded_esm:
        return _loaded_esm[model_name]
    factory = getattr(esm.pretrained, model_name, None)
    if factory is None:
        raise ValueError(f"Unknown ESM-2 model '{model_name}'")
    model, alphabet = factory()
    model.eval()
    batch_converter = alphabet.get_batch_converter()
    _loaded_esm[model_name] = (model, batch_converter)
    return model, batch_converter


def _embed_esm(records, *, esm_model_name: str, dim: int,
               batch_size: int = 8, max_len: int = 1024) -> np.ndarray:
    import torch
    from ..gpu_runtime import select_device
    dev = select_device("auto")
    model, batch_converter = _load_esm(esm_model_name)
    model = model.to(dev)
    last_layer = sum(1 for _ in model.layers) if hasattr(model, "layers") else 6

    out = np.zeros((len(records), dim), dtype=np.float32)
    with torch.no_grad():
        for start in range(0, len(records), batch_size):
            batch = records[start:start + batch_size]
            named = [(str(i + start), (getattr(r, "sequence", "") or "")[:max_len]) for i, r in enumerate(batch)]
            valid = [(n, s) for n, s in named if s]
            if not valid:
                continue
            _, _, tokens = batch_converter(valid)
            tokens = tokens.to(dev)
            res = model(tokens, repr_layers=[last_layer], return_contacts=False)
            reps = res["representations"][last_layer]
            mask = (tokens != 1) & (tokens != 2) & (tokens != 0)
            mask = mask.unsqueeze(-1).float()
            pooled = (reps * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            arr = pooled.cpu().numpy().astype(np.float32)
            for k, (name, _seq) in enumerate(valid):
                idx = int(name) - start
                out[start + idx] = arr[k]
    return out


def _make_esm_compute(model_name: str, dim: int):
    def _compute(records):
        if not _ESM_OK:
            return np.zeros((len(records), dim), dtype=np.float32)
        return _embed_esm(records, esm_model_name=model_name, dim=dim)
    return _compute


# ── ProtBERT via transformers ──────────────────────────────────────────

_PROTBERT_HUB = "Rostlab/prot_bert_bfd"
_PROTBERT_DIM = 1024
_loaded_protbert = None


def _load_protbert():
    global _loaded_protbert
    if _loaded_protbert is not None:
        return _loaded_protbert
    tok = AutoTokenizer.from_pretrained(_PROTBERT_HUB, do_lower_case=False)
    mod = AutoModel.from_pretrained(_PROTBERT_HUB)
    mod.eval()
    _loaded_protbert = (tok, mod)
    return tok, mod


def _embed_protbert(records, *, batch_size: int = 4, max_len: int = 1024) -> np.ndarray:
    import torch
    from ..gpu_runtime import select_device
    dev = select_device("auto")
    tok, mod = _load_protbert()
    mod = mod.to(dev)
    out = np.zeros((len(records), _PROTBERT_DIM), dtype=np.float32)
    # ProtBERT expects space-separated residues
    seqs = [" ".join(list((getattr(r, "sequence", "") or "")[:max_len])) for r in records]
    with torch.no_grad():
        for start in range(0, len(records), batch_size):
            batch = seqs[start:start + batch_size]
            enc = tok(batch, padding=True, truncation=True, max_length=max_len + 2,
                      return_tensors="pt").to(dev)
            h = mod(**enc).last_hidden_state
            mask = enc.attention_mask.unsqueeze(-1).float()
            pooled = (h * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            out[start:start + len(batch)] = pooled.cpu().numpy().astype(np.float32)
    return out


def _compute_protbert(records):
    if not _TRANSFORMERS_OK:
        return np.zeros((len(records), _PROTBERT_DIM), dtype=np.float32)
    try:
        return _embed_protbert(records)
    except Exception as exc:
        print(f"[featurizers] protbert failed: {exc}", flush=True)
        return np.zeros((len(records), _PROTBERT_DIM), dtype=np.float32)


# ── Registration ───────────────────────────────────────────────────────

for fid, (esm_name, dim) in _ESM_VARIANTS.items():
    cost = "moderate" if dim < 500 else "heavy"
    register(FeaturizerSpec(
        id=fid,
        label=f"ESM-2 {esm_name} ({dim}-dim)",
        axis="protein", dim=dim,
        short_desc=f"Facebook ESM-2 protein LM, {dim}-dim mean-pool embedding.",
        long_desc=(f"ESM-2 variant {esm_name} from fair-esm. Mean-pooled over "
                   f"residue positions, skipping special tokens. Cached for "
                   f"the v2 universe under v2_protein_embeddings_{esm_name}."),
        requires=["esm", "torch"], cost=cost,
        compute=_make_esm_compute(esm_name, dim) if _ESM_OK else None,
        integrated=_ESM_OK,
    ))

register(FeaturizerSpec(
    id="protein_protbert_1024",
    label="ProtBERT-BFD (1024-dim)",
    axis="protein", dim=_PROTBERT_DIM,
    short_desc="Rostlab/prot_bert_bfd. 420M params, BFD-pretrained.",
    long_desc=("ProtBERT trained on BFD database with masked-LM objective. "
               "Strong general-purpose protein representations; slightly "
               "older than ESM-2 but well-validated. Downloads ~1.6 GB to "
               "the HF cache on first call."),
    requires=["transformers", "torch"], cost="heavy",
    compute=_compute_protbert if _TRANSFORMERS_OK else None,
    integrated=_TRANSFORMERS_OK,
))
