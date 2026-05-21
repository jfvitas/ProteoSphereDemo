"""GPU runtime helpers — CUDA warmup, device selection, GPU-resident
featurization. Centralises every "is the GPU usable here?" decision so
new modules don't have to re-derive it.

Why this exists:
    First CUDA touch on an RTX 5080 / Blackwell card cooks the
    cudnn / cublas auto-tuner caches for ~1-3 minutes. Doing this at
    server boot makes the first training launch and the first leakage
    report snappy instead of fronting the cost to a user-facing call.
"""

from __future__ import annotations

import os
import threading
import time


_warmup_done = False
_warmup_lock = threading.Lock()


def gpu_available() -> bool:
    """Cheap probe — returns True if torch sees AT LEAST ONE CUDA device.

    NOTE: ``torch.cuda.is_available()`` is unreliable on Windows when
    CUDA_VISIBLE_DEVICES="" — it returns True even though
    ``torch.cuda.device_count()`` is 0, leading to "Attempting to
    deserialize object on CUDA device 0 but torch.cuda.device_count()
    is 0" later. The robust check combines both APIs.
    """
    try:
        import torch
        return bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
    except Exception:
        return False


def select_device(prefer: str = "auto"):
    """Resolves prefer ∈ {auto,cuda,cpu} → a torch.device.

    "auto" picks CUDA when at least one CUDA device is visible (not just
    "is_available"; that API misreports True when CUDA_VISIBLE_DEVICES=""
    even though no devices exist). Honors PROTEOSPHERE_FORCE_CPU as a
    kill-switch (useful for tests).
    """
    import torch
    if os.environ.get("PROTEOSPHERE_FORCE_CPU") == "1":
        return torch.device("cpu")
    if prefer == "cpu":
        return torch.device("cpu")
    cuda_ok = bool(torch.cuda.is_available()) and torch.cuda.device_count() > 0
    if prefer == "cuda":
        if not cuda_ok:
            raise RuntimeError("CUDA requested but not available.")
        return torch.device("cuda")
    return torch.device("cuda" if cuda_ok else "cpu")


def warmup_cuda(*, blocking: bool = False, verbose: bool = False) -> dict:
    """Touch CUDA + cudnn + a tiny conv/matmul to cook the autotune cache.

    Args:
        blocking: if False (default), runs in a background thread so the
                  call returns immediately; the first GPU op afterwards
                  pays nothing if the thread has already finished.
        verbose:  print timing info to stdout.

    Critically, when ``blocking=False`` we MUST NOT import torch from
    the caller's thread — that's how this function used to block server
    startup for minutes on first cold Python. All torch imports happen
    inside the daemon thread.
    """
    global _warmup_done
    if _warmup_done:
        return {"status": "already_warm", "device": "cuda"}

    def _do_warmup():
        global _warmup_done
        t0 = time.time()
        try:
            import torch  # heavy import lives entirely in the thread
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"[gpu_runtime] torch import failed: {exc}", flush=True)
            return
        if not torch.cuda.is_available():
            if verbose:
                print("[gpu_runtime] no CUDA device; skipping warmup", flush=True)
            return
        try:
            with _warmup_lock:
                if _warmup_done:
                    return
                dev = torch.device("cuda")
                # Conv1d (DeepDTA / GraphDTA tower) and matmul (transformer head)
                x = torch.randn(8, 128, 1000, device=dev)
                conv = torch.nn.Conv1d(128, 96, kernel_size=8, device=dev)
                _ = conv(x)
                # Matmul shape large enough to trigger cublas autotune
                a = torch.randn(1024, 1024, device=dev)
                b = torch.randn(1024, 1024, device=dev)
                _ = a @ b
                # int64 popcount path (GPU Tanimoto)
                c = torch.randint(0, 2**62, (256, 32), dtype=torch.int64, device=dev)
                _ = (c & c).sum(dim=1)
                torch.cuda.synchronize()
                _warmup_done = True
            if verbose:
                print(f"[gpu_runtime] CUDA warmup completed in {time.time()-t0:.1f}s", flush=True)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"[gpu_runtime] warmup error: {exc}", flush=True)

    if blocking:
        _do_warmup()
        return {"status": "warmed_up" if _warmup_done else "skipped",
                "device": "cuda" if _warmup_done else "cpu"}
    else:
        threading.Thread(target=_do_warmup, name="cuda-warmup", daemon=True).start()
        return {"status": "warmup_started"}


def gpu_info() -> dict:
    """Snapshot of GPU capability — used by /api/v2/system/gpu.

    Non-blocking: if torch hasn't been imported yet in this process,
    we return a "warming_up" response rather than triggering the
    multi-minute cold-import. Once warmup_cuda() has completed in its
    daemon thread, torch is cached in sys.modules and this returns the
    full info instantly.
    """
    import sys
    if "torch" not in sys.modules:
        return {
            "available":   None,
            "warmed_up":   False,
            "status":      "loading",
            "message":     "torch is still importing in the warmup thread",
        }
    torch = sys.modules["torch"]
    # Torch may be mid-import in the warmup thread — submodules like
    # torch.cuda might not be attached yet. Detect that and return a
    # transitional response instead of crashing.
    if not hasattr(torch, "cuda") or not hasattr(torch.cuda, "is_available"):
        return {
            "available":   None,
            "warmed_up":   False,
            "status":      "loading",
            "message":     "torch is partially initialised; retry shortly",
        }
    try:
        if not torch.cuda.is_available():
            return {"available": False, "warmed_up": False, "reason": "no_cuda_device"}
        idx = torch.cuda.current_device()
        props = torch.cuda.get_device_properties(idx)
        total = props.total_memory
        free, _ = torch.cuda.mem_get_info()
    except Exception as exc:  # noqa: BLE001
        return {
            "available":   None,
            "warmed_up":   False,
            "status":      "loading",
            "message":     f"torch not ready: {exc}",
        }
    return {
        "available":     True,
        "warmed_up":     _warmup_done,
        "device_index":  int(idx),
        "device_name":   props.name,
        "compute_cap":   f"{props.major}.{props.minor}",
        "total_memory_bytes": int(total),
        "free_memory_bytes":  int(free),
        "used_memory_bytes":  int(total - free),
        "free_pct":      round(100.0 * free / total, 1),
        "used_pct":      round(100.0 * (total - free) / total, 1),
        "torch_version": torch.__version__,
    }


# ── Host (CPU / RAM / disk) info ───────────────────────────────────────
# Used by /api/v2/system/host so the Training pane GPU/System card can
# show real numbers instead of fake A100 fixtures. Defensive: returns
# stdlib-only results if psutil isn't installed.

_LAST_DISK_SAMPLE = {"t": 0.0, "read_bytes": 0, "write_bytes": 0}


def host_info() -> dict:
    """Snapshot of host system stats (CPU / RAM / disk).

    Returns a dict that's always JSON-safe even when psutil is missing.
    The disk-I/O fields require psutil and rely on a difference between
    the previous sample and the current one — short polling cadence
    (1-3s) is fine; longer gaps just give a longer-window average.
    """
    import os as _os
    import shutil
    import time as _time

    out: dict = {
        "ok":         True,
        "cpu_count":  int(_os.cpu_count() or 0),
    }
    # CPU + RAM via psutil (preferred). Fall back to stdlib if missing.
    try:
        import psutil  # type: ignore
    except Exception:
        psutil = None  # type: ignore

    if psutil is not None:
        try:
            # cpu_percent(interval=None) returns the percent since the
            # last call; on the first call it returns 0.0. That's fine —
            # the frontend polls every 2-3s so subsequent calls give
            # accurate values.
            out["cpu_pct"]      = float(psutil.cpu_percent(interval=None))
            out["cpu_load_per_core"] = [float(x) for x in psutil.cpu_percent(percpu=True)]
            vm = psutil.virtual_memory()
            out["ram_total_bytes"] = int(vm.total)
            out["ram_used_bytes"]  = int(vm.used)
            out["ram_pct"]         = float(vm.percent)
        except Exception as exc:
            out["psutil_error"] = str(exc)
        # Disk I/O rate (read+write MB/s) since the last call
        try:
            io = psutil.disk_io_counters()
            now = _time.time()
            if io is not None:
                prev = _LAST_DISK_SAMPLE
                dt = max(0.001, now - prev["t"]) if prev["t"] else 0.0
                if dt > 0:
                    read_rate  = (io.read_bytes  - prev["read_bytes"])  / dt
                    write_rate = (io.write_bytes - prev["write_bytes"]) / dt
                    out["disk_read_bps"]  = float(max(0.0, read_rate))
                    out["disk_write_bps"] = float(max(0.0, write_rate))
                _LAST_DISK_SAMPLE.update({
                    "t":           now,
                    "read_bytes":  int(io.read_bytes),
                    "write_bytes": int(io.write_bytes),
                })
        except Exception as exc:
            out["disk_io_error"] = str(exc)
    else:
        out["psutil_missing"] = True

    # Disk free on the project drive — works without psutil.
    try:
        # D: is the dev volume per the user's MEMORY.md; fall back to cwd.
        target = "D:\\" if _os.path.exists("D:\\") else _os.getcwd()
        total, used, free = shutil.disk_usage(target)
        out["disk_root"]       = target
        out["disk_total_bytes"] = int(total)
        out["disk_used_bytes"]  = int(used)
        out["disk_free_bytes"]  = int(free)
        out["disk_pct"]         = round(100.0 * used / total, 1) if total else 0.0
    except Exception as exc:
        out["disk_error"] = str(exc)

    return out
