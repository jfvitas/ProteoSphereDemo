"""Hyperparameter sweep loop using Optuna.

A sweep is a set of training trials over a search space. Each trial
spawns one training run with its proposed params, watches the
``best_val_pearson`` (or AUC for binary tasks) at the end, and returns
that as the trial's score. Optuna's TPE/Random/Grid samplers + Hyperband
pruner steer the search.

Lifecycle:
    1. POST ``/api/v2/pipeline/sweep`` with a base config + search space
    2. Backend creates a SweepRun (similar to Run but parent of N child Runs)
    3. Optuna's ask/tell loop runs trials in this Python process
    4. Each trial's metrics stream back via the standard SSE channel
    5. Best trial's params + metrics are persisted to the sweep summary

The sweep itself does NOT call out to a Slurm-style cluster — for a
single-machine workstation the in-process trial loop is enough. Move
to ``optuna.integration.ddp`` when the cluster lands.

Search-space spec (JSON-friendly, easy to construct from the GUI):

    {
      "epochs":       {"type": "int",   "low": 5,   "high": 30, "step": 5},
      "batch_size":   {"type": "enum",  "values": [32, 64, 128]},
      "lr":           {"type": "float", "low": 1e-5, "high": 1e-2, "log": true},
      "weight_decay": {"type": "float", "low": 0.0,  "high": 0.1}
    }
"""

from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


# ── Sweep state model ────────────────────────────────────────────────

@dataclass
class SweepTrial:
    """One trial inside a sweep."""
    trial_id: int
    run_id: Optional[str] = None     # populated once the training Run is created
    params: dict = field(default_factory=dict)
    score: Optional[float] = None
    state: str = "queued"            # queued | running | completed | failed | pruned
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    failure: Optional[str] = None


@dataclass
class SweepState:
    """The parent state for a whole sweep."""
    sweep_id: str
    base_config: dict
    search_space: dict
    sampler: str = "tpe"            # tpe | random | grid
    pruner: str = "hyperband"       # hyperband | median | none
    n_trials: int = 12
    metric: str = "best_val_pearson"
    direction: str = "maximize"     # maximize | minimize
    trials: list[SweepTrial] = field(default_factory=list)
    best_trial: Optional[SweepTrial] = None
    state: str = "queued"            # queued | running | completed | failed
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    thread: Optional[threading.Thread] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict:
        # Drop the Thread + Event (not JSON-friendly); keep the rest.
        return {
            "sweep_id":      self.sweep_id,
            "base_config":   self.base_config,
            "search_space":  self.search_space,
            "sampler":       self.sampler,
            "pruner":        self.pruner,
            "n_trials":      self.n_trials,
            "metric":        self.metric,
            "direction":     self.direction,
            "state":         self.state,
            "created_at":    self.created_at,
            "finished_at":   self.finished_at,
            "trials":        [
                {
                    "trial_id":    t.trial_id,
                    "run_id":      t.run_id,
                    "params":      t.params,
                    "score":       t.score,
                    "state":       t.state,
                    "started_at":  t.started_at,
                    "finished_at": t.finished_at,
                    "failure":     t.failure,
                }
                for t in self.trials
            ],
            "best_trial": (
                {
                    "trial_id": self.best_trial.trial_id,
                    "params":   self.best_trial.params,
                    "score":    self.best_trial.score,
                    "run_id":   self.best_trial.run_id,
                }
                if self.best_trial else None
            ),
        }


# Sweep registry — keyed by sweep_id.
_SWEEPS: dict[str, SweepState] = {}
_SWEEP_LOCK = threading.Lock()


def get_sweep(sweep_id: str) -> Optional[SweepState]:
    return _SWEEPS.get(sweep_id)


def list_sweeps() -> list[dict]:
    with _SWEEP_LOCK:
        return [s.to_dict() for s in sorted(_SWEEPS.values(), key=lambda s: -s.created_at)]


def cancel_sweep(sweep_id: str) -> bool:
    s = _SWEEPS.get(sweep_id)
    if not s:
        return False
    s.cancel_event.set()
    return True


# ── Sampling logic ───────────────────────────────────────────────────

def _sample_params_from_optuna(trial, search_space: dict) -> dict:
    """Build a params dict from an Optuna trial + JSON search space."""
    out = {}
    for key, spec in search_space.items():
        kind = spec.get("type", "float")
        if kind == "int":
            step = spec.get("step")
            out[key] = trial.suggest_int(key, int(spec["low"]), int(spec["high"]),
                                         step=int(step) if step else 1)
        elif kind == "float":
            log = bool(spec.get("log", False))
            out[key] = trial.suggest_float(key, float(spec["low"]), float(spec["high"]), log=log)
        elif kind == "enum":
            out[key] = trial.suggest_categorical(key, spec["values"])
        elif kind == "bool":
            out[key] = trial.suggest_categorical(key, [True, False])
        else:
            raise ValueError(f"Unknown search-space type '{kind}' for key '{key}'")
    return out


def _build_sampler(sampler_name: str):
    import optuna
    if sampler_name == "random":
        return optuna.samplers.RandomSampler(seed=4192)
    if sampler_name == "grid":
        # Optuna's GridSampler needs an explicit grid — caller will skip this
        # branch unless they want it. For now fall back to TPE.
        return optuna.samplers.TPESampler(seed=4192)
    return optuna.samplers.TPESampler(seed=4192)


def _build_pruner(pruner_name: str):
    import optuna
    if pruner_name == "median":
        return optuna.pruners.MedianPruner(n_startup_trials=3, n_warmup_steps=2)
    if pruner_name == "none":
        return optuna.pruners.NopPruner()
    return optuna.pruners.HyperbandPruner(min_resource=2, max_resource="auto")


# ── Trial runner ─────────────────────────────────────────────────────

# Thread-local that holds the active Optuna trial. The trainer reads
# this in its epoch loop and calls trial.report() + should_prune() so
# the Hyperband pruner can early-terminate trials whose intermediate
# values look hopeless. Cleared after every trial.
_OPTUNA_TRIAL_LOCAL = threading.local()


def _run_one_trial(sweep: SweepState, trial_obj, trial_record: SweepTrial,
                   train_run_fn: Callable) -> float:
    """Run one trial. Spawns a training Run, watches its summary, returns
    the score. ``train_run_fn`` is the trainer entry point (defaulted at
    sweep launch time).
    """
    from .registry import get_registry
    params = _sample_params_from_optuna(trial_obj, sweep.search_space)
    trial_record.params = params
    trial_record.state = "running"
    trial_record.started_at = time.time()

    # Build the per-trial run config by merging base + trial params.
    base = sweep.base_config
    effective_config = dict(base.get("effective_config") or {})
    hparams = dict(base.get("hparams") or {})
    hparams.update(params)
    hparams["_sweep_id"] = sweep.sweep_id
    hparams["_sweep_trial_id"] = trial_record.trial_id

    registry = get_registry()
    run = registry.create_run(
        template_id=effective_config.get("template_id") or base.get("template_id") or "deepdta",
        effective_config=effective_config,
        hparams=hparams,
    )
    trial_record.run_id = run.run_id
    run.emit({"type": "log", "level": "info",
              "text": (f"Trial {trial_record.trial_id} of sweep {sweep.sweep_id} — "
                       f"params={params}")})

    # Expose the Optuna trial to the trainer's epoch loop so it can
    # report intermediate values + check should_prune. Cleared in the
    # finally block so the next trial's trainer doesn't see a stale ref.
    _OPTUNA_TRIAL_LOCAL.trial = trial_obj
    _OPTUNA_TRIAL_LOCAL.metric_key = sweep.metric
    try:
        try:
            train_run_fn(run)
        except Exception as exc:
            # If the trainer raised optuna.TrialPruned (via the per-epoch
            # should_prune check), translate that into a pruned trial
            # state and re-raise so Optuna's study counts it as pruned.
            try:
                import optuna
                if isinstance(exc, optuna.TrialPruned):
                    trial_record.state = "pruned"
                    trial_record.failure = "pruned by Hyperband"
                    trial_record.finished_at = time.time()
                    run.emit({"type": "log", "level": "warn",
                              "text": (f"Trial {trial_record.trial_id} pruned at "
                                       f"epoch {getattr(exc, 'last_step', '?')}.")})
                    raise
            except ImportError:
                pass
            # Non-pruned exception — record + re-raise.
            trial_record.state = "failed"
            trial_record.failure = f"{type(exc).__name__}: {exc}"
            trial_record.finished_at = time.time()
            run.emit({"type": "log", "level": "error",
                      "text": f"Trial failed: {trial_record.failure}"})
            raise
        # Pull the score from the run summary.
        metric_key = sweep.metric
        score = run.summary.get(metric_key)
        if score is None:
            # Fall back to the standard names.
            for k in ("best_val_pearson", "best_val_auc", "test_pearson", "test_auc"):
                if k in run.summary:
                    score = run.summary[k]
                    break
        if score is None:
            raise RuntimeError(f"Run finished but no '{metric_key}' in summary.")
        trial_record.score = float(score)
        trial_record.state = "completed"
        trial_record.finished_at = time.time()
        return float(score)
    finally:
        _OPTUNA_TRIAL_LOCAL.trial = None
        _OPTUNA_TRIAL_LOCAL.metric_key = None


def _drive_sweep(sweep: SweepState) -> None:
    """The actual sweep loop. Runs in a daemon thread."""
    import optuna
    from .training import train_run

    sweep.state = "running"
    direction = "maximize" if sweep.direction != "minimize" else "minimize"
    study = optuna.create_study(
        direction=direction,
        sampler=_build_sampler(sweep.sampler),
        pruner=_build_pruner(sweep.pruner),
        study_name=sweep.sweep_id,
    )

    for tid in range(sweep.n_trials):
        if sweep.cancel_event.is_set():
            break
        rec = SweepTrial(trial_id=tid)
        sweep.trials.append(rec)

        def _objective(trial):
            return _run_one_trial(sweep, trial, rec, train_run)

        try:
            study.optimize(_objective, n_trials=1, catch=())
        except Exception as exc:  # noqa: BLE001
            rec.failure = f"{type(exc).__name__}: {exc}"
            rec.state = "failed"

    # Find the best trial.
    completed = [t for t in sweep.trials if t.state == "completed" and t.score is not None]
    if completed:
        sweep.best_trial = max(completed, key=lambda t: t.score) if direction == "maximize" \
                          else min(completed, key=lambda t: t.score)
    sweep.state = "completed" if completed else "failed"
    sweep.finished_at = time.time()


def launch_sweep(
    *,
    base_config: dict,
    search_space: dict,
    n_trials: int = 12,
    sampler: str = "tpe",
    pruner: str = "hyperband",
    metric: str = "best_val_pearson",
    direction: str = "maximize",
) -> SweepState:
    """Create and start a sweep. Returns the SweepState immediately;
    the trials run in a background daemon thread.
    """
    import uuid
    sweep_id = "sweep_" + uuid.uuid4().hex[:10]
    sweep = SweepState(
        sweep_id=sweep_id,
        base_config=base_config,
        search_space=search_space,
        sampler=sampler,
        pruner=pruner,
        n_trials=int(n_trials),
        metric=metric,
        direction=direction,
    )
    with _SWEEP_LOCK:
        _SWEEPS[sweep_id] = sweep
    sweep.thread = threading.Thread(
        target=_drive_sweep, args=(sweep,),
        name=f"sweep-{sweep_id}", daemon=True,
    )
    sweep.thread.start()
    return sweep
