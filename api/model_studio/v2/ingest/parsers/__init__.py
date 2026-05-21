"""Per-source parsers — turn raw downloaded files into normalized parquet
fragments under ``INGEST_ROOT/normalized/<family>/<source>/<snapshot>/``.

Public surface:
    parse(source_id)             dispatch to the right parser
    register_parser(source_id, fn) extend the dispatch table
    list_implemented()           which sources have a parser today

Each parser is `(source_state, *, snapshot_dir) -> ParseResult` where
ParseResult is a small dataclass with row counts + emitted file paths +
a provenance claim. The catalog consolidation step (separate module)
ingests parquet fragments + emits a DuckDB view.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..state import SourceState, get_state


@dataclass
class ParseResult:
    source_id: str
    snapshot_id: str
    row_counts: dict[str, int]                 # table_name → n_rows
    output_files: dict[str, str]               # table_name → path
    provenance: dict                           # claim payload
    warnings: list[str] = field(default_factory=list)
    errors:   list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


_REGISTRY: dict[str, Callable[..., ParseResult]] = {}


def register_parser(source_id: str, fn: Callable[..., ParseResult]) -> None:
    _REGISTRY[source_id] = fn


def list_implemented() -> list[str]:
    return sorted(_REGISTRY.keys())


def parse(source_id: str, *, snapshot_dir: Path | None = None) -> ParseResult:
    fn = _REGISTRY.get(source_id)
    if not fn:
        raise NotImplementedError(
            f"No parser registered for '{source_id}'. "
            f"Implemented today: {', '.join(list_implemented()) or '(none)'}"
        )
    state = get_state().get(source_id)
    if not state:
        raise ValueError(f"No download state for '{source_id}'. Run download_source first.")
    if state.status != "verified":
        raise ValueError(f"Source '{source_id}' is not verified (status={state.status}).")
    return fn(state, snapshot_dir=snapshot_dir)


# ── Side-effect imports register each parser ────────────────────────
# Order matters only for log readability.
from . import gtopdb      # noqa: F401, E402
from . import huri        # noqa: F401, E402
from . import hippie      # noqa: F401, E402
from . import corum       # noqa: F401, E402
from . import davis_kiba  # noqa: F401, E402  registers both "davis" and "kiba"
from . import threedid    # noqa: F401, E402  registers "3did"
