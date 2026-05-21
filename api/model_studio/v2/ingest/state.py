"""Persistent ingest state — SQLite-backed cap accounting + per-source rows.

Schema:
    sources    one row per fetched source
        source_id     TEXT PRIMARY KEY
        snapshot_id   TEXT NOT NULL                   -- timestamped folder
        url           TEXT NOT NULL
        status        TEXT NOT NULL                   -- queued | downloading | verified
                                                      -- | failed | cancelled | blocked_by_cap
        bytes_pulled  INTEGER NOT NULL DEFAULT 0      -- absolute fetched bytes
        bytes_expected INTEGER                        -- expected total (Content-Length)
        sha256        TEXT                            -- computed on success
        sha256_expected TEXT                          -- if the manifest declared one
        started_at    REAL
        finished_at   REAL
        message       TEXT                            -- error / blocked reason
        retries       INTEGER NOT NULL DEFAULT 0
        local_path    TEXT                            -- where the file ended up

Cap accounting:
    running_used = SUM(bytes_pulled WHERE status IN ('verified','downloading'))
    A new request that would push (running_used + bytes_expected) > cap is
    refused with CapExceeded.

The state is process-local but persists across server restarts.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path

# ── Default paths + cap ────────────────────────────────────────────────
INGEST_ROOT = Path(os.environ.get(
    "PROTEOSPHERE_V2_INGEST_ROOT",
    r"%PROTEOSPHERE_WAREHOUSE%_v2",
))
INGEST_CAP_BYTES = int(os.environ.get(
    "PROTEOSPHERE_V2_INGEST_CAP_BYTES",
    str(5_000_000_000_000),  # 5 TB
))
_DB_PATH = INGEST_ROOT / ".ingest_state.sqlite"


class CapExceeded(Exception):
    """Raised when a fetch would push the running download total over the cap."""

    def __init__(self, *, source_id: str, current_used: int, would_use: int, cap: int):
        self.source_id = source_id
        self.current_used = current_used
        self.would_use = would_use
        self.cap = cap
        msg = (
            f"Download for '{source_id}' would push the running total over the cap "
            f"({current_used / 1e9:.2f} GB used + {would_use / 1e9:.2f} GB pending "
            f"vs {cap / 1e9:.2f} GB cap). Refusing. Raise PROTEOSPHERE_V2_INGEST_CAP_BYTES "
            "to override, or pick a LEAN variant of the source."
        )
        super().__init__(msg)


@dataclass
class SourceState:
    source_id: str
    snapshot_id: str
    url: str
    status: str
    bytes_pulled: int
    bytes_expected: int | None
    sha256: str | None
    sha256_expected: str | None
    started_at: float | None
    finished_at: float | None
    message: str | None
    retries: int
    local_path: str | None


# ── Connection ────────────────────────────────────────────────────────
_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is not None:
        return _conn
    INGEST_ROOT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH), isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            source_id        TEXT PRIMARY KEY,
            snapshot_id      TEXT NOT NULL,
            url              TEXT NOT NULL,
            status           TEXT NOT NULL,
            bytes_pulled     INTEGER NOT NULL DEFAULT 0,
            bytes_expected   INTEGER,
            sha256           TEXT,
            sha256_expected  TEXT,
            started_at       REAL,
            finished_at      REAL,
            message          TEXT,
            retries          INTEGER NOT NULL DEFAULT 0,
            local_path       TEXT
        )
    """)
    _conn = c
    return c


def _row(r: sqlite3.Row | None) -> SourceState | None:
    if not r:
        return None
    return SourceState(
        source_id=r["source_id"], snapshot_id=r["snapshot_id"], url=r["url"],
        status=r["status"], bytes_pulled=r["bytes_pulled"],
        bytes_expected=r["bytes_expected"], sha256=r["sha256"],
        sha256_expected=r["sha256_expected"],
        started_at=r["started_at"], finished_at=r["finished_at"],
        message=r["message"], retries=r["retries"], local_path=r["local_path"],
    )


# ── Public API ─────────────────────────────────────────────────────────

class IngestState:
    """Thread-safe wrapper around the SQLite log."""

    def __init__(self, cap_bytes: int = INGEST_CAP_BYTES):
        self.cap_bytes = cap_bytes

    def total_used_bytes(self) -> int:
        """Sum of bytes_pulled for in-flight + verified rows."""
        with _lock:
            c = _connect()
            r = c.execute(
                "SELECT COALESCE(SUM(bytes_pulled), 0) AS u "
                "FROM sources WHERE status IN ('downloading','verified')"
            ).fetchone()
            return int(r["u"] or 0)

    def reserve(self, source_id: str, url: str, *, bytes_expected: int | None,
                snapshot_id: str | None = None, sha256_expected: str | None = None,
                local_path: str | None = None) -> SourceState:
        """Acquire a row before starting a download. Checks the cap.

        Idempotent: if a 'verified' row already exists, returns it as-is.
        If a 'downloading' / 'failed' row exists, resets to 'downloading'
        but preserves the snapshot id so partial files line up.
        """
        if snapshot_id is None:
            snapshot_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        with _lock:
            c = _connect()
            existing = _row(c.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone())
            if existing and existing.status == "verified":
                return existing

            # Cap check — count CURRENT verified bytes (don't count this row's prior bytes)
            current_used = self.total_used_bytes()
            if existing and existing.status == "downloading":
                # We're resuming — don't double-count what we already have
                current_used -= existing.bytes_pulled
            projected = current_used + max(0, bytes_expected or 0)
            if projected > self.cap_bytes:
                c.execute(
                    "INSERT OR REPLACE INTO sources "
                    "(source_id, snapshot_id, url, status, bytes_pulled, bytes_expected, "
                    " sha256_expected, started_at, message, retries, local_path) "
                    "VALUES (?, ?, ?, 'blocked_by_cap', 0, ?, ?, ?, ?, "
                    "        COALESCE((SELECT retries FROM sources WHERE source_id = ?), 0), ?)",
                    (source_id, existing.snapshot_id if existing else snapshot_id,
                     url, bytes_expected, sha256_expected, time.time(),
                     f"would_use={projected}, cap={self.cap_bytes}", source_id, local_path),
                )
                raise CapExceeded(
                    source_id=source_id, current_used=current_used,
                    would_use=bytes_expected or 0, cap=self.cap_bytes,
                )

            # Insert or update row to 'downloading'
            now = time.time()
            if existing:
                c.execute(
                    "UPDATE sources SET status='downloading', started_at=?, message=NULL, "
                    "url=?, bytes_expected=COALESCE(?, bytes_expected), "
                    "sha256_expected=COALESCE(?, sha256_expected), local_path=COALESCE(?, local_path), "
                    "retries=retries + 1 WHERE source_id=?",
                    (now, url, bytes_expected, sha256_expected, local_path, source_id),
                )
            else:
                c.execute(
                    "INSERT INTO sources (source_id, snapshot_id, url, status, "
                    "bytes_pulled, bytes_expected, sha256_expected, started_at, retries, local_path) "
                    "VALUES (?, ?, ?, 'downloading', 0, ?, ?, ?, 0, ?)",
                    (source_id, snapshot_id, url, bytes_expected, sha256_expected, now, local_path),
                )
            return _row(c.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone())

    def update_progress(self, source_id: str, bytes_pulled: int) -> None:
        """Called periodically by the downloader. NB: bytes_pulled is the
        absolute count for this source, not a delta."""
        with _lock:
            _connect().execute(
                "UPDATE sources SET bytes_pulled=? WHERE source_id=?",
                (bytes_pulled, source_id),
            )

    def mark_verified(self, source_id: str, *, sha256: str, bytes_pulled: int,
                      local_path: str | None = None) -> SourceState:
        with _lock:
            c = _connect()
            c.execute(
                "UPDATE sources SET status='verified', sha256=?, bytes_pulled=?, "
                "finished_at=?, message=NULL, local_path=COALESCE(?, local_path) WHERE source_id=?",
                (sha256, bytes_pulled, time.time(), local_path, source_id),
            )
            return _row(c.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone())

    def mark_failed(self, source_id: str, *, message: str) -> SourceState:
        with _lock:
            c = _connect()
            c.execute(
                "UPDATE sources SET status='failed', message=?, finished_at=? WHERE source_id=?",
                (message, time.time(), source_id),
            )
            return _row(c.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone())

    def get(self, source_id: str) -> SourceState | None:
        with _lock:
            c = _connect()
            return _row(c.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone())

    def list(self) -> list[SourceState]:
        with _lock:
            c = _connect()
            rows = c.execute("SELECT * FROM sources ORDER BY started_at DESC NULLS LAST").fetchall()
            return [_row(r) for r in rows]

    def summary(self) -> dict:
        used = self.total_used_bytes()
        return {
            "cap_bytes": self.cap_bytes,
            "used_bytes": used,
            "free_bytes": self.cap_bytes - used,
            "used_pct": (100.0 * used / self.cap_bytes) if self.cap_bytes else 0.0,
            "n_sources": len(self.list()),
        }


_state: IngestState | None = None


def get_state() -> IngestState:
    global _state
    if _state is None:
        _state = IngestState()
    return _state
