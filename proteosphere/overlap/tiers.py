"""Severity-tier ranking of overlap kinds.

The composite score answers *how strong* the leakage signal is. The
severity tier answers *what kind of relationship* it is. A single shared
Pfam (``paralog_family`` weight ~0.4) and a shared kinase fold across
100k proteins (``broad_fold`` weight 0.04) can have very different
composites yet the curator still needs to know which tier is fueling the
score.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Highest-severity-first ordering. ``identity`` = same UniProt accession on
# both sides; we detect this directly from the accession sets.
# ---------------------------------------------------------------------------
SEVERITY_TIERS: tuple[str, ...] = (
    "identity",                      # same UniProt accession in both sets
    "direct_ortholog",               # eggNOG/OrthoDB/OMA group shared
    "paralog_family",                # specific Pfam/InterPro/PANTHER family
    "domain_architecture",           # exact multi-domain Pfam composition
    "distant_homology",              # superfamily-level (SUPFAM, SCOP/CATH SF)
    "convergent_function",           # GO MF / EC subclass with no seq/struct
    "broad_fold",                    # fold-level only (SCOP fold, Gene3D)
    "shared_motif",                  # ELM / PROSITE pattern
    "shared_partial_architecture",   # partial Pfam set (Jaccard >= 0.5)
    "co_localization",               # GO_CC only — same compartment
    "shared_pathway",                # Reactome / KEGG / BioCyc
    "shared_partner",                # STRING / BioGRID interaction overlap
)


# ---------------------------------------------------------------------------
# Map every axis name to its severity tier. When adding a new axis to
# profiles.ALL_AXES, also register it here so ``severity_tier()`` and
# ``tier_breakdown()`` know how to classify it.
# ---------------------------------------------------------------------------
AXIS_TIER: dict[str, str] = {
    # direct ortholog
    "shared_orthodb_group": "direct_ortholog",
    "shared_eggnog_group": "direct_ortholog",
    "shared_oma_group": "direct_ortholog",
    "shared_hogenom_group": "direct_ortholog",
    "shared_inparanoid_group": "direct_ortholog",
    "shared_treefam_group": "direct_ortholog",
    # paralog / specific family
    "shared_panther_family": "paralog_family",
    "shared_pfam_family": "paralog_family",
    "shared_interpro_family": "paralog_family",
    "shared_smart_family": "paralog_family",
    "shared_cdd_domain": "paralog_family",
    "shared_pirsf_family": "paralog_family",
    "shared_tigrfams_family": "paralog_family",
    "shared_sfld_family": "paralog_family",
    "shared_hamap_signature": "paralog_family",
    "shared_prints_family": "paralog_family",
    "shared_scope_family": "paralog_family",
    # domain architecture
    "shared_full_domain_architecture": "domain_architecture",
    # distant homology (superfamily)
    "shared_supfam_fold": "distant_homology",
    "shared_scop_superfamily": "distant_homology",
    "shared_scope_superfamily": "distant_homology",
    "shared_cath_homologous_superfamily": "distant_homology",
    "shared_cath_h_superfamily": "distant_homology",
    # convergent function
    "shared_go_mf": "convergent_function",
    "shared_go_bp": "convergent_function",
    "shared_ec_number": "convergent_function",
    "shared_ec_subclass": "convergent_function",
    # broad fold
    "shared_scope_fold": "broad_fold",
    "shared_cath_architecture": "broad_fold",
    "shared_cath_topology": "broad_fold",
    "shared_gene3d_topology": "broad_fold",
    # motif
    "shared_prosite_motif": "shared_motif",
    "shared_elm_motif": "shared_motif",
    # partial architecture
    "shared_partial_domain_architecture": "shared_partial_architecture",
    # co-localization
    "shared_go_cc": "co_localization",
    # pathway
    "shared_reactome_pathway": "shared_pathway",
    "shared_kegg_pathway": "shared_pathway",
    "shared_biocyc_pathway": "shared_pathway",
    # partner network
    "shared_string_network": "shared_partner",
    "shared_biogrid_network": "shared_partner",
}


# ---------------------------------------------------------------------------
# Human-readable narratives for each tier. Surfaced via
# OverlapReport.severity_description() and the CLI summary.
# ---------------------------------------------------------------------------
TIER_DESCRIPTIONS: dict[str, str] = {
    "identity":
        "Same UniProt accession appears in both sets - these are the same "
        "protein. Treat as direct leakage regardless of task.",
    "direct_ortholog":
        "The two sets share an ortholog group (eggNOG / OrthoDB / OMA). "
        "Cross-species 'same protein' - strong leakage for almost any task.",
    "paralog_family":
        "A specific protein family is shared (Pfam / InterPro / PANTHER). "
        "Closely related proteins; expected to have similar sequence/structure.",
    "domain_architecture":
        "The full multi-domain Pfam composition matches. Strong evidence "
        "of paralog/orthologous functional role.",
    "distant_homology":
        "The two sets share a superfamily (SUPFAM, SCOP/CATH superfamily). "
        "Distant evolutionary relationship; structural homology is real but "
        "sequence may have diverged. Concerning for structure tasks.",
    "convergent_function":
        "Shared GO molecular function or EC subclass without sequence/"
        "structure overlap. Independent solutions to the same problem; "
        "matters for function-prediction benchmarks.",
    "broad_fold":
        "Same broad fold class (SCOP fold, CATH topology). The architecture "
        "is shared but with thousands of unrelated members - weak signal "
        "outside structure-prediction contexts.",
    "shared_motif":
        "A short linear motif (ELM) or PROSITE pattern is shared. Weak "
        "signal; may be informative for binding-site prediction.",
    "shared_partial_architecture":
        "Partial Pfam set match (Jaccard >= 0.5) - overlapping but "
        "non-identical multi-domain composition.",
    "co_localization":
        "Same GO cellular-compartment annotation. Co-localization only; "
        "not necessarily related proteins.",
    "shared_pathway":
        "Co-membership in a Reactome / KEGG / BioCyc pathway. Functional "
        "association without direct homology.",
    "shared_partner":
        "Both proteins appear in the same STRING / BioGRID interaction "
        "graph neighborhood. Indirect signal.",
    "none":
        "No overlap detected above noise floor.",
    "unknown":
        "Axis fired but is not registered in AXIS_TIER.",
}


def tier_for_axis(axis_name: str) -> str:
    """Return the severity tier for an axis name, or ``'unknown'`` if unmapped."""
    return AXIS_TIER.get(axis_name, "unknown")
