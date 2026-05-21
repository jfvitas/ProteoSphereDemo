from __future__ import annotations

import hashlib
import os
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .warehouse import LOCAL_PATH_MARKERS, Warehouse


TEXT_SUFFIXES = {".json", ".md", ".txt", ".ps1", ".toml", ".csv", ".yaml", ".yml"}
PROVENANCE_LEDGER_NAMES = {"original_absolute_path_ledger.json", "absolute_path_provenance_ledger.json"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_size(path: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    if not path.exists():
        return 0, 0
    for item in path.rglob("*"):
        if item.is_file():
            file_count += 1
            try:
                byte_count += item.stat().st_size
            except OSError:
                pass
    return file_count, byte_count


def scan_text_path_leaks(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name in PROVENANCE_LEDGER_NAMES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        markers = [marker for marker in LOCAL_PATH_MARKERS if marker in text]
        if markers:
            findings.append(
                {
                    "path": str(path.relative_to(root)),
                    "markers": sorted(set(markers)),
                }
            )
    return findings


def validate_library(root: str | Path) -> dict[str, Any]:
    base = Path(root).resolve()
    warehouse = Warehouse.open(base)
    file_count, byte_count = tree_size(base)
    text_path_findings = scan_text_path_leaks(base)
    catalog_path_findings = warehouse.catalog_path_findings()
    table_counts = warehouse.table_counts()
    status = "passed"
    blockers: list[str] = []
    warnings: list[str] = []
    if not base.exists():
        status = "failed"
        blockers.append("warehouse root does not exist.")
    if not (base / "warehouse_manifest.json").exists():
        status = "failed"
        blockers.append("warehouse_manifest.json is missing.")
    if not (base / "warehouse_summary.json").exists():
        warnings.append("warehouse_summary.json is missing.")
    if not warehouse.duckdb_available:
        status = "metadata_only" if status == "passed" else status
        warnings.append("DuckDB is not installed; catalog-backed mapping is unavailable.")
    elif not warehouse.catalog_available:
        status = "metadata_only" if status == "passed" else status
        warnings.append("reference_library.duckdb is missing or could not be opened; metadata-only validation was used.")
    if text_path_findings or catalog_path_findings:
        status = "failed"
        blockers.append("runtime-required files contain local absolute path references.")
    duckdb_version = None
    if warehouse.duckdb_available:
        import duckdb

        duckdb_version = duckdb.__version__
    return {
        "artifact_id": "proteosphere_library_validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "warehouse_root": str(base),
        "python": {
            "version": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
        },
        "duckdb_version": duckdb_version,
        "file_count": file_count,
        "byte_count": byte_count,
        "manifest_present": (base / "warehouse_manifest.json").exists(),
        "summary_present": (base / "warehouse_summary.json").exists(),
        "catalog_available": warehouse.catalog_available,
        "table_counts": table_counts,
        "text_path_findings": text_path_findings,
        "catalog_path_findings": catalog_path_findings,
        "blockers": blockers,
        "warnings": warnings,
    }
