from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import statistics
import threading
from datetime import UTC, datetime
from api.model_studio._cache import invalidate_runtime_caches, ttl_cache
from pathlib import Path
from typing import Any
from uuid import uuid4

# Heavy imports are deferred — on this Windows box `import torch` and the
# scipy/numpy chain that sklearn pulls take ~40 minutes on cold start
# (AV scanning hundreds of .pyd files). We push those into the few
# functions that actually need them via :func:`_ensure_torch` and
# :func:`_ensure_sklearn`. The module body itself imports nothing heavy,
# so ``import api.model_studio.runtime`` completes in milliseconds and
# the server can bind its listening socket immediately.
#
# Tried earlier: importlib.util.LazyLoader for torch. That tripped
# scipy.stats' ``is_torch_array()`` check into infinite recursion through
# torch's own ``__init__.py`` cleanup logic. The lazy-on-function-call
# pattern is uglier but doesn't break scipy.

# Cached singletons populated by the _ensure_* helpers.
_torch_mod = None
_sklearn_cache: dict = {}


def _ensure_torch():
    """Import torch on first call and cache the module. Subsequent calls
    are free."""
    global _torch_mod
    if _torch_mod is None:
        import torch as _t
        _torch_mod = _t
    return _torch_mod


def _ensure_sklearn() -> dict:
    """Import the sklearn symbols runtime.py needs on first call."""
    if _sklearn_cache:
        return _sklearn_cache
    from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    from sklearn.neural_network import MLPRegressor
    _sklearn_cache.update({
        "HistGradientBoostingRegressor": HistGradientBoostingRegressor,
        "RandomForestRegressor":         RandomForestRegressor,
        "mean_absolute_error":           mean_absolute_error,
        "mean_squared_error":            mean_squared_error,
        "r2_score":                      r2_score,
        "MLPRegressor":                  MLPRegressor,
    })
    return _sklearn_cache


# Module-level convenience names. These are populated on first access
# via PEP 562 ``__getattr__`` further down, so existing call sites that
# reference ``runtime.HistGradientBoostingRegressor`` (or ``torch``) keep
# working without per-call shim work.

from api.model_studio.contracts import (
    ActivationReadinessReport,
    BetaFeatureGate,
    CandidateDatabaseSummary,
    CandidateDatabaseSummaryV2,
    CandidateDatabaseSummaryV3,
    CandidatePoolSummary,
    DatasetPoolManifest,
    FeatureRecipeSpec,
    GraphRecipeSpec,
    GovernedBridgeManifest,
    GovernedCandidateRow,
    GovernedCandidateRowV3,
    GovernedSubsetManifest,
    GovernedSubsetManifestV2,
    ModelActivationMatrix,
    ModelStudioPipelineSpec,
    PoolPromotionReportV2,
    PreprocessPlanSpec,
    RecommendationItem,
    SplitPlanSpec,
    StudioRunManifest,
    TrainingSetDiagnosticsReport,
    TrainingSetRequestSpec,
    compile_execution_graph,
    validate_pipeline_spec,
)
from core.library.summary_record import (
    ProteinProteinSummaryRecord,
    SummaryProvenancePointer,
    SummaryRecordContext,
)
from core.storage.canonical_store import (
    CanonicalStore,
    CanonicalStoreRecord,
    CanonicalStoreSourceRef,
)
from core.storage.embedding_cache import (
    EmbeddingCacheArtifactPointer,
    EmbeddingCacheCatalog,
    EmbeddingCacheEntry,
    EmbeddingCacheSourceRef,
    EmbeddingModelIdentity,
    EmbeddingRuntimeIdentity,
)
from core.storage.feature_cache import (
    FeatureCacheArtifactPointer,
    FeatureCacheCatalog,
    FeatureCacheEntry,
    FeatureCacheSourceRef,
)
from core.storage.package_manifest import (
    PackageManifest,
    PackageManifestArtifactPointer,
    PackageManifestExample,
    PackageManifestMaterialization,
    PackageManifestRawManifest,
)
from core.storage.planning_index_schema import (
    PlanningIndexEntry,
    PlanningIndexSchema,
    PlanningIndexSourceRecord,
)
from execution.storage_runtime import integrate_storage_runtime
from features.ppi_representation import build_ppi_representation
from training.multimodal.runtime import execute_multimodal_training
from training.runtime.experiment_registry import build_experiment_registry
from training.runtime.portfolio_runner import (
    PortfolioAblationSpec,
    PortfolioCandidateSpec,
    expand_portfolio_matrix,
    run_portfolio_matrix,
)

# Run / build identifiers come straight from URL path components. They
# are joined into filesystem paths in many places below, so we restrict
# the alphabet to a safe set: alphanumerics plus ``.``, ``_``, ``-``.
# This rules out path-separator and traversal attacks before joining.
_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = REPO_ROOT / "artifacts" / "runtime" / "model_studio"
RUN_DIR = RUNTIME_ROOT / "runs"
TRAINING_SET_REQUEST_DIR = RUNTIME_ROOT / "training_set_requests"
TRAINING_SET_BUILD_DIR = RUNTIME_ROOT / "training_set_builds"
GOVERNED_SUBSET_DIR = RUNTIME_ROOT / "governed_subsets"
STAGE2_TRACK_DIR = RUNTIME_ROOT / "stage2_tracks"
FEEDBACK_DIR = RUNTIME_ROOT / "feedback"
SESSION_EVENT_DIR = RUNTIME_ROOT / "session_events"
MISSING_STRUCTURE_SENTINEL = REPO_ROOT / "__missing_structure__"
ROBUST_LATEST = (
    REPO_ROOT / "data" / "reports" / "robust_split_candidates" / "LATEST_ROBUST_SPLIT.json"
)
EXPANDED_LATEST = (
    REPO_ROOT
    / "data"
    / "reports"
    / "expanded_pp_benchmark_candidates"
    / "LATEST_EXPANDED_PP_BENCHMARK.json"
)
RELEASE_ALPHA_LATEST = (
    REPO_ROOT
    / "data"
    / "reports"
    / "model_studio_release_benchmarks"
    / "LATEST_RELEASE_PP_ALPHA_BENCHMARK.json"
)
FINAL_STRUCTURED_LATEST = (
    REPO_ROOT / "data" / "reports" / "final_structured_datasets" / "LATEST.json"
)
EXPANDED_STRUCTURED_LATEST = (
    REPO_ROOT
    / "data"
    / "reports"
    / "expansion_staging"
    / "v2_post_procurement_expanded"
    / "LATEST_PDBBIND_EXPANDED.json"
)
EXPANSION_PROCUREMENT_STATE = (
    REPO_ROOT / "artifacts" / "runtime" / "expansion_procurement_state.json"
)
RUN_STATE_GRACE_SECONDS = 30 * 60

# Lock registry + manifest IO live in ``_persistence.py``. We
# re-export the historical names so existing call sites and tests
# keep working without churn.
from api.model_studio._persistence import (  # noqa: E402
    _STATE,
    read_manifest as _read_manifest,
    read_run_control as _read_run_control,
    register_run_thread as _register_run_thread,
    write_manifest as _write_manifest,
    write_run_control as _write_run_control,
)

# Direct references to the lock dictionaries (some legacy code reads
# ``_RUN_THREADS`` directly to register a thread; the proper API is
# :func:`_register_run_thread` and call sites should migrate to it).
_RUN_LOCKS = _STATE.run_locks
_RUN_LOCKS_GUARD = _STATE.run_locks_guard
_RUN_THREADS = _STATE.run_threads
_RUN_THREADS_GUARD = _STATE.run_threads_guard

HYDROPHOBIC = {"ALA", "VAL", "ILE", "LEU", "MET", "PHE", "TRP", "PRO"}
POLAR = {"SER", "THR", "ASN", "GLN", "TYR", "CYS", "GLY"}
ACIDIC = {"ASP", "GLU"}
BASIC = {"LYS", "ARG", "HIS"}
AROMATIC = {"PHE", "TRP", "TYR", "HIS"}
ELEMENT_ORDER = ("C", "N", "O", "S", "P", "SE", "HALOGEN", "METAL", "OTHER")
AA_ORDER = (
    "ALA",
    "ARG",
    "ASN",
    "ASP",
    "CYS",
    "GLN",
    "GLU",
    "GLY",
    "HIS",
    "ILE",
    "LEU",
    "LYS",
    "MET",
    "PHE",
    "PRO",
    "SER",
    "THR",
    "TRP",
    "TYR",
    "VAL",
)
THERMO_R_KCAL = 0.00198720425864083
DEFAULT_TEMPERATURE_K = 298.15
GOVERNED_PPI_SUBSET_DATASET_REF = "governed_ppi_blended_subset_v1"
GOVERNED_PPI_SUBSET_POOL_ID = f"pool:{GOVERNED_PPI_SUBSET_DATASET_REF}"
GOVERNED_PPI_SUBSET_SOURCE_FAMILY = "governed_ppi_blended_subset"
GOVERNED_PPI_SUBSET_V2_DATASET_REF = "governed_ppi_blended_subset_v2"
GOVERNED_PPI_SUBSET_V2_POOL_ID = f"pool:{GOVERNED_PPI_SUBSET_V2_DATASET_REF}"
GOVERNED_PPI_SUBSET_V2_SOURCE_FAMILY = "governed_ppi_blended_subset_v2"
GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF = "governed_ppi_stage2_candidate_v1"
GOVERNED_PPI_STAGE2_CANDIDATE_POOL_ID = f"pool:{GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF}"
GOVERNED_PPI_STAGE2_CANDIDATE_SOURCE_FAMILY = "governed_ppi_stage2_candidate_v1"
GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF = "governed_ppi_external_beta_candidate_v1"
GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_POOL_ID = (
    f"pool:{GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF}"
)
GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_SOURCE_FAMILY = (
    "governed_ppi_external_beta_candidate_v1"
)
GOVERNED_PL_BRIDGE_PILOT_DATASET_REF = "governed_pl_bridge_pilot_subset_v1"
GOVERNED_PL_BRIDGE_PILOT_POOL_ID = f"pool:{GOVERNED_PL_BRIDGE_PILOT_DATASET_REF}"
GOVERNED_PL_BRIDGE_PILOT_SOURCE_FAMILY = "governed_pl_bridge_pilot"
LOCAL_COPIES_ROOT = REPO_ROOT / "data" / "raw" / "local_copies"
PDBBIND_PL_INDEX = LOCAL_COPIES_ROOT / "pdbbind" / "index" / "INDEX_general_PL.2020R1.lst"
RCSB_STRUCTURE_ROOT = LOCAL_COPIES_ROOT / "structures_rcsb"
EXTRACTED_BOUND_OBJECT_ROOT = LOCAL_COPIES_ROOT / "extracted_bound_objects"
EXTRACTED_CHAIN_ROOT = LOCAL_COPIES_ROOT / "extracted_chains"
EXTRACTED_PROVENANCE_ROOT = LOCAL_COPIES_ROOT / "extracted_provenance"


# ``_utc_now`` and ``_clean_text`` are tiny utilities used by every
# submodule; their canonical implementations now live in ``_text.py``
# so extracted submodules can import them without circling back to
# ``runtime``. The underscore-prefixed aliases here are kept for the
# many in-module callers.
from api.model_studio._text import clean_text as _clean_text  # noqa: E402
from api.model_studio._text import utc_now as _utc_now  # noqa: E402


class RunCancelledError(RuntimeError):
    """Raised when a running Studio job receives a cancellation request."""


def _element_from_atom_name(atom_name: str, fallback: str) -> str:
    text = _clean_text(fallback).upper()
    if text:
        if text in {"F", "CL", "BR", "I"}:
            return "HALOGEN"
        if text in {"ZN", "MG", "MN", "FE", "CA", "CU", "CO", "NI", "NA", "K"}:
            return "METAL"
        return text if text in ELEMENT_ORDER else "OTHER"
    letters = "".join(char for char in atom_name.upper() if char.isalpha())
    if not letters:
        return "OTHER"
    if letters[:2] in {"CL", "BR", "SE", "ZN", "MG", "MN", "FE", "CA", "CU", "CO", "NI"}:
        return _element_from_atom_name("", letters[:2])
    return _element_from_atom_name("", letters[:1])


# Row-level pure helpers live in ``_rows.py``.
from api.model_studio._rows import (  # noqa: E402
    copy_row_with_metadata as _copy_row_with_metadata,
    measurement_type as _measurement_type,
    protein_accession_signature as _protein_accession_signature,
    row_description as _row_description,
)


# Manifest read/write + lock helpers extracted to ``_persistence.py``
# and imported above. The local definitions used to live here.


def _timestamp_age_seconds(timestamp: str | None) -> float | None:
    text = _clean_text(timestamp)
    if not text:
        return None
    try:
        then = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (datetime.now(tz=UTC) - then.astimezone(UTC)).total_seconds()


from api.model_studio._io import load_json as _load_json, save_json as _save_json
# Re-exported under their historical underscore-prefixed names so the
# ~80 internal call sites in this module keep working without churn.


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def persist_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    feedback_id = f"feedback-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    record = {
        "feedback_id": feedback_id,
        "created_at": _utc_now(),
        "study_title": _clean_text(payload.get("study_title")) or "unknown-study",
        "pipeline_id": _clean_text(payload.get("pipeline_id")) or "unknown-pipeline",
        "run_id": _clean_text(payload.get("run_id")) or None,
        "step_id": _clean_text(payload.get("step_id")) or None,
        "category": _clean_text(payload.get("category")) or "general",
        "severity": _clean_text(payload.get("severity")) or "normal",
        "message": _clean_text(payload.get("message")),
        "context": payload.get("context") if isinstance(payload.get("context"), dict) else {},
    }
    if not record["message"]:
        raise ValueError("Feedback message is required.")
    target = FEEDBACK_DIR / f"{feedback_id}.json"
    _save_json(target, record)
    return {
        "feedback_id": feedback_id,
        "path": _artifact_rel(target),
        "record": record,
    }


def persist_session_event(payload: dict[str, Any]) -> dict[str, Any]:
    session_id = _clean_text(payload.get("session_id")) or "anonymous-session"
    event_id = f"event-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"
    normalized_session = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in session_id
    ).strip("-") or "anonymous-session"
    record = {
        "event_id": event_id,
        "session_id": normalized_session,
        "created_at": _utc_now(),
        "event_type": _clean_text(payload.get("event_type")) or "ui_event",
        "step_id": _clean_text(payload.get("step_id")) or None,
        "pipeline_id": _clean_text(payload.get("pipeline_id")) or None,
        "run_id": _clean_text(payload.get("run_id")) or None,
        "detail": _clean_text(payload.get("detail")) or "",
        "context": payload.get("context") if isinstance(payload.get("context"), dict) else {},
    }
    target = SESSION_EVENT_DIR / normalized_session / f"{event_id}.json"
    _save_json(target, record)
    return {
        "event_id": event_id,
        "session_id": normalized_session,
        "path": _artifact_rel(target),
        "record": record,
    }


def _append_log(log_lines: list[str], message: str) -> None:
    log_lines.append(f"[{_utc_now()}] {message}")


def _artifact_rel(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(resolved).replace("\\", "/")


def _split_tokens(value: str) -> tuple[str, ...]:
    normalized = value.replace(";", ",").replace("/", ",")
    return tuple(token for token in (_clean_text(item) for item in normalized.split(",")) if token)


def _safe_float(value: str | None, default: float = 0.0) -> float:
    text = _clean_text(value)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _safe_int(value: str | None, default: int = 0) -> int:
    text = _clean_text(value)
    if not text:
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


# Hardware discovery + execution-placement resolution live in
# ``_hardware.py``. Re-exported here under the historical names so
# every existing ``runtime.discover_hardware_profile(...)`` /
# ``runtime._resolve_execution_placement(...)`` call site keeps
# working unchanged.
from api.model_studio._hardware import (  # noqa: E402
    discover_hardware_profile,
    resolve_execution_placement as _resolve_execution_placement,
)


def _check_for_cancellation(run_dir: Path) -> None:
    control = _read_run_control(run_dir)
    if control.get("cancel_requested"):
        raise RunCancelledError("Cancellation was requested by the user.")


# ``_row_description`` is now imported from ``_rows.py`` (see import
# block above). The original inline definition lived here.


def _row_preview_payload(
    row: BenchmarkRow,
    *,
    inclusion_reason: str,
    split: str | None = None,
    label_type: str = "delta_G",
) -> dict[str, Any]:
    label_payload = _label_payload(row, label_type)
    return {
        "pdb_id": row.pdb_id,
        "description": _row_description(row),
        "label": label_payload["value"],
        "label_type": label_payload["requested_label_type"],
        "label_origin": label_payload["label_origin"],
        "label_provenance": label_payload["conversion_provenance"],
        "assay_family": label_payload["assay_family"],
        "source_family": row.source_dataset,
        "structure_status": "available" if row.structure_file.exists() else "missing",
        "resolution_angstrom": row.resolution,
        "release_year": row.release_year,
        "split": split or row.split,
        "inclusion_reason": inclusion_reason,
    }


def _distance(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _mean_coordinate(coords: list[tuple[float, float, float]]) -> tuple[float, float, float]:
    if not coords:
        return (0.0, 0.0, 0.0)
    return (
        sum(item[0] for item in coords) / len(coords),
        sum(item[1] for item in coords) / len(coords),
        sum(item[2] for item in coords) / len(coords),
    )


def _pearson(y_true: list[float], y_pred: list[float]) -> float:
    if len(y_true) < 2:
        return 0.0
    true_mean = statistics.fmean(y_true)
    pred_mean = statistics.fmean(y_pred)
    num = sum((a - true_mean) * (b - pred_mean) for a, b in zip(y_true, y_pred, strict=True))
    den_left = math.sqrt(sum((a - true_mean) ** 2 for a in y_true))
    den_right = math.sqrt(sum((b - pred_mean) ** 2 for b in y_pred))
    if not den_left or not den_right:
        return 0.0
    return num / (den_left * den_right)


# Core data shapes live in ``_types.py`` so helper modules
# (``_rows.py``, ``_structures.py``, ...) can import them without
# circling back into ``runtime``. Re-exported here under their
# historical names for the ~120 in-module callers.
from api.model_studio._types import (  # noqa: E402
    AtomRecord,
    BenchmarkRow,
    DatasetDescriptor,
    ResidueRecord,
)


def _count_csv_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def list_known_datasets() -> list[dict[str, Any]]:
    datasets: list[DatasetDescriptor] = []
    robust = _load_json(ROBUST_LATEST, {})
    if robust:
        datasets.append(
            DatasetDescriptor(
                dataset_ref="robust_pp_benchmark_v1",
                label="Robust PPI benchmark",
                task_type="protein-protein",
                split_strategy="leakage_resistant_benchmark",
                train_csv=Path(robust["train_csv"]),
                val_csv=Path(robust["val_csv"]) if robust.get("val_csv") else None,
                test_csv=Path(robust["test_csv"]),
                source_manifest=ROBUST_LATEST,
                row_count=_count_csv_rows(Path(robust["train_csv"]))
                + _count_csv_rows(Path(robust["test_csv"])),
                tags=("ppi", "robust", "leakage-resistant"),
                maturity="training_ready_candidate",
                catalog_status="beta",
            )
        )
    expanded = _load_json(EXPANDED_LATEST, {})
    if expanded:
        expanded_train = Path(expanded["train_csv"])
        expanded_test = Path(expanded["test_csv"])
        datasets.append(
            DatasetDescriptor(
                dataset_ref="expanded_pp_benchmark_v1",
                label="Expanded PPI benchmark",
                task_type="protein-protein",
                split_strategy="graph_component_grouped",
                train_csv=expanded_train,
                val_csv=Path(expanded["val_csv"]) if expanded.get("val_csv") else None,
                test_csv=expanded_test,
                source_manifest=EXPANDED_LATEST,
                row_count=_count_csv_rows(expanded_train) + _count_csv_rows(expanded_test),
                tags=("ppi", "expanded", "pdbbind"),
                maturity="training_ready_candidate",
                catalog_status="beta",
            )
        )
    release_manifest = _load_json(RELEASE_ALPHA_LATEST, {})
    if release_manifest:
        release_train = Path(release_manifest["train_csv"])
        release_test = Path(release_manifest["test_csv"])
        datasets.append(
            DatasetDescriptor(
                dataset_ref="release_pp_alpha_benchmark_v1",
                label="Release PPI alpha benchmark",
                task_type="protein-protein",
                split_strategy="leakage_resistant_benchmark",
                train_csv=release_train,
                val_csv=(
                    Path(release_manifest["val_csv"]) if release_manifest.get("val_csv") else None
                ),
                test_csv=release_test,
                source_manifest=RELEASE_ALPHA_LATEST,
                row_count=_count_csv_rows(release_train) + _count_csv_rows(release_test),
                tags=("ppi", "release", "frozen", "structure-backed"),
                maturity="internal_alpha_candidate",
                catalog_status="release",
            )
        )
    if TRAINING_SET_BUILD_DIR.exists():
        for manifest_path in sorted(TRAINING_SET_BUILD_DIR.glob("*/build_manifest.json")):
            manifest = _load_json(manifest_path, {})
            dataset_ref = _clean_text(manifest.get("dataset_ref"))
            if not dataset_ref:
                continue
            datasets.append(
                DatasetDescriptor(
                    dataset_ref=dataset_ref,
                    label=_clean_text(manifest.get("label")) or dataset_ref,
                    task_type=_clean_text(manifest.get("task_type")) or "protein-protein",
                    split_strategy=_clean_text(manifest.get("split_strategy"))
                    or "leakage_resistant_benchmark",
                    train_csv=Path(manifest["train_csv"]),
                    val_csv=Path(manifest["val_csv"]) if manifest.get("val_csv") else None,
                    test_csv=Path(manifest["test_csv"]),
                    source_manifest=manifest_path,
                    row_count=int(manifest.get("row_count") or 0),
                    tags=tuple(manifest.get("tags", ())),
                    maturity=_clean_text(manifest.get("maturity")) or "pilot_candidate",
                    catalog_status="release",
                )
            )
    governed_subset = _materialize_governed_ppi_subset().get("dataset_manifest") or {}
    dataset_ref = _clean_text(governed_subset.get("dataset_ref"))
    if dataset_ref:
        datasets.append(
            DatasetDescriptor(
                dataset_ref=dataset_ref,
                label=_clean_text(governed_subset.get("label")) or dataset_ref,
                task_type=_clean_text(governed_subset.get("task_type")) or "protein-protein",
                split_strategy=_clean_text(governed_subset.get("split_strategy"))
                or "accession_grouped",
                train_csv=Path(governed_subset["train_csv"]),
                val_csv=Path(governed_subset["val_csv"]) if governed_subset.get("val_csv") else None,
                test_csv=Path(governed_subset["test_csv"]),
                source_manifest=Path(governed_subset["source_manifest"]),
                row_count=int(governed_subset.get("row_count") or 0),
                tags=tuple(governed_subset.get("tags", ())),
                maturity=_clean_text(governed_subset.get("maturity")) or "review_pending_candidate",
                catalog_status=_clean_text(governed_subset.get("catalog_status")) or "beta",
            )
        )
    governed_subset_v2 = _materialize_governed_ppi_subset_v2().get("dataset_manifest") or {}
    dataset_ref_v2 = _clean_text(governed_subset_v2.get("dataset_ref"))
    if dataset_ref_v2:
        datasets.append(
            DatasetDescriptor(
                dataset_ref=dataset_ref_v2,
                label=_clean_text(governed_subset_v2.get("label")) or dataset_ref_v2,
                task_type=_clean_text(governed_subset_v2.get("task_type")) or "protein-protein",
                split_strategy=_clean_text(governed_subset_v2.get("split_strategy"))
                or "accession_grouped",
                train_csv=Path(governed_subset_v2["train_csv"]),
                val_csv=Path(governed_subset_v2["val_csv"]) if governed_subset_v2.get("val_csv") else None,
                test_csv=Path(governed_subset_v2["test_csv"]),
                source_manifest=Path(governed_subset_v2["source_manifest"]),
                row_count=int(governed_subset_v2.get("row_count") or 0),
                tags=tuple(governed_subset_v2.get("tags", ())),
                maturity=_clean_text(governed_subset_v2.get("maturity")) or "launchable_governed_candidate",
                catalog_status=_clean_text(governed_subset_v2.get("catalog_status")) or "beta",
            )
        )
    governed_stage2_candidate = _materialize_governed_ppi_stage2_candidate_v1().get("dataset_manifest") or {}
    dataset_ref_stage2 = _clean_text(governed_stage2_candidate.get("dataset_ref"))
    if dataset_ref_stage2:
        datasets.append(
            DatasetDescriptor(
                dataset_ref=dataset_ref_stage2,
                label=_clean_text(governed_stage2_candidate.get("label")) or dataset_ref_stage2,
                task_type=_clean_text(governed_stage2_candidate.get("task_type")) or "protein-protein",
                split_strategy=_clean_text(governed_stage2_candidate.get("split_strategy"))
                or "accession_grouped",
                train_csv=Path(governed_stage2_candidate["train_csv"]),
                val_csv=Path(governed_stage2_candidate["val_csv"]) if governed_stage2_candidate.get("val_csv") else None,
                test_csv=Path(governed_stage2_candidate["test_csv"]),
                source_manifest=Path(governed_stage2_candidate["source_manifest"]),
                row_count=int(governed_stage2_candidate.get("row_count") or 0),
                tags=tuple(governed_stage2_candidate.get("tags", ())),
                maturity=_clean_text(governed_stage2_candidate.get("maturity")) or "review_pending_candidate",
                catalog_status=_clean_text(governed_stage2_candidate.get("catalog_status")) or "beta_soon",
            )
        )
    external_beta_candidate = _materialize_governed_ppi_external_beta_candidate_v1().get(
        "dataset_manifest"
    ) or {}
    dataset_ref_external_beta = _clean_text(external_beta_candidate.get("dataset_ref"))
    if dataset_ref_external_beta:
        datasets.append(
            DatasetDescriptor(
                dataset_ref=dataset_ref_external_beta,
                label=_clean_text(external_beta_candidate.get("label")) or dataset_ref_external_beta,
                task_type=_clean_text(external_beta_candidate.get("task_type")) or "protein-protein",
                split_strategy=_clean_text(external_beta_candidate.get("split_strategy"))
                or "graph_component_grouped",
                train_csv=Path(external_beta_candidate["train_csv"]),
                val_csv=Path(external_beta_candidate["val_csv"])
                if external_beta_candidate.get("val_csv")
                else None,
                test_csv=Path(external_beta_candidate["test_csv"]),
                source_manifest=Path(external_beta_candidate["source_manifest"]),
                row_count=int(external_beta_candidate.get("row_count") or 0),
                tags=tuple(external_beta_candidate.get("tags", ())),
                maturity=_clean_text(external_beta_candidate.get("maturity"))
                or "review_pending_candidate",
                catalog_status=_clean_text(external_beta_candidate.get("catalog_status"))
                or "beta_soon",
            )
        )
    ligand_pilot = _materialize_governed_pl_bridge_pilot_subset_v1().get("dataset_manifest") or {}
    dataset_ref_ligand = _clean_text(ligand_pilot.get("dataset_ref"))
    if dataset_ref_ligand:
        datasets.append(
            DatasetDescriptor(
                dataset_ref=dataset_ref_ligand,
                label=_clean_text(ligand_pilot.get("label")) or dataset_ref_ligand,
                task_type=_clean_text(ligand_pilot.get("task_type")) or "protein-ligand",
                split_strategy=_clean_text(ligand_pilot.get("split_strategy"))
                or "protein_ligand_component_grouped",
                train_csv=Path(ligand_pilot["train_csv"]),
                val_csv=Path(ligand_pilot["val_csv"]) if ligand_pilot.get("val_csv") else None,
                test_csv=Path(ligand_pilot["test_csv"]),
                source_manifest=Path(ligand_pilot["source_manifest"]),
                row_count=int(ligand_pilot.get("row_count") or 0),
                tags=tuple(ligand_pilot.get("tags", ())),
                maturity=_clean_text(ligand_pilot.get("maturity")) or "launchable_ligand_pilot",
                catalog_status=_clean_text(ligand_pilot.get("catalog_status")) or "beta",
            )
        )
    return [item.to_dict() for item in datasets]


def _dataset_source_rows(dataset: DatasetDescriptor) -> list[BenchmarkRow]:
    train_rows, val_rows, test_rows = _load_dataset_rows(dataset)
    return [*train_rows, *val_rows, *test_rows]


def _dataset_balance_metadata(rows: list[BenchmarkRow]) -> dict[str, Any]:
    if not rows:
        return {
            "source_breakdown": {},
            "top_source_share": 0.0,
            "partner_redundancy_hotspots": [],
        }
    source_breakdown = _source_breakdown(rows)
    total = len(rows)
    top_source_share = max(source_breakdown.values()) / total if source_breakdown else 0.0
    partner_counts: dict[str, int] = {}
    for row in rows:
        partner_key = _partner_signature(row)
        partner_counts[partner_key] = partner_counts.get(partner_key, 0) + 1
    hotspots = [
        {"partner_signature": key, "count": count}
        for key, count in sorted(partner_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        if count > 2
    ]
    return {
        "source_breakdown": source_breakdown,
        "top_source_share": round(top_source_share, 4),
        "partner_redundancy_hotspots": hotspots,
    }


def _candidate_row_identity(row: BenchmarkRow) -> tuple[Any, ...]:
    return (
        row.pdb_id,
        row.complex_type,
        tuple(sorted(accession for accession in row.protein_accessions if accession)),
        _clean_text(row.metadata.get("Ligand Canonical Component Id")),
        _clean_text(row.metadata.get("Protein-Ligand Pair Grouping Key")),
        _clean_text(row.source_dataset) or "unknown",
        _measurement_type(row),
        f"{row.exp_dg:.4f}",
    )


def _dataset_truth_boundary(dataset: DatasetDescriptor) -> dict[str, Any]:
    return {
        "dataset_ref": dataset.dataset_ref,
        "catalog_status": dataset.catalog_status,
        "source_manifest": _artifact_rel(dataset.source_manifest),
        "maturity": dataset.maturity,
        "structure_backed": True,
    }


def _derive_exp_dg_from_molar(
    value_molar: float | None,
    *,
    temperature_k: float = DEFAULT_TEMPERATURE_K,
) -> float:
    value = float(value_molar or 0.0)
    if value <= 0.0:
        return float("nan")
    return round(THERMO_R_KCAL * float(temperature_k) * math.log(value), 4)


_PDBBIND_PL_LINE_RE = re.compile(
    r"^(?P<pdb>[0-9A-Za-z]{4})\s+.*?(?P<measurement>Kd|Ki|IC50)\s*=\s*"
    r"(?P<value>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(?P<unit>mM|uM|nM|pM|fM|M)\b"
)
_MOLAR_UNIT_SCALE = {
    "M": 1.0,
    "mM": 1e-3,
    "uM": 1e-6,
    "nM": 1e-9,
    "pM": 1e-12,
    "fM": 1e-15,
}


def _protein_ligand_pair_signature(row: BenchmarkRow) -> str:
    explicit = _clean_text(row.metadata.get("Protein-Ligand Pair Grouping Key"))
    if explicit:
        return explicit
    ligand_id = _clean_text(row.metadata.get("Ligand Canonical Component Id")) or "unknown_ligand"
    protein_signature = _protein_accession_signature(row)
    return f"{protein_signature}::ligand:{ligand_id}"


def _parse_exact_pdbbind_pl_index() -> dict[str, dict[str, Any]]:
    if not PDBBIND_PL_INDEX.exists():
        return {}
    parsed: dict[str, dict[str, Any]] = {}
    for raw_line in PDBBIND_PL_INDEX.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _PDBBIND_PL_LINE_RE.search(line)
        if match is None:
            continue
        measurement_type = _clean_text(match.group("measurement"))
        if measurement_type not in {"Kd", "Ki", "IC50"}:
            continue
        if "incomplete ligand structure" in line.casefold():
            continue
        unit = _clean_text(match.group("unit")) or "M"
        value_molar = float(match.group("value")) * _MOLAR_UNIT_SCALE.get(unit, 1.0)
        pdb_id = _clean_text(match.group("pdb")).upper()
        parsed[pdb_id] = {
            "pdb_id": pdb_id,
            "measurement_type": measurement_type,
            "value_molar": value_molar,
            "raw_affinity": f"{measurement_type}={match.group('value')}{unit}",
            "index_line": raw_line.rstrip(),
        }
    return parsed


def _select_primary_ligand_payload(pdb_id: str) -> dict[str, Any] | None:
    payload_path = EXTRACTED_BOUND_OBJECT_ROOT / f"{pdb_id}.json"
    if not payload_path.exists():
        return None
    try:
        items = json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    candidates = [
        item
        for item in items
        if _clean_text(item.get("component_type")) == "small_molecule"
        and _clean_text(item.get("component_role")) == "primary_binder"
        and tuple(_clean_text(chain) for chain in item.get("chain_ids", ()) if _clean_text(chain))
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            _clean_text(item.get("component_role")) != "primary_binder",
            _clean_text(item.get("component_id")),
        ),
    )[0]


def _protein_chain_rows(pdb_id: str) -> list[dict[str, Any]]:
    payload_path = EXTRACTED_CHAIN_ROOT / f"{pdb_id}.json"
    if not payload_path.exists():
        return []
    try:
        items = json.loads(payload_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not item.get("is_protein"):
            continue
        chain_id = _clean_text(item.get("chain_id"))
        accession = _clean_text(item.get("uniprot_id"))
        if not chain_id or not accession:
            continue
        rows.append(item)
    return rows


def _write_benchmark_rows(path: Path, rows_to_write: list[BenchmarkRow]) -> None:
    fieldnames = [
        "PDB",
        "exp_dG",
        "Source Data Set",
        "Source Family",
        "Complex Type",
        "Mapped Protein Accessions",
        "Ligand Chains",
        "Receptor Chains",
        "Structure File",
        "Resolution (A)",
        "Release Year",
        "Label Temperature (K)",
        "Measurement Type",
        "Affinity Value (M)",
        "Raw Affinity String",
        "Mapped Chain IDs",
        "Partner Role Resolution",
        "Ligand Canonical Component Id",
        "Protein-Ligand Pair Grouping Key",
        "Structure Evidence State",
        "Ligand Bridge Provenance Refs",
        "Source Manifest",
        "Governed Row Id",
        "Governed Canonical IDs",
        "UniRef Cluster",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows_to_write:
            writer.writerow(
                {
                    "PDB": row.pdb_id,
                    "exp_dG": row.exp_dg,
                    "Source Data Set": row.source_dataset,
                    "Source Family": row.metadata.get("Source Family", ""),
                    "Complex Type": row.complex_type,
                    "Mapped Protein Accessions": ";".join(row.protein_accessions),
                    "Ligand Chains": ";".join(row.ligand_chains),
                    "Receptor Chains": ";".join(row.receptor_chains),
                    "Structure File": str(row.structure_file),
                    "Resolution (A)": row.resolution,
                    "Release Year": row.release_year,
                    "Label Temperature (K)": row.temperature_k,
                    "Measurement Type": _measurement_type(row),
                    "Affinity Value (M)": row.metadata.get("Affinity Value (M)", ""),
                    "Raw Affinity String": row.metadata.get("Raw Affinity String", ""),
                    "Mapped Chain IDs": row.metadata.get("Mapped Chain IDs", ""),
                    "Partner Role Resolution": row.metadata.get("Partner Role Resolution", ""),
                    "Ligand Canonical Component Id": row.metadata.get(
                        "Ligand Canonical Component Id", ""
                    ),
                    "Protein-Ligand Pair Grouping Key": row.metadata.get(
                        "Protein-Ligand Pair Grouping Key", ""
                    ),
                    "Structure Evidence State": row.metadata.get("Structure Evidence State", ""),
                    "Ligand Bridge Provenance Refs": row.metadata.get(
                        "Ligand Bridge Provenance Refs", ""
                    ),
                    "Source Manifest": row.metadata.get("Source Manifest", ""),
                    "Governed Row Id": row.metadata.get("Governed Row Id", ""),
                    "Governed Canonical IDs": row.metadata.get("Governed Canonical IDs", ""),
                    "UniRef Cluster": row.metadata.get("UniRef Cluster", ""),
                }
            )


def _label_bin_mix(rows: list[BenchmarkRow]) -> dict[str, int]:
    return _bucket_breakdown(rows, lambda row: _label_bin_name(row.exp_dg))


@ttl_cache(maxsize=2)
def _staged_ppi_bridge_rows() -> tuple[BenchmarkRow, ...]:
    state_payload = _load_structured_bundle_state(EXPANDED_STRUCTURED_LATEST)
    corpus_rows = list((state_payload.get("corpus") or {}).get("rows") or [])
    measurement_rows: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    structure_rows: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in corpus_rows:
        payload = row.get("payload") or {}
        if _clean_text(payload.get("complex_type")) != "protein_protein":
            continue
        canonical_ids = tuple(
            _clean_text(item) for item in row.get("canonical_ids", ()) if _clean_text(item)
        )
        if not canonical_ids:
            continue
        row_family = _clean_text(row.get("row_family"))
        if row_family == "measurement":
            measurement_rows.setdefault(canonical_ids, []).append(row)
        elif row_family == "structure":
            structure_rows.setdefault(canonical_ids, []).append(row)

    def _measurement_priority(item: dict[str, Any]) -> tuple[int, float, str]:
        payload = item.get("payload") or {}
        measurement_type = _clean_text(payload.get("measurement_type")) or "unknown"
        return (
            {"Kd": 0, "Ki": 1, "IC50": 2}.get(measurement_type, 9),
            -float(payload.get("p_affinity") or 0.0),
            _clean_text(item.get("row_id")),
        )

    def _structure_priority(item: dict[str, Any]) -> tuple[float, int, str]:
        payload = item.get("payload") or {}
        resolution = float(payload.get("resolution_angstrom") or 99.0)
        release_year = int(payload.get("release_year") or 0)
        return (resolution, -release_year, _clean_text(item.get("row_id")))

    compiled_rows: list[BenchmarkRow] = []
    for canonical_ids, measurements in measurement_rows.items():
        structures = structure_rows.get(canonical_ids)
        if not structures:
            continue
        measurement = sorted(measurements, key=_measurement_priority)[0]
        structure = sorted(structures, key=_structure_priority)[0]
        measurement_payload = measurement.get("payload") or {}
        structure_payload = structure.get("payload") or {}
        structure_file = Path(_clean_text(structure_payload.get("structure_file_path")) or "")
        if not structure_file.exists():
            continue
        value_molar = float(measurement_payload.get("value_molar_normalized") or 0.0)
        exp_dg = _derive_exp_dg_from_molar(value_molar)
        if exp_dg != exp_dg:
            continue
        temperature_k = float(
            measurement_payload.get("temperature_k")
            or structure_payload.get("temperature_k")
            or DEFAULT_TEMPERATURE_K
        )
        pdb_id = next(
            (
                item.split(":", 1)[1]
                for item in canonical_ids
                if item.startswith("structure:")
            ),
            _clean_text(structure.get("row_id")).split(":")[-1].upper(),
        )
        mapped_chain_ids = tuple(
            _clean_text(item)
            for item in measurement_payload.get("mapped_chain_ids", ())
            if _clean_text(item)
        ) or tuple(
            _clean_text(item)
            for item in structure_payload.get("mapped_chain_ids", ())
            if _clean_text(item)
        )
        protein_accessions = tuple(
            _clean_text(item)
            for item in (
                measurement_payload.get("mapped_protein_accessions")
                or structure_payload.get("mapped_protein_accessions")
                or ()
            )
            if _clean_text(item)
        )
        compiled_rows.append(
            BenchmarkRow(
                split="candidate",
                pdb_id=pdb_id.upper(),
                exp_dg=exp_dg,
                source_dataset="governed_ppi_bridge",
                complex_type="protein_protein",
                protein_accessions=protein_accessions,
                ligand_chains=(),
                receptor_chains=(),
                structure_file=structure_file,
                resolution=float(structure_payload.get("resolution_angstrom") or 0.0),
                release_year=int(structure_payload.get("release_year") or 0),
                temperature_k=temperature_k,
                metadata={
                    "Measurement Type": _clean_text(measurement_payload.get("measurement_type")) or "Kd",
                    "Affinity Value (M)": value_molar,
                    "Raw Affinity String": _clean_text(
                        measurement_payload.get("raw_affinity_string")
                    )
                    or (
                        f"{_clean_text(measurement_payload.get('measurement_type'))}={value_molar:.3g}M"
                        if value_molar
                        else ""
                    ),
                    "Mapped Chain IDs": ";".join(mapped_chain_ids),
                    "Partner Role Resolution": "unresolved_whole_complex_only",
                    "Governed Canonical IDs": ";".join(canonical_ids),
                    "Governed Row Id": _clean_text(measurement.get("row_id")),
                    "Source Family": "expanded_ppi_procurement_bridge",
                    "Assay Family": _clean_text(measurement_payload.get("measurement_type")) or "unknown",
                },
            )
        )
    return tuple(sorted(compiled_rows, key=_row_priority))


@ttl_cache(maxsize=1)
def _ligand_bridge_rows() -> tuple[BenchmarkRow, ...]:
    index_rows = _parse_exact_pdbbind_pl_index()
    compiled_rows: list[BenchmarkRow] = []
    for pdb_id, affinity in sorted(index_rows.items()):
        structure_file = RCSB_STRUCTURE_ROOT / f"{pdb_id}.cif"
        if not structure_file.exists():
            continue
        ligand_payload = _select_primary_ligand_payload(pdb_id)
        if ligand_payload is None:
            continue
        ligand_component_id = _clean_text(ligand_payload.get("component_id"))
        ligand_chains = tuple(
            _clean_text(chain)
            for chain in ligand_payload.get("chain_ids", ())
            if _clean_text(chain)
        )
        if not ligand_component_id or not ligand_chains:
            continue
        protein_rows = _protein_chain_rows(pdb_id)
        receptor_chains = tuple(
            sorted(
                {
                    _clean_text(item.get("chain_id"))
                    for item in protein_rows
                    if _clean_text(item.get("chain_id")) not in ligand_chains
                }
            )
        )
        protein_accessions = tuple(
            sorted(
                {
                    _clean_text(item.get("uniprot_id"))
                    for item in protein_rows
                    if _clean_text(item.get("chain_id")) in receptor_chains
                    and _clean_text(item.get("uniprot_id"))
                }
            )
        )
        if not receptor_chains or not protein_accessions:
            continue
        if set(ligand_chains) & set(receptor_chains):
            continue
        provenance_path = EXTRACTED_PROVENANCE_ROOT / f"{pdb_id}.json"
        provenance_refs = [
            _artifact_rel(PDBBIND_PL_INDEX),
            _artifact_rel(structure_file),
            _artifact_rel(EXTRACTED_BOUND_OBJECT_ROOT / f"{pdb_id}.json"),
            _artifact_rel(EXTRACTED_CHAIN_ROOT / f"{pdb_id}.json"),
        ]
        if provenance_path.exists():
            provenance_refs.append(_artifact_rel(provenance_path))
        pair_grouping_key = f"{'|'.join(protein_accessions)}::{ligand_component_id}"
        compiled_rows.append(
            BenchmarkRow(
                split="candidate",
                pdb_id=pdb_id,
                exp_dg=_derive_exp_dg_from_molar(affinity.get("value_molar")),
                source_dataset=GOVERNED_PL_BRIDGE_PILOT_SOURCE_FAMILY,
                complex_type="protein_ligand",
                protein_accessions=protein_accessions,
                ligand_chains=ligand_chains,
                receptor_chains=receptor_chains,
                structure_file=structure_file,
                resolution=0.0,
                release_year=0,
                temperature_k=DEFAULT_TEMPERATURE_K,
                metadata={
                    "Measurement Type": affinity.get("measurement_type"),
                    "Affinity Value (M)": affinity.get("value_molar"),
                    "Raw Affinity String": affinity.get("raw_affinity"),
                    "Mapped Chain IDs": ";".join(dict.fromkeys((*receptor_chains, *ligand_chains))),
                    "Partner Role Resolution": "native_partner_roles",
                    "Assay Family": f"pdbbind_pl_exact_{str(affinity.get('measurement_type')).lower()}",
                    "Source Family": GOVERNED_PL_BRIDGE_PILOT_SOURCE_FAMILY,
                    "Ligand Canonical Component Id": ligand_component_id,
                    "Protein-Ligand Pair Grouping Key": pair_grouping_key,
                    "Structure Evidence State": "experimental_only",
                    "Ligand Bridge Provenance Refs": ";".join(provenance_refs),
                    "Source Manifest": str(PDBBIND_PL_INDEX),
                    "Governed Row Id": f"{GOVERNED_PL_BRIDGE_PILOT_SOURCE_FAMILY}:{pdb_id}:{ligand_component_id}",
                    "UniRef Cluster": "|".join(protein_accessions),
                },
            )
        )
    return tuple(sorted(compiled_rows, key=_row_priority))


def _select_governed_subset_staged_rows(
    existing_rows: list[BenchmarkRow],
    *,
    target_staged_count: int = 900,
) -> tuple[list[BenchmarkRow], dict[str, Any]]:
    existing_pdbs = {row.pdb_id for row in existing_rows if _clean_text(row.pdb_id)}
    staged_rows: list[BenchmarkRow] = []
    selected: list[BenchmarkRow] = []
    seen_partner_keys: set[str] = set()
    seen_redundancy_keys: dict[str, int] = {}
    exclusion_counts = {
        "existing_pdb_overlap": 0,
        "duplicate_partner_cluster": 0,
        "redundancy_cap": 0,
        "target_cap": 0,
    }
    for row in _staged_ppi_bridge_rows():
        if row.pdb_id in existing_pdbs:
            exclusion_counts["existing_pdb_overlap"] += 1
            continue
        if not row.structure_file.exists():
            continue
        staged_rows.append(row)
        partner_key = _partner_signature(row)
        redundancy_key = _redundancy_cluster_key(row)
        if partner_key in seen_partner_keys:
            exclusion_counts["duplicate_partner_cluster"] += 1
            continue
        if seen_redundancy_keys.get(redundancy_key, 0) >= 1:
            exclusion_counts["redundancy_cap"] += 1
            continue
        if len(selected) >= target_staged_count:
            exclusion_counts["target_cap"] += 1
            continue
        selected.append(row)
        seen_partner_keys.add(partner_key)
        seen_redundancy_keys[redundancy_key] = seen_redundancy_keys.get(redundancy_key, 0) + 1
    diagnostics = {
        "available_non_overlapping_rows": len(staged_rows),
        "selected_rows": len(selected),
        "exclusion_counts": exclusion_counts,
    }
    return selected, diagnostics


def _governed_subset_root(dataset_ref: str) -> Path:
    return GOVERNED_SUBSET_DIR / dataset_ref


def _stage2_track_root(track_id: str) -> Path:
    return STAGE2_TRACK_DIR / track_id


def _mapped_chain_ids(row: BenchmarkRow) -> tuple[str, ...]:
    return tuple(
        _clean_text(item)
        for item in _clean_text(row.metadata.get("Mapped Chain IDs", "")).split(";")
        if _clean_text(item)
    )


def _redundancy_cluster_key(row: BenchmarkRow) -> str:
    if row.complex_type == "protein_ligand":
        return (
            _clean_text(row.metadata.get("Protein-Ligand Pair Grouping Key"))
            or _clean_text(row.metadata.get("Ligand Canonical Component Id"))
            or _protein_ligand_pair_signature(row)
            or row.pdb_id
        )
    return (
        _clean_text(row.metadata.get("Governed Canonical IDs"))
        or _partner_signature(row)
        or _protein_accession_signature(row)
        or row.pdb_id
    )


def _benchmark_governed_row_v3(row: BenchmarkRow) -> GovernedCandidateRowV3:
    source_family = _clean_text(row.metadata.get("Source Family")) or row.source_dataset or "unknown"
    pair_grouping_key = (
        _protein_ligand_pair_signature(row)
        if row.complex_type == "protein_ligand"
        else _partner_signature(row)
    )
    redundancy_cluster_id = (
        _clean_text(row.metadata.get("Protein-Ligand Pair Grouping Key"))
        if row.complex_type == "protein_ligand"
        else _clean_text(row.metadata.get("Governed Canonical IDs"))
    )
    partner_role_resolution = (
        _clean_text(row.metadata.get("Partner Role Resolution"))
        or (
            "native_partner_roles"
            if row.ligand_chains or row.receptor_chains
            else "whole_complex_only_launchable"
            if row.structure_file.exists()
            and len(row.protein_accessions) >= 2
            and len(_mapped_chain_ids(row)) >= 2
            else "unknown"
        )
    )
    measurement_family = _measurement_type(row)
    provenance = tuple(
        item
        for item in (
            _clean_text(row.metadata.get("Source Manifest")),
            _clean_text(row.metadata.get("Governed Row Id")),
            _clean_text(row.metadata.get("Raw Affinity String")),
        )
        if item
    ) or (f"dataset:{row.source_dataset}",)
    structure_backed = row.structure_file.exists()
    governance_state = (
        "governing_ready"
        if structure_backed and partner_role_resolution == "native_partner_roles"
        else "whole_complex_launchable"
        if structure_backed
        and partner_role_resolution == "whole_complex_only_launchable"
        and len(row.protein_accessions) >= 2
        and len(_mapped_chain_ids(row)) >= 2
        else "whole_complex_review_pending"
        if structure_backed
        and partner_role_resolution == "whole_complex_only_review_pending"
        and len(row.protein_accessions) >= 2
        and len(_mapped_chain_ids(row)) >= 2
        else "blocked_pending_acquisition"
        if not structure_backed
        else "candidate_only_non_governing"
    )
    training_eligibility = (
        "launchable_study_eligible"
        if governance_state in {"governing_ready", "whole_complex_launchable"}
        else "blocked_pending_acquisition"
        if governance_state == "blocked_pending_acquisition"
        else "beta_review_only"
    )
    review_notes: list[str] = []
    if governance_state == "whole_complex_launchable":
        review_notes.append(
            "Launchable only through whole_complex_graph plus whole_molecule plus symmetric partner awareness."
        )
    if governance_state == "whole_complex_review_pending":
        review_notes.append(
            "Structurally packagable only through whole_complex_graph plus whole_molecule plus symmetric partner awareness, but still review-pending rather than launch-approved."
        )
    if not structure_backed:
        review_notes.append("Missing structure file blocks native launchability.")
    return GovernedCandidateRowV3(
        canonical_row_id=f"{row.source_dataset}:{row.pdb_id}:{_protein_accession_signature(row)}",
        source_family=source_family,
        source_provenance=provenance,
        measurement_family=measurement_family,
        normalization_state="row_level_compiled",
        provenance_completeness="row_level_compiled",
        structure_backed_readiness="structure_backed" if structure_backed else "missing_structure",
        partner_role_resolution_state=partner_role_resolution or "unknown",
        partner_grouping_key=pair_grouping_key,
        accession_grouping_key=_protein_accession_signature(row),
        uniref_grouping_key=_clean_text(row.metadata.get("UniRef Cluster")) or _protein_accession_signature(row),
        redundancy_cluster_id=redundancy_cluster_id or _redundancy_cluster_key(row),
        admissibility=governance_state,
        governance_state=governance_state,
        training_eligibility=training_eligibility,
        row_family=_clean_text(row.metadata.get("Assay Family"))
        or ("protein_ligand_measurement" if row.complex_type == "protein_ligand" else "protein_protein_measurement"),
        balance_tags=tuple(
            dict.fromkeys(
                item
                for item in (
                    f"source:{source_family}",
                    f"assay:{measurement_family}",
                    f"partner_roles:{partner_role_resolution}",
                )
                if item
            )
        ),
        review_notes=tuple(review_notes),
    )


def _benchmark_dataset_descriptor(
    *,
    dataset_ref: str,
    label: str,
    split_strategy: str,
    source_manifest: Path,
    tags: tuple[str, ...],
    maturity: str,
    catalog_status: str,
) -> DatasetDescriptor | None:
    manifest = _load_json(source_manifest, {})
    if not manifest:
        return None
    return DatasetDescriptor(
        dataset_ref=dataset_ref,
        label=label,
        task_type="protein-protein",
        split_strategy=split_strategy,
        train_csv=Path(manifest["train_csv"]),
        val_csv=Path(manifest["val_csv"]) if manifest.get("val_csv") else None,
        test_csv=Path(manifest["test_csv"]),
        source_manifest=source_manifest,
        row_count=0,
        tags=tags,
        maturity=maturity,
        catalog_status=catalog_status,
    )


def _annotated_benchmark_rows(
    dataset: DatasetDescriptor | None,
    *,
    source_family: str,
    assay_family: str = "benchmark_delta_g",
    filter_missing_structure: bool = True,
) -> list[BenchmarkRow]:
    """Load rows from ``dataset`` and stamp them with subset metadata.

    ``filter_missing_structure`` defaults to True (matches v2 /
    external-beta / stage2 expectations -- structure file must exist).
    Pass False to retain rows whose structure file is absent, which is
    the v1 base materializer's intentional behaviour for the
    robust+expanded blend (the v1 lane materializes structure-less
    rows so downstream review machinery can flag them rather than
    silently dropping).
    """
    if dataset is None:
        return []
    rows: list[BenchmarkRow] = []
    for row in _dataset_source_rows(dataset):
        if filter_missing_structure and not row.structure_file.exists():
            continue
        rows.append(
            _copy_row_with_metadata(
                row,
                split="candidate",
                metadata_updates={
                    "Assay Family": assay_family,
                    "Source Family": source_family,
                    "Partner Role Resolution": "native_partner_roles",
                },
            )
        )
    return rows


def _structured_whole_complex_launchable(
    *,
    source_family: str,
    canonical_ids: tuple[str, ...],
    payload: dict[str, Any],
    row: dict[str, Any],
    governing_status: str,
    training_admissibility: str,
) -> bool:
    if source_family != "expanded_ppi_procurement_bridge":
        return False
    if governing_status not in {
        "governing_ready",
        "support_only_structure_backed",
        "candidate_only_non_governing",
    }:
        return False
    if training_admissibility in {"unknown", "blocked_pending_acquisition"}:
        return False
    if not _structured_payload_has_structure_evidence(payload=payload, row=row):
        return False
    return len(canonical_ids) >= 2


def _structured_payload_structure_refs(
    *,
    payload: dict[str, Any],
    row: dict[str, Any],
) -> tuple[str, ...]:
    refs: list[str] = []
    refs.extend(
        _clean_text(item)
        for item in payload.get("associated_structure_ids_sample", []) or []
        if _clean_text(item)
    )
    pdb_id = _clean_text(payload.get("pdb_id"))
    if pdb_id:
        refs.append(pdb_id)
    refs.extend(
        _clean_text(item)
        for item in row.get("modality_payload_refs", []) or []
        if _clean_text(item)
    )
    if not refs:
        refs.extend(
            _clean_text(item.removeprefix("structure:"))
            for item in row.get("canonical_ids", []) or []
            if _clean_text(item).startswith("structure:")
        )
    return tuple(dict.fromkeys(refs))


def _structured_payload_has_structure_evidence(
    *,
    payload: dict[str, Any],
    row: dict[str, Any],
) -> bool:
    return bool(_structured_payload_structure_refs(payload=payload, row=row))


def _governed_row_v3_from_structured_payload(
    *,
    source_family: str,
    row: dict[str, Any],
) -> GovernedCandidateRowV3:
    payload = row.get("payload") or {}
    canonical_ids = tuple(
        _clean_text(item) for item in row.get("canonical_ids", []) if _clean_text(item)
    )
    seed_accession = _clean_text(row.get("seed_accession"))
    accession_root = _clean_text(payload.get("accession_root")) or seed_accession
    partner_key = "|".join(sorted(canonical_ids)) or accession_root or _clean_text(row.get("row_id"))
    redundancy_key = (
        _clean_text(payload.get("uniref50_cluster"))
        or _clean_text(payload.get("uniref90_cluster"))
        or _clean_text(payload.get("uniref100_cluster"))
        or _clean_text(row.get("relationship_context"))
        or _clean_text(row.get("row_id"))
    )
    governing_status = _clean_text(row.get("governing_status")) or "unknown"
    training_admissibility = _clean_text(row.get("training_admissibility")) or governing_status
    structure_sample = list(_structured_payload_structure_refs(payload=payload, row=row))
    structure_backed_readiness = "structure_backed" if structure_sample else "missing_structure"
    whole_complex_launchable = _structured_whole_complex_launchable(
        source_family=source_family,
        canonical_ids=canonical_ids,
        payload=payload,
        row=row,
        governing_status=governing_status,
        training_admissibility=training_admissibility,
    )
    partner_role_resolution_state = (
        "whole_complex_only_launchable"
        if whole_complex_launchable
        else "whole_complex_only_review_pending"
        if governing_status in {"governing_ready", "support_only_structure_backed"}
        else "unresolved_partner_roles"
    )
    review_notes = [
        "Structured staged rows stay governed through the canonical row authority."
    ]
    if whole_complex_launchable:
        review_notes.append(
            "Row is launchable only through whole_complex_graph plus whole_molecule plus symmetric partner awareness."
        )
    else:
        review_notes.append(
            "Structured staged rows remain under promotion review until row-level provenance, admissibility, and partner-role semantics are signed off."
        )
    if not structure_sample:
        review_notes.append("No associated structure sample is recorded for this staged row.")
    if training_admissibility != "governing_ready" and not whole_complex_launchable:
        review_notes.append(
            "Training admissibility remains below launchable-study-eligible for this staged row."
        )
    return GovernedCandidateRowV3(
        canonical_row_id=_clean_text(row.get("row_id")) or partner_key,
        source_family=source_family,
        source_provenance=tuple(
            _clean_text(item)
            for item in row.get("source_provenance_refs", [])
            if _clean_text(item)
        ),
        measurement_family=(
            _clean_text(payload.get("measurement_type"))
            or _clean_text(payload.get("measurement_kind"))
            or _clean_text(row.get("row_family"))
            or "unknown"
        ),
        normalization_state=(
            "row_level_compiled"
            if _clean_text(row.get("row_id")) and training_admissibility != "unknown"
            else "partial_row_metadata"
        ),
        provenance_completeness=(
            "row_level_compiled"
            if row.get("source_provenance_refs")
            else "partial_row_metadata"
        ),
        structure_backed_readiness=structure_backed_readiness,
        partner_role_resolution_state=partner_role_resolution_state,
        partner_grouping_key=partner_key,
        accession_grouping_key=accession_root or partner_key,
        uniref_grouping_key=(
            _clean_text(payload.get("uniref50_cluster"))
            or _clean_text(payload.get("uniref90_cluster"))
            or _clean_text(payload.get("uniref100_cluster"))
            or accession_root
            or partner_key
        ),
        redundancy_cluster_id=redundancy_key,
        admissibility="whole_complex_only_launchable" if whole_complex_launchable else training_admissibility,
        governance_state="whole_complex_only_launchable" if whole_complex_launchable else governing_status,
        training_eligibility=(
            "launchable_study_eligible"
            if whole_complex_launchable
            else _governed_training_eligibility(governing_status, training_admissibility)
        ),
        row_family=_clean_text(row.get("row_family")) or "unknown",
        balance_tags=tuple(
            dict.fromkeys(
                item
                for item in (
                    f"source:{source_family}",
                    f"row_family:{_clean_text(row.get('row_family')) or 'unknown'}",
                    (
                        f"cluster:{_clean_text(payload.get('uniref50_cluster'))}"
                        if _clean_text(payload.get("uniref50_cluster"))
                        else ""
                    ),
                )
                if item
            )
        ),
        review_notes=tuple(review_notes),
    )


def _select_governed_subset_v2_staged_rows(
    existing_rows: list[BenchmarkRow],
    *,
    target_staged_count: int = 900,
) -> tuple[list[BenchmarkRow], dict[str, Any]]:
    existing_pdbs = {row.pdb_id for row in existing_rows if _clean_text(row.pdb_id)}
    selected: list[BenchmarkRow] = []
    seen_partner_keys: set[str] = set()
    redundancy_counts: dict[str, int] = {}
    exclusion_counts = {
        "existing_pdb_overlap": 0,
        "missing_structure": 0,
        "insufficient_partner_mapping": 0,
        "duplicate_partner_cluster": 0,
        "redundancy_cap": 0,
        "target_cap": 0,
    }
    for row in _staged_ppi_bridge_rows():
        if row.pdb_id in existing_pdbs:
            exclusion_counts["existing_pdb_overlap"] += 1
            continue
        if not row.structure_file.exists():
            exclusion_counts["missing_structure"] += 1
            continue
        if len(row.protein_accessions) < 2 or len(_mapped_chain_ids(row)) < 2:
            exclusion_counts["insufficient_partner_mapping"] += 1
            continue
        partner_key = _partner_signature(row)
        redundancy_key = _redundancy_cluster_key(row)
        if partner_key in seen_partner_keys:
            exclusion_counts["duplicate_partner_cluster"] += 1
            continue
        if redundancy_counts.get(redundancy_key, 0) >= 1:
            exclusion_counts["redundancy_cap"] += 1
            continue
        if len(selected) >= target_staged_count:
            exclusion_counts["target_cap"] += 1
            continue
        selected.append(
            _copy_row_with_metadata(
                row,
                source_dataset=GOVERNED_PPI_SUBSET_V2_SOURCE_FAMILY,
                metadata_updates={
                    "Source Family": "expanded_ppi_procurement_bridge",
                    "Partner Role Resolution": "whole_complex_only_launchable",
                    "Subset Scope Constraint": "whole_complex_graph+whole_molecule+symmetric",
                },
            )
        )
        seen_partner_keys.add(partner_key)
        redundancy_counts[redundancy_key] = redundancy_counts.get(redundancy_key, 0) + 1
    diagnostics = {
        "available_structure_backed_rows": len(_staged_ppi_bridge_rows()),
        "selected_rows": len(selected),
        "exclusion_counts": exclusion_counts,
        "selection_rule": "structure_backed_pair_rows_with_whole_complex_launchability",
    }
    return selected, diagnostics


@ttl_cache(maxsize=1)
# ---------------------------------------------------------------------------
# Governed-subset materializers
# ---------------------------------------------------------------------------
#
# The five ``_materialize_governed_*`` functions below are structurally
# similar: each loads a couple of source manifests, builds
# ``DatasetDescriptor`` records, stamps subset-specific metadata onto
# every row, and returns a ``subset_manifest`` payload. The natural
# refactor is a single config-driven routine taking a
# ``GovernedSubsetConfig`` dataclass. That refactor is deferred until
# the beta-readiness tests in ``tests/unit/test_model_studio_beta_readiness.py``
# can run in CI (they need data under ``data/reports/`` that is
# currently gitignored) -- without those tests the refactor would be
# unverifiable. See the May 2026 review (P2-3) for the full plan.

def _materialize_governed_ppi_subset_v2() -> dict[str, Any]:
    # The inline DatasetDescriptor + row-copy boilerplate that used to
    # live here was identical to ``_benchmark_dataset_descriptor`` +
    # ``_annotated_benchmark_rows``. Using the helpers eliminates ~60
    # lines of repeated structure with no behaviour change -- both
    # helpers were extracted from the same pattern in
    # ``_materialize_governed_ppi_stage2_candidate_v1`` and friends.
    release_dataset = _benchmark_dataset_descriptor(
        dataset_ref="release_pp_alpha_benchmark_v1",
        label="Release PPI alpha benchmark",
        split_strategy="leakage_resistant_benchmark",
        source_manifest=RELEASE_ALPHA_LATEST,
        tags=("ppi", "release"),
        maturity="internal_alpha_candidate",
        catalog_status="release",
    )
    expanded_dataset = _benchmark_dataset_descriptor(
        dataset_ref="expanded_pp_benchmark_v1",
        label="Expanded PPI benchmark",
        split_strategy="graph_component_grouped",
        source_manifest=EXPANDED_LATEST,
        tags=("ppi", "expanded"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    if release_dataset is None or expanded_dataset is None:
        # Either source manifest is missing; preserve the empty-state
        # contract pinned by tests/unit/api/test_model_studio_materializers.
        return {}
    release_rows = _annotated_benchmark_rows(
        release_dataset, source_family="release_pp_alpha_benchmark_v1"
    )
    expanded_rows = _annotated_benchmark_rows(
        expanded_dataset, source_family="expanded_pp_benchmark_v1"
    )
    staged_rows, staged_diagnostics = _select_governed_subset_v2_staged_rows(
        [*release_rows, *expanded_rows]
    )
    combined_rows = [*release_rows, *expanded_rows, *staged_rows]
    split_rows, split_diagnostics = _compile_split_rows(
        combined_rows,
        SplitPlanSpec(
            plan_id="split:governed_ppi_blended_subset_v2",
            objective="accession_grouped",
            grouping_policy="accession_grouped",
            holdout_policy="governed_structure_backed_subset_v2",
            train_fraction=0.7,
            val_fraction=0.1,
            test_fraction=0.2,
        ),
    )
    subset_root = _governed_subset_root(GOVERNED_PPI_SUBSET_V2_DATASET_REF)
    subset_root.mkdir(parents=True, exist_ok=True)
    train_csv = subset_root / "train.csv"
    val_csv = subset_root / "val.csv"
    test_csv = subset_root / "test.csv"
    _write_benchmark_rows(train_csv, split_rows["train"])
    _write_benchmark_rows(val_csv, split_rows["val"])
    _write_benchmark_rows(test_csv, split_rows["test"])

    row_count = len(combined_rows)
    source_rows = {
        "release_pp_alpha_benchmark_v1": len(release_rows),
        "expanded_pp_benchmark_v1": len(expanded_rows),
        "expanded_ppi_procurement_bridge": len(staged_rows),
    }
    source_family_mix = dict(source_rows)
    assay_family_mix = _bucket_breakdown(
        combined_rows,
        lambda row: row.metadata.get("Assay Family") or _measurement_type(row),
    )
    label_bin_mix = _label_bin_mix(combined_rows)
    structure_coverage = round(
        sum(1 for row in combined_rows if row.structure_file.exists()) / max(row_count, 1),
        4,
    )
    source_share_ok = max(source_rows.values()) / max(row_count, 1) <= 0.45 if source_rows else True
    assay_share_ok = (
        max(assay_family_mix.values()) / max(row_count, 1) <= 0.60 if assay_family_mix else True
    )
    partner_counts = _bucket_breakdown(combined_rows, _partner_signature)
    partner_cluster_ok = (
        max(partner_counts.values()) / max(row_count, 1) <= 0.05 if partner_counts else True
    )
    redundancy_counts = _bucket_breakdown(
        combined_rows,
        _redundancy_cluster_key,
    )
    redundancy_cluster_ok = (
        max(redundancy_counts.values()) / max(row_count, 1) <= 0.03 if redundancy_counts else True
    )
    release_expanded_overlap = sum(
        1
        for row in release_rows
        if any(
            _candidate_row_identity(row) == _candidate_row_identity(other)
            for other in expanded_rows
        )
    )
    overlap_diagnostics = [
        f"release_vs_expanded_overlap={release_expanded_overlap}",
        f"staged_selection={staged_diagnostics.get('selected_rows', 0)}",
        "Staged rows are constrained to whole_complex_graph + whole_molecule + symmetric partner awareness.",
    ]
    blockers: list[str] = []
    if row_count < 1500:
        blockers.append("Governed subset v2 remains below the 1,500-row promotion threshold.")
    if not source_share_ok:
        blockers.append("One source family still exceeds the 45% dominance threshold.")
    if not assay_share_ok:
        blockers.append("One assay family still exceeds the 60% dominance threshold.")
    if structure_coverage < 1.0:
        blockers.append("Subset is not fully structure-backed yet.")
    launchability_reason = (
        "Launchable now in the broadened PPI beta lane. This subset is fully structure-backed and balanced enough for beta use, but staged rows remain constrained to whole-complex graphs with symmetric partner awareness until native partner-role resolution lands."
        if not blockers
        else "Review-pending candidate for the broadened PPI beta lane."
    )
    truth_boundary = {
        "dataset_ref": GOVERNED_PPI_SUBSET_V2_DATASET_REF,
        "catalog_status": "beta",
        "source_manifest": _artifact_rel(subset_root / "dataset_manifest.json"),
        "structure_backed": structure_coverage >= 1.0,
        "structure_coverage": structure_coverage,
        "governed_subset": True,
        "graph_partner_roles_resolved": False,
        "partner_role_resolution": "mixed_native_and_whole_complex_only_launchable",
        "whole_complex_only_for_staged_rows": True,
        "launchability_reason": launchability_reason,
        "promotion_readiness": "launchable_now" if not blockers else "review_pending_candidate",
    }
    balancing = {
        "source_family_mix": source_family_mix,
        "selected_measurement_type_counts": assay_family_mix,
        "label_bin_mix": label_bin_mix,
        "quality_verdict": "launchable_governed_subset" if not blockers else "review_pending_governed_subset",
        "staged_row_selection": staged_diagnostics,
        "split_overlap_diagnostics": {
            "release_vs_expanded_overlap": release_expanded_overlap,
            "partner_cluster_hotspots": [
                {"partner_signature": key, "count": count}
                for key, count in sorted(partner_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
                if count > 1
            ],
        },
        "cap_checks": {
            "source_family_leq_45pct": source_share_ok,
            "assay_family_leq_60pct": assay_share_ok,
            "partner_cluster_leq_5pct": partner_cluster_ok,
            "redundancy_cluster_leq_3pct": redundancy_cluster_ok,
        },
    }
    dataset_manifest = {
        "dataset_ref": GOVERNED_PPI_SUBSET_V2_DATASET_REF,
        "label": "Governed blended PPI subset v2",
        "task_type": "protein-protein",
        "split_strategy": "accession_grouped",
        "train_csv": str(train_csv),
        "val_csv": str(val_csv),
        "test_csv": str(test_csv),
        "row_count": row_count,
        "source_manifest": str(subset_root / "dataset_manifest.json"),
        "tags": [
            "ppi",
            "governed-subset",
            "promotion-approved",
            "structure-backed",
            "whole-complex-constrained-staged-rows",
        ],
        "maturity": "launchable_governed_candidate" if not blockers else "review_pending_candidate",
        "catalog_status": "beta",
        "truth_boundary": truth_boundary,
        "balancing": balancing,
        "split_preview": split_diagnostics,
        "created_at": _utc_now(),
    }
    _save_json(subset_root / "dataset_manifest.json", dataset_manifest)
    subset_manifest = GovernedSubsetManifestV2(
        subset_id=f"subset:{GOVERNED_PPI_SUBSET_V2_DATASET_REF}",
        label="Governed blended PPI subset v2",
        promoted_dataset_ref=GOVERNED_PPI_SUBSET_V2_DATASET_REF,
        row_count=row_count,
        source_rows=source_rows,
        balancing_policy="balance_first_structure_backed_subset",
        source_family_mix=source_family_mix,
        assay_family_mix=assay_family_mix,
        label_bin_mix=label_bin_mix,
        overlap_diagnostics=tuple(overlap_diagnostics),
        exclusion_reasons=tuple(
            f"{key}={value}"
            for key, value in sorted((staged_diagnostics.get("exclusion_counts") or {}).items())
            if value
        ),
        promotion_readiness="launchable_now" if not blockers else "review_pending_candidate",
        review_signoff_state="wave_4_ready_for_freeze" if not blockers else "wave_2_pending_reviews",
        status="launchable_now" if not blockers else "review_pending",
        launchability_reason=launchability_reason,
        blockers=tuple(blockers),
        required_reviewers=("Kepler", "Euler", "Ampere", "Mill", "Bacon", "McClintock"),
        required_matrix_tests=("preview_training_set_request", "build_training_set", "run_matrix", "compare_runs"),
        caps_met={
            "source_family_leq_45pct": source_share_ok,
            "assay_family_leq_60pct": assay_share_ok,
            "partner_cluster_leq_5pct": partner_cluster_ok,
            "redundancy_cluster_leq_3pct": redundancy_cluster_ok,
        },
        notes=(
            "Subset v2 blends the release and expanded launchable pools with structure-backed staged bridge rows.",
            "Staged bridge rows are launchable only through whole-complex graphs and symmetric partner awareness.",
            "Release/expanded overlap remains explicitly disclosed in the subset diagnostics rather than silently hidden.",
        ),
    )
    _save_json(subset_root / "governed_subset_manifest_v2.json", subset_manifest.to_dict())
    return {
        "dataset_manifest": dataset_manifest,
        "subset_manifest": subset_manifest.to_dict(),
    }


def _select_stage2_candidate_rows(
    rows: list[BenchmarkRow],
    *,
    target_count: int,
) -> list[BenchmarkRow]:
    selected: list[BenchmarkRow] = []
    partner_counts: dict[str, int] = {}
    redundancy_counts: dict[str, int] = {}
    max_partner_count = max(1, int(target_count * 0.05))
    max_redundancy_count = max(1, int(target_count * 0.03))
    for row in rows:
        partner_key = _partner_signature(row)
        redundancy_key = _redundancy_cluster_key(row)
        if partner_counts.get(partner_key, 0) >= max_partner_count:
            continue
        if redundancy_counts.get(redundancy_key, 0) >= max_redundancy_count:
            continue
        selected.append(
            _copy_row_with_metadata(
                row,
                metadata_updates={
                    "Source Family": _clean_text(row.metadata.get("Source Family"))
                    or _clean_text(row.source_dataset)
                    or "unknown"
                },
            )
        )
        partner_counts[partner_key] = partner_counts.get(partner_key, 0) + 1
        redundancy_counts[redundancy_key] = redundancy_counts.get(redundancy_key, 0) + 1
        if len(selected) >= target_count:
            break
    return selected


@ttl_cache(maxsize=1)
def _materialize_governed_ppi_stage2_candidate_v1() -> dict[str, Any]:
    release_dataset = _benchmark_dataset_descriptor(
        dataset_ref="release_pp_alpha_benchmark_v1",
        label="Release PPI alpha benchmark",
        split_strategy="leakage_resistant_benchmark",
        source_manifest=RELEASE_ALPHA_LATEST,
        tags=("ppi", "release"),
        maturity="internal_alpha_candidate",
        catalog_status="release",
    )
    expanded_dataset = _benchmark_dataset_descriptor(
        dataset_ref="expanded_pp_benchmark_v1",
        label="Expanded PPI benchmark",
        split_strategy="graph_component_grouped",
        source_manifest=EXPANDED_LATEST,
        tags=("ppi", "expanded"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    release_rows = _select_stage2_candidate_rows(
        _annotated_benchmark_rows(
            release_dataset,
            source_family="release_pp_alpha_benchmark_v1",
        ),
        target_count=450,
    )
    expanded_rows = _select_stage2_candidate_rows(
        _annotated_benchmark_rows(
            expanded_dataset,
            source_family="expanded_pp_benchmark_v1",
        ),
        target_count=450,
    )
    staged_rows = _select_stage2_candidate_rows(
        [
            _copy_row_with_metadata(
                row,
                source_dataset=GOVERNED_PPI_STAGE2_CANDIDATE_SOURCE_FAMILY,
                metadata_updates={
                    "Source Family": "expanded_ppi_procurement_bridge",
                    "Partner Role Resolution": "whole_complex_only_launchable",
                    "Subset Scope Constraint": "whole_complex_graph+whole_molecule+symmetric",
                },
            )
            for row in _staged_ppi_bridge_rows()
            if row.structure_file.exists() and len(row.protein_accessions) >= 2 and len(_mapped_chain_ids(row)) >= 2
        ],
        target_count=650,
    )
    combined_rows = [*release_rows, *expanded_rows, *staged_rows]
    if not combined_rows:
        return {}
    split_rows, split_diagnostics = _compile_split_rows(
        combined_rows,
        SplitPlanSpec(
            plan_id="split:governed_ppi_stage2_candidate_v1",
            objective="accession_grouped",
            grouping_policy="accession_grouped",
            holdout_policy="governed_structure_backed_stage2_candidate",
            train_fraction=0.7,
            val_fraction=0.1,
            test_fraction=0.2,
        ),
    )
    subset_root = _governed_subset_root(GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF)
    subset_root.mkdir(parents=True, exist_ok=True)
    train_csv = subset_root / "train.csv"
    val_csv = subset_root / "val.csv"
    test_csv = subset_root / "test.csv"
    _write_benchmark_rows(train_csv, split_rows["train"])
    _write_benchmark_rows(val_csv, split_rows["val"])
    _write_benchmark_rows(test_csv, split_rows["test"])

    row_count = len(combined_rows)
    source_rows = {
        "release_pp_alpha_benchmark_v1": len(release_rows),
        "expanded_pp_benchmark_v1": len(expanded_rows),
        "expanded_ppi_procurement_bridge": len(staged_rows),
    }
    source_family_mix = dict(source_rows)
    assay_family_mix = _bucket_breakdown(
        combined_rows,
        lambda row: row.metadata.get("Assay Family") or _measurement_type(row),
    )
    label_bin_mix = _label_bin_mix(combined_rows)
    structure_coverage = round(
        sum(1 for row in combined_rows if row.structure_file.exists()) / max(row_count, 1),
        4,
    )
    partner_counts = _bucket_breakdown(combined_rows, _partner_signature)
    redundancy_counts = _bucket_breakdown(
        combined_rows,
        _redundancy_cluster_key,
    )
    source_share_ok = max(source_rows.values()) / max(row_count, 1) <= 0.45 if source_rows else True
    assay_share_ok = max(assay_family_mix.values()) / max(row_count, 1) <= 0.60 if assay_family_mix else True
    partner_cluster_ok = max(partner_counts.values()) / max(row_count, 1) <= 0.05 if partner_counts else True
    redundancy_cluster_ok = (
        max(redundancy_counts.values()) / max(row_count, 1) <= 0.03 if redundancy_counts else True
    )
    blockers: list[str] = []
    if row_count < 1500:
        blockers.append("Stage 2 governed subset remains below the 1,500-row promotion threshold.")
    if not source_share_ok:
        blockers.append("One source family still exceeds the 45% dominance threshold.")
    if not assay_share_ok:
        blockers.append("One assay family still exceeds the 60% dominance threshold.")
    if not partner_cluster_ok:
        blockers.append("A partner/accession cluster still exceeds the 5% threshold.")
    if not redundancy_cluster_ok:
        blockers.append("A structural redundancy cluster still exceeds the 3% threshold.")
    if structure_coverage < 1.0:
        blockers.append("Subset is not fully structure-backed yet.")
    blockers.append(
        "Stage 2 partner-role and PyRosetta/free-state review remains pending for staged rows in this candidate subset."
    )
    overlap_diagnostics = [
        f"release_rows={len(release_rows)}",
        f"expanded_rows={len(expanded_rows)}",
        f"staged_rows={len(staged_rows)}",
        "This subset is compiled as a Stage 2 promotion candidate rather than a launchable default benchmark.",
    ]
    launchability_reason = (
        "Review-pending Stage 2 governed PPI subset candidate. Balance and structure caps are compiled explicitly, but staged-row Stage 2 scientific review is still required before launch."
    )
    truth_boundary = {
        "dataset_ref": GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF,
        "catalog_status": "beta_soon",
        "source_manifest": _artifact_rel(subset_root / "dataset_manifest.json"),
        "structure_backed": structure_coverage >= 1.0,
        "structure_coverage": structure_coverage,
        "governed_subset": True,
        "graph_partner_roles_resolved": False,
        "partner_role_resolution": "mixed_native_and_stage2_review_pending",
        "whole_complex_only_for_staged_rows": True,
        "launchability_reason": launchability_reason,
        "promotion_readiness": "review_pending_candidate",
    }
    balancing = {
        "source_family_mix": source_family_mix,
        "selected_measurement_type_counts": assay_family_mix,
        "label_bin_mix": label_bin_mix,
        "quality_verdict": "stage2_promotion_candidate",
        "split_overlap_diagnostics": {
            "partner_cluster_hotspots": [
                {"partner_signature": key, "count": count}
                for key, count in sorted(partner_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
                if count > 1
            ],
            "redundancy_cluster_hotspots": [
                {"cluster": key, "count": count}
                for key, count in sorted(redundancy_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
                if count > 1
            ],
        },
        "cap_checks": {
            "source_family_leq_45pct": source_share_ok,
            "assay_family_leq_60pct": assay_share_ok,
            "partner_cluster_leq_5pct": partner_cluster_ok,
            "redundancy_cluster_leq_3pct": redundancy_cluster_ok,
        },
    }
    dataset_manifest = {
        "dataset_ref": GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF,
        "label": "Governed PPI Stage 2 candidate subset",
        "task_type": "protein-protein",
        "split_strategy": "accession_grouped",
        "train_csv": str(train_csv),
        "val_csv": str(val_csv),
        "test_csv": str(test_csv),
        "row_count": row_count,
        "source_manifest": str(subset_root / "dataset_manifest.json"),
        "tags": [
            "ppi",
            "governed-subset",
            "stage2-candidate",
            "structure-backed",
            "review-pending",
        ],
        "maturity": "review_pending_candidate",
        "catalog_status": "beta_soon",
        "truth_boundary": truth_boundary,
        "balancing": balancing,
        "split_preview": split_diagnostics,
        "created_at": _utc_now(),
    }
    _save_json(subset_root / "dataset_manifest.json", dataset_manifest)
    subset_manifest = GovernedSubsetManifestV2(
        subset_id=f"subset:{GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF}",
        label="Governed PPI Stage 2 candidate subset",
        promoted_dataset_ref=GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF,
        row_count=row_count,
        source_rows=source_rows,
        balancing_policy="balance_first_stage2_promotion_candidate",
        source_family_mix=source_family_mix,
        assay_family_mix=assay_family_mix,
        label_bin_mix=label_bin_mix,
        overlap_diagnostics=tuple(overlap_diagnostics),
        promotion_readiness="review_pending_candidate",
        review_signoff_state="wave_1_pending_reviews",
        status="review_pending",
        launchability_reason=launchability_reason,
        blockers=tuple(dict.fromkeys(blockers)),
        required_reviewers=("Kepler", "Euler", "Ampere", "Mill", "Bacon", "McClintock"),
        required_matrix_tests=(
            "preview_training_set_request",
            "build_training_set",
            "stage2_scientific_review",
            "compare_runs",
        ),
        caps_met={
            "source_family_leq_45pct": source_share_ok,
            "assay_family_leq_60pct": assay_share_ok,
            "partner_cluster_leq_5pct": partner_cluster_ok,
            "redundancy_cluster_leq_3pct": redundancy_cluster_ok,
        },
        notes=(
            "Stage 2 candidate blends release, expanded, and staged governed rows into a review-pending promotion subset.",
            "This subset is intentionally not launchable until Stage 2 partner-role and scientific track reviews are complete.",
        ),
    )
    _save_json(subset_root / "governed_subset_manifest_v2.json", subset_manifest.to_dict())
    return {
        "dataset_manifest": dataset_manifest,
        "subset_manifest": subset_manifest.to_dict(),
    }


@ttl_cache(maxsize=1)
def _materialize_governed_ppi_external_beta_candidate_v1() -> dict[str, Any]:
    # See ``_materialize_governed_ppi_subset_v2`` -- same dedup: the
    # inline DatasetDescriptor + row-copy boilerplate is replaced with
    # the shared ``_benchmark_dataset_descriptor`` +
    # ``_annotated_benchmark_rows`` helpers.
    release_dataset = _benchmark_dataset_descriptor(
        dataset_ref="release_pp_alpha_benchmark_v1",
        label="Release PPI alpha benchmark",
        split_strategy="leakage_resistant_benchmark",
        source_manifest=RELEASE_ALPHA_LATEST,
        tags=("ppi", "release"),
        maturity="internal_alpha_candidate",
        catalog_status="release",
    )
    expanded_dataset = _benchmark_dataset_descriptor(
        dataset_ref="expanded_pp_benchmark_v1",
        label="Expanded PPI benchmark",
        split_strategy="graph_component_grouped",
        source_manifest=EXPANDED_LATEST,
        tags=("ppi", "expanded"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    if release_dataset is None or expanded_dataset is None:
        return {}
    release_rows = _annotated_benchmark_rows(
        release_dataset, source_family="release_pp_alpha_benchmark_v1"
    )
    expanded_rows = _annotated_benchmark_rows(
        expanded_dataset, source_family="expanded_pp_benchmark_v1"
    )
    staged_rows, staged_diagnostics = _select_governed_subset_v2_staged_rows(
        [*release_rows, *expanded_rows],
        target_staged_count=850,
    )
    combined_rows = [*release_rows, *expanded_rows, *staged_rows]
    if not combined_rows:
        return {}
    split_rows, split_diagnostics = _compile_split_rows(
        combined_rows,
        SplitPlanSpec(
            plan_id="split:governed_ppi_external_beta_candidate_v1",
            objective="graph_component_grouped",
            grouping_policy="graph_component_grouped",
            holdout_policy="governed_external_beta_candidate",
            train_fraction=0.7,
            val_fraction=0.1,
            test_fraction=0.2,
        ),
    )
    subset_root = _governed_subset_root(GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF)
    subset_root.mkdir(parents=True, exist_ok=True)
    train_csv = subset_root / "train.csv"
    val_csv = subset_root / "val.csv"
    test_csv = subset_root / "test.csv"
    _write_benchmark_rows(train_csv, split_rows["train"])
    _write_benchmark_rows(val_csv, split_rows["val"])
    _write_benchmark_rows(test_csv, split_rows["test"])

    row_count = len(combined_rows)
    source_rows = {
        "release_pp_alpha_benchmark_v1": len(release_rows),
        "expanded_pp_benchmark_v1": len(expanded_rows),
        "expanded_ppi_procurement_bridge": len(staged_rows),
    }
    source_family_mix = dict(source_rows)
    assay_family_mix = _bucket_breakdown(
        combined_rows,
        lambda row: row.metadata.get("Assay Family") or _measurement_type(row),
    )
    label_bin_mix = _label_bin_mix(combined_rows)
    structure_coverage = round(
        sum(1 for row in combined_rows if row.structure_file.exists()) / max(row_count, 1),
        4,
    )
    source_share_ok = max(source_rows.values()) / max(row_count, 1) <= 0.45 if source_rows else True
    assay_share_ok = max(assay_family_mix.values()) / max(row_count, 1) <= 0.60 if assay_family_mix else True
    partner_counts = _bucket_breakdown(combined_rows, _partner_signature)
    partner_cluster_ok = (
        max(partner_counts.values()) / max(row_count, 1) <= 0.05 if partner_counts else True
    )
    redundancy_counts = _bucket_breakdown(
        combined_rows,
        _redundancy_cluster_key,
    )
    redundancy_cluster_ok = (
        max(redundancy_counts.values()) / max(row_count, 1) <= 0.03 if redundancy_counts else True
    )
    overlap_diagnostics = [
        "release_plus_expanded provide the frozen benchmark anchor for this candidate.",
        f"staged_selection={staged_diagnostics.get('selected_rows', 0)}",
        f"release_rows={len(release_rows)}",
        f"expanded_rows={len(expanded_rows)}",
        "This candidate is compiled as the controlled external beta launch subset with explicit staged-row whole-complex launch constraints and disclosed redundancy hotspots.",
    ]
    blockers: list[str] = []
    if row_count < 1500:
        blockers.append("External beta candidate remains below the 1,500-row promotion threshold.")
    if not source_share_ok:
        blockers.append("One source-bucket share still exceeds the 45% dominance threshold.")
    if not assay_share_ok:
        blockers.append("One assay family still exceeds the 60% dominance threshold.")
    if structure_coverage < 1.0:
        blockers.append("Subset is not fully structure-backed yet.")
    if not partner_cluster_ok:
        blockers.append("A partner/accession cluster still exceeds the 5% threshold.")
    if not redundancy_cluster_ok:
        blockers.append("A redundancy cluster still exceeds the 3% threshold.")
    launchability_reason = (
        "Launchable now for the controlled external beta lane. This governed subset meets the external-beta size and source-bucket/assay balance targets, remains fully structure-backed, and keeps staged procurement rows under explicit whole-complex symmetric launch scope with disclosed overlap diagnostics."
        if not blockers
        else "Review-pending governed external-beta candidate. It meets most external-beta size and balance targets, but remaining blockers still prevent invited-user promotion."
    )
    truth_boundary = {
        "dataset_ref": GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF,
        "catalog_status": "beta",
        "source_manifest": _artifact_rel(subset_root / "dataset_manifest.json"),
        "structure_backed": structure_coverage >= 1.0,
        "structure_coverage": structure_coverage,
        "governed_subset": True,
        "graph_partner_roles_resolved": False,
        "partner_role_resolution": "mixed_native_and_whole_complex_only_launchable",
        "whole_complex_only_for_staged_rows": True,
        "launchability_reason": launchability_reason,
        "promotion_readiness": "launchable_now" if not blockers else "review_pending_candidate",
    }
    balancing = {
        "source_family_mix": source_family_mix,
        "selected_measurement_type_counts": assay_family_mix,
        "label_bin_mix": label_bin_mix,
        "quality_verdict": (
            "launchable_controlled_external_beta_candidate"
            if not blockers
            else "review_pending_controlled_external_beta_candidate"
        ),
        "staged_row_selection": staged_diagnostics,
        "split_overlap_diagnostics": {
            "partner_cluster_hotspots": [
                {"partner_signature": key, "count": count}
                for key, count in sorted(partner_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
                if count > 1
            ],
            "redundancy_cluster_hotspots": [
                {"cluster": key, "count": count}
                for key, count in sorted(redundancy_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
                if count > 1
            ],
        },
        "cap_checks": {
            "source_family_leq_45pct": source_share_ok,
            "assay_family_leq_60pct": assay_share_ok,
            "partner_cluster_leq_5pct": partner_cluster_ok,
            "redundancy_cluster_leq_3pct": redundancy_cluster_ok,
        },
    }
    dataset_manifest = {
        "dataset_ref": GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF,
        "label": "Governed external beta candidate subset",
        "task_type": "protein-protein",
        "split_strategy": "graph_component_grouped",
        "train_csv": str(train_csv),
        "val_csv": str(val_csv),
        "test_csv": str(test_csv),
        "row_count": row_count,
        "source_manifest": str(subset_root / "dataset_manifest.json"),
        "tags": [
            "ppi",
            "governed-subset",
            "controlled-external-beta-candidate",
            "structure-backed",
            "launchable-now" if not blockers else "review-pending",
        ],
        "maturity": "launchable_governed_candidate" if not blockers else "review_pending_candidate",
        "catalog_status": "beta",
        "truth_boundary": truth_boundary,
        "balancing": balancing,
        "split_preview": split_diagnostics,
        "created_at": _utc_now(),
    }
    _save_json(subset_root / "dataset_manifest.json", dataset_manifest)
    subset_manifest = GovernedSubsetManifestV2(
        subset_id=f"subset:{GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF}",
        label="Governed external beta candidate subset",
        promoted_dataset_ref=GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF,
        row_count=row_count,
        source_rows=source_rows,
        balancing_policy="balance_first_controlled_external_beta_candidate",
        source_family_mix=source_family_mix,
        assay_family_mix=assay_family_mix,
        label_bin_mix=label_bin_mix,
        overlap_diagnostics=tuple(overlap_diagnostics),
        promotion_readiness="launchable_now" if not blockers else "review_pending_candidate",
        review_signoff_state="controlled_external_beta_ready" if not blockers else "external_beta_signoff_pending",
        status="launchable_now" if not blockers else "review_pending",
        launchability_reason=launchability_reason,
        blockers=tuple(dict.fromkeys(blockers)),
        required_reviewers=("Kepler", "Euler", "Ampere", "Mill", "Bacon", "McClintock"),
        required_matrix_tests=(
            "preview_training_set_request",
            "build_training_set",
            "run_matrix",
            "compare_runs",
            "external_beta_rehearsal",
        ),
        caps_met={
            "source_family_leq_45pct": source_share_ok,
            "assay_family_leq_60pct": assay_share_ok,
            "partner_cluster_leq_5pct": partner_cluster_ok,
            "redundancy_cluster_leq_3pct": redundancy_cluster_ok,
        },
        notes=(
            "External beta candidate blends the release anchor, expanded benchmark rows, and staged governed bridge rows into the controlled external beta launch subset.",
            "Staged rows remain launchable only through whole-complex graphs and symmetric partner awareness until native partner-role resolution lands.",
            "Redundancy hotspots remain disclosed explicitly in diagnostics rather than hidden by silent pruning.",
        ),
    )
    _save_json(subset_root / "governed_subset_manifest_v2.json", subset_manifest.to_dict())
    return {
        "dataset_manifest": dataset_manifest,
        "subset_manifest": subset_manifest.to_dict(),
    }


@ttl_cache(maxsize=1)
def _materialize_governed_pl_bridge_pilot_subset_v1() -> dict[str, Any]:
    rows = list(_ligand_bridge_rows())
    if not rows:
        return {}
    split_rows, split_diagnostics = _compile_split_rows(
        rows,
        SplitPlanSpec(
            plan_id="split:governed_pl_bridge_pilot_subset_v1",
            objective="protein_ligand_component_grouped",
            grouping_policy="protein_ligand_component_grouped",
            holdout_policy="protein_ligand_pair_holdout",
            train_fraction=0.7,
            val_fraction=0.1,
            test_fraction=0.2,
        ),
    )
    subset_root = _governed_subset_root(GOVERNED_PL_BRIDGE_PILOT_DATASET_REF)
    subset_root.mkdir(parents=True, exist_ok=True)
    train_csv = subset_root / "train.csv"
    val_csv = subset_root / "val.csv"
    test_csv = subset_root / "test.csv"
    _write_benchmark_rows(train_csv, split_rows["train"])
    _write_benchmark_rows(val_csv, split_rows["val"])
    _write_benchmark_rows(test_csv, split_rows["test"])
    row_count = sum(len(items) for items in split_rows.values())
    source_rows = _source_breakdown(rows)
    assay_family_mix = _bucket_breakdown(rows, _measurement_type)
    label_bin_mix = _label_bin_mix(rows)
    pair_keys = {
        _protein_ligand_pair_signature(row)
        for row in rows
        if _protein_ligand_pair_signature(row)
    }
    truth_boundary = {
        "row_level_provenance_state": "row_level_compiled",
        "measurement_normalization_state": "row_level_compiled",
        "admissibility_flag_state": "row_level_compiled",
        "structure_backed": True,
        "structure_source_policy": "experimental_only",
        "structure_substitution_policy": "missing_structure_never_launchable",
        "bridge_provenance_role": "provenance_support_not_structure_substitute",
        "quality_verdict": "launchable_ligand_pilot",
        "complex_type": "protein_ligand",
        "source_family": GOVERNED_PL_BRIDGE_PILOT_SOURCE_FAMILY,
    }
    balancing = {
        "source_rows": source_rows,
        "selected_measurement_type_counts": assay_family_mix,
        "label_bin_mix": label_bin_mix,
        "pair_group_count": len(pair_keys),
        "quality_verdict": "launchable_ligand_pilot",
        "structure_backed_only": True,
        "launchable_model_families": ["graphsage", "multimodal_fusion"],
        "blocked_model_families": ["gin", "gcn", "gat", "atom_graph"],
        "split_preview": split_diagnostics,
    }
    dataset_manifest = {
        "dataset_ref": GOVERNED_PL_BRIDGE_PILOT_DATASET_REF,
        "label": "Governed protein-ligand bridge pilot subset",
        "task_type": "protein-ligand",
        "split_strategy": "protein_ligand_component_grouped",
        "train_csv": str(train_csv),
        "val_csv": str(val_csv),
        "test_csv": str(test_csv),
        "row_count": row_count,
        "source_manifest": str(subset_root / "dataset_manifest.json"),
        "tags": [
            "protein-ligand",
            "governed-subset",
            "structure-backed-only",
            "experimental_only",
            "delta_g_regression",
        ],
        "maturity": "launchable_ligand_pilot",
        "catalog_status": "beta",
        "truth_boundary": truth_boundary,
        "balancing": balancing,
        "split_preview": split_diagnostics,
        "created_at": _utc_now(),
    }
    _save_json(subset_root / "dataset_manifest.json", dataset_manifest)
    subset_manifest = GovernedSubsetManifestV2(
        subset_id=f"subset:{GOVERNED_PL_BRIDGE_PILOT_DATASET_REF}",
        label="Governed protein-ligand bridge pilot subset",
        promoted_dataset_ref=GOVERNED_PL_BRIDGE_PILOT_DATASET_REF,
        row_count=row_count,
        source_rows=source_rows,
        balancing_policy="structure_backed_pair_grouped_ligand_pilot",
        source_family_mix=source_rows,
        assay_family_mix=assay_family_mix,
        label_bin_mix=label_bin_mix,
        overlap_diagnostics=(
            "protein_ligand_component_grouped split groups canonical protein-ligand pair keys.",
            "Rows without local structure or explicit ligand identity are excluded from the launchable pilot.",
        ),
        promotion_readiness="launchable_now",
        review_signoff_state="wave_6_ready_for_beta",
        status="launchable_now",
        launchability_reason=(
            "Structure-backed protein-ligand pilot is launchable with canonical ligand identity, "
            "explicit bridge provenance, and pair-group split diagnostics."
        ),
        blockers=(),
        required_reviewers=("Kepler", "Euler", "Ampere", "Mill", "Bacon", "McClintock"),
        required_matrix_tests=(
            "preview_training_set_request",
            "build_training_set",
            "ligand_pilot_matrix",
            "external_beta_rehearsal",
        ),
        caps_met={
            "structure_backed_only": True,
            "canonical_provenance_complete": True,
            "pair_group_split_ready": True,
            "compare_export_provenance_complete": True,
        },
        notes=(
            "Launchable pilot is limited to exact Kd/Ki-derived delta_G rows with local experimental structures.",
            "Bridge-backed provenance supports row auditability, but missing structure never becomes launchable.",
            "Launchable model families are limited to graphsage and multimodal_fusion.",
        ),
    )
    _save_json(subset_root / "governed_subset_manifest_v2.json", subset_manifest.to_dict())
    return {
        "dataset_manifest": dataset_manifest,
        "subset_manifest": subset_manifest.to_dict(),
    }


@ttl_cache(maxsize=8)
def _load_structured_bundle_state(path: Path) -> dict[str, Any]:
    def _load_optional_json(raw_path: str) -> dict[str, Any]:
        cleaned = _clean_text(raw_path)
        if not cleaned:
            return {}
        candidate = Path(cleaned)
        if not candidate.exists() or not candidate.is_file():
            return {}
        return _load_json(candidate, {})

    state = _load_json(path, {})
    if not state:
        return {}
    bundle_manifest = _load_optional_json(state.get("bundle_manifest_path", ""))
    corpus = _load_optional_json(
        state.get("corpus_path", "") or bundle_manifest.get("corpus_path", "")
    )
    baseline_sidecar = _load_optional_json(bundle_manifest.get("baseline_sidecar_path", ""))
    multimodal_sidecar = _load_optional_json(bundle_manifest.get("multimodal_sidecar_path", ""))
    return {
        "state": state,
        "bundle_manifest": bundle_manifest,
        "corpus": corpus,
        "baseline_sidecar": baseline_sidecar,
        "multimodal_sidecar": multimodal_sidecar,
    }


def _build_structured_pool_manifest(
    *,
    pool_id: str,
    label: str,
    source_family: str,
    dataset_ref: str,
    split_provenance: str,
    maturity: str,
    status: str,
    state_payload: dict[str, Any],
    notes: tuple[str, ...],
) -> DatasetPoolManifest | None:
    if not state_payload:
        return None
    bundle_manifest = state_payload.get("bundle_manifest") or {}
    corpus = state_payload.get("corpus") or {}
    summary = corpus.get("summary") or bundle_manifest
    baseline_sidecar = state_payload.get("baseline_sidecar") or {}
    multimodal_sidecar = state_payload.get("multimodal_sidecar") or {}
    row_count = int(
        summary.get("row_count")
        or bundle_manifest.get("corpus_row_count")
        or multimodal_sidecar.get("summary", {}).get("corpus_row_count")
        or 0
    )
    label_count = int(summary.get("measurement_count") or 0)
    structure_present = (
        int(summary.get("structure_file_coverage_counts", {}).get("present") or 0)
        or int(summary.get("structure_count") or 0)
    )
    structure_total = int(summary.get("structure_count") or structure_present or row_count)
    structure_coverage = (
        round(structure_present / structure_total, 4) if structure_total else 0.0
    )
    label_coverage = round(label_count / row_count, 4) if row_count else 0.0
    balancing_metadata = {
        "row_family_counts": dict(summary.get("row_family_counts") or {}),
        "governing_status_counts": dict(summary.get("governing_status_counts") or {}),
        "training_admissibility_counts": dict(
            summary.get("training_admissibility_counts") or {}
        ),
        "complex_type_counts": dict(summary.get("complex_type_counts") or {}),
        "structure_file_coverage_counts": dict(
            summary.get("structure_file_coverage_counts") or {}
        ),
        "strict_governing_training_view_count": int(
            summary.get("strict_governing_training_view_count")
            or bundle_manifest.get("strict_governing_training_view_count")
            or baseline_sidecar.get("summary", {}).get("governing_ready_example_count")
            or 0
        ),
        "all_visible_training_candidates_view_count": int(
            summary.get("all_visible_training_candidates_view_count")
            or bundle_manifest.get("visible_training_candidate_count")
            or baseline_sidecar.get("summary", {}).get("all_visible_training_candidates_view_count")
            or 0
        ),
        "multimodal_selected_example_count": int(
            multimodal_sidecar.get("summary", {}).get("example_count") or 0
        ),
    }
    truth_boundary = dict(bundle_manifest.get("truth_boundary") or {})
    truth_boundary.update(corpus.get("truth_boundary") or {})
    return DatasetPoolManifest(
        pool_id=pool_id,
        label=label,
        source_family=source_family,
        dataset_refs=(dataset_ref,),
        row_count=row_count,
        structure_coverage=structure_coverage,
        label_coverage=label_coverage,
        split_provenance=split_provenance,
        maturity=maturity,
        truth_boundary=truth_boundary,
        balancing_metadata=balancing_metadata,
        status=status,
        notes=notes,
    )


def _load_latest_candidate_artifact(latest_manifest: Path) -> dict[str, Any]:
    latest = _load_json(latest_manifest, {})
    artifact_text = _clean_text(latest.get("artifact_json", ""))
    if not artifact_text:
        return {}
    artifact_path = Path(artifact_text)
    return _load_json(artifact_path, {}) if artifact_path.is_file() else {}


def _augment_pool_with_candidate_artifact(
    pool: DatasetPoolManifest,
    *,
    artifact: dict[str, Any],
) -> None:
    if not artifact:
        return
    if "robust_split" in artifact:
        assessment = (((artifact.get("quality_assessment") or {}).get("assessment")) or {})
        summary = assessment.get("summary") or {}
        core_pool = artifact.get("core_pool_summary") or {}
        pool.balancing_metadata.update(
            {
                "core_pool_summary": core_pool,
                "robust_overlap_summary": {
                    "uniref50_cluster_overlap_count": int(
                        summary.get("uniref50_cluster_overlap_count") or 0
                    ),
                    "shared_partner_overlap_count": int(
                        summary.get("shared_partner_overlap_count") or 0
                    ),
                    "flagged_structure_pair_count": int(
                        summary.get("flagged_structure_pair_count") or 0
                    ),
                    "direct_protein_overlap_count": int(
                        summary.get("direct_protein_overlap_count") or 0
                    ),
                },
                "quality_verdict": _clean_text(summary.get("verdict")) or "unknown",
            }
        )
        pool.truth_boundary.update(
            {
                "candidate_artifact_id": _clean_text(artifact.get("artifact_id")) or None,
                "quality_assessment_status": _clean_text(assessment.get("status")) or None,
            }
        )
        return
    selection_summary = artifact.get("selection_summary") or {}
    split_summary = artifact.get("split_summary") or {}
    candidate_universe = artifact.get("candidate_universe") or {}
    selected_measurement_counts = dict(selection_summary.get("selected_measurement_type_counts") or {})
    train_bin_counts = dict(split_summary.get("train_bin_counts") or {})
    test_bin_counts = dict(split_summary.get("test_bin_counts") or {})
    pool.balancing_metadata.update(
        {
            "selected_measurement_type_counts": selected_measurement_counts,
            "selected_exp_dg_stats": dict(selection_summary.get("selected_exp_dG_stats") or {}),
            "selected_component_count": int(selection_summary.get("selected_component_count") or 0),
            "selected_total_count": int(selection_summary.get("selected_total_count") or 0),
            "candidate_universe_counts": {
                "total_count": int(candidate_universe.get("count") or 0),
                "protein_protein_count": int(candidate_universe.get("ppi_candidate_count") or 0),
            },
            "split_bin_counts": {
                "train": train_bin_counts,
                "test": test_bin_counts,
            },
        }
    )
    measurement_total = sum(selected_measurement_counts.values()) or 0
    kd_share = (
        selected_measurement_counts.get("Kd", 0) / measurement_total if measurement_total else 0.0
    )
    pool.truth_boundary.update(
        {
            "candidate_artifact_id": _clean_text(artifact.get("artifact_id")) or None,
            "candidate_universe_count": int(candidate_universe.get("count") or 0),
            "measurement_skew_kd_share": round(kd_share, 4),
        }
    )


def _procurement_bridge_metadata(
    procurement_state: dict[str, Any],
    structured_state: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset_results = procurement_state.get("dataset_results", {})
    status_counts: dict[str, int] = {}
    source_mix: dict[str, int] = {}
    completed_files = 0
    total_files = 0
    completed_datasets = 0
    dataset_count = 0
    for dataset_name, item in sorted(dataset_results.items()):
        dataset_count += 1
        status = _clean_text(item.get("status")) or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        source_mix[_clean_text(item.get("source")) or dataset_name] = int(
            item.get("completed_count") or 0
        )
        completed_files += int(item.get("completed_count") or 0)
        total_files += int(item.get("file_count") or 0)
        if status == "completed":
            completed_datasets += 1
    bundle_manifest = structured_state.get("bundle_manifest") or {}
    corpus = structured_state.get("corpus") or {}
    summary = corpus.get("summary") or bundle_manifest
    bridge_row_count = int(
        summary.get("row_count")
        or bundle_manifest.get("corpus_row_count")
        or 0
    )
    strict_governing = int(
        (summary.get("strict_governing_training_view_count") or 0)
        or (bundle_manifest.get("strict_governing_training_view_count") or 0)
    )
    readiness = {
        "row_level_provenance": "row_level_compiled",
        "measurement_type_normalization": "row_level_compiled",
        "partner_grouping_keys": "row_level_compiled",
        "accession_grouping_keys": "row_level_compiled",
        "admissibility_flags": "row_level_compiled",
    }
    balancing_metadata = {
        "procurement_dataset_statuses": {
            name: _clean_text(item.get("status")) or "unknown"
            for name, item in sorted(dataset_results.items())
        },
        "procurement_completed_file_count": completed_files,
        "procurement_total_file_count": total_files,
        "procurement_dataset_count": dataset_count,
        "procurement_completed_dataset_count": completed_datasets,
        "procurement_active_dataset": _clean_text(procurement_state.get("active_dataset")) or None,
        "procurement_status_counts": status_counts,
        "procurement_source_mix": source_mix,
        "procurement_bridge_row_count": bridge_row_count,
        "strict_governing_training_view_count": strict_governing,
        "governance_readiness": readiness,
        "quality_verdict": "row_governed_but_still_gated" if completed_files else "procurement_not_ready",
    }
    truth_boundary = {
        "procurement_state_path": _artifact_rel(EXPANSION_PROCUREMENT_STATE),
        "governed_bridge_state": "row_level_bridge_compiled_but_gated",
        "row_level_provenance_state": readiness["row_level_provenance"],
        "measurement_normalization_state": readiness["measurement_type_normalization"],
        "partner_grouping_state": readiness["partner_grouping_keys"],
        "accession_grouping_state": readiness["accession_grouping_keys"],
        "admissibility_flag_state": readiness["admissibility_flags"],
    }
    return balancing_metadata, truth_boundary


def _governed_training_eligibility(
    governing_status: str,
    training_admissibility: str,
) -> str:
    governing = _clean_text(governing_status) or "unknown"
    admissibility = _clean_text(training_admissibility) or "unknown"
    if governing == "governing_ready" and admissibility == "governing_ready":
        return "launchable_study_eligible"
    if "blocked" in governing or "blocked" in admissibility:
        return "blocked_pending_acquisition"
    if "support_only" in governing or "support_only" in admissibility:
        return "beta_review_only"
    if "candidate_only" in governing or "candidate_only" in admissibility:
        return "beta_review_only"
    return "non_governing"


def _governed_row_from_structured_payload(
    *,
    source_family: str,
    row: dict[str, Any],
) -> GovernedCandidateRow:
    payload = row.get("payload") or {}
    canonical_ids = tuple(_clean_text(item) for item in row.get("canonical_ids", []) if _clean_text(item))
    seed_accession = _clean_text(row.get("seed_accession"))
    accession_root = _clean_text(payload.get("accession_root")) or seed_accession
    partner_key = "|".join(sorted(canonical_ids)) or seed_accession or _clean_text(row.get("row_id"))
    redundancy_key = (
        _clean_text(payload.get("uniref50_cluster"))
        or _clean_text(payload.get("uniref90_cluster"))
        or _clean_text(payload.get("uniref100_cluster"))
        or "|".join(_clean_text(item) for item in payload.get("associated_structure_ids_sample", [])[:4] if _clean_text(item))
        or _clean_text(row.get("relationship_context"))
        or _clean_text(row.get("row_id"))
    )
    measurement_type = (
        _clean_text(payload.get("measurement_type"))
        or _clean_text(payload.get("measurement_kind"))
        or _clean_text(row.get("row_family"))
        or "unknown"
    )
    row_provenance = tuple(
        _clean_text(item) for item in row.get("source_provenance_refs", []) if _clean_text(item)
    )
    governing_status = _clean_text(row.get("governing_status")) or "unknown"
    training_admissibility = _clean_text(row.get("training_admissibility")) or governing_status
    whole_complex_launchable = _structured_whole_complex_launchable(
        source_family=source_family,
        canonical_ids=canonical_ids,
        payload=payload,
        row=row,
        governing_status=governing_status,
        training_admissibility=training_admissibility,
    )
    normalization_state = (
        "row_level_compiled"
        if row_provenance
        and training_admissibility != "unknown"
        and governing_status != "unknown"
        and (canonical_ids or accession_root or _clean_text(row.get("row_id")))
        else "partial_row_metadata"
    )
    balance_tags = tuple(
        dict.fromkeys(
            item
            for item in (
                f"source:{source_family}",
                f"row_family:{_clean_text(row.get('row_family')) or 'unknown'}",
                f"context:{_clean_text(row.get('relationship_context')) or 'unknown'}",
                (
                    f"complex:{_clean_text(payload.get('complex_type'))}"
                    if _clean_text(payload.get("complex_type"))
                    else ""
                ),
                (
                    f"cluster:{_clean_text(payload.get('uniref50_cluster'))}"
                    if _clean_text(payload.get("uniref50_cluster"))
                    else ""
                ),
            )
            if item
        )
    )
    return GovernedCandidateRow(
        canonical_row_id=_clean_text(row.get("row_id")) or partner_key,
        source_family=source_family,
        source_provenance=row_provenance,
        measurement_type=measurement_type,
        normalization_state=normalization_state,
        partner_grouping_key=partner_key,
        accession_grouping_key=accession_root or partner_key,
        structural_redundancy_key=redundancy_key,
        admissibility="whole_complex_only_launchable" if whole_complex_launchable else training_admissibility,
        governing_status="whole_complex_only_launchable" if whole_complex_launchable else governing_status,
        training_eligibility=(
            "launchable_study_eligible"
            if whole_complex_launchable
            else _governed_training_eligibility(governing_status, training_admissibility)
        ),
        row_family=_clean_text(row.get("row_family")) or "unknown",
        balance_tags=balance_tags,
    )


@ttl_cache(maxsize=4)
def _governed_bridge_rows_by_source() -> dict[str, tuple[GovernedCandidateRow, ...]]:
    final_structured_state = _load_structured_bundle_state(FINAL_STRUCTURED_LATEST)
    expanded_structured_state = _load_structured_bundle_state(EXPANDED_STRUCTURED_LATEST)
    return {
        "final_structured_candidates_v1": tuple(
            _governed_row_from_structured_payload(
                source_family="final_structured_candidates_v1",
                row=row,
            )
            for row in list((final_structured_state.get("corpus") or {}).get("rows") or [])
        ),
        "expanded_ppi_procurement_bridge": tuple(
            _governed_row_from_structured_payload(
                source_family="expanded_ppi_procurement_bridge",
                row=row,
            )
            for row in list((expanded_structured_state.get("corpus") or {}).get("rows") or [])
        ),
    }


def _compile_governed_bridge_manifest(
    *,
    bridge_id: str,
    source_family: str,
    governed_rows: list[GovernedCandidateRow],
    notes: tuple[str, ...] = (),
) -> GovernedBridgeManifest:
    readiness_counts: dict[str, int] = {}
    assay_mix: dict[str, int] = {}
    redundancy_counts: dict[str, int] = {}
    compiled_rows = 0
    for row in governed_rows:
        readiness_counts[row.training_eligibility] = readiness_counts.get(row.training_eligibility, 0) + 1
        assay_mix[row.measurement_type] = assay_mix.get(row.measurement_type, 0) + 1
        if row.structural_redundancy_key:
            redundancy_counts[row.structural_redundancy_key] = (
                redundancy_counts.get(row.structural_redundancy_key, 0) + 1
            )
        if row.normalization_state == "row_level_compiled":
            compiled_rows += 1
    governing_ready_count = readiness_counts.get("launchable_study_eligible", 0)
    promotion_readiness = (
        "candidate_promotion_ready_for_review"
        if governing_ready_count >= 24
        else "row_governed_but_still_gated"
        if governed_rows
        else "pending_bridge_compilation"
    )
    launchability_reason = (
        "Governed rows are compiled, but the staged bridge remains gated until governing-ready coverage and promotion review expand."
        if governed_rows and governing_ready_count < 24
        else "Bridge has enough governing-ready rows to justify promotion review."
        if governing_ready_count >= 24
        else "No governed rows are available yet."
    )
    sample_rows = tuple(
        {
            "canonical_row_id": row.canonical_row_id,
            "row_family": row.row_family,
            "measurement_type": row.measurement_type,
            "governing_status": row.governing_status,
            "training_eligibility": row.training_eligibility,
            "accession_grouping_key": row.accession_grouping_key,
            "structural_redundancy_key": row.structural_redundancy_key,
        }
        for row in governed_rows[:8]
    )
    hotspots = [
        key
        for key, count in sorted(redundancy_counts.items(), key=lambda item: (-item[1], item[0]))
        if count >= 8
    ][:3]
    manifest_notes = list(notes)
    if assay_mix:
        manifest_notes.append(
            "Assay / row-family mix: "
            + ", ".join(f"{key}={value}" for key, value in sorted(assay_mix.items()))
        )
    if hotspots:
        manifest_notes.append(
            "Redundancy hotspots remain around: " + ", ".join(hotspots)
        )
    completeness = (
        "row_level_compiled"
        if governed_rows and compiled_rows == len(governed_rows)
        else "partial_row_metadata"
        if governed_rows
        else "missing"
    )
    return GovernedBridgeManifest(
        bridge_id=bridge_id,
        source_family=source_family,
        row_count=len(governed_rows),
        readiness_counts=readiness_counts,
        provenance_completeness=completeness,
        normalization_completeness=completeness,
        admissibility_completeness=completeness,
        governing_ready_count=governing_ready_count,
        promotion_readiness=promotion_readiness,
        launchability_reason=launchability_reason,
        sample_rows=sample_rows,
        notes=tuple(manifest_notes),
    )


@ttl_cache(maxsize=4)
def build_governed_bridge_manifests() -> list[dict[str, Any]]:
    manifests: list[GovernedBridgeManifest] = []
    governed_rows_by_source = _governed_bridge_rows_by_source()
    final_rows = list(governed_rows_by_source.get("final_structured_candidates_v1", ()))
    if final_rows:
        manifests.append(
            _compile_governed_bridge_manifest(
                bridge_id="bridge:final_structured_candidates_v1",
                source_family="final_structured_candidates_v1",
                governed_rows=final_rows,
                notes=(
                    "Final structured rows are now compiled into a governed bridge manifest.",
                    "This bridge remains gated because the staged bundle is still report-only and mostly non-governing.",
                ),
            )
        )

    expanded_rows = list(governed_rows_by_source.get("expanded_ppi_procurement_bridge", ()))
    if expanded_rows:
        manifests.append(
            _compile_governed_bridge_manifest(
                bridge_id="bridge:expanded_ppi_procurement_bridge",
                source_family="expanded_ppi_procurement_bridge",
                governed_rows=expanded_rows,
                notes=(
                    "Procurement-expanded rows are compiled from the staged structured corpus into a governed bridge manifest.",
                    "This bridge stays gated until protein-protein governing coverage and promotion review improve.",
                ),
            )
        )
    return [manifest.to_dict() for manifest in manifests]


@ttl_cache(maxsize=2)
def build_governed_row_authority_v3() -> list[dict[str, Any]]:
    release_dataset = _benchmark_dataset_descriptor(
        dataset_ref="release_pp_alpha_benchmark_v1",
        label="Release PPI alpha benchmark",
        split_strategy="leakage_resistant_benchmark",
        source_manifest=RELEASE_ALPHA_LATEST,
        tags=("ppi", "release"),
        maturity="internal_alpha_candidate",
        catalog_status="release",
    )
    robust_dataset = _benchmark_dataset_descriptor(
        dataset_ref="robust_pp_benchmark_v1",
        label="Robust structure-backed benchmark",
        split_strategy="accession_grouped",
        source_manifest=ROBUST_LATEST,
        tags=("ppi", "robust"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    expanded_dataset = _benchmark_dataset_descriptor(
        dataset_ref="expanded_pp_benchmark_v1",
        label="Expanded PPI benchmark",
        split_strategy="graph_component_grouped",
        source_manifest=EXPANDED_LATEST,
        tags=("ppi", "expanded"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    rows: list[GovernedCandidateRowV3] = []
    for benchmark_row in _annotated_benchmark_rows(
        release_dataset,
        source_family="release_pp_alpha_benchmark_v1",
    ):
        rows.append(_benchmark_governed_row_v3(benchmark_row))
    for benchmark_row in _annotated_benchmark_rows(
        robust_dataset,
        source_family="robust_pp_benchmark_v1",
    ):
        rows.append(_benchmark_governed_row_v3(benchmark_row))
    for benchmark_row in _annotated_benchmark_rows(
        expanded_dataset,
        source_family="expanded_pp_benchmark_v1",
    ):
        rows.append(_benchmark_governed_row_v3(benchmark_row))
    final_structured_state = _load_structured_bundle_state(FINAL_STRUCTURED_LATEST)
    for row in list((final_structured_state.get("corpus") or {}).get("rows") or []):
        rows.append(
            _governed_row_v3_from_structured_payload(
                source_family="final_structured_candidates_v1",
                row=row,
            )
        )
    expanded_structured_state = _load_structured_bundle_state(EXPANDED_STRUCTURED_LATEST)
    for row in list((expanded_structured_state.get("corpus") or {}).get("rows") or []):
        rows.append(
            _governed_row_v3_from_structured_payload(
                source_family="expanded_ppi_procurement_bridge",
                row=row,
            )
        )
    return [row.to_dict() for row in rows]


def _free_state_row_candidates() -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: list[dict[str, Any]] = []
    counts = {
        "rows_scanned": 0,
        "paired_candidates": 0,
        "missing_free_state": 0,
        "missing_bound_state": 0,
    }
    for row in build_governed_row_authority_v3():
        counts["rows_scanned"] += 1
        provenance = [str(item) for item in row.get("source_provenance", [])]
        free_state_ref = next(
            (
                item
                for item in provenance
                if any(token in item.casefold() for token in ("apo", "free_state", "free-state"))
            ),
            "",
        )
        bound_state_ready = row.get("structure_backed_readiness") == "structure_backed"
        if free_state_ref and bound_state_ready:
            counts["paired_candidates"] += 1
            candidates.append(
                {
                    "canonical_row_id": row.get("canonical_row_id"),
                    "source_family": row.get("source_family"),
                    "accession_grouping_key": row.get("accession_grouping_key"),
                    "free_state_ref": free_state_ref,
                }
            )
        elif not free_state_ref:
            counts["missing_free_state"] += 1
        elif not bound_state_ready:
            counts["missing_bound_state"] += 1
    return candidates[:16], counts


@ttl_cache(maxsize=2)
def build_stage2_scientific_tracks() -> list[dict[str, Any]]:
    STAGE2_TRACK_DIR.mkdir(parents=True, exist_ok=True)
    governed_rows = build_governed_row_authority_v3()
    structure_backed_rows = [
        row for row in governed_rows if row.get("structure_backed_readiness") == "structure_backed"
    ]
    sample_rows = [
        {
            "canonical_row_id": row.get("canonical_row_id"),
            "source_family": row.get("source_family"),
            "accession_grouping_key": row.get("accession_grouping_key"),
        }
        for row in structure_backed_rows[:12]
    ]
    pyrosetta_track_root = _stage2_track_root("pyrosetta")
    pyrosetta_track_root.mkdir(parents=True, exist_ok=True)
    pyrosetta_contract = {
        "track_id": "ros:pyrosetta",
        "track_label": "PyRosetta prototype lane",
        "status": "review_pending",
        "runtime_available": False,
        "runtime_probe": {
            "python_module": "pyrosetta",
            "importable": False,
            "probe_time": _utc_now(),
        },
        "governed_input_selection": {
            "eligible_structure_backed_rows": len(structure_backed_rows),
            "sample_rows": sample_rows,
        },
        "materialization_contract": {
            "artifact_format": "pyrosetta_materialization_contract_v1",
            "cache_key_fields": [
                "canonical_row_id",
                "structure_source",
                "partner_awareness",
                "graph_scope",
            ],
            "expected_outputs": [
                "rosetta_score_bundle.json",
                "rosetta_interface_metrics.json",
                "rosetta_packaging_manifest.json",
            ],
        },
        "blockers": [
            "PyRosetta is not installed on this machine, so the native Stage 2 lane cannot execute yet.",
            "Rosetta-derived outputs remain blocked until Mill and Kepler sign off on the materialization contract and Bacon signs off on runtime provenance disclosure.",
        ],
        "required_reviewers": ["Kepler", "Mill", "Bacon", "Euler", "Ampere"],
        "required_matrix_tests": ["rosetta_materialization", "compare_runs", "export_truth"],
    }
    _save_json(pyrosetta_track_root / "materialization_contract.json", pyrosetta_contract)

    free_state_track_root = _stage2_track_root("free_state")
    free_state_track_root.mkdir(parents=True, exist_ok=True)
    free_state_candidates, free_state_counts = _free_state_row_candidates()
    free_state_contract = {
        "track_id": "preprocess:free_state_comparison",
        "track_label": "Free-state comparison prototype lane",
        "status": "review_pending",
        "pairing_audit": free_state_counts,
        "paired_candidate_samples": free_state_candidates,
        "materialization_contract": {
            "artifact_format": "free_state_pairing_contract_v1",
            "required_inputs": [
                "bound_state_structure",
                "free_state_structure",
                "accession_grouping_key",
            ],
            "expected_outputs": [
                "free_state_pairing_report.json",
                "free_state_delta_features.json",
            ],
        },
        "blockers": [
            "No governed row currently records a complete bound-state plus free-state structure pair for launchable PPI studies.",
            "Free-state comparison remains blocked until pairing coverage and state-truth semantics are review-cleared.",
        ],
        "required_reviewers": ["Kepler", "Mill", "McClintock", "Euler", "Ampere"],
        "required_matrix_tests": ["free_state_pairing", "compare_runs", "export_truth"],
    }
    _save_json(free_state_track_root / "pairing_contract.json", free_state_contract)

    return [
        {
            **pyrosetta_contract,
            "artifact_path": _artifact_rel(pyrosetta_track_root / "materialization_contract.json"),
        },
        {
            **free_state_contract,
            "artifact_path": _artifact_rel(free_state_track_root / "pairing_contract.json"),
        },
    ]


@ttl_cache(maxsize=4)
def build_candidate_database_summary() -> dict[str, Any]:
    """v1 -- governed-row only summary.

    Deprecated. Prefer :func:`build_candidate_database_summary_v3` /
    the ``candidate_database_summary_canonical`` field in the workspace
    payload. Retained because v2/v3 chain through v1 and a few legacy
    callers still read this shape.
    """
    manifests = build_governed_bridge_manifests()
    governed_rows = [
        *list(_governed_bridge_rows_by_source().get("final_structured_candidates_v1", ())),
        *list(_governed_bridge_rows_by_source().get("expanded_ppi_procurement_bridge", ())),
    ]
    total_governed_rows = sum(int(item.get("row_count") or 0) for item in manifests)
    governing_ready_rows = sum(int(item.get("governing_ready_count") or 0) for item in manifests)
    source_family_mix = {
        item.get("source_family", f"bridge-{index}"): int(item.get("row_count") or 0)
        for index, item in enumerate(manifests)
    }
    assay_family_mix: dict[str, int] = {}
    label_bin_mix: dict[str, int] = {}
    redundancy_counts: dict[str, int] = {}
    for row in governed_rows:
        assay_family_mix[row.measurement_type] = assay_family_mix.get(row.measurement_type, 0) + 1
        label_bin_mix[row.training_eligibility] = label_bin_mix.get(row.training_eligibility, 0) + 1
        if row.structural_redundancy_key:
            redundancy_counts[row.structural_redundancy_key] = (
                redundancy_counts.get(row.structural_redundancy_key, 0) + 1
            )
    redundancy_hotspots = [
        f"{key} ({count} rows)"
        for key, count in sorted(redundancy_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        if count >= 16
    ][:5]
    bias_diagnostics: list[str] = []
    if source_family_mix:
        dominant_source, dominant_count = sorted(
            source_family_mix.items(), key=lambda pair: (-pair[1], pair[0])
        )[0]
        dominant_share = dominant_count / max(total_governed_rows, 1)
        if dominant_share > 0.75:
            bias_diagnostics.append(
                f"{dominant_source} contributes {dominant_share:.0%} of the governed staged candidate rows."
            )
    if governing_ready_rows < 24:
        bias_diagnostics.append(
            "Most governed staged rows remain candidate-only or support-only; governing-ready coverage is still sparse."
        )
    summary = CandidateDatabaseSummary(
        summary_id="candidate-database-summary:governed-ppi-source-graph",
        total_governed_rows=total_governed_rows,
        governing_ready_rows=governing_ready_rows,
        source_family_mix=source_family_mix,
        assay_family_mix=assay_family_mix,
        label_bin_mix=label_bin_mix,
        redundancy_hotspots=tuple(redundancy_hotspots),
        bias_diagnostics=tuple(bias_diagnostics),
        notes=(
            "The candidate database summary covers row-governed staged corpora, not only the launchable benchmark pools.",
            "Balance-first broadening remains the policy while governed staged pools move toward promotion review.",
        ),
    )
    return summary.to_dict()


@ttl_cache(maxsize=2)
def build_governed_subset_manifests() -> list[dict[str, Any]]:
    """v1 -- single-subset view. Deprecated; new code should read the
    ``governed_subset_manifests_canonical`` payload field which maps to
    :func:`build_governed_subset_manifests_v2` and includes every
    materialized subset, not just the original PPI one."""
    subset_manifest = _materialize_governed_ppi_subset().get("subset_manifest") or {}
    return [subset_manifest] if subset_manifest else []


@ttl_cache(maxsize=2)
def build_governed_subset_manifests_v2() -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    v1_manifest = _materialize_governed_ppi_subset().get("subset_manifest") or {}
    v2_manifest = _materialize_governed_ppi_subset_v2().get("subset_manifest") or {}
    stage2_manifest = _materialize_governed_ppi_stage2_candidate_v1().get("subset_manifest") or {}
    external_beta_manifest = (
        _materialize_governed_ppi_external_beta_candidate_v1().get("subset_manifest") or {}
    )
    ligand_pilot_manifest = (
        _materialize_governed_pl_bridge_pilot_subset_v1().get("subset_manifest") or {}
    )
    if v1_manifest:
        manifests.append(v1_manifest)
    if v2_manifest:
        manifests.append(v2_manifest)
    if stage2_manifest:
        manifests.append(stage2_manifest)
    if external_beta_manifest:
        manifests.append(external_beta_manifest)
    if ligand_pilot_manifest:
        manifests.append(ligand_pilot_manifest)
    return manifests


@ttl_cache(maxsize=2)
def build_candidate_database_summary_v2() -> dict[str, Any]:
    base_summary = build_candidate_database_summary()
    subsets = build_governed_subset_manifests_v2()
    governance_state_mix = dict(base_summary.get("label_bin_mix") or {})
    promotion_ready_subset_count = 0
    for subset in subsets:
        governance_state = _clean_text(subset.get("promotion_readiness")) or "hold"
        if governance_state == "launchable_now":
            promotion_ready_subset_count += 1
    return CandidateDatabaseSummaryV2(
        summary_id="candidate-database-summary-v2:governed-ppi-promotion-graph",
        total_governed_rows=int(base_summary.get("total_governed_rows") or 0),
        governing_ready_rows=int(base_summary.get("governing_ready_rows") or 0),
        source_family_mix=dict(base_summary.get("source_family_mix") or {}),
        assay_family_mix=dict(base_summary.get("assay_family_mix") or {}),
        label_bin_mix=dict(base_summary.get("label_bin_mix") or {}),
        governance_state_mix=governance_state_mix,
        promotion_ready_subset_count=promotion_ready_subset_count,
        redundancy_hotspots=tuple(base_summary.get("redundancy_hotspots") or ()),
        bias_diagnostics=tuple(
            [
                *(base_summary.get("bias_diagnostics") or []),
                (
                    "A governed blended subset is now compiled as a promotion candidate with explicit whole-complex-only constraints on staged rows."
                    if subsets
                    else "No governed subset has been compiled yet."
                ),
            ]
        ),
        notes=tuple(
            [
                *(base_summary.get("notes") or []),
                "V2 summary adds promotion-ready subset state on top of the governed staged source graph.",
            ]
        ),
    ).to_dict()


@ttl_cache(maxsize=2)
def build_promotion_queue() -> list[dict[str, Any]]:
    """v1 -- governed-subset-first promotion queue.

    Deprecated. New code should read ``promotion_queue_canonical`` from
    the workspace payload, which maps to
    :func:`build_promotion_queue_v2` (same data, deterministically
    ordered)."""
    queue: list[dict[str, Any]] = []
    subset_dataset_refs: set[str] = set()
    for subset in build_governed_subset_manifests_v2():
        promoted_dataset_ref = _clean_text(subset.get("promoted_dataset_ref"))
        if promoted_dataset_ref:
            subset_dataset_refs.add(promoted_dataset_ref)
        queue.append(
            {
                "queue_id": subset.get("subset_id"),
                "kind": "governed_subset",
                "label": subset.get("label"),
                "promoted_dataset_ref": subset.get("promoted_dataset_ref"),
                "promotion_readiness": subset.get("promotion_readiness"),
                "review_signoff_state": subset.get("review_signoff_state"),
                "launchability_reason": subset.get("launchability_reason"),
                "blockers": list(subset.get("blockers") or []),
                "required_reviewers": list(
                    subset.get("required_reviewers")
                    or ["Kepler", "Euler", "Ampere", "Mill", "Bacon", "McClintock"]
                ),
                "required_matrix_tests": list(subset.get("required_matrix_tests") or []),
            }
        )
    for report in build_pool_promotion_reports():
        if report.get("status") not in {"beta_soon", "planned_inactive"}:
            continue
        promoted_dataset_ref = next(iter(report.get("promoted_dataset_refs") or []), None)
        if promoted_dataset_ref in subset_dataset_refs:
            continue
        queue.append(
            {
                "queue_id": report.get("pool_id"),
                "kind": "dataset_pool",
                "label": report.get("pool_id"),
                "promoted_dataset_ref": promoted_dataset_ref,
                "promotion_readiness": report.get("promotion_readiness"),
                "review_signoff_state": report.get("review_signoff_state"),
                "launchability_reason": report.get("launchability_reason"),
                "blockers": list(report.get("blockers") or []),
                "required_reviewers": ["Kepler", "Euler", "Mill", "McClintock"],
                "required_matrix_tests": [],
            }
        )
    return queue


@ttl_cache(maxsize=2)
def build_promotion_queue_v2() -> list[dict[str, Any]]:
    queue = build_promotion_queue()
    queue.sort(key=lambda item: (0 if item.get("kind") == "governed_subset" else 1, item.get("queue_id", "")))
    return queue


@ttl_cache(maxsize=2)
def build_candidate_database_summary_v3() -> dict[str, Any]:
    base_summary = build_candidate_database_summary_v2()
    subset_manifests = build_governed_subset_manifests_v2()
    governance_state_mix: dict[str, int] = {}
    promoted_subset_count = 0
    gated_subset_count = 0
    for subset in subset_manifests:
        state = _clean_text(subset.get("review_signoff_state")) or "pending"
        governance_state_mix[state] = governance_state_mix.get(state, 0) + 1
        if _clean_text(subset.get("status")) == "launchable_now":
            promoted_subset_count += 1
        else:
            gated_subset_count += 1
    bias_hotspots = list(base_summary.get("bias_diagnostics") or [])
    for hotspot in base_summary.get("redundancy_hotspots") or []:
        if hotspot not in bias_hotspots:
            bias_hotspots.append(hotspot)
    readiness_blockers: list[str] = []
    if int(base_summary.get("governing_ready_rows") or 0) < 24:
        readiness_blockers.append(
            "Canonical governed-row launchability is still too sparse for a stable promoted-subset program."
        )
    if promoted_subset_count < 2:
        readiness_blockers.append(
            "A second governed subset is still required before the governed promotion system is fully launch-ready."
        )
    promotion_backlog = sum(
        1 for subset in subset_manifests if _clean_text(subset.get("status")) != "launchable_now"
    )
    return CandidateDatabaseSummaryV3(
        summary_id="candidate-database-summary-v3:governed-ppi-promotion-engine",
        total_governed_rows=int(base_summary.get("total_governed_rows") or 0),
        governing_ready_rows=int(base_summary.get("governing_ready_rows") or 0),
        source_family_mix=dict(base_summary.get("source_family_mix") or {}),
        assay_family_mix=dict(base_summary.get("assay_family_mix") or {}),
        label_bin_mix=dict(base_summary.get("label_bin_mix") or {}),
        governance_state_mix=governance_state_mix,
        promoted_subset_count=promoted_subset_count,
        gated_subset_count=gated_subset_count,
        redundancy_hotspots=tuple(base_summary.get("redundancy_hotspots") or ()),
        readiness_blockers=tuple(readiness_blockers),
        bias_hotspots=tuple(bias_hotspots),
        notes=tuple(
            [
                *(base_summary.get("notes") or []),
                "V3 summary adds governed subset promotion counts, governance-state aggregation, and readiness blockers.",
                f"Promotion backlog currently includes {promotion_backlog} governed subset candidate(s).",
            ]
        ),
    ).to_dict()


def _pool_priority(pool: dict[str, Any]) -> tuple[int, str]:
    return (
        {
            "pool:release_pp_alpha_benchmark_v1": 0,
            "pool:robust_pp_benchmark_v1": 1,
            "pool:expanded_pp_benchmark_v1": 2,
            "pool:governed_ppi_blended_subset_v1": 3,
            "pool:governed_ppi_blended_subset_v2": 4,
            "pool:governed_ppi_stage2_candidate_v1": 5,
            "pool:governed_ppi_external_beta_candidate_v1": 6,
            "pool:governed_pl_bridge_pilot_subset_v1": 7,
        }.get(pool.get("pool_id"), 10),
        pool.get("pool_id", ""),
    )


def _promotion_status_for_pool_id(pool_id: str, fallback: str) -> str:
    return {
        "pool:release_pp_alpha_benchmark_v1": "release",
        "pool:robust_pp_benchmark_v1": "beta",
        "pool:expanded_pp_benchmark_v1": "beta",
        "pool:governed_ppi_blended_subset_v1": "beta_soon",
        "pool:governed_ppi_blended_subset_v2": "beta",
        "pool:governed_ppi_stage2_candidate_v1": "beta_soon",
        "pool:governed_ppi_external_beta_candidate_v1": "beta",
        "pool:governed_pl_bridge_pilot_subset_v1": "beta",
        "pool:final_structured_candidates_v1": "beta_soon",
        "pool:expanded_ppi_procurement_bridge": "beta_soon",
    }.get(pool_id, fallback)


def list_dataset_pools() -> list[dict[str, Any]]:
    pools: list[DatasetPoolManifest] = []
    bridge_manifest_lookup = {
        item["bridge_id"]: item for item in build_governed_bridge_manifests()
    }
    robust_artifact = _load_latest_candidate_artifact(ROBUST_LATEST)
    expanded_artifact = _load_latest_candidate_artifact(EXPANDED_LATEST)
    for item in list_known_datasets():
        dataset = DatasetDescriptor(
            dataset_ref=item["dataset_ref"],
            label=item["label"],
            task_type=item["task_type"],
            split_strategy=item["split_strategy"],
            train_csv=Path(item["train_csv"]),
            val_csv=Path(item["val_csv"]) if item.get("val_csv") else None,
            test_csv=Path(item["test_csv"]),
            source_manifest=Path(item["source_manifest"]),
            row_count=int(item["row_count"]),
            tags=tuple(item["tags"]),
            maturity=item["maturity"],
            catalog_status=item.get("catalog_status", "planned_inactive"),
        )
        if dataset.dataset_ref.startswith("study_build:"):
            continue
        rows = _dataset_source_rows(dataset)
        labeled_rows = sum(1 for row in rows if row.exp_dg == row.exp_dg)
        coverage = (
            0.0
            if not rows
            else sum(1 for row in rows if row.structure_file.exists()) / len(rows)
        )
        pools.append(
            DatasetPoolManifest(
                pool_id=f"pool:{dataset.dataset_ref}",
                label=dataset.label,
                source_family=dataset.dataset_ref,
                dataset_refs=(dataset.dataset_ref,),
                row_count=len(rows),
                structure_coverage=round(coverage, 4),
                label_coverage=round(labeled_rows / len(rows), 4) if rows else 0.0,
                split_provenance=dataset.split_strategy,
                maturity=dataset.maturity,
                truth_boundary=_dataset_truth_boundary(dataset),
                balancing_metadata=_dataset_balance_metadata(rows),
                status=_promotion_status_for_pool_id(
                    f"pool:{dataset.dataset_ref}",
                    dataset.catalog_status,
                ),
                notes=tuple(dataset.tags),
            )
        )
        current = pools[-1]
        if dataset.dataset_ref == "robust_pp_benchmark_v1":
            _augment_pool_with_candidate_artifact(current, artifact=robust_artifact)
        elif dataset.dataset_ref == "expanded_pp_benchmark_v1":
            _augment_pool_with_candidate_artifact(current, artifact=expanded_artifact)
        elif dataset.dataset_ref == GOVERNED_PPI_SUBSET_DATASET_REF:
            governed_subset = _materialize_governed_ppi_subset().get("dataset_manifest") or {}
            current.balancing_metadata.update(governed_subset.get("balancing") or {})
            current.truth_boundary.update(governed_subset.get("truth_boundary") or {})
            object.__setattr__(
                current,
                "notes",
                tuple(
                    dict.fromkeys(
                        [
                            *current.notes,
                            "governed_subset",
                            "whole_complex_only_for_staged_rows",
                        ]
                    )
                ),
            )
        elif dataset.dataset_ref == GOVERNED_PPI_SUBSET_V2_DATASET_REF:
            governed_subset_v2 = _materialize_governed_ppi_subset_v2().get("dataset_manifest") or {}
            current.balancing_metadata.update(governed_subset_v2.get("balancing") or {})
            current.truth_boundary.update(governed_subset_v2.get("truth_boundary") or {})
            object.__setattr__(
                current,
                "notes",
                tuple(
                    dict.fromkeys(
                        [
                            *current.notes,
                            "governed_subset_v2",
                            "launchable_now",
                            "whole_complex_only_for_staged_rows",
                        ]
                    )
                ),
            )
        elif dataset.dataset_ref == GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF:
            external_beta_candidate = (
                _materialize_governed_ppi_external_beta_candidate_v1().get("dataset_manifest")
                or {}
            )
            current.balancing_metadata.update(external_beta_candidate.get("balancing") or {})
            current.truth_boundary.update(external_beta_candidate.get("truth_boundary") or {})
            object.__setattr__(
                current,
                "notes",
                tuple(
                    dict.fromkeys(
                        [
                            *current.notes,
                            "governed_external_beta_candidate",
                            "launchable_now",
                            "whole_complex_only_for_staged_rows",
                        ]
                    )
                ),
            )
        elif dataset.dataset_ref == GOVERNED_PL_BRIDGE_PILOT_DATASET_REF:
            ligand_pilot = _materialize_governed_pl_bridge_pilot_subset_v1().get(
                "dataset_manifest"
            ) or {}
            current.balancing_metadata.update(ligand_pilot.get("balancing") or {})
            current.truth_boundary.update(ligand_pilot.get("truth_boundary") or {})
            object.__setattr__(
                current,
                "notes",
                tuple(
                    dict.fromkeys(
                        [
                            *current.notes,
                            "governed_ligand_bridge_pilot",
                            "launchable_now",
                            "structure_backed_only",
                            "bridge_provenance_support_only",
                        ]
                    )
                ),
            )

    final_structured_state = _load_structured_bundle_state(FINAL_STRUCTURED_LATEST)
    final_structured_pool = _build_structured_pool_manifest(
        pool_id="pool:final_structured_candidates_v1",
        label="Final structured candidate bundle",
        source_family="final_structured_candidates_v1",
        dataset_ref="final_structured_candidates_v1",
        split_provenance="bundle_only_not_launchable",
        maturity=(
            _clean_text(final_structured_state.get("bundle_manifest", {}).get("package_readiness_state"))
            or "bundle_ready"
        ),
        status=_promotion_status_for_pool_id("pool:final_structured_candidates_v1", "beta_soon"),
        state_payload=final_structured_state,
        notes=("bundle-only", "awaiting_study_builder_integration"),
    )
    if final_structured_pool is not None:
        bridge_manifest = bridge_manifest_lookup.get("bridge:final_structured_candidates_v1", {})
        if bridge_manifest:
            final_structured_pool.balancing_metadata.update(
                {
                    "governed_bridge_row_count": bridge_manifest.get("row_count"),
                    "governed_bridge_governing_ready_count": bridge_manifest.get("governing_ready_count"),
                    "governed_bridge_readiness_counts": bridge_manifest.get("readiness_counts", {}),
                }
            )
            final_structured_pool.truth_boundary.update(
                {
                    "row_level_provenance_state": bridge_manifest.get("provenance_completeness"),
                    "measurement_normalization_state": bridge_manifest.get("normalization_completeness"),
                    "admissibility_flag_state": bridge_manifest.get("admissibility_completeness"),
                    "governed_bridge_promotion_readiness": bridge_manifest.get("promotion_readiness"),
                    "governed_bridge_launchability_reason": bridge_manifest.get("launchability_reason"),
                }
            )
        pools.append(final_structured_pool)

    expanded_structured_state = _load_structured_bundle_state(EXPANDED_STRUCTURED_LATEST)
    expanded_structured_pool = _build_structured_pool_manifest(
        pool_id="pool:expanded_ppi_procurement_bridge",
        label="Expanded procurement PPI staging corpus",
        source_family="expanded_ppi_procurement",
        dataset_ref="expanded_ppi_procurement_bridge",
        split_provenance="procurement_bridge_pending_canonicalization",
        maturity="expansion_staging_candidate",
        status=_promotion_status_for_pool_id("pool:expanded_ppi_procurement_bridge", "beta_soon"),
        state_payload=expanded_structured_state,
        notes=("staged_expansion_corpus", "candidate_only_non_governing"),
    )
    if expanded_structured_pool is not None:
        procurement = _load_json(EXPANSION_PROCUREMENT_STATE, {})
        balancing_metadata, truth_boundary = _procurement_bridge_metadata(
            procurement,
            expanded_structured_state,
        )
        expanded_structured_pool.balancing_metadata.update(balancing_metadata)
        expanded_structured_pool.truth_boundary.update(truth_boundary)
        bridge_manifest = bridge_manifest_lookup.get("bridge:expanded_ppi_procurement_bridge", {})
        if bridge_manifest:
            expanded_structured_pool.balancing_metadata.update(
                {
                    "governed_bridge_row_count": bridge_manifest.get("row_count"),
                    "governed_bridge_governing_ready_count": bridge_manifest.get("governing_ready_count"),
                    "governed_bridge_readiness_counts": bridge_manifest.get("readiness_counts", {}),
                }
            )
            expanded_structured_pool.truth_boundary.update(
                {
                    "row_level_provenance_state": bridge_manifest.get("provenance_completeness"),
                    "measurement_normalization_state": bridge_manifest.get("normalization_completeness"),
                    "admissibility_flag_state": bridge_manifest.get("admissibility_completeness"),
                    "governed_bridge_promotion_readiness": bridge_manifest.get("promotion_readiness"),
                    "governed_bridge_launchability_reason": bridge_manifest.get("launchability_reason"),
                }
            )
        pools.append(expanded_structured_pool)
    return [pool.to_dict() for pool in pools]


def _promoted_pool_ids_from_reports() -> set[str]:
    return {
        report.get("pool_id", "")
        for report in build_pool_promotion_reports()
        if report.get("status") in {"release", "beta"}
        and not report.get("blockers")
        and report.get("review_signoff_state")
        in {
            "approved",
            "wave_4_ready_for_freeze",
            "controlled_external_beta_ready",
            "wave_6_ready_for_beta",
        }
        and report.get("promotion_readiness") in {"promoted", "launchable_now"}
    }


def build_candidate_pool_summary() -> dict[str, Any]:
    active_pools = sorted(_launchable_dataset_pools(), key=_pool_priority)
    dataset_lookup = _dataset_lookup()
    seen_rows: set[tuple[Any, ...]] = set()
    source_mix: dict[str, int] = {}
    raw_row_total = 0
    canonical_rows: list[BenchmarkRow] = []
    for pool in active_pools:
        unique_contribution = 0
        for dataset_ref in pool.get("dataset_refs", []):
            dataset = dataset_lookup.get(dataset_ref)
            if dataset is None:
                continue
            rows = _dataset_source_rows(dataset)
            raw_row_total += len(rows)
            for row in rows:
                row_key = _candidate_row_identity(row)
                if row_key in seen_rows:
                    continue
                seen_rows.add(row_key)
                canonical_rows.append(row)
                unique_contribution += 1
        source_mix[pool["label"]] = unique_contribution
    total_rows = len(seen_rows)
    bias_hotspots: list[str] = []
    for label, count in sorted(source_mix.items(), key=lambda item: (-item[1], item[0])):
        share = 0.0 if total_rows == 0 else count / total_rows
        if share > 0.5:
            bias_hotspots.append(f"{label} contributes {share:.0%} of the active candidate rows.")
    assay_mix: dict[str, int] = {}
    label_bin_mix: dict[str, int] = {}
    robust_overlap_count = 0
    for pool in active_pools:
        balancing = pool.get("balancing_metadata", {})
        robust_overlap_count += int(
            (balancing.get("robust_overlap_summary") or {}).get("uniref50_cluster_overlap_count")
            or 0
        )
    for row in canonical_rows:
        assay_key = _measurement_type(row)
        assay_mix[assay_key] = assay_mix.get(assay_key, 0) + 1
    assay_total = sum(assay_mix.values()) or 0
    if assay_total:
        dominant_assay, dominant_count = sorted(
            assay_mix.items(),
            key=lambda item: (-item[1], item[0]),
        )[0]
        dominant_share = dominant_count / assay_total
        if dominant_share > 0.8:
            bias_hotspots.append(
                f"{dominant_assay} contributes {dominant_share:.0%} of the promoted assay mix."
            )
    if len(active_pools) < 2:
        bias_hotspots.append(
            "Only one active pool is currently promoted; broadening remains limited."
        )
    for row in canonical_rows:
        label_bin = _label_bin_name(row.exp_dg)
        label_bin_mix[label_bin] = label_bin_mix.get(label_bin, 0) + 1
    leakage_risk_summary = (
        "Grouped split governance remains active across promoted pools, with explicit "
        "partner-overlap and residual-cluster diagnostics carried into the broadened beta lane."
    )
    summary = CandidatePoolSummary(
        summary_id="candidate-pool-summary:beta-broadened",
        promoted_pool_ids=tuple(pool["pool_id"] for pool in active_pools),
        total_row_count=total_rows,
        source_mix=source_mix,
        assay_mix=assay_mix,
        label_bin_mix=label_bin_mix,
        bias_hotspots=tuple(bias_hotspots),
        recommended_inclusion_policy=(
            "balance_first_merge_across_release_robust_expanded"
            if len(active_pools) >= 3
            else "release_plus_beta_candidates"
        ),
        leakage_risk="managed_by_grouped_split",
        leakage_risk_summary=leakage_risk_summary,
        notes=(
            "Source mix reflects canonicalized unique row contributions in pool-priority order.",
            (
                "Promoted assay mix: "
                + ", ".join(f"{key}={value}" for key, value in sorted(assay_mix.items()))
                if assay_mix
                else "Promoted assay mix is not yet recorded."
            ),
            "Canonical measurement mix: "
            f"{_bucket_breakdown(canonical_rows, _measurement_type)}.",
            "Canonical label-bin mix: "
            f"{_bucket_breakdown(canonical_rows, lambda row: _label_bin_name(row.exp_dg))}.",
            "Raw active-pool rows: "
            f"{raw_row_total}; canonicalized unique candidate rows: {total_rows}.",
            (
                f"Residual robust-pool UniRef50 overlap flags: {robust_overlap_count}."
                if robust_overlap_count
                else "Promoted pools currently report no residual UniRef50 overlap flags."
            ),
            "Balance is prioritized over raw breadth while the broadened beta lane is activated.",
        ),
    )
    return summary.to_dict()


def _subset_decision_lookup() -> dict[str, dict[str, Any]]:
    review_wave_by_dataset = {
        GOVERNED_PPI_SUBSET_DATASET_REF: "wave-1",
        GOVERNED_PPI_SUBSET_V2_DATASET_REF: "wave-4",
        GOVERNED_PPI_STAGE2_CANDIDATE_DATASET_REF: "wave-1",
        GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF: "wave-5",
        GOVERNED_PL_BRIDGE_PILOT_DATASET_REF: "wave-6",
    }
    decisions: dict[str, dict[str, Any]] = {}
    for item in build_governed_subset_manifests_v2():
        promoted_dataset_ref = _clean_text(item.get("promoted_dataset_ref"))
        if not promoted_dataset_ref:
            continue
        decisions[f"pool:{promoted_dataset_ref}"] = {
            "status": _clean_text(item.get("status")) or "review_pending",
            "promotion_readiness": _clean_text(item.get("promotion_readiness")) or "hold",
            "review_signoff_state": _clean_text(item.get("review_signoff_state")) or "pending",
            "launchability_reason": _clean_text(item.get("launchability_reason")),
            "blockers": tuple(item.get("blockers") or ()),
            "required_reviewers": tuple(item.get("required_reviewers") or ()),
            "required_matrix_tests": tuple(item.get("required_matrix_tests") or ()),
            "notes": tuple(item.get("notes") or ()),
            "last_review_wave": review_wave_by_dataset.get(promoted_dataset_ref, "wave-0"),
        }
    return decisions


def build_pool_promotion_reports() -> list[dict[str, Any]]:
    reports: list[PoolPromotionReportV2] = []
    subset_lookup = _subset_decision_lookup()
    # Each ``bridge_id`` is unique, so we look up a single matching
    # manifest per pool rather than dict-comprehending with a constant
    # key (which silently keeps only the last match).
    bridge_manifests = build_governed_bridge_manifests()
    bridge_manifest_lookup: dict[str, Any] = {}
    final_structured = next(
        (item for item in bridge_manifests
         if item.get("bridge_id") == "bridge:final_structured_candidates_v1"),
        None,
    )
    if final_structured is not None:
        bridge_manifest_lookup["pool:final_structured_candidates_v1"] = final_structured
    expanded_ppi = next(
        (item for item in bridge_manifests
         if item.get("bridge_id") == "bridge:expanded_ppi_procurement_bridge"),
        None,
    )
    if expanded_ppi is not None:
        bridge_manifest_lookup["pool:expanded_ppi_procurement_bridge"] = expanded_ppi
    for pool in list_dataset_pools():
        pool_id = pool.get("pool_id", "")
        status = _promotion_status_for_pool_id(pool_id, pool.get("status", "planned_inactive"))
        promotion_bar = {
            "release": "release_matrix_plus_review",
            "beta": "beta_matrix_plus_review",
            "beta_soon": "governed_bridge_plus_balance_review",
            "planned_inactive": "native_implementation_plus_review",
            "blocked": "native_implementation_plus_review",
        }.get(status, "review_required")
        last_review_wave = {
            "pool:release_pp_alpha_benchmark_v1": "wave-3",
            "pool:robust_pp_benchmark_v1": "wave-3",
            "pool:expanded_pp_benchmark_v1": "wave-3",
            "pool:governed_ppi_blended_subset_v1": "wave-1",
            "pool:governed_ppi_blended_subset_v2": "wave-4",
            "pool:governed_ppi_stage2_candidate_v1": "wave-1",
            "pool:governed_ppi_external_beta_candidate_v1": "wave-5",
            "pool:governed_pl_bridge_pilot_subset_v1": "wave-6",
            "pool:final_structured_candidates_v1": "wave-1",
            "pool:expanded_ppi_procurement_bridge": "wave-1",
        }.get(pool_id, "wave-0")
        review_signoff_state = {
            "pool:release_pp_alpha_benchmark_v1": "approved",
            "pool:robust_pp_benchmark_v1": "approved",
            "pool:expanded_pp_benchmark_v1": "approved",
            "pool:governed_ppi_blended_subset_v1": "wave_1_pending_reviews",
            "pool:governed_ppi_blended_subset_v2": "wave_4_ready_for_freeze",
            "pool:governed_ppi_stage2_candidate_v1": "wave_1_pending_reviews",
            "pool:governed_ppi_external_beta_candidate_v1": "external_beta_signoff_pending",
            "pool:governed_pl_bridge_pilot_subset_v1": "wave_6_ready_for_beta",
            "pool:final_structured_candidates_v1": "pending",
            "pool:expanded_ppi_procurement_bridge": "pending",
        }.get(pool_id, "pending")
        blockers: list[str] = []
        remediation: list[str] = []
        notes = list(pool.get("notes", ()))
        balancing = pool.get("balancing_metadata", {})
        subset_manifest = subset_lookup.get(pool_id, {})
        subset_promotion_readiness = _clean_text(subset_manifest.get("promotion_readiness"))
        if status == "beta_soon" and subset_promotion_readiness != "promotion_ready_candidate":
            blockers.append("Pool is visible but not yet promoted into the default beta lane.")
            remediation.append(
                "Complete canonicalization, balancing review, "
                "and launch-surface integration."
            )
        truth_boundary = pool.get("truth_boundary", {})
        quality_verdict = _clean_text(balancing.get("quality_verdict")) or _clean_text(
            truth_boundary.get("quality_verdict")
        )
        overlap_summary = balancing.get("robust_overlap_summary") or {}
        assay_mix = balancing.get("selected_measurement_type_counts") or {}
        if truth_boundary.get("report_only_bundle") or truth_boundary.get("report_only"):
            blockers.append("Bundle is report-only and not directly launchable.")
            remediation.append(
                "Add a study-builder bridge that materializes "
                "training rows from the bundle."
            )
        if truth_boundary.get("non_governing"):
            blockers.append("Corpus remains non-governing and cannot yet authorize training.")
            remediation.append(
                "Promote a governed training view with explicit row-level admissibility."
            )
        if truth_boundary.get("whole_complex_only_for_staged_rows"):
            notes.append(
                "Staged bridge rows in this promoted subset stay whole-complex only until native partner-role resolution lands."
            )
            remediation.append(
                "Use whole-complex graph recipes with symmetric partner awareness for staged-row-heavy runs."
            )
        if truth_boundary.get("row_level_provenance_state") == "dataset_level_only_pending_bridge_compiler":
            blockers.append("Row-level provenance and admissibility metadata are not yet compiled.")
            remediation.append(
                "Complete the governed procurement bridge compiler before promoting this pool."
            )
        if truth_boundary.get("measurement_normalization_state") == "pending_bridge_compiler":
            blockers.append("Measurement-type normalization remains pending for this bridge pool.")
            remediation.append(
                "Normalize assay/measurement semantics before exposing this pool as launchable."
            )
        strict_governing = int(balancing.get("strict_governing_training_view_count") or 0)
        if strict_governing and strict_governing < 24:
            message = f"Only {strict_governing} governing-ready examples are currently available."
            if status in {"release", "beta"}:
                notes.append(message)
                remediation.append(
                    "Keep this pool beta-only until governing-ready coverage expands further."
                )
            else:
                blockers.append(message)
                remediation.append(
                    "Expand governing-ready coverage before promoting this pool into the active beta lane."
                )
        ppi_count = int(balancing.get("complex_type_counts", {}).get("protein_protein") or 0)
        row_count = int(pool.get("row_count") or 0)
        if row_count and ppi_count and ppi_count < max(24, int(row_count * 0.1)):
            message = "Protein-protein rows are a minority inside this broader staged corpus."
            if status in {"release", "beta"}:
                notes.append(message)
                remediation.append(
                    "Keep protein-protein specificity visible in diagnostics while this pool remains beta."
                )
            else:
                blockers.append(message)
                remediation.append(
                    "Extract a protein-protein-specific governed subset before exposing it as an active study source."
                )
        if quality_verdict == "remote_cluster_overlap_review_needed":
            message = (
                "Residual remote cluster overlaps still need review before this pool can replace the frozen benchmark."
            )
            if status in {"release", "beta"}:
                notes.append(message)
                remediation.append(
                    "Keep grouped split governance active and retain explicit overlap review during beta use."
                )
            else:
                blockers.append(message)
                remediation.append(
                    "Keep grouped split governance active and retain explicit overlap review during beta promotion."
                )
        if int(overlap_summary.get("shared_partner_overlap_count") or 0) > 0:
            remediation.append(
                "Shared-partner overlap remains non-zero; keep partner-overlap diagnostics visible in study review."
            )
        if assay_mix:
            total_measurements = sum(int(value or 0) for value in assay_mix.values()) or 0
            dominant_key, dominant_count = sorted(
                ((key, int(value or 0)) for key, value in assay_mix.items()),
                key=lambda item: (-item[1], item[0]),
            )[0]
            if total_measurements and (dominant_count / total_measurements) > 0.8:
                message = (
                    f"{dominant_key} dominates the promoted assay mix ({dominant_count}/{total_measurements})."
                )
                if status in {"release", "beta"}:
                    notes.append(message)
                    remediation.append(
                        "Keep this pool labeled as a breadth-expansion beta source, not a universal balanced benchmark."
                    )
                else:
                    blockers.append(message)
                    remediation.append(
                        "Promote this pool as a breadth-expansion beta source, not a fully balanced universal benchmark."
                    )
        bridge_manifest = bridge_manifest_lookup.get(pool_id, {})
        if subset_manifest:
            subset_blockers = tuple(subset_manifest.get("blockers") or ())
            blockers.extend(item for item in subset_blockers if item not in blockers)
            review_signoff_state = _clean_text(
                subset_manifest.get("review_signoff_state")
            ) or review_signoff_state
            notes.extend(item for item in subset_manifest.get("notes", ()) if item not in notes)
            last_review_wave = _clean_text(subset_manifest.get("last_review_wave")) or last_review_wave
        promotion_readiness = (
            _clean_text(subset_manifest.get("promotion_readiness"))
            or
            _clean_text(bridge_manifest.get("promotion_readiness"))
            or _clean_text(pool.get("truth_boundary", {}).get("governed_bridge_promotion_readiness"))
            or ("promoted" if status in {"release", "beta"} and not blockers else "hold")
        )
        if blockers:
            launchability_reason = (
                "Pool remains gated in the beta lane: " + blockers[0]
            )
        else:
            launchability_reason = (
                _clean_text(subset_manifest.get("launchability_reason"))
                or
                _clean_text(bridge_manifest.get("launchability_reason"))
                or (
                    "Pool is promoted into the launchable beta lane."
                    if status in {"release", "beta"}
                    else "Pool remains visible but gated until promotion review is complete."
                )
            )
        reports.append(
            PoolPromotionReportV2(
                pool_id=pool_id,
                status=status,
                promotion_bar=promotion_bar,
                last_review_wave=last_review_wave,
                review_signoff_state=review_signoff_state,
                promotion_readiness=promotion_readiness,
                launchability_reason=launchability_reason,
                blockers=tuple(blockers),
                remediation=tuple(remediation),
                promoted_dataset_refs=tuple(pool.get("dataset_refs", ())),
                required_reviewers=tuple(
                    subset_manifest.get("required_reviewers")
                    or ("Kepler", "Euler", "Ampere", "Mill", "Bacon", "McClintock")
                    if pool_id.startswith("pool:governed_ppi_")
                    or pool_id == GOVERNED_PL_BRIDGE_PILOT_POOL_ID
                    else ()
                ),
                required_matrix_tests=tuple(
                    subset_manifest.get("required_matrix_tests") or ()
                ),
                notes=tuple(dict.fromkeys(notes)),
            )
        )
    return [report.to_dict() for report in reports]


@ttl_cache(maxsize=2)
def build_feature_gate_views() -> list[dict[str, Any]]:
    ledger = {item["feature_id"]: item for item in build_activation_ledger()}
    readiness = {item["feature_id"]: item for item in build_activation_readiness_reports()}
    stage2_tracks = {item["track_id"]: item for item in build_stage2_scientific_tracks()}
    views: list[dict[str, Any]] = []
    for feature_id, gate in ledger.items():
        report = readiness.get(feature_id, {})
        stage2_track = stage2_tracks.get(feature_id, {})
        current_state = _clean_text(gate.get("current_state")) or "inactive"
        audience_state = (
            "launchable_now"
            if current_state in {"release", "beta"}
            else "review_pending"
            if current_state == "beta_soon"
            else "inactive"
        )
        views.append(
            {
                "feature_id": feature_id,
                "category": gate.get("category"),
                "status": current_state,
                "audience_state": audience_state,
                "activation_bar": gate.get("activation_bar"),
                "resolved_backend_fidelity": gate.get("resolved_backend_fidelity"),
                "launchability_reason": (
                    "; ".join(gate.get("blockers") or [])
                    if gate.get("blockers")
                    else "Feature is active in the current beta lane."
                ),
                "promotion_readiness": report.get("promotion_decision", "hold"),
                "implementation_completeness": report.get("implementation_completeness"),
                "remaining_risks": list(report.get("remaining_risks") or []),
                "required_reviewers": list(gate.get("reviewers_required") or []),
                "required_matrix_tests": list(gate.get("tests_required") or []),
                "notes": list(report.get("notes") or []),
                "blockers": list(gate.get("blockers") or []),
                "prototype_artifact": stage2_track.get("artifact_path"),
                "prototype_status": stage2_track.get("status"),
                "prototype_summary": stage2_track.get("track_label"),
            }
        )
    return sorted(views, key=lambda item: (item["category"] or "", item["feature_id"]))


def build_activation_ledger() -> list[dict[str, Any]]:
    gates: list[BetaFeatureGate] = [
        BetaFeatureGate(
            feature_id="split:graph_component_grouped",
            category="split_strategies",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_split_compiler",
            tests_required=("preview_training_set_request", "build_training_set"),
            reviewers_required=("architecture", "qa", "scientific-runtime"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-1",
        ),
        BetaFeatureGate(
            feature_id="split:accession_grouped",
            category="split_strategies",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_split_compiler",
            tests_required=("preview_training_set_request", "build_training_set"),
            reviewers_required=("architecture", "qa", "scientific-runtime"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-1",
        ),
        BetaFeatureGate(
            feature_id="graph:shell_graph",
            category="graph_kinds",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_structure_materialization",
            tests_required=("graph_materialization", "run_matrix"),
            reviewers_required=("architecture", "qa", "biochem-structural"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="graph:whole_complex_graph",
            category="graph_kinds",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_structure_materialization",
            tests_required=("graph_materialization", "run_matrix"),
            reviewers_required=("architecture", "qa", "biochem-structural"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="graph:atom_graph",
            category="graph_kinds",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_atom_materialization",
            tests_required=("atom_graph_materialization", "run_matrix", "analysis_artifacts"),
            reviewers_required=("architecture", "qa", "biochem-structural", "ml-systems"),
            last_matrix_pass="activation-wave-atom-beta",
            last_review_wave="wave-4",
        ),
        BetaFeatureGate(
            feature_id="node:atom",
            category="node_granularities",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_atom_materialization",
            tests_required=("atom_graph_materialization", "run_matrix", "analysis_artifacts"),
            reviewers_required=("architecture", "qa", "biochem-structural", "ml-systems"),
            last_matrix_pass="activation-wave-atom-beta",
            last_review_wave="wave-4",
        ),
        BetaFeatureGate(
            feature_id="encoding:one_hot",
            category="node_feature_policies",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_feature_encoder",
            tests_required=("graph_materialization", "run_matrix"),
            reviewers_required=("qa", "ml-systems"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="encoding:ordinal_ranked",
            category="node_feature_policies",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_feature_encoder",
            tests_required=("graph_materialization", "run_matrix"),
            reviewers_required=("qa", "ml-systems"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="partner:role_conditioned",
            category="partner_awareness_modes",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_partner_role_scalars",
            tests_required=("graph_materialization", "run_matrix"),
            reviewers_required=("qa", "biochem-structural", "ml-systems"),
            last_matrix_pass="activation-wave-b",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="distributed:water_network_descriptors",
            category="distributed_feature_sets",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_water_bridge_proxies",
            tests_required=("feature_materialization", "run_matrix"),
            reviewers_required=("qa", "scientific-runtime", "biochem-structural"),
            last_matrix_pass="activation-wave-b",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="distributed:sequence_embeddings",
            category="distributed_feature_sets",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_sequence_embedding_materialization",
            tests_required=("embedding_materialization", "sequence_leakage_audit", "run_matrix"),
            reviewers_required=("architecture", "qa", "ml-systems", "scientific-runtime"),
            last_matrix_pass="activation-wave-sequence-beta",
            last_review_wave="wave-4",
        ),
        BetaFeatureGate(
            feature_id="preprocess:sequence_embeddings",
            category="preprocessing_modules",
            current_state="beta",
            activation_bar="native_only",
            resolved_backend_fidelity="native_sequence_embedding_materialization",
            tests_required=("embedding_materialization", "sequence_leakage_audit", "run_matrix"),
            reviewers_required=("architecture", "qa", "ml-systems"),
            last_matrix_pass="activation-wave-sequence-beta",
            last_review_wave="wave-4",
        ),
        BetaFeatureGate(
            feature_id="model:gin",
            category="model_families",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="adapter:graphsage-lite-family",
            tests_required=("run_matrix", "analysis_artifacts"),
            reviewers_required=("qa", "ml-systems", "architecture"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="model:gcn",
            category="model_families",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="adapter:graphsage-lite-family",
            tests_required=("run_matrix", "analysis_artifacts"),
            reviewers_required=("qa", "ml-systems", "architecture"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="model:late_fusion_ensemble",
            category="model_families",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="adapter:local_tabular_ensemble",
            tests_required=("run_matrix", "compare_runs"),
            reviewers_required=("qa", "ml-systems", "ux"),
            last_matrix_pass="activation-wave-a",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="model:gat",
            category="model_families",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="adapter:graphsage-lite-family",
            tests_required=("run_matrix", "analysis_artifacts"),
            reviewers_required=("qa", "ml-systems", "architecture"),
            last_matrix_pass="activation-wave-c",
            last_review_wave="wave-3",
        ),
        BetaFeatureGate(
            feature_id="optimizer:lion",
            category="optimizer_policies",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="adapter:lion-via-adamw",
            tests_required=("run_matrix", "analysis_artifacts"),
            reviewers_required=("qa", "ml-systems"),
            last_matrix_pass="activation-wave-b",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="scheduler:warmup_cosine",
            category="scheduler_policies",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="native_graph_scheduler",
            tests_required=("run_matrix", "analysis_artifacts"),
            reviewers_required=("qa", "ml-systems"),
            last_matrix_pass="activation-wave-b",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="batch:adaptive_gradient_accumulation",
            category="batch_policies",
            current_state="beta",
            activation_bar="mixed",
            resolved_backend_fidelity="native_graph_accumulation_policy",
            tests_required=("run_matrix", "analysis_artifacts"),
            reviewers_required=("qa", "ml-systems"),
            last_matrix_pass="activation-wave-b",
            last_review_wave="wave-2",
        ),
        BetaFeatureGate(
            feature_id="ros:pyrosetta",
            category="preprocessing_modules",
            current_state="beta_soon",
            activation_bar="native_only",
            resolved_backend_fidelity="contract_and_provenance_prototype",
            blockers=(
                "PyRosetta prototype materialization is now defined, but the native runtime is not installed on this machine.",
                "Rosetta-derived outputs remain blocked from launch until Stage 2 scientific review clears the materialization contract.",
            ),
            tests_required=("rosetta_materialization", "compare_runs", "export_truth"),
            reviewers_required=("architecture", "biochem-structural", "ml-systems", "qa", "ux"),
            last_matrix_pass="stage2-track-spec",
            last_review_wave="wave-0",
        ),
        BetaFeatureGate(
            feature_id="preprocess:free_state_comparison",
            category="preprocessing_modules",
            current_state="beta_soon",
            activation_bar="native_only",
            resolved_backend_fidelity="contract_and_pairing_audit_prototype",
            blockers=(
                "Free-state comparison now has a pairing contract, but no governed bound-state/free-state row pairs are launchable yet.",
                "State-pairing semantics remain blocked until Stage 2 free-state review clears the source requirements.",
            ),
            tests_required=("free_state_pairing", "compare_runs", "export_truth"),
            reviewers_required=("architecture", "biochem-structural", "candidate-database", "qa", "ux"),
            last_matrix_pass="stage2-track-spec",
            last_review_wave="wave-0",
        ),
    ]
    return [gate.to_dict() for gate in gates]


def build_activation_readiness_reports() -> list[dict[str, Any]]:
    reports = [
        ActivationReadinessReport(
            feature_id="ros:pyrosetta",
            implementation_classification="native_only_required",
            implementation_completeness="prototype_track_ready_review_pending",
            remaining_risks=(
                "PyRosetta is not installed locally, so the lane currently proves materialization contracts and provenance only, not native execution.",
                "Rosetta outputs must remain explicitly labeled as Rosetta-derived Stage 2 artifacts if and when runtime execution becomes available.",
            ),
            promotion_decision="review_pending",
            notes=(
                "A Stage 2 PyRosetta materialization contract is now emitted as a concrete artifact with required reviewers and matrix tests.",
            ),
        ),
        ActivationReadinessReport(
            feature_id="preprocess:free_state_comparison",
            implementation_classification="native_only_required",
            implementation_completeness="prototype_track_ready_review_pending",
            remaining_risks=(
                "No governed bound-state/free-state structure pairs are currently launchable, so the track is limited to pairing audits and blocker disclosure.",
            ),
            promotion_decision="review_pending",
            notes=(
                "The free-state comparison lane now emits a concrete pairing contract and readiness audit rather than roadmap-only copy.",
            ),
        ),
        ActivationReadinessReport(
            feature_id="graph:shell_graph",
            implementation_classification="native_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Shell-only graphs are still residue-level and rely on interface anchor discovery from bound structures.",
            ),
            promotion_decision="promote_beta",
            notes=("Validated through the same structure parser and shell-selection path used by hybrid graphs.",),
        ),
        ActivationReadinessReport(
            feature_id="graph:whole_complex_graph",
            implementation_classification="native_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Whole-complex mode is truthful for annotated partner rows, but governed staged rows can still fall back to whole-structure residue packaging until native partner-role resolution lands.",
            ),
            promotion_decision="promote_beta",
            notes=("Whole-complex residue packaging is active end-to-end, with explicit disclosure when staged rows still rely on unresolved partner roles.",),
        ),
        ActivationReadinessReport(
            feature_id="graph:atom_graph",
            implementation_classification="native_only_required",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Atom-native beta currently focuses on whole-structure atom connectivity and does not yet expose multi-scale residue-plus-atom fusion views.",
            ),
            promotion_decision="promote_beta",
            notes=("Atom graph now uses a native atom parser, atom-feature payloads, and atom-level artifact summaries.",),
        ),
        ActivationReadinessReport(
            feature_id="node:atom",
            implementation_classification="native_only_required",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Atom node granularity remains beta-scoped and is currently only supported with atom_graph packaging.",
            ),
            promotion_decision="promote_beta",
            notes=("Packaging, validation, and analysis now support atom-native node payloads.",),
        ),
        ActivationReadinessReport(
            feature_id="model:gin",
            implementation_classification="adapter_backed",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Resolved backend must remain visible so users do not confuse "
                "the adapter with a distinct native GIN implementation.",
            ),
            promotion_decision="promote_beta",
            notes=("Validated through the same graph-materialization lane as graphsage-lite.",),
        ),
        ActivationReadinessReport(
            feature_id="model:gcn",
            implementation_classification="adapter_backed",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=("Resolved backend must stay visible in UI and reports.",),
            promotion_decision="promote_beta",
            notes=("Adapter-backed execution is allowed only for model runtime families.",),
        ),
        ActivationReadinessReport(
            feature_id="model:gat",
            implementation_classification="adapter_backed",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "The current GAT option is an honest adapter over the lightweight graph trainer, not a distinct attention-native backend.",
            ),
            promotion_decision="promote_beta",
            notes=("Resolved backend and requested family must both stay visible in analysis and compare views.",),
        ),
        ActivationReadinessReport(
            feature_id="pool:expanded_ppi_procurement_bridge",
            implementation_classification="native_only_required",
            implementation_completeness="gated_beta_soon",
            remaining_risks=("Canonicalization and balancing review are incomplete.",),
            promotion_decision="hold",
            notes=("Procurement content stays visible but gated until integration completes.",),
        ),
        ActivationReadinessReport(
            feature_id="partner:role_conditioned",
            implementation_classification="native_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Role-conditioned channels currently affect graph payloads only; "
                "UI copy must keep that boundary explicit.",
            ),
            promotion_decision="promote_beta",
            notes=("Graph node payloads now vary by partner-awareness mode.",),
        ),
        ActivationReadinessReport(
            feature_id="distributed:water_network_descriptors",
            implementation_classification="native_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Descriptors are bound-state structural proxies and do not yet "
                "encode displaced-water or free-state reasoning.",
            ),
            promotion_decision="promote_beta",
            notes=("Distributed packaging now emits explicit water-bridge proxy descriptors.",),
        ),
        ActivationReadinessReport(
            feature_id="distributed:sequence_embeddings",
            implementation_classification="native_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Sequence embeddings are deterministic local study artifacts and must keep their model/runtime identity visible in compare and export views.",
            ),
            promotion_decision="promote_beta",
            notes=("Studio-native sequence embeddings now materialize per-example payloads with provenance and leakage-audit disclosure.",),
        ),
        ActivationReadinessReport(
            feature_id="preprocess:sequence_embeddings",
            implementation_classification="native_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Sequence-embedding preprocessing is beta-scoped and should remain explicit about local deterministic runtime identity.",
            ),
            promotion_decision="promote_beta",
            notes=("Sequence-embedding materialization is now owned by the Studio runtime rather than being a placeholder module.",),
        ),
        ActivationReadinessReport(
            feature_id="optimizer:lion",
            implementation_classification="adapter_graph_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=(
                "Lion remains blocked for sklearn-backed tabular families and "
                "must stay compatibility-gated.",
            ),
            promotion_decision="promote_beta",
            notes=("Graph trainer now exposes Lion-style control selection with resolved adapter metadata.",),
        ),
        ActivationReadinessReport(
            feature_id="scheduler:warmup_cosine",
            implementation_classification="native_graph_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=("Warmup + cosine remains limited to the lightweight graph trainer families.",),
            promotion_decision="promote_beta",
            notes=("Scheduler resolution is now recorded in graph model details and manifests.",),
        ),
        ActivationReadinessReport(
            feature_id="batch:adaptive_gradient_accumulation",
            implementation_classification="native_graph_only",
            implementation_completeness="promoted_beta_ready",
            remaining_risks=("Adaptive accumulation is advisory for the lightweight graph trainer and not a general execution engine policy.",),
            promotion_decision="promote_beta",
            notes=("Graph trainer now records resolved batch and accumulation behavior.",),
        ),
    ]
    return [report.to_dict() for report in reports]


def build_model_activation_matrix() -> dict[str, Any]:
    active_models = (
        "xgboost",
        "catboost",
        "mlp",
        "multimodal_fusion",
        "graphsage",
        "gin",
        "gcn",
        "gat",
        "late_fusion_ensemble",
    )
    feature_bundles = ("global_only", "graph_plus_global", "graph_plus_distributed_plus_global")
    split_modes = ("leakage_resistant_benchmark", "graph_component_grouped", "accession_grouped")
    hardware_modes = ("auto_recommend", "cpu_parallel", "single_gpu", "multi_worker_large_memory")
    graph_training_controls = {
        "optimizers": ["adam", "adamw", "sgd_momentum", "lion"],
        "schedulers": ["cosine_decay", "one_cycle", "plateau", "warmup_cosine"],
        "batch_policies": [
            "dynamic_by_graph_size",
            "fixed_small_batch",
            "fixed_medium_batch",
            "adaptive_gradient_accumulation",
        ],
    }
    entries: list[dict[str, Any]] = []
    governed_scope_allowed = {
        "graph_kind": ["whole_complex_graph"],
        "region_policy": ["whole_molecule"],
        "partner_awareness": ["symmetric"],
        "reason": (
            "Governed staged rows are launchable only through whole_complex_graph plus "
            "whole_molecule plus symmetric partner awareness until native partner-role "
            "resolution lands."
        ),
    }
    governed_scope_blocked = {
        "status": "blocked",
        "reason": (
            "Governed staged rows are only launchable through whole_complex_graph plus "
            "whole_molecule plus symmetric partner awareness."
        ),
    }
    for model in active_models:
        supported_graphs = (
            (
                "interface_graph",
                "residue_graph",
                "hybrid_graph",
                "shell_graph",
                "whole_complex_graph",
                "atom_graph",
            )
            if model in {"graphsage", "gin", "gcn", "gat", "multimodal_fusion"}
            else ("interface_graph", "hybrid_graph", "whole_complex_graph")
        )
        for graph_kind in supported_graphs:
            for feature_bundle in feature_bundles:
                entries.append(
                    {
                        "model_family": model,
                        "graph_kind": graph_kind,
                        "feature_bundle": feature_bundle,
                        "resolved_backend_family": (
                            "adapter:graphsage-lite-family"
                            if model in {"gin", "gcn", "gat"}
                            else "torch-graphsage-lite"
                            if model == "graphsage"
                            else "mixed-model-family"
                        ),
                        "supported_splits": list(split_modes),
                        "supported_hardware": list(hardware_modes),
                        "supported_partner_awareness": (
                            ["symmetric"]
                            if graph_kind in {"whole_complex_graph", "atom_graph"}
                            else ["symmetric", "asymmetric", "role_conditioned"]
                            if model in {"graphsage", "gin", "gcn", "gat"}
                            else ["symmetric", "asymmetric"]
                        ),
                        "dataset_scope_constraints": (
                            {
                                GOVERNED_PPI_SUBSET_DATASET_REF: (
                                    governed_scope_allowed
                                    if graph_kind == "whole_complex_graph"
                                    else governed_scope_blocked
                                ),
                                GOVERNED_PPI_SUBSET_V2_DATASET_REF: (
                                    governed_scope_allowed
                                    if graph_kind == "whole_complex_graph"
                                    else governed_scope_blocked
                                ),
                            }
                        ),
                        "supported_distributed_feature_sets": [
                            "residue_contacts",
                            "interface_geometry",
                            "water_context",
                            "interface_chemistry_maps",
                            "water_network_descriptors",
                            "sequence_embeddings",
                        ],
                        "training_controls": (
                            graph_training_controls
                            if model in {"graphsage", "gin", "gcn", "gat"}
                            else {
                                "optimizers": ["backend_default_only"],
                                "schedulers": ["backend_default_only"],
                                "batch_policies": ["backend_default_only"],
                            }
                        ),
                        "status": (
                            "active"
                            if model != "late_fusion_ensemble"
                            or graph_kind != "shell_graph"
                            else "beta"
                        ),
                    }
                )
    return ModelActivationMatrix(
        matrix_id="model-activation-matrix:broadened-beta",
        entries=tuple(entries),
        notes=(
            "Adapter-backed model families remain active only when the "
            "resolved backend is surfaced in run outputs.",
        ),
    ).to_dict()


def _dataset_lookup() -> dict[str, DatasetDescriptor]:
    return {
        item["dataset_ref"]: DatasetDescriptor(
            dataset_ref=item["dataset_ref"],
            label=item["label"],
            task_type=item["task_type"],
            split_strategy=item["split_strategy"],
            train_csv=Path(item["train_csv"]),
            val_csv=Path(item["val_csv"]) if item.get("val_csv") else None,
            test_csv=Path(item["test_csv"]),
            source_manifest=Path(item["source_manifest"]),
            row_count=int(item["row_count"]),
            tags=tuple(item["tags"]),
            maturity=item["maturity"],
            catalog_status=item.get("catalog_status", "lab"),
        )
        for item in list_known_datasets()
    }


def _launchable_dataset_pools() -> list[dict[str, Any]]:
    promoted_ids = _promoted_pool_ids_from_reports()
    launchable: list[dict[str, Any]] = []
    for pool in list_dataset_pools():
        truth_boundary = pool.get("truth_boundary", {})
        if pool.get("pool_id") not in promoted_ids:
            continue
        if truth_boundary.get("report_only_bundle") or truth_boundary.get("non_governing"):
            continue
        if truth_boundary.get("row_level_provenance_state") == "dataset_level_only_pending_bridge_compiler":
            continue
        if truth_boundary.get("measurement_normalization_state") == "pending_bridge_compiler":
            continue
        if truth_boundary.get("admissibility_flag_state") == "pending_bridge_compiler":
            continue
        if not pool.get("dataset_refs"):
            continue
        launchable.append(pool)
    return launchable


def _source_family_registry() -> dict[str, tuple[str, ...]]:
    launchable_pools = _launchable_dataset_pools()
    all_launchable_refs = tuple(
        dict.fromkeys(
            ref
            for pool in launchable_pools
            for ref in pool.get("dataset_refs", ())
            if _clean_text(ref)
        )
    )
    pool_by_ref = {
        ref: pool
        for pool in launchable_pools
        for ref in pool.get("dataset_refs", ())
        if _clean_text(ref)
    }
    ppi_launchable_refs = tuple(
        ref
        for ref in all_launchable_refs
        if _clean_text(pool_by_ref.get(ref, {}).get("truth_boundary", {}).get("complex_type"))
        != "protein_ligand"
        and (
            _clean_text(pool_by_ref.get(ref, {}).get("source_family")) !=
            GOVERNED_PL_BRIDGE_PILOT_SOURCE_FAMILY
        )
    )
    release_refs = tuple(
        ref
        for ref in ppi_launchable_refs
        if (pool_by_ref.get(ref, {}).get("status") == "release")
    )
    robust_refs = tuple(ref for ref in ppi_launchable_refs if ref == "robust_pp_benchmark_v1")
    governed_subset_refs = tuple(
        ref
        for ref in ppi_launchable_refs
        if ref
        in {
            GOVERNED_PPI_SUBSET_DATASET_REF,
            GOVERNED_PPI_SUBSET_V2_DATASET_REF,
            GOVERNED_PPI_EXTERNAL_BETA_CANDIDATE_DATASET_REF,
        }
    )
    ligand_pilot_refs = tuple(
        ref for ref in all_launchable_refs if ref == GOVERNED_PL_BRIDGE_PILOT_DATASET_REF
    )
    approved_local_refs = tuple(dict.fromkeys((*release_refs, *robust_refs)))
    return {
        "release_frozen": release_refs or ("release_pp_alpha_benchmark_v1",),
        "robust_structure_backed": robust_refs,
        "approved_local_ppi": approved_local_refs,
        "balanced_ppi_beta_pool": ppi_launchable_refs,
        "governed_ppi_promoted_subsets": governed_subset_refs,
        "governed_pl_bridge_pilot": ligand_pilot_refs,
        "expanded_ppi_procurement": ("expanded_ppi_procurement_bridge",),
    }


def _resolve_dataset(spec: ModelStudioPipelineSpec) -> DatasetDescriptor:
    known = _dataset_lookup()
    for ref in spec.data_strategy.dataset_refs:
        if ref in known:
            return known[ref]
    if spec.data_strategy.task_type == "protein-protein":
        if "release_pp_alpha_benchmark_v1" in known:
            return known["release_pp_alpha_benchmark_v1"]
        if "expanded_pp_benchmark_v1" in known:
            return known["expanded_pp_benchmark_v1"]
        if "robust_pp_benchmark_v1" in known:
            return known["robust_pp_benchmark_v1"]
    if spec.data_strategy.task_type == "protein-ligand":
        if GOVERNED_PL_BRIDGE_PILOT_DATASET_REF in known:
            return known[GOVERNED_PL_BRIDGE_PILOT_DATASET_REF]
    raise FileNotFoundError("No runnable Studio dataset matches the current pipeline draft.")


def _resolve_request_dataset_refs(
    request: TrainingSetRequestSpec,
    fallback_refs: tuple[str, ...],
) -> tuple[str, ...]:
    resolved: list[str] = []
    family_map = _source_family_registry()
    for item in request.dataset_refs:
        if item not in resolved:
            resolved.append(item)
    for family in request.source_families:
        for item in family_map.get(family, ()):
            if item not in resolved:
                resolved.append(item)
    for item in fallback_refs:
        if item not in resolved:
            resolved.append(item)
    if not resolved:
        if request.task_type == "protein-ligand":
            resolved.append(GOVERNED_PL_BRIDGE_PILOT_DATASET_REF)
        else:
            resolved.append("release_pp_alpha_benchmark_v1")
    return tuple(resolved)


@ttl_cache(maxsize=128)
def _load_rows_from_csv(path: Path, split: str) -> list[BenchmarkRow]:
    rows: list[BenchmarkRow] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for item in reader:
            structure_text = _clean_text(item.get("Structure File"))
            structure_file = (
                MISSING_STRUCTURE_SENTINEL
                if not structure_text or structure_text in {".", "none", "null"}
                else Path(structure_text)
            )
            rows.append(
                BenchmarkRow(
                    split=split,
                    pdb_id=_clean_text(item.get("PDB")),
                    exp_dg=_safe_float(item.get("exp_dG")),
                    source_dataset=_clean_text(item.get("Source Data Set")) or "unknown",
                    complex_type=_clean_text(item.get("Complex Type")) or "protein_protein",
                    protein_accessions=_split_tokens(item.get("Mapped Protein Accessions", "")),
                    ligand_chains=_split_tokens(item.get("Ligand Chains", "")),
                    receptor_chains=_split_tokens(item.get("Receptor Chains", "")),
                    structure_file=structure_file,
                    resolution=_safe_float(item.get("Resolution (A)")),
                    release_year=_safe_int(item.get("Release Year")),
                    temperature_k=_safe_float(item.get("Label Temperature (K)"), 298.15),
                    metadata={key: value for key, value in item.items()},
                )
            )
    return rows


def _load_dataset_rows(
    dataset: DatasetDescriptor,
) -> tuple[list[BenchmarkRow], list[BenchmarkRow], list[BenchmarkRow]]:
    return (
        _load_rows_from_csv(dataset.train_csv, "train"),
        _load_rows_from_csv(dataset.val_csv, "val") if dataset.val_csv else [],
        _load_rows_from_csv(dataset.test_csv, "test"),
    )


def _copy_row(row: BenchmarkRow, *, split: str | None = None) -> BenchmarkRow:
    return BenchmarkRow(
        split=split or row.split,
        pdb_id=row.pdb_id,
        exp_dg=row.exp_dg,
        source_dataset=row.source_dataset,
        complex_type=row.complex_type,
        protein_accessions=row.protein_accessions,
        ligand_chains=row.ligand_chains,
        receptor_chains=row.receptor_chains,
        structure_file=row.structure_file,
        resolution=row.resolution,
        release_year=row.release_year,
        temperature_k=row.temperature_k,
        metadata=dict(row.metadata),
    )


def _row_priority(row: BenchmarkRow) -> tuple[Any, ...]:
    return (
        not row.structure_file.exists(),
        row.resolution or 99.0,
        -(row.release_year or 0),
        row.source_dataset,
        row.pdb_id,
    )


# ``_measurement_type`` is imported from ``_rows.py`` (see import
# block above). The original inline definition lived here.


def _affinity_value_molar(row: BenchmarkRow) -> float | None:
    value = _safe_float(row.metadata.get("Affinity Value (M)"), math.nan)
    if value != value or value <= 0.0:
        return None
    return float(value)


def _label_origin(row: BenchmarkRow, label_type: str) -> str:
    requested = _clean_text(label_type) or "delta_G"
    if requested == "IC50":
        return "proxy_assay_measurement"
    if requested in {"Kd", "Ki"}:
        return "direct_measured_affinity"
    return "derived_thermodynamic_delta_g"


def _label_conversion_provenance(row: BenchmarkRow, label_type: str) -> str | None:
    requested = _clean_text(label_type) or "delta_G"
    measurement_type = _measurement_type(row)
    if requested == "delta_G":
        if measurement_type in {"Kd", "Ki", "IC50"} and _affinity_value_molar(row) is not None:
            return f"delta_g_derived_from_{measurement_type.lower()}_molar_affinity"
        return "dataset_native_delta_g"
    if requested == "IC50":
        if (
            measurement_type == "IC50"
            and _affinity_value_molar(row) is not None
            and _clean_text(row.metadata.get("Assay Family"))
        ):
            return "normalized_ic50_molar_proxy_with_assay_disclosure"
        return None
    if requested in {"Kd", "Ki"}:
        if measurement_type == requested and _affinity_value_molar(row) is not None:
            return f"direct_{requested.lower()}_molar_measurement"
        return None
    return None


def _label_value(row: BenchmarkRow, label_type: str) -> float:
    requested = _clean_text(label_type) or "delta_G"
    if requested == "delta_G":
        return row.exp_dg
    measurement_type = _measurement_type(row)
    affinity_value = _affinity_value_molar(row)
    if affinity_value is None:
        return math.nan
    if requested in {"Kd", "Ki"}:
        return affinity_value if measurement_type == requested else math.nan
    if requested == "IC50":
        return affinity_value if measurement_type == "IC50" else math.nan
    return math.nan


def _label_bin_name_for_type(value: float, label_type: str) -> str:
    requested = _clean_text(label_type) or "delta_G"
    if value != value:
        return "unknown"
    if requested == "delta_G":
        return _label_bin_name(value)
    if value <= 0.0:
        return "unknown"
    potency = -math.log10(value)
    if potency >= 11.0:
        return "very_high"
    if potency >= 9.0:
        return "high"
    if potency >= 7.0:
        return "mid"
    if potency >= 5.0:
        return "low"
    return "very_low"


def _label_payload(row: BenchmarkRow, label_type: str) -> dict[str, Any]:
    requested = _clean_text(label_type) or "delta_G"
    return {
        "requested_label_type": requested,
        "resolved_label_type": requested,
        "value": _label_value(row, requested),
        "measurement_type": _measurement_type(row),
        "label_origin": _label_origin(row, requested),
        "conversion_provenance": _label_conversion_provenance(row, requested),
        "assay_family": _clean_text(row.metadata.get("Assay Family")) or None,
    }


def _label_provenance_summary(rows: list[BenchmarkRow], label_type: str) -> dict[str, Any]:
    payloads = [_label_payload(row, label_type) for row in rows]
    origins = sorted({item["label_origin"] for item in payloads if _clean_text(item["label_origin"])})
    conversions = sorted(
        {
            item["conversion_provenance"]
            for item in payloads
            if _clean_text(item["conversion_provenance"])
        }
    )
    assay_families = sorted(
        {item["assay_family"] for item in payloads if _clean_text(item["assay_family"])}
    )
    return {
        "requested_label_type": _clean_text(label_type) or "delta_G",
        "resolved_label_type": _clean_text(label_type) or "delta_G",
        "label_origin": origins[0] if len(origins) == 1 else ("mixed" if origins else "unknown"),
        "label_origin_variants": origins,
        "conversion_provenance": conversions[0] if len(conversions) == 1 else None,
        "conversion_provenance_variants": conversions,
        "assay_families": assay_families,
    }


def _label_bin_name(value: float) -> str:
    if value <= -14.0:
        return "very_high"
    if value <= -11.0:
        return "high"
    if value <= -8.0:
        return "mid"
    if value <= -5.0:
        return "low"
    return "very_low"


def _partner_signature(row: BenchmarkRow) -> str:
    if row.complex_type == "protein_ligand":
        return _protein_ligand_pair_signature(row)
    return "|".join(sorted(accession for accession in row.protein_accessions if accession)) or (
        f"pdb:{row.pdb_id}"
    )


def _bucket_breakdown(rows: list[BenchmarkRow], key_fn: Any) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for row in rows:
        key = _clean_text(key_fn(row)) or "unknown"
        breakdown[key] = breakdown.get(key, 0) + 1
    return breakdown


def _drop_reason_summary(
    rows: list[BenchmarkRow],
    dropped: list[str],
) -> dict[str, Any]:
    source_by_pdb: dict[str, str] = {}
    for row in rows:
        source_by_pdb.setdefault(row.pdb_id.upper(), _clean_text(row.source_dataset) or "unknown")
    drop_reason_breakdown: dict[str, int] = {}
    drop_source_breakdown: dict[str, dict[str, int]] = {}
    for item in dropped:
        pdb_id, _, reason = item.partition(":")
        normalized_reason = _clean_text(reason) or "unknown"
        source_name = source_by_pdb.get(_clean_text(pdb_id).upper(), "unknown")
        drop_reason_breakdown[normalized_reason] = drop_reason_breakdown.get(normalized_reason, 0) + 1
        source_payload = drop_source_breakdown.setdefault(source_name, {})
        source_payload[normalized_reason] = source_payload.get(normalized_reason, 0) + 1
    total = len(rows) or 1
    return {
        "drop_reason_breakdown": drop_reason_breakdown,
        "drop_source_breakdown": drop_source_breakdown,
        "missing_structure_rate": round(drop_reason_breakdown.get("missing_structure", 0) / total, 6),
        "resolution_filter_rate": round(drop_reason_breakdown.get("resolution", 0) / total, 6),
    }


# ``_copy_row_with_metadata`` is imported from ``_rows.py`` (see
# import block at the top). The original inline definition lived here.


@ttl_cache(maxsize=1)
def _materialize_governed_ppi_subset() -> dict[str, Any]:
    # v1 base materializer for the legacy ``governed_ppi_blended_subset_v1``
    # bridge. Unlike v2/external_beta/stage2 which filter out rows whose
    # structure files are missing, this lane deliberately retains
    # structure-less rows so downstream review machinery can flag them
    # (the v1 lane was designed before structure-backed filtering
    # became standard).
    #
    # Same shared helpers as the structure-filtered materializers,
    # just with ``filter_missing_structure=False``.
    robust_dataset = _benchmark_dataset_descriptor(
        dataset_ref="robust_pp_benchmark_v1",
        label="Robust PPI benchmark",
        split_strategy="leakage_resistant_benchmark",
        source_manifest=ROBUST_LATEST,
        tags=("ppi", "robust"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    expanded_dataset = _benchmark_dataset_descriptor(
        dataset_ref="expanded_pp_benchmark_v1",
        label="Expanded PPI benchmark",
        split_strategy="graph_component_grouped",
        source_manifest=EXPANDED_LATEST,
        tags=("ppi", "expanded"),
        maturity="training_ready_candidate",
        catalog_status="beta",
    )
    if robust_dataset is None or expanded_dataset is None:
        return {}
    robust_rows = _annotated_benchmark_rows(
        robust_dataset,
        source_family="robust_pp_benchmark_v1",
        filter_missing_structure=False,
    )
    expanded_rows = _annotated_benchmark_rows(
        expanded_dataset,
        source_family="expanded_pp_benchmark_v1",
        filter_missing_structure=False,
    )
    base_rows = [*robust_rows, *expanded_rows]
    staged_rows, staged_diagnostics = _select_governed_subset_staged_rows(base_rows)
    staged_rows = [
        _copy_row_with_metadata(
            row,
            source_dataset=GOVERNED_PPI_SUBSET_SOURCE_FAMILY,
            metadata_updates={
                "Source Family": "expanded_ppi_procurement_bridge",
            },
        )
        for row in staged_rows
    ]
    combined_rows = [*base_rows, *staged_rows]
    split_rows, split_diagnostics = _compile_split_rows(
        combined_rows,
        SplitPlanSpec(
            plan_id="split:governed_ppi_blended_subset_v1",
            objective="accession_grouped",
            grouping_policy="accession_grouped",
            holdout_policy="governed_structure_backed_subset",
            train_fraction=0.7,
            val_fraction=0.1,
            test_fraction=0.2,
        ),
    )
    subset_root = _governed_subset_root(GOVERNED_PPI_SUBSET_DATASET_REF)
    subset_root.mkdir(parents=True, exist_ok=True)
    train_csv = subset_root / "train.csv"
    val_csv = subset_root / "val.csv"
    test_csv = subset_root / "test.csv"
    _write_benchmark_rows(train_csv, split_rows["train"])
    _write_benchmark_rows(val_csv, split_rows["val"])
    _write_benchmark_rows(test_csv, split_rows["test"])

    row_count = len(combined_rows)
    source_rows = {
        "robust_pp_benchmark_v1": len(robust_rows),
        "expanded_pp_benchmark_v1": len(expanded_rows),
        "expanded_ppi_procurement_bridge": len(staged_rows),
    }
    assay_family_mix = _bucket_breakdown(
        combined_rows,
        lambda row: row.metadata.get("Assay Family") or _measurement_type(row),
    )
    label_bin_mix = _label_bin_mix(combined_rows)
    structure_present = sum(1 for row in combined_rows if row.structure_file.exists())
    structure_coverage = round(structure_present / max(row_count, 1), 4)
    top_source_share = max(source_rows.values()) / max(row_count, 1)
    top_assay_share = max(assay_family_mix.values()) / max(row_count, 1)
    blockers: list[str] = []
    if row_count < 1500:
        blockers.append("Governed subset remains below the 1,500-row promotion threshold.")
    if top_source_share > 0.45:
        blockers.append("One source family still exceeds the 45% dominance threshold.")
    if top_assay_share > 0.60:
        blockers.append("One assay family still exceeds the 60% dominance threshold.")
    blockers.append(
        "Staged procurement-bridge rows remain beta-review-only and whole-complex only until governed training eligibility expands beyond the current bridge review state."
    )
    promotion_readiness = "review_pending_candidate" if row_count >= 1500 else "review_pending"
    launchability_reason = (
        "Review-pending candidate for the broadened beta lane. The subset is balanced and execution-tested, but staged procurement-bridge rows remain beta-review-only and whole-complex only until native partner-role resolution and governing-ready coverage expand."
    )
    truth_boundary = {
        "dataset_ref": GOVERNED_PPI_SUBSET_DATASET_REF,
        "catalog_status": "beta",
        "source_manifest": _artifact_rel(subset_root / "dataset_manifest.json"),
        "structure_backed": structure_coverage >= 0.95,
        "structure_coverage": structure_coverage,
        "governed_subset": True,
        "graph_partner_roles_resolved": False,
        "partner_role_resolution": "mixed_native_and_unresolved_whole_complex_only",
        "whole_complex_only_for_staged_rows": True,
        "launchability_reason": launchability_reason,
        "promotion_readiness": promotion_readiness,
    }
    balancing = {
        "source_family_mix": source_rows,
        "selected_measurement_type_counts": assay_family_mix,
        "label_bin_mix": label_bin_mix,
        "quality_verdict": (
            "promotion_ready_governed_subset" if not blockers else "review_pending_governed_subset"
        ),
        "staged_row_selection": staged_diagnostics,
        "graph_runtime_constraint": "whole_complex_only_for_unresolved_partner_rows",
    }
    dataset_manifest = {
        "dataset_ref": GOVERNED_PPI_SUBSET_DATASET_REF,
        "label": "Governed blended PPI subset",
        "task_type": "protein-protein",
        "split_strategy": "accession_grouped",
        "train_csv": str(train_csv),
        "val_csv": str(val_csv),
        "test_csv": str(test_csv),
        "row_count": row_count,
        "source_manifest": str(subset_root / "dataset_manifest.json"),
        "tags": [
            "ppi",
            "governed-subset",
            "promotion-queue",
            "staged-structure-backed-under-review",
        ],
        "maturity": "review_pending_candidate",
        "catalog_status": "beta",
        "truth_boundary": truth_boundary,
        "balancing": balancing,
        "split_preview": split_diagnostics,
        "created_at": _utc_now(),
    }
    _save_json(subset_root / "dataset_manifest.json", dataset_manifest)
    subset_manifest = GovernedSubsetManifest(
        subset_id=f"subset:{GOVERNED_PPI_SUBSET_DATASET_REF}",
        label="Governed blended PPI subset",
        promoted_dataset_ref=GOVERNED_PPI_SUBSET_DATASET_REF,
        row_count=row_count,
        source_rows=source_rows,
        balancing_policy="balance_first_structured_subset",
        source_family_mix=source_rows,
        assay_family_mix=assay_family_mix,
        label_bin_mix=label_bin_mix,
        exclusion_reasons=tuple(
            f"{key}={value}"
            for key, value in sorted((staged_diagnostics.get("exclusion_counts") or {}).items())
            if value
        ),
        launchability_reason=launchability_reason,
        promotion_readiness=promotion_readiness,
        review_signoff_state="wave_1_pending_reviews",
        blockers=tuple(blockers),
        notes=(
            "Subset blends the robust and expanded launchable pools with non-overlapping governed staged PPI rows.",
            "Staged bridge rows stay whole-complex only until native partner-role resolution lands.",
            "Current staged rows remain beta-review-only rather than launchable-study-eligible, so the subset is execution-tested but not promoted into the default beta lane.",
        ),
    )
    _save_json(subset_root / "governed_subset_manifest.json", subset_manifest.to_dict())
    return {
        "dataset_manifest": dataset_manifest,
        "subset_manifest": subset_manifest.to_dict(),
    }


def _balance_candidate_rows(
    rows: list[BenchmarkRow],
    request: TrainingSetRequestSpec,
) -> tuple[list[BenchmarkRow], dict[str, Any]]:
    unique_rows = sorted(
        { _candidate_row_identity(row): row for row in rows }.values(),
        key=_row_priority,
    )
    requested_target_size = request.target_size if (request.target_size or 0) > 0 else None
    eligible_quality_ceiling = len(unique_rows)
    resolved_target_cap = min(requested_target_size or eligible_quality_ceiling, eligible_quality_ceiling)
    target_size_warning = None
    if requested_target_size is not None and requested_target_size > eligible_quality_ceiling:
        target_size_warning = (
            f"Requested target size {requested_target_size} exceeds the eligible quality-controlled "
            f"ceiling of {eligible_quality_ceiling}. The build will cap at {resolved_target_cap}."
        )
    if not unique_rows or resolved_target_cap >= len(unique_rows):
        return unique_rows, {
            "mode": "full_pool",
            "selected_count": len(unique_rows),
            "requested_target_size": requested_target_size,
            "eligible_quality_ceiling": eligible_quality_ceiling,
            "resolved_target_cap": resolved_target_cap,
            "final_selected_count": len(unique_rows),
            "target_size_warning": target_size_warning,
            "source_breakdown": _source_breakdown(unique_rows),
            "measurement_type_breakdown": _bucket_breakdown(unique_rows, _measurement_type),
            "label_bin_breakdown": _bucket_breakdown(
                unique_rows,
                lambda row: _label_bin_name_for_type(
                    _label_value(row, request.label_type),
                    request.label_type,
                ),
            ),
            "partner_cap": None,
            "source_cap": None,
        }
    target_size = resolved_target_cap
    by_source: dict[str, list[BenchmarkRow]] = {}
    by_source_label: dict[tuple[str, str], list[BenchmarkRow]] = {}
    for row in unique_rows:
        source_name = row.source_dataset or "unknown"
        label_bin = _label_bin_name_for_type(_label_value(row, request.label_type), request.label_type)
        by_source.setdefault(source_name, []).append(row)
        by_source_label.setdefault((source_name, label_bin), []).append(row)
    source_names = sorted(by_source)
    label_bins = ("very_high", "high", "mid", "low", "very_low")
    partner_cap = 2 if len(source_names) > 1 else 999999
    source_cap = max(4, math.ceil(target_size / max(len(source_names), 1)) + 2)
    selected: list[BenchmarkRow] = []
    seen_pdbs: set[str] = set()
    partner_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    while len(selected) < target_size and any(by_source_label.values()):
        progressed = False
        for label_bin in label_bins:
            for source_name in source_names:
                queue = by_source_label.get((source_name, label_bin), [])
                while queue:
                    row = queue.pop(0)
                    partner_key = _partner_signature(row)
                    if source_counts.get(source_name, 0) >= source_cap:
                        continue
                    if row.pdb_id in seen_pdbs:
                        continue
                    if partner_counts.get(partner_key, 0) >= partner_cap:
                        continue
                    selected.append(row)
                    seen_pdbs.add(row.pdb_id)
                    partner_counts[partner_key] = partner_counts.get(partner_key, 0) + 1
                    source_counts[source_name] = source_counts.get(source_name, 0) + 1
                    progressed = True
                    break
                if len(selected) >= target_size:
                    break
            if len(selected) >= target_size:
                break
        if not progressed:
            fallback = sorted(
                (
                    row
                    for queue in by_source_label.values()
                    for row in queue
                    if row.pdb_id not in seen_pdbs
                ),
                key=lambda row: (
                    source_counts.get(row.source_dataset or "unknown", 0),
                    partner_counts.get(_partner_signature(row), 0),
                    _label_bin_name_for_type(_label_value(row, request.label_type), request.label_type),
                    *_row_priority(row),
                ),
            )
            for row in fallback:
                if len(selected) >= target_size:
                    break
                source_name = row.source_dataset or "unknown"
                partner_key = _partner_signature(row)
                if row.pdb_id in seen_pdbs:
                    continue
                if partner_counts.get(partner_key, 0) >= partner_cap:
                    continue
                selected.append(row)
                seen_pdbs.add(row.pdb_id)
                partner_counts[partner_key] = partner_counts.get(partner_key, 0) + 1
                source_counts[source_name] = source_counts.get(source_name, 0) + 1
            break
    return selected[:target_size], {
        "mode": "balance_first_source_and_label_bins",
        "selected_count": min(target_size, len(selected)),
        "requested_target_size": requested_target_size,
        "eligible_quality_ceiling": eligible_quality_ceiling,
        "resolved_target_cap": resolved_target_cap,
        "final_selected_count": min(target_size, len(selected)),
        "target_size_warning": target_size_warning,
        "source_breakdown": _source_breakdown(selected[:target_size]),
        "measurement_type_breakdown": _bucket_breakdown(
            selected[:target_size], _measurement_type
        ),
        "label_bin_breakdown": _bucket_breakdown(
            selected[:target_size],
            lambda row: _label_bin_name_for_type(
                _label_value(row, request.label_type),
                request.label_type,
            ),
        ),
        "partner_cap": None if partner_cap == 999999 else partner_cap,
        "source_cap": source_cap,
        "unique_partner_signatures": len(
            {_partner_signature(row) for row in selected[:target_size]}
        ),
    }


def _filter_candidate_rows(
    rows: list[BenchmarkRow],
    request: TrainingSetRequestSpec,
) -> tuple[list[BenchmarkRow], list[str]]:
    filtered: list[BenchmarkRow] = []
    dropped: list[str] = []
    max_resolution = float(request.inclusion_filters.get("max_resolution", 99.0) or 99.0)
    min_release_year = int(request.inclusion_filters.get("min_release_year", 0) or 0)
    exclude_ids = {
        _clean_text(item).upper() for item in request.exclusion_filters.get("exclude_pdb_ids", [])
    }
    required_structure = request.structure_source_policy == "experimental_only"
    expected_complex_type = (
        "protein_ligand" if request.task_type == "protein-ligand" else "protein_protein"
    )
    requested_label_type = _clean_text(request.label_type) or "delta_G"
    seen_rows: set[tuple[Any, ...]] = set()
    for row in rows:
        pdb_id = row.pdb_id.upper()
        row_key = _candidate_row_identity(row)
        if row_key in seen_rows:
            continue
        seen_rows.add(row_key)
        if row.complex_type != expected_complex_type:
            dropped.append(
                f"{pdb_id}:{'non_protein_ligand' if expected_complex_type == 'protein_ligand' else 'non_ppi'}"
            )
            continue
        if pdb_id in exclude_ids:
            dropped.append(f"{pdb_id}:excluded")
            continue
        if row.resolution and row.resolution > max_resolution:
            dropped.append(f"{pdb_id}:resolution")
            continue
        if row.release_year and row.release_year < min_release_year:
            dropped.append(f"{pdb_id}:release_year")
            continue
        if required_structure and not row.structure_file.exists():
            dropped.append(f"{pdb_id}:missing_structure")
            continue
        label_payload = _label_payload(row, requested_label_type)
        if label_payload["value"] != label_payload["value"]:
            if requested_label_type in {"Kd", "Ki", "IC50"}:
                dropped.append(f"{pdb_id}:label_family_mismatch")
            else:
                dropped.append(f"{pdb_id}:missing_label_value")
            continue
        if requested_label_type == "IC50" and not label_payload["conversion_provenance"]:
            dropped.append(f"{pdb_id}:missing_ic50_provenance")
            continue
        if requested_label_type == "IC50" and not label_payload["assay_family"]:
            dropped.append(f"{pdb_id}:missing_assay_family")
            continue
        if expected_complex_type == "protein_ligand":
            if not _clean_text(row.metadata.get("Ligand Canonical Component Id")):
                dropped.append(f"{pdb_id}:missing_ligand_canonical_id")
                continue
            if not _clean_text(row.metadata.get("Protein-Ligand Pair Grouping Key")):
                dropped.append(f"{pdb_id}:missing_pair_grouping_key")
                continue
            if not _clean_text(row.metadata.get("Ligand Bridge Provenance Refs")):
                dropped.append(f"{pdb_id}:missing_bridge_provenance")
                continue
        filtered.append(_copy_row(row, split="candidate"))
    return filtered, dropped


def _group_rows_by_accession_components(rows: list[BenchmarkRow]) -> list[list[BenchmarkRow]]:
    if not rows:
        return []
    accession_to_indices: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        accessions = row.protein_accessions or (f"pdb:{row.pdb_id}",)
        for accession in accessions:
            accession_to_indices.setdefault(accession, []).append(index)
    parent = list(range(len(rows)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for indices in accession_to_indices.values():
        anchor = indices[0]
        for index in indices[1:]:
            union(anchor, index)

    components: dict[int, list[BenchmarkRow]] = {}
    for index, row in enumerate(rows):
        components.setdefault(find(index), []).append(row)
    return sorted(
        components.values(),
        key=lambda items: (-len(items), items[0].pdb_id),
    )


def _group_rows_by_exact_accession_signature(rows: list[BenchmarkRow]) -> list[list[BenchmarkRow]]:
    groups: dict[tuple[str, ...], list[BenchmarkRow]] = {}
    for row in rows:
        signature = tuple(sorted(row.protein_accessions)) or (f"pdb:{row.pdb_id}",)
        groups.setdefault(signature, []).append(row)
    return sorted(groups.values(), key=lambda items: (-len(items), items[0].pdb_id))


def _group_rows_by_protein_ligand_pair_signature(rows: list[BenchmarkRow]) -> list[list[BenchmarkRow]]:
    groups: dict[str, list[BenchmarkRow]] = {}
    for row in rows:
        signature = _protein_ligand_pair_signature(row)
        groups.setdefault(signature, []).append(row)
    return sorted(groups.values(), key=lambda items: (-len(items), items[0].pdb_id))


def _group_rows_by_uniref_signature(rows: list[BenchmarkRow]) -> list[list[BenchmarkRow]]:
    groups: dict[str, list[BenchmarkRow]] = {}
    for row in rows:
        cluster = _clean_text(row.metadata.get("UniRef Cluster"))
        signature = cluster or f"fallback::{_protein_accession_signature(row) or row.pdb_id}"
        groups.setdefault(signature, []).append(row)
    return sorted(groups.values(), key=lambda items: (-len(items), items[0].pdb_id))


def _group_rows_by_source_dataset(rows: list[BenchmarkRow]) -> list[list[BenchmarkRow]]:
    groups: dict[str, list[BenchmarkRow]] = {}
    for row in rows:
        source_name = _clean_text(row.metadata.get("Source Family")) or row.source_dataset or "unknown"
        groups.setdefault(source_name, []).append(row)
    return sorted(groups.values(), key=lambda items: (-len(items), items[0].pdb_id))


def _assign_split_name(
    component_size: int,
    split_targets: dict[str, int],
    split_rows: dict[str, list[BenchmarkRow]],
) -> str:
    best_name = "train"
    best_score = None
    for name in ("train", "val", "test"):
        target = max(split_targets[name], 1)
        current = len(split_rows[name])
        projected = (current + component_size) / target
        score = (projected, current)
        if best_score is None or score < best_score:
            best_name = name
            best_score = score
    return best_name


def _compile_split_rows(
    rows: list[BenchmarkRow],
    split_plan: SplitPlanSpec,
    *,
    label_type: str = "delta_G",
) -> tuple[dict[str, list[BenchmarkRow]], dict[str, Any]]:
    grouping_policy = _clean_text(split_plan.grouping_policy)
    if grouping_policy == "accession_grouped":
        components = _group_rows_by_exact_accession_signature(rows)
    elif grouping_policy == "uniref_grouped":
        components = _group_rows_by_uniref_signature(rows)
    elif grouping_policy == "protein_ligand_component_grouped":
        components = _group_rows_by_protein_ligand_pair_signature(rows)
    elif grouping_policy == "paper_faithful_external":
        components = _group_rows_by_source_dataset(rows)
    else:
        components = _group_rows_by_accession_components(rows)
    total = len(rows)
    split_targets = {
        "train": max(1, round(total * split_plan.train_fraction)),
        "val": max(1, round(total * split_plan.val_fraction)) if total >= 10 else 0,
        "test": max(1, round(total * split_plan.test_fraction)),
    }
    split_rows: dict[str, list[BenchmarkRow]] = {"train": [], "val": [], "test": []}
    assignments: list[dict[str, Any]] = []
    for component in components:
        split_name = _assign_split_name(len(component), split_targets, split_rows)
        updated_rows = [_copy_row(item, split=split_name) for item in component]
        split_rows[split_name].extend(updated_rows)
        assignments.append(
            {
                "split": split_name,
                "component_size": len(component),
                "pdb_ids": [item.pdb_id for item in updated_rows],
                "protein_accessions": sorted(
                    {accession for item in updated_rows for accession in item.protein_accessions}
                ),
                "ligand_component_ids": sorted(
                    {
                        _clean_text(item.metadata.get("Ligand Canonical Component Id"))
                        for item in updated_rows
                        if _clean_text(item.metadata.get("Ligand Canonical Component Id"))
                    }
                ),
            }
        )
    if not split_rows["val"] and split_rows["train"]:
        split_rows["val"] = split_rows["train"][-1:]
        split_rows["train"] = split_rows["train"][:-1] or split_rows["val"]
        split_rows["val"] = [_copy_row(item, split="val") for item in split_rows["val"]]
    diagnostics = {
        "status": "ready",
        "grouping_policy": grouping_policy,
        "objective": split_plan.objective,
        "train_count": len(split_rows["train"]),
        "val_count": len(split_rows["val"]),
        "test_count": len(split_rows["test"]),
        "component_count": len(components),
        "assignments": assignments,
        "source_mix_by_split": {
            name: _source_breakdown(items) for name, items in split_rows.items()
        },
        "label_bin_mix_by_split": {
            name: _bucket_breakdown(
                items,
                lambda row: _label_bin_name_for_type(_label_value(row, label_type), label_type),
            )
            for name, items in split_rows.items()
        },
        "assay_mix_by_split": {
            name: _bucket_breakdown(
                items,
                lambda row: row.metadata.get("Assay Family") or _measurement_type(row),
            )
            for name, items in split_rows.items()
        },
    }
    if grouping_policy == "uniref_grouped":
        diagnostics["uniref_grouping_diagnostics"] = {
            "total_components": len(components),
            "fallback_component_count": sum(
                1
                for component in components
                if not _clean_text(component[0].metadata.get("UniRef Cluster"))
            ),
            "unique_uniref_clusters": len(
                {
                    _clean_text(row.metadata.get("UniRef Cluster"))
                    for row in rows
                    if _clean_text(row.metadata.get("UniRef Cluster"))
                }
            ),
        }
    if grouping_policy == "paper_faithful_external":
        diagnostics["paper_faithful_external_diagnostics"] = {
            "external_source_components": len(components),
            "held_out_sources": sorted(
                {
                    _clean_text(item.metadata.get("Source Family")) or item.source_dataset
                    for item in split_rows["test"]
                }
            ),
        }
    return split_rows, diagnostics


def _source_breakdown(rows: list[BenchmarkRow]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for row in rows:
        breakdown[row.source_dataset] = breakdown.get(row.source_dataset, 0) + 1
    return breakdown


def _diagnostics_from_rows(
    report_id: str,
    rows: list[BenchmarkRow],
    split_rows: dict[str, list[BenchmarkRow]] | None = None,
    dropped: list[str] | None = None,
    *,
    label_type: str = "delta_G",
    source_rows: list[BenchmarkRow] | None = None,
) -> TrainingSetDiagnosticsReport:
    label_values = [
        value
        for row in rows
        if (value := _label_value(row, label_type)) == value
    ]
    missing_structure_count = sum(1 for row in rows if not row.structure_file.exists())
    structure_coverage = 0.0 if not rows else 1.0 - (missing_structure_count / len(rows))
    items: list[RecommendationItem] = []
    blockers: list[str] = []
    drop_summary = _drop_reason_summary(source_rows or rows, dropped or [])
    if not rows:
        blockers.append("No candidate rows survived the current training-set request filters.")
    if structure_coverage < 0.95:
        items.append(
            RecommendationItem(
                level="warning",
                category="structure_coverage",
                message=(
                    "Candidate study dataset includes missing structures and "
                    "will reduce build fidelity."
                ),
                action="Tighten filters or stay on the frozen release benchmark.",
                related_fields=("training_set_request",),
            )
        )
    if dropped:
        items.append(
            RecommendationItem(
                level="info",
                category="dataset_filters",
                message=(
                    f"{len(dropped)} candidate rows were dropped during dataset filtering "
                    f"across reasons {sorted(drop_summary['drop_reason_breakdown'])}."
                ),
                action=(
                    "Review the dropped-row manifest if the resulting candidate looks too narrow."
                ),
                related_fields=("training_set_request",),
            )
        )
    return TrainingSetDiagnosticsReport(
        report_id=report_id,
        status="blocked" if blockers else "ready",
        row_count=len(rows),
        train_count=len(split_rows["train"]) if split_rows else 0,
        val_count=len(split_rows["val"]) if split_rows else 0,
        test_count=len(split_rows["test"]) if split_rows else 0,
        structure_coverage=structure_coverage,
        missing_structure_count=missing_structure_count,
        label_min=min(label_values) if label_values else None,
        label_max=max(label_values) if label_values else None,
        label_mean=statistics.fmean(label_values) if label_values else None,
        leakage_risk="component_grouped" if split_rows else "candidate_only",
        source_breakdown=_source_breakdown(rows),
        drop_reason_breakdown=drop_summary["drop_reason_breakdown"],
        drop_source_breakdown=drop_summary["drop_source_breakdown"],
        missing_structure_rate=drop_summary["missing_structure_rate"],
        resolution_filter_rate=drop_summary["resolution_filter_rate"],
        items=tuple(items),
        blockers=tuple(blockers),
        notes=("Generated by the Studio training-set diagnostics builder.",),
    )


def _distribution_bins(values: list[float], *, bucket_count: int = 8) -> list[dict[str, Any]]:
    if not values:
        return []
    minimum = min(values)
    maximum = max(values)
    if math.isclose(minimum, maximum):
        return [{"label": f"{minimum:.2f}", "count": len(values), "min": minimum, "max": maximum}]
    width = (maximum - minimum) / bucket_count
    bins = [0 for _ in range(bucket_count)]
    for value in values:
        index = min(int((value - minimum) / width), bucket_count - 1)
        bins[index] += 1
    payload: list[dict[str, Any]] = []
    for index, count in enumerate(bins):
        start = minimum + (index * width)
        end = start + width
        payload.append(
            {
                "label": f"{start:.2f} to {end:.2f}",
                "count": count,
                "min": round(start, 4),
                "max": round(end, 4),
            }
        )
    return payload


def _split_distribution_payload(split_rows: dict[str, list[BenchmarkRow]]) -> list[dict[str, Any]]:
    return [
        {"split": split_name, "count": len(rows)}
        for split_name, rows in split_rows.items()
    ]


def _row_payloads(rows: list[BenchmarkRow], *, inclusion_reason: str) -> list[dict[str, Any]]:
    return [
        _row_preview_payload(
            row,
            inclusion_reason=inclusion_reason,
            split=row.split,
        )
        for row in rows
    ]


def _dataset_chart_payload(
    rows: list[BenchmarkRow],
    split_rows: dict[str, list[BenchmarkRow]],
    *,
    label_type: str = "delta_G",
) -> dict[str, Any]:
    label_values = [
        value
        for row in rows
        if (value := _label_value(row, label_type)) == value
    ]
    return {
        "label_distribution": _distribution_bins(label_values),
        "split_distribution": _split_distribution_payload(split_rows),
        "source_distribution": [
            {"source_family": key, "count": value}
            for key, value in sorted(_source_breakdown(rows).items())
        ],
        "structure_distribution": [
            {
                "label": "available",
                "count": sum(1 for row in rows if row.structure_file.exists()),
            },
            {
                "label": "missing",
                "count": sum(1 for row in rows if not row.structure_file.exists()),
            },
        ],
    }


def preview_training_set_request(
    request: TrainingSetRequestSpec,
    split_plan: SplitPlanSpec,
    *,
    fallback_dataset_refs: tuple[str, ...] = (),
) -> dict[str, Any]:
    dataset_lookup = _dataset_lookup()
    resolved_refs = [
        ref
        for ref in _resolve_request_dataset_refs(request, fallback_dataset_refs)
        if ref in dataset_lookup
    ]
    rows: list[BenchmarkRow] = []
    for ref in resolved_refs:
        train_rows, val_rows, test_rows = _load_dataset_rows(dataset_lookup[ref])
        rows.extend((*train_rows, *val_rows, *test_rows))
    filtered_rows, dropped = _filter_candidate_rows(rows, request)
    candidate_rows, balance_report = _balance_candidate_rows(filtered_rows, request)
    split_rows, split_diagnostics = _compile_split_rows(
        candidate_rows,
        split_plan,
        label_type=request.label_type,
    )
    diagnostics = _diagnostics_from_rows(
        f"training-set-diagnostics:{request.request_id}",
        candidate_rows,
        split_rows=split_rows,
        dropped=dropped,
        label_type=request.label_type,
        source_rows=rows,
    )
    preview_rows = [
        _row_preview_payload(
            row,
            inclusion_reason="Matched current released study-builder filters.",
            split=_split_name,
            label_type=request.label_type,
        )
        for _split_name in ("train", "val", "test")
        for row in split_rows[_split_name]
    ]
    drop_summary = _drop_reason_summary(rows, dropped)
    requested_target_size = balance_report.get("requested_target_size")
    eligible_quality_ceiling = balance_report.get("eligible_quality_ceiling", len(candidate_rows))
    resolved_target_cap = balance_report.get("resolved_target_cap", len(candidate_rows))
    final_selected_count = balance_report.get("final_selected_count", len(candidate_rows))
    target_size_warning = balance_report.get("target_size_warning")
    return {
        "training_set_request": request.to_dict(),
        "resolved_dataset_refs": resolved_refs,
        "candidate_preview": {
            "row_count": len(candidate_rows),
            "total_candidate_count": len(rows),
            "filtered_candidate_count": len(filtered_rows),
            "eligible_quality_ceiling": eligible_quality_ceiling,
            "requested_target_size": requested_target_size,
            "resolved_target_cap": resolved_target_cap,
            "final_selected_count": final_selected_count,
            "target_size_warning": target_size_warning,
            "sample_pdb_ids": [row.pdb_id for row in candidate_rows[:12]],
            "dropped_rows": dropped,
            "drop_reason_breakdown": drop_summary["drop_reason_breakdown"],
            "drop_source_breakdown": drop_summary["drop_source_breakdown"],
            "missing_structure_rate": drop_summary["missing_structure_rate"],
            "resolution_filter_rate": drop_summary["resolution_filter_rate"],
            "source_breakdown": _source_breakdown(candidate_rows),
            "balancing": balance_report,
            "rows": preview_rows,
            "pagination": {
                "page_size": 25,
                "page_count": max(1, math.ceil(len(preview_rows) / 25)) if preview_rows else 0,
            },
        },
        "split_preview": split_diagnostics,
        "diagnostics": diagnostics.to_dict(),
        "charts": _dataset_chart_payload(candidate_rows, split_rows, label_type=request.label_type),
    }


def build_training_set(
    pipeline_id: str,
    study_title: str,
    request: TrainingSetRequestSpec,
    split_plan: SplitPlanSpec,
    *,
    fallback_dataset_refs: tuple[str, ...] = (),
) -> dict[str, Any]:
    preview = preview_training_set_request(
        request,
        split_plan,
        fallback_dataset_refs=fallback_dataset_refs,
    )
    resolved_refs = preview["resolved_dataset_refs"]
    dataset_lookup = _dataset_lookup()
    rows: list[BenchmarkRow] = []
    for ref in resolved_refs:
        train_rows, val_rows, test_rows = _load_dataset_rows(dataset_lookup[ref])
        rows.extend((*train_rows, *val_rows, *test_rows))
    filtered_rows, dropped = _filter_candidate_rows(rows, request)
    candidate_rows, balance_report = _balance_candidate_rows(filtered_rows, request)
    split_rows, split_diagnostics = _compile_split_rows(
        candidate_rows,
        split_plan,
        label_type=request.label_type,
    )
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
    build_id = f"training-set-build-{stamp}-{uuid4().hex[:6]}"
    build_dir = TRAINING_SET_BUILD_DIR / build_id
    build_dir.mkdir(parents=True, exist_ok=False)
    train_csv = build_dir / "train.csv"
    val_csv = build_dir / "val.csv"
    test_csv = build_dir / "test.csv"

    _write_benchmark_rows(train_csv, split_rows["train"])
    _write_benchmark_rows(val_csv, split_rows["val"])
    _write_benchmark_rows(test_csv, split_rows["test"])
    dropped_manifest = build_dir / "dropped_rows.json"
    _save_json(dropped_manifest, {"items": dropped})
    diagnostics = _diagnostics_from_rows(
        f"training-set-diagnostics:{build_id}",
        candidate_rows,
        split_rows=split_rows,
        dropped=dropped,
        label_type=request.label_type,
        source_rows=rows,
    )
    dataset_ref = f"study_build:{build_id}"
    manifest = {
        "build_id": build_id,
        "pipeline_id": pipeline_id,
        "study_title": study_title,
        "dataset_ref": dataset_ref,
        "label": f"{study_title} training-set build",
        "task_type": request.task_type,
        "label_type": request.label_type,
        "split_strategy": split_plan.objective,
        "maturity": ("pilot_candidate" if diagnostics.status == "ready" else "blocked_candidate"),
        "train_csv": str(train_csv),
        "val_csv": str(val_csv),
        "test_csv": str(test_csv),
        "row_count": len(candidate_rows),
        "total_candidate_count": len(rows),
        "filtered_candidate_count": len(filtered_rows),
        "eligible_quality_ceiling": balance_report.get("eligible_quality_ceiling", len(candidate_rows)),
        "requested_target_size": balance_report.get("requested_target_size"),
        "resolved_target_cap": balance_report.get("resolved_target_cap", len(candidate_rows)),
        "final_selected_count": balance_report.get("final_selected_count", len(candidate_rows)),
        "target_size_warning": balance_report.get("target_size_warning"),
        "source_refs": resolved_refs,
        "source_manifest": str(build_dir / "build_manifest.json"),
        "tags": ["study-build", "ppi", "structure-backed"],
        "balancing": balance_report,
        "dropped_row_manifest": str(dropped_manifest),
        "split_preview": split_diagnostics,
        "diagnostics": diagnostics.to_dict(),
        "selected_rows": [
            _row_preview_payload(
                row,
                inclusion_reason="Selected into the built study dataset.",
                split=row.split,
                label_type=request.label_type,
            )
            for split_name in ("train", "val", "test")
            for row in split_rows[split_name]
        ],
        "selected_rows_preview": [
            _row_preview_payload(
                row,
                inclusion_reason="Selected into the built study dataset.",
                split=row.split,
                label_type=request.label_type,
            )
            for split_name in ("train", "val", "test")
            for row in split_rows[split_name][:10]
        ],
        "excluded_rows": dropped,
        "excluded_rows_preview": dropped[:24],
        "charts": _dataset_chart_payload(candidate_rows, split_rows, label_type=request.label_type),
        "created_at": _utc_now(),
    }
    _save_json(build_dir / "build_manifest.json", manifest)
    # A new training-set build is now visible -- ensure ``/training-set-builds``
    # and any pool/promotion cache reflects it on the next request.
    invalidate_runtime_caches()
    return manifest


def list_training_set_builds() -> list[dict[str, Any]]:
    if not TRAINING_SET_BUILD_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for manifest_path in sorted(TRAINING_SET_BUILD_DIR.glob("*/build_manifest.json"), reverse=True):
        items.append(_load_json(manifest_path, {}))
    return items


def load_training_set_build(build_id: str) -> dict[str, Any]:
    if not build_id or not _RUN_ID_PATTERN.match(build_id):
        raise FileNotFoundError(build_id)
    candidate = (TRAINING_SET_BUILD_DIR / build_id / "build_manifest.json").resolve()
    base = TRAINING_SET_BUILD_DIR.resolve()
    if not candidate.is_relative_to(base):
        raise FileNotFoundError(build_id)
    manifest = _load_json(candidate, None)
    if manifest is None:
        raise FileNotFoundError(build_id)
    return manifest


# PDB / mmCIF parsers live in ``_structures.py``; re-export under the
# historical underscore-prefixed names so existing call sites keep
# working without churn.
from api.model_studio._structures import (  # noqa: E402
    iter_structure_atoms as _iter_structure_atoms,
)


def _parse_structure(
    row: BenchmarkRow,
    graph_recipe: GraphRecipeSpec,
    preprocess_plan: PreprocessPlanSpec,
) -> dict[str, Any]:
    residues: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    atoms_by_residue: dict[str, list[AtomRecord]] = {}
    atom_records: list[AtomRecord] = []
    water_coords: list[tuple[float, float, float]] = []
    atom_count = 0
    hetero_count = 0
    if not row.structure_file.exists():
        return {
            "structure_found": False,
            "atom_count": 0,
            "hetero_count": 0,
            "water_count": 0,
            "residue_count": 0,
            "interface_residues": [],
            "shell_residues": [],
            "graph_nodes": [],
            "graph_edges": [],
            "global_features": {},
            "graph_node_granularity": graph_recipe.node_granularity,
            "sequence_embedding": None,
        }

    ligand = set(row.ligand_chains)
    receptor = set(row.receptor_chains)
    unresolved_partner_roles = not ligand and not receptor
    for atom in _iter_structure_atoms(row.structure_file):
        atom_count += 1
        record_type = atom["record_type"]
        resname = atom["resname"]
        chain_id = atom["chain_id"]
        resseq = atom["resseq"]
        icode = atom["icode"]
        coord = atom["coord"]
        if resname in {"HOH", "WAT"}:
            water_coords.append(coord)
            continue
        if record_type == "HETATM":
            hetero_count += 1
        atom_name = atom["atom_name"]
        element = atom["element"]
        key = (chain_id, resseq, icode, resname)
        bucket = residues.setdefault(
            key,
            {
                "chain_id": chain_id,
                "resname": resname,
                "coords": [],
                "partner": (
                    "ligand"
                    if chain_id in ligand
                    else "receptor"
                    if chain_id in receptor
                    else "unresolved"
                    if unresolved_partner_roles
                    else "other"
                ),
            },
        )
        bucket["coords"].append(coord)
        residue_id = f"{chain_id}:{resseq}:{icode}:{resname}"
        atom_record = AtomRecord(
            atom_id=f"{residue_id}:{atom_name}:{len(bucket['coords'])}",
            residue_id=residue_id,
            atom_name=atom_name,
            element=element,
            chain_id=chain_id,
            partner=bucket["partner"],
            coord=coord,
        )
        atom_records.append(atom_record)
        atoms_by_residue.setdefault(residue_id, []).append(atom_record)

    residue_records: list[ResidueRecord] = []
    for chain_id, resseq, icode, resname in sorted(residues):
        bucket = residues[(chain_id, resseq, icode, resname)]
        coord = _mean_coordinate(bucket["coords"])
        residue_records.append(
            ResidueRecord(
                residue_id=f"{chain_id}:{resseq}:{icode}:{resname}",
                chain_id=chain_id,
                resname=resname,
                coord=coord,
                atom_count=len(bucket["coords"]),
                partner=bucket["partner"],
            )
        )

    ligand_res = [item for item in residue_records if item.partner == "ligand"]
    receptor_res = [item for item in residue_records if item.partner == "receptor"]
    interface_ids: set[str] = set()
    contact_pairs = 0
    salt_bridge_count = 0
    hbond_proxy_count = 0
    residue_contact_counts: dict[str, int] = {}
    for left in ligand_res:
        for right in receptor_res:
            dist = _distance(left.coord, right.coord)
            if dist <= 10.0:
                contact_pairs += 1
                interface_ids.add(left.residue_id)
                interface_ids.add(right.residue_id)
                residue_contact_counts[left.residue_id] = (
                    residue_contact_counts.get(left.residue_id, 0) + 1
                )
                residue_contact_counts[right.residue_id] = (
                    residue_contact_counts.get(right.residue_id, 0) + 1
                )
                is_salt_bridge = (left.resname in ACIDIC and right.resname in BASIC) or (
                    left.resname in BASIC and right.resname in ACIDIC
                )
                if is_salt_bridge and dist <= 6.0:
                    salt_bridge_count += 1
                left_is_hbond_like = (
                    left.resname in POLAR or left.resname in BASIC or left.resname in ACIDIC
                )
                right_is_hbond_like = (
                    right.resname in POLAR or right.resname in BASIC or right.resname in ACIDIC
                )
                if left_is_hbond_like and right_is_hbond_like and dist <= 4.5:
                    hbond_proxy_count += 1

    interface_residues = [item for item in residue_records if item.residue_id in interface_ids]
    shell_distance = float(preprocess_plan.shell_distance_angstroms or 6.0)
    shell_ids: set[str] = set()
    if graph_recipe.include_contact_shell or graph_recipe.region_policy == "interface_plus_shell":
        for residue in residue_records:
            if residue.residue_id in interface_ids:
                continue
            if any(
                _distance(residue.coord, anchor.coord) <= shell_distance
                for anchor in interface_residues
            ):
                shell_ids.add(residue.residue_id)

    selected_residues: list[ResidueRecord]
    if (
        graph_recipe.graph_kind in {"residue_graph", "whole_complex_graph"}
        or graph_recipe.region_policy == "whole_molecule"
    ):
        selected_residues = (
            list(residue_records)
            if unresolved_partner_roles
            else [item for item in residue_records if item.partner in {"ligand", "receptor"}]
        )
    elif graph_recipe.graph_kind == "shell_graph":
        selected_residues = [item for item in residue_records if item.residue_id in shell_ids]
    elif graph_recipe.graph_kind == "hybrid_graph":
        selected_ids = set(interface_ids)
        if (
            graph_recipe.include_contact_shell
            or graph_recipe.region_policy == "interface_plus_shell"
        ):
            selected_ids.update(shell_ids)
        selected_residues = [item for item in residue_records if item.residue_id in selected_ids]
    else:
        selected_residues = interface_residues

    include_waters = graph_recipe.include_waters and "waters" in preprocess_plan.modules
    include_salt_bridges = (
        graph_recipe.include_salt_bridges and "salt bridges" in preprocess_plan.modules
    )
    include_contact_metrics = "hydrogen-bond/contact summaries" in preprocess_plan.modules
    ligand_interface = [item for item in interface_residues if item.partner == "ligand"]
    receptor_interface = [item for item in interface_residues if item.partner == "receptor"]
    water_bridge_proxy_count = 0
    if include_waters and ligand_interface and receptor_interface:
        for water in water_coords:
            ligand_touch = any(_distance(item.coord, water) <= 3.8 for item in ligand_interface)
            receptor_touch = any(
                _distance(item.coord, water) <= 3.8 for item in receptor_interface
            )
            if ligand_touch and receptor_touch:
                water_bridge_proxy_count += 1
    for residue in selected_residues:
        residue.water_contact = include_waters and any(
            _distance(residue.coord, water) <= 4.0 for water in water_coords
        )
    for atom in atom_records:
        atom.water_contact = include_waters and any(
            _distance(atom.coord, water) <= 3.5 for water in water_coords
        )

    graph_nodes: list[dict[str, Any]] = []
    graph_edges: list[dict[str, Any]] = []
    selected_residue_ids = {item.residue_id for item in selected_residues}
    if graph_recipe.graph_kind == "atom_graph" or graph_recipe.node_granularity == "atom":
        selected_atoms = [
            atom for atom in atom_records if atom.residue_id in selected_residue_ids
        ]
        for index, atom in enumerate(selected_atoms):
            element_bucket = atom.element
            if element_bucket not in ELEMENT_ORDER:
                if element_bucket in {"F", "CL", "BR", "I"}:
                    element_bucket = "HALOGEN"
                elif element_bucket in {"ZN", "MG", "CA", "MN", "FE", "CU", "CO", "NI"}:
                    element_bucket = "METAL"
                else:
                    element_bucket = "OTHER"
            element_one_hot = [1.0 if element_bucket == item else 0.0 for item in ELEMENT_ORDER]
            role_scalar = 1.0 if atom.partner == "ligand" else -1.0 if atom.partner == "receptor" else 0.0
            partner_flags = [
                1.0 if atom.partner == "ligand" else 0.0,
                1.0 if atom.partner == "receptor" else 0.0,
                1.0 if atom.water_contact else 0.0,
                float(atom.residue_id in interface_ids),
                float(residue_contact_counts.get(atom.residue_id, 0)),
            ]
            if graph_recipe.partner_awareness == "role_conditioned":
                partner_encoding = [partner_flags[0], partner_flags[1], role_scalar, *partner_flags[2:]]
            elif graph_recipe.partner_awareness == "symmetric":
                partner_encoding = partner_flags[2:]
            else:
                partner_encoding = partner_flags
            if graph_recipe.encoding_policy == "ordinal_ranked":
                feature_vector = [
                    float(ELEMENT_ORDER.index(element_bucket) + 1),
                    role_scalar,
                    float(atom.water_contact),
                    float(atom.residue_id in interface_ids),
                    float(residue_contact_counts.get(atom.residue_id, 0)),
                ]
            else:
                feature_vector = element_one_hot + partner_encoding
            graph_nodes.append(
                {
                    "node_id": atom.atom_id,
                    "node_index": index,
                    "partner": atom.partner,
                    "resname": atom.residue_id.split(":")[-1],
                    "atom_name": atom.atom_name,
                    "element": atom.element,
                    "residue_id": atom.residue_id,
                    "coord": list(atom.coord),
                    "features": feature_vector,
                }
            )
        if not graph_nodes:
            fallback_feature_dim = len(ELEMENT_ORDER) + 5
            graph_nodes.append(
                {
                    "node_id": "dummy:0",
                    "node_index": 0,
                    "partner": "other",
                    "resname": "UNK",
                    "atom_name": "UNK",
                    "element": "OTHER",
                    "residue_id": "dummy",
                    "coord": [0.0, 0.0, 0.0],
                    "features": [0.0] * fallback_feature_dim,
                }
            )
        for left_index, left in enumerate(selected_atoms):
            for right_index in range(left_index + 1, len(selected_atoms)):
                right = selected_atoms[right_index]
                dist = _distance(left.coord, right.coord)
                if dist <= 4.5:
                    graph_edges.append(
                        {
                            "source": left_index,
                            "target": right_index,
                            "distance": dist,
                            "cross_partner": left.partner != right.partner,
                            "same_residue": left.residue_id == right.residue_id,
                        }
                    )
    else:
        for index, residue in enumerate(selected_residues):
            residue_one_hot = [1.0 if residue.resname == aa else 0.0 for aa in AA_ORDER]
            ordinal_rank = (
                float(AA_ORDER.index(residue.resname) + 1)
                if residue.resname in AA_ORDER
                else 0.0
            )
            partner_flags = [
                1.0 if residue.partner == "ligand" else 0.0,
                1.0 if residue.partner == "receptor" else 0.0,
                1.0 if residue.resname in HYDROPHOBIC else 0.0,
                1.0 if residue.resname in POLAR else 0.0,
                1.0 if residue.resname in ACIDIC else 0.0,
                1.0 if residue.resname in BASIC else 0.0,
                1.0 if residue.resname in AROMATIC else 0.0,
                1.0 if residue.water_contact else 0.0,
                float(residue_contact_counts.get(residue.residue_id, 0)),
                float(residue.atom_count),
            ]
            symmetric_flags = partner_flags[2:]
            role_scalar = (
                1.0
                if residue.partner == "ligand"
                else -1.0
                if residue.partner == "receptor"
                else 0.0
            )
            role_conditioned_flags = [
                partner_flags[0],
                partner_flags[1],
                role_scalar,
                float(residue_contact_counts.get(residue.residue_id, 0))
                / max(float(contact_pairs), 1.0),
                1.0 if residue.residue_id in interface_ids else 0.0,
                *symmetric_flags,
            ]
            if graph_recipe.partner_awareness == "symmetric":
                partner_encoding = symmetric_flags
            elif graph_recipe.partner_awareness == "role_conditioned":
                partner_encoding = role_conditioned_flags
            else:
                partner_encoding = partner_flags
            feature_vector = residue_one_hot + partner_encoding
            if graph_recipe.encoding_policy == "one_hot":
                feature_vector = residue_one_hot + partner_encoding
            elif graph_recipe.encoding_policy == "ordinal_ranked":
                if graph_recipe.partner_awareness == "role_conditioned":
                    ordinal_partner_encoding = [role_scalar, float(residue.partner == "other")]
                elif graph_recipe.partner_awareness == "symmetric":
                    ordinal_partner_encoding = [0.0, 0.0, 0.0]
                else:
                    ordinal_partner_encoding = [
                        float(residue.partner == "ligand"),
                        float(residue.partner == "receptor"),
                        float(residue.partner == "other"),
                    ]
                feature_vector = [
                    ordinal_rank,
                    *ordinal_partner_encoding,
                    float(residue.resname in HYDROPHOBIC),
                    float(residue.resname in POLAR),
                    float(residue.resname in ACIDIC),
                    float(residue.resname in BASIC),
                    float(residue.water_contact),
                    float(residue_contact_counts.get(residue.residue_id, 0)),
                    float(residue.atom_count),
                ]
            graph_nodes.append(
                {
                    "node_id": residue.residue_id,
                    "node_index": index,
                    "partner": residue.partner,
                    "resname": residue.resname,
                    "coord": list(residue.coord),
                    "features": feature_vector,
                }
            )
        if not graph_nodes:
            fallback_feature_dim = (
                (len(AA_ORDER) + 10)
                if graph_recipe.encoding_policy != "ordinal_ranked"
                else 11
            )
            graph_nodes.append(
                {
                    "node_id": "dummy:0",
                    "node_index": 0,
                    "partner": "other",
                    "resname": "UNK",
                    "coord": [0.0, 0.0, 0.0],
                    "features": [0.0] * fallback_feature_dim,
                }
            )
        for left_index, left in enumerate(selected_residues):
            for right_index in range(left_index + 1, len(selected_residues)):
                right = selected_residues[right_index]
                dist = _distance(left.coord, right.coord)
                if dist <= 8.5:
                    graph_edges.append(
                        {
                            "source": left_index,
                            "target": right_index,
                            "distance": dist,
                            "cross_partner": left.partner != right.partner,
                        }
                    )

    geometry_enabled = "pocket/interface geometry" in preprocess_plan.modules
    interface_total = max(len(interface_residues), 1)
    interface_hydrophobic = sum(1 for item in interface_residues if item.resname in HYDROPHOBIC)
    interface_polar = sum(1 for item in interface_residues if item.resname in POLAR)
    interface_acidic = sum(1 for item in interface_residues if item.resname in ACIDIC)
    interface_basic = sum(1 for item in interface_residues if item.resname in BASIC)
    interface_aromatic = sum(1 for item in interface_residues if item.resname in AROMATIC)
    selected_water_contact_count = sum(1 for item in selected_residues if item.water_contact)
    interface_water_contact_count = sum(1 for item in interface_residues if item.water_contact)
    selected_atom_count = len(
        [atom for atom in atom_records if atom.residue_id in selected_residue_ids]
    )
    interface_atom_count = len(
        [atom for atom in atom_records if atom.residue_id in interface_ids]
    )
    global_features = {
        "atom_count": atom_count,
        "hetero_count": hetero_count,
        "residue_count": len(residue_records),
        "ligand_chain_count": len(ligand),
        "receptor_chain_count": len(receptor),
        "ligand_residue_count": len(ligand_res),
        "receptor_residue_count": len(receptor_res),
        "partner_role_resolution_mode": (
            "unresolved_whole_complex_only" if unresolved_partner_roles else "native_partner_roles"
        ),
        "unresolved_partner_chain_count": (
            len({item.chain_id for item in residue_records}) if unresolved_partner_roles else 0
        ),
        "interface_residue_count": len(interface_residues),
        "water_count": len(water_coords) if include_waters else 0,
        "shell_residue_count": len(shell_ids),
        "selected_residue_count": len(selected_residues),
        "contact_pair_count": contact_pairs,
        "salt_bridge_count": salt_bridge_count if include_salt_bridges else 0,
        "hbond_proxy_count": hbond_proxy_count if include_contact_metrics else 0,
        "resolution": row.resolution or 0.0,
        "release_year": row.release_year,
        "protein_accession_count": len(row.protein_accessions),
        "graph_kind": graph_recipe.graph_kind,
        "graph_node_granularity": graph_recipe.node_granularity,
        "region_policy": graph_recipe.region_policy,
        "selected_atom_count": selected_atom_count,
        "interface_atom_count": interface_atom_count,
        "interface_density_proxy": (
            round(contact_pairs / max(len(interface_residues), 1), 4) if geometry_enabled else 0.0
        ),
        "pocket_depth_proxy": (
            round(len(shell_ids) / max(len(interface_residues), 1), 4) if geometry_enabled else 0.0
        ),
        "selected_water_contact_count": selected_water_contact_count if include_waters else 0,
        "interface_water_contact_count": interface_water_contact_count if include_waters else 0,
        "water_bridge_proxy_count": water_bridge_proxy_count if include_waters else 0,
        "interface_hydrophobic_fraction": round(interface_hydrophobic / interface_total, 4),
        "interface_polar_fraction": round(interface_polar / interface_total, 4),
        "interface_acidic_fraction": round(interface_acidic / interface_total, 4),
        "interface_basic_fraction": round(interface_basic / interface_total, 4),
        "interface_aromatic_fraction": round(interface_aromatic / interface_total, 4),
        "interface_charge_balance": round(
            (interface_basic - interface_acidic) / interface_total,
            4,
        ),
        "cross_partner_contact_ratio": round(
            contact_pairs / max(len(selected_residues), 1),
            4,
        ),
    }
    return {
        "structure_found": True,
        "atom_count": atom_count,
        "hetero_count": hetero_count,
        "water_count": len(water_coords) if include_waters else 0,
        "residue_count": len(residue_records),
        "interface_residues": [item.residue_id for item in interface_residues],
        "shell_residues": sorted(shell_ids),
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "global_features": global_features,
        "graph_node_granularity": graph_recipe.node_granularity,
        "sequence_embedding": None,
    }


def _tabular_vector(
    row: BenchmarkRow,
    parsed: dict[str, Any],
    feature_recipe: FeatureRecipeSpec,
) -> list[float]:
    global_features = parsed["global_features"]
    vector: list[float] = []
    for bundle in feature_recipe.global_feature_sets:
        if bundle == "assay_globals":
            vector.extend(
                [
                    float(row.temperature_k),
                    float(global_features.get("protein_accession_count", 0)),
                ]
            )
        elif bundle == "structure_quality":
            vector.extend(
                [
                    float(global_features.get("atom_count", 0)),
                    float(global_features.get("hetero_count", 0)),
                    float(global_features.get("resolution", 0.0)),
                    float(global_features.get("release_year", 0)),
                ]
            )
        elif bundle == "interface_composition":
            vector.extend(
                [
                    float(global_features.get("residue_count", 0)),
                    float(global_features.get("ligand_chain_count", 0)),
                    float(global_features.get("receptor_chain_count", 0)),
                    float(global_features.get("ligand_residue_count", 0)),
                    float(global_features.get("receptor_residue_count", 0)),
                    float(global_features.get("interface_residue_count", 0)),
                    float(global_features.get("contact_pair_count", 0)),
                    float(global_features.get("salt_bridge_count", 0)),
                    float(global_features.get("hbond_proxy_count", 0)),
                    float(global_features.get("water_count", 0)),
                    float(global_features.get("shell_residue_count", 0)),
                ]
            )
        elif bundle == "interface_chemistry":
            vector.extend(
                [
                    float(global_features.get("interface_density_proxy", 0.0)),
                    float(global_features.get("pocket_depth_proxy", 0.0)),
                    float(global_features.get("interface_hydrophobic_fraction", 0.0)),
                    float(global_features.get("interface_polar_fraction", 0.0)),
                    float(global_features.get("interface_acidic_fraction", 0.0)),
                    float(global_features.get("interface_basic_fraction", 0.0)),
                    float(global_features.get("interface_aromatic_fraction", 0.0)),
                    float(global_features.get("interface_charge_balance", 0.0)),
                    float(global_features.get("cross_partner_contact_ratio", 0.0)),
                ]
            )
    return vector


def _graph_summary_vector(parsed: dict[str, Any], feature_recipe: FeatureRecipeSpec) -> list[float]:
    node_count = len(parsed["graph_nodes"])
    edge_count = len(parsed["graph_edges"])
    mean_degree = (2.0 * edge_count / node_count) if node_count else 0.0
    cross_partner_edges = sum(1 for edge in parsed["graph_edges"] if edge["cross_partner"])
    summary = [
        float(node_count),
        float(edge_count),
        mean_degree,
        float(cross_partner_edges),
        float(parsed["global_features"].get("salt_bridge_count", 0)),
        float(parsed["global_features"].get("hbond_proxy_count", 0)),
        float(parsed["global_features"].get("water_count", 0)),
        float(parsed["global_features"].get("shell_residue_count", 0)),
    ]
    if "interface_chemistry_maps" in feature_recipe.distributed_feature_sets:
        summary.extend(
            [
                float(parsed["global_features"].get("interface_hydrophobic_fraction", 0.0)),
                float(parsed["global_features"].get("interface_polar_fraction", 0.0)),
                float(parsed["global_features"].get("interface_charge_balance", 0.0)),
            ]
        )
    if "water_network_descriptors" in feature_recipe.distributed_feature_sets:
        summary.extend(
            [
                float(parsed["global_features"].get("water_bridge_proxy_count", 0.0)),
                float(parsed["global_features"].get("interface_water_contact_count", 0.0)),
            ]
        )
    return summary


def _distributed_feature_payload(
    row: BenchmarkRow,
    parsed: dict[str, Any],
    feature_recipe: FeatureRecipeSpec,
    preprocess_plan: PreprocessPlanSpec,
) -> dict[str, Any]:
    global_features = parsed["global_features"]
    payload: dict[str, Any] = {}
    if "residue_contacts" in feature_recipe.distributed_feature_sets:
        payload["residue_contacts"] = {
            "node_feature_count": len(parsed["graph_nodes"]),
            "edge_feature_count": len(parsed["graph_edges"]),
            "interface_residue_count": len(parsed["interface_residues"]),
            "shell_residue_count": len(parsed["shell_residues"]),
            "cross_partner_contact_ratio": float(
                global_features.get("cross_partner_contact_ratio", 0.0)
            ),
        }
    if "interface_geometry" in feature_recipe.distributed_feature_sets:
        payload["interface_geometry"] = {
            "interface_density_proxy": float(
                global_features.get("interface_density_proxy", 0.0)
            ),
            "pocket_depth_proxy": float(global_features.get("pocket_depth_proxy", 0.0)),
            "shell_residue_count": float(global_features.get("shell_residue_count", 0)),
        }
    if "water_context" in feature_recipe.distributed_feature_sets:
        payload["water_context"] = {
            "water_count": float(global_features.get("water_count", 0)),
            "selected_water_contact_count": float(
                global_features.get("selected_water_contact_count", 0)
            ),
            "interface_water_contact_count": float(
                global_features.get("interface_water_contact_count", 0)
            ),
        }
    if "interface_chemistry_maps" in feature_recipe.distributed_feature_sets:
        payload["interface_chemistry_maps"] = {
            "hydrophobic_fraction": float(
                global_features.get("interface_hydrophobic_fraction", 0.0)
            ),
            "polar_fraction": float(global_features.get("interface_polar_fraction", 0.0)),
            "acidic_fraction": float(global_features.get("interface_acidic_fraction", 0.0)),
            "basic_fraction": float(global_features.get("interface_basic_fraction", 0.0)),
            "aromatic_fraction": float(
                global_features.get("interface_aromatic_fraction", 0.0)
            ),
            "charge_balance": float(global_features.get("interface_charge_balance", 0.0)),
        }
    if "water_network_descriptors" in feature_recipe.distributed_feature_sets:
        payload["water_network_descriptors"] = {
            "water_bridge_proxy_count": float(
                global_features.get("water_bridge_proxy_count", 0)
            ),
            "water_bridge_density": float(
                global_features.get("water_bridge_proxy_count", 0)
            )
            / max(float(global_features.get("interface_residue_count", 0)), 1.0),
            "interface_water_ratio": float(
                global_features.get("interface_water_contact_count", 0)
            )
            / max(float(global_features.get("interface_residue_count", 0)), 1.0),
        }
    if (
        "sequence_embeddings" in feature_recipe.distributed_feature_sets
        and "sequence embeddings" in preprocess_plan.modules
    ):
        payload["sequence_embeddings"] = _sequence_embedding_payload(row)
    return payload


def _distributed_feature_vector(payload: dict[str, Any]) -> list[float]:
    vector: list[float] = []
    for group_name in sorted(payload):
        group_payload = payload[group_name]
        if not isinstance(group_payload, dict):
            continue
        for key in sorted(group_payload):
            value = group_payload[key]
            if isinstance(value, (int, float)):
                vector.append(float(value))
            elif isinstance(value, list) and all(isinstance(item, (int, float)) for item in value):
                vector.extend(float(item) for item in value)
    return vector


def _sequence_embedding_payload(row: BenchmarkRow) -> dict[str, Any]:
    seed_text = "|".join(sorted(row.protein_accessions) or [row.pdb_id])
    digest = hashlib.sha256(seed_text.encode("utf-8")).digest()
    values: list[float] = []
    for index in range(12):
        chunk = digest[index * 2 : (index * 2) + 2]
        if len(chunk) < 2:
            chunk = (chunk + digest)[:2]
        raw_value = int.from_bytes(chunk, "big")
        values.append(round((raw_value / 65535.0) * 2.0 - 1.0, 6))
    return {
        "embedding_dim": len(values),
        "values": values,
        "model_identity": {
            "model_name": "studio-sequence-embedder-ppi-beta",
            "model_version": "1",
        },
        "runtime_identity": {
            "runtime_name": "studio-sequence-materializer",
            "runtime_version": "1",
        },
        "cache_identity": f"sequence-embedding:{hashlib.sha1(seed_text.encode('utf-8')).hexdigest()[:12]}",
        "leakage_audit": "required_sequence_grouping_review",
        "source_selection": {
            "protein_accessions": list(row.protein_accessions),
            "pdb_id": row.pdb_id,
        },
    }


def _sequence_embedding_summary(extracted_examples: list[dict[str, Any]]) -> dict[str, Any] | None:
    for item in extracted_examples:
        payload = item.get("sequence_embedding") or {}
        if not payload:
            continue
        return {
            "enabled": True,
            "embedding_dim": int(payload.get("embedding_dim") or 0),
            "model_identity": dict(payload.get("model_identity") or {}),
            "runtime_identity": dict(payload.get("runtime_identity") or {}),
            "cache_identity": _clean_text(payload.get("cache_identity")),
            "leakage_audit": _clean_text(payload.get("leakage_audit")) or "unknown",
        }
    return None


def _rmse(y_true: list[float], y_pred: list[float]) -> float:
    mean_squared_error = _ensure_sklearn()["mean_squared_error"]
    return float(math.sqrt(mean_squared_error(y_true, y_pred)))


# ── Lazy torch.nn.Module / torch.optim.Optimizer subclasses ────────────
#
# These class statements used to live at module level and forced `import
# torch` to complete at module load (a 41-minute cold-start cost on
# Windows). We defer the actual class creation into a factory that runs
# on first use. The module-level names ``_GraphSageRegressor`` and
# ``_LionOptimizer`` are exposed via ``__getattr__`` (PEP 562) so any
# existing call sites keep working unchanged.

_GraphSageRegressor = None  # populated on first access
_LionOptimizer = None       # populated on first access


def _ensure_graph_classes() -> None:
    """Build the deferred torch-derived classes on first use. Called by
    the module-level ``__getattr__`` and by every public function that
    instantiates these classes.
    """
    global _GraphSageRegressor, _LionOptimizer
    if _GraphSageRegressor is not None and _LionOptimizer is not None:
        return

    torch = _ensure_torch()
    _torch_nn = torch.nn
    _torch_optim = torch.optim

    # Capture the resolved torch reference into the class closures so we
    # don't re-import per method call.
    _t = torch

    class _GraphSageRegressorImpl(_torch_nn.Module):
        def __init__(self, input_dim: int, hidden_dim: int = 32) -> None:
            super().__init__()
            self.self_1 = _torch_nn.Linear(input_dim, hidden_dim)
            self.neigh_1 = _torch_nn.Linear(input_dim, hidden_dim)
            self.self_2 = _torch_nn.Linear(hidden_dim, hidden_dim)
            self.neigh_2 = _torch_nn.Linear(hidden_dim, hidden_dim)
            self.head = _torch_nn.Linear(hidden_dim, 1)

        def _aggregate(self, adjacency, values):
            degree = adjacency.sum(dim=1, keepdim=True).clamp(min=1.0)
            return adjacency @ values / degree

        def forward(self, node_features, adjacency):
            neigh_1 = self._aggregate(adjacency, node_features)
            hidden_1 = _t.relu(self.self_1(node_features) + self.neigh_1(neigh_1))
            neigh_2 = self._aggregate(adjacency, hidden_1)
            hidden_2 = _t.relu(self.self_2(hidden_1) + self.neigh_2(neigh_2))
            pooled = hidden_2.mean(dim=0)
            return self.head(pooled).squeeze()

    class _LionOptimizerImpl(_torch_optim.Optimizer):
        def __init__(
            self,
            params: Any,
            *,
            lr: float = 1e-4,
            betas: tuple[float, float] = (0.9, 0.99),
            weight_decay: float = 0.0,
        ) -> None:
            defaults = {"lr": lr, "betas": betas, "weight_decay": weight_decay}
            super().__init__(params, defaults)

        def step(self, closure: Any = None) -> Any:
            loss = None
            if closure is not None:
                with _t.enable_grad():
                    loss = closure()
            for group in self.param_groups:
                beta1, beta2 = group["betas"]
                lr = group["lr"]
                weight_decay = group["weight_decay"]
                for parameter in group["params"]:
                    if parameter.grad is None:
                        continue
                    grad = parameter.grad
                    if grad.is_sparse:
                        raise RuntimeError("Lion does not support sparse gradients.")
                    state = self.state[parameter]
                    if not state:
                        state["exp_avg"] = _t.zeros_like(parameter)
                    exp_avg = state["exp_avg"]
                    if weight_decay:
                        parameter.data.mul_(1 - lr * weight_decay)
                    update = exp_avg.mul(beta1).add(grad, alpha=1 - beta1)
                    parameter.data.add_(_t.sign(update), alpha=-lr)
                    exp_avg.mul_(beta2).add_(grad, alpha=1 - beta2)
            return loss

    _GraphSageRegressor = _GraphSageRegressorImpl
    _LionOptimizer = _LionOptimizerImpl


_SKLEARN_ATTRS = (
    "HistGradientBoostingRegressor",
    "RandomForestRegressor",
    "mean_absolute_error",
    "mean_squared_error",
    "r2_score",
    "MLPRegressor",
)


def __getattr__(name: str):  # PEP 562 — module-level attribute access
    """Lazy resolution for symbols whose eager import would block server
    boot (torch + sklearn family). Anything that does
    ``runtime.HistGradientBoostingRegressor`` or ``runtime.torch.Tensor``
    pays the import cost on first reference, not at module load."""
    if name == "torch":
        m = _ensure_torch()
        globals()["torch"] = m
        return m
    if name in _SKLEARN_ATTRS:
        sym = _ensure_sklearn()[name]
        globals()[name] = sym
        return sym
    if name in ("_GraphSageRegressor", "_LionOptimizer"):
        _ensure_graph_classes()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _select_graph_optimizer(name: str, model) -> tuple[Any, str]:
    torch = _ensure_torch()
    optimizer_name = _clean_text(name) or "adam"
    if optimizer_name == "adamw":
        return torch.optim.AdamW(model.parameters(), lr=0.003, weight_decay=1e-2), "adamw"
    if optimizer_name == "sgd_momentum":
        return torch.optim.SGD(model.parameters(), lr=0.01, momentum=0.9), "sgd_momentum"
    if optimizer_name == "lion":
        _ensure_graph_classes()
        return _LionOptimizer(model.parameters(), lr=0.002, weight_decay=1e-2), "lion"
    return torch.optim.Adam(model.parameters(), lr=0.005), "adam"


def _select_graph_loss(name: str) -> tuple[Any, str]:
    torch = _ensure_torch()
    loss_name = _clean_text(name) or "mse"
    if loss_name == "mae":
        return torch.nn.L1Loss(), "mae"
    if loss_name == "huber":
        return torch.nn.HuberLoss(delta=1.0), "huber"
    return torch.nn.MSELoss(), "mse"


def _graph_accumulation_steps(
    batch_policy: str,
    train_graphs: list[dict[str, Any]],
) -> int:
    policy = _clean_text(batch_policy) or "dynamic_by_graph_size"
    if policy == "fixed_small_batch":
        return 2
    if policy == "fixed_medium_batch":
        return 4
    if policy == "adaptive_gradient_accumulation":
        node_counts = [
            len(example.get("graph", {}).get("nodes", [])) for example in train_graphs
        ] or [1]
        mean_nodes = statistics.fmean(node_counts)
        if mean_nodes >= 80:
            return 6
        if mean_nodes >= 40:
            return 4
        return 3
    return 1


def _train_graph_model(
    train_graphs: list[dict[str, Any]],
    test_graphs: list[dict[str, Any]],
    epoch_budget: int,
    *,
    backend_family: str = "graphsage",
    resolved_backend: str = "torch-graphsage-lite",
    optimizer_name: str = "adam",
    scheduler_name: str = "cosine_decay",
    loss_name: str = "mse",
    batch_policy: str = "dynamic_by_graph_size",
) -> tuple[dict[str, Any], list[float], list[float]]:
    torch = _ensure_torch()
    input_dim = len(train_graphs[0]["graph"]["nodes"][0]["features"])
    _ensure_graph_classes()
    model = _GraphSageRegressor(input_dim=input_dim)
    optimizer, resolved_optimizer = _select_graph_optimizer(optimizer_name, model)
    loss_fn, resolved_loss = _select_graph_loss(loss_name)
    epochs = max(8, min(epoch_budget, 30))
    accumulation_steps = _graph_accumulation_steps(batch_policy, train_graphs)
    training_curve: list[float] = []
    scheduler = None
    scheduler_kind = _clean_text(scheduler_name) or "cosine_decay"
    if scheduler_kind == "warmup_cosine":
        warmup_epochs = max(1, min(epochs // 5, 4))

        def _warmup_cosine_lambda(epoch_index: int) -> float:
            if epoch_index < warmup_epochs:
                return float(epoch_index + 1) / float(warmup_epochs)
            progress = (epoch_index - warmup_epochs) / max(epochs - warmup_epochs, 1)
            return max(0.05, 0.5 * (1.0 + math.cos(math.pi * progress)))

        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, _warmup_cosine_lambda)
    elif scheduler_kind == "one_cycle":
        scheduler = torch.optim.lr_scheduler.OneCycleLR(
            optimizer,
            max_lr=0.01,
            epochs=epochs,
            steps_per_epoch=max(len(train_graphs), 1),
        )
    elif scheduler_kind == "plateau":
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer,
            mode="min",
            factor=0.5,
            patience=2,
        )
    else:
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
            optimizer,
            T_max=max(epochs, 1),
        )
    model.train()
    for _epoch_index in range(epochs):
        optimizer.zero_grad()
        epoch_losses: list[float] = []
        for example_index, example in enumerate(train_graphs, start=1):
            nodes = torch.tensor(
                [node["features"] for node in example["graph"]["nodes"]],
                dtype=torch.float32,
            )
            adjacency = torch.zeros((nodes.shape[0], nodes.shape[0]), dtype=torch.float32)
            for edge in example["graph"]["edges"]:
                    adjacency[edge["source"], edge["target"]] = 1.0
                    adjacency[edge["target"], edge["source"]] = 1.0
            prediction = model(nodes, adjacency)
            target = torch.tensor(example["target"], dtype=torch.float32)
            loss = loss_fn(prediction, target) / max(accumulation_steps, 1)
            loss.backward()
            epoch_losses.append(float(loss.item() * max(accumulation_steps, 1)))
            should_step = (
                example_index % max(accumulation_steps, 1) == 0
                or example_index == len(train_graphs)
            )
            if should_step:
                optimizer.step()
                optimizer.zero_grad()
                if scheduler_kind == "one_cycle" and scheduler is not None:
                    scheduler.step()
        mean_epoch_loss = float(statistics.fmean(epoch_losses)) if epoch_losses else 0.0
        training_curve.append(mean_epoch_loss)
        if scheduler_kind == "plateau" and scheduler is not None:
            scheduler.step(mean_epoch_loss)
        elif scheduler_kind not in {"plateau", "one_cycle"} and scheduler is not None:
            scheduler.step()
    model.eval()

    def predict(graph_examples: list[dict[str, Any]]) -> list[float]:
        values: list[float] = []
        with torch.no_grad():
            for example in graph_examples:
                nodes = torch.tensor(
                    [node["features"] for node in example["graph"]["nodes"]],
                    dtype=torch.float32,
                )
                adjacency = torch.zeros((nodes.shape[0], nodes.shape[0]), dtype=torch.float32)
                for edge in example["graph"]["edges"]:
                    adjacency[edge["source"], edge["target"]] = 1.0
                    adjacency[edge["target"], edge["source"]] = 1.0
                values.append(float(model(nodes, adjacency).item()))
        return values

    train_pred = predict(train_graphs)
    test_pred = predict(test_graphs)
    metrics = {
        "backend_family": backend_family,
        "resolved_backend": resolved_backend,
        "epoch_count": epochs,
        "optimizer": resolved_optimizer,
        "scheduler": scheduler_kind,
        "loss_function": resolved_loss,
        "batch_policy": batch_policy,
        "gradient_accumulation_steps": accumulation_steps,
        "training_curve": training_curve,
    }
    return metrics, train_pred, test_pred


def _train_tabular_model(
    model_family: str,
    x_train: list[list[float]],
    y_train: list[float],
    x_test: list[list[float]],
    epoch_budget: int,
) -> tuple[dict[str, Any], list[float], list[float]]:
    sklearn_syms = _ensure_sklearn()
    HistGradientBoostingRegressor = sklearn_syms["HistGradientBoostingRegressor"]
    RandomForestRegressor = sklearn_syms["RandomForestRegressor"]
    MLPRegressor = sklearn_syms["MLPRegressor"]
    resolved_backend = model_family
    if model_family == "xgboost":
        model = HistGradientBoostingRegressor(
            max_depth=6,
            max_iter=max(50, min(epoch_budget * 4, 250)),
            learning_rate=0.05,
            random_state=7,
        )
        resolved_backend = "sklearn-hist-gradient-boosting-adapter"
    elif model_family == "catboost":
        model = RandomForestRegressor(
            n_estimators=max(100, min(epoch_budget * 6, 400)),
            max_depth=10,
            random_state=7,
        )
        resolved_backend = "sklearn-random-forest-adapter"
    elif model_family == "mlp":
        model = MLPRegressor(
            hidden_layer_sizes=(64, 32),
            max_iter=max(100, min(epoch_budget * 8, 400)),
            random_state=7,
        )
        resolved_backend = "sklearn-mlp-regressor"
    else:
        raise ValueError(f"Unsupported tabular family: {model_family}")
    model.fit(x_train, y_train)
    train_pred = [float(item) for item in model.predict(x_train)]
    test_pred = [float(item) for item in model.predict(x_test)]
    training_curve = []
    if hasattr(model, "loss_curve_"):
        training_curve = [float(item) for item in getattr(model, "loss_curve_", [])]
    return {
        "resolved_backend": resolved_backend,
        "feature_count": len(x_train[0]) if x_train else 0,
        "training_curve": training_curve,
    }, train_pred, test_pred


def _train_late_fusion_ensemble(
    x_train: list[list[float]],
    y_train: list[float],
    x_test: list[list[float]],
    epoch_budget: int,
) -> tuple[dict[str, Any], list[float], list[float]]:
    tree_meta, tree_train, tree_test = _train_tabular_model(
        "xgboost",
        x_train,
        y_train,
        x_test,
        epoch_budget,
    )
    forest_meta, forest_train, forest_test = _train_tabular_model(
        "catboost",
        x_train,
        y_train,
        x_test,
        epoch_budget,
    )
    mlp_meta, mlp_train, mlp_test = _train_tabular_model(
        "mlp",
        x_train,
        y_train,
        x_test,
        epoch_budget,
    )
    train_pred = [
        float(statistics.fmean(values))
        for values in zip(tree_train, forest_train, mlp_train, strict=True)
    ]
    test_pred = [
        float(statistics.fmean(values))
        for values in zip(tree_test, forest_test, mlp_test, strict=True)
    ]
    return {
        "resolved_backend": "local-late-fusion-ensemble-adapter",
        "ensemble_members": [
            tree_meta["resolved_backend"],
            forest_meta["resolved_backend"],
            mlp_meta["resolved_backend"],
        ],
        "feature_count": len(x_train[0]) if x_train else 0,
    }, train_pred, test_pred


def _scale_summary(values: list[float]) -> dict[str, float | None]:
    clean = [float(value) for value in values if value == value]
    if not clean:
        return {"min": None, "max": None, "mean": None, "span": None}
    minimum = min(clean)
    maximum = max(clean)
    return {
        "min": minimum,
        "max": maximum,
        "mean": float(statistics.fmean(clean)),
        "span": maximum - minimum,
    }


def _outlier_mass_summary(outliers: list[dict[str, Any]], total_count: int) -> dict[str, Any]:
    if total_count <= 0:
        return {
            "flagged_count": 0,
            "fraction": 0.0,
            "worst_absolute_error": 0.0,
            "median_absolute_error": 0.0,
        }
    absolute_errors = [float(item.get("absolute_error") or 0.0) for item in outliers]
    return {
        "flagged_count": len(outliers),
        "fraction": round(len(outliers) / total_count, 6),
        "worst_absolute_error": max(absolute_errors) if absolute_errors else 0.0,
        "median_absolute_error": (
            float(statistics.median(absolute_errors)) if absolute_errors else 0.0
        ),
    }


def _quality_summary(
    y_train: list[float],
    y_test: list[float],
    train_pred: list[float],
    test_pred: list[float],
    metrics: dict[str, Any],
    outliers: list[dict[str, Any]],
) -> dict[str, Any]:
    label_scale_expected = _scale_summary([*y_train, *y_test])
    prediction_scale_observed = _scale_summary(test_pred)
    outlier_mass_summary = _outlier_mass_summary(outliers, len(y_test))
    blockers: list[str] = []
    warnings: list[str] = []
    expected_span = float(label_scale_expected.get("span") or 0.0)
    observed_span = float(prediction_scale_observed.get("span") or 0.0)
    expected_mean = label_scale_expected.get("mean")
    observed_mean = prediction_scale_observed.get("mean")
    if expected_span:
        if observed_span > max(expected_span * 4.0, 1.0):
            blockers.append("prediction_scale_mismatch")
        elif observed_span < max(expected_span * 0.1, 1e-6):
            warnings.append("prediction_scale_compressed")
    if expected_mean is not None and observed_mean is not None:
        if abs(float(observed_mean) - float(expected_mean)) > max(expected_span * 2.0, 1.0):
            blockers.append("prediction_mean_shift")
    test_r2 = float(metrics.get("test_r2") or 0.0)
    if test_r2 < -1.0:
        blockers.append("strongly_negative_r2")
    elif test_r2 < 0.0:
        warnings.append("negative_r2")
    if outlier_mass_summary["fraction"] >= 0.2:
        blockers.append("high_outlier_concentration")
    elif outlier_mass_summary["fraction"] >= 0.1:
        warnings.append("elevated_outlier_concentration")
    train_rmse = float(metrics.get("train_rmse") or 0.0)
    test_rmse = float(metrics.get("test_rmse") or 0.0)
    if train_rmse > 0.0 and test_rmse > (train_rmse * 4.0):
        warnings.append("train_test_metric_divergence")
    quality_verdict = "healthy"
    if blockers:
        quality_verdict = "quality_blocked"
    elif warnings:
        quality_verdict = "quality_warning"
    return {
        "quality_verdict": quality_verdict,
        "quality_blockers": blockers,
        "quality_warnings": warnings,
        "label_scale_expected": label_scale_expected,
        "prediction_scale_observed": prediction_scale_observed,
        "outlier_mass_summary": outlier_mass_summary,
    }


def _uncertainty_summary(
    uncertainty_head: str | None,
    y_train: list[float],
    train_pred: list[float],
    test_pred: list[float],
) -> dict[str, Any]:
    requested = _clean_text(uncertainty_head) or "none"
    if requested == "none":
        return {"enabled": False, "requested_uncertainty_head": "none"}
    residuals = [abs(prediction - target) for target, prediction in zip(y_train, train_pred, strict=True)]
    base_residual = float(statistics.fmean(residuals)) if residuals else 0.0
    mean_prediction = float(statistics.fmean(test_pred)) if test_pred else 0.0
    predictive_std = [
        round(base_residual + (abs(prediction - mean_prediction) * 0.1), 6)
        for prediction in test_pred
    ]
    resolved_head = (
        "adapter:ensemble_dropout_proxy"
        if requested == "ensemble_dropout"
        else "adapter:heteroscedastic_proxy"
    )
    return {
        "enabled": True,
        "requested_uncertainty_head": requested,
        "resolved_uncertainty_head": resolved_head,
        "provenance": "residual_calibrated_proxy_summary",
        "sample_count": len(test_pred),
        "mean_predictive_std": (
            float(statistics.fmean(predictive_std)) if predictive_std else 0.0
        ),
        "max_predictive_std": max(predictive_std) if predictive_std else 0.0,
        "predictive_std_by_index": predictive_std,
    }


def _evaluate_predictions(
    train_examples: list[dict[str, Any]],
    test_examples: list[dict[str, Any]],
    train_pred: list[float],
    test_pred: list[float],
    extra_metrics: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    y_train = [item["target"] for item in train_examples]
    y_test = [item["target"] for item in test_examples]
    metrics = {
        "train_rmse": _rmse(y_train, train_pred),
        "test_rmse": _rmse(y_test, test_pred),
        "train_mae": float(mean_absolute_error(y_train, train_pred)),
        "test_mae": float(mean_absolute_error(y_test, test_pred)),
        "train_r2": float(r2_score(y_train, train_pred)),
        "test_r2": float(r2_score(y_test, test_pred)),
        "test_pearson": _pearson(y_test, test_pred),
        **extra_metrics,
    }
    outliers = sorted(
        [
            {
                "example_id": example["example_id"],
                "pdb_id": example["row"]["pdb_id"],
                "target": example["target"],
                "prediction": prediction,
                "residual": prediction - example["target"],
                "absolute_error": abs(prediction - example["target"]),
                "protein_accessions": list(example["row"]["protein_accessions"]),
            }
            for example, prediction in zip(test_examples, test_pred, strict=True)
        ],
        key=lambda item: item["absolute_error"],
        reverse=True,
    )[:10]
    analysis = {
        "chart_family": "regression",
        "prediction_vs_actual": [
            {
                "example_id": example["example_id"],
                "pdb_id": example["row"]["pdb_id"],
                "split": example["row"]["split"],
                "actual": example["target"],
                "predicted": prediction,
            }
            for example, prediction in zip(test_examples, test_pred, strict=True)
        ],
        "residuals": [
            {
                "example_id": example["example_id"],
                "pdb_id": example["row"]["pdb_id"],
                "residual": prediction - example["target"],
                "absolute_error": abs(prediction - example["target"]),
                "predicted": prediction,
            }
            for example, prediction in zip(test_examples, test_pred, strict=True)
        ],
        "residual_histogram": _distribution_bins(
            [
                prediction - example["target"]
                for example, prediction in zip(test_examples, test_pred, strict=True)
            ]
        ),
        "label_distribution_by_split": {
            "train": _distribution_bins(y_train),
            "test": _distribution_bins(y_test),
        },
        "prediction_distribution": _distribution_bins(test_pred),
        "roc_curve": None,
        "pr_curve": None,
        "roc_unavailable_reason": (
            "AUC-ROC and precision-recall charts are only generated for "
            "classification-capable runs."
        ),
    }
    quality = _quality_summary(y_train, y_test, train_pred, test_pred, metrics, outliers)
    uncertainty = _uncertainty_summary(
        extra_metrics.get("requested_uncertainty_head"),
        y_train,
        train_pred,
        test_pred,
    )
    metrics.update(quality)
    metrics["uncertainty_summary"] = uncertainty
    if uncertainty.get("enabled"):
        analysis["uncertainty_by_example"] = [
            {
                "example_id": example["example_id"],
                "pdb_id": example["row"]["pdb_id"],
                "predicted": prediction,
                "predictive_std": uncertainty["predictive_std_by_index"][index],
            }
            for index, (example, prediction) in enumerate(
                zip(test_examples, test_pred, strict=True)
            )
        ]
    return metrics, outliers, analysis


def _build_storage_runtime(examples: list[dict[str, Any]], run_id: str) -> tuple[Any, Any]:
    package_examples: list[PackageManifestExample] = []
    planning_entries: list[PlanningIndexEntry] = []
    canonical_records: list[CanonicalStoreRecord] = []
    seen_canonical_ids: set[str] = set()
    feature_records: list[FeatureCacheEntry] = []
    embedding_records: list[EmbeddingCacheEntry] = []
    available_artifacts: dict[str, Any] = {}
    ppi_rows: list[ProteinProteinSummaryRecord] = []
    seen_ppi_pairs: set[tuple[str, str]] = set()

    for example in examples:
        row = example["row"]
        pdb_id = row["pdb_id"]
        planning_ref = f"planning/{pdb_id}"
        sequence_pointer = f"sequence/{pdb_id}"
        structure_pointer = f"structure/{pdb_id}"
        package_examples.append(
            PackageManifestExample(
                example_id=example["example_id"],
                planning_index_ref=planning_ref,
                source_record_refs=(
                    f"pdb:{pdb_id}",
                    *[f"protein:{item}" for item in row["protein_accessions"]],
                ),
                canonical_ids=tuple(f"protein:{item}" for item in row["protein_accessions"]),
                artifact_pointers=(
                    PackageManifestArtifactPointer(
                        artifact_kind="feature",
                        pointer=sequence_pointer,
                        selector="sequence",
                        source_name="sequence",
                        source_record_id=(
                            f"protein:{row['protein_accessions'][0]}"
                            if row["protein_accessions"]
                            else f"pdb:{pdb_id}"
                        ),
                    ),
                    PackageManifestArtifactPointer(
                        artifact_kind="embedding",
                        pointer=structure_pointer,
                        selector="structure",
                        source_name="structure",
                        source_record_id=f"pdb:{pdb_id}",
                    ),
                ),
                notes=(row["split"], row["source_dataset"]),
            )
        )
        planning_entries.append(
            PlanningIndexEntry(
                planning_id=planning_ref,
                source_records=(
                    PlanningIndexSourceRecord(
                        source_name="pdbbind-benchmark",
                        source_record_id=f"pdb:{pdb_id}",
                        release_version="2026-04-08",
                        manifest_id=f"studio:{run_id}",
                    ),
                ),
                canonical_ids=tuple(f"protein:{item}" for item in row["protein_accessions"]),
                join_status="joined",
            )
        )
        for accession in row["protein_accessions"]:
            canonical_id = f"protein:{accession}"
            if canonical_id not in seen_canonical_ids:
                seen_canonical_ids.add(canonical_id)
                canonical_records.append(
                    CanonicalStoreRecord(
                        canonical_id=canonical_id,
                        entity_kind="protein",
                        canonical_payload={
                            "accession": accession,
                            "pdb_id": pdb_id,
                            "split": row["split"],
                        },
                        source_refs=(
                            CanonicalStoreSourceRef(
                                source_name="pdbbind-benchmark",
                                source_record_id=f"protein:{accession}",
                                source_manifest_id=f"studio:{run_id}",
                                planning_index_ref=planning_ref,
                                package_id=f"model-studio:{run_id}",
                            ),
                        ),
                        planning_index_refs=(planning_ref,),
                        package_ids=(f"model-studio:{run_id}",),
                    )
                )
        feature_records.append(
            FeatureCacheEntry(
                cache_id=f"feature-{pdb_id}",
                feature_family="sequence",
                cache_version="v1",
                source_refs=(
                    FeatureCacheSourceRef(
                        source_name="sequence",
                        source_record_id=f"pdb:{pdb_id}",
                        manifest_id=f"studio:{run_id}",
                        planning_id=planning_ref,
                    ),
                ),
                canonical_ids=tuple(f"protein:{item}" for item in row["protein_accessions"]),
                join_status="joined",
                artifact_pointers=(
                    FeatureCacheArtifactPointer(
                        artifact_kind="feature_matrix",
                        pointer=sequence_pointer,
                        source_name="sequence",
                        source_record_id=f"pdb:{pdb_id}",
                        planning_id=planning_ref,
                    ),
                ),
            )
        )
        embedding_records.append(
            EmbeddingCacheEntry(
                cache_id=f"embedding-{pdb_id}",
                cache_family="structure",
                cache_version="v1",
                model_identity=EmbeddingModelIdentity(
                    model_name="studio-structure-ref",
                    model_version="1",
                ),
                runtime_identity=EmbeddingRuntimeIdentity(
                    runtime_name="studio-model-runtime",
                    runtime_version="1",
                ),
                source_refs=(
                    EmbeddingCacheSourceRef(
                        source_name="structure",
                        source_record_id=f"pdb:{pdb_id}",
                        manifest_id=f"studio:{run_id}",
                        planning_id=planning_ref,
                        provenance_id=f"pdb:{pdb_id}",
                    ),
                ),
                canonical_ids=tuple(f"protein:{item}" for item in row["protein_accessions"]),
                join_status="joined",
                artifact_pointers=(
                    EmbeddingCacheArtifactPointer(
                        artifact_kind="embedding",
                        pointer=structure_pointer,
                        source_name="structure",
                        source_record_id=f"pdb:{pdb_id}",
                        planning_id=planning_ref,
                    ),
                ),
            )
        )
        available_artifacts[sequence_pointer] = {
            "materialized_ref": f"sequence://{pdb_id}",
            "checksum": f"sha256:sequence:{pdb_id}",
            "provenance_refs": [f"protein:{item}" for item in row["protein_accessions"]],
            "notes": [f"studio-sequence-ref:{pdb_id}"],
        }
        available_artifacts[structure_pointer] = {
            "materialized_ref": str(row["structure_file"]),
            "checksum": f"sha256:structure:{pdb_id}",
            "provenance_refs": [f"pdb:{pdb_id}"],
            "notes": [f"studio-structure-ref:{pdb_id}"],
        }
        if len(row["protein_accessions"]) >= 2:
            pair_key = tuple(sorted((row["protein_accessions"][0], row["protein_accessions"][1])))
            if pair_key not in seen_ppi_pairs:
                seen_ppi_pairs.add(pair_key)
                ppi_rows.append(
                    ProteinProteinSummaryRecord(
                        summary_id=f"ppi:{pdb_id}",
                        protein_a_ref=f"protein:{row['protein_accessions'][0]}",
                        protein_b_ref=f"protein:{row['protein_accessions'][1]}",
                        interaction_type="protein complex",
                        interaction_id=pdb_id,
                        interaction_refs=(pdb_id,),
                        organism_name="unknown",
                        physical_interaction=True,
                        join_status="joined",
                        context=SummaryRecordContext(
                            provenance_pointers=(
                                SummaryProvenancePointer(
                                    provenance_id=f"pdb:{pdb_id}",
                                    source_name="pdbbind-benchmark",
                                    source_record_id=pdb_id,
                                    release_version="2026-04-08",
                                ),
                            ),
                            storage_notes=("studio multimodal bridge",),
                        ),
                    )
                )

    package_manifest = PackageManifest(
        package_id=f"model-studio:{run_id}",
        selected_examples=tuple(package_examples),
        raw_manifests=(
            PackageManifestRawManifest(
                source_name="model-studio-benchmark",
                raw_manifest_id=f"studio:{run_id}",
                raw_manifest_ref=f"artifacts/runtime/model_studio/runs/{run_id}/run_manifest.json",
                release_version="2026-04-08",
                planning_index_ref=f"planning/studio:{run_id}",
                notes=("Model Studio benchmark bridge",),
            ),
        ),
        planning_index_refs=(f"planning/studio:{run_id}",),
        materialization=PackageManifestMaterialization(
            split_name="studio-train-test",
            split_artifact_id=f"split:{run_id}",
            materialization_run_id=run_id,
            materialization_mode="selective",
            package_version="2026-04-08",
            package_state="draft",
            materialized_at=_utc_now(),
            published_at=_utc_now(),
            notes=("Model Studio runtime bridge",),
        ),
        provenance=("api/model_studio/runtime.py",),
        notes=("Model Studio multimodal/portfolio bridge.",),
    )

    storage_runtime = integrate_storage_runtime(
        package_manifest,
        planning_index=PlanningIndexSchema(records=tuple(planning_entries)),
        canonical_store=CanonicalStore(records=tuple(canonical_records)),
        feature_cache=FeatureCacheCatalog(records=tuple(feature_records)),
        embedding_cache=EmbeddingCacheCatalog(records=tuple(embedding_records)),
        available_artifacts=available_artifacts,
        materialization_run_id=run_id,
        materialized_at=datetime.now(tz=UTC),
        package_version="2026-04-08",
        package_state="draft",
        split_name="studio-train-test",
        split_artifact_id=f"split:{run_id}",
        published_at=datetime.now(tz=UTC),
        provenance_refs=("api/model_studio/runtime.py",),
        notes=("Model Studio benchmark bridge.",),
    )
    ppi_representation = build_ppi_representation(
        tuple(ppi_rows),
        representation_id=f"ppi:{run_id}",
        library_id=f"ppi:{run_id}",
        source_manifest_id=f"studio:{run_id}",
        provenance=("api/model_studio/runtime.py",),
    )
    return storage_runtime, ppi_representation


def _build_leakage_summary(
    train_rows: list[BenchmarkRow],
    test_rows: list[BenchmarkRow],
) -> dict[str, Any]:
    train_accessions = {item for row in train_rows for item in row.protein_accessions}
    test_accessions = {item for row in test_rows for item in row.protein_accessions}
    exact_overlap = sorted(train_accessions & test_accessions)
    return {
        "train_examples": len(train_rows),
        "test_examples": len(test_rows),
        "train_unique_accessions": len(train_accessions),
        "test_unique_accessions": len(test_accessions),
        "direct_protein_overlap_count": len(exact_overlap),
        "direct_protein_overlap": exact_overlap[:25],
        "status": "blocked" if exact_overlap else "ready",
    }


def _run_artifact_index(run_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for relative in (
        "run_manifest.json",
        "execution_graph.json",
        "stage_status.json",
        "training_set_candidate_preview.json",
        "canonical_examples.json",
        "split_summary.json",
        "structure_materialization.json",
        "feature_index.json",
        "training_examples.json",
        "packaging_manifest.json",
        "artifacts.json",
        "metrics.json",
        "analysis.json",
        "outliers.json",
        "recommendations.json",
        "logs.json",
        "report.md",
    ):
        path = run_dir / relative
        if path.exists():
            mapping[relative] = _artifact_rel(path)
    return mapping


def recover_stale_runs(grace_seconds: int = RUN_STATE_GRACE_SECONDS) -> list[str]:
    recovered: list[str] = []
    if not RUN_DIR.exists():
        return recovered
    for run_dir in RUN_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        # Skip anything whose name doesn't look like a run id. Recovery
        # mutates the manifest file in place, so we never want it to
        # touch directories an attacker might have placed under
        # ``RUN_DIR`` (a stray ``..`` sibling, dotfile cache, etc.).
        if not _RUN_ID_PATTERN.match(run_dir.name):
            continue
        manifest = _read_manifest(run_dir)
        if not manifest:
            continue
        status = _clean_text(manifest.get("status"))
        if status not in {"running", "queued"}:
            continue
        age_seconds = (
            _timestamp_age_seconds(manifest.get("heartbeat_at"))
            or _timestamp_age_seconds(manifest.get("updated_at"))
            or _timestamp_age_seconds(manifest.get("created_at"))
        )
        if age_seconds is None or age_seconds < grace_seconds:
            continue
        normalized = dict(manifest)
        normalized["status"] = "interrupted"
        normalized["active_stage"] = None
        normalized["recovered_at"] = _utc_now()
        notes = list(normalized.get("notes", []))
        note = (
            "Recovered from stale in-progress state after exceeding the internal-alpha "
            "heartbeat grace period."
        )
        if note not in notes:
            notes.append(note)
        normalized["notes"] = notes
        _write_manifest(run_dir, normalized)
        recovered.append(_clean_text(normalized.get("run_id")))
    return recovered


def _read_manifest_without_recovery(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    if not manifest:
        return manifest
    return manifest


def _execute_run_sync(
    spec: ModelStudioPipelineSpec,
    *,
    manifest_ready: threading.Event | None = None,
) -> dict[str, Any]:
    """Execute one Model Studio run synchronously.

    ``manifest_ready`` is set as soon as the run directory and an
    initial ``run_manifest.json`` exist on disk; :func:`launch_run` uses
    this to know the manifest is visible without polling.
    """
    report = validate_pipeline_spec(spec)
    blockers = [item.message for item in report.items if item.level == "blocker"]
    if blockers:
        raise ValueError("Release catalog blockers: " + " | ".join(blockers))

    run_stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
    run_suffix = spec.pipeline_id.split(":")[-1]
    run_id = f"run-{run_stamp}-{run_suffix}-{uuid4().hex[:8]}"
    run_dir = RUN_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    log_lines: list[str] = []
    hardware_profile = discover_hardware_profile()
    placement = _resolve_execution_placement(
        _clean_text(spec.preprocess_plan.options.get("hardware_runtime_preset")),
        hardware_profile,
    )
    graph = compile_execution_graph(spec)
    stage_status: dict[str, dict[str, Any]] = {
        stage: {
            "stage": stage,
            "status": "pending",
            "adapter_status": "catalog",
            "detail": "Waiting for execution.",
            "artifact_refs": [],
            "blockers": [],
            "progress_current": 0,
            "progress_total": 0,
            "progress_percent": 0.0,
            "substage": None,
            "latest_artifact": None,
            "updated_at": _utc_now(),
        }
        for stage in graph.stages
    }
    stage_started_at: dict[str, str] = {}
    _save_json(run_dir / "execution_graph.json", graph.to_dict())
    _save_json(run_dir / "stage_status.json", stage_status)
    _save_json(run_dir / "recommendations.json", report.to_dict())
    _append_log(log_lines, f"Launching Model Studio run {run_id}")
    _write_run_control(
        run_dir,
        {
            "cancel_requested": False,
            "resume_requested": False,
            "updated_at": _utc_now(),
        },
    )

    def mark_stage(
        stage: str,
        status: str,
        *,
        adapter_status: str = "runnable",
        detail: str = "",
        artifact_refs: list[str] | None = None,
        blockers: list[str] | None = None,
        progress_current: int = 0,
        progress_total: int = 0,
        substage: str | None = None,
        latest_artifact: str | None = None,
        technical_detail: str | None = None,
    ) -> None:
        if status == "running" and stage not in stage_started_at:
            stage_started_at[stage] = _utc_now()
        progress_percent = (
            round((progress_current / progress_total) * 100, 2)
            if progress_total
            else 0.0
        )
        stage_status[stage] = {
            "stage": stage,
            "status": status,
            "adapter_status": adapter_status,
            "detail": detail,
            "artifact_refs": artifact_refs or [],
            "blockers": blockers or [],
            "progress_current": progress_current,
            "progress_total": progress_total,
            "progress_percent": progress_percent,
            "substage": substage,
            "latest_artifact": latest_artifact,
            "technical_detail": technical_detail,
            "started_at": stage_started_at.get(stage),
            "updated_at": _utc_now(),
        }
        _save_json(run_dir / "stage_status.json", stage_status)
        try:
            manifest["updated_at"] = _utc_now()
            manifest["heartbeat_at"] = _utc_now()
            manifest["active_stage"] = (
                stage if status in {"running", "blocked"} else manifest.get("active_stage")
            )
            _write_manifest(run_dir, manifest)
        except NameError:
            pass

    def persist_manifest(**updates: Any) -> None:
        manifest.update(updates)
        manifest["updated_at"] = _utc_now()
        manifest["heartbeat_at"] = _utc_now()
        _write_manifest(run_dir, manifest)

    manifest = StudioRunManifest(
        run_id=run_id,
        pipeline_id=spec.pipeline_id,
        graph_id=graph.graph_id,
        status="running",
        active_stage="training_set_request_resolution",
        artifact_refs=(),
        blocker_refs=tuple(graph.blockers),
        notes=(),
    ).to_dict()
    manifest.update(
        {
            "study_title": spec.study_title,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "heartbeat_at": _utc_now(),
            "model_family": spec.training_plan.model_family,
            "dataset_ref": None,
            "training_set_build_id": None,
            "requested_hardware_preset": placement["requested_hardware_preset"],
            "resolved_hardware_preset": placement["resolved_hardware_preset"],
            "resolved_execution_device": placement["resolved_execution_device"],
            "resolved_training_backend": None,
            "placement_notes": list(placement.get("placement_notes") or []),
        }
    )
    _write_manifest(run_dir, manifest)
    # The manifest is now on disk. Tell ``launch_run`` it can return
    # immediately rather than poll-and-wait.
    if manifest_ready is not None:
        manifest_ready.set()

    mark_stage(
        "training_set_request_resolution",
        "running",
        detail="Resolving the requested training-set sources.",
        substage="Inspecting known local dataset manifests.",
    )
    _check_for_cancellation(run_dir)
    preview = preview_training_set_request(
        spec.training_set_request,
        spec.split_plan,
        fallback_dataset_refs=spec.data_strategy.dataset_refs,
    )
    preview_path = run_dir / "training_set_candidate_preview.json"
    _save_json(preview_path, preview)
    mark_stage(
        "training_set_request_resolution",
        "completed",
        detail=(
            f"Resolved request against {len(preview['resolved_dataset_refs'])} source dataset(s)."
        ),
        artifact_refs=[_artifact_rel(preview_path)],
        progress_current=len(preview["resolved_dataset_refs"]),
        progress_total=len(preview["resolved_dataset_refs"]),
        latest_artifact=_artifact_rel(preview_path),
    )
    _append_log(
        log_lines,
        (
            "Training-set request resolved to "
            f"{', '.join(preview['resolved_dataset_refs']) or 'no sources'}"
        ),
    )

    mark_stage(
        "dataset_candidate_preview",
        "completed" if preview["diagnostics"]["status"] == "ready" else "blocked",
        detail=(
            f"{preview['candidate_preview']['row_count']} candidate examples, "
            f"structure coverage {preview['diagnostics']['structure_coverage']:.2%}"
        ),
        artifact_refs=[_artifact_rel(preview_path)],
        blockers=preview["diagnostics"].get("blockers", []),
        progress_current=preview["candidate_preview"]["row_count"],
        progress_total=preview["candidate_preview"]["row_count"],
        latest_artifact=_artifact_rel(preview_path),
    )
    if preview["diagnostics"]["status"] == "blocked":
        persist_manifest(status="blocked", active_stage=None, blocked_at=_utc_now())
        _save_json(run_dir / "logs.json", {"lines": log_lines})
        _save_json(run_dir / "artifacts.json", _run_artifact_index(run_dir))
        return manifest

    mark_stage(
        "dataset_build",
        "running",
        detail="Building the study dataset and split package.",
        substage="Writing train, val, and test manifests.",
    )
    _check_for_cancellation(run_dir)
    built_manifest = build_training_set(
        spec.pipeline_id,
        spec.study_title,
        spec.training_set_request,
        spec.split_plan,
        fallback_dataset_refs=spec.data_strategy.dataset_refs,
    )
    build_manifest_path = Path(built_manifest["source_manifest"])
    persist_manifest(
        dataset_ref=built_manifest["dataset_ref"],
        training_set_build_id=built_manifest["build_id"],
    )
    mark_stage(
        "dataset_build",
        "completed",
        detail=(
            f"Built study dataset {built_manifest['dataset_ref']} with "
            f"{built_manifest['row_count']} examples."
        ),
        artifact_refs=[
            _artifact_rel(build_manifest_path),
            _artifact_rel(Path(built_manifest["train_csv"])),
            _artifact_rel(Path(built_manifest["test_csv"])),
        ],
        progress_current=built_manifest["row_count"],
        progress_total=built_manifest["row_count"],
        latest_artifact=_artifact_rel(build_manifest_path),
    )
    _append_log(log_lines, f"Built training set {built_manifest['dataset_ref']}")

    dataset = DatasetDescriptor(
        dataset_ref=built_manifest["dataset_ref"],
        label=built_manifest["label"],
        task_type=built_manifest["task_type"],
        split_strategy=built_manifest["split_strategy"],
        train_csv=Path(built_manifest["train_csv"]),
        val_csv=Path(built_manifest["val_csv"]) if built_manifest.get("val_csv") else None,
        test_csv=Path(built_manifest["test_csv"]),
        source_manifest=Path(built_manifest["source_manifest"]),
        row_count=int(built_manifest["row_count"]),
        tags=tuple(built_manifest.get("tags", ())),
        maturity=built_manifest["maturity"],
        catalog_status="release",
    )
    graph_recipe = spec.graph_recipes[0]
    requested_label_type = _clean_text(spec.data_strategy.label_type) or "delta_G"
    train_rows, val_rows, test_rows = _load_dataset_rows(dataset)
    all_rows = [*train_rows, *val_rows, *test_rows]
    label_provenance_summary = _label_provenance_summary(all_rows, requested_label_type)
    canonical_examples = [
        {
            "example_id": row.example_id,
            "pdb_id": row.pdb_id,
            "split": row.split,
            "protein_accessions": list(row.protein_accessions),
            "structure_file": str(row.structure_file),
        }
        for row in all_rows
    ]
    _save_json(run_dir / "canonical_examples.json", canonical_examples)

    leakage = _build_leakage_summary([*train_rows, *val_rows], test_rows)
    _save_json(run_dir / "split_summary.json", leakage)
    mark_stage(
        "split_compilation",
        "completed",
        detail=(f"{len(train_rows)} train / {len(val_rows)} val / {len(test_rows)} test examples."),
        artifact_refs=[_artifact_rel(run_dir / "split_summary.json")],
        blockers=(
            ["Direct protein overlap detected in split."] if leakage["status"] == "blocked" else []
        ),
        progress_current=len(all_rows),
        progress_total=len(all_rows),
        latest_artifact=_artifact_rel(run_dir / "split_summary.json"),
    )

    mark_stage(
        "structure_resolution",
        "running",
        detail="Checking local structure availability for the built study dataset.",
        progress_current=0,
        progress_total=len(all_rows),
        substage="Scanning structure file paths.",
    )
    _check_for_cancellation(run_dir)
    structure_materialization = {
        "present_count": 0,
        "missing_count": 0,
        "missing_examples": [],
    }
    for row in all_rows:
        if row.structure_file.exists():
            structure_materialization["present_count"] += 1
        else:
            structure_materialization["missing_count"] += 1
            structure_materialization["missing_examples"].append(row.example_id)
        checked = (
            structure_materialization["present_count"] + structure_materialization["missing_count"]
        )
        if checked == len(all_rows) or checked % 25 == 0:
            mark_stage(
                "structure_resolution",
                "running",
                detail=(
                    f"Checked {checked} / {len(all_rows)} structures; "
                    f"{structure_materialization['missing_count']} missing so far."
                ),
                progress_current=checked,
                progress_total=len(all_rows),
                substage="Scanning structure file paths.",
            )
    _save_json(run_dir / "structure_materialization.json", structure_materialization)
    mark_stage(
        "structure_resolution",
        "completed" if not structure_materialization["missing_count"] else "blocked",
        detail=(
            f"{structure_materialization['present_count']} structures available, "
            f"{structure_materialization['missing_count']} missing."
        ),
        artifact_refs=[_artifact_rel(run_dir / "structure_materialization.json")],
        blockers=structure_materialization["missing_examples"][:20],
        progress_current=len(all_rows),
        progress_total=len(all_rows),
        latest_artifact=_artifact_rel(run_dir / "structure_materialization.json"),
    )
    if structure_materialization["missing_count"]:
        persist_manifest(
            status="blocked",
            active_stage=None,
            blocked_at=_utc_now(),
        )
        _save_json(run_dir / "logs.json", {"lines": log_lines})
        _save_json(run_dir / "artifacts.json", _run_artifact_index(run_dir))
        return manifest

    unresolved_partner_examples = [
        row.example_id
        for row in all_rows
        if _clean_text(row.metadata.get("Partner Role Resolution")) == "unresolved_whole_complex_only"
    ]
    if unresolved_partner_examples and (
        graph_recipe.graph_kind != "whole_complex_graph"
        or graph_recipe.region_policy != "whole_molecule"
        or graph_recipe.partner_awareness != "symmetric"
    ):
        blocker_payload = {
            "reason": (
                "This governed subset contains staged rows with unresolved partner roles. "
                "Those rows are launchable only with whole-complex graphs and symmetric partner awareness "
                "until native partner-role resolution lands."
            ),
            "required_graph_kind": "whole_complex_graph",
            "required_region_policy": "whole_molecule",
            "required_partner_awareness": "symmetric",
            "affected_example_ids": unresolved_partner_examples[:40],
        }
        _save_json(run_dir / "structure_role_resolution_blocker.json", blocker_payload)
        mark_stage(
            "feature_materialization",
            "blocked",
            detail=blocker_payload["reason"],
            artifact_refs=[_artifact_rel(run_dir / "structure_role_resolution_blocker.json")],
            blockers=unresolved_partner_examples[:20],
            latest_artifact=_artifact_rel(run_dir / "structure_role_resolution_blocker.json"),
        )
        persist_manifest(
            status="blocked",
            active_stage=None,
            blocked_at=_utc_now(),
        )
        _save_json(run_dir / "logs.json", {"lines": log_lines})
        _save_json(run_dir / "artifacts.json", _run_artifact_index(run_dir))
        return manifest

    mark_stage(
        "feature_materialization",
        "running",
        detail="Extracting structural feature summaries from local structures.",
        progress_current=0,
        progress_total=len(all_rows),
        substage="Generating per-example feature payloads.",
    )
    _check_for_cancellation(run_dir)
    extracted_examples: list[dict[str, Any]] = []
    feature_dir = run_dir / "features"
    graph_dir = run_dir / "graphs"
    for row in all_rows:
        label_payload = _label_payload(row, requested_label_type)
        parsed = _parse_structure(row, graph_recipe, spec.preprocess_plan)
        distributed_payload = _distributed_feature_payload(
            row,
            parsed,
            spec.feature_recipes[0],
            spec.preprocess_plan,
        )
        distributed_vector = _distributed_feature_vector(distributed_payload)
        feature_payload = {
            "example_id": row.example_id,
            "pdb_id": row.pdb_id,
            "split": row.split,
            "tabular_features": _tabular_vector(row, parsed, spec.feature_recipes[0]),
            "graph_summary_features": _graph_summary_vector(parsed, spec.feature_recipes[0]),
            "distributed_features": distributed_payload,
            "distributed_feature_vector": distributed_vector,
            "global_features": parsed["global_features"],
            "protein_accessions": list(row.protein_accessions),
            "target": label_payload["value"],
            "requested_label_type": label_payload["requested_label_type"],
            "resolved_label_type": label_payload["resolved_label_type"],
            "label_origin": label_payload["label_origin"],
            "label_provenance": label_payload["conversion_provenance"],
            "assay_family": label_payload["assay_family"],
            "graph_kind": graph_recipe.graph_kind,
            "graph_node_granularity": parsed.get("graph_node_granularity", graph_recipe.node_granularity),
            "sequence_embedding": parsed.get("sequence_embedding"),
        }
        graph_payload = {
            "example_id": row.example_id,
            "pdb_id": row.pdb_id,
            "split": row.split,
            "nodes": parsed["graph_nodes"],
            "edges": parsed["graph_edges"],
            "interface_residues": parsed["interface_residues"],
            "shell_residues": parsed["shell_residues"],
            "graph_kind": graph_recipe.graph_kind,
            "graph_node_granularity": parsed.get("graph_node_granularity", graph_recipe.node_granularity),
            "region_policy": graph_recipe.region_policy,
        }
        feature_path = feature_dir / f"{row.example_id.replace(':', '_')}.json"
        graph_path = graph_dir / f"{row.example_id.replace(':', '_')}.json"
        _save_json(feature_path, feature_payload)
        _save_json(graph_path, graph_payload)
        extracted_examples.append(
            {
                "example_id": row.example_id,
                "row": {
                    "pdb_id": row.pdb_id,
                    "split": row.split,
                    "protein_accessions": row.protein_accessions,
                    "structure_file": str(row.structure_file),
                    "source_dataset": row.source_dataset,
                    "measurement_type": _measurement_type(row),
                    "assay_family": label_payload["assay_family"],
                },
                "target": label_payload["value"],
                "tabular_features": feature_payload["tabular_features"],
                "graph_summary_features": feature_payload["graph_summary_features"],
                "distributed_features": feature_payload["distributed_features"],
                "distributed_feature_vector": feature_payload["distributed_feature_vector"],
                "sequence_embedding": feature_payload["distributed_features"].get("sequence_embeddings"),
                "graph": graph_payload,
                "requested_label_type": label_payload["requested_label_type"],
                "resolved_label_type": label_payload["resolved_label_type"],
                "label_origin": label_payload["label_origin"],
                "label_provenance": label_payload["conversion_provenance"],
                "feature_artifact": _artifact_rel(feature_path),
                "graph_artifact": _artifact_rel(graph_path),
            }
        )
        produced = len(extracted_examples)
        if produced == len(all_rows) or produced % 20 == 0:
            mark_stage(
                "feature_materialization",
                "running",
                detail=(
                    f"Materialized feature payloads for {produced} / {len(all_rows)} examples."
                ),
                progress_current=produced,
                progress_total=len(all_rows),
                substage="Generating per-example feature payloads.",
                latest_artifact=_artifact_rel(feature_path),
            )
    _save_json(
        run_dir / "feature_index.json",
        {
            "items": [
                {
                    "example_id": item["example_id"],
                    "feature_artifact": item["feature_artifact"],
                    "graph_artifact": item["graph_artifact"],
                }
                for item in extracted_examples
            ]
        },
    )
    mark_stage(
        "feature_materialization",
        "completed",
        detail=f"Extracted structural summaries for {len(extracted_examples)} examples.",
        artifact_refs=[_artifact_rel(run_dir / "feature_index.json")],
        progress_current=len(extracted_examples),
        progress_total=len(extracted_examples),
        latest_artifact=_artifact_rel(run_dir / "feature_index.json"),
    )
    mark_stage(
        "graph_materialization",
        "completed",
        detail=f"{graph_recipe.graph_kind} payloads created from local structures.",
        artifact_refs=[_artifact_rel(graph_dir)],
        progress_current=len(extracted_examples),
        progress_total=len(extracted_examples),
        latest_artifact=_artifact_rel(graph_dir),
    )

    mark_stage(
        "example_packaging",
        "running",
        detail="Packaging graph, global, and distributed features into model-ready examples.",
        progress_current=0,
        progress_total=len(extracted_examples),
        substage="Writing packaged training example manifest.",
    )
    _check_for_cancellation(run_dir)
    train_examples = [item for item in extracted_examples if item["row"]["split"] == "train"]
    val_examples = [item for item in extracted_examples if item["row"]["split"] == "val"]
    test_examples = [item for item in extracted_examples if item["row"]["split"] == "test"]
    sequence_embedding_summary = _sequence_embedding_summary(extracted_examples)
    _save_json(
        run_dir / "training_examples.json",
        {"train": train_examples, "val": val_examples, "test": test_examples},
    )
    _save_json(
        run_dir / "packaging_manifest.json",
        {
            "train_count": len(train_examples),
            "val_count": len(val_examples),
            "test_count": len(test_examples),
            "train_ids": [item["example_id"] for item in train_examples],
            "val_ids": [item["example_id"] for item in val_examples],
            "test_ids": [item["example_id"] for item in test_examples],
            "feature_bundle": {
                "global": spec.example_materialization.include_global_features,
                "distributed": spec.example_materialization.include_distributed_features,
                "graph": spec.example_materialization.include_graph_payloads,
                "global_feature_sets": list(spec.feature_recipes[0].global_feature_sets),
                "distributed_feature_sets": list(
                    spec.feature_recipes[0].distributed_feature_sets
                ),
                "partner_awareness": graph_recipe.partner_awareness,
                "graph_node_granularity": graph_recipe.node_granularity,
                "sequence_embedding_enabled": "sequence embeddings" in spec.preprocess_plan.modules
                and "sequence_embeddings" in spec.feature_recipes[0].distributed_feature_sets,
                "sequence_embedding_summary": sequence_embedding_summary or {"enabled": False},
            },
        },
    )
    mark_stage(
        "example_packaging",
        "completed",
        detail="Packaged graph, global, and distributed features for model-ready consumption.",
        artifact_refs=[
            _artifact_rel(run_dir / "training_examples.json"),
            _artifact_rel(run_dir / "packaging_manifest.json"),
        ],
        progress_current=len(extracted_examples),
        progress_total=len(extracted_examples),
        latest_artifact=_artifact_rel(run_dir / "packaging_manifest.json"),
    )

    mark_stage(
        "model_training",
        "running",
        detail="Training the selected released model family.",
        progress_current=0,
        progress_total=max(len(train_examples), 1),
        substage=f"Backend: {spec.training_plan.model_family}",
    )
    _check_for_cancellation(run_dir)
    x_train = [item["tabular_features"] for item in train_examples]
    x_test = [item["tabular_features"] for item in test_examples]
    if spec.example_materialization.include_distributed_features:
        x_train = [
            [*item["tabular_features"], *item["distributed_feature_vector"]]
            for item in train_examples
        ]
        x_test = [
            [*item["tabular_features"], *item["distributed_feature_vector"]]
            for item in test_examples
        ]
    x_train_fusion = [
        [
            *item["tabular_features"],
            *(
                item["distributed_feature_vector"]
                if spec.example_materialization.include_distributed_features
                else []
            ),
            *item["graph_summary_features"],
        ]
        for item in train_examples
    ]
    x_test_fusion = [
        [
            *item["tabular_features"],
            *(
                item["distributed_feature_vector"]
                if spec.example_materialization.include_distributed_features
                else []
            ),
            *item["graph_summary_features"],
        ]
        for item in test_examples
    ]
    y_train = [item["target"] for item in train_examples]

    model_family = spec.training_plan.model_family
    if model_family in {"xgboost", "catboost", "mlp"}:
        train_meta, train_pred, test_pred = _train_tabular_model(
            model_family,
            x_train,
            y_train,
            x_test,
            spec.training_plan.epoch_budget,
        )
        training_backend = train_meta["resolved_backend"]
        model_details = train_meta
    elif model_family == "multimodal_fusion":
        requested_modalities = ["structure", "ppi"]
        if (
            "sequence embeddings" in spec.preprocess_plan.modules
            and "sequence_embeddings" in spec.feature_recipes[0].distributed_feature_sets
        ):
            requested_modalities.insert(0, "sequence")
        train_meta, train_pred, test_pred = _train_tabular_model(
            "mlp",
            x_train_fusion,
            y_train,
            x_test_fusion,
            spec.training_plan.epoch_budget,
        )
        training_backend = "sklearn-mlp-fusion-adapter"
        storage_runtime, ppi_representation = _build_storage_runtime(train_examples, run_id)
        experiment_registry = build_experiment_registry(
            storage_runtime,
            ppi_representation=ppi_representation,
            requested_modalities=tuple(requested_modalities),
            model_name="studio-multimodal-fusion",
            fusion_dim=8,
            deterministic_seed=7,
            provenance=("api/model_studio/runtime.py",),
            notes=("Studio multimodal bridge",),
        )
        portfolio_slices = expand_portfolio_matrix(
            (
                PortfolioCandidateSpec(
                    candidate_id="fusion-primary",
                    rank=1,
                    model_name="studio-multimodal-fusion",
                    requested_modalities=tuple(requested_modalities),
                    fusion_dim=8,
                    notes=("Studio primary multimodal candidate",),
                    tags=("studio", "multimodal"),
                ),
            ),
            ablations=(
                PortfolioAblationSpec(
                    ablation_id="drop-ppi",
                    rank_offset=1,
                    drop_modalities=("ppi",),
                    notes=("Check PPI sidecar impact",),
                    tags=("ablation",),
                ),
            ),
        )
        portfolio_run = run_portfolio_matrix(
            storage_runtime,
            portfolio_slices,
            ppi_representation=ppi_representation,
            deterministic_seed_base=7,
            provenance=("api/model_studio/runtime.py",),
            notes=("Studio multimodal portfolio",),
        )
        _save_json(run_dir / "experiment_registry.json", experiment_registry.to_dict())
        _save_json(run_dir / "portfolio_matrix.json", portfolio_run.to_dict())
        _save_json(
            run_dir / "multimodal_runtime_summary.json",
            execute_multimodal_training(
                storage_runtime,
                ppi_representation=ppi_representation,
                deterministic_seed=7,
                learning_rate=0.05,
                max_examples=min(12, len(train_examples)),
                provenance=("api/model_studio/runtime.py",),
                notes=("Studio prototype multimodal runtime",),
            ).to_dict(),
        )
        model_details = {
            **train_meta,
            "resolved_backend": training_backend,
            "integration_artifacts": [
                _artifact_rel(run_dir / "experiment_registry.json"),
                _artifact_rel(run_dir / "portfolio_matrix.json"),
                _artifact_rel(run_dir / "multimodal_runtime_summary.json"),
            ],
        }
    elif model_family in {"graphsage", "gin", "gcn", "gat"}:
        graph_backend_family = {
            "graphsage": "graphsage",
            "gin": "graphsage_lite_family",
            "gcn": "graphsage_lite_family",
            "gat": "graphsage_lite_family",
        }[model_family]
        graph_resolved_backend = {
            "graphsage": "torch-graphsage-lite",
            "gin": "adapter:graphsage-lite-family",
            "gcn": "adapter:graphsage-lite-family",
            "gat": "adapter:graphsage-lite-family",
        }[model_family]
        graph_meta, train_pred, test_pred = _train_graph_model(
            train_examples,
            test_examples,
            spec.training_plan.epoch_budget,
            backend_family=graph_backend_family,
            resolved_backend=graph_resolved_backend,
            optimizer_name=spec.training_plan.optimizer,
            scheduler_name=spec.training_plan.scheduler,
            loss_name=spec.training_plan.loss_function,
            batch_policy=spec.training_plan.batch_policy,
        )
        training_backend = graph_meta["resolved_backend"]
        graph_meta.setdefault("requested_model_family", model_family)
        model_details = graph_meta
    elif model_family == "late_fusion_ensemble":
        ensemble_meta, train_pred, test_pred = _train_late_fusion_ensemble(
            x_train_fusion,
            y_train,
            x_test_fusion,
            spec.training_plan.epoch_budget,
        )
        training_backend = ensemble_meta["resolved_backend"]
        model_details = ensemble_meta
    else:
        training_backend = f"blocked_or_stubbed:{model_family}"
        model_details = {"resolved_backend": training_backend}
        train_pred = list(y_train)
        test_pred = [statistics.fmean(y_train)] * len(test_examples)

    model_details.setdefault("requested_hardware_preset", placement["requested_hardware_preset"])
    model_details.setdefault("resolved_hardware_preset", placement["resolved_hardware_preset"])
    model_details.setdefault("resolved_execution_device", placement["resolved_execution_device"])
    model_details.setdefault("placement_notes", list(placement.get("placement_notes") or []))
    model_details.setdefault("graph_node_granularity", graph_recipe.node_granularity)
    model_details.setdefault(
        "sequence_embedding_enabled",
        "sequence embeddings" in spec.preprocess_plan.modules
        and "sequence_embeddings" in spec.feature_recipes[0].distributed_feature_sets,
    )
    model_details.setdefault(
        "sequence_embedding_summary",
        sequence_embedding_summary or {"enabled": False},
    )
    persist_manifest(
        resolved_training_backend=training_backend,
        resolved_execution_device=placement["resolved_execution_device"],
        resolved_hardware_preset=placement["resolved_hardware_preset"],
    )

    metrics, outliers, analysis = _evaluate_predictions(
        train_examples,
        test_examples,
        train_pred,
        test_pred,
        {
            "requested_model_family": model_family,
            "requested_label_type": requested_label_type,
            "resolved_label_type": label_provenance_summary["resolved_label_type"],
            "label_origin": label_provenance_summary["label_origin"],
            "label_origin_variants": label_provenance_summary["label_origin_variants"],
            "label_conversion_provenance": label_provenance_summary["conversion_provenance"],
            "label_conversion_provenance_variants": label_provenance_summary["conversion_provenance_variants"],
            "assay_family_disclosure": label_provenance_summary["assay_families"],
            "resolved_backend": training_backend,
            "train_count": len(train_examples),
            "val_count": len(val_examples),
            "test_count": len(test_examples),
            "dataset_ref": dataset.dataset_ref,
            "split_strategy": spec.data_strategy.split_strategy,
            "requested_dataset_refs": list(spec.data_strategy.dataset_refs),
            "requested_source_families": list(spec.training_set_request.source_families),
            "training_set_build_id": built_manifest["build_id"],
            "requested_hardware_preset": placement["requested_hardware_preset"],
            "resolved_hardware_preset": placement["resolved_hardware_preset"],
            "resolved_execution_device": placement["resolved_execution_device"],
            "requested_uncertainty_head": _clean_text(spec.training_plan.uncertainty_head) or "none",
            "graph_node_granularity": graph_recipe.node_granularity,
            "sequence_embedding_enabled": "sequence embeddings" in spec.preprocess_plan.modules
            and "sequence_embeddings" in spec.feature_recipes[0].distributed_feature_sets,
            "sequence_embedding_summary": sequence_embedding_summary or {"enabled": False},
        },
    )
    _save_json(run_dir / "metrics.json", metrics)
    _save_json(run_dir / "outliers.json", {"items": outliers})
    _save_json(run_dir / "analysis.json", analysis)
    _save_json(run_dir / "model_details.json", model_details)
    mark_stage(
        "model_training",
        "completed",
        adapter_status=(
            "blocked_or_stubbed"
            if training_backend.startswith("blocked_or_stubbed")
            else "runnable"
        ),
        detail=f"Trained with {training_backend}",
        artifact_refs=[
            _artifact_rel(run_dir / "metrics.json"),
            _artifact_rel(run_dir / "model_details.json"),
            _artifact_rel(run_dir / "analysis.json"),
        ],
        progress_current=len(train_examples),
        progress_total=len(train_examples),
        latest_artifact=_artifact_rel(run_dir / "analysis.json"),
    )
    mark_stage(
        "evaluation",
        "completed",
        detail=(
            f"Test RMSE {metrics['test_rmse']:.3f}, Pearson {metrics['test_pearson']:.3f}, "
            f"quality verdict {metrics['quality_verdict']}"
        ),
        artifact_refs=[
            _artifact_rel(run_dir / "metrics.json"),
            _artifact_rel(run_dir / "outliers.json"),
            _artifact_rel(run_dir / "analysis.json"),
        ],
        progress_current=len(test_examples),
        progress_total=len(test_examples),
        latest_artifact=_artifact_rel(run_dir / "analysis.json"),
    )

    mark_stage(
        "reporting",
        "running",
        detail="Writing the study summary and review artifacts.",
        substage="Building exportable summary and report files.",
    )
    _check_for_cancellation(run_dir)
    report_lines = [
        f"# Model Studio Run Summary: {spec.study_title}",
        "",
        f"- Run id: `{run_id}`",
        f"- Dataset: `{dataset.dataset_ref}`",
        f"- Training-set build: `{built_manifest['build_id']}`",
        f"- Model family: `{model_family}`",
        f"- Label family: `{requested_label_type}`",
        f"- Label origin: `{label_provenance_summary['label_origin']}`",
        f"- Resolved backend: `{training_backend}`",
        f"- Graph node granularity: `{graph_recipe.node_granularity}`",
        f"- Execution device: `{placement['resolved_execution_device']}`",
        f"- Hardware preset: `{placement['resolved_hardware_preset']}` "
        f"(requested `{placement['requested_hardware_preset']}`)",
        f"- Uncertainty head: `{metrics['uncertainty_summary'].get('resolved_uncertainty_head') if metrics.get('uncertainty_summary', {}).get('enabled') else 'none'}`",
        f"- Sequence embeddings: `{'enabled' if ('sequence embeddings' in spec.preprocess_plan.modules and 'sequence_embeddings' in spec.feature_recipes[0].distributed_feature_sets) else 'disabled'}`",
        (
            f"- Train/Val/Test: `{len(train_examples)}` / "
            f"`{len(val_examples)}` / `{len(test_examples)}`"
        ),
        f"- Test RMSE: `{metrics['test_rmse']:.4f}`",
        f"- Test MAE: `{metrics['test_mae']:.4f}`",
        f"- Test Pearson: `{metrics['test_pearson']:.4f}`",
        f"- Quality verdict: `{metrics['quality_verdict']}`",
        "",
        "## Leakage and Quality Gates",
        "",
        f"- Direct protein overlap count: `{leakage['direct_protein_overlap_count']}`",
        f"- Split status: `{leakage['status']}`",
        f"- Quality blockers: `{metrics['quality_blockers']}`",
        f"- Quality warnings: `{metrics['quality_warnings']}`",
        f"- Expected label scale: `{metrics['label_scale_expected']}`",
        f"- Observed prediction scale: `{metrics['prediction_scale_observed']}`",
        f"- Outlier mass summary: `{metrics['outlier_mass_summary']}`",
        "",
        "## Top Outliers",
        "",
    ]
    for item in outliers[:5]:
        report_lines.append(
            f"- `{item['pdb_id']}` residual `{item['residual']:.4f}` "
            f"(target `{item['target']:.4f}`, prediction `{item['prediction']:.4f}`)"
        )
    _write_text(run_dir / "report.md", "\n".join(report_lines) + "\n")
    mark_stage(
        "reporting",
        "completed",
        detail="Publishable Studio run summary written.",
        artifact_refs=[_artifact_rel(run_dir / "report.md")],
        progress_current=1,
        progress_total=1,
        latest_artifact=_artifact_rel(run_dir / "report.md"),
    )

    _save_json(
        run_dir / "recommendations.json",
        {
            "status": "completed",
            "items": [
                {
                    "level": "warning" if leakage["direct_protein_overlap_count"] else "info",
                    "category": "split_governance",
                    "message": (
                        "Current dataset still contains direct protein overlap across train/test."
                        if leakage["direct_protein_overlap_count"]
                        else "Selected benchmark is aligned with leakage-resistant evaluation."
                    ),
                },
                {
                    "level": "warning" if model_family in {"xgboost", "catboost"} else "info",
                    "category": "backend_resolution",
                    "message": (
                        f"{model_family} is executed through "
                        f"`{training_backend}` in this environment."
                    ),
                },
                *[
                    {
                        "level": "warning",
                        "category": "quality_verdict",
                        "message": (
                            "Prediction scale is not numerically aligned with the expected label range."
                            if blocker == "prediction_scale_mismatch"
                            else "Run quality is blocked by strongly negative test R2."
                            if blocker == "strongly_negative_r2"
                            else "Run quality is blocked by excessive high-error outliers."
                            if blocker == "high_outlier_concentration"
                            else "Run quality is blocked by a large shift between expected and observed prediction centers."
                        ),
                        "action": (
                            "Prefer a narrower governed source family, robust/release-only fallback, or a different model family before treating this run as scientifically credible."
                        ),
                    }
                    for blocker in metrics.get("quality_blockers", [])
                ],
                *[
                    {
                        "level": "info",
                        "category": "quality_remediation",
                        "message": (
                            "Train/test metrics diverge materially; compare a simpler architecture or smaller source scope."
                            if warning == "train_test_metric_divergence"
                            else "Prediction scale is compressed relative to the label range; inspect target coverage and feature sufficiency."
                            if warning == "prediction_scale_compressed"
                            else "Outlier concentration is elevated; inspect dropped-row reasons and split balance before comparing runs."
                            if warning == "elevated_outlier_concentration"
                            else "Test R2 is below zero; compare against release/robust baselines before broadening scope."
                        ),
                    }
                    for warning in metrics.get("quality_warnings", [])
                ],
            ],
            "quality_verdict": metrics.get("quality_verdict"),
            "quality_blockers": metrics.get("quality_blockers", []),
            "quality_warnings": metrics.get("quality_warnings", []),
        },
    )
    _save_json(run_dir / "logs.json", {"lines": log_lines})
    _save_json(run_dir / "artifacts.json", _run_artifact_index(run_dir))
    persist_manifest(
        status="completed",
        active_stage=None,
        completed_at=_utc_now(),
        artifact_refs=list(_run_artifact_index(run_dir).values()),
    )
    return manifest


# Time the launch endpoint is willing to wait for the worker to write
# the initial manifest before returning a launch_timed_out error. The
# previous 2 s ceiling was a frequent source of spurious user errors;
# 30 s comfortably covers the cold-import + hardware-discovery cost.
_LAUNCH_MANIFEST_TIMEOUT_SECONDS = 30.0


def launch_run(spec: ModelStudioPipelineSpec) -> dict[str, Any]:
    """Start a Model Studio run in the background and return as soon as
    the initial ``run_manifest.json`` is visible on disk.

    Uses a :class:`threading.Event` set by the worker rather than a
    busy-wait poll, so it returns within milliseconds of the manifest
    becoming readable instead of paying a fixed polling latency.
    """
    # A new run is about to land on disk -- blow caches so the next
    # ``/runs`` and ``/program-status`` GET observe it.
    invalidate_runtime_caches()
    before = {path.name for path in RUN_DIR.iterdir()} if RUN_DIR.exists() else set()
    holder: dict[str, Any] = {}
    error_holder: list[BaseException] = []
    manifest_ready = threading.Event()

    def _latest_created_run_dir() -> Path | None:
        after = {path.name for path in RUN_DIR.iterdir()} if RUN_DIR.exists() else set()
        created = sorted(after - before, reverse=True)
        return RUN_DIR / created[0] if created else None

    def _mark_failed_run(exc: BaseException) -> None:
        run_dir = _latest_created_run_dir()
        if run_dir is None:
            return
        manifest = _read_manifest(run_dir)
        if not manifest:
            return
        stage_status = _load_json(run_dir / "stage_status.json", {})
        active_stage = manifest.get("active_stage")
        if active_stage and active_stage in stage_status:
            stage_status[active_stage]["status"] = (
                "cancelled" if isinstance(exc, RunCancelledError) else "failed"
            )
            stage_status[active_stage]["detail"] = str(exc)
            stage_status[active_stage]["technical_detail"] = str(exc)
            stage_status[active_stage]["updated_at"] = _utc_now()
            _save_json(run_dir / "stage_status.json", stage_status)
        manifest["status"] = (
            "cancelled" if isinstance(exc, RunCancelledError) else "failed"
        )
        manifest["active_stage"] = None
        terminal_key = "cancelled_at" if isinstance(exc, RunCancelledError) else "failed_at"
        manifest[terminal_key] = _utc_now()
        manifest["failure"] = str(exc)
        _write_manifest(run_dir, manifest)
        log_payload = _load_json(run_dir / "logs.json", {"lines": []})
        log_lines = list(log_payload.get("lines", []))
        _append_log(log_lines, f"Background worker failed: {exc}")
        _save_json(run_dir / "logs.json", {"lines": log_lines})

    def _worker() -> None:
        try:
            manifest = _execute_run_sync(spec, manifest_ready=manifest_ready)
            holder["manifest"] = manifest
        except BaseException as exc:
            # This IS the production error path -- exercised by every
            # failed launch. Coverage should measure it.
            _mark_failed_run(exc)
            error_holder.append(exc)
        finally:
            # Always release the launcher even if the worker crashed
            # before signalling. The launcher then inspects
            # ``error_holder`` to decide whether to raise.
            manifest_ready.set()

    thread = threading.Thread(
        target=_worker,
        name=f"model-studio-runner-{spec.pipeline_id.split(':')[-1]}",
        daemon=True,
    )
    thread.start()

    signalled = manifest_ready.wait(timeout=_LAUNCH_MANIFEST_TIMEOUT_SECONDS)

    def _register_thread(manifest: dict[str, Any]) -> None:
        # Use the shared registry helper from ``_persistence.py`` so
        # thread eviction policy stays in one place.
        _register_run_thread(_clean_text(manifest.get("run_id")), thread)

    if error_holder:
        # Worker died early. If we have a partial manifest on disk,
        # return it so the GUI can show the failure; otherwise raise.
        run_dir = _latest_created_run_dir()
        if run_dir is not None:
            manifest = _read_manifest(run_dir)
            if manifest:
                _register_thread(manifest)
                return manifest
        raise error_holder[0]

    if "manifest" in holder:
        manifest = holder["manifest"]
        _register_thread(manifest)
        return manifest

    if signalled:
        run_dir = _latest_created_run_dir()
        if run_dir is not None:
            manifest = _read_manifest(run_dir)
            if manifest:
                _register_thread(manifest)
                return manifest

    raise RuntimeError(
        "Run launch timed out before the background manifest became visible "
        f"(waited {_LAUNCH_MANIFEST_TIMEOUT_SECONDS:.0f} s)."
    )


def list_runs() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not RUN_DIR.exists():
        return items
    for path in sorted(RUN_DIR.iterdir(), key=lambda item: item.name, reverse=True):
        if not path.is_dir():
            continue
        manifest = _read_manifest_without_recovery(path / "run_manifest.json", _read_manifest(path))
        if manifest:
            items.append(manifest)
    return items


def _resolve_run_dir(run_id: str) -> Path:
    """Return the on-disk directory for ``run_id`` after validating that
    the id is a safe filename component and that the resolved path is
    contained under :data:`RUN_DIR`. Defends against ``..`` traversal,
    absolute-path injection, NUL bytes, and arbitrary slashes.
    """
    if not run_id or not _RUN_ID_PATTERN.match(run_id):
        raise FileNotFoundError(run_id)
    candidate = (RUN_DIR / run_id).resolve()
    base = RUN_DIR.resolve()
    if not candidate.is_relative_to(base):
        raise FileNotFoundError(run_id)
    return candidate


def load_run(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    if not run_dir.is_dir():
        raise FileNotFoundError(run_id)
    return {
        "run_manifest": _read_manifest_without_recovery(
            run_dir / "run_manifest.json",
            _read_manifest(run_dir),
        ),
        "execution_graph": _load_json(run_dir / "execution_graph.json", {}),
        "stage_status": _load_json(run_dir / "stage_status.json", {}),
        "artifacts": _load_json(run_dir / "artifacts.json", {}),
        "metrics": _load_json(run_dir / "metrics.json", {}),
        "model_details": _load_json(run_dir / "model_details.json", {}),
        "analysis": _load_json(run_dir / "analysis.json", {}),
        "outliers": _load_json(run_dir / "outliers.json", {}),
        "recommendations": _load_json(run_dir / "recommendations.json", {}),
        "logs": _load_json(run_dir / "logs.json", {"lines": []}),
    }


def load_run_artifacts(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    if not run_dir.is_dir():
        raise FileNotFoundError(run_id)
    return _load_json(run_dir / "artifacts.json", {})


def load_run_logs(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    if not run_dir.is_dir():
        raise FileNotFoundError(run_id)
    return _load_json(run_dir / "logs.json", {"lines": []})


def cancel_run(run_id: str) -> dict[str, Any]:
    run_dir = _resolve_run_dir(run_id)
    manifest = _read_manifest_without_recovery(
        run_dir / "run_manifest.json",
        _read_manifest(run_dir),
    )
    if not manifest:
        raise FileNotFoundError(run_id)
    if manifest.get("status") in {"running", "queued"}:
        _write_run_control(
            run_dir,
            {
                "cancel_requested": True,
                "resume_requested": False,
                "updated_at": _utc_now(),
            },
        )
        manifest["notes"] = [
            *manifest.get("notes", []),
            "Cancellation has been requested and will be applied at the next stage boundary.",
        ]
        _write_manifest(run_dir, manifest)
    elif manifest.get("status") not in {"completed", "cancelled"}:
        manifest["status"] = "cancelled"
        manifest["active_stage"] = None
        manifest["cancelled_at"] = _utc_now()
        _write_manifest(run_dir, manifest)
    # Manifest state changed; nuke caches so the next ``/runs`` GET
    # reflects the new status rather than the pre-cancel snapshot.
    invalidate_runtime_caches()
    return manifest


def compare_runs(run_ids: list[str]) -> dict[str, Any]:
    runs = [load_run(run_id) for run_id in run_ids]
    comparison_items = []
    for item in runs:
        manifest = item["run_manifest"]
        metrics = item["metrics"]
        model_details = item.get("model_details", {}) or {}
        comparison_items.append(
            {
                "run_id": manifest.get("run_id"),
                "pipeline_id": manifest.get("pipeline_id"),
                "status": manifest.get("status"),
                "dataset_ref": manifest.get("dataset_ref"),
                "training_set_build_id": manifest.get("training_set_build_id"),
                "requested_model_family": metrics.get("requested_model_family")
                or manifest.get("model_family")
                or model_details.get("requested_model_family"),
                "requested_label_type": metrics.get("requested_label_type"),
                "resolved_label_type": metrics.get("resolved_label_type"),
                "label_origin": metrics.get("label_origin"),
                "label_conversion_provenance": metrics.get("label_conversion_provenance"),
                "assay_family_disclosure": metrics.get("assay_family_disclosure"),
                "resolved_backend": metrics.get("resolved_backend")
                or model_details.get("resolved_backend")
                or manifest.get("resolved_training_backend"),
                "split_strategy": metrics.get("split_strategy"),
                "requested_dataset_refs": metrics.get("requested_dataset_refs"),
                "requested_hardware_preset": metrics.get("requested_hardware_preset")
                or model_details.get("requested_hardware_preset")
                or manifest.get("requested_hardware_preset"),
                "resolved_hardware_preset": metrics.get("resolved_hardware_preset")
                or model_details.get("resolved_hardware_preset")
                or manifest.get("resolved_hardware_preset"),
                "resolved_execution_device": metrics.get("resolved_execution_device")
                or model_details.get("resolved_execution_device")
                or manifest.get("resolved_execution_device"),
                "graph_node_granularity": metrics.get("graph_node_granularity")
                or model_details.get("graph_node_granularity")
                or manifest.get("graph_node_granularity"),
                "requested_uncertainty_head": metrics.get("requested_uncertainty_head"),
                "uncertainty_summary": metrics.get("uncertainty_summary"),
                "sequence_embedding_summary": metrics.get("sequence_embedding_summary")
                or model_details.get("sequence_embedding_summary")
                or {"enabled": False},
                "test_rmse": metrics.get("test_rmse"),
                "test_mae": metrics.get("test_mae"),
                "test_r2": metrics.get("test_r2"),
                "test_pearson": metrics.get("test_pearson"),
                "quality_verdict": metrics.get("quality_verdict"),
                "quality_blockers": metrics.get("quality_blockers"),
                "quality_warnings": metrics.get("quality_warnings"),
                "label_scale_expected": metrics.get("label_scale_expected"),
                "prediction_scale_observed": metrics.get("prediction_scale_observed"),
                "outlier_mass_summary": metrics.get("outlier_mass_summary"),
                "chart_family": item.get("analysis", {}).get("chart_family"),
                "outlier_count": len(item.get("outliers", {}).get("items", [])),
                "analysis_artifact": item.get("artifacts", {}).get("analysis.json"),
            }
        )
    ranked = sorted(
        comparison_items,
        key=lambda entry: float(entry.get("test_rmse") or 1e9),
    )
    return {
        "items": comparison_items,
        "best_run_id": ranked[0]["run_id"] if ranked else None,
        "ranking_metric": "test_rmse",
    }


__all__ = [
    "RUN_DIR",
    "TRAINING_SET_BUILD_DIR",
    "build_training_set",
    "cancel_run",
    "compare_runs",
    "launch_run",
    "list_known_datasets",
    "list_training_set_builds",
    "list_runs",
    "load_training_set_build",
    "load_run",
    "load_run_artifacts",
    "load_run_logs",
    "preview_training_set_request",
]
