"""Task profiles — let users select which axes are in scope for their model.

A :class:`TaskProfile` declares which axes are relevant to a specific ML
task, with optional weight overrides and a prevalence floor. The 'all'
profile reports every axis with default weights; the other profiles narrow
or reweight to match how a benchmark for that task would actually leak.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# All defined axis names — used by task profiles for explicit scoping. Adding
# a new axis means adding it here AND in tiers.AXIS_TIER.
# ---------------------------------------------------------------------------
ALL_AXES: frozenset[str] = frozenset({
    # ortholog
    "shared_orthodb_group", "shared_eggnog_group", "shared_oma_group",
    "shared_hogenom_group", "shared_inparanoid_group", "shared_panther_family",
    "shared_treefam_group",
    # pathway
    "shared_reactome_pathway", "shared_kegg_pathway", "shared_biocyc_pathway",
    # interaction
    "shared_string_network", "shared_biogrid_network",
    # family / domain
    "shared_pfam_family", "shared_interpro_family", "shared_prosite_motif",
    "shared_supfam_fold", "shared_smart_family", "shared_cdd_domain",
    "shared_gene3d_topology", "shared_hamap_signature", "shared_pirsf_family",
    "shared_prints_family", "shared_tigrfams_family", "shared_sfld_family",
    "shared_elm_motif",
    # fold / superfamily
    "shared_cath_homologous_superfamily", "shared_scop_superfamily",
    "shared_scope_fold", "shared_scope_superfamily", "shared_scope_family",
    "shared_cath_architecture", "shared_cath_topology", "shared_cath_h_superfamily",
    # function class
    "shared_go_mf", "shared_go_bp", "shared_go_cc",
    "shared_ec_number", "shared_ec_subclass",
    # domain architecture
    "shared_full_domain_architecture", "shared_partial_domain_architecture",
})


@dataclass(frozen=True)
class TaskProfile:
    """A named preset that scopes which overlap axes get reported.

    Parameters
    ----------
    name
        Profile key, also used as the canonical identifier.
    description
        Human-readable description; surfaced in CLI ``--list-profiles``.
    in_scope_axes
        Axis names that count for this task. ``None`` means every axis is
        in scope (no filter).
    weight_overrides
        Maps axis name -> replacement weight in [0, 1]. Useful when an axis
        is in-scope for a task but should count less than the default
        (e.g. antibody benchmarks de-emphasize Ig-fold).
    prevalence_floor
        Minimum prevalence factor to apply for axes the task profile
        explicitly opts into (those listed in ``in_scope_axes`` or
        ``weight_overrides``). Prevents very common families from being
        crushed when the user has explicitly opted into that axis. Default
        ``0.0`` (no floor).
    """
    name: str
    description: str
    in_scope_axes: frozenset[str] | None = None
    weight_overrides: dict[str, float] = field(default_factory=dict)
    prevalence_floor: float = 0.0


# ---------------------------------------------------------------------------
# Built-in profile presets. Add new profiles by inserting another entry here.
# ---------------------------------------------------------------------------
TASK_PROFILES: dict[str, TaskProfile] = {
    "all": TaskProfile(
        name="all",
        description="Every axis. Default for general-purpose audits.",
        in_scope_axes=None,
    ),
    "strict": TaskProfile(
        name="strict",
        description="Only direct identity / close orthology. Use when you know "
                    "your benchmark only cares about identity-level leakage.",
        in_scope_axes=frozenset({
            "shared_orthodb_group", "shared_eggnog_group", "shared_oma_group",
        }),
    ),
    "sequence_classification": TaskProfile(
        name="sequence_classification",
        description="ML model takes raw sequence. Cares about identity + sequence "
                    "homology + family. Doesn't care about pure structural fold.",
        in_scope_axes=frozenset({
            "shared_orthodb_group", "shared_eggnog_group",
            "shared_pfam_family", "shared_interpro_family",
            "shared_panther_family", "shared_smart_family",
            "shared_full_domain_architecture",
            "shared_partial_domain_architecture",
        }),
    ),
    "structure_prediction": TaskProfile(
        name="structure_prediction",
        description="ML model uses 3D coords. Cares about fold/superfamily because "
                    "structural homology = label leakage in structure prediction. "
                    "Even very common folds count as moderate leakage signal.",
        in_scope_axes=frozenset({
            "shared_orthodb_group", "shared_eggnog_group",
            "shared_pfam_family", "shared_interpro_family",
            "shared_supfam_fold", "shared_cath_homologous_superfamily",
            "shared_scop_superfamily", "shared_scope_fold",
            "shared_scope_superfamily", "shared_cath_architecture",
            "shared_cath_topology", "shared_cath_h_superfamily",
            "shared_gene3d_topology", "shared_full_domain_architecture",
            "shared_partial_domain_architecture",
        }),
        weight_overrides={
            "shared_supfam_fold": 0.55,
            "shared_scope_superfamily": 0.55,
            "shared_cath_h_superfamily": 0.55,
        },
        # User has opted in to fold/family axes -> don't crush them even
        # when the family is very common.
        prevalence_floor=0.40,
    ),
    "function_prediction": TaskProfile(
        name="function_prediction",
        description="Predict GO/EC/pathway from sequence or structure. "
                    "Cares about every axis that captures function.",
        in_scope_axes=frozenset({
            "shared_orthodb_group", "shared_eggnog_group", "shared_panther_family",
            "shared_pfam_family", "shared_interpro_family",
            "shared_kegg_pathway", "shared_reactome_pathway",
            "shared_go_mf", "shared_go_bp",
            "shared_ec_number", "shared_ec_subclass",
            "shared_full_domain_architecture",
        }),
        weight_overrides={
            "shared_go_mf": 0.7,
            "shared_ec_number": 0.7,
            "shared_ec_subclass": 0.55,
            "shared_kegg_pathway": 0.5,
            "shared_reactome_pathway": 0.5,
        },
    ),
    "drug_target_affinity": TaskProfile(
        name="drug_target_affinity",
        description="Predict ligand binding for protein-ligand pairs. Cares about "
                    "binding-pocket-relevant axes (family, fold), NOT broad orthology.",
        in_scope_axes=frozenset({
            "shared_pfam_family", "shared_interpro_family",
            "shared_supfam_fold", "shared_cath_homologous_superfamily",
            "shared_scope_superfamily", "shared_full_domain_architecture",
        }),
        weight_overrides={
            "shared_pfam_family": 0.55,
            "shared_supfam_fold": 0.55,
        },
        # Pocket-shape leakage is real even for huge families; floor at 0.30.
        prevalence_floor=0.30,
    ),
    "ppi_prediction": TaskProfile(
        name="ppi_prediction",
        description="Predict protein-protein interactions. Cares about partner "
                    "graph + interaction-relevant family + ortholog overlap.",
        in_scope_axes=frozenset({
            "shared_orthodb_group", "shared_eggnog_group",
            "shared_string_network", "shared_biogrid_network",
            "shared_pfam_family", "shared_reactome_pathway",
            "shared_full_domain_architecture",
        }),
    ),
    "antibody_design": TaskProfile(
        name="antibody_design",
        description="Antibody-specific tasks. Ig fold is shared by all Abs and "
                    "should not over-flag. De-emphasize family/fold/GO axes.",
        in_scope_axes=None,
        weight_overrides={
            "shared_pfam_family": 0.1,
            "shared_interpro_family": 0.1,
            "shared_supfam_fold": 0.1,
            "shared_smart_family": 0.1,
            "shared_panther_family": 0.1,
            "shared_gene3d_topology": 0.1,
            "shared_cdd_domain": 0.1,
            # Two antibodies share immune-related GO terms by definition.
            # Dampen GO axes too for antibody benchmarks.
            "shared_go_mf": 0.1,
            "shared_go_bp": 0.1,
            "shared_go_cc": 0.1,
        },
    ),
}


def get_task_profile(name: str | None) -> TaskProfile:
    """Look up a named task profile.

    Falls back to ``'all'`` when ``name`` is None or empty. Raises
    :class:`ValueError` if ``name`` is provided but unknown so typos surface
    instead of silently picking ``'all'``.
    """
    if not name:
        return TASK_PROFILES["all"]
    if name not in TASK_PROFILES:
        raise ValueError(
            f"Unknown task profile {name!r}. Choose from: {sorted(TASK_PROFILES)}"
        )
    return TASK_PROFILES[name]
