"""SQLite-backed model + promotion registry.

Two tables:
    models       (id PK, run_id, template_id, template_label,
                  metrics_json, hparams_json, checkpoint_dir,
                  created_at, status — 'candidate'|'promoted'|'demoted')
    promotions   (id PK, model_id FK, status — 'open'|'approved'|'rejected'|'promoted',
                  gates_json, audit_json, comment, created_at, decided_at)

`gates_json` is the snapshot of the gate evaluation at promotion-request
time. `audit_json` is the append-only log of state changes.

A model becomes `promoted` only when every gate passes AND a promotion
record transitions to `promoted`. A second promotion event automatically
demotes the previous prod model (so there's always at most one).

Single-process SQLite is fine for the dev server. Multi-process deployment
would need a real DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path

REGISTRY_DB = Path(os.environ.get(
    "PROTEOSPHERE_V2_REGISTRY",
    str(Path.home() / ".proteosphere_v2" / "registry.sqlite"),
))

_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    REGISTRY_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(REGISTRY_DB), isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS models (
            id              TEXT PRIMARY KEY,
            run_id          TEXT NOT NULL UNIQUE,
            template_id     TEXT NOT NULL,
            template_label  TEXT,
            metrics_json    TEXT NOT NULL,
            hparams_json    TEXT NOT NULL,
            checkpoint_dir  TEXT,
            created_at      REAL NOT NULL,
            status          TEXT NOT NULL  -- candidate | promoted | demoted
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS promotions (
            id           TEXT PRIMARY KEY,
            model_id     TEXT NOT NULL REFERENCES models(id),
            status       TEXT NOT NULL,
            gates_json   TEXT NOT NULL,
            audit_json   TEXT NOT NULL,
            comment      TEXT,
            created_at   REAL NOT NULL,
            decided_at   REAL
        )
    """)
    return con


# ── Gate config ────────────────────────────────────────────────────────
# Each gate has: id, label, kind ('metric_floor'|'metric_ceiling'),
# metric (key in summary), threshold, severity ('blocker'|'warning').
# A 'blocker' gate failing means promotion is refused.
DEFAULT_GATES = [
    {"id": "min_test_pearson",  "label": "Test Pearson ≥ 0.50", "kind": "metric_floor",   "metric": "test_pearson",  "threshold": 0.50, "severity": "blocker"},
    {"id": "max_test_rmse",     "label": "Test RMSE ≤ 1.00",    "kind": "metric_ceiling", "metric": "test_rmse",     "threshold": 1.00, "severity": "blocker"},
    {"id": "min_test_ci",       "label": "Test CI ≥ 0.70",       "kind": "metric_floor",   "metric": "test_ci",       "threshold": 0.70, "severity": "blocker"},
    {"id": "min_auc_pki6",      "label": "AUC (pKi≥6) ≥ 0.75", "kind": "metric_floor", "metric": "test_auc_pki6", "threshold": 0.75, "severity": "warning"},
    {"id": "beat_current_prod", "label": "Beats current prod on test Pearson", "kind": "beat_current_prod", "metric": "test_pearson", "severity": "blocker"},
]


def evaluate_gates(metrics: dict, *, current_prod_metrics: dict | None = None) -> list[dict]:
    """Evaluate the default gate set against a metrics dict. Returns a list
    of { id, label, severity, passed, detail }.
    """
    out: list[dict] = []
    for g in DEFAULT_GATES:
        v = metrics.get(g["metric"])
        passed = False
        detail = "metric missing"
        if g["kind"] == "metric_floor" and isinstance(v, (int, float)):
            passed = v >= g["threshold"]
            detail = f"value = {v:.4f}, need ≥ {g['threshold']}"
        elif g["kind"] == "metric_ceiling" and isinstance(v, (int, float)):
            passed = v <= g["threshold"]
            detail = f"value = {v:.4f}, need ≤ {g['threshold']}"
        elif g["kind"] == "beat_current_prod":
            if current_prod_metrics is None:
                passed = True
                detail = "no current prod — first promotion bypasses this gate"
            else:
                prod_v = current_prod_metrics.get(g["metric"])
                if isinstance(v, (int, float)) and isinstance(prod_v, (int, float)):
                    passed = v > prod_v
                    detail = f"candidate {v:.4f} vs prod {prod_v:.4f}"
                else:
                    detail = "current prod metrics incomplete"
        out.append({
            "id": g["id"], "label": g["label"], "severity": g["severity"],
            "passed": passed, "detail": detail,
        })
    return out


# ── CRUD ───────────────────────────────────────────────────────────────

def register_model(*, run_id: str, template_id: str, template_label: str | None,
                   metrics: dict, hparams: dict, checkpoint_dir: str) -> dict:
    """Insert (or upsert) a model row. Idempotent on run_id."""
    with _lock:
        con = _conn()
        try:
            existing = con.execute("SELECT * FROM models WHERE run_id = ?", (run_id,)).fetchone()
            if existing:
                return _row_to_model(existing)
            mid = "model_" + uuid.uuid4().hex[:10]
            con.execute(
                "INSERT INTO models (id, run_id, template_id, template_label, "
                "metrics_json, hparams_json, checkpoint_dir, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, run_id, template_id, template_label,
                 json.dumps(metrics), json.dumps(hparams), checkpoint_dir,
                 time.time(), "candidate"),
            )
            row = con.execute("SELECT * FROM models WHERE id = ?", (mid,)).fetchone()
            return _row_to_model(row)
        finally:
            con.close()


def get_model(model_id: str) -> dict | None:
    with _lock:
        con = _conn()
        try:
            row = con.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
            return _row_to_model(row) if row else None
        finally:
            con.close()


def get_model_by_run(run_id: str) -> dict | None:
    with _lock:
        con = _conn()
        try:
            row = con.execute("SELECT * FROM models WHERE run_id = ?", (run_id,)).fetchone()
            return _row_to_model(row) if row else None
        finally:
            con.close()


def list_models(limit: int = 50) -> list[dict]:
    with _lock:
        con = _conn()
        try:
            rows = con.execute("SELECT * FROM models ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [_row_to_model(r) for r in rows]
        finally:
            con.close()


def current_prod_model() -> dict | None:
    with _lock:
        con = _conn()
        try:
            row = con.execute("SELECT * FROM models WHERE status = 'promoted' ORDER BY created_at DESC LIMIT 1").fetchone()
            return _row_to_model(row) if row else None
        finally:
            con.close()


def _row_to_model(row) -> dict:
    return {
        "id":             row["id"],
        "run_id":         row["run_id"],
        "template_id":    row["template_id"],
        "template_label": row["template_label"],
        "metrics":        json.loads(row["metrics_json"]),
        "hparams":        json.loads(row["hparams_json"]),
        "checkpoint_dir": row["checkpoint_dir"],
        "created_at":     row["created_at"],
        "status":         row["status"],
    }


def create_promotion_request(model_id: str, comment: str | None = None) -> dict:
    """Open a promotion request and evaluate gates."""
    model = get_model(model_id)
    if not model:
        raise ValueError(f"unknown model_id {model_id}")
    prod = current_prod_model()
    prod_metrics = prod["metrics"] if prod else None
    gates = evaluate_gates(model["metrics"], current_prod_metrics=prod_metrics)
    audit = [{"at": time.time(), "actor": "system", "event": "opened",
              "detail": f"gates: {sum(1 for g in gates if g['passed'])}/{len(gates)} passing"}]
    pid = "promo_" + uuid.uuid4().hex[:10]
    with _lock:
        con = _conn()
        try:
            con.execute(
                "INSERT INTO promotions (id, model_id, status, gates_json, audit_json, comment, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (pid, model_id, "open", json.dumps(gates), json.dumps(audit), comment, time.time()),
            )
            row = con.execute("SELECT * FROM promotions WHERE id = ?", (pid,)).fetchone()
            return _row_to_promotion(row)
        finally:
            con.close()


def get_promotion(promotion_id: str) -> dict | None:
    with _lock:
        con = _conn()
        try:
            row = con.execute("SELECT * FROM promotions WHERE id = ?", (promotion_id,)).fetchone()
            return _row_to_promotion(row) if row else None
        finally:
            con.close()


def list_promotions(*, model_id: str | None = None) -> list[dict]:
    with _lock:
        con = _conn()
        try:
            if model_id:
                rows = con.execute("SELECT * FROM promotions WHERE model_id = ? ORDER BY created_at DESC", (model_id,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM promotions ORDER BY created_at DESC LIMIT 50").fetchall()
            return [_row_to_promotion(r) for r in rows]
        finally:
            con.close()


def decide_promotion(promotion_id: str, *, approve: bool, actor: str = "user", note: str | None = None) -> dict:
    """Approve or reject. Approve only succeeds if all blocker gates pass.
    On approve, the model becomes 'promoted' (and any prior prod becomes 'demoted').
    """
    promo = get_promotion(promotion_id)
    if not promo:
        raise ValueError(f"unknown promotion {promotion_id}")
    if promo["status"] != "open":
        raise ValueError(f"promotion {promotion_id} is already {promo['status']}")
    model = get_model(promo["model_id"])
    if not model:
        raise ValueError(f"underlying model {promo['model_id']} is gone")

    gates = promo["gates"]
    blockers_passing = all(g["passed"] for g in gates if g["severity"] == "blocker")

    audit = list(promo["audit"])
    now = time.time()
    if approve:
        if not blockers_passing:
            new_status = "rejected"
            audit.append({"at": now, "actor": actor, "event": "rejected_blocker_failed",
                          "detail": "Attempted to approve but at least one blocker gate is failing."})
        else:
            new_status = "promoted"
            audit.append({"at": now, "actor": actor, "event": "approved",
                          "detail": note or "Approved by reviewer."})
    else:
        new_status = "rejected"
        audit.append({"at": now, "actor": actor, "event": "rejected", "detail": note or "Rejected by reviewer."})

    with _lock:
        con = _conn()
        try:
            con.execute(
                "UPDATE promotions SET status = ?, audit_json = ?, decided_at = ? WHERE id = ?",
                (new_status, json.dumps(audit), now, promotion_id),
            )
            if new_status == "promoted":
                # Demote previous prod (if any), then promote this model.
                con.execute("UPDATE models SET status = 'demoted' WHERE status = 'promoted'")
                con.execute("UPDATE models SET status = 'promoted' WHERE id = ?", (model["id"],))
            row = con.execute("SELECT * FROM promotions WHERE id = ?", (promotion_id,)).fetchone()
            return _row_to_promotion(row)
        finally:
            con.close()


def _row_to_promotion(row) -> dict:
    return {
        "id":          row["id"],
        "model_id":    row["model_id"],
        "status":      row["status"],
        "gates":       json.loads(row["gates_json"]),
        "audit":       json.loads(row["audit_json"]),
        "comment":     row["comment"],
        "created_at":  row["created_at"],
        "decided_at":  row["decided_at"],
    }
