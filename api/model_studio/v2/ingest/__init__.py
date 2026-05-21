"""ProteoSphere v2 — source-database ingest.

Public surface:
    MANIFEST          declarative source list
    Downloader        cap-aware HTTP fetcher with resume + checksum
    IngestState       SQLite-backed bytes/cap accounting + per-source state

Storage rooted at INGEST_ROOT (default ``E:\\ProteoSphere\\reference_library_v2``).
Cap enforced at INGEST_CAP_BYTES (default 5 TB) — overridable via
``PROTEOSPHERE_V2_INGEST_CAP_BYTES`` env var.

See ``docs/WAREHOUSE_GROWTH_PROJECTION.md`` for the size projection that
motivates the cap.
"""

from .state import (
    IngestState, get_state, INGEST_ROOT, INGEST_CAP_BYTES,
    CapExceeded, SourceState,
)
from .manifest import MANIFEST, SourceDescriptor
from .downloader import Downloader, download_source

__all__ = [
    "IngestState", "get_state", "INGEST_ROOT", "INGEST_CAP_BYTES",
    "CapExceeded", "SourceState",
    "MANIFEST", "SourceDescriptor",
    "Downloader", "download_source",
]
