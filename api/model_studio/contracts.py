from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from api.model_studio.capabilities import is_active_option, option_reason


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _dedupe_text(values: Sequence[Any] | None) -> tuple[str, ...]:
    if not values:
        return ()
    ordered: dict[str, str] = {}
    for value in values:
        text = _clean_text(value)
        if text:
            ordered.setdefault(text.casefold(), text)
    return tuple(ordered.values())


def _as_mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True, slots=True)
class FeatureRecipeSpec:
    recipe_id: str
    node_feature_policy: str
    edge_feature_policy: str
    global_feature_sets: tuple[str, ...] = ()
    distributed_feature_sets: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipe_id", _clean_text(self.recipe_id))
        object.__setattr__(self, "node_feature_policy", _clean_text(self.node_feature_policy))
        object.__setattr__(self, "edge_feature_policy", _clean_text(self.edge_feature_policy))
        object.__setattr__(self, "global_feature_sets", _dedupe_text(self.global_feature_sets))
        object.__setattr__(
            self,
            "distributed_feature_sets",
            _dedupe_text(self.distributed_feature_sets),
        )
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.recipe_id:
            raise ValueError("recipe_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GraphRecipeSpec:
    recipe_id: str
    graph_kind: str
    region_policy: str
    node_granularity: str
    encoding_policy: str
    feature_recipe_id: str
    partner_awareness: str = "symmetric"
    include_waters: bool = False
    include_salt_bridges: bool = False
    include_contact_shell: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "recipe_id", _clean_text(self.recipe_id))
        object.__setattr__(self, "graph_kind", _clean_text(self.graph_kind))
        object.__setattr__(self, "region_policy", _clean_text(self.region_policy))
        object.__setattr__(self, "node_granularity", _clean_text(self.node_granularity))
        object.__setattr__(self, "encoding_policy", _clean_text(self.encoding_policy))
        object.__setattr__(self, "feature_recipe_id", _clean_text(self.feature_recipe_id))
        object.__setattr__(self, "partner_awareness", _clean_text(self.partner_awareness))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.recipe_id:
            raise ValueError("recipe_id must not be empty")
        if not self.feature_recipe_id:
            raise ValueError("feature_recipe_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DataStrategySpec:
    strategy_id: str
    task_type: str
    label_type: str
    label_transform: str
    split_strategy: str
    structure_source_policy: str
    graph_recipe_ids: tuple[str, ...] = ()
    feature_recipe_ids: tuple[str, ...] = ()
    dataset_refs: tuple[str, ...] = ()
    audit_requirements: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_id", _clean_text(self.strategy_id))
        object.__setattr__(self, "task_type", _clean_text(self.task_type))
        object.__setattr__(self, "label_type", _clean_text(self.label_type))
        object.__setattr__(self, "label_transform", _clean_text(self.label_transform))
        object.__setattr__(self, "split_strategy", _clean_text(self.split_strategy))
        object.__setattr__(
            self,
            "structure_source_policy",
            _clean_text(self.structure_source_policy),
        )
        object.__setattr__(self, "graph_recipe_ids", _dedupe_text(self.graph_recipe_ids))
        object.__setattr__(self, "feature_recipe_ids", _dedupe_text(self.feature_recipe_ids))
        object.__setattr__(self, "dataset_refs", _dedupe_text(self.dataset_refs))
        object.__setattr__(self, "audit_requirements", _dedupe_text(self.audit_requirements))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.strategy_id:
            raise ValueError("strategy_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrainingSetRequestSpec:
    request_id: str
    task_type: str
    label_type: str
    structure_source_policy: str
    source_families: tuple[str, ...] = ()
    dataset_refs: tuple[str, ...] = ()
    target_size: int = 0
    acceptable_fidelity: str = "pilot_ready"
    inclusion_filters: dict[str, Any] = field(default_factory=dict)
    exclusion_filters: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _clean_text(self.request_id))
        object.__setattr__(self, "task_type", _clean_text(self.task_type))
        object.__setattr__(self, "label_type", _clean_text(self.label_type))
        object.__setattr__(
            self,
            "structure_source_policy",
            _clean_text(self.structure_source_policy),
        )
        object.__setattr__(self, "source_families", _dedupe_text(self.source_families))
        object.__setattr__(self, "dataset_refs", _dedupe_text(self.dataset_refs))
        object.__setattr__(
            self,
            "acceptable_fidelity",
            _clean_text(self.acceptable_fidelity) or "pilot_ready",
        )
        object.__setattr__(self, "inclusion_filters", _as_mapping(self.inclusion_filters))
        object.__setattr__(self, "exclusion_filters", _as_mapping(self.exclusion_filters))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.request_id:
            raise ValueError("request_id must not be empty")
        if self.target_size < 0:
            raise ValueError("target_size must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PreprocessPlanSpec:
    plan_id: str
    modules: tuple[str, ...]
    cache_policy: str
    source_policy: str
    shell_distance_angstroms: float | None = None
    shell_strategy: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _clean_text(self.plan_id))
        object.__setattr__(self, "modules", _dedupe_text(self.modules))
        object.__setattr__(self, "cache_policy", _clean_text(self.cache_policy))
        object.__setattr__(self, "source_policy", _clean_text(self.source_policy))
        object.__setattr__(self, "shell_strategy", _clean_text(self.shell_strategy) or None)
        object.__setattr__(self, "options", _as_mapping(self.options))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.plan_id:
            raise ValueError("plan_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SplitPlanSpec:
    plan_id: str
    objective: str
    grouping_policy: str
    holdout_policy: str
    train_fraction: float = 0.7
    val_fraction: float = 0.1
    test_fraction: float = 0.2
    hard_constraints: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _clean_text(self.plan_id))
        object.__setattr__(self, "objective", _clean_text(self.objective))
        object.__setattr__(self, "grouping_policy", _clean_text(self.grouping_policy))
        object.__setattr__(self, "holdout_policy", _clean_text(self.holdout_policy))
        object.__setattr__(self, "hard_constraints", _dedupe_text(self.hard_constraints))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.plan_id:
            raise ValueError("plan_id must not be empty")
        total = self.train_fraction + self.val_fraction + self.test_fraction
        if min(self.train_fraction, self.val_fraction, self.test_fraction) < 0:
            raise ValueError("split fractions must be non-negative")
        if not math.isclose(total, 1.0, rel_tol=1e-6, abs_tol=1e-6):
            raise ValueError("train_fraction + val_fraction + test_fraction must equal 1.0")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExampleMaterializationSpec:
    spec_id: str
    graph_recipe_ids: tuple[str, ...]
    feature_recipe_ids: tuple[str, ...]
    preprocess_modules: tuple[str, ...]
    region_policy: str
    cache_policy: str
    include_global_features: bool = True
    include_distributed_features: bool = True
    include_graph_payloads: bool = True
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_id", _clean_text(self.spec_id))
        object.__setattr__(self, "graph_recipe_ids", _dedupe_text(self.graph_recipe_ids))
        object.__setattr__(self, "feature_recipe_ids", _dedupe_text(self.feature_recipe_ids))
        object.__setattr__(self, "preprocess_modules", _dedupe_text(self.preprocess_modules))
        object.__setattr__(self, "region_policy", _clean_text(self.region_policy))
        object.__setattr__(self, "cache_policy", _clean_text(self.cache_policy))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.spec_id:
            raise ValueError("spec_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrainingPlanSpec:
    plan_id: str
    model_family: str
    architecture: str
    optimizer: str
    scheduler: str
    loss_function: str
    epoch_budget: int
    batch_policy: str
    mixed_precision: str
    uncertainty_head: str | None = None
    hyperparameters: dict[str, Any] = field(default_factory=dict)
    ablations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _clean_text(self.plan_id))
        object.__setattr__(self, "model_family", _clean_text(self.model_family))
        object.__setattr__(self, "architecture", _clean_text(self.architecture))
        object.__setattr__(self, "optimizer", _clean_text(self.optimizer))
        object.__setattr__(self, "scheduler", _clean_text(self.scheduler))
        object.__setattr__(self, "loss_function", _clean_text(self.loss_function))
        object.__setattr__(self, "batch_policy", _clean_text(self.batch_policy))
        object.__setattr__(self, "mixed_precision", _clean_text(self.mixed_precision))
        object.__setattr__(self, "uncertainty_head", _clean_text(self.uncertainty_head) or None)
        object.__setattr__(self, "hyperparameters", _as_mapping(self.hyperparameters))
        object.__setattr__(self, "ablations", _dedupe_text(self.ablations))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.plan_id:
            raise ValueError("plan_id must not be empty")
        if self.epoch_budget < 1:
            raise ValueError("epoch_budget must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EvaluationPlanSpec:
    plan_id: str
    metric_families: tuple[str, ...]
    robustness_slices: tuple[str, ...]
    outlier_policy: str
    attribution_policy: str
    leakage_checks: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _clean_text(self.plan_id))
        object.__setattr__(self, "metric_families", _dedupe_text(self.metric_families))
        object.__setattr__(self, "robustness_slices", _dedupe_text(self.robustness_slices))
        object.__setattr__(self, "outlier_policy", _clean_text(self.outlier_policy))
        object.__setattr__(self, "attribution_policy", _clean_text(self.attribution_policy))
        object.__setattr__(self, "leakage_checks", _dedupe_text(self.leakage_checks))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.plan_id:
            raise ValueError("plan_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RecommendationItem:
    level: str
    category: str
    message: str
    action: str | None = None
    related_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "level", _clean_text(self.level))
        object.__setattr__(self, "category", _clean_text(self.category))
        object.__setattr__(self, "message", _clean_text(self.message))
        object.__setattr__(self, "action", _clean_text(self.action) or None)
        object.__setattr__(self, "related_fields", _dedupe_text(self.related_fields))
        if not self.level or not self.category or not self.message:
            raise ValueError("recommendation items require level, category, and message")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class TrainingSetDiagnosticsReport:
    report_id: str
    status: str
    row_count: int
    train_count: int = 0
    val_count: int = 0
    test_count: int = 0
    structure_coverage: float = 0.0
    missing_structure_count: int = 0
    label_min: float | None = None
    label_max: float | None = None
    label_mean: float | None = None
    leakage_risk: str = "unknown"
    source_breakdown: dict[str, int] = field(default_factory=dict)
    drop_reason_breakdown: dict[str, int] = field(default_factory=dict)
    drop_source_breakdown: dict[str, dict[str, int]] = field(default_factory=dict)
    missing_structure_rate: float = 0.0
    resolution_filter_rate: float = 0.0
    items: tuple[RecommendationItem, ...] = ()
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _clean_text(self.report_id))
        object.__setattr__(self, "status", _clean_text(self.status))
        object.__setattr__(self, "source_breakdown", _as_mapping(self.source_breakdown))
        object.__setattr__(self, "drop_reason_breakdown", _as_mapping(self.drop_reason_breakdown))
        object.__setattr__(
            self,
            "drop_source_breakdown",
            {
                _clean_text(key): _as_mapping(value)
                for key, value in (self.drop_source_breakdown or {}).items()
                if _clean_text(key)
            },
        )
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.report_id or not self.status:
            raise ValueError("diagnostics reports require report_id and status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "status": self.status,
            "row_count": self.row_count,
            "train_count": self.train_count,
            "val_count": self.val_count,
            "test_count": self.test_count,
            "structure_coverage": self.structure_coverage,
            "missing_structure_count": self.missing_structure_count,
            "label_min": self.label_min,
            "label_max": self.label_max,
            "label_mean": self.label_mean,
            "leakage_risk": self.leakage_risk,
            "source_breakdown": dict(self.source_breakdown),
            "drop_reason_breakdown": dict(self.drop_reason_breakdown),
            "drop_source_breakdown": {
                key: dict(value) for key, value in self.drop_source_breakdown.items()
            },
            "missing_structure_rate": self.missing_structure_rate,
            "resolution_filter_rate": self.resolution_filter_rate,
            "items": [item.to_dict() for item in self.items],
            "blockers": list(self.blockers),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class DatasetPoolManifest:
    pool_id: str
    label: str
    source_family: str
    dataset_refs: tuple[str, ...]
    row_count: int
    structure_coverage: float
    label_coverage: float
    split_provenance: str
    maturity: str
    truth_boundary: dict[str, Any] = field(default_factory=dict)
    balancing_metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "planned_inactive"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pool_id", _clean_text(self.pool_id))
        object.__setattr__(self, "label", _clean_text(self.label))
        object.__setattr__(self, "source_family", _clean_text(self.source_family))
        object.__setattr__(self, "dataset_refs", _dedupe_text(self.dataset_refs))
        object.__setattr__(self, "split_provenance", _clean_text(self.split_provenance))
        object.__setattr__(self, "maturity", _clean_text(self.maturity))
        object.__setattr__(self, "truth_boundary", _as_mapping(self.truth_boundary))
        object.__setattr__(self, "balancing_metadata", _as_mapping(self.balancing_metadata))
        object.__setattr__(self, "status", _clean_text(self.status) or "planned_inactive")
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.pool_id or not self.label or not self.source_family:
            raise ValueError("dataset pool manifests require pool_id, label, and source_family")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pool_id": self.pool_id,
            "label": self.label,
            "source_family": self.source_family,
            "dataset_refs": list(self.dataset_refs),
            "row_count": self.row_count,
            "structure_coverage": self.structure_coverage,
            "label_coverage": self.label_coverage,
            "split_provenance": self.split_provenance,
            "maturity": self.maturity,
            "truth_boundary": dict(self.truth_boundary),
            "balancing_metadata": dict(self.balancing_metadata),
            "status": self.status,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class CandidatePoolSummary:
    summary_id: str
    promoted_pool_ids: tuple[str, ...]
    total_row_count: int
    source_mix: dict[str, int] = field(default_factory=dict)
    assay_mix: dict[str, int] = field(default_factory=dict)
    label_bin_mix: dict[str, int] = field(default_factory=dict)
    bias_hotspots: tuple[str, ...] = ()
    recommended_inclusion_policy: str = ""
    leakage_risk: str = "unknown"
    leakage_risk_summary: str = ""
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_id", _clean_text(self.summary_id))
        object.__setattr__(self, "promoted_pool_ids", _dedupe_text(self.promoted_pool_ids))
        object.__setattr__(self, "source_mix", _as_mapping(self.source_mix))
        object.__setattr__(self, "assay_mix", _as_mapping(self.assay_mix))
        object.__setattr__(self, "label_bin_mix", _as_mapping(self.label_bin_mix))
        object.__setattr__(self, "bias_hotspots", _dedupe_text(self.bias_hotspots))
        object.__setattr__(
            self,
            "recommended_inclusion_policy",
            _clean_text(self.recommended_inclusion_policy),
        )
        object.__setattr__(self, "leakage_risk", _clean_text(self.leakage_risk) or "unknown")
        object.__setattr__(self, "leakage_risk_summary", _clean_text(self.leakage_risk_summary))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.summary_id:
            raise ValueError("candidate pool summaries require summary_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "promoted_pool_ids": list(self.promoted_pool_ids),
            "total_row_count": self.total_row_count,
            "source_mix": dict(self.source_mix),
            "assay_mix": dict(self.assay_mix),
            "label_bin_mix": dict(self.label_bin_mix),
            "bias_hotspots": list(self.bias_hotspots),
            "recommended_inclusion_policy": self.recommended_inclusion_policy,
            "leakage_risk": self.leakage_risk,
            "leakage_risk_summary": self.leakage_risk_summary,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class GovernedCandidateRow:
    canonical_row_id: str
    source_family: str
    source_provenance: tuple[str, ...] = ()
    measurement_type: str = "unknown"
    normalization_state: str = "unknown"
    partner_grouping_key: str = ""
    accession_grouping_key: str = ""
    structural_redundancy_key: str = ""
    admissibility: str = "unknown"
    governing_status: str = "unknown"
    training_eligibility: str = "non_governing"
    row_family: str = "unknown"
    balance_tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_row_id", _clean_text(self.canonical_row_id))
        object.__setattr__(self, "source_family", _clean_text(self.source_family))
        object.__setattr__(self, "source_provenance", _dedupe_text(self.source_provenance))
        object.__setattr__(self, "measurement_type", _clean_text(self.measurement_type) or "unknown")
        object.__setattr__(self, "normalization_state", _clean_text(self.normalization_state) or "unknown")
        object.__setattr__(self, "partner_grouping_key", _clean_text(self.partner_grouping_key))
        object.__setattr__(self, "accession_grouping_key", _clean_text(self.accession_grouping_key))
        object.__setattr__(self, "structural_redundancy_key", _clean_text(self.structural_redundancy_key))
        object.__setattr__(self, "admissibility", _clean_text(self.admissibility) or "unknown")
        object.__setattr__(self, "governing_status", _clean_text(self.governing_status) or "unknown")
        object.__setattr__(self, "training_eligibility", _clean_text(self.training_eligibility) or "non_governing")
        object.__setattr__(self, "row_family", _clean_text(self.row_family) or "unknown")
        object.__setattr__(self, "balance_tags", _dedupe_text(self.balance_tags))
        if not self.canonical_row_id or not self.source_family:
            raise ValueError("governed candidate rows require canonical_row_id and source_family")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GovernedBridgeManifest:
    bridge_id: str
    source_family: str
    row_count: int
    readiness_counts: dict[str, int] = field(default_factory=dict)
    provenance_completeness: str = "unknown"
    normalization_completeness: str = "unknown"
    admissibility_completeness: str = "unknown"
    governing_ready_count: int = 0
    promotion_readiness: str = "hold"
    launchability_reason: str = ""
    sample_rows: tuple[dict[str, Any], ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bridge_id", _clean_text(self.bridge_id))
        object.__setattr__(self, "source_family", _clean_text(self.source_family))
        object.__setattr__(self, "readiness_counts", _as_mapping(self.readiness_counts))
        object.__setattr__(self, "provenance_completeness", _clean_text(self.provenance_completeness) or "unknown")
        object.__setattr__(self, "normalization_completeness", _clean_text(self.normalization_completeness) or "unknown")
        object.__setattr__(self, "admissibility_completeness", _clean_text(self.admissibility_completeness) or "unknown")
        object.__setattr__(self, "promotion_readiness", _clean_text(self.promotion_readiness) or "hold")
        object.__setattr__(self, "launchability_reason", _clean_text(self.launchability_reason))
        object.__setattr__(self, "sample_rows", tuple(dict(item) for item in self.sample_rows))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.bridge_id or not self.source_family:
            raise ValueError("governed bridge manifests require bridge_id and source_family")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_id": self.bridge_id,
            "source_family": self.source_family,
            "row_count": self.row_count,
            "readiness_counts": dict(self.readiness_counts),
            "provenance_completeness": self.provenance_completeness,
            "normalization_completeness": self.normalization_completeness,
            "admissibility_completeness": self.admissibility_completeness,
            "governing_ready_count": self.governing_ready_count,
            "promotion_readiness": self.promotion_readiness,
            "launchability_reason": self.launchability_reason,
            "sample_rows": [dict(item) for item in self.sample_rows],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class CandidateDatabaseSummary:
    summary_id: str
    total_governed_rows: int
    governing_ready_rows: int
    source_family_mix: dict[str, int] = field(default_factory=dict)
    assay_family_mix: dict[str, int] = field(default_factory=dict)
    label_bin_mix: dict[str, int] = field(default_factory=dict)
    redundancy_hotspots: tuple[str, ...] = ()
    bias_diagnostics: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_id", _clean_text(self.summary_id))
        object.__setattr__(self, "source_family_mix", _as_mapping(self.source_family_mix))
        object.__setattr__(self, "assay_family_mix", _as_mapping(self.assay_family_mix))
        object.__setattr__(self, "label_bin_mix", _as_mapping(self.label_bin_mix))
        object.__setattr__(self, "redundancy_hotspots", _dedupe_text(self.redundancy_hotspots))
        object.__setattr__(self, "bias_diagnostics", _dedupe_text(self.bias_diagnostics))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.summary_id:
            raise ValueError("candidate database summaries require summary_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "total_governed_rows": self.total_governed_rows,
            "governing_ready_rows": self.governing_ready_rows,
            "source_family_mix": dict(self.source_family_mix),
            "assay_family_mix": dict(self.assay_family_mix),
            "label_bin_mix": dict(self.label_bin_mix),
            "redundancy_hotspots": list(self.redundancy_hotspots),
            "bias_diagnostics": list(self.bias_diagnostics),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class CandidateDatabaseSummaryV2:
    summary_id: str
    total_governed_rows: int
    governing_ready_rows: int
    source_family_mix: dict[str, int] = field(default_factory=dict)
    assay_family_mix: dict[str, int] = field(default_factory=dict)
    label_bin_mix: dict[str, int] = field(default_factory=dict)
    governance_state_mix: dict[str, int] = field(default_factory=dict)
    promotion_ready_subset_count: int = 0
    redundancy_hotspots: tuple[str, ...] = ()
    bias_diagnostics: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_id", _clean_text(self.summary_id))
        object.__setattr__(self, "source_family_mix", _as_mapping(self.source_family_mix))
        object.__setattr__(self, "assay_family_mix", _as_mapping(self.assay_family_mix))
        object.__setattr__(self, "label_bin_mix", _as_mapping(self.label_bin_mix))
        object.__setattr__(self, "governance_state_mix", _as_mapping(self.governance_state_mix))
        object.__setattr__(self, "redundancy_hotspots", _dedupe_text(self.redundancy_hotspots))
        object.__setattr__(self, "bias_diagnostics", _dedupe_text(self.bias_diagnostics))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.summary_id:
            raise ValueError("candidate database summaries require summary_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "total_governed_rows": self.total_governed_rows,
            "governing_ready_rows": self.governing_ready_rows,
            "source_family_mix": dict(self.source_family_mix),
            "assay_family_mix": dict(self.assay_family_mix),
            "label_bin_mix": dict(self.label_bin_mix),
            "governance_state_mix": dict(self.governance_state_mix),
            "promotion_ready_subset_count": self.promotion_ready_subset_count,
            "redundancy_hotspots": list(self.redundancy_hotspots),
            "bias_diagnostics": list(self.bias_diagnostics),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class GovernedCandidateRowV3:
    canonical_row_id: str
    source_family: str
    source_provenance: tuple[str, ...] = ()
    measurement_family: str = "unknown"
    normalization_state: str = "unknown"
    provenance_completeness: str = "unknown"
    structure_backed_readiness: str = "unknown"
    partner_role_resolution_state: str = "unknown"
    partner_grouping_key: str = ""
    accession_grouping_key: str = ""
    uniref_grouping_key: str = ""
    redundancy_cluster_id: str = ""
    admissibility: str = "unknown"
    governance_state: str = "unknown"
    training_eligibility: str = "non_governing"
    row_family: str = "unknown"
    balance_tags: tuple[str, ...] = ()
    review_notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "canonical_row_id", _clean_text(self.canonical_row_id))
        object.__setattr__(self, "source_family", _clean_text(self.source_family))
        object.__setattr__(self, "source_provenance", _dedupe_text(self.source_provenance))
        object.__setattr__(self, "measurement_family", _clean_text(self.measurement_family) or "unknown")
        object.__setattr__(self, "normalization_state", _clean_text(self.normalization_state) or "unknown")
        object.__setattr__(self, "provenance_completeness", _clean_text(self.provenance_completeness) or "unknown")
        object.__setattr__(self, "structure_backed_readiness", _clean_text(self.structure_backed_readiness) or "unknown")
        object.__setattr__(self, "partner_role_resolution_state", _clean_text(self.partner_role_resolution_state) or "unknown")
        object.__setattr__(self, "partner_grouping_key", _clean_text(self.partner_grouping_key))
        object.__setattr__(self, "accession_grouping_key", _clean_text(self.accession_grouping_key))
        object.__setattr__(self, "uniref_grouping_key", _clean_text(self.uniref_grouping_key))
        object.__setattr__(self, "redundancy_cluster_id", _clean_text(self.redundancy_cluster_id))
        object.__setattr__(self, "admissibility", _clean_text(self.admissibility) or "unknown")
        object.__setattr__(self, "governance_state", _clean_text(self.governance_state) or "unknown")
        object.__setattr__(self, "training_eligibility", _clean_text(self.training_eligibility) or "non_governing")
        object.__setattr__(self, "row_family", _clean_text(self.row_family) or "unknown")
        object.__setattr__(self, "balance_tags", _dedupe_text(self.balance_tags))
        object.__setattr__(self, "review_notes", _dedupe_text(self.review_notes))
        if not self.canonical_row_id or not self.source_family:
            raise ValueError("governed candidate row v3 records require canonical_row_id and source_family")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GovernedSubsetManifestV2:
    subset_id: str
    label: str
    promoted_dataset_ref: str
    row_count: int
    source_rows: dict[str, int] = field(default_factory=dict)
    balancing_policy: str = "balance_first"
    source_family_mix: dict[str, int] = field(default_factory=dict)
    assay_family_mix: dict[str, int] = field(default_factory=dict)
    label_bin_mix: dict[str, int] = field(default_factory=dict)
    overlap_diagnostics: tuple[str, ...] = ()
    exclusion_reasons: tuple[str, ...] = ()
    promotion_readiness: str = "hold"
    review_signoff_state: str = "pending"
    status: str = "review_pending"
    launchability_reason: str = ""
    blockers: tuple[str, ...] = ()
    required_reviewers: tuple[str, ...] = ()
    required_matrix_tests: tuple[str, ...] = ()
    caps_met: dict[str, bool] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subset_id", _clean_text(self.subset_id))
        object.__setattr__(self, "label", _clean_text(self.label))
        object.__setattr__(self, "promoted_dataset_ref", _clean_text(self.promoted_dataset_ref))
        object.__setattr__(self, "source_rows", _as_mapping(self.source_rows))
        object.__setattr__(self, "balancing_policy", _clean_text(self.balancing_policy) or "balance_first")
        object.__setattr__(self, "source_family_mix", _as_mapping(self.source_family_mix))
        object.__setattr__(self, "assay_family_mix", _as_mapping(self.assay_family_mix))
        object.__setattr__(self, "label_bin_mix", _as_mapping(self.label_bin_mix))
        object.__setattr__(self, "overlap_diagnostics", _dedupe_text(self.overlap_diagnostics))
        object.__setattr__(self, "exclusion_reasons", _dedupe_text(self.exclusion_reasons))
        object.__setattr__(self, "promotion_readiness", _clean_text(self.promotion_readiness) or "hold")
        object.__setattr__(self, "review_signoff_state", _clean_text(self.review_signoff_state) or "pending")
        object.__setattr__(self, "status", _clean_text(self.status) or "review_pending")
        object.__setattr__(self, "launchability_reason", _clean_text(self.launchability_reason))
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        object.__setattr__(self, "required_reviewers", _dedupe_text(self.required_reviewers))
        object.__setattr__(self, "required_matrix_tests", _dedupe_text(self.required_matrix_tests))
        object.__setattr__(self, "caps_met", _as_mapping(self.caps_met))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.subset_id or not self.promoted_dataset_ref:
            raise ValueError("governed subset manifest v2 records require subset_id and promoted_dataset_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subset_id": self.subset_id,
            "label": self.label,
            "promoted_dataset_ref": self.promoted_dataset_ref,
            "row_count": self.row_count,
            "source_rows": dict(self.source_rows),
            "balancing_policy": self.balancing_policy,
            "source_family_mix": dict(self.source_family_mix),
            "assay_family_mix": dict(self.assay_family_mix),
            "label_bin_mix": dict(self.label_bin_mix),
            "overlap_diagnostics": list(self.overlap_diagnostics),
            "exclusion_reasons": list(self.exclusion_reasons),
            "promotion_readiness": self.promotion_readiness,
            "review_signoff_state": self.review_signoff_state,
            "status": self.status,
            "launchability_reason": self.launchability_reason,
            "blockers": list(self.blockers),
            "required_reviewers": list(self.required_reviewers),
            "required_matrix_tests": list(self.required_matrix_tests),
            "caps_met": dict(self.caps_met),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class PoolPromotionReportV2:
    pool_id: str
    status: str
    promotion_bar: str = ""
    last_review_wave: str | None = None
    review_signoff_state: str = "pending"
    promotion_readiness: str = "hold"
    launchability_reason: str = ""
    blockers: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    promoted_dataset_refs: tuple[str, ...] = ()
    required_reviewers: tuple[str, ...] = ()
    required_matrix_tests: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pool_id", _clean_text(self.pool_id))
        object.__setattr__(self, "status", _clean_text(self.status))
        object.__setattr__(self, "promotion_bar", _clean_text(self.promotion_bar))
        object.__setattr__(self, "last_review_wave", _clean_text(self.last_review_wave) or None)
        object.__setattr__(self, "review_signoff_state", _clean_text(self.review_signoff_state) or "pending")
        object.__setattr__(self, "promotion_readiness", _clean_text(self.promotion_readiness) or "hold")
        object.__setattr__(self, "launchability_reason", _clean_text(self.launchability_reason))
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        object.__setattr__(self, "remediation", _dedupe_text(self.remediation))
        object.__setattr__(self, "promoted_dataset_refs", _dedupe_text(self.promoted_dataset_refs))
        object.__setattr__(self, "required_reviewers", _dedupe_text(self.required_reviewers))
        object.__setattr__(self, "required_matrix_tests", _dedupe_text(self.required_matrix_tests))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.pool_id or not self.status:
            raise ValueError("pool promotion report v2 records require pool_id and status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CandidateDatabaseSummaryV3:
    summary_id: str
    total_governed_rows: int
    governing_ready_rows: int
    promoted_subset_count: int = 0
    gated_subset_count: int = 0
    source_family_mix: dict[str, int] = field(default_factory=dict)
    assay_family_mix: dict[str, int] = field(default_factory=dict)
    label_bin_mix: dict[str, int] = field(default_factory=dict)
    governance_state_mix: dict[str, int] = field(default_factory=dict)
    readiness_blockers: tuple[str, ...] = ()
    redundancy_hotspots: tuple[str, ...] = ()
    bias_hotspots: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_id", _clean_text(self.summary_id))
        object.__setattr__(self, "source_family_mix", _as_mapping(self.source_family_mix))
        object.__setattr__(self, "assay_family_mix", _as_mapping(self.assay_family_mix))
        object.__setattr__(self, "label_bin_mix", _as_mapping(self.label_bin_mix))
        object.__setattr__(self, "governance_state_mix", _as_mapping(self.governance_state_mix))
        object.__setattr__(self, "readiness_blockers", _dedupe_text(self.readiness_blockers))
        object.__setattr__(self, "redundancy_hotspots", _dedupe_text(self.redundancy_hotspots))
        object.__setattr__(self, "bias_hotspots", _dedupe_text(self.bias_hotspots))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.summary_id:
            raise ValueError("candidate database summary v3 requires summary_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "total_governed_rows": self.total_governed_rows,
            "governing_ready_rows": self.governing_ready_rows,
            "promoted_subset_count": self.promoted_subset_count,
            "gated_subset_count": self.gated_subset_count,
            "source_family_mix": dict(self.source_family_mix),
            "assay_family_mix": dict(self.assay_family_mix),
            "label_bin_mix": dict(self.label_bin_mix),
            "governance_state_mix": dict(self.governance_state_mix),
            "readiness_blockers": list(self.readiness_blockers),
            "redundancy_hotspots": list(self.redundancy_hotspots),
            "bias_hotspots": list(self.bias_hotspots),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class GovernedSubsetManifest:
    subset_id: str
    label: str
    promoted_dataset_ref: str
    row_count: int
    source_rows: dict[str, int] = field(default_factory=dict)
    balancing_policy: str = "balance_first"
    source_family_mix: dict[str, int] = field(default_factory=dict)
    assay_family_mix: dict[str, int] = field(default_factory=dict)
    label_bin_mix: dict[str, int] = field(default_factory=dict)
    exclusion_reasons: tuple[str, ...] = ()
    launchability_reason: str = ""
    promotion_readiness: str = "hold"
    review_signoff_state: str = "pending"
    blockers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subset_id", _clean_text(self.subset_id))
        object.__setattr__(self, "label", _clean_text(self.label))
        object.__setattr__(self, "promoted_dataset_ref", _clean_text(self.promoted_dataset_ref))
        object.__setattr__(self, "source_rows", _as_mapping(self.source_rows))
        object.__setattr__(self, "balancing_policy", _clean_text(self.balancing_policy) or "balance_first")
        object.__setattr__(self, "source_family_mix", _as_mapping(self.source_family_mix))
        object.__setattr__(self, "assay_family_mix", _as_mapping(self.assay_family_mix))
        object.__setattr__(self, "label_bin_mix", _as_mapping(self.label_bin_mix))
        object.__setattr__(self, "exclusion_reasons", _dedupe_text(self.exclusion_reasons))
        object.__setattr__(self, "launchability_reason", _clean_text(self.launchability_reason))
        object.__setattr__(self, "promotion_readiness", _clean_text(self.promotion_readiness) or "hold")
        object.__setattr__(self, "review_signoff_state", _clean_text(self.review_signoff_state) or "pending")
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.subset_id or not self.promoted_dataset_ref:
            raise ValueError("governed subset manifests require subset_id and promoted_dataset_ref")

    def to_dict(self) -> dict[str, Any]:
        return {
            "subset_id": self.subset_id,
            "label": self.label,
            "promoted_dataset_ref": self.promoted_dataset_ref,
            "row_count": self.row_count,
            "source_rows": dict(self.source_rows),
            "balancing_policy": self.balancing_policy,
            "source_family_mix": dict(self.source_family_mix),
            "assay_family_mix": dict(self.assay_family_mix),
            "label_bin_mix": dict(self.label_bin_mix),
            "exclusion_reasons": list(self.exclusion_reasons),
            "launchability_reason": self.launchability_reason,
            "promotion_readiness": self.promotion_readiness,
            "review_signoff_state": self.review_signoff_state,
            "blockers": list(self.blockers),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class PoolPromotionReport:
    pool_id: str
    status: str
    promotion_bar: str = ""
    last_review_wave: str | None = None
    review_signoff_state: str = "pending"
    promotion_readiness: str = "hold"
    launchability_reason: str = ""
    blockers: tuple[str, ...] = ()
    remediation: tuple[str, ...] = ()
    promoted_dataset_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pool_id", _clean_text(self.pool_id))
        object.__setattr__(self, "status", _clean_text(self.status))
        object.__setattr__(self, "promotion_bar", _clean_text(self.promotion_bar))
        object.__setattr__(self, "last_review_wave", _clean_text(self.last_review_wave) or None)
        object.__setattr__(self, "review_signoff_state", _clean_text(self.review_signoff_state) or "pending")
        object.__setattr__(self, "promotion_readiness", _clean_text(self.promotion_readiness) or "hold")
        object.__setattr__(self, "launchability_reason", _clean_text(self.launchability_reason))
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        object.__setattr__(self, "remediation", _dedupe_text(self.remediation))
        object.__setattr__(self, "promoted_dataset_refs", _dedupe_text(self.promoted_dataset_refs))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.pool_id or not self.status:
            raise ValueError("pool promotion reports require pool_id and status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BetaFeatureGate:
    feature_id: str
    category: str
    current_state: str
    activation_bar: str
    resolved_backend_fidelity: str
    blockers: tuple[str, ...] = ()
    tests_required: tuple[str, ...] = ()
    reviewers_required: tuple[str, ...] = ()
    last_matrix_pass: str | None = None
    last_review_wave: str | None = None
    promotion_readiness: str = "hold"
    launchability_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_id", _clean_text(self.feature_id))
        object.__setattr__(self, "category", _clean_text(self.category))
        object.__setattr__(self, "current_state", _clean_text(self.current_state))
        object.__setattr__(self, "activation_bar", _clean_text(self.activation_bar))
        object.__setattr__(
            self,
            "resolved_backend_fidelity",
            _clean_text(self.resolved_backend_fidelity),
        )
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        object.__setattr__(self, "tests_required", _dedupe_text(self.tests_required))
        object.__setattr__(self, "reviewers_required", _dedupe_text(self.reviewers_required))
        object.__setattr__(self, "last_matrix_pass", _clean_text(self.last_matrix_pass) or None)
        object.__setattr__(self, "last_review_wave", _clean_text(self.last_review_wave) or None)
        object.__setattr__(self, "promotion_readiness", _clean_text(self.promotion_readiness) or "hold")
        object.__setattr__(self, "launchability_reason", _clean_text(self.launchability_reason))
        if not self.feature_id or not self.category or not self.current_state:
            raise ValueError("beta feature gates require feature_id, category, and current_state")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ActivationReadinessReport:
    feature_id: str
    implementation_classification: str
    implementation_completeness: str
    remaining_risks: tuple[str, ...] = ()
    promotion_decision: str = "hold"
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "feature_id", _clean_text(self.feature_id))
        object.__setattr__(
            self,
            "implementation_classification",
            _clean_text(self.implementation_classification),
        )
        object.__setattr__(
            self,
            "implementation_completeness",
            _clean_text(self.implementation_completeness),
        )
        object.__setattr__(self, "remaining_risks", _dedupe_text(self.remaining_risks))
        object.__setattr__(self, "promotion_decision", _clean_text(self.promotion_decision))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.feature_id or not self.implementation_classification:
            raise ValueError(
                "activation readiness reports require "
                "feature_id and implementation_classification"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelActivationMatrix:
    matrix_id: str
    entries: tuple[dict[str, Any], ...]
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "matrix_id", _clean_text(self.matrix_id))
        object.__setattr__(self, "entries", tuple(dict(item) for item in self.entries))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.matrix_id:
            raise ValueError("model activation matrices require matrix_id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "matrix_id": self.matrix_id,
            "entries": [dict(item) for item in self.entries],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class RecommendationReport:
    report_id: str
    status: str
    items: tuple[RecommendationItem, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "report_id", _clean_text(self.report_id))
        object.__setattr__(self, "status", _clean_text(self.status))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.report_id or not self.status:
            raise ValueError("recommendation reports require report_id and status")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "status": self.status,
            "items": [item.to_dict() for item in self.items],
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class StudioExecutionGraph:
    graph_id: str
    stages: tuple[str, ...]
    dependencies: dict[str, tuple[str, ...]]
    capability_checks: dict[str, tuple[str, ...]] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "graph_id", _clean_text(self.graph_id))
        object.__setattr__(self, "stages", _dedupe_text(self.stages))
        dep_map = {
            _clean_text(key): _dedupe_text(value)
            for key, value in (self.dependencies or {}).items()
            if _clean_text(key)
        }
        cap_map = {
            _clean_text(key): _dedupe_text(value)
            for key, value in (self.capability_checks or {}).items()
            if _clean_text(key)
        }
        object.__setattr__(self, "dependencies", dep_map)
        object.__setattr__(self, "capability_checks", cap_map)
        object.__setattr__(self, "blockers", _dedupe_text(self.blockers))
        if not self.graph_id:
            raise ValueError("graph_id must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "stages": list(self.stages),
            "dependencies": {key: list(value) for key, value in self.dependencies.items()},
            "capability_checks": {
                key: list(value) for key, value in self.capability_checks.items()
            },
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True, slots=True)
class StudioRunManifest:
    run_id: str
    pipeline_id: str
    graph_id: str
    status: str
    active_stage: str | None = None
    artifact_refs: tuple[str, ...] = ()
    blocker_refs: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _clean_text(self.run_id))
        object.__setattr__(self, "pipeline_id", _clean_text(self.pipeline_id))
        object.__setattr__(self, "graph_id", _clean_text(self.graph_id))
        object.__setattr__(self, "status", _clean_text(self.status))
        object.__setattr__(self, "active_stage", _clean_text(self.active_stage) or None)
        object.__setattr__(self, "artifact_refs", _dedupe_text(self.artifact_refs))
        object.__setattr__(self, "blocker_refs", _dedupe_text(self.blocker_refs))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.run_id or not self.pipeline_id or not self.graph_id or not self.status:
            raise ValueError("run manifests require run_id, pipeline_id, graph_id, and status")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ModelStudioPipelineSpec:
    pipeline_id: str
    schema_version: str
    study_title: str
    data_strategy: DataStrategySpec
    feature_recipes: tuple[FeatureRecipeSpec, ...]
    graph_recipes: tuple[GraphRecipeSpec, ...]
    training_set_request: TrainingSetRequestSpec
    preprocess_plan: PreprocessPlanSpec
    split_plan: SplitPlanSpec
    example_materialization: ExampleMaterializationSpec
    training_plan: TrainingPlanSpec
    evaluation_plan: EvaluationPlanSpec
    templates: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pipeline_id", _clean_text(self.pipeline_id))
        object.__setattr__(self, "schema_version", _clean_text(self.schema_version))
        object.__setattr__(self, "study_title", _clean_text(self.study_title))
        object.__setattr__(self, "templates", _dedupe_text(self.templates))
        object.__setattr__(self, "tags", _dedupe_text(self.tags))
        object.__setattr__(self, "notes", _dedupe_text(self.notes))
        if not self.pipeline_id or not self.schema_version or not self.study_title:
            raise ValueError("pipeline_id, schema_version, and study_title must not be empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "schema_version": self.schema_version,
            "study_title": self.study_title,
            "data_strategy": self.data_strategy.to_dict(),
            "feature_recipes": [item.to_dict() for item in self.feature_recipes],
            "graph_recipes": [item.to_dict() for item in self.graph_recipes],
            "training_set_request": self.training_set_request.to_dict(),
            "preprocess_plan": self.preprocess_plan.to_dict(),
            "split_plan": self.split_plan.to_dict(),
            "example_materialization": self.example_materialization.to_dict(),
            "training_plan": self.training_plan.to_dict(),
            "evaluation_plan": self.evaluation_plan.to_dict(),
            "templates": list(self.templates),
            "tags": list(self.tags),
            "notes": list(self.notes),
        }


def _check_catalog_option(
    category: str,
    value: str,
    field_name: str,
    *,
    message_template: str,
) -> list[RecommendationItem]:
    """Return blocker items if ``value`` is not an active option of
    ``category``. Centralizes the previously copy-pasted catalog check
    pattern used by every spec field that maps to a UI option."""
    if is_active_option(category, value):
        return []
    return [
        RecommendationItem(
            level="blocker",
            category="beta_catalog",
            message=message_template.format(value=value, category=category),
            action=option_reason(category, value),
            related_fields=(field_name,),
        )
    ]


# ---------------------------------------------------------------------------
# Pluggable pipeline-spec checks (P2-7)
# ---------------------------------------------------------------------------
#
# The validator below is the entry point for the GUI's
# ``RecommendationReport``. It runs a list of ``_check_*`` functions
# against the spec; each returns a list of :class:`RecommendationItem`.
# Each check is wrapped in a ``try/except`` so a bug in one rule can't
# take down the rest of the report -- a diagnostic
# ``validator_internal_error`` blocker is emitted in that case.
#
# To add a new rule:
#
#   1. Write ``_check_my_new_rule(spec, ctx) -> list[RecommendationItem]``.
#   2. Add the function to the ``_PIPELINE_CHECKS`` tuple below.
#
# Use the shared :class:`_ValidationContext` for the two flags every
# check needs (``governed_subset_selected``, ``ligand_pilot_selected``,
# ``requested_dataset_refs``) so callers don't recompute them.


@dataclass(frozen=True, slots=True)
class _ValidationContext:
    """Pre-computed flags shared across the rule-block checks."""

    requested_dataset_refs: frozenset[str]
    governed_subset_selected: bool
    ligand_pilot_selected: bool


def _build_validation_context(spec: ModelStudioPipelineSpec) -> _ValidationContext:
    requested = frozenset(
        {*spec.training_set_request.dataset_refs, *spec.data_strategy.dataset_refs}
    )
    governed = bool(
        {"governed_ppi_blended_subset_v1", "governed_ppi_blended_subset_v2"} & requested
    ) or ("governed_ppi_promoted_subsets" in spec.training_set_request.source_families)
    ligand_pilot = bool({"governed_pl_bridge_pilot_subset_v1"} & requested) or (
        "governed_pl_bridge_pilot" in spec.training_set_request.source_families
    )
    return _ValidationContext(
        requested_dataset_refs=requested,
        governed_subset_selected=governed,
        ligand_pilot_selected=ligand_pilot,
    )


PipelineCheck = "Callable[[ModelStudioPipelineSpec, _ValidationContext], list[RecommendationItem]]"


def _check_capability_options(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Catalog blockers for top-level capability fields."""
    items: list[RecommendationItem] = []
    capability_checks = (
        ("task_types", spec.data_strategy.task_type, "data_strategy.task_type"),
        ("label_types", spec.data_strategy.label_type, "data_strategy.label_type"),
        ("split_strategies", spec.data_strategy.split_strategy, "data_strategy.split_strategy"),
        (
            "structure_source_policies",
            spec.data_strategy.structure_source_policy,
            "data_strategy.structure_source_policy",
        ),
        (
            "structure_source_policies",
            spec.preprocess_plan.source_policy,
            "preprocess_plan.source_policy",
        ),
        ("model_families", spec.training_plan.model_family, "training_plan.model_family"),
    )
    for category, value, field_name in capability_checks:
        items.extend(
            _check_catalog_option(
                category,
                value,
                field_name,
                message_template="`{value}` is not in the active beta catalog for {category}.",
            )
        )
    return items


def _check_ui_options(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Catalog blockers for the secondary UI option fields."""
    items: list[RecommendationItem] = []
    ui_option_checks = (
        ("architectures", spec.training_plan.architecture, "training_plan.architecture"),
        ("optimizer_policies", spec.training_plan.optimizer, "training_plan.optimizer"),
        ("scheduler_policies", spec.training_plan.scheduler, "training_plan.scheduler"),
        ("loss_functions", spec.training_plan.loss_function, "training_plan.loss_function"),
        ("batch_policies", spec.training_plan.batch_policy, "training_plan.batch_policy"),
        (
            "mixed_precision_policies",
            spec.training_plan.mixed_precision,
            "training_plan.mixed_precision",
        ),
        (
            "uncertainty_heads",
            spec.training_plan.uncertainty_head or "none",
            "training_plan.uncertainty_head",
        ),
        (
            "acceptable_fidelity_levels",
            spec.training_set_request.acceptable_fidelity,
            "training_set_request.acceptable_fidelity",
        ),
        (
            "hardware_runtime_presets",
            _clean_text(spec.preprocess_plan.options.get("hardware_runtime_preset")) or "auto_recommend",
            "preprocess_plan.options.hardware_runtime_preset",
        ),
    )
    for category, value, field_name in ui_option_checks:
        items.extend(
            _check_catalog_option(
                category,
                value,
                field_name,
                message_template="`{value}` is not in the active beta {category} catalog.",
            )
        )
    return items


def _check_source_families(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Each requested source family must be in the active catalog."""
    items: list[RecommendationItem] = []
    for family in spec.training_set_request.source_families:
        if not is_active_option("source_families", family):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=f"`{family}` is not in the active beta source-family catalog.",
                    action=option_reason("source_families", family),
                    related_fields=("training_set_request.source_families",),
                )
            )
    return items


def _check_dataset_refs(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Dataset refs are governed-subset-aware: stage2/external-beta candidates get their own review-pending messages, otherwise fall through to the generic beta-catalog blocker."""
    items: list[RecommendationItem] = []
    for dataset_ref in ctx.requested_dataset_refs:
        if not is_active_option("dataset_refs", dataset_ref):
            if dataset_ref == "governed_ppi_stage2_candidate_v1":
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="stage2_governed_subset",
                        message=(
                            "The `governed_ppi_stage2_candidate_v1` dataset is review-pending, not launchable now."
                        ),
                        action=(
                            "Keep this subset in diagnostics/review mode until the 1,500-row threshold, 45% source cap, and Stage 2 scientific reviews are cleared."
                        ),
                        related_fields=(
                            "training_set_request.dataset_refs",
                            "data_strategy.dataset_refs",
                        ),
                    )
                )
                continue
            if (
                dataset_ref == "governed_ppi_external_beta_candidate_v1"
                and not is_active_option("dataset_refs", dataset_ref)
            ):
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="external_beta_governed_subset",
                        message=(
                            "The `governed_ppi_external_beta_candidate_v1` dataset is review-pending and not launchable now."
                        ),
                        action=(
                            "Keep this subset in external-beta rehearsal mode until canonical governed-row launchability and invited-beta signoff are both cleared."
                        ),
                        related_fields=(
                            "training_set_request.dataset_refs",
                            "data_strategy.dataset_refs",
                        ),
                    )
                )
                continue
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=f"`{dataset_ref}` is not in the active beta dataset catalog.",
                    action=option_reason("dataset_refs", dataset_ref),
                    related_fields=(
                        "training_set_request.dataset_refs",
                        "data_strategy.dataset_refs",
                    ),
                )
            )
    return items


def _check_governed_subset_scope(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Governed blended PPI subset only supports staged rows via whole_complex_graph + whole_molecule + symmetric."""
    items: list[RecommendationItem] = []
    if not ctx.governed_subset_selected:
        return items
    for recipe in spec.graph_recipes:
        if recipe.graph_kind != "whole_complex_graph":
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="governed_subset_scope",
                    message=(
                        "The governed blended PPI subset currently supports staged rows only through "
                        "`whole_complex_graph`."
                    ),
                    action=(
                        "Switch graph kind to `whole_complex_graph` until native partner-role "
                        "resolution is available for staged rows."
                    ),
                    related_fields=("graph_recipes",),
                )
            )
        if recipe.region_policy != "whole_molecule":
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="governed_subset_scope",
                    message=(
                        "The governed blended PPI subset currently supports staged rows only through "
                        "the `whole_molecule` region policy."
                    ),
                    action=(
                        "Switch region policy to `whole_molecule` while staged rows remain "
                        "whole-complex only."
                    ),
                    related_fields=("graph_recipes",),
                )
            )
        if recipe.partner_awareness != "symmetric":
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="governed_subset_scope",
                    message=(
                        "The governed blended PPI subset currently supports staged rows only with "
                        "symmetric partner awareness."
                    ),
                    action=(
                        "Switch partner awareness to `symmetric` until native partner-role "
                        "resolution is implemented for staged rows."
                    ),
                    related_fields=("graph_recipes",),
                )
            )
    return items


def _check_ligand_pilot_scope(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Ligand pilot only supports protein-ligand task + experimental_only sourcing."""
    items: list[RecommendationItem] = []
    if not ctx.ligand_pilot_selected:
        return items
    if spec.data_strategy.task_type != "protein-ligand":
        items.append(
            RecommendationItem(
                level="blocker",
                category="ligand_pilot_scope",
                message="The governed ligand pilot only supports the `protein-ligand` task type.",
                action="Switch the draft to the protein-ligand lane for the ligand pilot subset.",
                related_fields=("data_strategy.task_type", "training_set_request.task_type"),
            )
        )
    if spec.data_strategy.structure_source_policy != "experimental_only":
        items.append(
            RecommendationItem(
                level="blocker",
                category="ligand_pilot_scope",
                message="The governed ligand pilot is structure-backed only and requires `experimental_only` structure sourcing.",
                action="Switch structure source policy to `experimental_only` for the ligand pilot.",
                related_fields=(
                    "data_strategy.structure_source_policy",
                    "training_set_request.structure_source_policy",
                ),
            )
        )
    return items


def _check_request_vs_strategy_consistency(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Training-set request task/label/structure-policy must agree with data strategy."""
    items: list[RecommendationItem] = []
    if spec.training_set_request.task_type != spec.data_strategy.task_type:
        items.append(
            RecommendationItem(
                level="blocker",
                category="training_set_request",
                message="Training-set request task type must match the data strategy task type.",
                action="Keep the request and data strategy on the same protein-binding lane.",
                related_fields=("training_set_request.task_type", "data_strategy.task_type"),
            )
        )
    if spec.training_set_request.label_type != spec.data_strategy.label_type:
        items.append(
            RecommendationItem(
                level="blocker",
                category="training_set_request",
                message="Training-set request label type must match the data strategy label type.",
                action="Use one label definition from request through evaluation.",
                related_fields=("training_set_request.label_type", "data_strategy.label_type"),
            )
        )
    if (
        spec.training_set_request.structure_source_policy
        != spec.data_strategy.structure_source_policy
    ):
        items.append(
            RecommendationItem(
                level="warning",
                category="training_set_request",
                message=(
                    "Training-set request and data strategy use different structure "
                    "source policies."
                ),
                action=(
                    "Prefer one shared structure policy for dataset build and "
                    "runtime execution."
                ),
                related_fields=(
                    "training_set_request.structure_source_policy",
                    "data_strategy.structure_source_policy",
                ),
            )
        )
    return items


def _check_target_size_floor(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Warn if target_size is too small to be useful as a pilot."""
    items: list[RecommendationItem] = []
    if spec.training_set_request.target_size < 24:
        items.append(
            RecommendationItem(
                level="warning",
                category="training_set_request",
                message=(
                    "Very small target training sets are usually weak for "
                    "meaningful pilot evaluation."
                ),
                action=(
                    "Prefer a target size of at least 24 structure-backed "
                    "examples for pilot runs."
                ),
                related_fields=("training_set_request.target_size",),
            )
        )
    return items


def _check_publication_candidate_fidelity(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Info note for publication-candidate fidelity."""
    items: list[RecommendationItem] = []
    if spec.training_set_request.acceptable_fidelity == "publication_candidate":
        items.append(
            RecommendationItem(
                level="info",
                category="fidelity",
                message=(
                    "Publication-candidate fidelity keeps stricter dropped-row disclosure and quality summaries than the pilot-ready lane."
                ),
                action=(
                    "Review dropped-row breakdowns, quality verdicts, and compare/export provenance before treating results as publication-ready."
                ),
                related_fields=("training_set_request.acceptable_fidelity",),
            )
        )
    return items


def _check_ic50_label(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Warn that IC50 is a proxy label even when launchable."""
    items: list[RecommendationItem] = []
    if spec.data_strategy.label_type == "IC50":
        items.append(
            RecommendationItem(
                level="warning",
                category="label_provenance",
                message=(
                    "IC50 remains a proxy assay label, not direct thermodynamic truth, even when it is launchable."
                ),
                action=(
                    "Keep assay-family disclosure and conversion provenance visible in compare/export summaries."
                ),
                related_fields=(
                    "data_strategy.label_type",
                    "training_set_request.label_type",
                ),
            )
        )
    return items


def _check_graph_recipes(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Every graph recipe's catalog choices + ligand-pilot scope constraints."""
    items: list[RecommendationItem] = []
    for recipe in spec.graph_recipes:
        if not is_active_option("graph_kinds", recipe.graph_kind):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=f"`{recipe.graph_kind}` is not in the active beta graph catalog.",
                    action=option_reason("graph_kinds", recipe.graph_kind),
                    related_fields=("graph_recipes",),
                )
            )
        if not is_active_option("region_policies", recipe.region_policy):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=f"`{recipe.region_policy}` is not in the active beta region catalog.",
                    action=option_reason("region_policies", recipe.region_policy),
                    related_fields=("graph_recipes",),
                )
            )
        if not is_active_option("node_feature_policies", recipe.encoding_policy):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=(
                        f"`{recipe.encoding_policy}` is not an active beta "
                        "graph encoding policy."
                    ),
                    action=option_reason("node_feature_policies", recipe.encoding_policy),
                    related_fields=("graph_recipes",),
                )
            )
        if not is_active_option("node_granularities", recipe.node_granularity):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=(
                        f"`{recipe.node_granularity}` is not in the active beta "
                        "node-granularity catalog."
                    ),
                    action=option_reason("node_granularities", recipe.node_granularity),
                    related_fields=("graph_recipes",),
                )
            )
        if recipe.graph_kind == "atom_graph" and recipe.node_granularity != "atom":
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="representation_mismatch",
                    message="Atom graphs require `atom` node granularity.",
                    action="Switch node granularity to `atom` or pick a residue-level graph kind.",
                    related_fields=("graph_recipes",),
                )
            )
        if recipe.graph_kind != "atom_graph" and recipe.node_granularity == "atom":
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="representation_mismatch",
                    message="Atom node granularity is only active with the `atom_graph` beta lane.",
                    action="Use `atom_graph` or switch node granularity back to `residue`.",
                    related_fields=("graph_recipes",),
                )
            )
        if not is_active_option("partner_awareness_modes", recipe.partner_awareness):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=(
                        f"`{recipe.partner_awareness}` is not in the active beta "
                        "partner-awareness catalog."
                    ),
                    action=option_reason("partner_awareness_modes", recipe.partner_awareness),
                    related_fields=("graph_recipes",),
                )
            )
        if ctx.ligand_pilot_selected:
            if recipe.graph_kind != "whole_complex_graph":
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="ligand_pilot_scope",
                        message="The launchable ligand pilot currently requires `whole_complex_graph`.",
                        action="Switch graph kind to `whole_complex_graph` for the ligand pilot.",
                        related_fields=("graph_recipes",),
                    )
                )
            if recipe.region_policy != "whole_molecule":
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="ligand_pilot_scope",
                        message="The launchable ligand pilot currently requires the `whole_molecule` region policy.",
                        action="Switch region policy to `whole_molecule` for the ligand pilot.",
                        related_fields=("graph_recipes",),
                    )
                )
            if recipe.partner_awareness != "role_conditioned":
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="ligand_pilot_scope",
                        message="The launchable ligand pilot currently requires `role_conditioned` partner awareness.",
                        action="Switch partner awareness to `role_conditioned` for the ligand pilot.",
                        related_fields=("graph_recipes",),
                    )
                )
    return items


def _check_feature_recipes(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Every feature recipe's catalog choices + sequence-embedding preprocess dependency."""
    items: list[RecommendationItem] = []
    for recipe in spec.feature_recipes:
        if not is_active_option("node_feature_policies", recipe.node_feature_policy):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=(
                        f"`{recipe.node_feature_policy}` is not an active beta node feature policy."
                    ),
                    action=option_reason(
                        "node_feature_policies",
                        recipe.node_feature_policy,
                    ),
                    related_fields=("feature_recipes",),
                )
            )
        for feature_set in recipe.global_feature_sets:
            if not is_active_option("global_feature_sets", feature_set):
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="beta_catalog",
                        message=(
                            f"`{feature_set}` is not in the active beta global "
                            "feature catalog."
                        ),
                        action=option_reason("global_feature_sets", feature_set),
                        related_fields=("feature_recipes",),
                    )
                )
        for feature_set in recipe.distributed_feature_sets:
            if not is_active_option("distributed_feature_sets", feature_set):
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="beta_catalog",
                        message=(
                            f"`{feature_set}` is not in the active beta distributed "
                            "feature catalog."
                        ),
                        action=option_reason("distributed_feature_sets", feature_set),
                        related_fields=("feature_recipes",),
                    )
                )
        if (
            "sequence_embeddings" in recipe.distributed_feature_sets
            and "sequence embeddings" not in spec.preprocess_plan.modules
        ):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="preprocess_dependency",
                    message=(
                        "Sequence-embedding distributed features require the `sequence embeddings` preprocessing lane."
                    ),
                    action=(
                        "Enable `sequence embeddings` in preprocessing or remove `sequence_embeddings` from the distributed feature set."
                    ),
                    related_fields=("feature_recipes", "preprocess_plan.modules"),
                )
            )
        if not is_active_option("node_feature_policies", recipe.edge_feature_policy):
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=(
                        f"`{recipe.edge_feature_policy}` is not an active beta edge feature policy."
                    ),
                    action=option_reason(
                        "node_feature_policies",
                        recipe.edge_feature_policy,
                    ),
                    related_fields=("feature_recipes",),
                )
            )
        # NOTE: the original validator emitted two near-identical blockers
        # for each inactive global / distributed feature set -- the
        # ``global_feature_sets`` and ``distributed_feature_sets`` loops
        # above already cover this case. The duplicate ``-feature`` (vs
        # ``feature``) wording made the GUI Recommendations card show
        # the same problem twice and reasonably looked like two separate
        # issues. The duplicate emission has been removed; the original
        # category-rooted check remains the single source of truth.
    return items


def _check_preprocess_modules(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Each preprocess module is in the active catalog, with two named review-pending exceptions (PyRosetta, Free-state comparison)."""
    items: list[RecommendationItem] = []
    for module in spec.preprocess_plan.modules:
        if not is_active_option("preprocessing_modules", module):
            if module == "PyRosetta":
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="stage2_scientific_lane",
                        message=(
                            "The `PyRosetta` preprocessing lane is review-pending and cannot run yet on this machine."
                        ),
                        action=(
                            "Keep PyRosetta in blocked Stage 2 review mode until the native runtime is installed and the Rosetta materialization contract clears scientific review."
                        ),
                        related_fields=("preprocess_plan.modules",),
                    )
                )
                continue
            if module == "Free-state comparison":
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="stage2_scientific_lane",
                        message=(
                            "The `Free-state comparison` preprocessing lane is review-pending and does not yet have governed bound/free structure pairs."
                        ),
                        action=(
                            "Keep free-state comparison in blocked Stage 2 review mode until governed bound-state and free-state structure pairs are available and audited."
                        ),
                        related_fields=("preprocess_plan.modules",),
                    )
                )
                continue
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="beta_catalog",
                    message=f"`{module}` is not in the active beta preprocessing catalog.",
                    action=option_reason("preprocessing_modules", module),
                    related_fields=("preprocess_plan.modules",),
                )
            )
    return items


def _check_ligand_pilot_preprocess(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Ligand pilot requires the ligand-descriptors preprocess module."""
    items: list[RecommendationItem] = []
    if ctx.ligand_pilot_selected and "ligand descriptors" not in spec.preprocess_plan.modules:
        items.append(
            RecommendationItem(
                level="blocker",
                category="preprocess_dependency",
                message="The launchable ligand pilot requires the `ligand descriptors` preprocessing module.",
                action="Enable `ligand descriptors` in the preprocessing plan for protein-ligand pilot runs.",
                related_fields=("preprocess_plan.modules",),
            )
        )
    return items


def _check_sequence_embeddings_audit(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Sequence embeddings imply a sequence_leakage audit requirement."""
    items: list[RecommendationItem] = []
    if (
        "sequence embeddings" in spec.preprocess_plan.modules
        and "sequence_leakage" not in spec.data_strategy.audit_requirements
    ):
        items.append(
            RecommendationItem(
                level="warning",
                category="quality_gate",
                message=(
                    "Sequence embedding studies should keep sequence leakage audits explicit in the data strategy."
                ),
                action="Retain `sequence_leakage` in the audit requirements when sequence embeddings are enabled.",
                related_fields=("preprocess_plan.modules", "data_strategy.audit_requirements"),
            )
        )
    return items


def _check_graph_only_controls(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Some training controls (lion, warmup_cosine, adaptive_gradient_accumulation) only apply to graph-native model families."""
    items: list[RecommendationItem] = []
    graph_native_families = {"graphsage", "gin", "gcn", "gat"}
    if spec.training_plan.model_family not in graph_native_families:
        graph_only_controls = {
            "optimizer": {"lion"},
            "scheduler": {"warmup_cosine"},
            "batch_policy": {"adaptive_gradient_accumulation"},
        }
        selected_controls = {
            "optimizer": spec.training_plan.optimizer,
            "scheduler": spec.training_plan.scheduler,
            "batch_policy": spec.training_plan.batch_policy,
        }
        for control_name, values in graph_only_controls.items():
            selected = selected_controls[control_name]
            if selected in values:
                items.append(
                    RecommendationItem(
                        level="blocker",
                        category="training_plan",
                        message=(
                            f"`{selected}` is currently active only for graph-backed "
                            "model families."
                        ),
                        action=(
                            "Use graphsage/gcn/gin/gat for this beta training control, "
                            "or switch back to a release control."
                        ),
                        related_fields=(f"training_plan.{control_name}",),
                    )
                )
    return items


def _check_ppi_requires_graph(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """protein-protein studies must declare at least one graph recipe."""
    items: list[RecommendationItem] = []
    if spec.data_strategy.task_type == "protein-protein" and not spec.graph_recipes:
        items.append(
            RecommendationItem(
                level="blocker",
                category="representation",
                message="Protein-protein studies require at least one graph recipe.",
                action="Add an interface, shell, or whole-complex graph recipe.",
                related_fields=("graph_recipes",),
            )
        )
    return items


def _check_tree_with_graphs_warning(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Tree/boosting models with graph recipes get a 'flattened summaries' warning."""
    items: list[RecommendationItem] = []
    if spec.training_plan.model_family in {"xgboost", "catboost"} and spec.graph_recipes:
        items.append(
            RecommendationItem(
                level="warning",
                category="model_compatibility",
                message=(
                    "Tree/boosting models usually need flattened graph summaries "
                    "rather than raw graph objects."
                ),
                action=(
                    "Add a pooled/global feature recipe or switch to a graph-native "
                    "model family."
                ),
                related_fields=("training_plan.model_family", "graph_recipes"),
            )
        )
    return items


def _check_governed_subset_with_trees_warning(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Governed subset + tree family gets the flattened-summary caveat."""
    items: list[RecommendationItem] = []
    if ctx.governed_subset_selected and spec.training_plan.model_family in {"xgboost", "catboost"}:
        items.append(
            RecommendationItem(
                level="warning",
                category="governed_subset_scope",
                message=(
                    "The governed blended PPI subset is currently compiled and execution-tested through whole-complex graph packaging, "
                    "so tree families only see flattened summaries of the staged rows."
                ),
                action=(
                    "Prefer graphsage/gin/gcn/gat for staged-row-heavy governed-subset studies, or keep "
                    "tree-family results labeled as pooled-summary baselines."
                ),
                related_fields=("training_plan.model_family", "graph_recipes"),
            )
        )
    return items


def _check_random_split_warning(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Random splits are usually too weak for protein-binding benchmarks."""
    items: list[RecommendationItem] = []
    if spec.data_strategy.split_strategy == "random":
        items.append(
            RecommendationItem(
                level="warning",
                category="leakage",
                message="Random splits are usually too weak for protein-binding benchmarks.",
                action=(
                    "Prefer UniRef-grouped, graph-component-grouped, or "
                    "leakage-resistant benchmark splitting."
                ),
                related_fields=("data_strategy.split_strategy",),
            )
        )
    return items


def _check_graph_native_requires_graph(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Graph-native models must declare at least one graph recipe."""
    items: list[RecommendationItem] = []
    if spec.training_plan.model_family in {
        "gcn",
        "graphsage",
        "gat",
        "gin",
        "edge_message_passing",
        "heterograph",
    } and not spec.graph_recipes:
        items.append(
            RecommendationItem(
                level="blocker",
                category="model_compatibility",
                message="Graph-native models require at least one graph recipe.",
                action="Add a residue, interface, shell, atom, or hybrid graph recipe.",
                related_fields=("training_plan.model_family", "graph_recipes"),
            )
        )
    return items


def _check_ligand_pilot_model_family(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Ligand pilot only supports graphsage or multimodal_fusion."""
    items: list[RecommendationItem] = []
    if ctx.ligand_pilot_selected and spec.training_plan.model_family not in {"graphsage", "multimodal_fusion"}:
        items.append(
            RecommendationItem(
                level="blocker",
                category="ligand_pilot_scope",
                message="The launchable ligand pilot currently supports only `graphsage` and `multimodal_fusion`.",
                action="Switch the ligand pilot to `graphsage` or `multimodal_fusion`.",
                related_fields=("training_plan.model_family",),
            )
        )
    return items


def _check_cnn_unet_needs_whole_complex(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """CNN/U-Net families want whole-complex or hybrid representations."""
    items: list[RecommendationItem] = []
    if spec.training_plan.model_family in {"cnn", "unet"} and not any(
        recipe.graph_kind in {"whole_complex_graph", "hybrid_graph"}
        for recipe in spec.graph_recipes
    ):
        items.append(
            RecommendationItem(
                level="warning",
                category="representation_mismatch",
                message=(
                    "CNN/U-Net style models usually need voxelized or whole-complex spatial "
                    "inputs rather than interface-only graph recipes."
                ),
                action=(
                    "Enable a whole-complex or hybrid representation and add a compatible "
                    "spatial preprocessing lane."
                ),
                related_fields=("training_plan.model_family", "graph_recipes"),
            )
        )
    return items


def _check_multimodal_fusion_modalities(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Multimodal fusion needs at least 2 declared input modalities."""
    items: list[RecommendationItem] = []
    if spec.training_plan.model_family == "multimodal_fusion":
        modality_count = 0
        if spec.graph_recipes:
            modality_count += 1
        if spec.feature_recipes and any(
            recipe.global_feature_sets or recipe.distributed_feature_sets
            for recipe in spec.feature_recipes
        ):
            modality_count += 1
        if "sequence embeddings" in spec.preprocess_plan.modules:
            modality_count += 1
        if modality_count < 2:
            items.append(
                RecommendationItem(
                    level="warning",
                    category="fusion_depth",
                    message=(
                        "Multimodal fusion is selected, but the draft only declares one strong "
                        "input modality."
                    ),
                    action=(
                        "Add global/distributed features, sequence embeddings, or another graph "
                        "view so the fusion stack has a real purpose."
                    ),
                    related_fields=(
                        "training_plan.model_family",
                        "feature_recipes",
                        "graph_recipes",
                        "preprocess_plan.modules",
                    ),
                )
            )
    return items


def _check_protein_studies_audit(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """protein-* studies should require sequence_leakage audits."""
    items: list[RecommendationItem] = []
    if "sequence_leakage" not in spec.data_strategy.audit_requirements and (
        spec.data_strategy.task_type.startswith("protein-")
        or spec.data_strategy.task_type == "protein-protein"
    ):
        items.append(
            RecommendationItem(
                level="warning",
                category="quality_gate",
                message=(
                    "Protein-binding studies should explicitly require sequence leakage "
                    "checks."
                ),
                action="Add sequence leakage and state-reuse audits to the data strategy.",
                related_fields=("data_strategy.audit_requirements",),
            )
        )
    return items


def _check_protein_studies_preprocess_deps(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """protein-protein/protein-ligand pipelines require PDB acquisition + chain mapping."""
    items: list[RecommendationItem] = []
    if spec.data_strategy.task_type in {"protein-protein", "protein-ligand"} and (
        "PDB acquisition" not in spec.preprocess_plan.modules
        or "chain extraction and canonical mapping" not in spec.preprocess_plan.modules
    ):
        items.append(
            RecommendationItem(
                level="blocker",
                category="preprocess_dependency",
                message=(
                    "Released protein-binding pipelines require structure acquisition and "
                    "canonical chain mapping."
                ),
                action=(
                    "Enable both `PDB acquisition` and "
                    "`chain extraction and canonical mapping`."
                ),
                related_fields=("preprocess_plan.modules",),
            )
        )
    return items


def _check_ligand_pilot_split_strategy(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Ligand pilot requires protein_ligand_component_grouped split + grouping."""
    items: list[RecommendationItem] = []
    if ctx.ligand_pilot_selected and spec.data_strategy.split_strategy != "protein_ligand_component_grouped":
        items.append(
            RecommendationItem(
                level="blocker",
                category="ligand_pilot_scope",
                message="The launchable ligand pilot currently requires the `protein_ligand_component_grouped` split strategy.",
                action="Switch the data strategy split strategy to `protein_ligand_component_grouped`.",
                related_fields=("data_strategy.split_strategy",),
            )
        )
    if ctx.ligand_pilot_selected and spec.split_plan.grouping_policy != "protein_ligand_component_grouped":
        items.append(
            RecommendationItem(
                level="blocker",
                category="ligand_pilot_scope",
                message="The launchable ligand pilot currently requires the `protein_ligand_component_grouped` grouping policy.",
                action="Set the split plan grouping policy to `protein_ligand_component_grouped`.",
                related_fields=("split_plan.grouping_policy",),
            )
        )
    return items


def _check_empty_sources(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Warn when neither dataset_refs nor source_families are declared."""
    items: list[RecommendationItem] = []
    if not spec.training_set_request.dataset_refs and not spec.training_set_request.source_families:
        items.append(
            RecommendationItem(
                level="warning",
                category="training_set_request",
                message=(
                    "No source families or dataset refs were declared for the "
                    "training-set request."
                ),
                action=(
                    "The runtime will fall back to the released benchmark, but "
                    "explicit study sources are preferred."
                ),
                related_fields=(
                    "training_set_request.dataset_refs",
                    "training_set_request.source_families",
                ),
            )
        )
    return items


def _check_graph_payload_materialization(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Graph-native models require graph payloads to be packaged."""
    items: list[RecommendationItem] = []
    if (
        not spec.example_materialization.include_graph_payloads
        and spec.training_plan.model_family in {"graphsage", "gin", "gcn"}
    ):
        items.append(
            RecommendationItem(
                level="blocker",
                category="materialization",
                message="Graph-native models require graph payload materialization.",
                action="Enable graph payload packaging in the example materialization plan.",
                related_fields=("example_materialization.include_graph_payloads",),
            )
        )
    return items


def _check_global_features_materialization(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Tabular/fusion lanes expect global features to be packaged."""
    items: list[RecommendationItem] = []
    if (
        not spec.example_materialization.include_global_features
        and spec.training_plan.model_family
        in {"xgboost", "catboost", "mlp", "multimodal_fusion"}
    ):
        items.append(
            RecommendationItem(
                level="warning",
                category="materialization",
                message=(
                    "Current released tabular and fusion lanes expect global "
                    "features to be packaged."
                ),
                action="Keep global feature packaging enabled for the internal pilot path.",
                related_fields=("example_materialization.include_global_features",),
            )
        )
    return items


def _check_hybrid_graph_region_policy(spec: ModelStudioPipelineSpec, ctx: _ValidationContext) -> list[RecommendationItem]:
    """Hybrid graphs are strongest with interface_plus_shell."""
    items: list[RecommendationItem] = []
    for recipe in spec.graph_recipes:
        if recipe.graph_kind == "hybrid_graph" and recipe.region_policy != "interface_plus_shell":
            items.append(
                RecommendationItem(
                    level="warning",
                    category="representation_mismatch",
                    message=(
                        "Hybrid graphs are strongest when paired with the released "
                        "`interface_plus_shell` region policy."
                    ),
                    action="Prefer `interface_plus_shell` for hybrid graph release runs.",
                    related_fields=("graph_recipes",),
                )
            )
    return items


# Order matters: a later check may rely on context flags but the
# context is computed once up front. To add a rule, append a function
# here and define it above.
_PIPELINE_CHECKS: tuple = (
    _check_capability_options,
    _check_ui_options,
    _check_source_families,
    _check_dataset_refs,
    _check_governed_subset_scope,
    _check_ligand_pilot_scope,
    _check_request_vs_strategy_consistency,
    _check_target_size_floor,
    _check_publication_candidate_fidelity,
    _check_ic50_label,
    _check_graph_recipes,
    _check_feature_recipes,
    _check_preprocess_modules,
    _check_ligand_pilot_preprocess,
    _check_sequence_embeddings_audit,
    _check_graph_only_controls,
    _check_ppi_requires_graph,
    _check_tree_with_graphs_warning,
    _check_governed_subset_with_trees_warning,
    _check_random_split_warning,
    _check_graph_native_requires_graph,
    _check_ligand_pilot_model_family,
    _check_cnn_unet_needs_whole_complex,
    _check_multimodal_fusion_modalities,
    _check_protein_studies_audit,
    _check_protein_studies_preprocess_deps,
    _check_ligand_pilot_split_strategy,
    _check_empty_sources,
    _check_graph_payload_materialization,
    _check_global_features_materialization,
    _check_hybrid_graph_region_policy,
)


def validate_pipeline_spec(spec: ModelStudioPipelineSpec) -> RecommendationReport:
    """Run every catalog / shape / consistency check against ``spec``.

    Each rule block lives in a named ``_check_*`` function in this
    module. The list is in :data:`_PIPELINE_CHECKS` -- add a new
    function there to add a new rule. Each check runs inside a
    ``try``/``except``: a bug in one rule emits a diagnostic
    ``validator_internal_error`` blocker but doesn't stop the others.
    """
    ctx = _build_validation_context(spec)
    items: list[RecommendationItem] = []
    for check in _PIPELINE_CHECKS:
        try:
            items.extend(check(spec, ctx))
        except Exception as exc:  # noqa: BLE001 -- isolate bad rule blocks
            items.append(
                RecommendationItem(
                    level="blocker",
                    category="validator_internal_error",
                    message=(
                        f"Validator check {check.__name__} raised "
                        f"{type(exc).__name__}: {exc!s}"
                    ),
                    action=(
                        "Treat the report as incomplete. See the server log "
                        "for the full stack trace."
                    ),
                    related_fields=(),
                )
            )

    status = "ok" if not any(item.level == "blocker" for item in items) else "blocked"
    if not items:
        items.append(
            RecommendationItem(
                level="info",
                category="readiness",
                message="Draft is structurally valid and ready for execution-graph compilation.",
                related_fields=("pipeline_id",),
            )
        )
    return RecommendationReport(
        report_id=f"recommendation:{spec.pipeline_id}",
        status=status,
        items=tuple(items),
        notes=("Generated by the Model Studio rules-based validator.",),
    )



def compile_execution_graph(spec: ModelStudioPipelineSpec) -> StudioExecutionGraph:
    stages = (
        "training_set_request_resolution",
        "dataset_candidate_preview",
        "dataset_build",
        "split_compilation",
        "structure_resolution",
        "feature_materialization",
        "graph_materialization",
        "example_packaging",
        "model_training",
        "evaluation",
        "reporting",
    )
    dependencies = {
        "dataset_candidate_preview": ("training_set_request_resolution",),
        "dataset_build": ("dataset_candidate_preview",),
        "split_compilation": ("dataset_build",),
        "structure_resolution": ("split_compilation",),
        "feature_materialization": ("structure_resolution",),
        "graph_materialization": ("feature_materialization",),
        "example_packaging": ("graph_materialization",),
        "model_training": ("example_packaging",),
        "evaluation": ("model_training",),
        "reporting": ("evaluation",),
    }
    capability_checks = {
        "training_set_request_resolution": (
            spec.training_set_request.task_type,
            spec.training_set_request.label_type,
        ),
        "dataset_build": (
            *spec.training_set_request.dataset_refs,
            *spec.training_set_request.source_families,
        ),
        "structure_resolution": (
            "structure_policy_supported",
            "source_caps_loaded",
        ),
        "feature_materialization": tuple(spec.preprocess_plan.modules),
        "graph_materialization": tuple(recipe.graph_kind for recipe in spec.graph_recipes),
        "example_packaging": (
            "global_features"
            if spec.example_materialization.include_global_features
            else "no_globals",
            "distributed_features"
            if spec.example_materialization.include_distributed_features
            else "no_distributed",
            "graph_payloads"
            if spec.example_materialization.include_graph_payloads
            else "no_graph_payloads",
        ),
        "model_training": (spec.training_plan.model_family, spec.training_plan.architecture),
    }
    report = validate_pipeline_spec(spec)
    blockers = tuple(item.message for item in report.items if item.level == "blocker")
    return StudioExecutionGraph(
        graph_id=f"graph:{spec.pipeline_id}",
        stages=stages,
        dependencies=dependencies,
        capability_checks=capability_checks,
        blockers=blockers,
    )


def feature_recipe_from_dict(payload: Mapping[str, Any]) -> FeatureRecipeSpec:
    return FeatureRecipeSpec(
        recipe_id=payload.get("recipe_id", ""),
        node_feature_policy=payload.get("node_feature_policy", ""),
        edge_feature_policy=payload.get("edge_feature_policy", ""),
        global_feature_sets=tuple(payload.get("global_feature_sets", ())),
        distributed_feature_sets=tuple(payload.get("distributed_feature_sets", ())),
        notes=tuple(payload.get("notes", ())),
    )


def graph_recipe_from_dict(payload: Mapping[str, Any]) -> GraphRecipeSpec:
    return GraphRecipeSpec(
        recipe_id=payload.get("recipe_id", ""),
        graph_kind=payload.get("graph_kind", ""),
        region_policy=payload.get("region_policy", ""),
        node_granularity=payload.get("node_granularity", ""),
        encoding_policy=payload.get("encoding_policy", ""),
        feature_recipe_id=payload.get("feature_recipe_id", ""),
        partner_awareness=payload.get("partner_awareness", "symmetric"),
        include_waters=_as_bool(payload.get("include_waters", False)),
        include_salt_bridges=_as_bool(payload.get("include_salt_bridges", False)),
        include_contact_shell=_as_bool(payload.get("include_contact_shell", False)),
        notes=tuple(payload.get("notes", ())),
    )


def data_strategy_from_dict(payload: Mapping[str, Any]) -> DataStrategySpec:
    return DataStrategySpec(
        strategy_id=payload.get("strategy_id", ""),
        task_type=payload.get("task_type", ""),
        label_type=payload.get("label_type", ""),
        label_transform=payload.get("label_transform", ""),
        split_strategy=payload.get("split_strategy", ""),
        structure_source_policy=payload.get("structure_source_policy", ""),
        graph_recipe_ids=tuple(payload.get("graph_recipe_ids", ())),
        feature_recipe_ids=tuple(payload.get("feature_recipe_ids", ())),
        dataset_refs=tuple(payload.get("dataset_refs", ())),
        audit_requirements=tuple(payload.get("audit_requirements", ())),
        notes=tuple(payload.get("notes", ())),
    )


def preprocess_plan_from_dict(payload: Mapping[str, Any]) -> PreprocessPlanSpec:
    return PreprocessPlanSpec(
        plan_id=payload.get("plan_id", ""),
        modules=tuple(payload.get("modules", ())),
        cache_policy=payload.get("cache_policy", ""),
        source_policy=payload.get("source_policy", ""),
        shell_distance_angstroms=payload.get("shell_distance_angstroms"),
        shell_strategy=payload.get("shell_strategy"),
        options=_as_mapping(payload.get("options")),
        notes=tuple(payload.get("notes", ())),
    )


def training_set_request_from_dict(payload: Mapping[str, Any]) -> TrainingSetRequestSpec:
    return TrainingSetRequestSpec(
        request_id=payload.get("request_id", ""),
        task_type=payload.get("task_type", ""),
        label_type=payload.get("label_type", ""),
        structure_source_policy=payload.get("structure_source_policy", ""),
        source_families=tuple(payload.get("source_families", ())),
        dataset_refs=tuple(payload.get("dataset_refs", ())),
        target_size=int(payload.get("target_size", 0) or 0),
        acceptable_fidelity=payload.get("acceptable_fidelity", "pilot_ready"),
        inclusion_filters=_as_mapping(payload.get("inclusion_filters")),
        exclusion_filters=_as_mapping(payload.get("exclusion_filters")),
        notes=tuple(payload.get("notes", ())),
    )


def split_plan_from_dict(payload: Mapping[str, Any]) -> SplitPlanSpec:
    return SplitPlanSpec(
        plan_id=payload.get("plan_id", ""),
        objective=payload.get("objective", ""),
        grouping_policy=payload.get("grouping_policy", ""),
        holdout_policy=payload.get("holdout_policy", ""),
        train_fraction=float(payload.get("train_fraction", 0.7)),
        val_fraction=float(payload.get("val_fraction", 0.1)),
        test_fraction=float(payload.get("test_fraction", 0.2)),
        hard_constraints=tuple(payload.get("hard_constraints", ())),
        notes=tuple(payload.get("notes", ())),
    )


def example_materialization_from_dict(payload: Mapping[str, Any]) -> ExampleMaterializationSpec:
    return ExampleMaterializationSpec(
        spec_id=payload.get("spec_id", ""),
        graph_recipe_ids=tuple(payload.get("graph_recipe_ids", ())),
        feature_recipe_ids=tuple(payload.get("feature_recipe_ids", ())),
        preprocess_modules=tuple(payload.get("preprocess_modules", ())),
        region_policy=payload.get("region_policy", ""),
        cache_policy=payload.get("cache_policy", ""),
        include_global_features=_as_bool(payload.get("include_global_features", True)),
        include_distributed_features=_as_bool(
            payload.get("include_distributed_features", True)
        ),
        include_graph_payloads=_as_bool(payload.get("include_graph_payloads", True)),
        notes=tuple(payload.get("notes", ())),
    )


def training_plan_from_dict(payload: Mapping[str, Any]) -> TrainingPlanSpec:
    return TrainingPlanSpec(
        plan_id=payload.get("plan_id", ""),
        model_family=payload.get("model_family", ""),
        architecture=payload.get("architecture", ""),
        optimizer=payload.get("optimizer", ""),
        scheduler=payload.get("scheduler", ""),
        loss_function=payload.get("loss_function", ""),
        epoch_budget=int(payload.get("epoch_budget", 1)),
        batch_policy=payload.get("batch_policy", ""),
        mixed_precision=payload.get("mixed_precision", ""),
        uncertainty_head=payload.get("uncertainty_head"),
        hyperparameters=_as_mapping(payload.get("hyperparameters")),
        ablations=tuple(payload.get("ablations", ())),
        notes=tuple(payload.get("notes", ())),
    )


def evaluation_plan_from_dict(payload: Mapping[str, Any]) -> EvaluationPlanSpec:
    return EvaluationPlanSpec(
        plan_id=payload.get("plan_id", ""),
        metric_families=tuple(payload.get("metric_families", ())),
        robustness_slices=tuple(payload.get("robustness_slices", ())),
        outlier_policy=payload.get("outlier_policy", ""),
        attribution_policy=payload.get("attribution_policy", ""),
        leakage_checks=tuple(payload.get("leakage_checks", ())),
        notes=tuple(payload.get("notes", ())),
    )


def pipeline_spec_from_dict(payload: Mapping[str, Any]) -> ModelStudioPipelineSpec:
    return ModelStudioPipelineSpec(
        pipeline_id=payload.get("pipeline_id", ""),
        schema_version=payload.get("schema_version", ""),
        study_title=payload.get("study_title", ""),
        data_strategy=data_strategy_from_dict(_as_mapping(payload.get("data_strategy"))),
        feature_recipes=tuple(
            feature_recipe_from_dict(item) for item in payload.get("feature_recipes", ())
        ),
        graph_recipes=tuple(
            graph_recipe_from_dict(item) for item in payload.get("graph_recipes", ())
        ),
        training_set_request=training_set_request_from_dict(
            _as_mapping(payload.get("training_set_request"))
        ),
        preprocess_plan=preprocess_plan_from_dict(_as_mapping(payload.get("preprocess_plan"))),
        split_plan=split_plan_from_dict(_as_mapping(payload.get("split_plan"))),
        example_materialization=example_materialization_from_dict(
            _as_mapping(payload.get("example_materialization"))
        ),
        training_plan=training_plan_from_dict(_as_mapping(payload.get("training_plan"))),
        evaluation_plan=evaluation_plan_from_dict(_as_mapping(payload.get("evaluation_plan"))),
        templates=tuple(payload.get("templates", ())),
        tags=tuple(payload.get("tags", ())),
        notes=tuple(payload.get("notes", ())),
    )
