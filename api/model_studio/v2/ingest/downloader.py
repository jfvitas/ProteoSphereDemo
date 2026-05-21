"""HTTP downloader for the v2 ingest pipeline.

Responsibilities:
    * Read SourceDescriptor → resolve URLs to local paths under INGEST_ROOT
    * Reserve the slot in IngestState (cap-check happens here; CapExceeded
      bubbles up to the caller)
    * Stream the body in chunks, writing to ``<name>.partial``, computing
      sha256 as we go
    * Periodically update IngestState.bytes_pulled so a separate process
      / the GUI can poll progress
    * On success: atomic rename to final path, sha256 verify, mark verified
    * On any exception: leave the .partial in place so a re-run resumes;
      mark the row 'failed' with the exception message
    * Resume: if a .partial exists and bytes_pulled > 0, send Range: bytes=N-

Multi-URL sources (e.g. Davis's 3 files) concatenate into one .partial?
No — each URL gets its own file under the same snapshot dir. Combined
bytes count toward the cap.

No external HTTP deps: uses urllib.request from stdlib. That's enough for
the LEAN smoke target (GtoPdb's three plain-text TSVs).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import ssl
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Iterable

from .manifest import SourceDescriptor, by_id
from .state import IngestState, get_state, INGEST_ROOT, CapExceeded, SourceState


# SSL context that skips verification — only used when a SourceDescriptor
# explicitly opts in via insecure_ssl=True. Document the reason in the
# manifest entry's `notes`.
_INSECURE_CTX = ssl.create_default_context()
_INSECURE_CTX.check_hostname = False
_INSECURE_CTX.verify_mode = ssl.CERT_NONE

# Some publishers (3did via Cloudflare, etc.) reject the default
# `Python-urllib/3.x` user agent. Identify as a generic client so requests
# aren't silently 403'd.
_DEFAULT_HEADERS = {
    "User-Agent": "ProteoSphere-Ingest/0.2 (+research; contact: proteosphere@users.noreply.github.com)",
    "Accept": "*/*",
}


_CHUNK_BYTES = 1 << 20   # 1 MiB
_PROGRESS_EVERY_BYTES = 4 << 20   # update SQLite every 4 MiB


# ── HEAD probe ────────────────────────────────────────────────────────

def head_size(url: str, *, timeout: float = 30.0, insecure_ssl: bool = False) -> int | None:
    """Return Content-Length from a HEAD request, or None if unknown.

    Some servers (esp. github raw) don't reply to HEAD. Fall back to a GET
    with bytes=0-0 (one-byte range) and read the Content-Range header.
    """
    ctx = _INSECURE_CTX if insecure_ssl else None
    try:
        req = urllib.request.Request(url, method="HEAD", headers=_DEFAULT_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            cl = resp.headers.get("Content-Length")
            if cl is not None:
                return int(cl)
    except (urllib.error.HTTPError, urllib.error.URLError, OSError):
        pass
    # Range fallback
    try:
        req = urllib.request.Request(url, headers={**_DEFAULT_HEADERS, "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            cr = resp.headers.get("Content-Range")  # e.g. "bytes 0-0/12345"
            if cr and "/" in cr:
                total = cr.rsplit("/", 1)[1]
                if total.isdigit():
                    return int(total)
    except Exception:
        pass
    return None


# ── Path helpers ──────────────────────────────────────────────────────

def _safe_filename_from_url(url: str) -> str:
    # Last path segment, stripped of querystring
    path = url.split("?", 1)[0].rstrip("/")
    name = path.rsplit("/", 1)[-1] or "payload"
    # Strip characters Windows doesn't like in filenames
    bad = '<>:"|?*'
    return "".join("_" if ch in bad else ch for ch in name)


def _snapshot_dir(source: SourceDescriptor, snapshot_id: str) -> Path:
    return INGEST_ROOT / source.family / source.source_id / snapshot_id


def _local_path(source: SourceDescriptor, url: str, snapshot_id: str) -> Path:
    return _snapshot_dir(source, snapshot_id) / _safe_filename_from_url(url)


# ── Core downloader ───────────────────────────────────────────────────

class Downloader:
    """Cap-aware, resumable, atomic-rename HTTP fetcher.

    Usage:
        d = Downloader()
        result = d.fetch(source_descriptor, on_progress=cb)

    `on_progress(source_id, bytes_pulled, bytes_expected)` is called every
    _PROGRESS_EVERY_BYTES while streaming.
    """

    def __init__(self, state: IngestState | None = None, timeout: float = 60.0):
        self.state = state or get_state()
        self.timeout = timeout

    def fetch(
        self,
        source: SourceDescriptor,
        *,
        on_progress: Callable[[str, int, int | None], None] | None = None,
        force: bool = False,
    ) -> SourceState:
        """Download every URL in the descriptor. Returns the final state row.

        For multi-URL sources, each URL writes to its own file under the
        snapshot directory; total bytes are the sum.
        """
        # If the row is already 'verified' and not forced, short-circuit.
        existing = self.state.get(source.source_id)
        if existing and existing.status == "verified" and not force:
            return existing

        # Pre-flight: HEAD-probe each URL where the manifest didn't declare
        # a size. This lets the cap check be accurate even when expected_bytes
        # was None.
        per_url_sizes: list[int | None] = []
        for u in source.urls:
            sz = head_size(u, timeout=self.timeout, insecure_ssl=source.insecure_ssl)
            per_url_sizes.append(sz)
        known_total = sum(s for s in per_url_sizes if s is not None)
        # If we don't know some URLs' sizes, lean on the manifest hint.
        if any(s is None for s in per_url_sizes):
            expected = source.expected_bytes or known_total
        else:
            expected = known_total

        # Reserve the slot — raises CapExceeded if too big
        snapshot_id = (existing.snapshot_id if existing and existing.status in ("downloading", "failed", "blocked_by_cap")
                       else time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
        primary_path = _local_path(source, source.urls[0], snapshot_id)
        primary_path.parent.mkdir(parents=True, exist_ok=True)

        state_row = self.state.reserve(
            source.source_id, source.urls[0],
            bytes_expected=expected,
            snapshot_id=snapshot_id,
            sha256_expected=source.sha256,
            local_path=str(primary_path.parent),
        )

        bytes_pulled_total = 0
        last_progress_emitted = 0
        sha = hashlib.sha256()

        try:
            for url, expected_for_this_url in zip(source.urls, per_url_sizes):
                local = _local_path(source, url, snapshot_id)
                partial = local.with_suffix(local.suffix + ".partial")
                # Resume position
                resume_from = partial.stat().st_size if partial.exists() else 0
                headers = {}
                if resume_from > 0 and (expected_for_this_url is None or resume_from < expected_for_this_url):
                    headers["Range"] = f"bytes={resume_from}-"

                req = urllib.request.Request(url, headers={**_DEFAULT_HEADERS, **headers})
                ctx = _INSECURE_CTX if source.insecure_ssl else None
                try:
                    resp = urllib.request.urlopen(req, timeout=self.timeout, context=ctx)
                except urllib.error.HTTPError as exc:
                    # Some servers return 416 if we ask to resume past EOF —
                    # that means the partial already has everything we asked for.
                    if exc.code == 416 and partial.exists():
                        os.replace(partial, local)
                        bytes_pulled_total += local.stat().st_size
                        sha.update(local.read_bytes())
                        continue
                    raise

                mode = "ab" if resume_from > 0 and resp.status == 206 else "wb"
                if mode == "wb" and partial.exists():
                    partial.unlink()
                with open(partial, mode) as f:
                    if mode == "ab":
                        bytes_pulled_total += resume_from
                        # Re-hash from existing bytes on resume to keep sha intact
                        with open(partial, "rb") as existing_partial:
                            while True:
                                chunk = existing_partial.read(_CHUNK_BYTES)
                                if not chunk:
                                    break
                                sha.update(chunk)
                    while True:
                        chunk = resp.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        f.write(chunk)
                        sha.update(chunk)
                        bytes_pulled_total += len(chunk)
                        if bytes_pulled_total - last_progress_emitted >= _PROGRESS_EVERY_BYTES:
                            self.state.update_progress(source.source_id, bytes_pulled_total)
                            if on_progress:
                                on_progress(source.source_id, bytes_pulled_total, expected)
                            last_progress_emitted = bytes_pulled_total
                # Atomic rename .partial → final
                os.replace(partial, local)

            # Sha verify if the manifest declared one
            computed = sha.hexdigest()
            if source.sha256 and computed != source.sha256:
                raise ValueError(f"sha256 mismatch: got {computed}, expected {source.sha256}")

            result = self.state.mark_verified(
                source.source_id,
                sha256=computed,
                bytes_pulled=bytes_pulled_total,
                local_path=str(primary_path.parent),
            )
            if on_progress:
                on_progress(source.source_id, bytes_pulled_total, expected)
            return result
        except CapExceeded:
            # state.reserve already wrote 'blocked_by_cap'; just re-raise
            raise
        except Exception as exc:
            self.state.mark_failed(source.source_id, message=f"{type(exc).__name__}: {exc}")
            raise


# ── Convenience ──────────────────────────────────────────────────────

def download_source(source_id: str, *, force: bool = False,
                    on_progress: Callable[[str, int, int | None], None] | None = None) -> SourceState:
    src = by_id(source_id)
    if not src:
        raise ValueError(f"Unknown source_id: {source_id}")
    return Downloader().fetch(src, on_progress=on_progress, force=force)
