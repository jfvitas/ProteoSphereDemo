"""Manifest read/write + per-run lock registry.

Extracted from the original 9000-line ``runtime.py`` (May 2026 review
P2-1). The lock registry, manifest write/read pair, and run-control
write/read pair all live here as a single coupled unit -- they share
the lock-protected guarantee that no two threads ever rewrite the
same ``run_manifest.json`` at the same time.

Process-wide state is centralized in :class:`_PersistenceState` so
tests can reset between cases via :func:`reset_state`.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from api.model_studio._io import load_json, save_json
from api.model_studio._text import clean_text


# The filename embedded in every run directory that holds the cancel /
# resume control flags. Kept as a module constant so callers don't
# need to know the layout.
RUN_CONTROL_FILE = "run_control.json"

# Cap on how many run-specific locks we keep in memory. The locks are
# only valuable while a run is active or imminently being read; older
# entries are evicted in FIFO order so a long-lived server doesn't
# leak a lock per historical run id.
MAX_RUN_LOCKS = 256


@dataclass
class _PersistenceState:
    """Process-wide locks. Wrapped in a dataclass so tests can ``reset()``."""

    run_locks: dict[str, threading.Lock] = field(default_factory=dict)
    run_locks_guard: threading.Lock = field(default_factory=threading.Lock)
    run_threads: dict[str, threading.Thread] = field(default_factory=dict)
    run_threads_guard: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self.run_locks_guard:
            self.run_locks.clear()
        with self.run_threads_guard:
            self.run_threads.clear()


_STATE = _PersistenceState()


def reset_state() -> None:
    """Test hook: wipe every per-run lock + thread registration."""
    _STATE.reset()


def run_lock(run_id: str) -> threading.Lock:
    """Return the manifest-write lock for ``run_id``, creating one if
    needed. ``setdefault`` is wrapped in a guard lock so two threads
    can't observe ``None`` simultaneously and create different locks.

    The registry is bounded to :data:`MAX_RUN_LOCKS`; the oldest entry
    is evicted in FIFO order on overflow.
    """
    with _STATE.run_locks_guard:
        lock = _STATE.run_locks.get(run_id)
        if lock is None:
            if len(_STATE.run_locks) >= MAX_RUN_LOCKS:
                oldest = next(iter(_STATE.run_locks))
                _STATE.run_locks.pop(oldest, None)
            lock = threading.Lock()
            _STATE.run_locks[run_id] = lock
        return lock


def register_run_thread(run_id: str, thread: threading.Thread) -> None:
    """Record the worker thread that owns ``run_id``.

    Same FIFO eviction policy as :func:`run_lock` so the dict cannot
    grow unboundedly across the lifetime of a long-lived server.
    """
    if not run_id:
        return
    with _STATE.run_threads_guard:
        if len(_STATE.run_threads) >= MAX_RUN_LOCKS:
            _STATE.run_threads.pop(next(iter(_STATE.run_threads)), None)
        _STATE.run_threads[run_id] = thread


def write_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    """Atomically rewrite ``run_dir/run_manifest.json`` under the per-run lock."""
    run_id = clean_text(manifest.get("run_id"))
    lock = run_lock(run_id) if run_id else threading.Lock()
    with lock:
        save_json(run_dir / "run_manifest.json", manifest)


def read_manifest(run_dir: Path) -> dict[str, Any]:
    """Load ``run_dir/run_manifest.json`` under the per-run lock.

    Returns an empty dict if the file is missing, empty, or invalid.
    """
    manifest_path = run_dir / "run_manifest.json"
    lock = run_lock(run_dir.name)
    with lock:
        return load_json(manifest_path, {})


def write_run_control(run_dir: Path, payload: dict[str, Any]) -> None:
    """Write the run-control flag file (cancel / resume requests)."""
    save_json(run_dir / RUN_CONTROL_FILE, payload)


def read_run_control(run_dir: Path) -> dict[str, Any]:
    """Read the run-control flag file. Empty dict if absent."""
    return load_json(run_dir / RUN_CONTROL_FILE, {})
