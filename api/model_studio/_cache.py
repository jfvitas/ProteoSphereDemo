"""TTL-based memoization for functions that read mutating on-disk state.

The Model Studio runtime contains many builder functions whose results
derive from JSON files under ``artifacts/runtime/`` and ``data/reports/``.
The previous implementation used ``functools.lru_cache``, which is
permanent for the process lifetime. As soon as ``launch_run`` or
``build_training_set`` mutated one of those files, every cached view
served stale data until the server restarted -- a class of bug the May
2026 review identified across 22 wrappers.

This module provides:

* :func:`ttl_cache` -- a drop-in decorator that caches by argument tuple
  and *expires* each entry after a fixed wall-clock interval.
* :func:`invalidate_runtime_caches` -- nukes every registered cache.
  Call from any writer path that mutates state the caches depend on.

The TTL default (30 s) is intentionally generous: it caps cold-start
amplification under load while keeping the inevitable post-write
"why doesn't my UI update?" window human-scale.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any


_DEFAULT_TTL_SECONDS = 30.0
_REGISTRY: list[Callable[[], None]] = []
_REGISTRY_LOCK = threading.Lock()


def _make_key(args: tuple, kwargs: dict) -> tuple[Hashable, ...]:
    """Build a hashable cache key. Mirrors ``lru_cache`` semantics: all
    positional args + sorted kwargs. Unhashable types fall through to
    the original TypeError so the misuse is loud, not silent."""
    return (args, tuple(sorted(kwargs.items())))


def ttl_cache(maxsize: int = 8, ttl_seconds: float = _DEFAULT_TTL_SECONDS):
    """Memoize ``fn`` by argument tuple for at most ``ttl_seconds``.

    Returns a wrapper with ``.cache_clear`` (manual eviction) and
    ``__wrapped__`` (introspection). The cache is bounded to
    ``maxsize`` entries with FIFO eviction, like a poor man's LRU.

    The wrapped function is registered with the module-level registry
    so :func:`invalidate_runtime_caches` can clear all caches at once
    after a writer mutates state.
    """
    def decorator(fn):
        store: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        lock = threading.Lock()

        def clear() -> None:
            with lock:
                store.clear()

        with _REGISTRY_LOCK:
            _REGISTRY.append(clear)

        def wrapper(*args, **kwargs):
            key = _make_key(args, kwargs)
            now = time.monotonic()
            with lock:
                hit = store.get(key)
                if hit is not None:
                    timestamp, value = hit
                    if now - timestamp < ttl_seconds:
                        store.move_to_end(key)
                        return value
                    store.pop(key, None)
            # Compute outside the per-cache lock so a slow miss doesn't
            # block a different cache key hitting the same wrapper.
            value = fn(*args, **kwargs)
            with lock:
                store[key] = (now, value)
                while len(store) > maxsize:
                    store.popitem(last=False)
            return value

        wrapper.cache_clear = clear
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        wrapper.__name__ = getattr(fn, "__name__", "ttl_cached")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


def invalidate_runtime_caches() -> None:
    """Force every ``ttl_cache``-wrapped function to recompute on next call.

    Call this from any writer path that mutates on-disk state the
    caches read from (launch_run, build_training_set, cancel_run,
    save_pipeline_spec, ...). Cheap; no I/O.
    """
    with _REGISTRY_LOCK:
        for clear in _REGISTRY:
            clear()
