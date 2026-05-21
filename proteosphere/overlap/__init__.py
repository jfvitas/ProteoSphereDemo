"""Multi-resolution biological-similarity overlap detection.

This package audits per-axis overlap between a *test* set of protein
accessions and a *comparison* set, returning a structured leakage signature.
Each axis represents a distinct level of biological similarity:

- identity (same UniProt accession on both sides)
- direct ortholog (OrthoDB / eggNOG / OMA / HOGENOM / InParanoid)
- paralog family (Pfam / InterPro / PANTHER / SMART / etc.)
- domain architecture (full or partial Pfam composition match)
- distant homology (SUPFAM / SCOP / CATH superfamily)
- convergent function (GO MF / EC subclass with no sequence/structure overlap)
- broad fold (SCOP fold / CATH topology / Gene3D)
- pathway / interaction / motif / co-localization (weak signals)

Two outputs come back per pair:
- ``composite_score``: numeric leakage signal in [0, 1]
- ``severity_tier``: categorical label (identity / direct_ortholog / ...)

Public API
----------
- :func:`discover_overlap` — one call, one report
- :func:`discover_pair_axes` + :func:`apply_task_profile` — split for batch
  scoring under multiple profiles
- :class:`OverlapReport`, :class:`CoMembershipResult` — result types
- :class:`TaskProfile`, :data:`TASK_PROFILES` — profile presets
- :data:`SEVERITY_TIERS`, :data:`TIER_DESCRIPTIONS` — categorical ranking
"""
from __future__ import annotations

from .clusters import (
    DEFAULT_CLUSTER_TIERS,
    DEFAULT_MIN_SPECIFICITY,
    ClusterSource,
    LeakageCluster,
    LeakageManifest,
    TIER_SOURCES,
    compute_leakage_clusters,
)
from .profiles import (
    ALL_AXES,
    TASK_PROFILES,
    TaskProfile,
    get_task_profile,
)
from .report import CoMembershipResult, OverlapReport
from .runner import (
    apply_task_profile,
    discover_overlap,
    discover_pair_axes,
)
from .tiers import (
    AXIS_TIER,
    SEVERITY_TIERS,
    TIER_DESCRIPTIONS,
    tier_for_axis,
)

# Per-axis discovery functions are exposed for advanced callers who want to
# run only a subset of axes. Most users should call ``discover_overlap``.
from .axes import (
    shared_domain_architecture,
    shared_function_class,
    shared_interaction_network,
    shared_motif_domain_family,
    shared_ortholog_membership,
    shared_pathway_membership,
    shared_structural_classification,
)

__all__ = [
    # Public types
    "TaskProfile",
    "OverlapReport",
    "CoMembershipResult",
    "LeakageManifest",
    "LeakageCluster",
    "ClusterSource",
    # Profiles
    "TASK_PROFILES",
    "ALL_AXES",
    "get_task_profile",
    # Tiers
    "SEVERITY_TIERS",
    "AXIS_TIER",
    "TIER_DESCRIPTIONS",
    "tier_for_axis",
    # Top-level driver
    "discover_overlap",
    "discover_pair_axes",
    "apply_task_profile",
    # Cluster builder
    "compute_leakage_clusters",
    "DEFAULT_CLUSTER_TIERS",
    "DEFAULT_MIN_SPECIFICITY",
    "TIER_SOURCES",
    # Per-axis (advanced)
    "shared_ortholog_membership",
    "shared_pathway_membership",
    "shared_interaction_network",
    "shared_motif_domain_family",
    "shared_structural_classification",
    "shared_function_class",
    "shared_domain_architecture",
]
