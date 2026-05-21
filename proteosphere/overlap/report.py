"""Report dataclasses and the composite-score formula.

:class:`OverlapReport` is the public result type. It carries the raw
per-axis matches (:class:`CoMembershipResult`), exposes a numeric
composite score, and emits categorical severity tier metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .tiers import SEVERITY_TIERS, TIER_DESCRIPTIONS, tier_for_axis


# ---------------------------------------------------------------------------
# Composite-score scaling. Per-axis score is::
#
#     weight * min(1.0, COMPOSITE_BASE + COMPOSITE_PER_OVERLAP * overlap_count)
#
# Tuned so a single specific match (overlap_count=1) keeps ~68% of its
# weight, and overlap_count >= 5 saturates at 100%. Sharing one rare
# specific family is strong evidence; we don't crush single-match cases as
# hard as the naive ``overlap_count / 5`` damping does.
# ---------------------------------------------------------------------------
COMPOSITE_BASE = 0.6
COMPOSITE_PER_OVERLAP = 0.08

# Score returned when identity overlap is present (same UniProt accession
# in both sets). Identity is the strongest possible signal, so the
# composite is hard-pinned at 1.0.
IDENTITY_SCORE = 1.0


@dataclass
class CoMembershipResult:
    """One axis's contribution to an overlap report.

    Attributes
    ----------
    axis
        Axis name, e.g. ``shared_pfam_family``.
    namespace
        Source data tag, e.g. ``Pfam``, ``Reactome``, ``GO_MF``. Used for
        prevalence-weighting routing.
    weight
        Leakage weight in [0, 1] for this axis, after profile + prevalence
        adjustments have been applied.
    shared_ids
        Sorted list of shared identifiers (e.g. all shared Pfam families).
    test_co_member_count, comparison_co_member_count
        How many distinct identifiers the test/comparison sides have under
        this axis (useful for gauging breadth).
    overlap_count
        How many shared identifiers fired. Drives the smooth count-factor
        in the composite.
    examples
        Truncated preview of ``shared_ids`` for compact display.
    """
    axis: str
    namespace: str
    weight: float
    shared_ids: list[str] = field(default_factory=list)
    test_co_member_count: int = 0
    comparison_co_member_count: int = 0
    overlap_count: int = 0
    examples: list[str] = field(default_factory=list)

    @property
    def overlap_fraction_test_axis(self) -> float:
        """Fraction of the test side's identifiers that are also in comparison."""
        n = max(self.test_co_member_count, 1)
        return self.overlap_count / n


@dataclass
class OverlapReport:
    """A multi-axis overlap report between two accession sets.

    The numeric :meth:`composite_score` answers *how strong* the leakage
    is; the categorical :meth:`severity_tier` answers *what kind* of
    relationship it is. Both are useful for benchmark curators.
    """
    test_accessions: list[str]
    comparison_accessions: list[str]
    axes: dict[str, CoMembershipResult] = field(default_factory=dict)
    task_profile_name: str = "all"
    # Set when test_accessions and comparison_accessions overlap directly.
    identity_accessions: list[str] = field(default_factory=list)

    # ----- score -------------------------------------------------------

    def composite_score(self) -> float:
        """Weighted-max leakage signal across all positive axes.

        Per-axis score is ``weight * min(1.0, 0.6 + 0.08 * overlap_count)``
        — single-match strong axes aren't crushed, multi-match axes
        saturate at full weight by 5 overlaps.

        The composite is the max across axes — leakage is risky if ANY
        axis fires confidently, not only when many fire.

        When identity overlap is present (same UniProt accession in both
        sets) the score is hard-pinned to ``IDENTITY_SCORE`` (1.0) — there
        is no stronger signal than "this is the same protein."
        """
        if self.identity_accessions:
            return IDENTITY_SCORE
        if not self.axes:
            return 0.0
        return max(
            a.weight * min(1.0, COMPOSITE_BASE + COMPOSITE_PER_OVERLAP * a.overlap_count)
            for a in self.axes.values()
        )

    # ----- severity tier -----------------------------------------------

    def severity_tier(self) -> str:
        """Highest-severity tier hit by this report (or ``'none'``)."""
        if self.identity_accessions:
            return "identity"
        hit_tiers = {tier_for_axis(a) for a in self.axes}
        for tier in SEVERITY_TIERS:
            if tier in hit_tiers:
                return tier
        return "none"

    def severity_description(self) -> str:
        """Human-readable explanation of the severity tier."""
        return TIER_DESCRIPTIONS.get(
            self.severity_tier(),
            self.severity_tier(),
        )

    def tier_breakdown(self) -> dict[str, list[str]]:
        """Group fired axes by severity tier, in canonical severity order.

        Useful for the CLI display and for downstream curators who want
        to know *every* tier that contributed, not just the top one.
        """
        out: dict[str, list[str]] = {}
        if self.identity_accessions:
            out["identity"] = list(self.identity_accessions)
        per_tier: dict[str, list[str]] = {}
        for axis_name in self.axes:
            tier = tier_for_axis(axis_name)
            per_tier.setdefault(tier, []).append(axis_name)
        for tier in SEVERITY_TIERS:
            if tier in per_tier:
                out[tier] = sorted(per_tier[tier])
        if "unknown" in per_tier:
            out["unknown"] = sorted(per_tier["unknown"])
        return out

    # ----- serialization -----------------------------------------------

    def to_dict(self) -> dict:
        """JSON-friendly representation, suitable for ``json.dumps``."""
        return {
            "test_accessions": self.test_accessions,
            "comparison_accessions": self.comparison_accessions,
            "task_profile": self.task_profile_name,
            "composite_score": self.composite_score(),
            "severity_tier": self.severity_tier(),
            "severity_description": self.severity_description(),
            "tier_breakdown": self.tier_breakdown(),
            "identity_accessions": self.identity_accessions,
            "axes": {
                k: {
                    "axis": v.axis,
                    "namespace": v.namespace,
                    "weight": v.weight,
                    "tier": tier_for_axis(k),
                    "shared_ids": v.shared_ids[:20],
                    "test_co_member_count": v.test_co_member_count,
                    "comparison_co_member_count": v.comparison_co_member_count,
                    "overlap_count": v.overlap_count,
                    "examples": v.examples,
                }
                for k, v in self.axes.items()
            },
        }
