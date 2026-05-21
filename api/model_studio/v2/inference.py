"""Inference + comparison endpoints for completed runs.

Loads a saved checkpoint on-demand (LRU-cached so repeat calls are cheap)
and predicts pKd for a single P-L pair or a batch.

Models are kept in a small in-memory cache keyed by run_id; eviction is
size-bounded so a long-running server doesn't pin every checkpoint to GPU.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np
import torch

from .checkpoints import load_for_inference, load_meta
from .dataset import label_sequence, label_smiles, MAX_SEQ_LEN, MAX_SMI_LEN


_MAX_CACHED = 4
_cache: "OrderedDict[str, tuple]" = OrderedDict()
_cache_lock = threading.Lock()


def _get_cached(run_id: str):
    """LRU lookup. Returns (model, meta, device) or None."""
    with _cache_lock:
        v = _cache.get(run_id)
        if v is not None:
            _cache.move_to_end(run_id)
        return v


def _put_cached(run_id: str, model, meta, device) -> None:
    with _cache_lock:
        _cache[run_id] = (model, meta, device)
        _cache.move_to_end(run_id)
        while len(_cache) > _MAX_CACHED:
            _cache.popitem(last=False)


def _resolve(run_id: str):
    cached = _get_cached(run_id)
    if cached is not None:
        return cached
    model, meta = load_for_inference(run_id)
    device = next(model.parameters()).device
    _put_cached(run_id, model, meta, device)
    return model, meta, device


@torch.no_grad()
def predict_one(run_id: str, sequence: str, smiles: str) -> dict:
    """Predict pKd for a single (sequence, SMILES) pair."""
    model, meta, device = _resolve(run_id)
    seq_t = torch.from_numpy(label_sequence(sequence)).unsqueeze(0).to(device)
    smi_t = torch.from_numpy(label_smiles(smiles)).unsqueeze(0).to(device)
    yp = model(seq_t, smi_t).float().cpu().numpy()[0]
    return {
        "run_id": run_id,
        "template_id": meta["template_id"],
        "predicted_pkd": float(yp),
        "predicted_kd_nm": float(10 ** (9 - yp)),
        "y_pkd_range": meta.get("y_pkd_range"),
        "input": {
            "sequence_len": len(sequence),
            "sequence_truncated": len(sequence) > MAX_SEQ_LEN,
            "smiles_len": len(smiles),
            "smiles_truncated": len(smiles) > MAX_SMI_LEN,
        },
    }


@torch.no_grad()
def predict_batch(run_id: str, pairs: list[dict]) -> dict:
    """Predict pKd for a batch of (sequence, SMILES) pairs.

    Each item is { sequence: str, smiles: str, id?: str }. Order is preserved.
    """
    model, meta, device = _resolve(run_id)
    if not pairs:
        return {"run_id": run_id, "predictions": []}
    seqs = np.stack([label_sequence(p["sequence"]) for p in pairs])
    smis = np.stack([label_smiles(p["smiles"]) for p in pairs])
    # Batch in chunks of 256 to avoid OOM on big batches.
    out: list[float] = []
    BS = 256
    for i in range(0, len(pairs), BS):
        seq_t = torch.from_numpy(seqs[i:i+BS]).to(device)
        smi_t = torch.from_numpy(smis[i:i+BS]).to(device)
        yp = model(seq_t, smi_t).float().cpu().numpy()
        out.extend(float(v) for v in yp)
    return {
        "run_id": run_id,
        "template_id": meta["template_id"],
        "predictions": [
            {
                "id": pairs[i].get("id", str(i)),
                "predicted_pkd": out[i],
                "predicted_kd_nm": float(10 ** (9 - out[i])),
            }
            for i in range(len(pairs))
        ],
    }


def compare_runs(run_a: str, run_b: str) -> dict:
    """Side-by-side metric diff between two completed runs.

    Reads from checkpoint metadata so it works after server restart.
    """
    a = load_meta(run_a)
    b = load_meta(run_b)
    if a is None:
        raise FileNotFoundError(f"No checkpoint for run {run_a}")
    if b is None:
        raise FileNotFoundError(f"No checkpoint for run {run_b}")

    def _safe(m, k, default=None):
        return m.get("summary", {}).get(k, default)

    keys = [
        "test_pearson", "test_spearman", "test_rmse", "test_mae", "test_ci",
        "best_val_pearson", "best_val_rmse", "test_auc_pki6", "wall_time_s",
        "n_params", "n_train", "n_val", "n_test",
    ]
    rows = []
    for k in keys:
        va, vb = _safe(a, k), _safe(b, k)
        delta = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = vb - va
        rows.append({"metric": k, "a": va, "b": vb, "delta": delta})

    def _summary(meta):
        return {
            "run_id":         meta.get("run_id"),
            "template_id":    meta.get("template_id"),
            "template_label": (meta.get("effective_config") or {}).get("template_label"),
            "summary":        meta.get("summary", {}),
            "hparams":        meta.get("hparams", {}),
        }

    return {
        "a": _summary(a),
        "b": _summary(b),
        "rows": rows,
        # Headline winner per direction (higher-is-better vs lower-is-better)
        "winners": _compute_winners(rows),
    }


HIGHER_IS_BETTER = {"test_pearson", "test_spearman", "test_ci", "best_val_pearson", "test_auc_pki6"}
LOWER_IS_BETTER  = {"test_rmse", "test_mae", "best_val_rmse"}


def _compute_winners(rows: list[dict]) -> dict:
    """Tally how many metrics each run wins on. Returns counts + per-key winner."""
    counts = {"a": 0, "b": 0, "tie": 0}
    per = {}
    for r in rows:
        k = r["metric"]
        a, b = r["a"], r["b"]
        if a is None or b is None or not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            per[k] = None
            continue
        if k in HIGHER_IS_BETTER:
            winner = "a" if a > b else "b" if b > a else "tie"
        elif k in LOWER_IS_BETTER:
            winner = "a" if a < b else "b" if b < a else "tie"
        else:
            per[k] = None
            continue
        per[k] = winner
        counts[winner] += 1
    return {"counts": counts, "per_metric": per}
