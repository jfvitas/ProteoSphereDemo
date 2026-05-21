"""Manual-drop ingest path for URL_BROKEN sources.

Some sources (CORUM, TTD, BioPlex, …) migrated to JS-SPA download pages
without preserving the static URLs we'd hit from headless ingest. The
parsers themselves still work — they just need bytes on disk to chew on.
This module bridges that gap:

    1. User downloads the file manually via a browser
    2. Drops it anywhere on disk
    3. Runs `python -m api.model_studio.v2.ingest.manual_drop --source corum --file /path/to/allComplexes.json.zip`
    4. We copy + sha256 + register in the SQLite state, then invoke the
       parser as if the downloader had just fetched it

The parser dispatch is the same code path used by the downloader-driven
ingest, so once this finishes the catalog views, signatures, and bridges
all rebuild via the standard `signature_workflow.rebuild_all()`.

Usage:
    python -m api.model_studio.v2.ingest.manual_drop --source corum --file allComplexes.json.zip
    python -m api.model_studio.v2.ingest.manual_drop --source ttd   --file P1-01-TTD_target_download.txt
    python -m api.model_studio.v2.ingest.manual_drop --list

Supported source ids (URL_BROKEN):
    corum   - mammalian protein complexes; expects allComplexes.json.zip
    ttd     - therapeutic target DB; expects P1-01-TTD_target_download.txt
    bioplex - mass-spec protein interaction maps; expects BioPlex_293T.tsv
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

from .state import INGEST_ROOT, get_state


# Per-source destination layout + expected filename patterns.
_MANUAL_TARGETS: dict[str, dict] = {
    "corum": {
        "label":        "CORUM mammalian protein complexes",
        "subdir":       "interaction_network/corum",
        "expected_re":  r"allComplexes\.(json|json\.zip|zip)$",
        "url_hint":     "https://mips.helmholtz-muenchen.de/corum/ -> Downloads -> allComplexes.json.zip",
    },
    "ttd": {
        "label":        "Therapeutic Target Database",
        "subdir":       "ligand_assay/ttd",
        "expected_re":  r"(P1-01-TTD_target_download|P1-07-Drug-TargetMapping)\.(txt|xlsx)$",
        "url_hint":     "https://db.idrblab.net/ttd/ -> Download -> P1-01 (targets) + P1-07 (drug-target map)",
    },
    "bioplex": {
        "label":        "BioPlex AP-MS interactome",
        "subdir":       "interaction_network/bioplex",
        "expected_re":  r"BioPlex_.+\.tsv$",
        "url_hint":     "https://bioplex.hms.harvard.edu/interactions.php -> BioPlex_293T_Network_*.tsv",
    },
}


def _list_targets() -> None:
    print("Manual-drop ingest targets:")
    for sid, cfg in _MANUAL_TARGETS.items():
        print(f"  {sid:<10s}  {cfg['label']}")
        print(f"             expected file matching: {cfg['expected_re']}")
        print(f"             download from:         {cfg['url_hint']}")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manual_drop(source_id: str, file_path: str | Path,
                *, snapshot_id: str | None = None,
                parse: bool = True) -> dict:
    """Copy a user-supplied file into the canonical snapshot folder for
    ``source_id``, then optionally invoke the parser dispatcher.

    Args:
        source_id:   one of ``_MANUAL_TARGETS`` keys.
        file_path:   absolute or relative path to the locally-downloaded
                     source file.
        snapshot_id: timestamped snapshot to file the drop under. Defaults
                     to a UTC ``YYYYMMDDTHHMMSSZ`` stamp.
        parse:       if True (default), invoke the parser and register
                     the resulting catalog views.

    Returns a dict with ``status``, ``snapshot_id``, ``destination``,
    ``sha256``, ``bytes``, and (when ``parse=True``) a ``parse_result``.
    """
    cfg = _MANUAL_TARGETS.get(source_id)
    if cfg is None:
        return {
            "status": "unknown_source",
            "error": f"Unknown source_id '{source_id}'. Choices: {sorted(_MANUAL_TARGETS)}",
        }
    src = Path(file_path)
    if not src.exists() or not src.is_file():
        return {"status": "file_not_found", "error": f"No such file: {file_path}"}

    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    dest_dir = INGEST_ROOT / cfg["subdir"] / snapshot_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    sha = _sha256_of(dest)
    size = dest.stat().st_size

    # Reserve a SourceState row + mark verified. We pass `url` as a
    # synthetic file-scheme path so the audit log makes it obvious this
    # was a manual drop.
    state = get_state()
    state.reserve(
        source_id=source_id,
        url=f"manual-drop://{src.name}",
        bytes_expected=size,
        snapshot_id=snapshot_id,
        local_path=str(dest_dir),
    )
    state.mark_verified(
        source_id, sha256=sha, bytes_pulled=size, local_path=str(dest_dir),
    )

    result: dict = {
        "status":      "dropped",
        "source_id":   source_id,
        "snapshot_id": snapshot_id,
        "destination": str(dest),
        "sha256":      sha,
        "bytes":       size,
    }

    if parse:
        try:
            from . import parsers as _parsers
            if source_id not in _parsers.list_implemented():
                result["parse_skipped"] = f"no parser registered for {source_id}"
            else:
                parse_result = _parsers.parse(source_id)
                result["parse_result"] = {
                    "ok":           bool(parse_result.ok),
                    "row_counts":   dict(parse_result.row_counts),
                    "output_files": dict(parse_result.output_files),
                    "errors":       list(parse_result.errors),
                    "warnings":     list(parse_result.warnings),
                }
                if parse_result.ok:
                    from .catalog import refresh_source
                    cat = refresh_source(parse_result)
                    result["catalog"] = cat
        except Exception as exc:  # noqa: BLE001
            result["parse_error"] = str(exc)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manually ingest a file into the v2 warehouse for a URL_BROKEN source."
    )
    parser.add_argument("--source", help="Source id (one of: " + ", ".join(_MANUAL_TARGETS) + ")")
    parser.add_argument("--file",   help="Path to the locally-downloaded source file.")
    parser.add_argument("--list",   action="store_true", help="List supported manual-drop targets.")
    parser.add_argument("--no-parse", action="store_true",
                        help="Only copy + register; don't invoke the parser. Useful for staging.")
    args = parser.parse_args(argv)

    if args.list or (not args.source and not args.file):
        _list_targets()
        return 0
    if not args.source or not args.file:
        parser.error("Both --source and --file are required (or pass --list to inspect targets).")
    out = manual_drop(args.source, args.file, parse=not args.no_parse)
    import json as _json
    print(_json.dumps(out, indent=2, default=str))
    return 0 if out.get("status") == "dropped" else 1


if __name__ == "__main__":
    raise SystemExit(main())
