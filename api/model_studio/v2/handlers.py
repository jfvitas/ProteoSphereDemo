"""HTTP handlers for /api/v2/pipeline/* — mounted into the existing
ProteoSphere server (api/model_studio/server.py).

Routes:
    POST /api/v2/pipeline/launch              → start a run
    GET  /api/v2/pipeline/runs                → list runs
    GET  /api/v2/pipeline/runs/{run_id}       → run snapshot
    GET  /api/v2/pipeline/runs/{run_id}/stream → SSE event stream
    POST /api/v2/pipeline/runs/{run_id}/cancel → set cancel event
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
from http import HTTPStatus

from .registry import get_registry, iter_run_events
# Lazy: training + inference both import torch which is multi-second on boot.
# Imported on first launch / predict / compare instead.
from . import registry_db as model_db

# Reference Library endpoints — paginated browse over the v2 catalog
# plus a couple of utility actions wired to GUI buttons. See
# api/model_studio/v2/library.py for the row shape per family.
_LIBRARY_ROWS_RE         = re.compile(r"^/api/v2/library/(?P<family>[a-z_]+)$")
_LIBRARY_SCHEMA_RE       = re.compile(r"^/api/v2/library/_schema\.sql$")
_LIBRARY_SOURCE_URL_RE   = re.compile(r"^/api/v2/library/_source_url$")
_INGEST_STATUS_RE  = re.compile(r"^/api/v2/ingest/status$")
_INGEST_SOURCES_RE = re.compile(r"^/api/v2/ingest/sources$")
_INGEST_SUMMARY_RE = re.compile(r"^/api/v2/ingest/catalog$")
_SPLITS_LEAK_RE    = re.compile(r"^/api/v2/splits/leakage_report$")
_SYSTEM_GPU_RE         = re.compile(r"^/api/v2/system/gpu$")
_SYSTEM_HOST_RE        = re.compile(r"^/api/v2/system/host$")
_SYSTEM_USER_RE        = re.compile(r"^/api/v2/system/user$")
_SYSTEM_ROSETTA_RE     = re.compile(r"^/api/v2/system/rosetta$")
_SYSTEM_ROSETTA_INSTALL_RE = re.compile(r"^/api/v2/system/rosetta/install$")
_FEATURIZERS_LIST_RE   = re.compile(r"^/api/v2/featurizers$")
_FEATURIZER_DETAIL_RE  = re.compile(r"^/api/v2/featurizers/(?P<fid>[A-Za-z0-9_.]+)$")
_BLOCKS_LIST_RE        = re.compile(r"^/api/v2/blocks$")
_BLOCK_DETAIL_RE       = re.compile(r"^/api/v2/blocks/(?P<bid>[A-Za-z0-9_.]+)$")
_BLOCK_PRESETS_RE      = re.compile(r"^/api/v2/blocks/_presets$")

_LAUNCH_RE  = re.compile(r"^/api/v2/pipeline/launch$")
_SWEEP_LAUNCH_RE = re.compile(r"^/api/v2/pipeline/sweeps$")
_SWEEP_GET_RE    = re.compile(r"^/api/v2/pipeline/sweeps/(?P<sweep_id>[A-Za-z0-9_]+)$")
_SWEEP_CANCEL_RE = re.compile(r"^/api/v2/pipeline/sweeps/(?P<sweep_id>[A-Za-z0-9_]+)/cancel$")
_TEMPLATES_RE = re.compile(r"^/api/v2/pipeline/templates$")
_LIST_RE    = re.compile(r"^/api/v2/pipeline/runs$")
_RUN_RE     = re.compile(r"^/api/v2/pipeline/runs/(?P<run_id>[A-Za-z0-9_]+)$")
_STREAM_RE  = re.compile(r"^/api/v2/pipeline/runs/(?P<run_id>[A-Za-z0-9_]+)/stream$")
_CANCEL_RE  = re.compile(r"^/api/v2/pipeline/runs/(?P<run_id>[A-Za-z0-9_]+)/cancel$")
_RESULTS_RE = re.compile(r"^/api/v2/pipeline/runs/(?P<run_id>[A-Za-z0-9_]+)/results$")
_RESULTS_CSV_RE = re.compile(r"^/api/v2/pipeline/runs/(?P<run_id>[A-Za-z0-9_]+)/results\.csv$")
_PREDICT_RE = re.compile(r"^/api/v2/pipeline/runs/(?P<run_id>[A-Za-z0-9_]+)/predict$")
_COMPARE_RE = re.compile(r"^/api/v2/pipeline/compare$")
_MODELS_RE        = re.compile(r"^/api/v2/registry/models$")
_MODEL_RE         = re.compile(r"^/api/v2/registry/models/(?P<model_id>[A-Za-z0-9_]+)$")
_PROMOTIONS_RE    = re.compile(r"^/api/v2/registry/promotions$")
_PROMOTION_RE     = re.compile(r"^/api/v2/registry/promotions/(?P<promo_id>[A-Za-z0-9_]+)$")
_PROMOTION_DECIDE = re.compile(r"^/api/v2/registry/promotions/(?P<promo_id>[A-Za-z0-9_]+)/decide$")


def _read_body(handler) -> dict:
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0:
        return {}
    raw = handler.rfile.read(n)
    try:
        return json.loads(raw)
    except Exception:
        return {}


def _send_json(handler, status: int, body: dict) -> None:
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(payload)


def _send_sse_stream(handler, run_id: str, after_seq: int) -> None:
    registry = get_registry()
    run = registry.get(run_id)
    if not run:
        _send_json(handler, 404, {"error": "run_not_found", "run_id": run_id})
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-store, no-transform")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("X-Accel-Buffering", "no")  # tell nginx not to buffer
    handler.end_headers()
    try:
        for chunk in iter_run_events(run, after_seq=after_seq, idle_timeout_s=15.0):
            try:
                handler.wfile.write(chunk.encode("utf-8"))
                handler.wfile.flush()
            except (ConnectionError, BrokenPipeError):
                return
    except Exception:
        # Client disconnected mid-stream — that's fine.
        pass


# ── Route entry points called from server.py ──────────────────────────

def _parse_qs(path: str) -> dict[str, str]:
    """Tiny querystring parser; we only have at most 2 keys."""
    if "?" not in path:
        return {}
    qs = path.split("?", 1)[1]
    out: dict[str, str] = {}
    for kv in qs.split("&"):
        if not kv:
            continue
        k, _, v = kv.partition("=")
        out[k] = v
    return out


def handle_get(handler, path: str) -> bool:
    """Returns True if the path was a v2 route (handled), False otherwise."""
    # /compare carries a querystring, strip it for the regex match
    base = path.split("?", 1)[0]
    # GET /api/v2/pipeline/templates — the live "what does this server
    # actually support?" sanity endpoint. Useful for the GUI's pre-launch
    # check (does the running server know about the 'flow' template?) and
    # for diagnosing stale-server issues where the on-disk handlers.py is
    # ahead of the in-memory module. Returns the same supported set the
    # launch handler validates against, plus a build marker so the GUI
    # can flag if its expected feature isn't there yet.
    if _TEMPLATES_RE.match(base):
        from .models import TEMPLATE_BUILDERS
        supported = sorted(TEMPLATE_BUILDERS) + ["flow"]
        # Mark builds where "flow" is accepted by the handler. If the
        # running module pre-dates the flow exception this list will be
        # missing "flow", and that's exactly the stale-server signal the
        # GUI uses to nag the user to restart.
        _send_json(handler, 200, {
            "supported_templates": supported,
            "flow_compiler_available": True,
            "build": "v4-stage11+",
        })
        return True
    # ── Reference Library endpoints ─────────────────────────────────
    # Paginated browse over the v2 warehouse. The GUI's
    # gui/model_studio_web_v2/components/screen-library.jsx polls
    # these for every tab; see api/model_studio/v2/library.py for
    # per-family row shape + the catalog-vs-fixture fallback policy.
    m = _LIBRARY_ROWS_RE.match(base)
    if m:
        from .library import library_rows
        qs = _parse_qs(path)
        try:
            page = int(qs.get("page", "1"))
        except ValueError:
            page = 1
        try:
            per_page = int(qs.get("per_page", "50"))
        except ValueError:
            per_page = 50
        try:
            _send_json(handler, 200, library_rows(
                m.group("family"),
                q=qs.get("q", ""),
                page=page,
                per_page=per_page,
                tier=qs.get("tier", "any"),
            ))
        except Exception as exc:
            _send_json(handler, 500, {
                "error": "library_rows_failed",
                "detail": str(exc),
            })
        return True
    if _LIBRARY_SCHEMA_RE.match(base):
        # Real SQL download for the "Export schema" button. Returns the
        # warehouse's DuckDB CREATE statements (or a template when the
        # live catalog isn't accessible) as a text/plain attachment so
        # the browser downloads it instead of rendering inline.
        from .library import warehouse_schema_sql
        try:
            body = warehouse_schema_sql()
            handler.send_response(200)
            handler.send_header("Content-Type", "text/plain; charset=utf-8")
            handler.send_header(
                "Content-Disposition",
                "attachment; filename=warehouse_schema.sql",
            )
            handler.send_header("Content-Length", str(len(body.encode("utf-8"))))
            handler.end_headers()
            handler.wfile.write(body.encode("utf-8"))
        except Exception as exc:
            _send_json(handler, 500, {
                "error": "schema_export_failed",
                "detail": str(exc),
            })
        return True
    if _LIBRARY_SOURCE_URL_RE.match(base):
        # GET /api/v2/library/_source_url?family=...&payload=<json>
        # Returns the canonical upstream URL for a single row, or null
        # when no public anchor is known. Drives the DetailDrawer's
        # "View source" button.
        from .library import source_url as _src_url
        qs = _parse_qs(path)
        try:
            payload_raw = qs.get("payload", "{}")
            # URL-decode percent-escapes that the GUI sent.
            from urllib.parse import unquote
            payload_raw = unquote(payload_raw)
            row = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            row = {}
        url = _src_url(qs.get("family", ""), row)
        _send_json(handler, 200, {"url": url})
        return True

    # Ingest endpoints — read-only introspection of E:\ ingest state
    if _INGEST_STATUS_RE.match(base):
        from .ingest import get_state
        st = get_state()
        sm = st.summary()
        _send_json(handler, 200, {**sm, "sources": [s.__dict__ for s in st.list()]})
        return True
    if _INGEST_SOURCES_RE.match(base):
        from .ingest.manifest import MANIFEST
        from .ingest.parsers import list_implemented
        parsers = set(list_implemented())
        _send_json(handler, 200, {
            "items": [
                {**{k: v for k, v in s.__dict__.items()},
                 "parser_implemented": s.source_id in parsers}
                for s in MANIFEST
            ],
        })
        return True
    if _INGEST_SUMMARY_RE.match(base):
        from .ingest.catalog import summary as cat_summary
        try:
            _send_json(handler, 200, cat_summary())
        except Exception as exc:
            _send_json(handler, 500, {"error": "catalog_summary_failed", "detail": str(exc)})
        return True
    if _SPLITS_LEAK_RE.match(base):
        from .ingest.leakage import leakage_report
        # Forward GUI-side thresholds + relationship selection so the
        # endpoint becomes parameterisable (so a future, threshold-aware
        # leakage_report() doesn't need a second GUI roundtrip). For now
        # leakage_report() swallows whatever kwargs it doesn't recognise,
        # so the GET still succeeds even though the thresholds don't
        # affect the SQL yet.
        try:
            # _parse_qs returns {key: raw_value_string}. We coerce here so
            # leakage_report() receives typed values when threshold-aware
            # filtering lands. For now leakage_report() swallows whatever
            # kwargs it doesn't recognise (see ingest/leakage.py).
            qs = _parse_qs(path)
            kwargs: dict = {}
            if "prot_thresh" in qs:
                try: kwargs["prot_thresh"] = float(qs["prot_thresh"])
                except ValueError: pass
            if "lig_thresh" in qs:
                try: kwargs["lig_thresh"] = float(qs["lig_thresh"])
                except ValueError: pass
            if qs.get("merge_mode"):
                kwargs["merge_mode"] = qs["merge_mode"]
            if qs.get("relationships"):
                # The GUI sends a comma-joined CSV. URL-decode just enough
                # to handle %2C (encoded comma) so a single-relationship
                # request still parses correctly.
                raw = qs["relationships"].replace("%2C", ",").replace("%2c", ",")
                kwargs["relationships"] = [r for r in raw.split(",") if r]
            try:
                _send_json(handler, 200, leakage_report(**kwargs))
            except TypeError:
                # leakage_report() doesn't accept these kwargs yet — call
                # the zero-arg form so the GUI's recompute button still
                # gets a useful response.
                _send_json(handler, 200, leakage_report())
        except Exception as exc:
            _send_json(handler, 500, {"error": "leakage_report_failed", "detail": str(exc)})
        return True
    if _SYSTEM_GPU_RE.match(base):
        from .gpu_runtime import gpu_info
        try:
            _send_json(handler, 200, gpu_info())
        except Exception as exc:
            _send_json(handler, 500, {"error": "gpu_info_failed", "detail": str(exc)})
        return True
    if _SYSTEM_HOST_RE.match(base):
        from .gpu_runtime import host_info
        try:
            _send_json(handler, 200, host_info())
        except Exception as exc:
            _send_json(handler, 500, {"error": "host_info_failed", "detail": str(exc)})
        return True
    if _SYSTEM_USER_RE.match(base):
        from .user_identity import user_identity
        try:
            _send_json(handler, 200, user_identity())
        except Exception as exc:
            _send_json(handler, 500, {"error": "user_identity_failed", "detail": str(exc)})
        return True
    if _SYSTEM_ROSETTA_RE.match(base):
        from .rosetta_runtime import rosetta_status
        try:
            _send_json(handler, 200, rosetta_status())
        except Exception as exc:
            _send_json(handler, 500, {"error": "rosetta_status_failed", "detail": str(exc)})
        return True
    if _FEATURIZERS_LIST_RE.match(base):
        from . import featurizers
        try:
            _send_json(handler, 200, featurizers.catalog())
        except Exception as exc:
            _send_json(handler, 500, {"error": "featurizers_list_failed", "detail": str(exc)})
        return True
    m = _FEATURIZER_DETAIL_RE.match(base)
    if m:
        from . import featurizers
        spec = featurizers.get(m.group("fid"))
        if spec is None:
            _send_json(handler, 404, {"error": "featurizer_not_found", "id": m.group("fid")})
        else:
            _send_json(handler, 200, spec.to_catalog_entry())
        return True
    # Block registry — palette + role/impl swap targets for the new flow builder.
    # /api/v2/blocks               -> full catalog (roles + blocks + by_role)
    # /api/v2/blocks/_presets      -> template → block-composition presets
    # /api/v2/blocks/<block_id>    -> single block detail
    if _BLOCK_PRESETS_RE.match(base):
        from . import blocks as _blocks
        try:
            _send_json(handler, 200, _blocks.list_presets())
        except Exception as exc:
            _send_json(handler, 500, {"error": "block_presets_failed", "detail": str(exc)})
        return True
    if _BLOCKS_LIST_RE.match(base):
        from . import blocks as _blocks
        try:
            _send_json(handler, 200, _blocks.catalog())
        except Exception as exc:
            _send_json(handler, 500, {"error": "blocks_list_failed", "detail": str(exc)})
        return True
    m = _BLOCK_DETAIL_RE.match(base)
    if m:
        from . import blocks as _blocks
        spec = _blocks.get(m.group("bid"))
        if spec is None:
            _send_json(handler, 404, {"error": "block_not_found", "id": m.group("bid")})
        else:
            _send_json(handler, 200, spec.to_catalog_entry())
        return True
    if _COMPARE_RE.match(base):
        qs = _parse_qs(path)
        a, b = qs.get("a"), qs.get("b")
        if not a or not b:
            _send_json(handler, 400, {"error": "missing_a_or_b", "usage": "/api/v2/pipeline/compare?a=run_v2_…&b=run_v2_…"})
            return True
        try:
            from . import inference as v2_inference
            _send_json(handler, 200, v2_inference.compare_runs(a, b))
        except FileNotFoundError as exc:
            _send_json(handler, 404, {"error": "checkpoint_not_found", "detail": str(exc)})
        except Exception as exc:
            _send_json(handler, 500, {"error": "compare_failed", "detail": str(exc)})
        return True

    if _MODELS_RE.match(base):
        _send_json(handler, 200, {"items": model_db.list_models(), "current_prod": model_db.current_prod_model()})
        return True
    m = _MODEL_RE.match(base)
    if m:
        mod = model_db.get_model(m.group("model_id"))
        if not mod:
            _send_json(handler, 404, {"error": "model_not_found"})
        else:
            _send_json(handler, 200, {"model": mod, "promotions": model_db.list_promotions(model_id=mod["id"])})
        return True
    if _PROMOTIONS_RE.match(base):
        _send_json(handler, 200, {"items": model_db.list_promotions()})
        return True
    m = _PROMOTION_RE.match(base)
    if m:
        p = model_db.get_promotion(m.group("promo_id"))
        if not p:
            _send_json(handler, 404, {"error": "promotion_not_found"})
        else:
            _send_json(handler, 200, {"promotion": p, "model": model_db.get_model(p["model_id"])})
        return True
    m = _STREAM_RE.match(path)
    if m:
        after = 0
        # Standard SSE protocol — Last-Event-ID header replays from seq+1
        last_id = handler.headers.get("Last-Event-ID")
        if last_id and last_id.isdigit():
            after = int(last_id)
        _send_sse_stream(handler, m.group("run_id"), after_seq=after)
        return True

    m = _RESULTS_CSV_RE.match(path)
    if m:
        # CSV export of per-record predictions + the run summary. The
        # body is a single CSV with two sections (commented):
        #   # SUMMARY
        #   key,value
        #   benchmark,kiba
        #   best_val_pearson,0.62
        #   ...
        #   # PREDICTIONS
        #   index,y_true,y_pred,residual
        #   0,7.21,7.05,-0.16
        #   ...
        run = get_registry().get(m.group("run_id"))
        if not run:
            _send_json(handler, 404, {"error": "run_not_found"})
            return True
        if not run.results:
            _send_json(handler, 409, {
                "error": "results_not_ready",
                "status": run.status,
                "message": "Run hasn't produced results yet.",
            })
            return True
        results = run.results
        lines: list[str] = []
        lines.append("# SUMMARY")
        lines.append("key,value")
        for k, v in sorted(run.summary.items()):
            if isinstance(v, (list, dict)):
                continue   # skip nested structures
            sv = "" if v is None else str(v).replace("\n", " ").replace(",", ";")
            lines.append(f"{k},{sv}")
        lines.append("")
        lines.append("# PREDICTIONS")
        lines.append("index,y_true,y_pred,residual")
        # results may have y_true / y_pred OR scatter_inliers (normalised
        # pairs) — prefer raw arrays if present.
        y_true = results.get("y_true")
        y_pred = results.get("y_pred")
        if y_true is not None and y_pred is not None:
            for i, (t, p) in enumerate(zip(y_true, y_pred)):
                try:
                    tf, pf = float(t), float(p)
                    lines.append(f"{i},{tf:.6f},{pf:.6f},{(pf - tf):.6f}")
                except Exception:
                    continue
        body = "\n".join(lines).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/csv; charset=utf-8")
        handler.send_header("Content-Disposition",
                            f'attachment; filename="{m.group("run_id")}_results.csv"')
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
        return True

    m = _RESULTS_RE.match(path)
    if m:
        run = get_registry().get(m.group("run_id"))
        if not run:
            _send_json(handler, 404, {"error": "run_not_found"})
            return True
        if not run.results:
            _send_json(handler, 409, {
                "error": "results_not_ready",
                "status": run.status,
                "message": "Results aren't computed yet. The run must complete (or partially complete) first.",
            })
            return True
        _send_json(handler, 200, {
            "run_id": run.run_id,
            "template_id": run.template_id,
            "template_label": run.effective_config.get("template_label"),
            "status": run.status,
            "summary": run.summary,
            "results": run.results,
            "effective_config": run.effective_config,
        })
        return True

    m = _RUN_RE.match(path)
    if m:
        registry = get_registry()
        run = registry.get(m.group("run_id"))
        if not run:
            _send_json(handler, 404, {"error": "run_not_found"})
            return True
        _send_json(handler, 200, {"run": run.to_dict()})
        return True

    if _LIST_RE.match(path):
        _send_json(handler, 200, {"items": get_registry().list(limit=50)})
        return True
    # Sweep list / detail GETs
    if base == "/api/v2/pipeline/sweeps":
        from . import sweeps as _sweeps
        _send_json(handler, 200, {"items": _sweeps.list_sweeps()})
        return True
    m = _SWEEP_GET_RE.match(base)
    if m:
        from . import sweeps as _sweeps
        s = _sweeps.get_sweep(m.group("sweep_id"))
        if not s:
            _send_json(handler, 404, {"error": "sweep_not_found"})
        else:
            _send_json(handler, 200, {"sweep": s.to_dict()})
        return True
    return False


def handle_post(handler, path: str) -> bool:
    base = path.split("?", 1)[0]
    if _LAUNCH_RE.match(base):
        _handle_launch(handler)
        return True
    if _SWEEP_LAUNCH_RE.match(base):
        body = _read_body(handler)
        try:
            from . import sweeps as _sweeps
            sweep = _sweeps.launch_sweep(
                base_config=body.get("base_config") or {},
                search_space=body.get("search_space") or {},
                n_trials=int(body.get("n_trials", 12)),
                sampler=str(body.get("sampler", "tpe")),
                pruner=str(body.get("pruner", "hyperband")),
                metric=str(body.get("metric", "best_val_pearson")),
                direction=str(body.get("direction", "maximize")),
            )
            _send_json(handler, 202, {"sweep_id": sweep.sweep_id, "sweep": sweep.to_dict()})
        except Exception as exc:
            _send_json(handler, 500, {"error": "sweep_launch_failed", "detail": str(exc)})
        return True
    m = _SWEEP_CANCEL_RE.match(base)
    if m:
        from . import sweeps as _sweeps
        ok = _sweeps.cancel_sweep(m.group("sweep_id"))
        if not ok:
            _send_json(handler, 404, {"error": "sweep_not_found"})
        else:
            _send_json(handler, 202, {"cancelling": True, "sweep_id": m.group("sweep_id")})
        return True
    if _SYSTEM_ROSETTA_INSTALL_RE.match(base):
        body = _read_body(handler)
        try:
            from .rosetta_runtime import install_pyrosetta, rosetta_status
            # If the request body carries user/password/license_path, set them
            # in the process env so install_pyrosetta picks them up.
            import os as _os
            for key in ("license_path", "user", "password"):
                v = body.get(key)
                if not v: continue
                env_key = {"license_path": "PYROSETTA_LICENSE_PATH",
                           "user":         "PYROSETTA_LICENSE_USER",
                           "password":     "PYROSETTA_LICENSE_PASSWORD"}[key]
                _os.environ[env_key] = str(v)
            result = install_pyrosetta(force=bool(body.get("force", False)))
            _send_json(handler, 200, {"install": result, "status": rosetta_status()})
        except Exception as exc:
            _send_json(handler, 500, {"error": "rosetta_install_failed", "detail": str(exc)})
        return True
    m = _CANCEL_RE.match(base)
    if m:
        ok = get_registry().cancel(m.group("run_id"))
        if not ok:
            _send_json(handler, 404, {"error": "run_not_found"})
        else:
            _send_json(handler, 202, {"cancelling": True, "run_id": m.group("run_id")})
        return True
    m = _PREDICT_RE.match(base)
    if m:
        body = _read_body(handler)
        run_id = m.group("run_id")
        try:
            from . import inference as v2_inference
            # Batch mode if `pairs` is given; single mode if `sequence`+`smiles`.
            if "pairs" in body and isinstance(body["pairs"], list):
                _send_json(handler, 200, v2_inference.predict_batch(run_id, body["pairs"]))
            else:
                seq = body.get("sequence") or ""
                smi = body.get("smiles") or ""
                if not seq or not smi:
                    _send_json(handler, 400, {"error": "missing_sequence_or_smiles"})
                    return True
                _send_json(handler, 200, v2_inference.predict_one(run_id, seq, smi))
        except FileNotFoundError:
            _send_json(handler, 404, {"error": "checkpoint_not_found", "run_id": run_id,
                                       "message": "This run has no saved checkpoint. Only completed runs can be queried."})
        except Exception as exc:
            _send_json(handler, 500, {"error": "predict_failed", "detail": str(exc)})
        return True
    if _PROMOTIONS_RE.match(base):
        body = _read_body(handler)
        model_id = body.get("model_id")
        if not model_id:
            _send_json(handler, 400, {"error": "missing_model_id"})
            return True
        try:
            promo = model_db.create_promotion_request(model_id, comment=body.get("comment"))
            _send_json(handler, 201, {"promotion": promo, "model": model_db.get_model(model_id)})
        except ValueError as exc:
            _send_json(handler, 404, {"error": "model_not_found", "detail": str(exc)})
        return True
    m = _PROMOTION_DECIDE.match(base)
    if m:
        body = _read_body(handler)
        approve = bool(body.get("approve", False))
        try:
            promo = model_db.decide_promotion(
                m.group("promo_id"), approve=approve,
                actor=body.get("actor", "user"), note=body.get("note"),
            )
            _send_json(handler, 200, {"promotion": promo, "model": model_db.get_model(promo["model_id"])})
        except ValueError as exc:
            _send_json(handler, 400, {"error": "decide_failed", "detail": str(exc)})
        return True
    return False


def _handle_launch(handler) -> None:
    body = _read_body(handler)
    effective_config = body.get("effective_config") or {}
    template_id = effective_config.get("template_id") or body.get("template_id")
    hparams = body.get("hparams") or {}
    if not template_id:
        _send_json(handler, 400, {"error": "missing_template_id"})
        return

    # Validate the template against the live registry. Any template
    # registered in models.TEMPLATE_BUILDERS is accepted; "flow" is also
    # accepted (it compiles the user's graph at training time via
    # flow_compiler.compile_flow). Everything else gets a crisp 501.
    from .models import TEMPLATE_BUILDERS
    if template_id != "flow" and template_id not in TEMPLATE_BUILDERS:
        _send_json(handler, 501, {
            "error": "template_not_implemented",
            "template_id": template_id,
            "message": (
                f"The '{template_id}' template's torch implementation isn't in this build yet. "
                f"Wired templates: {sorted(TEMPLATE_BUILDERS) + ['flow']}. Add an nn.Module + builder "
                "entry in api/model_studio/v2/models.py to register a new one."
            ),
            "supported_templates": sorted(TEMPLATE_BUILDERS) + ["flow"],
        })
        return
    if template_id == "flow":
        # Validate the embedded flow spec early so the launch returns a
        # clear 400 rather than failing inside the worker thread.
        flow = effective_config.get("flow") or {}
        if not flow.get("nodes"):
            _send_json(handler, 400, {
                "error": "flow_missing_nodes",
                "message": (
                    "template_id=flow requires effective_config.flow.nodes to be a non-empty "
                    "list. Build the graph in the Pipeline (flow) screen and re-launch."
                ),
            })
            return

    registry = get_registry()
    run = registry.create_run(
        template_id=template_id,
        effective_config=effective_config,
        hparams=hparams,
    )
    run.emit({"type": "log", "level": "info",
              "text": f"Run {run.run_id} queued (template={template_id})."})

    # Spawn the worker. Daemon = True so it dies with the server. Torch is
    # only imported here (in the worker thread) so booting the server stays fast.
    def _worker() -> None:
        try:
            from .training import train_run
            train_run(run)
        except Exception as exc:
            run.failure = f"worker crashed: {exc}"
            run.emit({"type": "log", "level": "error", "text": run.failure})
            run.emit({"type": "log", "level": "error", "text": traceback.format_exc()})
            run.set_status("failed", finished_at=time.time(), failure=run.failure)

    t = threading.Thread(target=_worker, name=f"v2-train-{run.run_id}", daemon=True)
    t.start()
    run.thread = t

    _send_json(handler, 202, {
        "run_id": run.run_id,
        "stream_url": f"/api/v2/pipeline/runs/{run.run_id}/stream",
        "status_url": f"/api/v2/pipeline/runs/{run.run_id}",
        "run": run.to_dict(),
    })


def register_handlers(handler_class) -> None:
    """Patch the existing ModelStudioRequestHandler to dispatch v2 routes
    before its own routing. Called from server.py.
    """
    original_do_get = handler_class.do_GET
    original_do_post = handler_class.do_POST

    _V2_PREFIXES = ("/api/v2/pipeline", "/api/v2/registry", "/api/v2/ingest",
                    "/api/v2/splits", "/api/v2/system", "/api/v2/embeddings",
                    "/api/v2/featurizers", "/api/v2/blocks",
                    # `/api/v2/library` was added later for the
                    # Reference Library tab. Without this prefix the
                    # outer dispatch returns 404 before handle_get ever
                    # sees the path. Symptom: the Library pane shows
                    # "Backend error: HTTP 404" for every tab even
                    # though the route regex is registered.
                    "/api/v2/library")

    # Pre-warm CUDA in a background thread so the first user-facing GPU
    # operation (first training launch, first GPU Tanimoto, etc.) doesn't
    # eat the multi-minute cudnn/cublas autotune cost.
    try:
        from .gpu_runtime import warmup_cuda
        warmup_cuda(blocking=False, verbose=True)
    except Exception as exc:
        print(f"[v2] CUDA warmup skipped: {exc}", flush=True)

    # Boot-time inventory log. We deliberately AVOID importing
    # `.models` here because that pulls torch synchronously on the
    # main thread, and torch's cold import can take 30-120 seconds
    # on Windows when antivirus is scanning its ~700 .pyd files.
    # That delay used to make the launcher appear hung and the
    # browser auto-open hit ERR_CONNECTION_REFUSED.
    #
    # Instead we hardcode the template list. Templates are stable
    # across releases; if a new one ships, update this list. The
    # actual dispatch in handle_get / handle_post still imports
    # `.models` lazily on the first /pipeline/launch request, at
    # which point the user is explicitly asking to train and can
    # afford a few seconds of torch cold-load.
    _DECLARED_TEMPLATES = (
        "baseline_mlp", "conplex", "deepdta", "drugban", "graphdta",
        "moltrans", "ppi_gnn_siamese", "struct_gnn_dta",
        "tabular_mlp", "thermo_mlp", "flow",
    )
    print(f"[v2] supported templates ({len(_DECLARED_TEMPLATES)}): "
          f"{list(_DECLARED_TEMPLATES)} (declared; torch import deferred to "
          f"first /pipeline/launch request)", flush=True)
    print(f"[v2] flow compiler: enabled (POST /api/v2/pipeline/launch "
          f"with template_id='flow' + effective_config.flow=...)", flush=True)

    def new_do_get(self):
        # Note: keep the query string here so handle_get can read it.
        if any(self.path.startswith(p) for p in _V2_PREFIXES):
            if handle_get(self, self.path):
                return
        original_do_get(self)

    def new_do_post(self):
        if any(self.path.startswith(p) for p in _V2_PREFIXES):
            if handle_post(self, self.path):
                return
        original_do_post(self)

    handler_class.do_GET = new_do_get
    handler_class.do_POST = new_do_post
