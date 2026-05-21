"""ProteoSphere Model Studio v2 — real PyTorch training backend.

This module hosts the v2 pipeline launch + run lifecycle. The "v2" prefix
distinguishes it from the legacy model_studio.runtime which uses a different
pipeline-spec shape. Only DeepDTA is wired end-to-end in this first iteration;
other template ids return 501 from the launch handler with a clear message.

Public surface:
    register_handlers(handler_class)
        Adds /api/v2/pipeline/* routes to an existing http.server-style
        request handler. Called from api/model_studio/server.py.

    launch(payload) -> run_id
    get_status(run_id) -> dict
    cancel(run_id) -> bool
    stream_events(run_id) -> generator yielding SSE-formatted strings

Design notes:
- Runs live in-memory (this is a single-process dev server). Future
  multi-process deployments will need a Redis/SQLite-backed registry.
- Each run owns its own worker thread + cancel event. The training loop
  polls the cancel event between batches.
- Events (metrics, logs, status changes) are pushed to a per-run queue
  consumed by SSE subscribers. Multiple subscribers per run are supported
  via per-subscriber queues.
"""

# Eager imports are deliberately minimal — importing this package shouldn't
# pull torch / CUDA / 30+ seconds of warmup. Submodules that need handlers /
# training do their own targeted import.
from .registry import RunRegistry, get_registry


def register_handlers(handler_class):
    """Lazy import so ``import api.model_studio.v2`` doesn't load torch.

    Called from server.py at startup; importing handlers also loads the
    inference + training modules which pull torch (5+ seconds even when
    nothing actually trains). The legacy ingest CLI doesn't want torch at all.
    """
    from .handlers import register_handlers as _real
    return _real(handler_class)


__all__ = ["RunRegistry", "get_registry", "register_handlers"]
