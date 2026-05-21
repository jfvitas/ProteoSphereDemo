"""Slim v2-only server entry point.

The legacy ``server.py`` imports ``service.py`` and ``runtime.py`` at
module load, which transitively pull torch + sklearn + the multimodal
training stack. On a cold Python this can take **45+ minutes** to
import on Windows (AV scanning .pyd files, large numpy stack, etc).

This entry point only serves:
    * Static v2 GUI assets at ``/v2/*`` (the React/Babel SPA)
    * v2 API routes at ``/api/v2/*`` (mounted by handlers.register_handlers)

It deliberately does NOT touch the legacy service / runtime modules, so
boot is under 2 seconds. Use this when you only need the v2 surface.

Run:
    python -m api.model_studio.server_v2 --port 8765
Then open:
    http://127.0.0.1:8765/v2/
"""

from __future__ import annotations

import argparse
import logging
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_ROOT_V2 = REPO_ROOT / "gui" / "model_studio_web_v2"

_V2_CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript; charset=utf-8",
    ".jsx":  "text/babel; charset=utf-8",
    ".md":   "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ttf":  "font/ttf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

_V2_MAX_ASSET_BYTES = 5 * 1024 * 1024  # 5 MiB

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _build_v2_asset_map() -> dict[str, tuple[Path, str]]:
    """Walk the v2 directory once at import time and produce a closed
    allow-list of ``url_path -> (absolute_path, content_type)``.

    Same discipline as the legacy server:
      * extensions outside the whitelist are skipped
      * dotfile components are skipped
      * resolved symlinks must stay inside the v2 root
      * 5 MiB per-file cap
    """
    out: dict[str, tuple[Path, str]] = {}
    if not STATIC_ROOT_V2.is_dir():
        return out
    root_resolved = STATIC_ROOT_V2.resolve()
    for path in STATIC_ROOT_V2.rglob("*"):
        if not path.is_file():
            continue
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
        try:
            if path.stat().st_size > _V2_MAX_ASSET_BYTES:
                continue
        except OSError:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        rel = path.relative_to(STATIC_ROOT_V2).as_posix()
        out[f"/v2/{rel}"] = (resolved, ctype)
    index_path = STATIC_ROOT_V2 / "index.html"
    if index_path.is_file():
        alias = (index_path.resolve(), _V2_CONTENT_TYPES[".html"])
        out["/v2"] = alias
        out["/v2/"] = alias
    return out


_V2_ASSETS = _build_v2_asset_map()


logger = logging.getLogger("api.model_studio.server_v2")


class V2OnlyHandler(BaseHTTPRequestHandler):
    """Minimal handler — static v2 assets + v2 API routes, nothing else."""

    server_version = "ProteoSphereV2/0.2"

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # Route through the standard logger so output redirects work.
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path
        # 1. Static v2 asset?
        entry = _V2_ASSETS.get(path.split("?", 1)[0])
        if entry is not None:
            self._serve_file(*entry)
            return
        # 2. Redirect bare root → /v2/
        if path in ("/", ""):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", "/v2/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        # 3. v2 API routes (mounted at module load via register_handlers).
        # If we got here it means no route matched.
        self._send_404()

    def do_POST(self) -> None:  # noqa: N802
        # v2 API routes can override this via register_handlers. If we
        # got here the handler patch didn't intercept it.
        self._send_404()

    # ──────────────────────────────────────────────────────────────────
    def _serve_file(self, abs_path: Path, content_type: str) -> None:
        try:
            data = abs_path.read_bytes()
        except OSError as exc:
            logger.warning("failed to read %s: %s", abs_path, exc)
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_404(self) -> None:
        body = b'{"error":"not_found"}'
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _validate_bind_host(host: str, allow_remote: bool) -> str:
    if allow_remote:
        return host
    if host in _LOOPBACK_HOSTS:
        return host
    raise SystemExit(
        f"Refusing to bind to {host!r} without --allow-remote. "
        "This server has no auth; only enable on a trusted LAN."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the ProteoSphere v2 model studio (slim server)."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help=("Allow binding to a non-loopback host. The studio has no "
              "authentication; only enable this on a trusted LAN."),
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

    # Mount v2 API routes onto the handler. This is intentionally done
    # after argparse so --port etc. are fast even if v2.handlers takes a
    # moment to import (it doesn't pull torch at module load).
    try:
        from api.model_studio.v2 import register_handlers as _register_v2
        _register_v2(V2OnlyHandler)
        logger.info("v2 API routes mounted (/api/v2/*)")
    except Exception as exc:  # noqa: BLE001
        logger.warning("v2 API routes NOT mounted: %s", exc)

    server = ThreadingHTTPServer((host, args.port), V2OnlyHandler)
    url = f"http://{host}:{args.port}/v2/"
    logger.info("Listening on %s", url)
    print(url, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    # If invoked as a script, fix up sys.path so api.* imports resolve.
    if __package__ in {None, ""}:
        sys.path.insert(0, str(REPO_ROOT))
    raise SystemExit(main())
