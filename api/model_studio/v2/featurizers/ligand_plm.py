"""Chemical language model embeddings for ligands.

ChemBERTa and MolFormer give learned representations that outperform
fingerprints + descriptors on most public benchmarks. Both are pulled
from HuggingFace at first call; weights are cached under
``~/.cache/huggingface/``.

Models exposed:

    ligand_chemberta_77M      seyonec/ChemBERTa-zinc-base-v1
                              ZINC-pretrained, 768-dim CLS embedding
    ligand_molformer_24M      ibm/MoLFormer-XL-both-10pct
                              molformer trained on PubChem+ZINC, 768-dim

Both use mean-pooling over the encoder's last hidden state (skipping
[CLS] / [SEP] / pad tokens) for a stable representation. The HF cache
makes repeat calls free.
"""

from __future__ import annotations

import numpy as np

from . import register, FeaturizerSpec


_TRANSFORMERS_OK = False
try:
    import torch  # noqa: F401
    from transformers import AutoTokenizer, AutoModel  # type: ignore
    _TRANSFORMERS_OK = True
except ImportError:
    pass


_HF_MODELS: dict[str, dict] = {
    "ligand_chemberta_77M": {
        "hub_id":     "seyonec/ChemBERTa-zinc-base-v1",
        "dim":        768,
        "kind":       "ChemBERTa",
        "trust_remote_code": False,
    },
    # MolFormer's official repo needs remote code; we wire it but
    # transparently degrade if loading fails.
    "ligand_molformer_24M": {
        "hub_id":     "ibm/MoLFormer-XL-both-10pct",
        "dim":        768,
        "kind":       "MolFormer",
        "trust_remote_code": True,
    },
}


# In-process model cache, keyed by hub_id
_LOADED: dict[str, tuple] = {}


def _load(hub_id: str, trust_remote_code: bool):
    if hub_id in _LOADED:
        return _LOADED[hub_id]
    tok = AutoTokenizer.from_pretrained(hub_id, trust_remote_code=trust_remote_code)
    mod = AutoModel.from_pretrained(hub_id, trust_remote_code=trust_remote_code)
    mod.eval()
    _LOADED[hub_id] = (tok, mod)
    return tok, mod


def _embed_smiles(smiles_list: list[str], *, hub_id: str, dim: int,
                  trust_remote_code: bool, device: str = "auto",
                  batch_size: int = 16) -> np.ndarray:
    import torch
    from ..gpu_runtime import select_device
    dev = select_device(device)
    tok, mod = _load(hub_id, trust_remote_code)
    mod = mod.to(dev)
    out = np.zeros((len(smiles_list), dim), dtype=np.float32)
    with torch.no_grad():
        for start in range(0, len(smiles_list), batch_size):
            batch = smiles_list[start:start + batch_size]
            # Replace empty / None SMILES with a sentinel that the
            # tokeniser won't crash on; row stays zero.
            cleaned = [s if (isinstance(s, str) and s) else "C" for s in batch]
            enc = tok(cleaned, padding=True, truncation=True, max_length=256,
                      return_tensors="pt").to(dev)
            h = mod(**enc).last_hidden_state                    # (B, L, D)
            # Masked mean-pool (skip pad)
            mask = enc.attention_mask.unsqueeze(-1).float()
            pooled = (h * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            arr = pooled.cpu().numpy().astype(np.float32)
            # Re-zero rows where the original SMILES was empty
            for j, s in enumerate(batch):
                if not (isinstance(s, str) and s):
                    arr[j] = 0
            out[start:start + len(batch)] = arr
    return out


def _make_compute_fn(fid: str):
    cfg = _HF_MODELS[fid]
    hub_id = cfg["hub_id"]
    dim    = cfg["dim"]
    trust  = cfg["trust_remote_code"]
    def _compute(records):
        smiles_list = [getattr(r, "smiles", "") or "" for r in records]
        try:
            return _embed_smiles(smiles_list, hub_id=hub_id, dim=dim,
                                 trust_remote_code=trust)
        except Exception as exc:
            print(f"[featurizers] {fid} load failed: {exc}", flush=True)
            return np.zeros((len(records), dim), dtype=np.float32)
    return _compute


# ── Registration ───────────────────────────────────────────────────────

register(FeaturizerSpec(
    id="ligand_chemberta_77M",
    label="ChemBERTa-77M (768-dim, ZINC-pretrained)",
    axis="ligand", dim=768,
    short_desc="HuggingFace seyonec/ChemBERTa-zinc-base-v1. Mean-pool CLS embedding.",
    long_desc=("ChemBERTa is a RoBERTa-style transformer trained on 77M "
               "ZINC SMILES with masked-language-modelling. Strong baseline "
               "for property prediction; ~77M params. First call downloads "
               "~300 MB to the HF cache; subsequent calls are instant."),
    requires=["transformers", "torch"], cost="heavy",
    compute=_make_compute_fn("ligand_chemberta_77M") if _TRANSFORMERS_OK else None,
    integrated=_TRANSFORMERS_OK,
))

register(FeaturizerSpec(
    id="ligand_molformer_24M",
    label="MolFormer-XL (768-dim, PubChem+ZINC)",
    axis="ligand", dim=768,
    short_desc="IBM ibm/MoLFormer-XL-both-10pct. Rotary-positional transformer.",
    long_desc=("MolFormer is IBM's chemical-language transformer trained "
               "on 1.1B PubChem + ZINC molecules. Linear-time attention "
               "with rotary positional embeddings. State-of-the-art for "
               "property prediction in 2023. Requires trust_remote_code "
               "to download the custom tokenizer. ~500 MB weights."),
    requires=["transformers", "torch"], cost="heavy",
    compute=_make_compute_fn("ligand_molformer_24M") if _TRANSFORMERS_OK else None,
    integrated=_TRANSFORMERS_OK,
))
