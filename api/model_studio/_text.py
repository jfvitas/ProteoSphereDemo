"""Tiny shared utilities used across the runtime modules.

Lives outside ``runtime.py`` so the extracted-but-still-coupled
submodules (``_hardware``, future ``_persistence``/``_catalog``/...)
can use these helpers without a circular import back to ``runtime``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(tz=UTC).isoformat()


def clean_text(value: Any) -> str:
    """Coerce ``value`` to a stripped string, mapping ``None`` to ``""``.

    The pattern is so pervasive in the runtime helpers (manifest reads
    where any field may be ``None`` or have surrounding whitespace)
    that putting it in one place avoids ~80 inline ``str(value or
    "").strip()`` repetitions.
    """
    return str(value or "").strip()
