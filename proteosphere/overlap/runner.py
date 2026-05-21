"""Top-level driver: discover axes, apply task profiles, produce reports.

The two-stage split (:func:`discover_pair_axes` then
:func:`apply_task_profile`) lets you score one pair under multiple
profiles without rerunning the expensive parquet scans. The convenience
:func:`discover_overlap` does both in one call.

Progress callbacks
------------------

For long-running scans, callers (e.g. the CLI) can pass two optional
callbacks to surface live progress:

- ``on_axis_start(axis_fn_name: str) -> None`` -- fired BEFORE each axis
  discovery function runs. Useful for "starting <axis>..." indicators.
- ``on_axis_done(axis_fn_name: str, axes: dict) -> None`` -- fired AFTER
  the axis function returns. ``axes`` is the dict it produced so the
  caller can tell whether the axis fired.

Both default to ``None`` (no-op).
"""
from __future__ import annotations

from typing import Callable, Iterable

from proteosphere.config import Config

from .axes import (
    _norm,
    shared_domain_architecture,
    shared_function_class,
    shared_interaction_network,
    shared_motif_domain_family,
    shared_ortholog_membership,
    shared_pathway_membership,
    shared_structural_classification,
)
from .prevalence import (
    PREVALENCE_LOOKUP_LIMIT,
    PREVALENCE_NAMESPACES,
    family_prevalence,
    prevalence_factor,
)
from .profiles import TASK_PROFILES, TaskProfile, get_task_profile
from .report import CoMembershipResult, OverlapReport


# Discovery pipeline order. Each function adds its axes to the report.
_AXIS_DISCOVERY_FUNCTIONS = (
    shared_ortholog_membership,
    shared_pathway_membership,
    shared_interaction_network,
    shared_motif_domain_family,
    shared_structural_classification,
    shared_function_class,
    shared_domain_architecture,
)


def discover_pair_axes(
    config: Config,
    test: Iterable[str],
    comparison: Iterable[str],
    *,
    on_axis_start: Callable[[str], None] | None = None,
    on_axis_done: Callable[[str, dict], None] | None = None,
) -> OverlapReport:
    """Compute every defined axis for a pair without applying any profile.

    Use this when you want to score one pair under multiple task profiles —
    the expensive parquet scans run once, then :func:`apply_task_profile`
    can be called repeatedly with different profiles to get reweighted
    reports.

    Also detects ``identity`` overlap directly: any UniProt accession that
    appears in both sets is recorded on the report, and is treated as the
    highest-severity overlap tier.

    Optional ``on_axis_start`` / ``on_axis_done`` callbacks fire around
    every axis discovery function so callers can surface live progress
    (see module docstring).
    """
    test_norm = _norm(test)
    comp_norm = _norm(comparison)
    identity = sorted(set(test_norm) & set(comp_norm))
    report = OverlapReport(
        test_accessions=test_norm,
        comparison_accessions=comp_norm,
        task_profile_name="all",
        identity_accessions=identity,
    )
    for fn in _AXIS_DISCOVERY_FUNCTIONS:
        fn_name = fn.__name__
        if on_axis_start is not None:
            on_axis_start(fn_name)
        result = fn(config, report.test_accessions, report.comparison_accessions)
        if on_axis_done is not None:
            on_axis_done(fn_name, result)
        report.axes.update(result)
    return report


def apply_task_profile(
    report: OverlapReport,
    profile: TaskProfile | str | None,
    *,
    config: Config | None = None,
    apply_prevalence_weighting: bool = True,
) -> OverlapReport:
    """Filter + reweight an existing OverlapReport against a task profile.

    The input ``report`` is left untouched; a new :class:`OverlapReport` is
    returned with axes filtered to ``profile.in_scope_axes`` and weights
    adjusted by ``profile.weight_overrides`` and (optionally) prevalence.

    Parameters
    ----------
    report
        Output of :func:`discover_pair_axes`.
    profile
        :class:`TaskProfile`, profile name string, or ``None`` (= 'all').
    config
        Required when ``apply_prevalence_weighting`` is True so we know
        which warehouse to query for family sizes.
    apply_prevalence_weighting
        When True, axes whose namespace is in
        :data:`prevalence.PREVALENCE_NAMESPACES` get their weight scaled by
        the smallest (rarest) shared identifier's prevalence factor. Common
        families (>50k members) get crushed; rare ones keep full weight.
    """
    if isinstance(profile, str):
        prof = get_task_profile(profile)
    elif profile is None:
        prof = TASK_PROFILES["all"]
    else:
        prof = profile

    user_opted_axes = set(prof.weight_overrides) | (prof.in_scope_axes or set())

    new_axes: dict[str, CoMembershipResult] = {}
    for name, axis in report.axes.items():
        if prof.in_scope_axes is not None and name not in prof.in_scope_axes:
            continue
        # Start with profile-override weight if present, else original.
        base_weight = prof.weight_overrides.get(name, axis.weight)
        if (apply_prevalence_weighting and config is not None
                and axis.namespace in PREVALENCE_NAMESPACES and axis.shared_ids):
            min_prev = min(
                (family_prevalence(str(config.warehouse_root), axis.namespace, sid)
                 for sid in axis.shared_ids[:PREVALENCE_LOOKUP_LIMIT]),
                default=0,
            )
            factor = prevalence_factor(min_prev)
            # If the user explicitly opted into this axis via the profile,
            # apply the profile's prevalence_floor to keep the signal alive
            # even for very common families.
            if name in user_opted_axes and prof.prevalence_floor > 0.0:
                factor = max(factor, prof.prevalence_floor)
            base_weight = base_weight * factor
        new_axes[name] = CoMembershipResult(
            axis=axis.axis,
            namespace=axis.namespace,
            weight=base_weight,
            shared_ids=axis.shared_ids,
            test_co_member_count=axis.test_co_member_count,
            comparison_co_member_count=axis.comparison_co_member_count,
            overlap_count=axis.overlap_count,
            examples=axis.examples,
        )

    return OverlapReport(
        test_accessions=list(report.test_accessions),
        comparison_accessions=list(report.comparison_accessions),
        axes=new_axes,
        task_profile_name=prof.name,
        identity_accessions=list(report.identity_accessions),
    )


def discover_overlap(
    config: Config,
    test: Iterable[str],
    comparison: Iterable[str],
    *,
    task_profile: TaskProfile | str | None = None,
    apply_prevalence_weighting: bool = True,
    on_axis_start: Callable[[str], None] | None = None,
    on_axis_done: Callable[[str, dict], None] | None = None,
) -> OverlapReport:
    """One-shot: compute axes and apply a task profile in a single call.

    Convenience wrapper around :func:`discover_pair_axes` +
    :func:`apply_task_profile`. When you need to score the same pair under
    many profiles, call them separately and reuse the raw report.

    Progress callbacks are forwarded to :func:`discover_pair_axes`.
    """
    raw = discover_pair_axes(
        config, test, comparison,
        on_axis_start=on_axis_start,
        on_axis_done=on_axis_done,
    )
    return apply_task_profile(
        raw,
        task_profile,
        config=config,
        apply_prevalence_weighting=apply_prevalence_weighting,
    )
