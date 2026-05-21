# ruff: noqa: E501
from __future__ import annotations

from typing import Any

RELEASE_REVIEW_LANES = (
    "architect-review",
    "qa-review",
    "user-sim-review",
    "visual-review",
    "scientific-runtime-review",
    "refactor-review",
)

ACTIVE_OPTION_STATUSES = frozenset({"release", "beta"})
VISIBLE_OPTION_STATUSES = frozenset({"release", "beta", "beta_soon", "planned_inactive"})


_CATEGORY_ORDER = (
    "task_types",
    "label_types",
    "split_strategies",
    "graph_kinds",
    "node_feature_policies",
    "region_policies",
    "structure_source_policies",
    "preprocessing_modules",
    "model_families",
)

_UI_CATEGORY_ORDER = (
    "source_families",
    "dataset_refs",
    "acceptable_fidelity_levels",
    "node_granularities",
    "encoding_policies",
    "partner_awareness_modes",
    "global_feature_sets",
    "distributed_feature_sets",
    "architectures",
    "optimizer_policies",
    "scheduler_policies",
    "loss_functions",
    "batch_policies",
    "mixed_precision_policies",
    "uncertainty_heads",
    "evaluation_presets",
    "ablation_options",
    "hardware_runtime_presets",
)


_CAPABILITY_DETAILS: dict[str, dict[str, dict[str, str]]] = {
    "task_types": {
        "protein-protein": {
            "status": "release",
            "reason": "Structure-backed runtime exists.",
            "label": "protein-protein",
        },
        "protein-ligand": {
            "status": "beta",
            "reason": "A narrow structure-backed ligand pilot is launchable through the governed bridge subset with explicit provenance and model limits.",
        },
        "protein-nucleic-acid": {"status": "lab", "reason": "No released runtime lane yet."},
        "nucleic-acid-ligand": {"status": "lab", "reason": "No released runtime lane yet."},
    },
    "label_types": {
        "delta_G": {"status": "release", "reason": "Current runnable datasets expose delta_G."},
        "Kd": {
            "status": "beta",
            "reason": "Beta lane now supports direct Kd regression from measured molar affinity values without silently converting them to delta_G.",
        },
        "Ki": {
            "status": "beta",
            "reason": "Beta lane now supports direct Ki regression from measured molar affinity values without silently converting them to delta_G.",
        },
        "IC50": {
            "status": "beta",
            "reason": "Guarded proxy beta lane uses explicit IC50 provenance and assay-family disclosure rather than treating IC50 as direct thermodynamic truth.",
        },
        "classification": {"status": "lab", "reason": "No released classification lane."},
        "ranking": {"status": "lab", "reason": "No released ranking lane."},
    },
    "split_strategies": {
        "leakage_resistant_benchmark": {
            "status": "release",
            "reason": "Release benchmark is mapped to this strategy.",
            "label": "leakage_resistant_benchmark",
        },
        "graph_component_grouped": {
            "status": "beta",
            "reason": "Expanded PPI benchmark and beta candidate-pool compiler support graph-component grouped splitting.",
        },
        "random": {
            "status": "planned_inactive",
            "reason": "Kept visible for completeness, but not acceptable in the broadened beta protein-binding lane.",
        },
        "accession_grouped": {
            "status": "beta",
            "reason": "Beta split compiler groups by exact accession signatures, not single-accession holdout.",
        },
        "protein_ligand_component_grouped": {
            "status": "beta",
            "reason": "Launchable ligand pilot groups by canonical protein-ligand pair keys to avoid pair leakage across splits.",
        },
        "uniref_grouped": {
            "status": "beta",
            "reason": "Beta split compiler now groups on explicit UniRef keys where they exist and reports fallback coverage where they do not.",
        },
        "paper_faithful_external": {
            "status": "beta",
            "reason": "Reviewable beta split lane can now hold out external source components for audit and comparison workflows without becoming the default recommended split.",
        },
    },
    "graph_kinds": {
        "interface_graph": {
            "status": "release",
            "reason": "Runtime materializes interface residue graphs.",
            "label": "interface_graph",
        },
        "residue_graph": {
            "status": "release",
            "reason": "Runtime materializes full residue graphs.",
            "label": "residue_graph",
        },
        "hybrid_graph": {
            "status": "release",
            "reason": "Runtime materializes interface-plus-shell graphs.",
            "label": "hybrid_graph",
        },
        "shell_graph": {
            "status": "beta",
            "reason": "Beta runtime can now materialize a shell-only residue graph around interface anchors.",
        },
        "atom_graph": {
            "status": "beta",
            "reason": "Atom-native beta lane is now available with native atom parsing, atom feature vectors, and graph payload validation; keep its current limits visible in analysis and compare views.",
        },
        "whole_complex_graph": {
            "status": "beta",
            "reason": "Beta runtime currently keeps the full annotated ligand and receptor partners, not arbitrary bystander chains from the full assembly.",
            "label": "whole_complex_graph (annotated partners)",
        },
    },
    "node_feature_policies": {
        "normalized_continuous": {
            "status": "release",
            "reason": "Matches the current released feature vector path.",
            "label": "normalized_continuous",
        },
        "one_hot": {
            "status": "beta",
            "reason": "Beta residue encoders can now package residue identity and partner role as one-hot features.",
        },
        "ordinal_ranked": {
            "status": "beta",
            "reason": "Beta runtime can now emit deterministic ordinal-ranked residue encodings.",
        },
        "ordinal": {
            "status": "planned_inactive",
            "reason": "Legacy ordinal alias remains visible for compatibility, but ordinal_ranked is the beta-facing name.",
        },
        "learned_embeddings": {"status": "lab", "reason": "No released learned node encoder."},
        "provenance_aware_missing": {
            "status": "lab",
            "reason": "No released alternate missing-value encoder.",
        },
    },
    "region_policies": {
        "interface_only": {
            "status": "release",
            "reason": "Runtime supports interface-only extraction.",
            "label": "interface_only",
        },
        "interface_plus_shell": {
            "status": "release",
            "reason": "Runtime supports shell-enriched extraction.",
            "label": "interface_plus_shell",
        },
        "whole_molecule": {
            "status": "beta",
            "reason": "Beta runtime can now keep the full protein assembly when building whole-complex residue representations.",
        },
        "asymmetric_partner_aware": {
            "status": "lab",
            "reason": "Not independently surfaced in release UI.",
        },
    },
    "structure_source_policies": {
        "experimental_only": {
            "status": "release",
            "reason": "Current runtime is structure-backed only.",
            "label": "experimental_only",
        },
        "experimental_preferred_predicted_fallback": {
            "status": "lab",
            "reason": "Predicted fallback path is not released.",
        },
        "predicted_allowed": {"status": "lab", "reason": "Predicted-only path is not released."},
    },
    "preprocessing_modules": {
        "PDB acquisition": {
            "status": "release",
            "reason": "Required and executable.",
            "label": "PDB acquisition",
        },
        "chain extraction and canonical mapping": {
            "status": "release",
            "reason": "Required and executable.",
            "label": "chain extraction and canonical mapping",
        },
        "waters": {
            "status": "release",
            "reason": "Executable structural summary path exists.",
            "label": "waters",
        },
        "salt bridges": {
            "status": "release",
            "reason": "Executable structural summary path exists.",
            "label": "salt bridges",
        },
        "hydrogen-bond/contact summaries": {
            "status": "release",
            "reason": "Executable structural summary path exists.",
            "label": "hydrogen-bond/contact summaries",
        },
        "pocket/interface geometry": {
            "status": "beta",
            "reason": "Beta runtime now emits richer interface geometry summaries and pocket-style residue-depth proxies.",
        },
        "ligand descriptors": {
            "status": "beta",
            "reason": "Launchable ligand pilot emits deterministic ligand descriptor payloads alongside graph materialization.",
        },
        "sequence embeddings": {
            "status": "beta",
            "reason": "Studio-native beta materialization now emits deterministic sequence-embedding payloads with explicit model/runtime provenance and leakage-audit expectations.",
        },
        "Free-state comparison": {
            "status": "beta_soon",
            "reason": "A provenance-based Stage 2 free-state pairing audit now exists, but the lane remains review-pending until governed bound/free structure pairs are actually available and validated.",
        },
        "AlphaFold-derived support": {
            "status": "lab",
            "reason": "Blocked for the release candidate.",
        },
        "PyRosetta": {
            "status": "beta_soon",
            "reason": "A Stage 2 PyRosetta materialization contract is now compiled, but the native runtime is still blocked pending installation and scientific review.",
        },
    },
    "model_families": {
        "xgboost": {
            "status": "release",
            "reason": "Released through a HistGradientBoosting adapter.",
            "label": "xgboost-like (HistGradientBoosting)",
        },
        "catboost": {
            "status": "release",
            "reason": "Released through a RandomForest adapter.",
            "label": "catboost-like (RandomForest)",
        },
        "mlp": {
            "status": "release",
            "reason": "Released tabular NN path.",
            "label": "mlp",
        },
        "multimodal_fusion": {
            "status": "release",
            "reason": "Released as a fusion-MLP adapter over graph summaries plus globals.",
            "label": "fusion_mlp_adapter",
        },
        "graphsage": {
            "status": "release",
            "reason": "Released lightweight graph-native path.",
            "label": "graphsage-lite",
        },
        "gin": {
            "status": "beta",
            "reason": "Beta runtime currently exposes a GIN-style adapter over the lightweight graph backend with resolved-backend disclosure.",
            "label": "gin-style adapter",
        },
        "gcn": {
            "status": "beta",
            "reason": "Beta lane exposes a truthful GCN-style adapter backed by the lightweight graph trainer with resolved backend labeling.",
        },
        "gat": {
            "status": "beta",
            "reason": "Beta lane exposes a GAT-labeled adapter backed by the lightweight graph trainer with explicit resolved-backend labeling; it is not a native attention implementation.",
            "label": "gat-labeled adapter",
        },
        "edge_message_passing": {"status": "lab", "reason": "No released edge-aware adapter."},
        "heterograph": {"status": "lab", "reason": "Blocked for the release candidate."},
        "cnn": {"status": "lab", "reason": "No released spatial input lane."},
        "unet": {"status": "lab", "reason": "No released spatial input lane."},
        "late_fusion_ensemble": {
            "status": "beta",
            "reason": "Beta lane now supports an honest averaged ensemble over local tabular adapters, without a learned meta-fusion head.",
        },
    },
}

_UI_OPTION_DETAILS: dict[str, dict[str, dict[str, str]]] = {
    "source_families": {
        "release_frozen": {
            "status": "release",
            "label": "Frozen release benchmark",
            "reason": "Current internal-alpha default source family.",
        },
        "robust_structure_backed": {
            "status": "beta",
            "label": "Robust structure-backed benchmark",
            "reason": "Beta lane exposes the robust structure-backed benchmark for broader, leakage-aware studies.",
        },
        "approved_local_ppi": {
            "status": "beta",
            "label": "Approved local PPI pool",
            "reason": "Beta lane aggregates the frozen release benchmark plus the robust local structure-backed PPI benchmark, without the broader expanded pool.",
        },
        "balanced_ppi_beta_pool": {
            "status": "beta",
            "label": "Balanced broadened PPI beta pool",
            "reason": "Broadens candidate sourcing across release, robust, and expanded local PPI manifests while keeping balance diagnostics and known skew caveats visible.",
        },
        "governed_ppi_promoted_subsets": {
            "status": "beta",
            "label": "Governed promoted PPI subsets",
            "reason": "Focuses on governed PPI subsets with launchability and scope truth surfaced directly in the beta UI; promoted subsets are active while review-pending subsets remain visible but gated.",
        },
        "expanded_ppi_procurement": {
            "status": "beta",
            "label": "Expanded procurement PPI bridge",
            "reason": "Governed procurement-wave PPI rows are now selectable through the source-family lane with concentration risk and launchability diagnostics kept explicit.",
        },
        "governed_pl_bridge_pilot": {
            "status": "beta",
            "label": "Governed protein-ligand bridge pilot",
            "reason": "Structure-backed protein-ligand pilot with explicit bridge provenance, pair-group splits, and narrow launchability rules.",
        },
    },
    "dataset_refs": {
        "release_pp_alpha_benchmark_v1": {
            "status": "release",
            "label": "Release PPI alpha benchmark",
            "reason": "Frozen, structure-backed benchmark used by the released pilot lane.",
        },
        "robust_pp_benchmark_v1": {
            "status": "beta",
            "label": "Robust structure-backed benchmark",
            "reason": "Beta lane exposes the robust split benchmark as a broader, leakage-aware alternative.",
        },
        "expanded_pp_benchmark_v1": {
            "status": "beta",
            "label": "Expanded PPI benchmark",
            "reason": "Expanded structure-backed PPI benchmark is active in the broadened beta lane with balance diagnostics and known skew/overlap caveats still disclosed.",
        },
        "governed_ppi_blended_subset_v1": {
            "status": "beta_soon",
            "label": "Governed blended PPI subset",
            "reason": "Execution-tested governed PPI subset blending robust, expanded, and non-overlapping staged PPI rows; staged rows remain whole-complex only and beta-review-only until native partner-role resolution and bridge promotion review close.",
        },
        "governed_ppi_blended_subset_v2": {
            "status": "beta",
            "label": "Governed blended PPI subset v2",
            "reason": "Launchable governed PPI subset with explicit balance caps, structure-backed staged rows, and whole-complex-only constraints surfaced directly in the beta lane.",
        },
        "governed_ppi_stage2_candidate_v1": {
            "status": "beta_soon",
            "label": "Governed PPI Stage 2 candidate subset",
            "reason": "Review-pending governed PPI subset candidate compiled for Stage 2 promotion work; visible for diagnostics but not launchable by default.",
        },
        "governed_ppi_external_beta_candidate_v1": {
            "status": "beta",
            "label": "Governed external beta subset",
            "reason": "Launchable controlled external beta subset with explicit whole-complex constraints on staged rows and disclosed overlap diagnostics.",
        },
        "governed_pl_bridge_pilot_subset_v1": {
            "status": "beta",
            "label": "Governed protein-ligand bridge pilot subset",
            "reason": "Launchable structure-backed protein-ligand pilot limited to exact Kd/Ki-derived delta_G rows with explicit ligand bridge provenance.",
        },
        "final_structured_candidates_v1": {
            "status": "beta_soon",
            "label": "Final structured candidate bundle",
            "reason": "Visible for auditability, but bundle-only assets are not yet launchable as a study dataset.",
        },
        "expanded_ppi_procurement_bridge": {
            "status": "beta_soon",
            "label": "Procurement expansion bridge",
            "reason": "Latest procurement-wave datasets are visible, but not yet promoted into the study builder.",
        },
    },
    "acceptable_fidelity_levels": {
        "pilot_ready": {
            "status": "release",
            "label": "Pilot-ready",
            "reason": "Uses released, structure-backed study-builder defaults.",
        },
        "lower_fidelity_allowed": {
            "status": "release",
            "label": "Lower fidelity allowed",
            "reason": "Allows reduced study size and lighter diagnostics if needed.",
        },
        "publication_candidate": {
            "status": "beta",
            "label": "Publication candidate",
            "reason": "Beta lane now supports publication-candidate fidelity with stricter dropped-row disclosure, provenance, and quality-summary expectations than pilot_ready.",
        },
    },
    "node_granularities": {
        "residue": {
            "status": "release",
            "label": "Residue nodes",
            "reason": "Released node granularity for all currently supported graph lanes.",
        },
        "atom": {
            "status": "beta",
            "label": "Atom nodes",
            "reason": "Atom-native beta lane now exposes atom nodes and atom graph payloads with explicit current limits and compatibility checks.",
        },
        "residue_plus_atom_shell": {
            "status": "lab",
            "label": "Residue + atom shell",
            "reason": "Hybrid atom/residue granularity is planned but not yet enabled.",
        },
        "supernode_hybrid": {
            "status": "lab",
            "label": "Supernode hybrid",
            "reason": "Multi-scale node layouts remain inactive in the current beta lane.",
        },
    },
    "encoding_policies": {
        "normalized_continuous": {
            "status": "release",
            "label": "Normalized continuous",
            "reason": "Released encoding path for current graph and feature payloads.",
        },
        "one_hot": {
            "status": "beta",
            "label": "One-hot",
            "reason": "Beta lane supports explicit one-hot structural encoding for released residue-level graphs.",
        },
        "ordinal_ranked": {
            "status": "beta",
            "label": "Ordinal ranked",
            "reason": "Beta lane supports a deterministic ordinal-ranked structural encoding.",
        },
        "learned_embeddings": {
            "status": "lab",
            "label": "Learned embeddings",
            "reason": "Learned encoding policies remain inactive for the current beta path.",
        },
    },
    "partner_awareness_modes": {
        "symmetric": {
            "status": "release",
            "label": "Symmetric",
            "reason": "Released partner-awareness mode for current residue-level graphs.",
        },
        "asymmetric": {
            "status": "release",
            "label": "Asymmetric",
            "reason": "Released partner-aware mode used by the default hybrid recipe.",
        },
        "role_conditioned": {
            "status": "beta",
            "label": "Role conditioned",
            "reason": "Beta runtime adds role-conditioned node-feature encoding for ligand/receptor context with resolved feature-shape disclosure; it is not a separate graph-channel architecture.",
        },
    },
    "global_feature_sets": {
        "assay_globals": {
            "status": "release",
            "label": "Assay globals",
            "reason": "Includes label temperature and assay-context summary features.",
        },
        "structure_quality": {
            "status": "release",
            "label": "Structure quality",
            "reason": "Includes resolution and structural quality summaries.",
        },
        "interface_composition": {
            "status": "release",
            "label": "Interface composition",
            "reason": "Released engineered composition summaries for the binding interface.",
        },
        "rosetta_global_energies": {
            "status": "planned_inactive",
            "label": "Rosetta global energies",
            "reason": "PyRosetta/Rosetta lane remains Stage 2.",
        },
        "interface_chemistry": {
            "status": "beta",
            "label": "Interface chemistry",
            "reason": "Beta lane adds broader interface composition and charge/polarity summaries.",
        },
    },
    "distributed_feature_sets": {
        "residue_contacts": {
            "status": "release",
            "label": "Residue contacts",
            "reason": "Released distributed contact statistics.",
        },
        "interface_geometry": {
            "status": "release",
            "label": "Interface geometry",
            "reason": "Released distributed geometry features around the interface.",
        },
        "water_context": {
            "status": "release",
            "label": "Water context",
            "reason": "Released water-adjacent distributed summaries when waters are enabled.",
        },
        "rosetta_per_residue": {
            "status": "planned_inactive",
            "label": "Rosetta per-residue features",
            "reason": "Rosetta-derived distributed features are not yet released.",
        },
        "sequence_embeddings": {
            "status": "beta",
            "label": "Sequence embeddings",
            "reason": "Beta lane now emits Studio-native sequence embedding payloads with runtime/model provenance and leakage-audit disclosure.",
        },
        "interface_chemistry_maps": {
            "status": "beta",
            "label": "Interface chemistry maps",
            "reason": "Beta runtime can now emit richer per-example interface chemistry distribution summaries.",
        },
        "water_network_descriptors": {
            "status": "beta",
            "label": "Water-network descriptors",
            "reason": "Beta runtime now emits water-bridge and interface-water proxy descriptors from local bound structures.",
        },
    },
    "architectures": {
        "graph_global_fusion": {
            "status": "release",
            "label": "Graph + global fusion",
            "reason": "Released default multimodal fusion architecture.",
        },
        "tabular_mlp": {
            "status": "release",
            "label": "Tabular MLP",
            "reason": "Released for MLP-style studies.",
        },
        "boosted_trees_regression": {
            "status": "release",
            "label": "Boosted trees regression",
            "reason": "Released adapter for xgboost-like studies.",
        },
        "bagged_tree_regression": {
            "status": "release",
            "label": "Bagged tree regression",
            "reason": "Released adapter for catboost-like studies.",
        },
        "graphsage_interface_encoder": {
            "status": "release",
            "label": "GraphSAGE interface encoder",
            "reason": "Released lightweight graph-native architecture.",
        },
        "gin_encoder": {
            "status": "beta",
            "label": "GIN-style encoder adapter",
            "reason": "Beta lane exposes a GIN-style adapter over the lightweight graph backend.",
        },
        "gcn_encoder": {
            "status": "beta",
            "label": "GCN encoder",
            "reason": "Beta lane exposes a truthful GCN-style adapter architecture with backend disclosure.",
        },
        "late_fusion_stack": {
            "status": "beta",
            "label": "Averaged tabular ensemble",
            "reason": "Beta lane supports an averaged ensemble over local tabular adapters without a learned combiner.",
        },
        "cnn_spatial_tower": {
            "status": "planned_inactive",
            "label": "CNN spatial tower",
            "reason": "Spatial CNN lanes remain inactive.",
        },
        "gat_encoder": {
            "status": "beta",
            "label": "GAT encoder",
            "reason": "Beta lane exposes a GAT-labeled adapter architecture with backend disclosure, not a native attention-message-passing implementation.",
        },
    },
    "optimizer_policies": {
        "adamw": {"status": "release", "label": "AdamW", "reason": "Released default optimizer."},
        "adam": {"status": "release", "label": "Adam", "reason": "Released fallback optimizer."},
        "sgd_momentum": {
            "status": "release",
            "label": "SGD + momentum",
            "reason": "Released for lighter MLP-style baselines.",
        },
        "lion": {
            "status": "beta",
            "label": "Lion",
            "reason": "Beta runtime now supports Lion for graph-backed model families with resolved-backend disclosure.",
        },
    },
    "scheduler_policies": {
        "cosine_decay": {
            "status": "release",
            "label": "Cosine decay",
            "reason": "Released default scheduler.",
        },
        "one_cycle": {
            "status": "release",
            "label": "One-cycle",
            "reason": "Released alternate scheduler for fast pilot runs.",
        },
        "plateau": {
            "status": "release",
            "label": "Reduce on plateau",
            "reason": "Released for conservative local runs.",
        },
        "warmup_cosine": {
            "status": "beta",
            "label": "Warmup + cosine",
            "reason": "Beta graph trainer now supports warmup plus cosine decay scheduling.",
        },
    },
    "loss_functions": {
        "mse": {
            "status": "release",
            "label": "Mean squared error",
            "reason": "Released default regression loss.",
        },
        "mae": {
            "status": "release",
            "label": "Mean absolute error",
            "reason": "Released alternate regression loss.",
        },
        "huber": {
            "status": "release",
            "label": "Huber",
            "reason": "Released robust regression loss.",
        },
        "pairwise_ranking": {
            "status": "lab",
            "label": "Pairwise ranking",
            "reason": "Ranking lane is not released.",
        },
    },
    "batch_policies": {
        "dynamic_by_graph_size": {
            "status": "release",
            "label": "Dynamic by graph size",
            "reason": "Released default for graph-heavy pilots.",
        },
        "fixed_small_batch": {
            "status": "release",
            "label": "Fixed small batch",
            "reason": "Released conservative local setting.",
        },
        "fixed_medium_batch": {
            "status": "release",
            "label": "Fixed medium batch",
            "reason": "Released for moderate local hardware.",
        },
        "adaptive_gradient_accumulation": {
            "status": "beta",
            "label": "Adaptive gradient accumulation",
            "reason": "Beta graph trainer now adapts optimizer step cadence to graph size and local memory pressure.",
        },
    },
    "mixed_precision_policies": {
        "bf16": {
            "status": "release",
            "label": "BF16",
            "reason": "Released default when supported.",
        },
        "fp16": {
            "status": "release",
            "label": "FP16",
            "reason": "Released alternate mixed-precision option.",
        },
        "off": {"status": "release", "label": "Off", "reason": "Released CPU-safe fallback."},
        "fp8": {
            "status": "lab",
            "label": "FP8",
            "reason": "Not released for this hardware/runtime lane.",
        },
    },
    "uncertainty_heads": {
        "none": {
            "status": "release",
            "label": "None",
            "reason": "Released simplest training path.",
        },
        "heteroscedastic_regression": {
            "status": "release",
            "label": "Heteroscedastic regression",
            "reason": "Released uncertainty-aware regression head.",
        },
        "ensemble_dropout": {
            "status": "beta",
            "label": "Dropout ensemble",
            "reason": "Beta lane now exposes a clearly labeled uncertainty proxy summary with resolved-head provenance rather than silent uncertainty claims.",
        },
    },
    "evaluation_presets": {
        "regression_core": {
            "status": "release",
            "label": "Regression core",
            "reason": "Released default evaluation bundle.",
        },
        "regression_plus_calibration": {
            "status": "release",
            "label": "Regression + calibration",
            "reason": "Released for the current multimodal pilot path.",
        },
        "ranking_focus": {
            "status": "planned_inactive",
            "label": "Ranking focus",
            "reason": "Ranking evaluation is not released.",
        },
    },
    "ablation_options": {
        "graph_only": {
            "status": "release",
            "label": "Graph only",
            "reason": "Released ablation option.",
        },
        "global_only": {
            "status": "release",
            "label": "Global only",
            "reason": "Released ablation option.",
        },
        "graph_plus_global": {
            "status": "release",
            "label": "Graph + global",
            "reason": "Released ablation option.",
        },
        "drop_waters": {
            "status": "release",
            "label": "Drop waters",
            "reason": "Released ablation toggle through preprocessing selection.",
        },
        "drop_salt_bridges": {
            "status": "release",
            "label": "Drop salt bridges",
            "reason": "Released ablation toggle through preprocessing selection.",
        },
        "rosetta_only": {
            "status": "planned_inactive",
            "label": "Rosetta only",
            "reason": "Rosetta lane remains Stage 2.",
        },
    },
    "hardware_runtime_presets": {
        "auto_recommend": {
            "status": "release",
            "label": "Auto-detect and recommend",
            "reason": "Released default runtime selection path.",
        },
        "cpu_conservative": {
            "status": "release",
            "label": "CPU conservative",
            "reason": "Released for low-memory local runs.",
        },
        "cpu_parallel": {
            "status": "release",
            "label": "CPU parallel",
            "reason": "Released for stronger CPU-only hosts.",
        },
        "single_gpu": {
            "status": "release",
            "label": "Single GPU",
            "reason": "Released when CUDA is available.",
        },
        "memory_constrained": {
            "status": "release",
            "label": "Memory constrained",
            "reason": "Released fallback when RAM is limited.",
        },
        "custom": {
            "status": "beta",
            "label": "Custom",
            "reason": "Beta runtime can now resolve a custom preset against detected local hardware and disclose requested vs resolved placement truthfully.",
        },
        "multi_worker_large_memory": {
            "status": "beta",
            "label": "Large-memory multi-worker",
            "reason": "Beta runtime can recommend a higher-throughput local preset on hosts with enough RAM.",
        },
    },
}

FIELD_HELP_REGISTRY: dict[str, dict[str, Any]] = {
    "study_title": {
        "title": "Study title",
        "summary": "The human-readable name for the current dataset build, training run, and exported study summary.",
        "includes": "Used in run reports, training-set build labels, and exported audit artifacts.",
        "excludes": "Does not change dataset content or model behavior directly.",
        "artifacts": "Changes study summary text, build labels, and report headings.",
        "assumptions": "Titles should be descriptive enough for later comparison across runs.",
    },
    "task_type": {
        "title": "Task type",
        "summary": "Defines the biomolecular interaction problem the dataset and model pipeline are being built for.",
        "includes": "Current beta supports the full protein-protein lane plus a narrow launchable protein-ligand pilot.",
        "excludes": "Protein-nucleic-acid, nucleic-acid-ligand, and broader mixed interaction lanes remain inactive in this UI.",
        "artifacts": "Changes allowed dataset sources, graph semantics, model compatibility, and evaluation defaults.",
        "assumptions": "The ligand lane stays structure-backed, source-governed, and materially narrower than the primary PPI lane.",
    },
    "structure_source_policy": {
        "title": "Structure source policy",
        "summary": "Controls which structures are acceptable during dataset build and example materialization.",
        "includes": "The released path accepts experimental structures already present locally.",
        "excludes": "Predicted fallback and AlphaFold-only flows are inactive in the current UI.",
        "artifacts": "Affects dataset coverage, missing-structure blockers, and preprocessing eligibility.",
        "assumptions": "Experimental-only is the truthful current pilot default.",
    },
    "label_type": {
        "title": "Label type",
        "summary": "Defines the prediction target and downstream metric family used in the current study.",
        "includes": "The active expansion lane supports delta_G plus direct Kd/Ki regression and a guarded IC50 proxy lane with provenance disclosure.",
        "excludes": "Classification and ranking remain inactive in this phase.",
        "artifacts": "Changes compatible losses, evaluation charts, and model recommendations.",
        "assumptions": "Kd/Ki stay direct measured-label lanes, while IC50 remains a proxy assay lane and is never presented as direct thermodynamic truth.",
    },
    "include_waters": {
        "title": "Include waters",
        "summary": "Adds retained bound-state water context near the released interface region into the structural summaries.",
        "includes": "Local bound-state waters that remain in the resolved structure near interface residues and contribute to current water-context features.",
        "excludes": "Free-state comparison, displaced-water reasoning, hydrophobic displacement accounting, and specialized H-bond-network water classes are not included in the current lane.",
        "artifacts": "Changes water-context distributed features, graph summaries, and selected global counts.",
        "assumptions": "This setting is bound-state only until the Stage 2 free-state lane exists.",
    },
    "include_salt_bridges": {
        "title": "Include salt bridges",
        "summary": "Adds simple ionic interaction summaries derived from local residue geometry.",
        "includes": "Released salt-bridge counts and neighboring context around acidic/basic residue pairs in the resolved structure.",
        "excludes": "Free-state delta salt-bridge analysis and Rosetta-based electrostatics are not included.",
        "artifacts": "Changes distributed ionic summaries, graph summary features, and post-run study notes.",
        "assumptions": "Current salt-bridge logic is geometry-driven and local to the bound structure.",
    },
    "include_contact_shell": {
        "title": "Include contact shell",
        "summary": "Extends the representation beyond strict interface residues to include the released shell region around the interface.",
        "includes": "Neighboring residues around the interface within the current released shell strategy.",
        "excludes": "Whole-molecule and custom shell policies are inactive.",
        "artifacts": "Changes graph size, shell counts, and hybrid/interface-plus-shell packaging.",
        "assumptions": "The current released shell is tuned for the hybrid graph lane.",
    },
    "acceptable_fidelity": {
        "title": "Acceptable fidelity",
        "summary": "Controls how strict the Studio should be when assembling a usable study dataset from local sources.",
        "includes": "Pilot-ready, lower-fidelity, and publication-candidate gating are available with progressively stricter disclosure and review expectations.",
        "excludes": "It does not turn blocked features into launchable ones or bypass missing-structure constraints.",
        "artifacts": "Changes candidate row inclusion, build diagnostics, and warning severity.",
        "assumptions": "Publication-candidate expects stronger provenance, dropped-row, and quality summaries than the broader pilot-ready lane.",
    },
    "max_resolution": {
        "title": "Max resolution",
        "summary": "Filters out structures whose reported crystallographic resolution is worse than the selected threshold.",
        "includes": "Current filtering uses the available structure resolution value from the local dataset row.",
        "excludes": "Does not currently model per-chain or per-interface local resolution quality.",
        "artifacts": "Changes candidate dataset membership and structure-quality diagnostics.",
        "assumptions": "Lower values favor more reliable structural detail but reduce available row count.",
    },
    "min_release_year": {
        "title": "Min release year",
        "summary": "Drops structures released before the selected year threshold.",
        "includes": "Current filtering uses the structure release year recorded in the local dataset row.",
        "excludes": "Does not currently inspect deposition year vs release year separately.",
        "artifacts": "Changes candidate dataset membership and may shift source-family balance.",
        "assumptions": "Useful when users want a more modern structural subset or want to test temporal robustness.",
    },
    "exclude_pdb_ids": {
        "title": "Exclude PDB ids",
        "summary": "Removes specific structures from preview, build, and training flows.",
        "includes": "Accepts comma-separated PDB ids and records them as explicit exclusions.",
        "excludes": "Does not yet support exclusion by accession, paper, or source family through this field.",
        "artifacts": "Changes preview/build membership and exclusion diagnostics.",
        "assumptions": "Use this when a user knows certain structures should not participate in the study.",
    },
    "loss_function": {
        "title": "Loss function",
        "summary": "Defines the optimization objective used during training.",
        "includes": "Released regression losses validated in the current matrix.",
        "excludes": "Ranking and custom user-provided losses are inactive in the current UI.",
        "artifacts": "Changes training behavior, metrics interpretation, and recommendation checks.",
        "assumptions": "The pilot path is regression-first.",
    },
    "split_strategy": {
        "title": "Split strategy",
        "summary": "Defines how the study dataset is partitioned into train, validation, and test sets.",
        "includes": "The active beta lane uses leakage-aware grouping rules, including graph-component, exact accession-signature, UniRef-grouped, paper-faithful external, and ligand pair-grouped splits.",
        "excludes": "Random remains visible for completeness but is not recommended for serious protein-binding evaluation.",
        "artifacts": "Changes split membership, leakage checks, benchmark comparability, and post-run diagnostics.",
        "assumptions": "UniRef-grouped uses true UniRef keys where available and reports fallback grouping coverage where the local row metadata is incomplete.",
    },
    "dataset_primary": {
        "title": "Primary runnable dataset",
        "summary": "Selects the main structure-backed dataset the Studio should resolve first for preview and build operations.",
        "includes": "Released frozen and approved local structure-backed PPI datasets with known manifests.",
        "excludes": "Procurement-wave sources without integrated manifests remain inactive for the guided flow.",
        "artifacts": "Changes candidate PDB rows, provenance, maturity status, split behavior, and available run comparisons.",
        "assumptions": "The selected dataset is the anchor source unless source-family aggregation expands it.",
    },
    "dataset_refs": {
        "title": "Additional dataset refs",
        "summary": "Lets the Studio draw candidate rows from additional known dataset manifests beyond the primary selection.",
        "includes": "Only datasets with known local manifests appear here.",
        "excludes": "Unintegrated procurement wave sources remain inactive and cannot be launched from this control.",
        "artifacts": "Changes candidate row pool, source breakdown, and split/build diagnostics.",
        "assumptions": "Additional dataset refs expand the candidate pool but do not bypass released structure and split constraints.",
    },
    "audit_requirements": {
        "title": "Audit requirements",
        "summary": "Defines which readiness checks should stay visible while previewing, building, and reviewing a study.",
        "includes": "The current beta path surfaces leakage, overlap, balance, and maturity diagnostics.",
        "excludes": "It does not yet route into a formal signoff workflow or reviewer queue from this control alone.",
        "artifacts": "Changes which diagnostics are highlighted in preview, build, and post-run review surfaces.",
        "assumptions": "These checks help keep internal beta studies auditable and comparable.",
    },
    "hardware_runtime_preset": {
        "title": "Hardware & runtime preset",
        "summary": "Chooses how the Studio uses the local machine after checking CPU, RAM, and CUDA availability.",
        "includes": "Auto recommendation, released CPU/GPU presets, and a beta custom preset that resolves against detected local hardware.",
        "excludes": "It does not expose arbitrary device IDs or unsupported manual overrides beyond what the backend can resolve truthfully.",
        "artifacts": "Changes runtime recommendations, warnings, and training backend selection.",
        "assumptions": "The backend remains authoritative for the resolved runtime even when the user requests the custom preset.",
    },
    "architecture": {
        "title": "Architecture",
        "summary": "Defines the structural shape of the selected released model family.",
        "includes": "Released tabular, fusion, and GraphSAGE-lite architecture shapes that map to the current backends.",
        "excludes": "Inactive spatial, GIN, and custom-graph towers remain shown for planning only.",
        "artifacts": "Changes the live model diagram, compatibility checks, and which features are actually consumed at training time.",
        "assumptions": "The current architecture list is constrained by the selected model family and release matrix.",
    },
    "evaluation_preset": {
        "title": "Evaluation preset",
        "summary": "Applies a bundled set of post-training metrics and review slices for the current run.",
        "includes": "Released regression-first metrics, calibration-friendly summaries, and study-level diagnostics.",
        "excludes": "Ranking-only and publication-scale benchmark presets are inactive in this UI.",
        "artifacts": "Changes which metrics, charts, warnings, and compare-view summaries appear after training.",
        "assumptions": "Internal pilot testing prioritizes regression quality and interpretability over benchmark breadth.",
    },
    "source_families": {
        "title": "Source families",
        "summary": "Controls which approved local source pools the Studio may draw from when building a study dataset.",
        "includes": "Frozen and governed PPI pools, the active governed procurement bridge lane, and the narrow governed protein-ligand bridge pilot.",
        "excludes": "Broader non-PPI lanes and raw unmanaged procurement content remain gated or inactive.",
        "artifacts": "Changes candidate dataset rows, coverage, maturity, and split diagnostics.",
        "assumptions": "Procurement rows still flow through governed admissibility and concentration diagnostics; they are never allowed to bypass canonical promotion logic.",
    },
    "graph_kind": {
        "title": "Graph kind",
        "summary": "Defines the topology and scope of the graph payload used for modeling.",
        "includes": "Interface, residue, hybrid, shell-only, annotated-partner whole-complex, and atom-native beta lanes.",
        "excludes": "Heterograph and broader multi-scale graph families remain inactive.",
        "artifacts": "Changes graph payloads, graph summaries, compatibility, and model structure diagrams.",
        "assumptions": "The current whole-complex beta lane keeps the annotated ligand and receptor partners, not arbitrary bystander chains from the full assembly. Atom-native beta is available, but still narrower than the mature residue lane.",
    },
    "region_policy": {
        "title": "Region policy",
        "summary": "Defines which structural neighborhood is kept when building graph and feature payloads.",
        "includes": "Interface-only, interface-plus-shell, and whole-molecule beta partner-retention regions.",
        "excludes": "Custom asymmetric shell expansions remain inactive.",
        "artifacts": "Changes graph size, shell counts, feature packaging, and model compatibility.",
        "assumptions": "Hybrid graphs pair best with interface-plus-shell; interface graphs pair best with interface-only.",
    },
    "node_granularity": {
        "title": "Node granularity",
        "summary": "Controls whether graph nodes represent residues, atoms, or a future multi-scale hybrid.",
        "includes": "The active beta lane materializes both mature residue-node graphs and a native atom-node beta lane.",
        "excludes": "Residue-plus-atom hybrids and other multi-scale node modes remain inactive until a true multi-scale packaging path lands.",
        "artifacts": "Changes graph payload shape, feature engineering assumptions, graph-edge density, and model compatibility.",
        "assumptions": "Atom-node beta is real but narrower than the residue lane: use it with atom_graph and keep its current limits visible in analysis and compare surfaces.",
    },
    "encoding_policy": {
        "title": "Encoding policy",
        "summary": "Defines how raw structural attributes are represented before entering the graph payload.",
        "includes": "Normalized continuous, one-hot, and ordinal-ranked beta encodings.",
        "excludes": "Learned encoders remain planned but inactive.",
        "artifacts": "Changes graph node/edge payload semantics and compatibility warnings.",
        "assumptions": "Current runtime applies the selected encoding policy primarily through the graph recipe configuration.",
    },
    "partner_awareness": {
        "title": "Partner awareness",
        "summary": "Controls how explicitly the graph payload marks ligand-versus-receptor role information.",
        "includes": "Symmetric, asymmetric, and beta role-conditioned node-feature variants in the current bound-state graph payload.",
        "excludes": "Free-state, mutation-conditioned, or partner-history-aware semantics remain inactive.",
        "artifacts": "Changes graph node feature dimensionality, model compatibility notes, and feature-shape disclosure in beta runs.",
        "assumptions": "Role-conditioned mode is still bound-state-only and adds role-aware node features; it does not imply a separate thermodynamic-state model.",
    },
    "node_feature_policy": {
        "title": "Node feature policy",
        "summary": "Defines how node-level features are packaged for graph modeling.",
        "includes": "The current beta lane mirrors the graph encoding policy into the node payload configuration.",
        "excludes": "Learned node encoders and provenance-aware missing-value strategies remain inactive.",
        "artifacts": "Changes graph tensor structure, compatibility checks, and model structure previews.",
        "assumptions": "Node feature policy is currently coupled to the graph recipe encoding choice rather than implemented as an independent featurization branch.",
    },
    "edge_feature_policy": {
        "title": "Edge feature policy",
        "summary": "Defines how pairwise relationships are encoded on graph edges.",
        "includes": "The current beta lane records continuous geometric/contact edge metadata.",
        "excludes": "Specialized categorical or learned edge encoders remain inactive.",
        "artifacts": "Changes graph payload semantics and compatibility with future graph model families.",
        "assumptions": "Edge feature policy is currently descriptive metadata for the beta lane and does not activate a distinct edge-modeling backend.",
    },
    "global_feature_sets": {
        "title": "Global feature bundles",
        "summary": "Adds study-wide or structure-wide engineered features that are not attached to individual nodes.",
        "includes": "Released assay, structure-quality, and interface-composition bundles.",
        "excludes": "Rosetta global energies and other inactive bundles remain visible for planning only.",
        "artifacts": "Changes tabular inputs, fusion branches, and post-run feature summary reporting.",
        "assumptions": "These bundles complement graph payloads rather than replace them.",
    },
    "distributed_feature_sets": {
        "title": "Distributed feature bundles",
        "summary": "Adds structured per-example distributed descriptors that complement graph and global signals.",
        "includes": "Active beta bundles include residue contacts, interface geometry, water context, interface chemistry maps, water-network descriptors, and Studio-native sequence embeddings when supported by preprocessing.",
        "excludes": "Rosetta-derived per-residue bundles remain inactive.",
        "artifacts": "Changes packaged distributed tensors, multimodal fusion paths, compatibility warnings, and embedding provenance disclosure.",
        "assumptions": "Water-network descriptors are still proxy-level bound-state summaries, not free-state or displaced-water reasoning. Sequence embeddings are beta-scoped and must keep model/runtime provenance and leakage-audit expectations visible.",
    },
    "model_family": {
        "title": "Model family",
        "summary": "Selects the active training backend family for the current study run.",
        "includes": "Released tabular and fusion paths plus adapter-backed beta graph and ensemble families.",
        "excludes": "CNN, U-Net, heterograph, and unvalidated graph families remain inactive.",
        "artifacts": "Changes available architecture options, compatibility warnings, metrics interpretation, and runtime recommendations.",
        "assumptions": "Some beta-visible graph families are adapter-backed and disclose their resolved backend in run outputs.",
    },
    "optimizer": {
        "title": "Optimizer",
        "summary": "Controls how the selected trainable model updates its parameters during optimization.",
        "includes": "Released local optimizers plus beta Lion for graph-backed model families.",
        "excludes": "Sklearn-backed tree and tabular adapters still use backend-default fitting behavior instead of neural optimizer controls.",
        "artifacts": "Changes training dynamics, learning curves, and hardware/runtime recommendations.",
        "assumptions": "Lion is compatibility-gated to graph-native families in the current beta lane.",
    },
    "scheduler": {
        "title": "Scheduler",
        "summary": "Defines how learning-rate behavior evolves during training for trainable neural backends.",
        "includes": "Released cosine, one-cycle, and plateau-style options plus beta warmup-plus-cosine for graph-backed model families.",
        "excludes": "Sklearn-backed tabular adapters still ignore scheduler controls.",
        "artifacts": "Changes learning-curve behavior and training-plan summaries.",
        "assumptions": "Warmup-plus-cosine is compatibility-gated to graph-native families in the current beta lane.",
    },
    "batch_policy": {
        "title": "Batch policy",
        "summary": "Controls how examples are grouped during training, especially for graph-heavy payloads.",
        "includes": "Released fixed and dynamic batch policies plus beta adaptive gradient accumulation for graph-backed model families.",
        "excludes": "Sklearn-backed tabular families do not materialize minibatches in the same way and ignore graph batching controls.",
        "artifacts": "Changes training runtime behavior, memory pressure, and hardware warnings.",
        "assumptions": "Adaptive accumulation is compatibility-gated to graph-native families in the current beta lane.",
    },
    "mixed_precision": {
        "title": "Mixed precision",
        "summary": "Controls whether reduced-precision arithmetic is used during trainable model execution.",
        "includes": "Released BF16, FP16, and off modes.",
        "excludes": "FP8 and more aggressive precision modes remain inactive.",
        "artifacts": "Changes runtime performance, memory pressure, and hardware compatibility warnings.",
        "assumptions": "This only matters on trainable backends that use floating-point accelerators.",
    },
    "uncertainty_head": {
        "title": "Uncertainty head",
        "summary": "Adds optional predictive uncertainty behavior to supported regression models.",
        "includes": "The beta path supports no uncertainty head, heteroscedastic regression, and a guarded ensemble-dropout-style uncertainty summary with explicit proxy provenance.",
        "excludes": "It does not imply native Bayesian or MC-dropout semantics unless the resolved runtime explicitly says so.",
        "artifacts": "Changes metrics, post-run summaries, and compare-view interpretation.",
        "assumptions": "Uncertainty summaries are most meaningful for trainable neural backends, and proxy heads must stay labeled as proxies in compare/export views.",
    },
    "ablation_options": {
        "title": "Ablation options",
        "summary": "Defines which modality or preprocessing variants should be tested alongside the main configuration.",
        "includes": "Released graph/global and preprocessing-drop ablations.",
        "excludes": "Rosetta-only and other inactive ablation families remain visible but unavailable.",
        "artifacts": "Changes compare-view expectations and run-level recommendation notes.",
        "assumptions": "Ablations help users audit which modality families matter most.",
    },
    "preprocess_modules": {
        "title": "Preprocess modules",
        "summary": "Controls which released structural processing modules contribute to example materialization.",
        "includes": "Released structure checks, chain mapping, waters, salt bridges, and contact summaries.",
        "excludes": "PyRosetta and free-state comparison remain review-pending Stage 2 tracks rather than launchable preprocessing modules.",
        "artifacts": "Changes feature materialization, graph content, charts, semantic warnings, and blocked-feature explanations when a Stage 2 module is selected.",
        "assumptions": "Modules shown as review-pending or inactive are intentionally visible for roadmap transparency and scientific review, but they do not enter the launchable beta lane until their native contracts and matrix tests clear.",
    },
}

STEPPER_DEFINITION: tuple[dict[str, str], ...] = (
    {
        "id": "training-request",
        "label": "Training Set Request",
        "summary": "Define the target study dataset and source pool.",
        "workspace": "workspace-data-strategy",
    },
    {
        "id": "dataset-preview",
        "label": "Dataset Preview",
        "summary": "Inspect candidate PDBs, diagnostics, and early split quality.",
        "workspace": "workspace-data-strategy",
    },
    {
        "id": "build-split",
        "label": "Build & Split",
        "summary": "Create a concrete study dataset with train/val/test membership.",
        "workspace": "workspace-data-strategy",
    },
    {
        "id": "representation-features",
        "label": "Representation & Features",
        "summary": "Choose graph, global, distributed, and preprocessing bundles.",
        "workspace": "workspace-representation",
    },
    {
        "id": "pipeline-design",
        "label": "Pipeline Design",
        "summary": "Choose the model family, architecture, optimization, and evaluation presets.",
        "workspace": "workspace-pipeline-composer",
    },
    {
        "id": "run-monitor",
        "label": "Run & Monitor",
        "summary": "Launch the study and monitor heartbeat, progress, and artifacts.",
        "workspace": "workspace-execution-console",
    },
    {
        "id": "analysis-compare",
        "label": "Analysis & Compare",
        "summary": "Review metrics, charts, outliers, warnings, and comparisons.",
        "workspace": "workspace-analysis-review",
    },
    {
        "id": "export-review",
        "label": "Export & Review",
        "summary": "Share a summary and review the current study state.",
        "workspace": "workspace-analysis-review",
    },
)

ACTION_CONTRACT: tuple[dict[str, str], ...] = (
    {"action": "save_draft", "response": "Persists the current draft and refreshes saved state."},
    {"action": "validate_draft", "response": "Runs recommendation and quality-gate validation."},
    {
        "action": "compile_graph",
        "response": "Compiles the execution graph and updates stage readiness.",
    },
    {
        "action": "preview_dataset",
        "response": "Builds a candidate dataset preview and diagnostics summary.",
    },
    {
        "action": "build_dataset",
        "response": "Creates a concrete study dataset with split artifacts.",
    },
    {
        "action": "launch_run",
        "response": "Starts a background study run and opens live monitoring.",
    },
    {
        "action": "refresh_runtime",
        "response": "Refreshes run state, hardware state, and recent artifacts.",
    },
    {
        "action": "discover_hardware",
        "response": "Refreshes local CPU, RAM, and CUDA recommendations.",
    },
    {
        "action": "inactive_explanation",
        "response": "Explains why an inactive option is not yet enabled.",
    },
)


def build_capability_registry() -> dict[str, list[dict[str, str]]]:
    registry: dict[str, list[dict[str, str]]] = {}
    for category in _CATEGORY_ORDER:
        registry[category] = [
            {
                "value": value,
                "label": detail.get("label", value),
                "status": detail["status"],
                "reason": detail["reason"],
                "audience_visibility": detail.get(
                    "audience_visibility",
                    "default" if detail["status"] in ACTIVE_OPTION_STATUSES else "advanced",
                ),
                "help_summary": detail.get("help_summary", detail["reason"]),
                "help_detail": detail.get("help_detail", detail["reason"]),
                "inactive_reason": detail.get(
                    "inactive_reason",
                    detail["reason"] if detail["status"] != "release" else "",
                ),
            }
            for value, detail in _CAPABILITY_DETAILS[category].items()
        ]
    return registry


def build_design_catalog(*, include_lab: bool = False) -> dict[str, Any]:
    catalog: dict[str, Any] = {}
    for category in _CATEGORY_ORDER:
        allowed = (
            {"release", "beta", "beta_soon", "planned_inactive", "lab", "blocked"}
            if include_lab
            else ACTIVE_OPTION_STATUSES
        )
        catalog[category] = [
            {
                "value": value,
                "label": detail.get("label", value),
                "status": detail["status"],
                "reason": detail["reason"],
                "audience_visibility": detail.get(
                    "audience_visibility",
                    "default" if detail["status"] in ACTIVE_OPTION_STATUSES else "advanced",
                ),
            }
            for value, detail in _CAPABILITY_DETAILS[category].items()
            if detail["status"] in allowed
        ]
    return catalog


def build_lab_catalog() -> dict[str, Any]:
    return build_design_catalog(include_lab=True)


def build_ui_option_registry() -> dict[str, list[dict[str, str]]]:
    registry: dict[str, list[dict[str, str]]] = {}
    for category in _UI_CATEGORY_ORDER:
        registry[category] = [
            {
                "value": value,
                "label": detail.get("label", value),
                "status": detail["status"],
                "reason": detail["reason"],
                "audience_visibility": detail.get(
                    "audience_visibility",
                    "default" if detail["status"] in ACTIVE_OPTION_STATUSES else "advanced",
                ),
                "help_summary": detail.get("help_summary", detail["reason"]),
                "help_detail": detail.get("help_detail", detail["reason"]),
                "inactive_reason": detail.get(
                    "inactive_reason",
                    detail["reason"] if detail["status"] != "release" else "",
                ),
            }
            for value, detail in _UI_OPTION_DETAILS[category].items()
        ]
    return registry


def build_field_help_registry() -> dict[str, dict[str, Any]]:
    return {key: dict(value) for key, value in FIELD_HELP_REGISTRY.items()}


def build_stepper_definition() -> list[dict[str, str]]:
    return [dict(item) for item in STEPPER_DEFINITION]


def build_action_contract() -> list[dict[str, str]]:
    return [dict(item) for item in ACTION_CONTRACT]


def option_status(category: str, value: str) -> str:
    detail = _CAPABILITY_DETAILS.get(category, {}).get(value) or _UI_OPTION_DETAILS.get(
        category, {}
    ).get(value, {})
    return detail.get("status", "unsupported")


def option_reason(category: str, value: str) -> str:
    detail = _CAPABILITY_DETAILS.get(category, {}).get(value) or _UI_OPTION_DETAILS.get(
        category, {}
    ).get(value, {})
    return detail.get("reason", "Unsupported option.")


def is_release_option(category: str, value: str) -> bool:
    return option_status(category, value) == "release"


def is_active_option(category: str, value: str) -> bool:
    return option_status(category, value) in ACTIVE_OPTION_STATUSES


def filter_known_datasets(
    datasets: list[dict[str, Any]],
    *,
    include_lab: bool = False,
) -> list[dict[str, Any]]:
    allowed = (
        {"release", "beta", "beta_soon", "planned_inactive", "lab", "blocked"}
        if include_lab
        else ACTIVE_OPTION_STATUSES
    )
    return [item for item in datasets if item.get("catalog_status", "lab") in allowed]
