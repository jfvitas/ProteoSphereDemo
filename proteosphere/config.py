"""Single source of truth for ProteoSphere paths.

Resolution order for the warehouse root:

1. ``--warehouse-root`` CLI flag (when scripts accept one).
2. ``PROTEOSPHERE_WAREHOUSE`` environment variable.
3. ``proteosphere_config.json`` in the current working directory or any parent.
4. The OS-default install location (``~/.proteosphere/reference_library``).

The resolved warehouse root is then used to compute all family partition
paths from the warehouse manifest.

For optional source mirrors (raw payloads on a bulk drive), the same config
file may set ``source_mirror_root``; if absent, materialization routes that
require raw payloads return ``None`` and the caller is expected to either
skip the lane or invoke the live scraper.

Code in this package never hardcodes ``D:`` or ``E:``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CONFIG_FILENAME = "proteosphere_config.json"
ENV_WAREHOUSE = "PROTEOSPHERE_WAREHOUSE"
ENV_SOURCE_MIRROR = "PROTEOSPHERE_SOURCE_MIRROR"
ENV_BENCHMARK_DATA = "PROTEOSPHERE_BENCHMARK_DATA"
ENV_OFFLINE = "PROTEOSPHERE_OFFLINE"
DEFAULT_INSTALL_ROOT = Path.home() / ".proteosphere" / "reference_library"

# Family partition globs are relative to the warehouse root and pinned to the
# latest snapshot ID per family. When the warehouse is rehydrated, the
# manifest emits a new snapshot ID and the partition_glob entry is updated.
DEFAULT_PARTITION_GLOBS = {
    "proteins": "partitions/proteins/snapshot_id=full-local-backbone-2026-04-10/proteins.parquet",
    "protein_variants": "partitions/protein_variants/snapshot_id=raw-canonical-20260330T221513Z/protein_variants.parquet",
    "pdb_entries": "partitions/pdb_entries/snapshot_id=full-local-backbone-2026-04-10/pdb_entries.parquet",
    "structure_units": "partitions/structure_units/snapshot_id=full-local-backbone-2026-04-10/structure_units.parquet",
    "ligands": "partitions/ligands/snapshot_id=full-local-backbone-2026-04-10/ligands.parquet",
    "ligand_chemistry_signatures": "partitions/ligand_chemistry_signatures/snapshot_id=hardened-ligand-chemistry-all-local-2026-04-24/ligand_chemistry_signatures.parquet",
    "protein_ligand_edges": "partitions/protein_ligand_edges/snapshot_id=full-local-backbone-2026-04-10/protein_ligand_edges.parquet",
    "protein_protein_edges": "partitions/protein_protein_edges/snapshot_id=full-local-backbone-2026-04-10/protein_protein_edges.parquet",
    "motif_domain_site_annotations": "partitions/motif_domain_site_annotations/snapshot_id=full-local-backbone-2026-04-10/motif_domain_site_annotations.parquet",
    "pathway_roles": "partitions/pathway_roles/snapshot_id=full-local-backbone-2026-04-10/pathway_roles.parquet",
    "provenance_claims": "partitions/provenance_claims/snapshot_id=raw-canonical-20260330T221513Z/provenance_claims.parquet",
    "materialization_routes": "partitions/materialization_routes/snapshot_id=full-local-backbone-2026-04-10/materialization_routes.parquet",
    "leakage_groups": "partitions/leakage_groups/snapshot_id=hydrated-2026-05-08/leakage_groups.parquet",
    "similarity_signatures": "partitions/similarity_signatures/snapshot_id=hydrated-2026-05-08/similarity_signatures.parquet",
    "sequence_index": "partitions/sequence_index/snapshot_id=resolver-tier3-2026-05-08/sequence_index.parquet",
    "cross_references": "partitions/cross_references/snapshot_id=resolver-tier3-2026-05-08/cross_references.parquet",
    "domain_sequence_index": "partitions/domain_sequence_index/snapshot_id=resolver-tier3-2026-05-08/domain_sequence_index.parquet",
    "protein_family_index": "partitions/protein_family_index/snapshot_id=phaseB-2026-05-09/protein_family_index.parquet",
    "structural_classification_index": "partitions/structural_classification_index/snapshot_id=phaseB-2026-05-09/structural_classification_index.parquet",
    "function_class_index": "partitions/function_class_index/snapshot_id=phaseB-2026-05-09/function_class_index.parquet",
}

CATALOG_RELATIVE_PATH = "catalog/reference_library.duckdb"
MANIFEST_RELATIVE_PATH = "warehouse_manifest.json"


@dataclass(frozen=True)
class Config:
    """Resolved paths for one ProteoSphere environment."""

    warehouse_root: Path
    source_mirror_root: Path | None = None
    benchmark_data_root: Path | None = None
    offline_mode: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------ ctors

    @classmethod
    def discover(
        cls,
        warehouse_root: str | Path | None = None,
        config_file: str | Path | None = None,
    ) -> "Config":
        """Discover a Config from CLI override, env, config file, or default.

        Raises FileNotFoundError if no warehouse root can be resolved.
        """
        # 1. Explicit override
        if warehouse_root is not None:
            return cls._build(Path(warehouse_root))

        # 2. Explicit config file
        if config_file is not None:
            return cls._from_file(Path(config_file))

        # 3. Env var
        env_root = os.environ.get(ENV_WAREHOUSE)
        if env_root:
            return cls._build(Path(env_root))

        # 4. Walk up looking for proteosphere_config.json
        for parent in [Path.cwd(), *Path.cwd().parents]:
            candidate = parent / CONFIG_FILENAME
            if candidate.is_file():
                return cls._from_file(candidate)

        # 5. Default install location
        if DEFAULT_INSTALL_ROOT.is_dir():
            return cls._build(DEFAULT_INSTALL_ROOT)

        raise FileNotFoundError(
            "Cannot locate ProteoSphere warehouse. Set PROTEOSPHERE_WAREHOUSE, "
            "place a proteosphere_config.json next to your scripts, or install "
            f"the warehouse to {DEFAULT_INSTALL_ROOT}."
        )

    @classmethod
    def _from_file(cls, path: Path) -> "Config":
        payload = json.loads(path.read_text(encoding="utf-8"))
        warehouse_root = Path(payload["warehouse_root"]).expanduser()
        source_mirror_root = (
            Path(payload["source_mirror_root"]).expanduser()
            if payload.get("source_mirror_root")
            else None
        )
        benchmark_data_root = (
            Path(payload["benchmark_data_root"]).expanduser()
            if payload.get("benchmark_data_root")
            else None
        )
        offline_mode = bool(payload.get("offline_mode", False))
        extra = dict(payload.get("extra", {}))
        return cls._build(
            warehouse_root,
            source_mirror_root=source_mirror_root,
            benchmark_data_root=benchmark_data_root,
            offline_mode=offline_mode,
            extra=extra,
        )

    @classmethod
    def _build(
        cls,
        warehouse_root: Path,
        source_mirror_root: Path | None = None,
        benchmark_data_root: Path | None = None,
        offline_mode: bool | None = None,
        extra: dict[str, Any] | None = None,
    ) -> "Config":
        warehouse_root = warehouse_root.expanduser().resolve()
        if not warehouse_root.is_dir():
            raise FileNotFoundError(f"Warehouse root not found: {warehouse_root}")
        if not (warehouse_root / CATALOG_RELATIVE_PATH).is_file():
            raise FileNotFoundError(
                f"Warehouse root {warehouse_root} is missing "
                f"{CATALOG_RELATIVE_PATH}. Run the smoke test."
            )

        if source_mirror_root is None:
            env = os.environ.get(ENV_SOURCE_MIRROR)
            if env:
                source_mirror_root = Path(env).expanduser().resolve()
        if benchmark_data_root is None:
            env = os.environ.get(ENV_BENCHMARK_DATA)
            if env:
                benchmark_data_root = Path(env).expanduser().resolve()
        if offline_mode is None:
            offline_mode = os.environ.get(ENV_OFFLINE, "").lower() in {"1", "true", "yes"}

        return cls(
            warehouse_root=warehouse_root,
            source_mirror_root=source_mirror_root,
            benchmark_data_root=benchmark_data_root,
            offline_mode=offline_mode,
            extra=extra or {},
        )

    # ----------------------------------------------------------------- paths

    def catalog_path(self) -> Path:
        return self.warehouse_root / CATALOG_RELATIVE_PATH

    def manifest_path(self) -> Path:
        return self.warehouse_root / MANIFEST_RELATIVE_PATH

    def family_partition(self, family: str) -> Path:
        rel = DEFAULT_PARTITION_GLOBS.get(family)
        if rel is None:
            raise KeyError(f"Unknown family: {family}")
        return self.warehouse_root / rel

    def benchmark_artifact(self, name: str) -> Path:
        if self.benchmark_data_root is None:
            raise RuntimeError(
                "Benchmark data root not configured. Set PROTEOSPHERE_BENCHMARK_DATA "
                "or add benchmark_data_root to proteosphere_config.json."
            )
        return self.benchmark_data_root / name

    def benchmark_download(self, name: str) -> Path:
        """Resolve a third-party benchmark download under ``downloads/``.

        Convention used by the PINDER/PLINDER scripts: each upstream parquet
        sits at ``<benchmark_data_root>/downloads/<name>``.

        If ``benchmark_data_root`` is unset, falls back to
        ``<warehouse_root>/../benchmarks`` so smoke tests work without
        an explicit second config entry.
        """
        root = self.benchmark_data_root
        if root is None:
            root = self.warehouse_root.parent / "benchmarks"
        return root / "downloads" / name

    def source_mirror(self, source_key: str) -> Path | None:
        """Return the local mirror path for a source, if configured."""
        if self.source_mirror_root is None:
            return None
        candidate = self.source_mirror_root / "incoming_mirrors" / source_key
        return candidate if candidate.is_dir() else None

    # --------------------------------------------------------------- summary

    def to_dict(self) -> dict[str, Any]:
        return {
            "warehouse_root": str(self.warehouse_root),
            "source_mirror_root": str(self.source_mirror_root) if self.source_mirror_root else None,
            "benchmark_data_root": str(self.benchmark_data_root) if self.benchmark_data_root else None,
            "offline_mode": self.offline_mode,
            "catalog_path": str(self.catalog_path()),
            "manifest_path": str(self.manifest_path()),
            "extra": self.extra,
        }

    def __repr__(self) -> str:
        return (
            f"Config(warehouse_root={self.warehouse_root}, "
            f"source_mirror_root={self.source_mirror_root}, "
            f"offline_mode={self.offline_mode})"
        )
