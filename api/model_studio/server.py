# ruff: noqa: I001
"""HTTP entry point for the Model Studio preview server.

Design goals (cf. ``artifacts/reviews/model_studio_release_review`` and
the May 2026 review):

* Every endpoint catches its own exceptions and emits a JSON error
  envelope. The previous handler dropped the connection on any GET-side
  bug, which the GUI reported as a generic "failed to load" with no
  hint of what went wrong.
* Exception detail is logged server-side; the client receives a stable
  error code and a short, path-free message so internal filesystem
  layouts never leak.
* Static file serving is a closed allow-list with existence checks.
* POST bodies are size-capped (default 1 MiB) before any JSON parse.
* The ``--host`` flag refuses unknown bind addresses unless the caller
  explicitly opts in via ``--allow-remote``; an ``Origin``-header check
  keeps drive-by browser tabs from poking the loopback API.
* Identifier path components are validated by the service layer before
  they touch ``Path`` joins -- see ``runtime._resolve_run_dir`` and
  ``service._safe_filename_id``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
import threading
import traceback
import uuid
from http import HTTPStatus
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.model_studio.service import (
    build_hardware_profile_payload,
    build_program_status,
    build_training_set_payload,
    build_workspace_payload,
    cancel_pipeline_run,
    compare_pipeline_runs,
    compile_pipeline_payload,
    launch_pipeline_run,
    list_pipeline_runs,
    list_pipeline_specs,
    list_training_set_build_records,
    load_pipeline_run,
    load_pipeline_run_artifacts,
    load_pipeline_run_logs,
    load_pipeline_spec,
    load_training_set_build_record,
    preview_training_set_payload,
    record_session_event,
    save_pipeline_spec,
    submit_feedback,
    validate_pipeline_payload,
)
from api.model_studio.runtime import recover_stale_runs


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT = REPO_ROOT / "gui" / "model_studio_web"

# Maximum acceptable request-body size. 1 MiB is generous for pipeline
# spec payloads (the largest real-world spec we've seen is ~12 KiB).
_MAX_BODY_BYTES = 1 * 1024 * 1024

# Hosts the server is willing to bind to without the ``--allow-remote``
# flag. Everything else is rejected at argparse time so an operator
# can't accidentally publish the API on a LAN.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

# When ``Origin`` is set on a request, accept it only if the host
# portion matches a loopback address. Browsers omit ``Origin`` for
# same-origin GETs but include it on cross-origin fetches, so this
# stops drive-by tabs from reaching state-mutating POSTs.
_ALLOWED_ORIGIN_HOSTS = _LOOPBACK_HOSTS

# Static assets the server is willing to serve. New files go here; the
# raw filesystem is never browseable.
_STATIC_ASSETS: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


# ---------------------------------------------------------------------------
# v2 design preview
# ---------------------------------------------------------------------------
#
# The May 2026 redesign (Claude-Design bundle, see ``gui/model_studio_web_v2/
# AGENT_HANDOFF.md``) is a React-18 / Babel-Standalone prototype that lives
# alongside the working v1 GUI. It's served read-only at ``/v2/`` and uses
# fixture data (``data.js``) for screens that don't yet have backend
# endpoints. The intent is to let the team see and click through the new
# design in their actual environment while we incrementally wire screens
# to live data.
#
# Asset discipline matches the v1 ``_STATIC_ASSETS`` pattern: a closed
# allow-list of relative paths under ``STATIC_ROOT_V2``, built once at
# import time by walking the directory. New files appear automatically
# but raw filesystem browsing remains impossible.

STATIC_ROOT_V2 = REPO_ROOT / "gui" / "model_studio_web_v2"

_V2_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    # JSX is served as text/babel so the browser-side Babel transpiler
    # picks it up; the script tags in v2/index.html use
    # ``type="text/babel"``.
    ".jsx": "text/babel; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    # Vendored Geist fonts (self-hosted under v2/vendor/fonts/).
    ".ttf": "font-mod-ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

# Hard upper bound on per-file size. The v2 surface is small JS/CSS/MD
# plus ~80 KB TTFs; anything larger is almost certainly a binary dropped
# by mistake and should not be served from RAM by ``_write_v2_asset``.
_V2_MAX_ASSET_BYTES = 5 * 1024 * 1024  # 5 MiB


def _build_v2_asset_map() -> dict[str, tuple[Path, str]]:
    """Walk the v2 directory once at import time and produce a closed
    allow-list of ``url_path -> (absolute_path, content_type)``.

    The walker silently skips anything whose extension isn't in
    :data:`_V2_CONTENT_TYPES`; this keeps the AGENT_HANDOFF spec out
    of the served set if anyone drops a binary or backup file in
    there. Additional discipline applied:

    * Dotfile components (``.envrc``, ``.git``, ``.deep_settings_source``…)
      are skipped — defence-in-depth against accidental secret drops.
    * Symlinks whose resolved target escapes the v2 root are rejected.
    * Files larger than ``_V2_MAX_ASSET_BYTES`` are skipped so an
      accidental tarball does not get loaded into RAM on each GET.

    The walker is a one-shot at module import; restart the server to
    pick up new files.
    """
    out: dict[str, tuple[Path, str]] = {}
    if not STATIC_ROOT_V2.is_dir():
        return out
    root_resolved = STATIC_ROOT_V2.resolve()
    for path in STATIC_ROOT_V2.rglob("*"):
        if not path.is_file():
            continue
        # Skip dotfile components anywhere in the relative path.
        try:
            rel_parts = path.relative_to(STATIC_ROOT_V2).parts
        except ValueError:
            continue
        if any(part.startswith(".") for part in rel_parts):
            continue
        ext = path.suffix.lower()
        ctype = _V2_CONTENT_TYPES.get(ext)
        if ctype is None:
            continue
        # Per-file size cap.
        try:
            if path.stat().st_size > _V2_MAX_ASSET_BYTES:
                continue
        except OSError:
            continue
        # Defence-in-depth: confirm the resolved path is still under
        # the v2 root (defeats stray symlinks).
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        # Sentinel value (font-mod-ttf) flagged TTF as font for explicit policy;
        # rewrite to the canonical font/ttf type now.
        if ctype == "font-mod-ttf":
            ctype = "font/ttf"
        rel = path.relative_to(STATIC_ROOT_V2).as_posix()
        out[f"/v2/{rel}"] = (resolved, ctype)
    # Also expose the directory root as an alias for ``index.html``.
    index_path = STATIC_ROOT_V2 / "index.html"
    if index_path.is_file():
        alias = (index_path.resolve(), _V2_CONTENT_TYPES[".html"])
        out["/v2"] = alias
        out["/v2/"] = alias
    return out


_V2_ASSETS = _build_v2_asset_map()


logger = logging.getLogger("api.model_studio.server")


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

# Stable client-visible error codes. The ``detail`` strings are
# intentionally short and path-free; the full ``str(exc)`` (which may
# contain absolute filesystem paths) is only logged server-side.
_GENERIC_DETAIL: dict[str, str] = {
    "invalid_json": "Request body is not valid JSON.",
    "invalid_spec": "Pipeline spec failed validation.",
    "training_set_preview_failed": "Training-set preview failed; see server logs.",
    "training_set_build_failed": "Training-set build failed; see server logs.",
    "launch_failed": "Run launch failed; see server logs.",
    "feedback_failed": "Failed to record feedback.",
    "session_event_failed": "Failed to record session event.",
    "internal_error": "An internal error occurred; see server logs.",
    "body_too_large": (
        f"Request body exceeds the {_MAX_BODY_BYTES // 1024} KiB cap."
    ),
    "unsupported_media_type": "Content-Type must be application/json.",
    "forbidden_origin": "Origin not permitted for this server.",
}


class _ClientError(Exception):
    """Raised by request-parsing helpers when the *client* did something
    wrong. The handler converts these into 400/413/415 responses; the
    full message is never logged as an internal error."""

    def __init__(self, code: str, status: int = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class ModelStudioRequestHandler(BaseHTTPRequestHandler):
    server_version = "ProteoSphereModelStudio/0.2"

    # --- low-level write helpers -------------------------------------------

    def _write_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_json_with_etag(self, payload: Any) -> None:
        """Send ``payload`` with a strong ETag (SHA-256 of body bytes).

        If the client supplied ``If-None-Match`` and it matches, respond
        with ``304 Not Modified`` and no body. Used by the run-detail
        endpoint where the GUI polls every 2-3 seconds during a run --
        the 304 short-circuit drops payload to zero bytes when nothing
        has changed since the last poll.
        """
        body = json.dumps(payload, indent=2).encode("utf-8")
        etag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
        if_none_match = self.headers.get("If-None-Match", "")
        if if_none_match and etag in {t.strip() for t in if_none_match.split(",")}:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _write_error(
        self,
        status: int,
        code: str,
        *,
        request_id: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "error": code,
            "detail": _GENERIC_DETAIL.get(code, "Request failed."),
        }
        if request_id:
            payload["request_id"] = request_id
        self._write_json(payload, status=status)

    def _send_framing_headers(self) -> None:
        """Headers that defend against clickjacking, MIME sniffing and
        accidental cross-origin embedding for *every* static response.

        We forbid framing entirely. The v2 prototype lives at
        ``127.0.0.1:8765/v2/`` for the operator's own browser only — no
        legitimate caller embeds it in an iframe.
        """
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")

    def _write_static(self, asset: tuple[str, str]) -> None:
        rel_name, content_type = asset
        path = (STATIC_ROOT / rel_name).resolve()
        # Defence-in-depth: even though ``_STATIC_ASSETS`` is a closed
        # allow-list of relative names, double-check that nothing in
        # the table escapes ``STATIC_ROOT`` via an unexpected symlink.
        if not path.is_relative_to(STATIC_ROOT.resolve()):
            self._write_error(HTTPStatus.NOT_FOUND, "not_found")
            return
        if not path.is_file():
            self._write_error(HTTPStatus.NOT_FOUND, "not_found")
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Static assets are revved manually; let the GUI cache them.
        self.send_header("Cache-Control", "no-cache")
        self._send_framing_headers()
        self.end_headers()
        self.wfile.write(body)

    def _write_v2_asset(self, absolute_path: Path, content_type: str) -> None:
        """Serve a v2 prototype asset.

        Path is already resolved and validated inside :data:`_V2_ASSETS`
        at import time; the helper exists to keep the wire format
        consistent with :meth:`_write_static` (same headers, same
        no-cache policy, same 404 fallback if the file vanished after
        startup).
        """
        if not absolute_path.is_file():
            self._write_error(HTTPStatus.NOT_FOUND, "not_found")
            return
        body = absolute_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._send_framing_headers()
        self.end_headers()
        self.wfile.write(body)

    # --- request parsing ---------------------------------------------------

    def _check_origin(self) -> None:
        """Reject cross-origin POSTs from non-loopback browser tabs."""
        origin = self.headers.get("Origin")
        if not origin:
            return
        try:
            parsed = urlparse(origin)
        except ValueError as exc:
            raise _ClientError("forbidden_origin", HTTPStatus.FORBIDDEN) from exc
        host = (parsed.hostname or "").lower()
        if host not in _ALLOWED_ORIGIN_HOSTS:
            raise _ClientError("forbidden_origin", HTTPStatus.FORBIDDEN)

    def _check_host_header(self) -> None:
        """Defend against DNS rebinding: even though the socket is bound
        to a loopback address, browsers will happily resolve an attacker
        domain to 127.0.0.1 after a TTL trick. Refuse requests whose
        ``Host`` header doesn't match a loopback address."""
        host_header = (self.headers.get("Host") or "").split(":", 1)[0].lower()
        if host_header and host_header not in _ALLOWED_ORIGIN_HOSTS:
            raise _ClientError("forbidden_origin", HTTPStatus.FORBIDDEN)

    def _read_json(self) -> dict[str, Any]:
        length_text = self.headers.get("Content-Length", "0")
        try:
            length = int(length_text)
        except (TypeError, ValueError) as exc:
            raise _ClientError("invalid_json") from exc
        if length < 0:
            raise _ClientError("invalid_json")
        if length > _MAX_BODY_BYTES:
            raise _ClientError("body_too_large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        # The body is optional for POSTs (e.g. /runs/<id>/cancel takes no
        # payload), so length 0 is fine.
        if length == 0:
            return {}
        # Enforce Content-Type so non-CORS form CSRF doesn't reach the
        # body parser with a spoofed payload.
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype and ctype != "application/json":
            raise _ClientError("unsupported_media_type", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
        body = self.rfile.read(length)
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _ClientError("invalid_json") from exc
        if not isinstance(payload, dict):
            # Reject top-level arrays / scalars; they have no meaning to
            # any current endpoint and only widen the attack surface of
            # spec deserializers.
            raise _ClientError("invalid_json")
        return payload

    # --- dispatch helpers --------------------------------------------------

    def _run_handler(
        self,
        handler: Callable[[], Any],
        *,
        error_code: str = "internal_error",
        with_etag: bool = False,
    ) -> None:
        """Run ``handler`` and serialize the result to JSON, mapping any
        exception to a clean error envelope. Distinguishes:

        * ``_ClientError``      -> HTTP 4xx, no logging
        * ``FileNotFoundError`` -> 404 with a generic detail
        * ``ValueError``        -> 400 ``invalid_spec`` (kept for compat
          with service layer that raises ValueError for bad payloads)
        * anything else         -> 500 ``internal_error`` with a
          ``request_id`` correlation token; full stack only in the log
        """
        request_id = uuid.uuid4().hex[:12]
        try:
            payload = handler()
        except _ClientError as exc:
            self._write_error(exc.status, exc.code, request_id=request_id)
        except FileNotFoundError:
            self._write_error(HTTPStatus.NOT_FOUND, "not_found", request_id=request_id)
        except ValueError as exc:
            logger.info(
                "request %s: client-side validation failure: %s", request_id, exc,
            )
            self._write_error(
                HTTPStatus.BAD_REQUEST, "invalid_spec", request_id=request_id,
            )
        except Exception:  # noqa: BLE001 - any other failure is a 500
            logger.error(
                "request %s: unhandled exception in %s %s\n%s",
                request_id,
                self.command,
                self.path,
                traceback.format_exc(),
            )
            self._write_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                error_code,
                request_id=request_id,
            )
        else:
            if with_etag:
                self._write_json_with_etag(payload)
            else:
                self._write_json(payload)

    # --- routes ------------------------------------------------------------

    _RUN_ARTIFACTS_RE = re.compile(r"^/api/model-studio/runs/(?P<run_id>[^/]+)/artifacts$")
    _RUN_LOGS_RE = re.compile(r"^/api/model-studio/runs/(?P<run_id>[^/]+)/logs$")
    _RUN_RE = re.compile(r"^/api/model-studio/runs/(?P<run_id>[^/]+)$")
    _BUILD_RE = re.compile(r"^/api/model-studio/training-set-builds/(?P<build_id>[^/]+)$")
    _SPEC_RE = re.compile(r"^/api/model-studio/pipeline-specs/(?P<pipeline_id>[^/]+)$")
    _CANCEL_RE = re.compile(r"^/api/model-studio/runs/(?P<run_id>[^/]+)/cancel$")

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._check_host_header()
        except _ClientError as exc:
            self._write_error(exc.status, exc.code)
            return

        parsed = urlparse(self.path)
        path = parsed.path

        # Static assets first (fast path; no service-layer overhead).
        if path in _STATIC_ASSETS:
            self._write_static(_STATIC_ASSETS[path])
            return

        # v2 design-preview assets (May 2026 Claude-Design bundle).
        # Closed allow-list discovered at startup; see ``_build_v2_asset_map``.
        v2_hit = _V2_ASSETS.get(path)
        if v2_hit is not None:
            absolute_path, content_type = v2_hit
            self._write_v2_asset(absolute_path, content_type)
            return

        if path == "/api/model-studio/health":
            self._write_json({"status": "ok", "service": "model-studio"})
            return

        # API GETs all go through ``_run_handler``.
        query = parse_qs(parsed.query)

        if path == "/api/model-studio/catalog":
            self._run_handler(lambda: build_workspace_payload()["catalog"])
            return
        if path == "/api/model-studio/workspace-preview":
            pipeline_id = query.get("pipeline_id", [None])[0]
            self._run_handler(lambda: build_workspace_payload(pipeline_id))
            return
        if path == "/api/model-studio/program-status":
            self._run_handler(build_program_status)
            return
        if path == "/api/model-studio/hardware-profile":
            self._run_handler(build_hardware_profile_payload)
            return
        if path == "/api/model-studio/pipeline-specs":
            self._run_handler(lambda: {"items": list_pipeline_specs()})
            return
        match = self._SPEC_RE.match(path)
        if match:
            pipeline_id = match.group("pipeline_id")
            self._run_handler(lambda: load_pipeline_spec(pipeline_id))
            return
        if path == "/api/model-studio/runs":
            self._run_handler(list_pipeline_runs)
            return
        if path == "/api/model-studio/training-set-builds":
            self._run_handler(list_training_set_build_records)
            return
        match = self._BUILD_RE.match(path)
        if match:
            build_id = match.group("build_id")
            self._run_handler(lambda: load_training_set_build_record(build_id))
            return
        match = self._RUN_ARTIFACTS_RE.match(path)
        if match:
            run_id = match.group("run_id")
            self._run_handler(lambda: load_pipeline_run_artifacts(run_id))
            return
        match = self._RUN_LOGS_RE.match(path)
        if match:
            run_id = match.group("run_id")
            self._run_handler(lambda: load_pipeline_run_logs(run_id))
            return
        match = self._RUN_RE.match(path)
        if match:
            run_id = match.group("run_id")
            # ETag-enabled: a polling GUI sees 304 Not Modified while the
            # run sits at the same manifest revision, avoiding the
            # full-blob payload on every 2-3 second tick.
            self._run_handler(lambda: load_pipeline_run(run_id), with_etag=True)
            return
        if path == "/api/model-studio/compare":
            run_ids = query.get("run_id", [])
            self._run_handler(lambda: compare_pipeline_runs(run_ids))
            return

        self._write_error(HTTPStatus.NOT_FOUND, "not_found")

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._check_host_header()
            self._check_origin()
            payload = self._read_json()
        except _ClientError as exc:
            self._write_error(exc.status, exc.code)
            return

        path = self.path

        if path == "/api/model-studio/pipeline-specs/save-draft":
            self._run_handler(lambda: save_pipeline_spec(payload), error_code="invalid_spec")
            return
        if path == "/api/model-studio/pipeline-specs/validate":
            self._run_handler(lambda: validate_pipeline_payload(payload), error_code="invalid_spec")
            return
        if path == "/api/model-studio/pipeline-specs/compile":
            self._run_handler(lambda: compile_pipeline_payload(payload), error_code="invalid_spec")
            return
        if path == "/api/model-studio/training-set-requests/preview":
            self._run_handler(
                lambda: preview_training_set_payload(payload),
                error_code="training_set_preview_failed",
            )
            return
        if path == "/api/model-studio/training-set-builds/build":
            self._run_handler(
                lambda: build_training_set_payload(payload),
                error_code="training_set_build_failed",
            )
            return
        if path == "/api/model-studio/feedback":
            self._run_handler(lambda: submit_feedback(payload), error_code="feedback_failed")
            return
        if path == "/api/model-studio/session-events":
            self._run_handler(
                lambda: record_session_event(payload), error_code="session_event_failed",
            )
            return
        if path == "/api/model-studio/runs/launch":
            self._run_handler(lambda: launch_pipeline_run(payload), error_code="launch_failed")
            return
        match = self._CANCEL_RE.match(path)
        if match:
            run_id = match.group("run_id")
            self._run_handler(lambda: cancel_pipeline_run(run_id))
            return

        self._write_error(HTTPStatus.NOT_FOUND, "not_found")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _validate_bind_host(host: str, *, allow_remote: bool) -> str:
    if host in _LOOPBACK_HOSTS or allow_remote:
        return host
    raise SystemExit(
        f"Refusing to bind Model Studio to {host!r}. "
        "The studio has no authentication; binding to a non-loopback "
        "address would expose every endpoint to anyone on the network. "
        "Pass --allow-remote if you really mean to do this."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the ProteoSphere Model Studio preview server."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "Allow binding to a non-loopback host. The studio has no "
            "authentication; only enable this on a trusted LAN."
        ),
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python logging level (DEBUG/INFO/WARNING/ERROR).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    host = _validate_bind_host(args.host, allow_remote=args.allow_remote)

    # Stale-run recovery used to run synchronously here -- with hundreds
    # of historical runs it could block server startup for many seconds
    # while every manifest was re-read and (maybe) rewritten. Defer it
    # to a daemon thread so the listener is up immediately.
    def _background_recover() -> None:
        try:
            recovered = recover_stale_runs()
        except OSError as exc:
            if getattr(exc, "errno", None) == 28:  # ENOSPC -- disk full
                logger.warning(
                    "Skipped stale-run recovery because the local device is full.",
                )
                return
            logger.exception("Stale-run recovery failed:")
            return
        except Exception:  # noqa: BLE001 - never crash the recovery thread
            logger.exception("Unexpected error during stale-run recovery:")
            return
        if recovered:
            logger.info("Recovered %d stale run(s).", len(recovered))

    threading.Thread(
        target=_background_recover,
        name="model-studio-stale-recover",
        daemon=True,
    ).start()

    # Mount v2 pipeline handlers — adds POST /api/v2/pipeline/launch,
    # GET .../runs[/{id}[/stream]], POST .../runs/{id}/cancel. Done here
    # rather than at import time so an absent v2 module doesn't crash the
    # legacy server.
    try:
        from api.model_studio.v2 import register_handlers as _register_v2
        _register_v2(ModelStudioRequestHandler)
        logger.info("v2 pipeline handlers mounted at /api/v2/pipeline/*")
    except Exception as exc:
        logger.warning("v2 pipeline handlers NOT mounted: %s", exc)

    server = ThreadingHTTPServer((host, args.port), ModelStudioRequestHandler)
    logger.info("Listening on http://%s:%d", host, args.port)
    print(f"http://{host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
