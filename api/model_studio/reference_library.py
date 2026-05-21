from __future__ import annotations

import json
import zlib
from pathlib import Path
from typing import Any


CHUNK_MAGIC = b"PROTEOSPHERE-LITE-CHUNK\n"
DECODER_VERSION = "proteosphere-lite-decoder-v1"


def encode_chunk_payload(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return CHUNK_MAGIC + zlib.compress(body, level=9)


def decode_chunk_payload_bytes(data: bytes) -> dict[str, Any]:
    if not data.startswith(CHUNK_MAGIC):
        raise ValueError("Unsupported chunk payload format.")
    raw = zlib.decompress(data[len(CHUNK_MAGIC):])
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Chunk payload must decode to an object.")
    return payload


def decode_chunk_payload(path: Path) -> dict[str, Any]:
    return decode_chunk_payload_bytes(path.read_bytes())
