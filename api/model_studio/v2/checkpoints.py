"""Trained-model checkpoint persistence + on-demand reload for inference.

Layout on disk (CHECKPOINT_DIR / <run_id>/):
    state.pt          torch state_dict (best val Pearson)
    meta.json         { template_id, effective_config, hparams, summary, y_pkd_range }

Designed for the v0 backend (single-process, in-memory registry). When the
registry restarts and loses Run objects, the checkpoints still exist on disk
and can be referenced by run_id — load_for_inference() rebuilds the model
and metadata from those files.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import torch
import torch.nn as nn

from .models import model_for_template

CHECKPOINT_DIR = Path(os.environ.get(
    "PROTEOSPHERE_V2_CHECKPOINTS",
    str(Path.home() / ".proteosphere_v2" / "checkpoints"),
))


def _run_dir(run_id: str) -> Path:
    return CHECKPOINT_DIR / run_id


def save_checkpoint(
    *,
    run_id: str,
    model: nn.Module,
    template_id: str,
    effective_config: dict,
    hparams: dict,
    summary: dict,
    y_pkd_range: tuple[float, float],
) -> Path:
    """Atomically persist a checkpoint. Returns the saved directory."""
    d = _run_dir(run_id)
    d.mkdir(parents=True, exist_ok=True)
    # Save state_dict to a temp file and rename so a partial write never
    # confuses a concurrent load.
    # PyTorch 2.6+ added a strict device validator under weights_only=True
    # that runs BEFORE map_location resolves — meaning a state saved on
    # CUDA can't be loaded on a CPU-only host even with map_location='cpu'.
    # Calling .detach().cpu() per tensor isn't sufficient on its own,
    # because torch's serializer can still imprint the saver's original
    # CUDA device tag onto the storage's location string. The reliable
    # fix is a numpy round-trip: write each tensor's values to a fresh
    # numpy array, then rebuild via torch.from_numpy — that produces
    # CPU storages with a CPU device tag, guaranteed.
    state_clean: dict = {}
    for k, v in model.state_dict().items():
        if isinstance(v, torch.Tensor):
            arr = v.detach().cpu().numpy()
            state_clean[k] = torch.from_numpy(arr.copy())
        else:
            state_clean[k] = v
    state_tmp = d / "state.pt.partial"
    torch.save(state_clean, state_tmp)
    os.replace(state_tmp, d / "state.pt")
    meta = {
        "run_id": run_id,
        "template_id": template_id,
        "effective_config": effective_config,
        "hparams": hparams,
        "summary": summary,
        "y_pkd_range": list(y_pkd_range),
    }
    # ensure_ascii=True (the default) escapes every non-ASCII char so
    # we can never trip the Windows cp1252 charmap codec when something
    # downstream re-reads the meta.json. Also write the file in binary
    # mode + manual UTF-8 encode so the open()-side encoding can't be
    # overridden by a stray PYTHONIOENCODING.
    payload = json.dumps(meta, indent=2, ensure_ascii=True, default=str)
    with open(d / "meta.json", "wb") as f:
        f.write(payload.encode("utf-8"))
    return d


def has_checkpoint(run_id: str) -> bool:
    d = _run_dir(run_id)
    return (d / "state.pt").exists() and (d / "meta.json").exists()


def load_meta(run_id: str) -> dict | None:
    d = _run_dir(run_id)
    p = d / "meta.json"
    if not p.exists():
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def load_for_inference(run_id: str, *, device: str | torch.device | None = None) -> tuple[nn.Module, dict]:
    """Rebuild the model from its checkpoint. Returns (model_eval_mode, meta)."""
    meta = load_meta(run_id)
    if meta is None:
        raise FileNotFoundError(f"No checkpoint for run {run_id} at {_run_dir(run_id)}")
    # Robust CUDA detection: torch.cuda.is_available() returns True under
    # CUDA_VISIBLE_DEVICES="" even though device_count is 0. Combine both
    # checks so we never pick "cuda" when no device is actually visible.
    _cuda_ok = bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
    dev = torch.device(
        device if device is not None
        else ("cuda" if _cuda_ok else "cpu")
    )
    model = model_for_template(meta["template_id"], meta["effective_config"])
    # Bulletproof checkpoint load on CPU-only boxes. PyTorch 2.6+'s strict
    # device validator (under weights_only=True) and even the loose path
    # (weights_only=False) reject CUDA-tagged storages on a CPU host —
    # neither `map_location='cpu'` (string) nor `map_location=torch.device('cpu')`
    # short-circuits the validator, because both forms pass control through
    # default_restore_location which calls each backend's _deserialize with
    # the FILE'S original location ('cuda:0'). The CUDA backend then
    # raises before map_location gets a say.
    #
    # The reliable bypass is the CALLABLE map_location form. When the
    # unpickler sees a callable, it invokes it BEFORE the backend dispatch
    # and uses whatever the callable returns — sidestepping the validator
    # entirely. ``lambda storage, loc: storage`` says "the storage is
    # already a generic CPU-backed buffer, just use it" which is what we
    # want on a CPU host.
    state_path = _run_dir(run_id) / "state.pt"
    if dev.type == "cpu":
        # PyTorch 2.11's `weights_only=True` validator runs BEFORE
        # map_location resolves, so it rejects CUDA-tagged storages on
        # a CPU host even when map_location=torch.device('cpu') or
        # map_location='cpu'. The reliable bypass is `weights_only=False`
        # combined with a callable map_location that returns the storage
        # as-is (CPU). On CPU-only hosts, security is moot because the
        # checkpoint was just produced locally by the trainer.
        state = torch.load(
            state_path,
            map_location=lambda storage, _loc: storage,
            weights_only=False,
        )
    else:
        state = torch.load(
            state_path,
            map_location=dev,
            weights_only=True,
        )
    model.load_state_dict(state)
    model.to(dev).eval()
    return model, meta


def list_checkpoints() -> list[dict]:
    """List every checkpoint on disk with basic metadata for the registry UI."""
    out: list[dict] = []
    if not CHECKPOINT_DIR.exists():
        return out
    for d in sorted(CHECKPOINT_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        meta_p = d / "meta.json"
        if not meta_p.exists():
            continue
        try:
            with open(meta_p, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        out.append({
            "run_id": m.get("run_id", d.name),
            "template_id": m.get("template_id"),
            "template_label": (m.get("effective_config") or {}).get("template_label"),
            "summary": m.get("summary", {}),
            "y_pkd_range": m.get("y_pkd_range"),
        })
    return out
