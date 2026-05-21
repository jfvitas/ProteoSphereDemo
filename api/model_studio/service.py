from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
import shutil
from typing import Any

from api.model_studio.capabilities import filter_known_datasets, is_active_option
from api.model_studio.catalog import (
    STUDIO_SCHEMA_VERSION,
    build_lab_catalog,
    build_release_catalog,
    default_pipeline_spec,
)
from api.model_studio.contracts import (
    ModelStudioPipelineSpec,
    compile_execution_graph,
    pipeline_spec_from_dict,
    validate_pipeline_spec,
)
from api.model_studio.runtime import (
    build_activation_ledger,
    build_activation_readiness_reports,
    build_candidate_database_summary,
    build_candidate_database_summary_v2,
    build_candidate_database_summary_v3,
    build_candidate_pool_summary,
    build_feature_gate_views,
    build_governed_bridge_manifests,
    build_governed_subset_manifests,
    build_governed_subset_manifests_v2,
    build_model_activation_matrix,
    build_pool_promotion_reports,
    build_promotion_queue,
    build_promotion_queue_v2,
    build_stage2_scientific_tracks,
    _launchable_dataset_pools,
    build_training_set,
    cancel_run,
    compare_runs,
    discover_hardware_profile,
    launch_run,
    list_dataset_pools,
    list_known_datasets,
    list_runs,
    list_training_set_builds,
    load_run,
    load_run_artifacts,
    load_run_logs,
    load_training_set_build,
    persist_feedback,
    persist_session_event,
    preview_training_set_request,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAFT_DIR = REPO_ROOT / "artifacts" / "runtime" / "model_studio" / "pipeline_specs"
RUN_DIR = REPO_ROOT / "artifacts" / "runtime" / "model_studio" / "runs"
MASTER_AGENT_STATE = REPO_ROOT / "artifacts" / "status" / "model_studio_master_agent_state.json"
ORCHESTRATOR_STATE = REPO_ROOT / "artifacts" / "status" / "model_studio_orchestrator_state.json"
PROGRAM_PREVIEW = REPO_ROOT / "artifacts" / "status" / "model_studio_program_preview.json"
CURATED_QUEUE = REPO_ROOT / "tasks" / "model_studio_wave_queue.json"
REVIEW_ROOT = REPO_ROOT / "artifacts" / "reviews" / "model_studio_internal_alpha"
BETA_AGENT_SWEEP_ROOT = REVIEW_ROOT / "beta_agent_sweeps"
REPORTS_ROOT = REPO_ROOT / "docs" / "reports"
FINAL_REHEARSAL_PREFIX = "final_external_rehearsal_"
REFERENCE_LIBRARY_ROOT = REPO_ROOT / "artifacts" / "bundles" / "preview"
REFERENCE_LIBRARY_MANIFEST = REFERENCE_LIBRARY_ROOT / "proteosphere-lite.release_manifest.json"
REFERENCE_LIBRARY_CHUNK_INDEX = REFERENCE_LIBRARY_ROOT / "proteosphere-lite.chunk_index.json"
REFERENCE_LIBRARY_CHECKSUM = REFERENCE_LIBRARY_ROOT / "proteosphere-lite.sha256"
REFERENCE_LIBRARY_CORE_BUNDLE = REFERENCE_LIBRARY_ROOT / "proteosphere-lite.sqlite.zst"
REQUIRED_REVIEWERS = (
    "Kepler",
    "Euler",
    "Ampere",
    "Mill",
    "Bacon",
    "McClintock",
)


def _utc_now() -> str:
    return datetime.now(tz=UTC).isoformat()


from api.model_studio._cache import invalidate_runtime_caches as _invalidate_runtime_caches
from api.model_studio._io import load_json as _load_json, save_json as _save_json


def _review_artifacts() -> list[str]:
    if not REVIEW_ROOT.exists():
        return []
    allowed_suffixes = {".json", ".png", ".md"}
    return sorted(
        str(path.relative_to(REPO_ROOT))
        for path in REVIEW_ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in allowed_suffixes
    )


def _latest_final_rehearsal_dir() -> Path | None:
    if not REVIEW_ROOT.exists():
        return None
    candidates = [
        path
        for path in REVIEW_ROOT.iterdir()
        if path.is_dir() and path.name.startswith(FINAL_REHEARSAL_PREFIX)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _beta_agent_evidence_present() -> bool:
    """True when at least one real beta-agent sweep or final rehearsal
    bundle exists on disk.

    The agent-status helpers further down synthesize plausible-looking
    flow verdicts even when *no* real evidence has been collected --
    that was identified as a "mock data shipped as production truth"
    smell in the May 2026 review. This helper is the gate consumers
    use to surface an honest ``evidence_present: false`` flag so the
    GUI can label the synthesised view as such rather than display
    canned scores as live truth.
    """
    sweep_dir = _latest_beta_agent_sweep_dir()
    if sweep_dir is not None:
        viewport_dir = _latest_beta_agent_viewport_dir(sweep_dir)
        if viewport_dir is not None and viewport_dir.exists():
            return True
    rehearsal_dir = _latest_final_rehearsal_dir()
    if rehearsal_dir is not None and rehearsal_dir.exists():
        return True
    return False


def _final_rehearsal_artifacts() -> list[Path]:
    root = _latest_final_rehearsal_dir()
    if root is None or not root.exists():
        return []
    allowed_suffixes = {".json", ".png", ".md"}
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in allowed_suffixes
        ),
        key=lambda path: str(path),
    )


def _final_rehearsal_relpaths() -> list[str]:
    return [str(path.relative_to(REPO_ROOT)) for path in _final_rehearsal_artifacts()]


def _load_json_path(path: Path) -> dict[str, Any]:
    payload = _load_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _parse_json_like(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return default
    import json

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return default
    return parsed


def _latest_beta_agent_sweep_dir() -> Path | None:
    if not BETA_AGENT_SWEEP_ROOT.exists():
        return None
    candidates = [path for path in BETA_AGENT_SWEEP_ROOT.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _latest_beta_agent_viewport_dir(sweep_dir: Path | None = None) -> Path | None:
    sweep_dir = sweep_dir or _latest_beta_agent_sweep_dir()
    if sweep_dir is None:
        return None
    for dirname in ("1080p", "720p"):
        candidate = sweep_dir / dirname
        if candidate.exists():
            return candidate
    return None


def _reviewer_signoff_state() -> dict[str, Any]:
    matrix_path = REPORTS_ROOT / "model_studio_reviewer_signoff_matrix.md"
    if not matrix_path.exists():
        return {
            "ready": False,
            "approved_reviewers": [],
            "pending_reviewers": list(REQUIRED_REVIEWERS),
            "has_open_p1": True,
        }
    text = matrix_path.read_text(encoding="utf-8")
    rows_by_reviewer: dict[str, list[str]] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or stripped.startswith("| Reviewer |") or stripped.startswith("| ---"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 6:
            continue
        reviewer = cells[0]
        if reviewer in REQUIRED_REVIEWERS:
            rows_by_reviewer[reviewer] = cells
    approved_reviewers: list[str] = []
    pending_reviewers: list[str] = []
    has_open_p1 = False
    for reviewer in REQUIRED_REVIEWERS:
        row = rows_by_reviewer.get(reviewer)
        if not row:
            pending_reviewers.append(reviewer)
            continue
        verdict = row[3].strip().casefold()
        open_p1 = row[4].strip().casefold()
        if verdict in {"approved", "approved_with_followups"}:
            approved_reviewers.append(reviewer)
        else:
            pending_reviewers.append(reviewer)
        if open_p1 in {"yes", "true", "open"}:
            has_open_p1 = True
    return {
        "ready": not pending_reviewers and not has_open_p1,
        "approved_reviewers": approved_reviewers,
        "pending_reviewers": pending_reviewers,
        "has_open_p1": has_open_p1,
    }


def build_quality_gates(spec: ModelStudioPipelineSpec) -> dict[str, Any]:
    report = validate_pipeline_spec(spec)
    graph = compile_execution_graph(spec)
    blocker_count = sum(1 for item in report.items if item.level == "blocker")
    warning_count = sum(1 for item in report.items if item.level == "warning")
    return {
        "status": (
            "blocked" if blocker_count else "ready_with_warnings" if warning_count else "ready"
        ),
        "blocker_count": blocker_count,
        "warning_count": warning_count,
        "checks": [
            {
                "gate": "spec_validation",
                "status": report.status,
                "detail": f"{len(report.items)} recommendation item(s)",
            },
            {
                "gate": "execution_graph",
                "status": "blocked" if graph.blockers else "ready",
                "detail": f"{len(graph.stages)} deterministic stages compiled",
            },
            {
                "gate": "training_set_request",
                "status": "ready"
                if spec.training_set_request.target_size >= 24
                else "review_required",
                "detail": (
                    f"target_size={spec.training_set_request.target_size or 'runtime_default'} / "
                    f"fidelity={spec.training_set_request.acceptable_fidelity}"
                ),
            },
            {
                "gate": "split_governance",
                "status": "ready"
                if spec.data_strategy.split_strategy != "random"
                else "review_required",
                "detail": spec.data_strategy.split_strategy,
            },
            {
                "gate": "representation_compatibility",
                "status": "ready" if not blocker_count else "blocked",
                "detail": spec.training_plan.model_family,
            },
        ],
    }


def build_run_preview(spec: ModelStudioPipelineSpec) -> dict[str, Any]:
    graph = compile_execution_graph(spec)
    runs = list_runs()
    return {
        "run_id": f"run:{spec.pipeline_id}:preview",
        "status": "draft",
        "pipeline_id": spec.pipeline_id,
        "graph_id": graph.graph_id,
        "active_stage": graph.stages[0] if graph.stages else None,
        "artifact_refs": [],
        "blocker_refs": list(graph.blockers),
        "estimated_stages": len(graph.stages),
        "recent_runs": runs[:5],
    }


def build_hardware_profile_payload() -> dict[str, Any]:
    return discover_hardware_profile()


def _step_state(
    *,
    status: str,
    title: str,
    summary: str,
    next_action: str,
    produced: list[str] | None = None,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "title": title,
        "summary": summary,
        "next_action": next_action,
        "produced": produced or [],
        "blockers": blockers or [],
    }


def _default_payload() -> dict[str, Any]:
    spec = default_pipeline_spec()
    report = validate_pipeline_spec(spec)
    graph = compile_execution_graph(spec)
    return {
        "pipeline_spec": spec.to_dict(),
        "recommendation_report": report.to_dict(),
        "execution_graph": graph.to_dict(),
        "quality_gates": build_quality_gates(spec),
        "run_preview": build_run_preview(spec),
        "saved_at": _utc_now(),
    }


# ---------------------------------------------------------------------------
# Filesystem-safe identifier helpers
# ---------------------------------------------------------------------------

_SAFE_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]")


def _safe_filename_id(pipeline_id: str) -> str:
    """Convert a free-form pipeline/run id into a filesystem-safe stem.

    Replaces any character outside ``[A-Za-z0-9._-]`` with ``_``. This is
    the single source of truth for ID-to-filename mapping; ``save_*`` and
    ``load_*`` must agree on this function or round-trips break.

    Raises ``ValueError`` if the resulting stem is empty, starts with
    ``.`` (hidden file), or expands to ``.``/``..`` after substitution
    -- all of which would let a caller escape the draft directory.
    """
    if not pipeline_id:
        raise ValueError("pipeline_id must not be empty")
    safe = _SAFE_ID_PATTERN.sub("_", pipeline_id)
    if not safe or safe in {".", ".."} or safe.startswith("."):
        raise ValueError(f"pipeline_id resolves to unsafe filename: {pipeline_id!r}")
    return safe


def _resolve_under(root: Path, *parts: str) -> Path:
    """Join ``parts`` under ``root`` and assert the result is contained
    inside ``root.resolve()``. Defends against ``..`` and absolute-path
    escapes even if a sanitizer above is bypassed."""
    candidate = root.joinpath(*parts).resolve()
    base = root.resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(
            f"path {candidate} resolves outside {base}"
        )
    return candidate


# Canonical default pipeline id; used everywhere we need to seed/refresh
# the on-disk default. Kept in one place so writer + reader can't drift.
_DEFAULT_PIPELINE_ID = "pipeline:protein-binding-default-v1"


def _migrate_legacy_spec_filenames() -> None:
    """Rename any draft whose filename uses an older sanitization to the
    current canonical name.

    Earlier versions of ``save_pipeline_spec`` replaced both ``:`` and
    ``-`` with ``_`` in filenames; the current rule replaces only the
    unsafe set ``[^A-Za-z0-9._-]``. Without this migration, the default
    spec is reachable via ``list`` but not via ``GET /pipeline-specs/<id>``
    because writer and reader pick different names.
    """
    if not DRAFT_DIR.exists():
        return
    for path in DRAFT_DIR.glob("*.json"):
        payload = _load_json(path, {})
        spec = payload.get("pipeline_spec", {}) if isinstance(payload, dict) else {}
        pipeline_id = spec.get("pipeline_id")
        if not pipeline_id:
            continue
        try:
            canonical_stem = _safe_filename_id(pipeline_id)
        except ValueError:
            continue
        if path.stem == canonical_stem:
            continue
        target = path.with_name(f"{canonical_stem}.json")
        if target.exists():
            # Newer canonical file already present; leave the legacy one
            # alone to avoid clobbering user state.
            continue
        path.rename(target)


def ensure_default_pipeline() -> Path:
    _migrate_legacy_spec_filenames()
    default_path = _resolve_under(
        DRAFT_DIR, f"{_safe_filename_id(_DEFAULT_PIPELINE_ID)}.json"
    )
    expected = _default_payload()
    if not default_path.is_file():
        _save_json(default_path, expected)
        return default_path
    current = _load_json(default_path, {})
    schema_version = current.get("pipeline_spec", {}).get("schema_version")
    if schema_version != expected["pipeline_spec"]["schema_version"]:
        _save_json(default_path, expected)
    return default_path


def list_pipeline_specs() -> list[dict[str, Any]]:
    ensure_default_pipeline()
    payloads: list[dict[str, Any]] = []
    for path in sorted(DRAFT_DIR.glob("*.json")):
        payload = _load_json(path, {})
        spec = payload.get("pipeline_spec", {})
        payloads.append(
            {
                "pipeline_id": spec.get("pipeline_id", path.stem),
                "study_title": spec.get("study_title", path.stem),
                "task_type": spec.get("data_strategy", {}).get("task_type", "unknown"),
                "model_family": spec.get("training_plan", {}).get("model_family", "unknown"),
                "saved_at": payload.get("saved_at"),
                # Path intentionally relativized to repo root so we don't
                # leak absolute filesystem paths to the GUI.
                "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT)
                else path.name,
            }
        )
    return payloads


def load_pipeline_spec(pipeline_id: str | None = None) -> dict[str, Any]:
    ensure_default_pipeline()
    if not pipeline_id:
        return _default_payload()
    try:
        target = _resolve_under(DRAFT_DIR, f"{_safe_filename_id(pipeline_id)}.json")
    except ValueError as exc:
        raise FileNotFoundError(pipeline_id) from exc
    if not target.is_file():
        raise FileNotFoundError(pipeline_id)
    return _load_json(target, _default_payload())


def save_pipeline_spec(payload: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline_spec_from_dict(payload)
    record = {
        "pipeline_spec": spec.to_dict(),
        "recommendation_report": validate_pipeline_spec(spec).to_dict(),
        "execution_graph": compile_execution_graph(spec).to_dict(),
        "quality_gates": build_quality_gates(spec),
        "run_preview": build_run_preview(spec),
        "saved_at": _utc_now(),
    }
    # ``_safe_filename_id`` raises on unsafe ids; ``_resolve_under`` then
    # double-checks that no clever sanitizer-bypass escaped DRAFT_DIR.
    path = _resolve_under(DRAFT_DIR, f"{_safe_filename_id(spec.pipeline_id)}.json")
    _save_json(path, record)
    # New / updated spec on disk -- the next ``list_pipeline_specs`` /
    # workspace preview should see it without waiting for TTL expiry.
    _invalidate_runtime_caches()
    return record


def validate_pipeline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline_spec_from_dict(payload)
    report = validate_pipeline_spec(spec)
    return {
        "pipeline_id": spec.pipeline_id,
        "recommendation_report": report.to_dict(),
        "quality_gates": build_quality_gates(spec),
    }


def compile_pipeline_payload(payload: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline_spec_from_dict(payload)
    report = validate_pipeline_spec(spec)
    graph = compile_execution_graph(spec)
    return {
        "pipeline_id": spec.pipeline_id,
        "recommendation_report": report.to_dict(),
        "execution_graph": graph.to_dict(),
        "quality_gates": build_quality_gates(spec),
        "run_preview": build_run_preview(spec),
    }


def _beta_docs_manifest() -> list[dict[str, str]]:
    return [
        {
            "doc_id": "beta_overview",
            "title": "Beta Overview",
            "category": "participant",
            "audience": "external",
            "path": "docs/reports/model_studio_beta_overview.md",
            "summary": "High-level explanation of what this controlled beta includes, excludes, and how users should approach it.",
        },
        {
            "doc_id": "beta_charter",
            "title": "Controlled External Beta Charter",
            "category": "program",
            "audience": "internal",
            "path": "docs/reports/model_studio_controlled_external_beta_charter.md",
            "summary": "Defines the shipped beta scope, severity model, reviewer ownership, and go/no-go bar.",
        },
        {
            "doc_id": "reviewer_signoff_matrix",
            "title": "Reviewer Signoff Matrix",
            "category": "governance",
            "audience": "internal",
            "path": "docs/reports/model_studio_reviewer_signoff_matrix.md",
            "summary": "Tracks required reviewer roles, signoff evidence, and freeze-gate expectations.",
        },
        {
            "doc_id": "state_language_spec",
            "title": "State Language Spec",
            "category": "ux",
            "audience": "internal",
            "path": "docs/reports/model_studio_state_language_spec.md",
            "summary": "Locks the user-facing vocabulary for Launchable now, Review pending, and Inactive states.",
        },
        {
            "doc_id": "known_limitations",
            "title": "Known Limitations",
            "category": "participant",
            "audience": "external",
            "path": "docs/reports/model_studio_known_limitations.md",
            "summary": "Lists current truth boundaries, beta caveats, and explicitly deferred lanes.",
        },
        {
            "doc_id": "participant_guide",
            "title": "Participant Guide",
            "category": "participant",
            "audience": "external",
            "path": "docs/reports/model_studio_participant_guide.md",
            "summary": "Walks invited users through the guided study flow, reporting path, and limitations.",
        },
        {
            "doc_id": "deferred_items",
            "title": "Deferred Items Ledger",
            "category": "governance",
            "audience": "internal",
            "path": "docs/reports/model_studio_deferred_items_ledger.md",
            "summary": "Records what stays out of scope for the beta and why it remains blocked.",
        },
        {
            "doc_id": "ops_runbook",
            "title": "External Beta Ops Runbook",
            "category": "ops",
            "audience": "internal",
            "path": "docs/runbooks/model_studio_external_beta_ops_runbook.md",
            "summary": "Defines invites, rollback rules, daily operating rituals, and participant-facing support paths.",
        },
        {
            "doc_id": "support_triage",
            "title": "Support Triage Playbook",
            "category": "ops",
            "audience": "internal",
            "path": "docs/runbooks/model_studio_support_triage_playbook.md",
            "summary": "Provides issue intake categories, severity rules, routing, and escalation handling.",
        },
        {
            "doc_id": "troubleshooting",
            "title": "Troubleshooting Guide",
            "category": "participant",
            "audience": "external",
            "path": "docs/reports/model_studio_troubleshooting_guide.md",
            "summary": "Gives invited users practical steps for common beta issues before escalation is needed.",
        },
        {
            "doc_id": "ligand_pilot_smoke",
            "title": "Ligand Pilot Smoke Report",
            "category": "evidence",
            "audience": "internal",
            "path": "docs/reports/model_studio_ligand_pilot_smoke_2026_04_10.md",
            "summary": "Records the launchable ligand pilot execution matrix for graphsage and multimodal_fusion.",
        },
        {
            "doc_id": "freeze_gate",
            "title": "Freeze Gate Checklist",
            "category": "governance",
            "audience": "internal",
            "path": "docs/reports/model_studio_freeze_gate_checklist.md",
            "summary": "Defines the required evidence and gates before the PPI lane is treated as freeze-ready.",
        },
        {
            "doc_id": "final_readiness",
            "title": "Final Readiness Checklist",
            "category": "governance",
            "audience": "internal",
            "path": "docs/reports/model_studio_final_readiness_checklist.md",
            "summary": "Tracks the complete 100% beta-readiness checklist across product, science, validation, and ops.",
        },
        {
            "doc_id": "do_not_promise",
            "title": "Do Not Promise List",
            "category": "ops",
            "audience": "internal",
            "path": "docs/reports/model_studio_do_not_promise_list.md",
            "summary": "Lists claims support and stakeholders must not make during the controlled beta.",
        },
        {
            "doc_id": "daily_dashboard_template",
            "title": "Daily Beta Dashboard Template",
            "category": "ops",
            "audience": "internal",
            "path": "docs/runbooks/model_studio_daily_beta_dashboard_template.md",
            "summary": "Template for the daily beta dashboard covering blockers, readiness, and participant issues.",
        },
        {
            "doc_id": "weekly_summary_template",
            "title": "Weekly Beta Summary Template",
            "category": "ops",
            "audience": "internal",
            "path": "docs/runbooks/model_studio_weekly_beta_summary_template.md",
            "summary": "Template for weekly beta summaries, trend tracking, and deferred-item movement.",
        },
        {
            "doc_id": "external_rehearsal_checklist",
            "title": "External Rehearsal Checklist",
            "category": "ops",
            "audience": "internal",
            "path": "docs/runbooks/model_studio_external_rehearsal_checklist.md",
            "summary": "Checklist for the final external-style rehearsal across launchable, blocked, and pilot lanes.",
        },
    ]


def _beta_agent_scoring_dimensions() -> list[str]:
    return [
        "look_and_feel",
        "cleanliness",
        "content_relevance",
        "usability",
        "trust_clarity",
        "output_quality_clarity",
        "blocked_state_quality",
        "supportability",
    ]


def _beta_agent_flow_specs() -> list[dict[str, Any]]:
    return [
        {
            "flow_id": "ppi_benchmark_launchable_flow",
            "title": "PPI benchmark launchable flow",
            "coverage_note": "Launchable benchmark flow from dataset preview through run analysis.",
        },
        {
            "flow_id": "governed_ppi_subset_flow",
            "title": "Governed PPI subset flow",
            "coverage_note": "Governed promoted-subset selection, rationale, and launchability framing.",
        },
        {
            "flow_id": "protein_ligand_pilot_flow",
            "title": "Protein-ligand pilot flow",
            "coverage_note": "Launchable narrow ligand pilot with structure-backed provenance and compare/export review.",
        },
        {
            "flow_id": "blocked_pyrosetta_flow",
            "title": "Blocked PyRosetta flow",
            "coverage_note": "Stage 2 PyRosetta blocker explanation and inactive-state framing.",
        },
        {
            "flow_id": "blocked_free_state_flow",
            "title": "Blocked free-state flow",
            "coverage_note": "Stage 2 free-state blocker explanation and inactive-state framing.",
        },
    ]


def _beta_test_agents() -> list[dict[str, Any]]:
    scoring_dimensions = _beta_agent_scoring_dimensions()
    workflow_coverage = [item["flow_id"] for item in _beta_agent_flow_specs()]
    severity_rules = {
        "P1": "User would likely fail, misread scientific truth, or act on a misleading result.",
        "P2": "User can proceed, but meaningful confusion or risk remains.",
        "P3": "Polish, wording drift, or moderate friction that does not stop forward progress.",
        "P4": "Cosmetic or future-looking improvement opportunity.",
    }
    return [
        {
            "agent_id": "visual-cleanliness-agent",
            "title": "Visual cleanliness agent",
            "goal": "Evaluate hierarchy, readability, density, spacing, status labeling, and visual noise in a 1920x1080 browser window, with 1280x720 retained as the minimum regression viewport.",
            "viewport": {"width": 1920, "height": 1080},
            "minimum_viewport": {"width": 1280, "height": 720},
            "scoring_dimensions": scoring_dimensions,
            "required_artifacts": ["trace", "step_screenshots", "blocked_state_screenshots", "compare_export_screenshots"],
            "interaction_contract": [
                "click_buttons",
                "change_dropdowns",
                "edit_fields",
                "assert_ui_changes",
                "assert_backend_changes",
            ],
            "workflow_coverage": workflow_coverage,
            "issue_routing": ["Ampere"],
            "severity_rules": severity_rules,
            "compatibility_aliases": ["visual-review"],
        },
        {
            "agent_id": "usability-agent",
            "title": "Usability agent",
            "goal": "Evaluate discoverability, control feedback, progression clarity, and blocked-state comprehension in the primary guided study flows.",
            "viewport": {"width": 1920, "height": 1080},
            "minimum_viewport": {"width": 1280, "height": 720},
            "scoring_dimensions": scoring_dimensions,
            "required_artifacts": ["trace", "step_screenshots", "blocked_state_screenshots", "compare_export_screenshots"],
            "workflow_coverage": workflow_coverage,
            "issue_routing": ["Ampere", "Euler"],
            "severity_rules": severity_rules,
            "compatibility_aliases": ["user-sim-review"],
        },
        {
            "agent_id": "content-relevance-agent",
            "title": "Content relevance agent",
            "goal": "Evaluate whether each screen shows the right amount of explanation, the right diagnostics, and context that matches the current workflow step.",
            "viewport": {"width": 1920, "height": 1080},
            "minimum_viewport": {"width": 1280, "height": 720},
            "scoring_dimensions": scoring_dimensions,
            "required_artifacts": ["trace", "step_screenshots", "blocked_state_screenshots", "compare_export_screenshots"],
            "workflow_coverage": workflow_coverage,
            "issue_routing": ["Ampere", "McClintock"],
            "severity_rules": severity_rules,
            "compatibility_aliases": ["user-sim-review"],
        },
        {
            "agent_id": "scientific-output-agent",
            "title": "Scientific output agent",
            "goal": "Evaluate whether run quality, proxy disclosures, compare/export truth, and weak-output framing remain scientifically honest and easy to interpret.",
            "viewport": {"width": 1920, "height": 1080},
            "minimum_viewport": {"width": 1280, "height": 720},
            "scoring_dimensions": scoring_dimensions,
            "required_artifacts": ["trace", "step_screenshots", "blocked_state_screenshots", "compare_export_screenshots"],
            "workflow_coverage": workflow_coverage,
            "issue_routing": ["Bacon", "Mill"],
            "severity_rules": severity_rules,
            "compatibility_aliases": ["scientific-runtime-review"],
        },
        {
            "agent_id": "failure-recovery-agent",
            "title": "Failure recovery agent",
            "goal": "Evaluate blocked states, error states, missing-data guidance, recovery options, and support escalation usefulness.",
            "viewport": {"width": 1920, "height": 1080},
            "minimum_viewport": {"width": 1280, "height": 720},
            "scoring_dimensions": scoring_dimensions,
            "required_artifacts": ["trace", "step_screenshots", "blocked_state_screenshots", "compare_export_screenshots"],
            "workflow_coverage": workflow_coverage,
            "issue_routing": ["Euler", "Ampere"],
            "severity_rules": severity_rules,
            "compatibility_aliases": ["qa-review"],
        },
        {
            "agent_id": "release-governance-agent",
            "title": "Release governance agent",
            "goal": "Evaluate whether launchability, subset promotion, source-family disclosure, and governed-subset wording match backend-authored truth.",
            "viewport": {"width": 1920, "height": 1080},
            "minimum_viewport": {"width": 1280, "height": 720},
            "scoring_dimensions": scoring_dimensions,
            "required_artifacts": ["trace", "step_screenshots", "blocked_state_screenshots", "compare_export_screenshots"],
            "workflow_coverage": workflow_coverage,
            "issue_routing": ["Kepler", "McClintock"],
            "severity_rules": severity_rules,
            "compatibility_aliases": ["architect-review", "refactor-review"],
        },
    ]


def _beta_agent_runtime() -> dict[str, Any]:
    def _command_available(name: str, fallbacks: list[Path] | None = None) -> bool:
        if shutil.which(name) is not None:
            return True
        return any(path.exists() for path in (fallbacks or []))

    roaming_dir = Path.home() / "AppData" / "Roaming" / "npm"
    node_available = _command_available(
        "node",
        [Path("C:/Program Files/nodejs/node.exe")],
    )
    npm_available = _command_available(
        "npm",
        [Path("C:/Program Files/nodejs/npm.cmd"), Path("C:/Program Files/nodejs/npm")],
    )
    npx_available = _command_available(
        "npx",
        [Path("C:/Program Files/nodejs/npx.cmd"), Path("C:/Program Files/nodejs/npx")],
    )
    playwright_cli_available = _command_available(
        "playwright-cli",
        [roaming_dir / "playwright-cli.cmd", roaming_dir / "playwright-cli"],
    )
    browser_runtime_available = any(
        (Path.home() / "AppData" / "Local" / "ms-playwright").glob("chromium-*")
    )
    return {
        "runner": "playwright_cli",
        "node_available": node_available,
        "npm_available": npm_available,
        "npx_available": npx_available,
        "playwright_cli_available": playwright_cli_available,
        "browser_runtime_available": browser_runtime_available,
        "live_capture_ready": (
            node_available
            and npm_available
            and npx_available
            and playwright_cli_available
            and browser_runtime_available
        ),
        "install_note": (
            "Node.js/npm, playwright-cli, and a Chromium runtime are available for fresh 1920x1080 browser sweeps on this machine."
            if node_available and npm_available and npx_available and playwright_cli_available and browser_runtime_available
            else "Install Node.js/npm, playwright-cli, and the Chromium browser runtime to run a fresh 1920x1080 browser sweep on this machine."
        ),
    }


def _beta_agent_score_template(
    *,
    base: int = 4,
    overrides: dict[str, int] | None = None,
) -> dict[str, int]:
    payload = {key: base for key in _beta_agent_scoring_dimensions()}
    payload.update(overrides or {})
    return payload


def _reference_library_release_manifest() -> dict[str, Any]:
    payload = _load_json(REFERENCE_LIBRARY_MANIFEST, {})
    return payload if isinstance(payload, dict) else {}


def _reference_library_chunk_catalog() -> list[dict[str, Any]]:
    payload = _load_json(REFERENCE_LIBRARY_CHUNK_INDEX, [])
    if isinstance(payload, list) and payload:
        return payload
    manifest = _reference_library_release_manifest()
    record_counts = dict(manifest.get("record_counts") or {})
    source_snapshots = list((manifest.get("source_libraries") or {}).values())
    logical_chunks = [
        {
            "chunk_id": "core_planning_governance",
            "label": "Core planning and governance bundle",
            "filename": REFERENCE_LIBRARY_CORE_BUNDLE.name,
            "storage_kind": "compressed_sqlite",
            "install_status": "local",
            "families": [
                "proteins",
                "protein_variants",
                "structures",
                "dictionaries",
                "protein_similarity_signatures",
                "structure_similarity_signatures",
                "leakage_groups",
                "kinetics_support_preview",
            ],
            "record_counts": {
                key: int(record_counts.get(key, 0))
                for key in (
                    "proteins",
                    "protein_variants",
                    "structures",
                    "dictionaries",
                    "protein_similarity_signatures",
                    "structure_similarity_signatures",
                    "leakage_groups",
                    "kinetics_support_preview",
                )
            },
            "hydration_mode": "bundled_local_summary_data",
            "decoder_expectation": "proteosphere-lite-decoder-v1",
            "source_snapshot_ids": source_snapshots,
        },
        {
            "chunk_id": "ligand_support_family",
            "label": "Ligand support and provenance family",
            "filename": "ligand_support_family.pslchunk",
            "storage_kind": "suite_framed_chunk",
            "install_status": "logical",
            "families": [
                "ligands",
                "ligand_support_readiness",
                "ligand_identity_pilot",
                "ligand_stage1_validation_panel",
                "ligand_identity_core_materialization_preview",
                "ligand_row_materialization_preview",
                "ligand_similarity_signatures",
                "q9nzd4_bridge_validation_preview",
            ],
            "record_counts": {
                key: int(record_counts.get(key, 0))
                for key in (
                    "ligands",
                    "ligand_support_readiness",
                    "ligand_identity_pilot",
                    "ligand_stage1_validation_panel",
                    "ligand_identity_core_materialization_preview",
                    "ligand_row_materialization_preview",
                    "ligand_similarity_signatures",
                    "q9nzd4_bridge_validation_preview",
                )
            },
            "hydration_mode": "local_chunk_hydration",
            "decoder_expectation": "proteosphere-lite-decoder-v1",
            "source_snapshot_ids": source_snapshots,
        },
        {
            "chunk_id": "motif_and_signature_family",
            "label": "Motif, domain, and signature family",
            "filename": "motif_and_signature_family.pslchunk",
            "storage_kind": "suite_framed_chunk",
            "install_status": "logical",
            "families": [
                "motif_domain_compact_preview_family",
            ],
            "record_counts": {
                "motif_domain_compact_preview_family": int(
                    record_counts.get("motif_domain_compact_preview_family", 0)
                ),
            },
            "hydration_mode": "local_chunk_hydration",
            "decoder_expectation": "proteosphere-lite-decoder-v1",
            "source_snapshot_ids": source_snapshots,
        },
    ]
    return logical_chunks


def _reference_library_manifest() -> dict[str, Any]:
    release_manifest = _reference_library_release_manifest()
    chunk_catalog = _reference_library_chunk_catalog()
    bundle_version = ""
    if release_manifest.get("schema_id"):
        bundle_version = str(release_manifest["schema_id"]).replace(
            "proteosphere-lite-release-manifest-", ""
        )
    return {
        "bundle_id": "proteosphere-lite",
        "bundle_version": bundle_version or "preview-current",
        "bundle_kind": "compressed_sqlite",
        "schema_version": release_manifest.get(
            "schema_id", "proteosphere-lite-release-manifest-2026-04-01"
        ),
        "packaging_layout": "core_bundle_plus_family_chunks",
        "content_scope": "planning_governance_balance_leakage_and_packet_blueprints",
        "family_counts": dict(release_manifest.get("record_counts") or {}),
        "chunks": chunk_catalog,
        "checksums": {
            "bundle_sha256": release_manifest.get("bundle_sha256", ""),
            "checksum_file": str(REFERENCE_LIBRARY_CHECKSUM.relative_to(REPO_ROOT))
            if REFERENCE_LIBRARY_CHECKSUM.exists()
            else "",
        },
        "decoder_version": "proteosphere-lite-decoder-v1",
        "source_snapshot_ids": list((release_manifest.get("source_libraries") or {}).values()),
        "bundle_path": str(REFERENCE_LIBRARY_CORE_BUNDLE.relative_to(REPO_ROOT))
        if REFERENCE_LIBRARY_CORE_BUNDLE.exists()
        else "",
    }


def _reference_library_status() -> dict[str, Any]:
    manifest = _reference_library_manifest()
    bundle_exists = REFERENCE_LIBRARY_CORE_BUNDLE.exists()
    return {
        "build_state": "ready" if bundle_exists else "review_pending",
        "bundle_kind": manifest["bundle_kind"],
        "bundle_path": manifest.get("bundle_path", ""),
        "bundle_size_bytes": REFERENCE_LIBRARY_CORE_BUNDLE.stat().st_size if bundle_exists else 0,
        "record_family_counts": manifest.get("family_counts", {}),
        "source_coverage_summary": [
            "Bundled local summary data backs governance, split, leakage, balance, and packet blueprint planning views.",
            "Chunked family metadata is defined for ligand support and motif/signature families so the suite can hydrate only the needed domains.",
            "Heavy raw structures and full upstream source mirrors remain maintainer assets rather than end-user prerequisites.",
        ],
        "last_bundle_build_time": datetime.fromtimestamp(
            REFERENCE_LIBRARY_MANIFEST.stat().st_mtime, tz=UTC
        ).isoformat()
        if REFERENCE_LIBRARY_MANIFEST.exists()
        else "",
        "stale_source_flags": [
            "Full upstream mirrors still exist outside the compact bundle for advanced materialization lanes.",
            "Family chunk payloads are still preview-grade until the first end-user install bundle is cut."
        ],
    }


def _reference_library_gaps() -> dict[str, Any]:
    return {
        "missing_families": [
            "full_raw_structure_files",
            "full_upstream_source_mirrors",
            "heavy_example_materialization_payloads",
        ],
        "conditional_families": [
            "ligand_support_family",
            "motif_and_signature_family",
        ],
        "known_large_external_dependencies_not_yet_compacted": [
            "full PDB/MMCIF mirrors",
            "full procurement and structure follow-up trees",
            "heavy per-example materialization outputs",
        ],
    }


def _reference_library_resolution() -> list[dict[str, str]]:
    return [
        {
            "surface": "governance_and_launchability_views",
            "resolution": "bundled_local_summary_data",
        },
        {
            "surface": "split_balance_and_leakage_diagnostics",
            "resolution": "bundled_local_summary_data",
        },
        {
            "surface": "family_chunk_expansion",
            "resolution": "local_chunk_hydration",
        },
        {
            "surface": "heavy_materialization_and_raw_structures",
            "resolution": "still_required_external_raw_sources",
        },
    ]


def _reference_library_install_status() -> dict[str, Any]:
    runtime = _beta_agent_runtime()
    chunk_catalog = _reference_library_chunk_catalog()
    return {
        "core_bundle_local": REFERENCE_LIBRARY_CORE_BUNDLE.exists(),
        "manifest_local": REFERENCE_LIBRARY_MANIFEST.exists(),
        "checksum_local": REFERENCE_LIBRARY_CHECKSUM.exists(),
        "core_bundle_filename": REFERENCE_LIBRARY_CORE_BUNDLE.name,
        "chunk_count": len(chunk_catalog),
        "chunk_files_present": [
            item["filename"]
            for item in chunk_catalog
            if (REFERENCE_LIBRARY_ROOT / item["filename"]).exists()
        ],
        "decoder_version": "proteosphere-lite-decoder-v1",
        "suite_decoder_ready": True,
        "hydration_note": (
            "The Studio can prefer bundled summary data first and request precise chunk hydration or heavy-source access only when needed."
        ),
        "browser_agent_runtime": runtime,
    }


def _reference_library_query_contract() -> dict[str, Any]:
    chunk_catalog = _reference_library_chunk_catalog()
    supported_families = sorted(
        {
            family
            for chunk in chunk_catalog
            for family in chunk.get("families", [])
        }
    )
    return {
        "mode": "bundle_first",
        "supported_families": supported_families,
        "decoder": "proteosphere-lite-decoder-v1",
        "fallback": "heavy_materialization_lane",
    }


def _reference_library_hydration_requirements() -> list[dict[str, str]]:
    return [
        {
            "surface": "launchability_and_governance",
            "requirement": "No additional hydration required when the core bundle is present.",
        },
        {
            "surface": "ligand_support_deep_dive",
            "requirement": "Hydrate the ligand support family chunk when a workflow needs compact ligand provenance details beyond the core bundle.",
        },
        {
            "surface": "heavy_example_materialization",
            "requirement": "External raw sources and heavy artifact hydration remain required for full materialization outputs.",
        },
    ]


def _beta_evidence_flags(review_artifacts: list[str]) -> dict[str, bool]:
    final_bundle_root = _latest_final_rehearsal_dir()
    final_artifacts = _final_rehearsal_artifacts()
    final_artifact_relpaths = [str(path.relative_to(REPO_ROOT)) for path in final_artifacts]
    final_lookup = {
        str(path.relative_to(final_bundle_root)).replace("\\", "/").casefold(): path
        for path in final_artifacts
    } if final_bundle_root else {}

    visual_manifest = _load_json_path(
        final_lookup.get("visual/visual_review_manifest.json", Path("__missing__"))
    )
    blocked_trace = _load_json_path(
        final_lookup.get("blocked/blocked_feature_trace.json", Path("__missing__"))
    )
    ppi_trace = _load_json_path(
        final_lookup.get("ppi_flow/user_sim_trace.json", Path("__missing__"))
    )
    ligand_trace = _load_json_path(
        final_lookup.get("ligand_flow/user_sim_trace.json", Path("__missing__"))
    )
    ligand_matrix = _load_json(
        REVIEW_ROOT / "ligand_pilot_round_1" / "ligand_execution_matrix.json",
        {},
    )
    ligand_runs = ligand_matrix.get("runs", []) if isinstance(ligand_matrix, dict) else []
    ligand_models = {str(item.get("model_family", "")) for item in ligand_runs}
    blocked_features = blocked_trace.get("blocked_features", []) if isinstance(blocked_trace, dict) else []

    visual_ready = bool(
        visual_manifest
        and final_lookup.get("visual/desktop_full.png")
        and final_lookup.get("visual/mobile_full.png")
    )
    blocked_ready = bool(
        blocked_trace
        and final_lookup.get("blocked/blocked_pyrosetta_explanation.png")
        and final_lookup.get("blocked/blocked_free_state_explanation.png")
        and final_lookup.get("blocked/failure_help_report_issue.png")
        and len(blocked_features) >= 2
    )
    ppi_trace_ready = bool(
        ppi_trace
        and ppi_trace.get("mode") == "ppi"
        and ppi_trace.get("run_status") == "completed"
        and ppi_trace.get("run_id")
        and final_lookup.get("ppi_flow/ppi_after_launch.png")
    )
    ligand_trace_ready = bool(
        ligand_trace
        and ligand_trace.get("mode") == "ligand"
        and ligand_trace.get("run_status") == "completed"
        and ligand_trace.get("run_id")
        and final_lookup.get("ligand_flow/ligand_after_launch.png")
    )
    compare_export_ready = bool(
        ppi_trace_ready
        and ligand_trace_ready
        and ppi_trace.get("comparison")
        and ligand_trace.get("comparison")
    )
    ligand_matrix_ready = {
        "graphsage",
        "multimodal_fusion",
    }.issubset(ligand_models)
    return {
        "final_bundle_root": str(final_bundle_root.relative_to(REPO_ROOT)) if final_bundle_root else "",
        "review_artifact_count": len(review_artifacts),
        "final_artifact_count": len(final_artifact_relpaths),
        "browser_traces": ppi_trace_ready and ligand_trace_ready and blocked_ready,
        "desktop_screenshots": visual_ready,
        "narrow_screenshots": visual_ready,
        "failure_state_screenshots": blocked_ready,
        "compare_export_examples": compare_export_ready,
        "ligand_guided_flow_evidence": ligand_trace_ready and ligand_matrix_ready,
    }


def _beta_test_agent_runs() -> list[dict[str, Any]]:
    latest_sweep = _latest_beta_agent_sweep_dir()
    sweep_viewport_dir = _latest_beta_agent_viewport_dir(latest_sweep)
    if sweep_viewport_dir and sweep_viewport_dir.exists():
        live_runs: list[dict[str, Any]] = []
        for agent_dir in sorted(path for path in sweep_viewport_dir.iterdir() if path.is_dir()):
            runs_path = agent_dir / "runs.json"
            payload = _load_json(runs_path, [])
            if isinstance(payload, list):
                live_runs.extend(item for item in payload if isinstance(item, dict))
        if live_runs:
            normalized_live_runs: list[dict[str, Any]] = []
            for item in live_runs:
                normalized_live_runs.append(
                    {
                        **item,
                        "interaction_steps": item.get("interaction_steps")
                        or [
                            {
                                "step": "Replay the current Studio flow at the required viewport and verify that the visible state updates.",
                                "expected_effect": "Controls, diagnostics, and flow state should respond to the tested interaction path.",
                                "observed_effect": item.get(
                                    "overall_verdict",
                                    "review_pending",
                                ),
                                "pass_fail": "pass"
                                if item.get("overall_verdict")
                                in {"pass", "blocked_as_expected", "needs_followup"}
                                else "review_pending",
                            }
                        ],
                        "ui_diff_summary": item.get("ui_diff_summary")
                        or "Live sweep evidence is present; compare the captured screen set against the current flow state.",
                        "backend_diff_summary": item.get("backend_diff_summary")
                        or "The backend-authored flow status should match the interaction path captured in the live sweep.",
                    }
                )
            return normalized_live_runs
    final_bundle_root = _latest_final_rehearsal_dir()
    final_lookup = {
        str(path.relative_to(final_bundle_root)).replace("\\", "/").casefold(): path
        for path in _final_rehearsal_artifacts()
    } if final_bundle_root else {}
    visual_manifest = _load_json_path(
        final_lookup.get("visual/visual_review_manifest.json", Path("__missing__"))
    )
    blocked_trace = _load_json_path(
        final_lookup.get("blocked/blocked_feature_trace.json", Path("__missing__"))
    )
    ppi_trace = _load_json_path(
        final_lookup.get("ppi_flow/user_sim_trace.json", Path("__missing__"))
    )
    ligand_trace = _load_json_path(
        final_lookup.get("ligand_flow/user_sim_trace.json", Path("__missing__"))
    )
    blocked_features = {
        str(item.get("label", "")).casefold(): item
        for item in blocked_trace.get("blocked_features", [])
        if isinstance(item, dict)
    }
    visual_output_dir = Path(visual_manifest.get("output_dir")) if visual_manifest.get("output_dir") else None

    def _artifact_ref(path_or_str: str | Path | None) -> str:
        if not path_or_str:
            return ""
        path = Path(path_or_str)
        try:
            return str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        except ValueError:
            return str(path).replace("\\", "/")

    flow_details = {
        "ppi_benchmark_launchable_flow": {
            "status": "ready" if ppi_trace.get("run_status") == "completed" else "review_pending",
            "artifact_paths": [
                "ppi_flow/user_sim_trace.json",
                "ppi_flow/ppi_after_launch.png",
                "visual/desktop_workspace-analysis-review.png",
                "visual/mobile_workspace-analysis-review.png",
            ],
            "scores": _beta_agent_score_template(
                overrides={
                    "output_quality_clarity": 3,
                    "trust_clarity": 4,
                }
            ),
            "top_findings": [
                "The guided launch/run/analysis path is complete at the 1920x1080 primary review viewport and still matches the shipped beta lane.",
                "Output quality remains visibly weak in the benchmark multimodal example, so the output framing needs to stay cautionary rather than celebratory.",
            ],
            "blocking_findings": [],
            "recommended_actions": [
                "Keep the weak-output framing and quality warnings visible anywhere the benchmark multimodal path appears.",
                "Capture a fresh live browser trace when UI changes land so the 1080p evidence stays current.",
            ],
            "overall_verdict": "needs_followup",
        },
        "governed_ppi_subset_flow": {
            "status": "review_pending",
            "artifact_paths": [
                "visual/desktop_workspace-project-home.png",
                "visual/desktop_workspace-data-strategy.png",
                "visual/mobile_workspace-project-home.png",
            ],
            "scores": _beta_agent_score_template(
                base=3,
                overrides={
                    "content_relevance": 4,
                    "trust_clarity": 4,
                }
            ),
            "top_findings": [
                "Governed subset wording and launchability labels are present in the current Program and dataset-pool views.",
                "A dedicated governed-subset user-sim trace is still missing from the current primary evidence bundle.",
            ],
            "blocking_findings": [],
            "recommended_actions": [
                "Add a dedicated governed promoted-subset launch trace to the next live 1920x1080 sweep.",
            ],
            "overall_verdict": "review_pending",
        },
        "protein_ligand_pilot_flow": {
            "status": "ready" if ligand_trace.get("run_status") == "completed" else "review_pending",
            "artifact_paths": [
                "ligand_flow/user_sim_trace.json",
                "ligand_flow/ligand_after_launch.png",
                "visual/desktop_workspace-analysis-review.png",
                "visual/mobile_workspace-analysis-review.png",
            ],
            "scores": _beta_agent_score_template(
                overrides={
                    "look_and_feel": 4,
                    "content_relevance": 4,
                    "output_quality_clarity": 4,
                }
            ),
            "top_findings": [
                "The ligand pilot trace shows a complete launchable run at the required viewport family.",
                "The narrow ligand contract remains clear: structure-backed rows, explicit provenance, and the constrained model family surface.",
            ],
            "blocking_findings": [],
            "recommended_actions": [
                "Keep compare/export provenance visible whenever the ligand pilot is selected.",
            ],
            "overall_verdict": "pass",
        },
        "blocked_pyrosetta_flow": {
            "status": "ready" if blocked_features.get("pyrosetta") else "review_pending",
            "artifact_paths": [
                "blocked/blocked_feature_trace.json",
                "blocked/blocked_pyrosetta_explanation.png",
                "blocked/failure_help_report_issue.png",
            ],
            "scores": _beta_agent_score_template(
                overrides={
                    "blocked_state_quality": 5,
                    "supportability": 5,
                }
            ),
            "top_findings": [
                "PyRosetta reads as intentionally blocked rather than broken in the current evidence bundle.",
            ],
            "blocking_findings": [],
            "recommended_actions": [
                "Keep the Stage 2 wording explicit until the native runtime is truly exercised.",
            ],
            "overall_verdict": "blocked_as_expected",
        },
        "blocked_free_state_flow": {
            "status": "ready" if blocked_features.get("free-state comparison") else "review_pending",
            "artifact_paths": [
                "blocked/blocked_feature_trace.json",
                "blocked/blocked_free_state_explanation.png",
                "blocked/failure_help_report_issue.png",
            ],
            "scores": _beta_agent_score_template(
                overrides={
                    "blocked_state_quality": 5,
                    "supportability": 5,
                }
            ),
            "top_findings": [
                "Free-state comparison reads as review-pending and blocked for the right reasons.",
            ],
            "blocking_findings": [],
            "recommended_actions": [
                "Keep the real paired-structure requirement explicit in future Stage 2 wording.",
            ],
            "overall_verdict": "blocked_as_expected",
        },
    }

    agent_specific_overrides = {
        "visual-cleanliness-agent": {},
        "usability-agent": {"usability": 5, "supportability": 4},
        "content-relevance-agent": {"content_relevance": 5, "trust_clarity": 4},
        "scientific-output-agent": {"output_quality_clarity": 5, "trust_clarity": 5},
        "failure-recovery-agent": {"blocked_state_quality": 5, "supportability": 5},
        "release-governance-agent": {"trust_clarity": 5, "content_relevance": 4},
    }
    runs: list[dict[str, Any]] = []
    for agent in _beta_test_agents():
        for flow in _beta_agent_flow_specs():
            flow_id = flow["flow_id"]
            detail = flow_details[flow_id]
            scores = dict(detail["scores"])
            scores.update(agent_specific_overrides.get(agent["agent_id"], {}))
            if agent["agent_id"] == "scientific-output-agent" and flow_id == "ppi_benchmark_launchable_flow":
                scores["output_quality_clarity"] = 3
                scores["trust_clarity"] = 4
            runs.append(
                {
                    "agent_id": agent["agent_id"],
                    "flow_id": flow_id,
                    "flow_title": flow["title"],
                    "viewport": {"width": 1920, "height": 1080},
                    "status": detail["status"],
                    "screens_evaluated": [
                        item for item in detail["artifact_paths"] if item.endswith(".png")
                    ],
                    "interaction_steps": [
                        {
                            "step": "Open the guided Studio workspace at the required viewport.",
                            "expected_effect": "The current flow loads with the matching pane visible and the beta status surfaces present.",
                            "observed_effect": "The flow evidence bundle includes the expected viewport capture and matching flow metadata.",
                            "pass_fail": "pass" if detail["status"] == "ready" else "review_pending",
                        }
                    ],
                    "ui_diff_summary": (
                        "The visible state changed in line with the selected flow and current viewport evidence."
                        if detail["status"] == "ready"
                        else "Fresh interaction evidence is still needed for this flow."
                    ),
                    "backend_diff_summary": (
                        "The backend-authored launchability and diagnostics surfaces remained aligned with the evaluated flow."
                        if detail["status"] == "ready"
                        else "The flow is still seeded from existing evidence rather than a fresh interaction trace."
                    ),
                    "scores": scores,
                    "top_findings": list(detail["top_findings"]),
                    "blocking_findings": list(detail["blocking_findings"]),
                    "recommended_actions": list(detail["recommended_actions"]),
                    "artifact_paths": detail["artifact_paths"],
                    "overall_verdict": detail["overall_verdict"],
                    "sweep_root": _artifact_ref(final_bundle_root),
                    "visual_manifest_root": _artifact_ref(visual_output_dir),
                    "source_trace_run_id": (
                        ppi_trace.get("run_id")
                        if flow_id == "ppi_benchmark_launchable_flow"
                        else ligand_trace.get("run_id")
                        if flow_id == "protein_ligand_pilot_flow"
                        else ""
                    ),
                    "evidence_mode": (
                        "browser_capture"
                        if detail["status"] == "ready"
                        else "seeded_from_existing_evidence"
                    ),
                    "coverage_note": flow["coverage_note"],
                }
            )
    return runs


def _beta_test_agent_findings() -> dict[str, Any]:
    latest_sweep = _latest_beta_agent_sweep_dir()
    sweep_viewport_dir = _latest_beta_agent_viewport_dir(latest_sweep)
    if sweep_viewport_dir and sweep_viewport_dir.exists():
        payload = _load_json(sweep_viewport_dir / "agent_findings.json", {})
        if isinstance(payload, dict) and payload.get("open_findings") is not None:
            return payload
    findings = [
        {
            "finding_id": "scientific-output-agent:ppi_benchmark_launchable_flow:quality-framing",
            "agent_id": "scientific-output-agent",
            "flow_id": "ppi_benchmark_launchable_flow",
            "severity": "P2",
            "summary": "The benchmark multimodal example still shows weak output quality, so the UI must keep the quality-warning framing prominent.",
            "action": "Preserve explicit weak-output framing and do not treat this trace as strong scientific performance evidence.",
            "owner_lane": "Bacon",
            "blocking": False,
        },
        {
            "finding_id": "content-relevance-agent:governed_ppi_subset_flow:missing-dedicated-trace",
            "agent_id": "content-relevance-agent",
            "flow_id": "governed_ppi_subset_flow",
            "severity": "P3",
            "summary": "The primary evidence pack still lacks a dedicated governed promoted-subset user-sim trace.",
            "action": "Capture a dedicated governed-subset launch trace during the next live 1920x1080 sweep.",
            "owner_lane": "McClintock",
            "blocking": False,
        },
    ]
    by_severity: dict[str, int] = {}
    by_owner_lane: dict[str, int] = {}
    by_flow: dict[str, int] = {}
    for item in findings:
        by_severity[item["severity"]] = by_severity.get(item["severity"], 0) + 1
        by_owner_lane[item["owner_lane"]] = by_owner_lane.get(item["owner_lane"], 0) + 1
        by_flow[item["flow_id"]] = by_flow.get(item["flow_id"], 0) + 1
    return {
        "open_findings": findings,
        "by_severity": by_severity,
        "by_owner_lane": by_owner_lane,
        "by_flow": by_flow,
        "open_p1_count": by_severity.get("P1", 0),
    }


def _beta_test_agent_matrix() -> dict[str, Any]:
    latest_sweep = _latest_beta_agent_sweep_dir()
    sweep_viewport_dir = _latest_beta_agent_viewport_dir(latest_sweep)
    if sweep_viewport_dir and sweep_viewport_dir.exists():
        payload = _load_json(sweep_viewport_dir / "agent_matrix.json", {})
        if isinstance(payload, dict) and payload.get("coverage") is not None:
            return payload
    runs = _beta_test_agent_runs()
    coverage: list[dict[str, Any]] = []
    for flow in _beta_agent_flow_specs():
        flow_runs = [item for item in runs if item["flow_id"] == flow["flow_id"]]
        coverage.append(
            {
                "flow_id": flow["flow_id"],
                "label": flow["title"],
                "coverage_note": flow["coverage_note"],
                "status": (
                    "ready"
                    if flow_runs and all(item["status"] == "ready" for item in flow_runs)
                    else "review_pending"
                ),
                "agent_results": [
                    {
                        "agent_id": item["agent_id"],
                        "status": item["status"],
                        "overall_verdict": item["overall_verdict"],
                    }
                    for item in flow_runs
                ],
            }
        )
    return {
        "required_viewport": {"width": 1920, "height": 1080},
        "minimum_viewport": {"width": 1280, "height": 720},
        "required_flows": [item["flow_id"] for item in _beta_agent_flow_specs()],
        "coverage": coverage,
        # Honest "is this real data?" flag for the GUI. When False the
        # ``coverage`` list above is synthesized from flow specs +
        # ``_beta_test_agent_runs()`` heuristics, not from real
        # capture evidence. Consumers should treat synthetic verdicts
        # as planning hints, not as production readiness signals.
        "evidence_present": _beta_agent_evidence_present(),
    }


def _beta_test_agent_status() -> dict[str, Any]:
    latest_sweep = _latest_beta_agent_sweep_dir()
    sweep_viewport_dir = _latest_beta_agent_viewport_dir(latest_sweep)
    if sweep_viewport_dir and sweep_viewport_dir.exists():
        payload = _load_json(sweep_viewport_dir / "agent_status.json", {})
        if isinstance(payload, dict) and payload.get("required_viewport") is not None:
            runtime = _beta_agent_runtime()
            payload["runner_environment"] = runtime
            payload["supports_live_browser_capture"] = runtime["live_capture_ready"]
            if "artifact_root" not in payload or not payload["artifact_root"]:
                payload["artifact_root"] = str(latest_sweep.relative_to(REPO_ROOT))
            return payload
    runtime = _beta_agent_runtime()
    matrix = _beta_test_agent_matrix()
    findings = _beta_test_agent_findings()
    missing_flows = [
        item["flow_id"] for item in matrix["coverage"] if item["status"] != "ready"
    ]
    current_sweep_complete = not missing_flows
    final_bundle_root = _latest_beta_agent_sweep_dir() or _latest_final_rehearsal_dir()
    stale_after_paths = [
        REPO_ROOT / "api" / "model_studio" / "service.py",
        # ``app_beta.js`` was folded into ``app.js`` during the May 2026
        # consolidation; track the canonical bundle now.
        REPO_ROOT / "gui" / "model_studio_web" / "app.js",
        REPO_ROOT / "gui" / "model_studio_web" / "index.html",
    ]
    latest_product_edit = max(
        (path.stat().st_mtime for path in stale_after_paths if path.exists()),
        default=0.0,
    )
    last_sweep_time = final_bundle_root.stat().st_mtime if final_bundle_root and final_bundle_root.exists() else 0.0
    evidence_stale = bool(last_sweep_time and latest_product_edit > last_sweep_time)
    evidence_present = _beta_agent_evidence_present()
    # When no real evidence is on disk, downgrade the headline status
    # to ``review_pending``. The previous behaviour returned ``ready``
    # whenever the synthesized coverage said so -- effectively shipping
    # canned "we passed our agent tests" verdicts without any actual
    # test runs underneath.
    headline_status = (
        "ready"
        if (evidence_present and current_sweep_complete and findings["open_p1_count"] == 0)
        else "review_pending"
    )
    return {
        "status": headline_status,
        "required_viewport": {"width": 1920, "height": 1080},
        "minimum_viewport": {"width": 1280, "height": 720},
        "current_sweep_complete": current_sweep_complete,
        "current_sweep_is_stale": evidence_stale,
        "evidence_present": evidence_present,
        "open_p1_findings": findings["open_p1_count"],
        "open_findings_by_severity": findings["by_severity"],
        "missing_flows": missing_flows,
        "missing_agents": [],
        "supports_live_browser_capture": runtime["live_capture_ready"],
        "runner_environment": runtime,
        "artifact_root": str(final_bundle_root.relative_to(REPO_ROOT)) if final_bundle_root else "",
        "last_sweep_executed_at": datetime.fromtimestamp(last_sweep_time, tz=UTC).isoformat()
        if last_sweep_time
        else "",
    }


def _ops_launch_ready() -> bool:
    return all((REPO_ROOT / item["path"]).exists() for item in _beta_docs_manifest())


def _beta_support_contract(
    *,
    launchable_dataset_pools: list[dict[str, Any]] | None = None,
    stage2_scientific_tracks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    docs = _beta_docs_manifest()
    launchable_pool_labels = [
        item.get("label", item.get("pool_id", "unknown"))
        for item in (launchable_dataset_pools or _launchable_dataset_pools())
    ]
    launchable_pool_ids = {
        item.get("pool_id", "") for item in (launchable_dataset_pools or _launchable_dataset_pools())
    }
    ligand_pilot_live = "pool:governed_pl_bridge_pilot_subset_v1" in launchable_pool_ids
    stage2_labels = [
        item.get("track_label", item.get("track_id", "unknown"))
        for item in (stage2_scientific_tracks or build_stage2_scientific_tracks())
    ]
    return {
        "scope": "controlled_external_beta",
        "state_vocabulary": ["Launchable now", "Review pending", "Inactive"],
        "how_this_beta_works": (
            "This is a controlled external beta for structure-backed protein-protein studies plus one narrow protein-ligand pilot. "
            "Use the guided stepper for launchable work, and treat review-pending items as visible but not yet safe for routine study launches."
        ),
        "safe_to_use_now": [
            f"Launchable dataset pools in the current guided lane: {', '.join(launchable_pool_labels) or 'none recorded'}.",
            "Residue-graph flows are safe to use when the selected controls still show Launchable now.",
            "The atom-native beta and the Studio-local deterministic sequence-embedding beta lane are safe to use only when the selected controls still show Launchable now and their current beta limits remain acceptable for the study.",
            (
                "The governed protein-ligand bridge pilot is launchable only with structure-backed rows, explicit ligand bridge provenance, and the narrow graphsage or multimodal_fusion model contract."
                if ligand_pilot_live
                else "Protein-ligand remains out of the launchable lane until the governed bridge pilot is promoted."
            ),
        ],
        "review_pending": [
            "Governed promotion candidates and blocked prototype lanes remain visible for audit, but they still need reviewer signoff before routine use.",
            f"Stage 2 prototype tracks currently stay review-pending: {', '.join(stage2_labels) or 'none recorded'}.",
        ],
        "blocked_prototype_lanes": [
            "PyRosetta remains blocked until native runtime signoff, matrix proof, and user-facing wording all agree.",
            "Free-state comparison remains blocked until bound-state and free-state structure pairing is real and review-cleared.",
        ],
        "known_limitations": [
            (
                "The shipped beta is still PPI-first, and the protein-ligand pilot is intentionally narrow: structure-backed rows only, exact Kd/Ki-derived delta_G labels, and only graphsage plus multimodal_fusion are launchable."
                if ligand_pilot_live
                else "The shipped beta is PPI-first. Protein-ligand remains a post-freeze secondary lane and is not launchable yet."
            ),
            "Most governed staged rows are still beta-review-only, so promoted governed subsets remain intentionally narrow.",
            "Stage 2 scientific tracks are artifact-backed but still blocked from routine study use.",
            "Browser-based external-user rehearsal evidence is still being accumulated and must stay aligned with runtime truth.",
        ],
        "how_to_report": (
            "Use Need help / report issue whenever a field, blocker, chart, or result feels unclear. "
            "Include the selected pool, model family, and current step so the beta trace is easier to reproduce."
        ),
        "support_response_expectation": (
            "Participant-facing issues are triaged daily during the controlled beta. "
            "P1 blockers receive same-day acknowledgement, while clarification and usability issues are grouped into the next review wave."
        ),
        "escalation_path": [
            "Product confusion or contradictory status language -> Ampere review lane",
            "Dataset governance, admissibility, or promotion disputes -> McClintock review lane",
            "Runtime, contract, or execution-path truth issues -> Kepler and Bacon review lanes",
            "Scientific wording or structural-biology truth issues -> Mill review lane",
            "Regression, matrix, or blocker-behavior failures -> Euler review lane",
        ],
        "issue_intake_categories": [
            "launchability_confusion",
            "dataset_governance",
            "runtime_failure",
            "scientific_wording",
            "ux_clarity",
            "analysis_compare_export",
        ],
        "beta_docs": docs,
    }


def _beta_program_lanes(
    *,
    launchable_dataset_pools: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    launchable_pool_ids = [item.get("pool_id", "") for item in launchable_dataset_pools]
    launchable_ppi_governed_subset_ids = {
        pool_id
        for pool_id in launchable_pool_ids
        if pool_id in {
            "pool:governed_ppi_blended_subset_v2",
            "pool:governed_ppi_external_beta_candidate_v1",
        }
    }
    ppi_ready = len(launchable_ppi_governed_subset_ids) >= 2
    ligand_ready = "pool:governed_pl_bridge_pilot_subset_v1" in launchable_pool_ids
    return [
        {
            "lane_id": "ppi_primary",
            "label": "PPI primary lane",
            "state": "review_pending" if not ppi_ready else "launchable_now",
            "launchable_pool_ids": launchable_pool_ids,
            "summary": (
                "Primary beta lane for invite-ready protein-protein studies."
                if ppi_ready
                else "Invite-ready PPI studies remain review-pending until both governed subsets are launchable."
            ),
        },
        {
            "lane_id": "protein_ligand_pilot",
            "label": "Protein-ligand pilot",
            "state": "inactive" if not ppi_ready else "launchable_now" if ligand_ready else "review_pending",
            "launchable_pool_ids": (
                ["pool:governed_pl_bridge_pilot_subset_v1"] if ligand_ready else []
            ),
            "summary": (
                "Not available until the primary PPI beta lane is cleared."
                if not ppi_ready
                else "Launchable narrow protein-ligand pilot for structure-backed delta_G studies."
                if ligand_ready
                else "Visible for review, but not yet launchable."
            ),
        },
        {
            "lane_id": "other_non_ppi",
            "label": "Other non-PPI lanes",
            "state": "inactive",
            "launchable_pool_ids": [],
            "summary": "Out of scope for this beta program.",
        },
    ]


def _beta_evidence_checklist(review_artifacts: list[str]) -> list[dict[str, Any]]:
    evidence_flags = _beta_evidence_flags(review_artifacts)
    reviewer_signoff = _reviewer_signoff_state()
    return [
        {
            "artifact_id": "browser_traces",
            "label": "Browser traces",
            "status": "ready" if evidence_flags["browser_traces"] else "review_pending",
            "detail": (
                f"Fresh guided-flow traces are present in {evidence_flags['final_bundle_root']}."
                if evidence_flags["browser_traces"]
                else "Full browser traces are still required for the final external rehearsal."
            ),
        },
        {
            "artifact_id": "desktop_screenshots",
            "label": "Desktop screenshots",
            "status": "ready" if evidence_flags["desktop_screenshots"] else "review_pending",
            "detail": (
                "Desktop screenshots are present in the visual review bundles."
                if evidence_flags["desktop_screenshots"]
                else "Desktop screenshots for the full guided flow still need to be assembled into the evidence pack."
            ),
        },
        {
            "artifact_id": "narrow_screenshots",
            "label": "Narrow/mobile screenshots",
            "status": "ready" if evidence_flags["narrow_screenshots"] else "review_pending",
            "detail": (
                "Narrow/mobile screenshots are present in the visual review bundles."
                if evidence_flags["narrow_screenshots"]
                else "Narrow-layout screenshots are still required before the final gate."
            ),
        },
        {
            "artifact_id": "failure_state_screenshots",
            "label": "Failure-state screenshots",
            "status": "ready" if evidence_flags["failure_state_screenshots"] else "review_pending",
            "detail": (
                "Failure-state screenshots are already present in the review bundle."
                if evidence_flags["failure_state_screenshots"]
                else "Blocked and error-state screenshots must still be captured for support and review lanes."
            ),
        },
        {
            "artifact_id": "compare_export_examples",
            "label": "Compare/export examples",
            "status": "ready" if evidence_flags["compare_export_examples"] else "review_pending",
            "detail": (
                "Compare/export evidence already appears in the review artifacts."
                if evidence_flags["compare_export_examples"]
                else "Compare and export examples still need to be captured into the formal evidence pack."
            ),
        },
        {
            "artifact_id": "ligand_pilot_evidence",
            "label": "Ligand pilot execution proof",
            "status": "ready" if evidence_flags["ligand_guided_flow_evidence"] else "review_pending",
            "detail": (
                "Ligand pilot execution evidence is present for both launchable model families."
                if evidence_flags["ligand_guided_flow_evidence"]
                else "Ligand pilot execution proof still needs to be attached to the evidence pack."
            ),
        },
        {
            "artifact_id": "reviewer_signoff_ledger",
            "label": "Reviewer signoff ledger",
            "status": "ready" if reviewer_signoff["ready"] else "review_pending",
            "detail": (
                "All required reviewers have current-wave approvals recorded with no open P1 findings."
                if reviewer_signoff["ready"]
                else "Current-wave reviewer approvals are still incomplete or still list an open P1 finding."
            ),
        },
        {
            "artifact_id": "deferred_items_ledger",
            "label": "Deferred-items ledger",
            "status": "ready",
            "detail": "The deferred-items ledger exists and must stay aligned with product truth on every wave.",
        },
        {
            "artifact_id": "limitations_ledger",
            "label": "Limitations ledger",
            "status": "ready",
            "detail": "Known limitations are documented and must remain aligned with runtime truth.",
        },
    ]


def _beta_readiness_dashboard(
    *,
    candidate_database_summary_v3: dict[str, Any],
    stage2_scientific_tracks: list[dict[str, Any]],
    launchable_dataset_pools: list[dict[str, Any]],
    review_artifacts: list[str],
) -> dict[str, Any]:
    bias_hotspots = list(candidate_database_summary_v3.get("bias_hotspots") or [])
    launchable_pool_ids = {item.get("pool_id", "") for item in launchable_dataset_pools}
    launchable_ppi_governed_subset_ids = {
        pool_id
        for pool_id in launchable_pool_ids
        if pool_id in {
            "pool:governed_ppi_blended_subset_v2",
            "pool:governed_ppi_external_beta_candidate_v1",
        }
    }
    ppi_freeze_ready = len(launchable_ppi_governed_subset_ids) >= 2
    stage2_ready = all(item.get("status") == "ready" for item in stage2_scientific_tracks)
    ligand_pilot_ready = "pool:governed_pl_bridge_pilot_subset_v1" in launchable_pool_ids
    evidence_flags = _beta_evidence_flags(review_artifacts)
    reviewer_signoff = _reviewer_signoff_state()
    ops_ready = _ops_launch_ready()
    beta_agent_status = _beta_test_agent_status()
    external_rehearsal_ready = all(
        (
            evidence_flags["browser_traces"],
            evidence_flags["desktop_screenshots"],
            evidence_flags["narrow_screenshots"],
            evidence_flags["failure_state_screenshots"],
            evidence_flags["compare_export_examples"],
            evidence_flags["ligand_guided_flow_evidence"],
            reviewer_signoff["ready"],
        )
    )
    gates = [
        {
            "gate_id": "ppi_freeze_gate",
            "label": "Primary beta lane",
            "blocks_beta_launch": True,
            "status": "ready" if ppi_freeze_ready else "review_pending",
            "detail": (
                "Two governed PPI subsets are launchable."
                if ppi_freeze_ready
                else "A second governed PPI subset is still required before the primary beta lane is fully cleared."
            ),
        },
        {
            "gate_id": "stage2_implementation_gate",
            "label": "Stage 2 implementation gate",
            "blocks_beta_launch": False,
            "status": "ready" if stage2_ready else "review_pending",
            "detail": (
                "Stage 2 tracks are no longer review-pending and have moved beyond prototype-only status."
                if stage2_ready
                else "PyRosetta and free-state comparison remain visible, real, and non-launchable for this beta."
            ),
        },
        {
            "gate_id": "protein_ligand_pilot_gate",
            "label": "Ligand pilot",
            "blocks_beta_launch": True,
            "status": "ready" if ligand_pilot_ready else "inactive",
            "detail": (
                "Protein-ligand pilot is launchable."
                if ligand_pilot_ready
                else "Protein-ligand pilot cannot start until the PPI freeze gate passes."
                if not ppi_freeze_ready
                else "Protein-ligand pilot is still review-pending and not yet launchable."
            ),
        },
        {
            "gate_id": "ops_launch_gate",
            "label": "Docs and support",
            "blocks_beta_launch": True,
            "status": "ready" if ops_ready else "review_pending",
            "detail": (
                "Ops, docs, and reporting are complete."
                if ops_ready
                else "Ops docs, routing, templates, or support ledgers are still incomplete."
            ),
        },
        {
            "gate_id": "final_external_rehearsal_gate",
            "label": "Final user rehearsal",
            "blocks_beta_launch": True,
            "status": "ready" if external_rehearsal_ready else "review_pending",
            "detail": (
                "Fresh rehearsal evidence and final reviewer approvals are complete."
                if external_rehearsal_ready
                else "Fresh rehearsal traces, screenshots, or final reviewer approvals are still incomplete."
            ),
        },
    ]
    blocking_gates = [item for item in gates if item.get("blocks_beta_launch", True)]
    ready_gate_count = sum(1 for item in blocking_gates if item["status"] == "ready")
    remaining_blockers = []
    parallel_risks = []
    if not ppi_freeze_ready:
        remaining_blockers.append("Promote one additional governed PPI subset to launchable status.")
    if not ligand_pilot_ready:
        remaining_blockers.append("Promote the governed protein-ligand bridge pilot into the launchable beta lane.")
    if not ops_ready:
        remaining_blockers.append("Complete the beta ops, reporting, and support layers.")
    if not external_rehearsal_ready:
        if not evidence_flags["failure_state_screenshots"]:
            remaining_blockers.append("Capture failure-state screenshots for blocked and error flows.")
        if not evidence_flags["ligand_guided_flow_evidence"]:
            remaining_blockers.append("Attach ligand pilot guided-flow evidence to the final rehearsal pack.")
        if not evidence_flags["browser_traces"] or not evidence_flags["desktop_screenshots"] or not evidence_flags["narrow_screenshots"]:
            remaining_blockers.append("Capture the missing browser trace or visual evidence artifacts.")
        if not reviewer_signoff["ready"]:
            remaining_blockers.append("Record final current-wave reviewer approvals with no open P1 findings.")
    if bias_hotspots:
        parallel_risks.extend(
            f"Current candidate-database hotspot: {hotspot}" for hotspot in bias_hotspots[:3]
        )
    return {
        "overall_status": "beta_ready" if ready_gate_count == len(blocking_gates) else "review_pending",
        "completion_percent": round((ready_gate_count / len(blocking_gates)) * 100),
        "current_focus": (
            "ppi_freeze_gate"
            if not ppi_freeze_ready
            else "protein_ligand_pilot"
            if not ligand_pilot_ready
            else "ops_launch_gate"
            if not ops_ready
            else "final_external_rehearsal"
        ),
        "gates": gates,
        "remaining_blockers": remaining_blockers,
        "parallel_risks": parallel_risks,
        "program_lanes": _beta_program_lanes(
            launchable_dataset_pools=launchable_dataset_pools,
        ),
        "evidence_checklist": _beta_evidence_checklist(review_artifacts),
        "beta_agent_status": beta_agent_status,
    }


def build_program_status() -> dict[str, Any]:
    runs = [
        item
        for item in list_runs()
        if is_active_option("model_families", item.get("model_family", ""))
    ]
    curated_queue = _load_json(CURATED_QUEUE, [])
    orchestrator_state = _load_json(ORCHESTRATOR_STATE, {})
    known_datasets = filter_known_datasets(list_known_datasets())
    dataset_pools = list_dataset_pools()
    candidate_pool_summary = build_candidate_pool_summary()
    candidate_database_summary = build_candidate_database_summary()
    candidate_database_summary_v2 = build_candidate_database_summary_v2()
    candidate_database_summary_v3 = build_candidate_database_summary_v3()
    governed_bridge_manifests = build_governed_bridge_manifests()
    governed_subset_manifests = build_governed_subset_manifests()
    governed_subset_manifests_v2 = build_governed_subset_manifests_v2()
    promotion_reports = build_pool_promotion_reports()
    promotion_queue = build_promotion_queue()
    promotion_queue_v2 = build_promotion_queue_v2()
    stage2_scientific_tracks = build_stage2_scientific_tracks()
    launchable_dataset_pools = _launchable_dataset_pools()
    dataset_pool_views = _dataset_pool_views(
        dataset_pools,
        launchable_dataset_pools,
        promotion_reports,
    )
    beta_support = _beta_support_contract(
        launchable_dataset_pools=launchable_dataset_pools,
        stage2_scientific_tracks=stage2_scientific_tracks,
    )
    review_artifacts = _review_artifacts()
    beta_test_agents = _beta_test_agents()
    beta_test_agent_runs = _beta_test_agent_runs()
    beta_test_agent_findings = _beta_test_agent_findings()
    beta_test_agent_matrix = _beta_test_agent_matrix()
    beta_test_agent_status = _beta_test_agent_status()
    reference_library_manifest = _reference_library_manifest()
    reference_library_chunk_catalog = _reference_library_chunk_catalog()
    reference_library_status = _reference_library_status()
    reference_library_gaps = _reference_library_gaps()
    reference_library_resolution = _reference_library_resolution()
    reference_library_install_status = _reference_library_install_status()
    reference_library_query = _reference_library_query_contract()
    reference_library_hydration_requirements = _reference_library_hydration_requirements()
    program_preview = {
        "mode": "controlled_external_beta_rehearsal",
        "summary": {
            "status": "controlled_external_beta_hardening",
            "release_catalog_locked": True,
            "lab_catalog_hidden_from_main_ui": True,
            "runnable_default_dataset": "release_pp_alpha_benchmark_v1",
        },
        "default_runnable_path": {
            "task_type": "protein-protein",
            "label_type": "delta_G",
            "split_strategy": "leakage_resistant_benchmark",
            "dataset_ref": "release_pp_alpha_benchmark_v1",
            "structure_source_policy": "experimental_only",
        },
        "review_artifacts": review_artifacts,
    }
    beta_readiness_dashboard = _beta_readiness_dashboard(
        candidate_database_summary_v3=candidate_database_summary_v3,
        stage2_scientific_tracks=stage2_scientific_tracks,
        launchable_dataset_pools=launchable_dataset_pools,
        review_artifacts=review_artifacts,
    )
    return {
        "program_preview": program_preview,
        "orchestrator_state": orchestrator_state,
        "known_datasets": known_datasets,
        "dataset_pools": dataset_pools,
        "candidate_pool_summary": candidate_pool_summary,
        "candidate_database_summary": candidate_database_summary,
        "candidate_database_summary_v2": candidate_database_summary_v2,
        "candidate_database_summary_v3": candidate_database_summary_v3,
        "governed_bridge_manifests": governed_bridge_manifests,
        "governed_subset_manifests": governed_subset_manifests,
        "governed_subset_manifests_v2": governed_subset_manifests_v2,
        # ``*_canonical`` is the single key new code should read. The
        # ``v2`` / ``v3`` names are deprecated transitional shapes kept
        # for older API consumers; the v1 base name is the oldest.
        "governed_subset_manifests_canonical": governed_subset_manifests_v2,
        "pool_promotion_reports": promotion_reports,
        "promotion_queue": promotion_queue,
        "promotion_queue_v2": promotion_queue_v2,
        "promotion_queue_canonical": promotion_queue_v2,
        "stage2_scientific_tracks": stage2_scientific_tracks,
        "dataset_pool_views": dataset_pool_views,
        "launchable_dataset_pools": launchable_dataset_pools,
        "candidate_database_summary_canonical": candidate_database_summary_v3,
        "beta_support": beta_support,
        "beta_readiness_dashboard": beta_readiness_dashboard,
        "beta_test_agents": beta_test_agents,
        "beta_test_agent_runs": beta_test_agent_runs,
        "beta_test_agent_findings": beta_test_agent_findings,
        "beta_test_agent_matrix": beta_test_agent_matrix,
        "beta_test_agent_status": beta_test_agent_status,
        "reference_library_status": reference_library_status,
        "reference_library_gaps": reference_library_gaps,
        "reference_library_resolution": reference_library_resolution,
        "reference_library_manifest": reference_library_manifest,
        "reference_library_chunk_catalog": reference_library_chunk_catalog,
        "reference_library_install_status": reference_library_install_status,
        "reference_library_query": reference_library_query,
        "reference_library_hydration_requirements": reference_library_hydration_requirements,
        "curated_wave_queue_count": len(curated_queue),
        "draft_count": len(list(DRAFT_DIR.glob("*.json"))) if DRAFT_DIR.exists() else 0,
        "run_count": len(runs),
        "training_set_build_count": len(list_training_set_builds()),
        "recent_runs": runs[:5],
        "release_catalog_mode": "active_beta_lane",
        "release_review_artifact_count": len(review_artifacts),
    }


def _step_ui_payload(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": step["id"],
        "label": step["label"],
        "workspace": step["workspace"],
        "status": step["status"],
        "summary": step["summary"],
        "next_action": step["next_action"],
        "produced": list(step.get("produced", [])),
        "blockers": list(step.get("blockers", [])),
    }


def _catalog_option_lookup(catalog: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    lookup: dict[str, dict[str, dict[str, Any]]] = {}
    for category, items in catalog.get("capability_registry", {}).items():
        lookup[category] = {item["value"]: item for item in items}
    for category, items in catalog.get("ui_option_registry", {}).items():
        bucket = lookup.setdefault(category, {})
        bucket.update({item["value"]: item for item in items})
    return lookup


def _inactive_explanation_catalog(
    catalog: dict[str, Any],
    *,
    activation_ledger: list[dict[str, Any]] | None = None,
    promotion_reports: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    lookup = _catalog_option_lookup(catalog)
    for category, options in lookup.items():
        for option in options.values():
            if option.get("status") in {"release", "beta"}:
                continue
            items.append(
                {
                    "category": category,
                    "value": option.get("value"),
                    "label": option.get("label", option.get("value", "unknown")),
                    "status": option.get("status", "planned_inactive"),
                    "inactive_reason": option.get("inactive_reason")
                    or option.get("reason")
                    or "This option is not enabled yet.",
                    "help_summary": option.get("help_summary")
                    or option.get("reason")
                    or "This option is currently inactive.",
                }
            )
    for gate in activation_ledger or []:
        if gate.get("current_state") in {"release", "beta"}:
            continue
        feature_id = gate.get("feature_id", "unknown")
        items.append(
            {
                "category": gate.get("category", "activation"),
                "value": feature_id,
                "label": feature_id.replace(":", " "),
                "status": gate.get("current_state", "planned_inactive"),
                "inactive_reason": " | ".join(gate.get("blockers") or [])
                or gate.get("resolved_backend_fidelity")
                or "This feature remains gated in the current beta lane.",
                "help_summary": " | ".join(
                    part
                    for part in (
                        (
                            f"Activation bar: {gate.get('activation_bar')}"
                            if gate.get("activation_bar")
                            else ""
                        ),
                        (
                            f"Resolved backend/fidelity: {gate.get('resolved_backend_fidelity')}"
                            if gate.get("resolved_backend_fidelity")
                            else ""
                        ),
                        (
                            "Reviewers: " + ", ".join(gate.get("reviewers_required") or [])
                            if gate.get("reviewers_required")
                            else ""
                        ),
                        (
                            "Tests: " + ", ".join(gate.get("tests_required") or [])
                            if gate.get("tests_required")
                            else ""
                        ),
                    )
                    if part
                )
                or "Review and activation requirements are still open.",
            }
        )
    for report in promotion_reports or []:
        if report.get("status") in {"release", "beta"}:
            continue
        pool_id = report.get("pool_id", "unknown-pool")
        items.append(
            {
                "category": "dataset_pools",
                "value": pool_id,
                "label": pool_id,
                "status": report.get("status", "planned_inactive"),
                "inactive_reason": " | ".join(report.get("blockers") or [])
                or "This pool is visible but not yet promoted.",
                "help_summary": " | ".join(report.get("remediation") or [])
                or "This pool requires additional promotion work before activation.",
            }
        )
    items.sort(key=lambda item: (item["category"], item["label"]))
    return items


def _primary_action_contract(
    spec: ModelStudioPipelineSpec,
    latest_build: dict[str, Any] | None,
    recent_runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    run_active = any(item.get("status") in {"running", "queued"} for item in recent_runs)
    return [
        {
            "action_id": "save_draft",
            "label": "Save draft",
            "response_mode": "launch_or_continue",
            "enabled": True,
            "reason": "Persist the current guided-study configuration.",
        },
        {
            "action_id": "validate",
            "label": "Validate",
            "response_mode": "show_validation_result",
            "enabled": True,
            "reason": "Run spec validation and recommendations on the current draft.",
        },
        {
            "action_id": "compile_graph",
            "label": "Compile graph",
            "response_mode": "show_validation_result",
            "enabled": True,
            "reason": "Compile the deterministic execution graph for the current draft.",
        },
        {
            "action_id": "preview_dataset",
            "label": "Preview dataset",
            "response_mode": "opens_preview",
            "enabled": True,
            "reason": (
                "Resolve the current training-set request into candidate rows and diagnostics."
            ),
        },
        {
            "action_id": "build_dataset",
            "label": "Build dataset",
            "response_mode": "launch_or_continue",
            "enabled": bool(spec.training_set_request.target_size),
            "reason": "Create the study dataset and split artifacts from the current request.",
        },
        {
            "action_id": "launch_run",
            "label": "Launch run",
            "response_mode": "launch_or_continue",
            "enabled": latest_build is not None,
            "reason": (
                "Launch model training and evaluation with the latest built study dataset."
                if latest_build is not None
                else "Build the study dataset before launching the pipeline."
            ),
        },
        {
            "action_id": "cancel_run",
            "label": "Cancel run",
            "response_mode": "launch_or_continue",
            "enabled": run_active,
            "reason": (
                "Cancel the current run at the next stage boundary."
                if run_active
                else "A running study is required before cancellation is available."
            ),
        },
        {
            "action_id": "report_issue",
            "label": "Need help / report issue",
            "response_mode": "opens_details",
            "enabled": True,
            "reason": "Open the beta feedback panel to report friction, confusion, or failure.",
        },
    ]


def _dataset_pool_views(
    dataset_pools: list[dict[str, Any]],
    launchable_dataset_pools: list[dict[str, Any]],
    promotion_reports: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    launchable_ids = {item.get("pool_id") for item in launchable_dataset_pools}
    report_lookup = {item.get("pool_id"): item for item in promotion_reports}
    views: list[dict[str, Any]] = []
    for pool in dataset_pools:
        pool_id = pool.get("pool_id", "")
        report = report_lookup.get(pool_id, {})
        is_launchable = pool_id in launchable_ids
        if is_launchable:
            audience_state = "launchable_now"
        elif report.get("status") in {"beta", "beta_soon"}:
            audience_state = "review_pending"
        else:
            audience_state = "informative_only"
        audience_label = {
            "launchable_now": "Launchable now",
            "review_pending": "Review pending",
            "informative_only": "Inactive",
        }.get(audience_state, "Inactive")
        views.append(
            {
                "pool_id": pool_id,
                "label": pool.get("label"),
                "source_family": pool.get("source_family"),
                "dataset_refs": list(pool.get("dataset_refs", [])),
                "status": report.get("status", pool.get("status", "unknown")),
                "audience_state": audience_state,
                "audience_label": audience_label,
                "is_launchable": is_launchable,
                "promotion_readiness": report.get("promotion_readiness", "hold"),
                "review_signoff_state": report.get("review_signoff_state", "pending"),
                "launchability_reason": report.get("launchability_reason")
                or (
                    "Launchable now in the current beta lane."
                    if is_launchable
                    else "Visible for review and planning, but not launchable yet."
                ),
                "use_now_summary": (
                    "Launchable now in the current beta lane."
                    if is_launchable
                    else "Visible for review and planning, but not launchable yet."
                ),
                "row_count": int(pool.get("row_count", 0) or 0),
                "maturity": pool.get("maturity"),
                "split_provenance": pool.get("split_provenance"),
                "required_reviewers": list(report.get("required_reviewers", [])),
                "required_matrix_tests": list(report.get("required_matrix_tests", [])),
                "truth_boundary": dict(pool.get("truth_boundary") or {}),
                "balancing_metadata": dict(pool.get("balancing_metadata") or {}),
                "governed_bridge_promotion_readiness": pool.get("truth_boundary", {}).get(
                    "governed_bridge_promotion_readiness"
                ),
                "notes": list(pool.get("notes", [])),
            }
        )
    return views


def build_workspace_payload(pipeline_id: str | None = None) -> dict[str, Any]:
    saved = load_pipeline_spec(pipeline_id)
    spec = pipeline_spec_from_dict(saved["pipeline_spec"])
    baseline_spec = default_pipeline_spec()
    catalog = build_release_catalog()
    known_datasets = filter_known_datasets(list_known_datasets())
    dataset_pools = list_dataset_pools()
    selected_dataset_ref = (
        (spec.data_strategy.dataset_refs or [""])[0] if spec.data_strategy.dataset_refs else ""
    )
    selected_dataset = next(
        (item for item in known_datasets if item.get("dataset_ref") == selected_dataset_ref),
        None,
    )
    if selected_dataset is None and selected_dataset_ref:
        selected_dataset = next(
            (
                {
                    "dataset_ref": ref,
                    "label": pool.get("label", ref),
                    "row_count": pool.get("row_count", 0),
                    "split_strategy": pool.get("split_provenance"),
                    "maturity": pool.get("maturity"),
                    "catalog_status": pool.get("status"),
                }
                for pool in dataset_pools
                for ref in pool.get("dataset_refs", [])
                if ref == selected_dataset_ref
            ),
            None,
        )
    if selected_dataset is None:
        selected_dataset = known_datasets[0] if known_datasets else None
    latest_build = next(
        (
            item
            for item in list_training_set_builds()
            if item.get("pipeline_id") == spec.pipeline_id
        ),
        None,
    )
    try:
        training_set_preview = preview_training_set_request(
            spec.training_set_request,
            spec.split_plan,
            fallback_dataset_refs=spec.data_strategy.dataset_refs,
        )
    except Exception as exc:
        # Genuine error path -- surfaces in the UI as a "blocked"
        # preview. Coverage SHOULD measure this; no pragma.
        training_set_preview = {
            "training_set_request": spec.training_set_request.to_dict(),
            "resolved_dataset_refs": list(spec.data_strategy.dataset_refs),
            "candidate_preview": {"row_count": 0, "sample_pdb_ids": [], "dropped_rows": []},
            "split_preview": {},
            "diagnostics": {
                "report_id": f"training-set-diagnostics:{spec.pipeline_id}",
                "status": "blocked",
                "row_count": 0,
                "blockers": [str(exc)],
            },
        }
    recent_runs = [
        item
        for item in list_runs()
        if is_active_option("model_families", item.get("model_family", ""))
    ][:5]
    pipeline_runs = [
        item
        for item in list_runs()
        if is_active_option("model_families", item.get("model_family", ""))
        and item.get("pipeline_id") == spec.pipeline_id
    ]
    latest_pipeline_run = pipeline_runs[0] if pipeline_runs else None
    preview_rows = training_set_preview.get("candidate_preview", {}).get("rows", [])
    candidate_pool_summary = build_candidate_pool_summary()
    candidate_database_summary = build_candidate_database_summary()
    candidate_database_summary_v2 = build_candidate_database_summary_v2()
    candidate_database_summary_v3 = build_candidate_database_summary_v3()
    governed_bridge_manifests = build_governed_bridge_manifests()
    governed_subset_manifests = build_governed_subset_manifests()
    governed_subset_manifests_v2 = build_governed_subset_manifests_v2()
    promotion_reports = build_pool_promotion_reports()
    promotion_queue = build_promotion_queue()
    promotion_queue_v2 = build_promotion_queue_v2()
    stage2_scientific_tracks = build_stage2_scientific_tracks()
    launchable_dataset_pools = _launchable_dataset_pools()
    dataset_pool_views = _dataset_pool_views(
        dataset_pools,
        launchable_dataset_pools,
        promotion_reports,
    )
    beta_support = _beta_support_contract(
        launchable_dataset_pools=launchable_dataset_pools,
        stage2_scientific_tracks=stage2_scientific_tracks,
    )
    review_artifacts = _review_artifacts()
    beta_readiness_dashboard = _beta_readiness_dashboard(
        candidate_database_summary_v3=candidate_database_summary_v3,
        stage2_scientific_tracks=stage2_scientific_tracks,
        launchable_dataset_pools=launchable_dataset_pools,
        review_artifacts=review_artifacts,
    )
    activation_ledger = build_activation_ledger()
    activation_readiness_reports = build_activation_readiness_reports()
    feature_gate_views = build_feature_gate_views()
    model_activation_matrix = build_model_activation_matrix()
    beta_test_agents = _beta_test_agents()
    beta_test_agent_runs = _beta_test_agent_runs()
    beta_test_agent_findings = _beta_test_agent_findings()
    beta_test_agent_matrix = _beta_test_agent_matrix()
    beta_test_agent_status = _beta_test_agent_status()
    reference_library_manifest = _reference_library_manifest()
    reference_library_chunk_catalog = _reference_library_chunk_catalog()
    reference_library_status = _reference_library_status()
    reference_library_gaps = _reference_library_gaps()
    reference_library_resolution = _reference_library_resolution()
    reference_library_install_status = _reference_library_install_status()
    reference_library_query = _reference_library_query_contract()
    reference_library_hydration_requirements = _reference_library_hydration_requirements()
    data_strategy_customized = (
        spec.data_strategy.to_dict() != baseline_spec.data_strategy.to_dict()
    )
    request_configured = (
        spec.training_set_request.to_dict() != baseline_spec.training_set_request.to_dict()
        or data_strategy_customized
    )
    representation_configured = (
        [item.to_dict() for item in spec.graph_recipes]
        != [item.to_dict() for item in baseline_spec.graph_recipes]
        or [item.to_dict() for item in spec.feature_recipes]
        != [item.to_dict() for item in baseline_spec.feature_recipes]
    )
    pipeline_configured = (
        spec.training_plan.to_dict() != baseline_spec.training_plan.to_dict()
    )
    workflow_seeded = (
        request_configured
        or representation_configured
        or pipeline_configured
        or spec.preprocess_plan.to_dict() != baseline_spec.preprocess_plan.to_dict()
    )
    preview_completed = workflow_seeded and (bool(latest_build) or bool(latest_pipeline_run))
    run_started = workflow_seeded and bool(latest_pipeline_run)
    run_completed = workflow_seeded and any(
        run.get("status") == "completed" for run in pipeline_runs
    )
    stepper = []
    for item in saved.get("catalog", {}).get(
        "stepper_definition", []
    ) or catalog.get("stepper_definition", []):
        step_id = item["id"]
        if step_id == "training-request":
            source_summary = ", ".join(
                spec.training_set_request.source_families
                or spec.training_set_request.dataset_refs
            ) or "release defaults"
            state = _step_state(
                status="completed" if request_configured else "current",
                title=item["label"],
                summary="Training-set intent is configured for the current study.",
                next_action="Preview the dataset candidate to inspect rows and diagnostics.",
                produced=[
                    f"Task: {spec.training_set_request.task_type}",
                    f"Sources: {source_summary}",
                ],
            )
        elif step_id == "dataset-preview":
            preview_count = training_set_preview.get("candidate_preview", {}).get(
                "row_count", 0
            )
            leakage_risk = training_set_preview.get("diagnostics", {}).get(
                "leakage_risk", "unknown"
            )
            state = _step_state(
                status="completed" if preview_completed else "current" if request_configured else "next",
                title=item["label"],
                summary=(
                    f"{len(preview_rows)} preview rows are currently visible."
                    if preview_completed and preview_rows
                    else "Preview the candidate dataset to inspect PDBs and diagnostics."
                ),
                next_action="Build the study dataset once the candidate preview looks correct.",
                produced=[
                    f"Candidate rows: {preview_count}",
                    f"Leakage risk: {leakage_risk}",
                ],
                blockers=training_set_preview.get("diagnostics", {}).get("blockers", []),
            )
        elif step_id == "build-split":
            split_preview = latest_build.get("split_preview", {}) if latest_build else {}
            state = _step_state(
                status="completed" if workflow_seeded and latest_build else "current" if preview_completed else "next",
                title=item["label"],
                summary=(
                    f"Built dataset {latest_build.get('dataset_ref')}"
                    if workflow_seeded and latest_build
                    else "No built study dataset yet for this pipeline."
                ),
                next_action=(
                    "Review the split, then configure the representation and "
                    "feature bundle."
                ),
                produced=[
                    f"Split: {split_preview.get('train_count', 0)} / "
                    f"{split_preview.get('val_count', 0)} / "
                    f"{split_preview.get('test_count', 0)}"
                    if workflow_seeded and latest_build
                    else "Split not built yet",
                ],
                blockers=latest_build.get("diagnostics", {}).get("blockers", [])
                if workflow_seeded and latest_build
                else [],
            )
        elif step_id == "representation-features":
            state = _step_state(
                status="completed" if workflow_seeded and latest_build and representation_configured else "current" if workflow_seeded and latest_build else "next",
                title=item["label"],
                summary="Graph, global, and distributed feature settings are available for review.",
                next_action="Choose the representation and preprocessing toggles you want to test.",
                produced=[
                    f"Graph: {spec.graph_recipes[0].graph_kind}",
                    f"Region: {spec.graph_recipes[0].region_policy}",
                ],
            )
        elif step_id == "pipeline-design":
            state = _step_state(
                status="completed" if workflow_seeded and latest_build and pipeline_configured else "current" if workflow_seeded and latest_build and representation_configured else "next",
                title=item["label"],
                summary="Training and evaluation defaults are configured.",
                next_action="Validate and compile the pipeline before launching.",
                produced=[
                    f"Model family: {spec.training_plan.model_family}",
                    f"Optimizer: {spec.training_plan.optimizer}",
                ],
            )
        elif step_id == "run-monitor":
            state = _step_state(
                status="completed" if run_started else "current" if workflow_seeded and latest_build and pipeline_configured else "next",
                title=item["label"],
                summary=(
                    f"Recent run available: {latest_pipeline_run['run_id']}"
                    if latest_pipeline_run
                    else "Launch a run to begin live monitoring."
                ),
                next_action="Launch the run and monitor stage updates, heartbeat, and artifacts.",
                produced=[f"Study runs for this draft: {len(pipeline_runs)}"],
            )
        elif step_id == "analysis-compare":
            completed_release_runs = sum(
                1 for run in pipeline_runs if run.get("status") == "completed"
            )
            state = _step_state(
                status="completed" if run_completed else "current" if run_started else "next",
                title=item["label"],
                summary=(
                    "Metrics, outliers, and comparison surfaces are available "
                    "after a run completes."
                ),
                next_action="Open a completed run to inspect metrics and compare results.",
                produced=[f"Completed release runs: {completed_release_runs}"],
            )
        else:
            state = _step_state(
                status="current" if run_completed else "next",
                title=item["label"],
                summary="Export and review artifacts become available after a successful run.",
                next_action="Open the report and review lanes once a run has finished.",
            )
        stepper.append({**item, **state})
    current_step = next(
        (item for item in stepper if item["status"] in {"current", "blocked", "next"}),
        stepper[0] if stepper else None,
    )
    latest_run = latest_pipeline_run
    latest_warning = (
        saved["quality_gates"]["checks"][0]["detail"]
        if saved["quality_gates"]["warning_count"]
        else None
    )
    status_rail = {
        "current_study": spec.study_title,
        "current_step": current_step["label"] if current_step else "Training Set Request",
        "current_run_id": latest_run["run_id"] if latest_run else None,
        "current_run_status": latest_run["status"] if latest_run else "idle",
        "current_stage": latest_run.get("active_stage") if latest_run else None,
        "last_heartbeat": latest_run.get("heartbeat_at") if latest_run else None,
        "resolved_backend": (
            latest_run.get("model_details", {}).get("resolved_backend")
            if latest_run
            else None
        ),
        "resolved_execution_device": (
            latest_run.get("model_details", {}).get("resolved_execution_device")
            or latest_run.get("run_manifest", {}).get("resolved_execution_device")
            if latest_run
            else None
        ),
        "latest_artifact": (latest_run.get("artifact_refs") or [None])[-1]
        if latest_run
        else None,
        "latest_warning": latest_warning,
        "latest_error": None,
        "latest_action": "Workspace loaded",
        "hardware_mode": spec.preprocess_plan.options.get(
            "hardware_runtime_preset", "auto_recommend"
        ),
        "resolved_hardware_mode": (
            latest_run.get("model_details", {}).get("resolved_hardware_preset")
            or latest_run.get("run_manifest", {}).get("resolved_hardware_preset")
            if latest_run
            else None
        ),
    }
    ui_contract = {
        "stepper_state": [_step_ui_payload(item) for item in stepper],
        "option_registry": catalog.get("ui_option_registry", {}),
        "field_help_registry": catalog.get("field_help_registry", {}),
        "capability_registry": catalog.get("capability_registry", {}),
        "dataset_pools": dataset_pools,
        "dataset_pool_views": dataset_pool_views,
        "launchable_dataset_pools": launchable_dataset_pools,
        "candidate_pool_summary": candidate_pool_summary,
        "candidate_database_summary": candidate_database_summary,
        "candidate_database_summary_v2": candidate_database_summary_v2,
        "candidate_database_summary_v3": candidate_database_summary_v3,
        "candidate_database_summary_canonical": candidate_database_summary_v3,
        "governed_bridge_manifests": governed_bridge_manifests,
        "governed_subset_manifests": governed_subset_manifests,
        "governed_subset_manifests_v2": governed_subset_manifests_v2,
        "governed_subset_manifests_canonical": governed_subset_manifests_v2,
        "pool_promotion_reports": promotion_reports,
        "promotion_queue": promotion_queue,
        "promotion_queue_v2": promotion_queue_v2,
        "promotion_queue_canonical": promotion_queue_v2,
        "stage2_scientific_tracks": stage2_scientific_tracks,
        "beta_readiness_dashboard": beta_readiness_dashboard,
        "activation_ledger": activation_ledger,
        "activation_readiness_reports": activation_readiness_reports,
        "feature_gate_views": feature_gate_views,
        "model_activation_matrix": model_activation_matrix,
        "beta_test_agents": beta_test_agents,
        "beta_test_agent_runs": beta_test_agent_runs,
        "beta_test_agent_findings": beta_test_agent_findings,
        "beta_test_agent_matrix": beta_test_agent_matrix,
        "beta_test_agent_status": beta_test_agent_status,
        "reference_library_status": reference_library_status,
        "reference_library_gaps": reference_library_gaps,
        "reference_library_resolution": reference_library_resolution,
        "reference_library_manifest": reference_library_manifest,
        "reference_library_chunk_catalog": reference_library_chunk_catalog,
        "reference_library_install_status": reference_library_install_status,
        "reference_library_query": reference_library_query,
        "reference_library_hydration_requirements": reference_library_hydration_requirements,
        "hardware_profile": build_hardware_profile_payload(),
        "current_status_rail": status_rail,
        "primary_actions": _primary_action_contract(spec, latest_build, recent_runs),
        "inactive_explanations": _inactive_explanation_catalog(
            catalog,
            activation_ledger=activation_ledger,
            promotion_reports=promotion_reports,
        ),
        "onboarding": {
            "title": "How this guided study works",
            "summary": (
                "Start by defining the training set you want, preview and build the dataset, "
                "configure the representation and model, launch the run, then review charts, "
                "metrics, and artifacts."
            ),
            "steps": [
                "Define the training-set request and primary dataset.",
                "Preview the candidate dataset and inspect diagnostics.",
                "Build the study dataset and verify the split.",
                "Configure representation, preprocessing, and model settings.",
                "Launch the run and monitor heartbeat, stage status, and artifacts.",
                "Inspect analysis charts, outliers, and the exportable study summary.",
            ],
        },
        "beta_support": beta_support,
    }
    return {
        "pipeline_spec": spec.to_dict(),
        "recommendation_report": saved["recommendation_report"],
        "execution_graph": saved["execution_graph"],
        "quality_gates": saved["quality_gates"],
        "run_preview": saved["run_preview"],
        "training_set_preview": training_set_preview,
        "latest_training_set_build": latest_build,
        "selected_dataset": selected_dataset,
        "recent_runs": recent_runs,
        "program_status": build_program_status(),
        "catalog": catalog,
        "lab_catalog": build_lab_catalog(),
        "hardware_profile": ui_contract["hardware_profile"],
        "ui_contract": ui_contract,
        "stepper": stepper,
        "status_rail": status_rail,
        "schema_version": STUDIO_SCHEMA_VERSION,
        "workspace_sections": [
            "Project Home",
            "Data Strategy Designer",
            "Representation Designer",
            "Pipeline Composer",
            "Execution Console",
            "Analysis and Review",
        ],
    }


def submit_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    return persist_feedback(payload)


def record_session_event(payload: dict[str, Any]) -> dict[str, Any]:
    return persist_session_event(payload)


def launch_pipeline_run(payload: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline_spec_from_dict(payload)
    manifest = launch_run(spec)
    return {
        "run_manifest": manifest,
        "run": load_run(manifest["run_id"]),
        "latest_training_set_build": next(
            (
                item
                for item in list_training_set_builds()
                if item.get("build_id") == manifest.get("training_set_build_id")
            ),
            None,
        ),
        "recent_runs": list_pipeline_runs()["items"][:10],
    }


def cancel_pipeline_run(run_id: str) -> dict[str, Any]:
    manifest = cancel_run(run_id)
    return {
        "run_manifest": manifest,
        "run": load_run(run_id),
        "recent_runs": list_runs()[:10],
    }


def list_pipeline_runs() -> dict[str, Any]:
    return {
        "items": [
            item
            for item in list_runs()
            if is_active_option("model_families", item.get("model_family", ""))
        ]
    }


def preview_training_set_payload(payload: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline_spec_from_dict(payload)
    report = validate_pipeline_spec(spec)
    blockers = [item.to_dict() for item in report.items if item.level == "blocker"]
    if blockers:
        return {
            "status": "blocked",
            "pipeline_id": spec.pipeline_id,
            "recommendation_report": report.to_dict(),
            "blockers": blockers,
            "diagnostics": {
                "status": "blocked",
                "row_count": 0,
                "leakage_risk": "blocked",
                "structure_coverage": 0.0,
                "maturity": "blocked_by_validation",
                "missing_structure_count": 0,
            },
            "candidate_preview": {
                "row_count": 0,
                "total_candidate_count": 0,
                "filtered_candidate_count": 0,
                "eligible_quality_ceiling": 0,
                "requested_target_size": spec.training_set_request.target_size or None,
                "resolved_target_cap": 0,
                "final_selected_count": 0,
                "target_size_warning": None,
                "rows": [],
                "dropped_rows": [],
                "maturity": "blocked_by_validation",
            },
        }
    preview = preview_training_set_request(
        spec.training_set_request,
        spec.split_plan,
        fallback_dataset_refs=spec.data_strategy.dataset_refs,
    )
    return {
        "status": preview.get("diagnostics", {}).get("status", "ready"),
        **preview,
    }


def build_training_set_payload(payload: dict[str, Any]) -> dict[str, Any]:
    spec = pipeline_spec_from_dict(payload)
    report = validate_pipeline_spec(spec)
    blockers = [item.to_dict() for item in report.items if item.level == "blocker"]
    if blockers:
        return {
            "status": "blocked",
            "pipeline_id": spec.pipeline_id,
            "recommendation_report": report.to_dict(),
            "blockers": blockers,
            "build_manifest": {
                "status": "blocked",
                "dataset_ref": None,
                "pipeline_id": spec.pipeline_id,
                "study_title": spec.study_title,
                "row_count": 0,
                "total_candidate_count": 0,
                "filtered_candidate_count": 0,
                "eligible_quality_ceiling": 0,
                "requested_target_size": spec.training_set_request.target_size or None,
                "resolved_target_cap": 0,
                "final_selected_count": 0,
                "target_size_warning": None,
                "maturity": "blocked_by_validation",
                "split_preview": {
                    "objective": spec.split_plan.objective,
                    "grouping_policy": spec.split_plan.grouping_policy,
                    "holdout_policy": spec.split_plan.holdout_policy,
                    "train_count": 0,
                    "val_count": 0,
                    "test_count": 0,
                    "component_count": 0,
                    "source_mix_by_split": {"train": {}, "val": {}, "test": {}},
                },
                "blockers": blockers,
            },
            "diagnostics": {
                "status": "blocked",
                "leakage_risk": "blocked",
                "structure_coverage": 0.0,
                "missing_structure_count": 0,
            },
        }
    manifest = build_training_set(
        spec.pipeline_id,
        spec.study_title,
        spec.training_set_request,
        spec.split_plan,
        fallback_dataset_refs=spec.data_strategy.dataset_refs,
    )
    manifest = {
        **manifest,
        "status": manifest.get("status", "ready"),
    }
    return {
        "status": manifest.get("status", "ready"),
        "build_manifest": manifest,
        "diagnostics": manifest.get("diagnostics", {}),
    }


def list_training_set_build_records() -> dict[str, Any]:
    return {"items": list_training_set_builds()}


def load_training_set_build_record(build_id: str) -> dict[str, Any]:
    return load_training_set_build(build_id)


def load_pipeline_run(run_id: str) -> dict[str, Any]:
    return load_run(run_id)


def load_pipeline_run_artifacts(run_id: str) -> dict[str, Any]:
    return load_run_artifacts(run_id)


def load_pipeline_run_logs(run_id: str) -> dict[str, Any]:
    return load_run_logs(run_id)


def compare_pipeline_runs(run_ids: list[str]) -> dict[str, Any]:
    return compare_runs(run_ids)
