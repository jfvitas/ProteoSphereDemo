"""In-memory run registry for the v2 backend.

Each Run holds:
    * a snapshot of the launch payload (effective config + hparams)
    * a status (queued | running | completed | cancelled | failed)
    * a per-stage progress object (epoch, batch, total)
    * a cancel event the worker thread polls
    * a per-subscriber queue map for SSE streaming
    * a tail of recent events (last 500) so a fresh subscriber gets
      backfill without holding the writer back

This is intentionally in-process and ephemeral — restarting the server
loses runs. Persisting to disk is a future concern.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from queue import Queue, Empty
from typing import Any, Iterator


# ── Event envelope ─────────────────────────────────────────────────────
# Every event is a small dict with a `type` discriminator.
#   { type:"status",  status, at }
#   { type:"epoch",   epoch, total_epochs, train_loss, val_loss, val_pearson, val_rmse, lr, elapsed_s, eta_s }
#   { type:"batch",   epoch, batch, total_batches, loss }
#   { type:"log",     level, text }
#   { type:"final",   best_val_pearson, best_val_rmse, test_pearson, test_rmse }
# All events also carry `seq` (monotonic per-run) and `t` (server time ms).


@dataclass
class Run:
    run_id: str
    template_id: str
    effective_config: dict
    hparams: dict
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    failure: str | None = None
    summary: dict = field(default_factory=dict)
    # Full results bundle populated by training.compute_results_summary on
    # completion. Shape documented there. None while the run is in-flight.
    results: dict | None = None
    # Event bus
    _seq: int = 0
    _subscribers: list[Queue] = field(default_factory=list)
    _backfill: list[dict] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    BACKFILL_MAX: int = 500

    def emit(self, event: dict) -> None:
        """Push an event to every subscriber + backfill tail."""
        with self._lock:
            self._seq += 1
            ev = {**event, "seq": self._seq, "t": int(time.time() * 1000)}
            self._backfill.append(ev)
            if len(self._backfill) > self.BACKFILL_MAX:
                self._backfill = self._backfill[-self.BACKFILL_MAX:]
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(ev)
            except Exception:
                # Subscriber's queue is full or closed — drop.
                pass

    def subscribe(self) -> tuple[Queue, list[dict]]:
        """Add a subscriber. Returns its queue and a snapshot of recent
        events the subscriber should replay before listening live.
        """
        q: Queue = Queue(maxsize=2000)
        with self._lock:
            backfill = list(self._backfill)
            self._subscribers.append(q)
        return q, backfill

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def set_status(self, status: str, **fields: Any) -> None:
        self.status = status
        for k, v in fields.items():
            setattr(self, k, v)
        self.emit({"type": "status", "status": status, **fields})

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "template_id": self.template_id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure": self.failure,
            "summary": self.summary,
            "hparams": self.hparams,
            "template_label": self.effective_config.get("template_label"),
            "n_modules": len(self.effective_config.get("nodes", [])),
            "n_edges": len(self.effective_config.get("edges", [])),
        }


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def create_run(self, *, template_id: str, effective_config: dict, hparams: dict) -> Run:
        run_id = "run_v2_" + uuid.uuid4().hex[:10]
        run = Run(
            run_id=run_id,
            template_id=template_id,
            effective_config=effective_config,
            hparams=hparams,
        )
        with self._lock:
            self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def list(self, limit: int = 50) -> list[dict]:
        with self._lock:
            runs = list(self._runs.values())
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return [r.to_dict() for r in runs[:limit]]

    def cancel(self, run_id: str) -> bool:
        run = self.get(run_id)
        if not run:
            return False
        run.cancel_event.set()
        return True


_REGISTRY = RunRegistry()


def get_registry() -> RunRegistry:
    return _REGISTRY


# ── SSE formatting helpers ────────────────────────────────────────────

def sse_format(event: dict) -> str:
    """Render an event as a single SSE message (already includes \\n\\n)."""
    import json
    return f"data: {json.dumps(event, separators=(',', ':'))}\n\n"


def iter_run_events(run: Run, *, after_seq: int = 0, idle_timeout_s: float = 30.0) -> Iterator[str]:
    """Yield SSE strings for one subscriber. Replays backfill first,
    then blocks on the live queue. Terminates after `idle_timeout_s`
    of nothing (the client will reconnect) or when the run finishes.
    """
    q, backfill = run.subscribe()
    try:
        for ev in backfill:
            if ev["seq"] > after_seq:
                yield sse_format(ev)
        # Heartbeat lets proxies/intermediaries know we're still here.
        yield ": heartbeat\n\n"
        while True:
            try:
                ev = q.get(timeout=idle_timeout_s)
            except Empty:
                # Idle — emit heartbeat so the client knows we're alive
                yield ": heartbeat\n\n"
                if run.status in ("completed", "cancelled", "failed"):
                    return
                continue
            yield sse_format(ev)
            # Stop streaming the moment the run terminates.
            if ev.get("type") == "status" and ev.get("status") in (
                "completed", "cancelled", "failed"
            ):
                return
    finally:
        run.unsubscribe(q)
