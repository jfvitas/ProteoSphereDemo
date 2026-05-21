"""Internal IO helpers shared by ``runtime`` and ``service``.

These functions are the single source of truth for how Model Studio
reads and writes JSON on disk. Both are intentionally forgiving: a
missing file, an empty file, a directory, or a half-written manifest
should not crash the server -- they should fall back to the supplied
default and let the caller decide what to do.

The previous ad-hoc copies in ``runtime.py`` and ``service.py`` diverged
in subtle ways (one checked ``exists()`` then crashed on directories;
one wrote non-atomically). Centralizing here means the next bug fix
lands in one place.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    """Load a JSON file, returning ``default`` if the file is missing,
    is a directory, is empty, or contains invalid JSON.

    Critically uses :meth:`Path.is_file`, not :meth:`Path.exists`. An
    empty path string evaluates to ``Path(".")`` (current working
    directory), which always exists, and would otherwise crash inside
    ``read_text`` with ``PermissionError: '.'``. This was the root
    cause of 51 of 62 test failures in May 2026 and three GET endpoints
    returning ``curl: (52) Empty reply from server``.
    """
    if not path.is_file():
        return default
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return default
    if not text.strip():
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def save_json(
    path: Path,
    payload: Any,
    *,
    indent: int = 2,
    default=lambda value: value.to_dict() if hasattr(value, "to_dict") else str(value),
) -> None:
    """Atomically write JSON to ``path`` using temp-file + ``os.replace``.

    A crashed writer never leaves a half-written manifest that later
    reads would silently treat as empty. The ``default`` argument is the
    same shape ``json.dumps`` accepts for non-serializable types; the
    fallback to ``str()`` preserves the historical behaviour for
    ``Path`` and similar objects that lack ``to_dict``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, indent=indent, ensure_ascii=False, default=default),
        encoding="utf-8",
    )
    os.replace(tmp_path, path)
