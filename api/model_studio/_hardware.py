"""Hardware discovery and execution-placement resolution.

Extracted from the original 9000-line ``runtime.py`` (May 2026 review
P2-1). The two public entry points are:

* :func:`discover_hardware_profile` -- best-effort introspection of the
  local CPU, RAM, and CUDA capabilities. Uses ``platform`` and ``psutil``
  where available, plus a PowerShell fallback on Windows for the CPU
  model name and Win32_VideoController for non-CUDA GPU enumeration.

* :func:`resolve_execution_placement` -- maps a user-requested hardware
  preset (``"auto_recommend"``, ``"single_gpu"``, ``"cpu_parallel"``, ...)
  against the discovered profile to a concrete execution device.

Both are pure functions (no global state, no disk writes) so they're
safe to call from any thread. The runtime caches the discovery via
:func:`ttl_cache` upstream; nothing here memoises by itself.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from typing import Any

# Lazy torch import — `import torch` is the single slowest line on this
# Windows box (~25 min cold load). Discovery functions don't actually
# need torch loaded at module import; defer it to first call.
from api.model_studio._text import clean_text, utc_now


def _torch_cuda_state() -> tuple[bool, str | None, int]:
    """Returns (cuda_available, gpu_name, gpu_memory_bytes). All branches
    swallow exceptions so a missing / broken torch returns (False, None, 0)
    instead of crashing the workspace GET."""
    try:
        import torch  # noqa: PLC0415  -- intentionally lazy
    except Exception:
        return (False, None, 0)
    try:
        if not bool(torch.cuda.is_available()):
            return (False, None, 0)
        name = torch.cuda.get_device_name(0)
        mem = int(torch.cuda.get_device_properties(0).total_memory)
        return (True, name, mem)
    except Exception:
        return (False, None, 0)


def discover_hardware_profile() -> dict[str, Any]:
    """Probe the local host's CPU, RAM, and accelerator state.

    Returns a dict shaped for the workspace ``hardware_profile`` field.
    All branches return defensively (zeros / empty strings) on any
    discovery failure so a missing optional dependency, a denied
    PowerShell call, or an unsupported platform doesn't crash a GET
    that just wants a snapshot.
    """
    cpu_count = os.cpu_count() or 1
    cpu_model = clean_text(platform.processor())
    total_ram_bytes = 0
    try:  # pragma: no branch - optional dependency in local envs
        import psutil  # type: ignore

        total_ram_bytes = int(psutil.virtual_memory().total)
    except Exception:  # pragma: no cover - optional dependency
        total_ram_bytes = 0
    cuda_available, gpu_name, gpu_memory_bytes = _torch_cuda_state()
    if not cpu_model and platform.system().lower() == "windows":
        try:
            cpu_model = clean_text(
                subprocess.check_output(
                    [
                        "powershell",
                        "-NoProfile",
                        "-Command",
                        (
                            "(Get-CimInstance Win32_Processor | "
                            "Select-Object -First 1 -ExpandProperty Name)"
                        ),
                    ],
                    text=True,
                    timeout=5,
                )
            )
        except Exception:  # pragma: no cover - shell availability differs by host
            cpu_model = ""
    detected_gpus: list[dict[str, Any]] = []
    if platform.system().lower() == "windows":
        try:
            gpu_lines = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    (
                        "Get-CimInstance Win32_VideoController | "
                        "Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"
                    ),
                ],
                text=True,
                timeout=6,
            )
            parsed = json.loads(gpu_lines) if clean_text(gpu_lines) else []
            if isinstance(parsed, dict):
                parsed = [parsed]
            for item in parsed:
                detected_gpus.append(
                    {
                        "name": clean_text(item.get("Name")),
                        "memory_bytes": int(item.get("AdapterRAM") or 0),
                        "memory_gb": round(int(item.get("AdapterRAM") or 0) / (1024**3), 2)
                        if item.get("AdapterRAM")
                        else 0.0,
                    }
                )
        except Exception:  # pragma: no cover - shell availability differs by host
            detected_gpus = []
    ram_gb = total_ram_bytes / (1024**3) if total_ram_bytes else 0.0
    recommended_preset = "cpu_conservative"
    warnings: list[str] = []
    if cuda_available and gpu_memory_bytes >= 6 * 1024**3:
        recommended_preset = "single_gpu"
    elif ram_gb >= 24 and cpu_count >= 12:
        recommended_preset = "cpu_parallel"
    elif ram_gb and ram_gb < 12:
        recommended_preset = "memory_constrained"
        warnings.append(
            "Detected RAM is limited; prefer smaller study builds or lighter model families."
        )
    if not cuda_available:
        warnings.append("CUDA was not detected; GPU-only presets remain unavailable.")
    return {
        "host": platform.node() or "local-host",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_model": cpu_model or "unknown",
        "cpu_count": cpu_count,
        "total_ram_bytes": total_ram_bytes,
        "total_ram_gb": round(ram_gb, 2) if ram_gb else 0.0,
        "cuda_available": cuda_available,
        "gpu_name": gpu_name,
        "gpu_memory_bytes": gpu_memory_bytes,
        "gpu_memory_gb": round(gpu_memory_bytes / (1024**3), 2) if gpu_memory_bytes else 0.0,
        "detected_gpus": detected_gpus,
        "recommended_preset": recommended_preset,
        "warnings": warnings,
        "discovered_at": utc_now(),
    }


def resolve_execution_placement(
    requested_preset: str,
    hardware_profile: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a user-requested preset against the discovered profile.

    The returned dict is shipped verbatim into the run manifest, so
    callers can later audit ``placement_notes`` to understand any
    automatic fallback that happened.
    """
    preset = clean_text(requested_preset) or "auto_recommend"
    recommended = clean_text(hardware_profile.get("recommended_preset")) or "cpu_conservative"
    effective_preset = recommended if preset == "auto_recommend" else preset
    cuda_available = bool(hardware_profile.get("cuda_available"))
    device = "cpu"
    notes: list[str] = []
    if effective_preset == "single_gpu":
        if cuda_available:
            device = "cuda:0"
        else:
            notes.append(
                "Single-GPU preset requested, but CUDA was not detected; falling back to CPU."
            )
            effective_preset = "cpu_conservative"
    elif effective_preset == "multi_worker_large_memory":
        device = "cpu-parallel"
        notes.append(
            "Multi-worker large-memory mode remains CPU-parallel in the current beta lane."
        )
    elif effective_preset == "cpu_parallel":
        device = "cpu-parallel"
    elif effective_preset == "memory_constrained":
        device = "cpu-memory-constrained"
    elif effective_preset == "custom":
        if cuda_available and clean_text(hardware_profile.get("gpu_name")):
            device = "cuda:0"
            notes.append(
                "Custom runtime preset resolved to the locally detected CUDA device; "
                "compare/export should treat this as backend-authoritative."
            )
        else:
            device = (
                "cpu-parallel"
                if int(hardware_profile.get("cpu_count") or 0) >= 8
                else "cpu"
            )
            notes.append(
                "Custom runtime preset resolved against detected local hardware because "
                "fully manual device overrides are not exposed in this beta lane."
            )
    return {
        "requested_hardware_preset": preset,
        "resolved_hardware_preset": effective_preset,
        "resolved_execution_device": device,
        "placement_notes": notes,
    }
