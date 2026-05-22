// ProteoSphere — fixture data
// Sourced from the user's reference_library warehouse summary.

// Tier model — 5 lanes ("active lanes") an option can sit in.
// Plain-language labels matter — many users won't know "release" means "production".
window.PS_TIERS = {
  release:          { label: "Production",  short: "Prod",   tone: "signal",    desc: "Fully shipped. Safe to use in benchmarks and prod runs." },
  beta:             { label: "Beta",        short: "Beta",   tone: "primary",   desc: "Active beta. Works, but expect changes; tag your runs accordingly." },
  beta_soon:        { label: "Coming soon", short: "Soon",   tone: "warn",      desc: "Next to ship. Currently gated — needs a small piece of work to clear." },
  lab:              { label: "Lab",         short: "Lab",    tone: "molecular", desc: "Visible for completeness. Not in the beta lane yet — for the curious only." },
  planned_inactive: { label: "Blocked",     short: "Blocked", tone: "error",    desc: "Deliberately not shipping. Read the explanation before lobbying for it." },
};

// Plain-language glossary used by <Term> tooltips. Add to it freely.
window.PS_GLOSSARY = {
  "DTA": "Drug–target affinity. How tightly a small molecule binds to a protein.",
  "pKi": "Negative log of the inhibition constant Ki, in molar (pKi = −log10[Ki]). Higher = stronger binding. pKi 9 ≈ 1 nM.",
  "Kd": "Dissociation constant. Equilibrium binding affinity. Smaller = stronger. Reported in molar.",
  "Ki": "Inhibition constant — equilibrium binding affinity of a competitive inhibitor. Related to IC50 only under Cheng–Prusoff assumptions; not interchangeable in general.",
  "IC50": "Concentration that inhibits 50% of activity. Depends on assay conditions (substrate, ATP, time). Not the same as Ki or Kd without context.",
  "ECFP": "Extended-connectivity fingerprint (a.k.a. Morgan, radius 2 = ECFP4). A bit-vector of a molecule's substructure environments.",
  "Tanimoto": "Similarity score for fingerprints. 0 = unrelated, 1 = identical. For ECFP4, ~0.4 is a common leakage cutoff.",
  "MMseqs2": "Fast sequence clustering tool (Steinegger & Söding 2017). Used here to cluster proteins by sequence identity; ~0.30 is a defensible cutoff for honest leakage control.",
  "ESM-2": "Protein language model from Meta AI. Turns a sequence into a learned vector. Available at 8M–15B parameters; 650M fits on one consumer GPU for inference.",
  "MolFormer": "Chemistry language model. Turns a SMILES string into a learned vector.",
  "SMILES": "Text notation for chemical structure (e.g. 'CCO' = ethanol).",
  "leakage": "When information about the test set leaks into training (same protein, similar ligand, duplicated assay entry…), making metrics look better than reality.",
  "binding type": "The kind of bound thing you're modelling — protein + small molecule, two proteins, antibody + antigen, etc. Picked on the Goal screen; drives the dataset filter, the splits header, and which pipeline templates make sense.",
  "binding partners": "The molecules in the bound thing — typically a protein plus a small-molecule ligand (P-L) or a protein plus another protein (P-P).",
  "ECE": "Expected calibration error — a binned classification metric measuring how well predicted probabilities match observed frequencies. For regression heads you want interval-coverage curves instead.",
  "ROC AUC": "Area under the ROC curve. 0.5 = random, 1.0 = perfect ranking.",
  "Pearson": "Correlation of predicted vs actual values. 1.0 = perfect linear fit.",
  "Spearman": "Rank correlation. Robust to non-linear relationships.",
  "RMSE": "Root-mean-square error. Average prediction error, same units as target.",
  "MAE": "Mean absolute error. Average prediction error, less sensitive to outliers than RMSE.",
  "R²": "Variance explained. 1.0 = perfect, 0 = no better than the mean.",
  "conformal interval": "A prediction range that, on average across exchangeable held-out data, covers the true value at the stated rate (e.g. 90%). Coverage is marginal — it can sag under distribution shift.",
  "SHAP": "Additive Shapley feature attributions — splits a prediction into per-feature contributions that sum to the prediction. Not the same as attention weights.",
  "attention": "Internal weights a model assigns to input positions while computing its output. Not an attribution; high attention does not mean high contribution to the prediction (Jain & Wallace 2019).",
  "Pareto": "The set of options where you can't improve one metric without hurting another.",
  "scaffold split": "Splitting molecules by their Bemis–Murcko core, so chemically similar molecules don't cross splits.",
  "cold target": "A protein never seen during training — a hard test of generalization.",
  "ATP pocket": "The site where ATP binds in a kinase. Most kinase inhibitors target it.",
  "QED": "Quantitative Estimate of Drug-likeness (Bickerton 2012). A 0–1 score that combines molecular weight, lipophilicity, h-bond donors/acceptors, rotatable bonds, polar surface area, aromatic rings, and a structural-alert flag. 0 = nothing about it looks like a drug; 1 = looks like an approved drug.",
  "Pfam": "Protein family classification: ~20,000 hand-curated domains keyed by hidden-Markov-model profiles (e.g. PF00069 is the protein-kinase catalytic domain). Used here to filter targets by family.",
  "MW": "Molecular weight (Daltons). Most oral drugs are 150–500 Da; fragments are <300 Da; biologics are thousands.",
  "ΔG": "Binding free energy (kcal·mol⁻¹ or kJ·mol⁻¹). ΔG° = −RT · ln(Kd). At 298 K, 1 pKi unit ≈ −1.36 kcal·mol⁻¹.",
  "deltaG": "Binding free energy (kcal·mol⁻¹ or kJ·mol⁻¹). ΔG° = −RT · ln(Kd). At 298 K, 1 pKi unit ≈ −1.36 kcal·mol⁻¹.",
  "assay confidence": "ChEMBL's 0–9 score for how directly a measurement maps to a single human target. 9 = direct binding against the named human protein; 6–8 = homologous protein or close paralog; <6 = cell or tissue assay where the target isn't fully isolated.",
  "DTA": "Drug–target affinity: predicting how tightly a small molecule binds a protein.",
  "DDP": "Distributed data parallel — split a batch across GPUs.",
  "FSDP": "Fully sharded data parallel — also splits model weights across GPUs. Required for fine-tuning ≥1B-parameter models on consumer GPUs.",
  "bf16": "16-bit floating-point with extra range. Cheaper than fp32, less prone to overflow than fp16.",
  "DuckDB": "An embedded analytics database. Powers our warehouse queries.",
  "warehouse": "The reference library: a DuckDB catalog + partitioned parquet of authoritative proteins, ligands, binding pairs, structures and similarity signatures.",
  // ── Pipeline-tab jargon ──────────────────────────────────────────────
  "PLM":            "Protein language model — a transformer trained on millions of protein sequences (e.g. ESM-2, ProtBERT, Ankh). Encodes each residue into a vector that captures sequence + structural priors.",
  "chem-LM":        "Chemical language model — a transformer trained on millions of SMILES strings (e.g. MolFormer, ChemBERTa). Encodes a molecule into a vector that captures chemistry priors.",
  "checkpoint":     "A specific pretrained-weight file for a model. Larger checkpoints (e.g. ESM-2 t36 3B) are more accurate but cost more compute. Smaller (t12 35M) is fast but weaker.",
  "freeze":         "When `freeze = true`, the pretrained encoder's weights stay fixed during training and only the new layers learn. Cheaper and rarely degrades quality if your fine-tune data is small.",
  "LoRA":           "Low-Rank Adaptation. Injects small trainable matrices into a frozen pretrained model so you can fine-tune billion-parameter encoders on a single GPU. `rank` ≈ capacity (8–32 is typical).",
  "MSA":            "Multiple sequence alignment — a stack of related sequences from homologs across species. AlphaFold-style models read it to infer coevolution and predict structure.",
  "3Di":            "Foldseek's structural alphabet — a 20-letter code that summarises local 3D geometry, so you can compare folds with sequence-style tools (Foldseek, SaProt).",
  "GNN":            "Graph neural network — a network that operates on a molecular graph (atoms = nodes, bonds = edges) and aggregates information from each atom's neighbours.",
  "GCN":            "Graph Convolutional Network. The simplest GNN: each layer averages a node's features with its neighbours' features.",
  "GAT":            "Graph Attention Network. A GNN that learns attention weights over a node's neighbours rather than averaging them uniformly.",
  "GIN":            "Graph Isomorphism Network — a GNN provably as expressive as the Weisfeiler-Lehman graph-isomorphism test. Strong default for molecule property prediction.",
  "EGNN":           "Equivariant GNN (Satorras 2021). Operates on 3D atom coordinates and respects rotation/translation symmetry: rotate the input → output rotates the same way.",
  "GVP-GNN":        "Geometric Vector Perceptron GNN (Jing 2021). Mixes scalar and vector features so the network can reason about both bond lengths and bond orientations equivariantly.",
  "SE(3)":          "The group of rigid-body rotations and translations in 3D. An SE(3)-equivariant network commutes with these transformations: physics doesn't change if you rotate the molecule.",
  "Equiformer":     "An SE(3)-equivariant transformer (Liao 2023) that combines spherical-harmonic features with attention. Strong on molecular property prediction.",
  "MACE":           "Higher-body-order equivariant message passing (Batatia 2022). Used in molecular dynamics potentials and increasingly for DTA.",
  "Evoformer":      "AlphaFold-2's trunk: 48 blocks of MSA-attention + triangular pair updates. Produces single + pair representations consumed by the pose head.",
  "triangle update":"AlphaFold's pair-stack operation that updates a residue-pair feature using the other two edges of every triangle of residues. The geometric inductive bias that makes folding work.",
  "outer-product interaction": "Form a 2D interaction matrix by taking the outer product of the protein and ligand embeddings, so the model can see every protein-position × ligand-position pair.",
  "bilinear attention": "A trainable bilinear form (xᵀWy) that scores how strongly each protein position attends to each ligand atom. Cheaper than full cross-attention.",
  "cross-attention":"A transformer block where queries from one tower attend to keys/values from the other (protein → ligand and back). Heavier than concat or bilinear, often more accurate.",
  "co-attention":   "Like cross-attention but symmetric — both towers attend to each other simultaneously and the attention maps share parameters.",
  "joint-graph":    "Builds a combined molecular graph that includes both protein-side and ligand-side atoms with their inter-atomic edges, then runs message passing on the whole thing.",
  "keypoint matching": "Predict a small set of corresponding (protein-atom, ligand-atom) pairs and solve a rigid alignment from them. Used by EquiBind for one-shot pose prediction.",
  "diffusion denoiser": "A model that starts from random noise and iteratively denoises it toward a valid bound pose, guided by the protein structure. The principle behind DiffDock.",
  "pose":           "The predicted 3D conformation of a ligand bound in the protein pocket (atom positions + orientation).",
  "plDDT":          "AlphaFold's per-residue confidence score (0–100). Higher = the model trusts its own coordinate prediction for that residue.",
  "pTM":            "AlphaFold's predicted Template Modeling score — a single number 0–1 estimating how close the predicted structure is to the true one. ipTM is the same but limited to interface residues.",
  "FAPE":           "Frame-Aligned Point Error — AlphaFold's loss function for atomic coordinates, computed in each residue's local frame so it's translation/rotation invariant.",
  "BPR":            "Bayesian Personalised Ranking loss. Trains the model to score positive (active) pairs higher than negative (inactive) ones.",
  "InfoNCE":        "Contrastive loss that pulls a positive pair together and pushes a batch of negatives apart in embedding space. The basis of contrastive learning (CLIP, ConPLex).",
  "triplet loss":   "Anchor + positive + negative training where the loss enforces a margin between the positive and negative distances.",
  "pocket cropper": "Pre-processing step that keeps only the protein atoms within N Å of the ligand, dropping the rest. Makes 3D models tractable on big proteins.",
  "conformer":      "A 3D structure sample of a molecule. Most flexible molecules have many conformers; you usually pick the lowest-energy one or sample several.",
  "Foldseek":       "A fast structural search tool that encodes 3D folds as 3Di letter sequences so you can search PDB-scale databases with BLAST-like speed.",
  "ConPLex":        "Two-tower DTI model from Singh 2023: ESM-2 protein tower + ChemBERTa ligand tower trained contrastively. Built for cold-target generalisation.",
  "SaProt":         "Protein language model that fuses sequence with structural 3Di tokens. Better than ESM on tasks where AF2-predicted structure helps.",
  "Ankh":           "Protein language model (Elnaggar 2023). T5-style architecture; strong sequence representation, smaller than ESM-2 at comparable quality.",
  "ProtBERT":       "Early protein language model (BERT trained on UniRef). Cheap baseline; outperformed by ESM-2 / Ankh on most modern tasks.",
  "ChemBERTa":      "BERT pretrained on chemical SMILES; produces molecular embeddings for downstream property prediction.",
  "Uni-Mol":        "Pretrained 3D molecular transformer (Zhou 2023). Operates on atom clouds rather than 2D graphs.",
  "dMaSIF":         "Differentiable molecular surface featuriser (Sverrisson 2021). Encodes the protein surface mesh into per-vertex features for interaction prediction.",
  "AlphaFold-Multimer": "AlphaFold-2 retrained on multimer assemblies (Evans 2022). The gold-standard for predicting protein-protein complexes.",
  "TankBind":       "A pose-and-affinity model (Lu 2022) that crops a pocket, runs triangle-update on a protein-pocket × ligand pair stack, and predicts both binding affinity and pose.",
  "EquiBind":       "An SE(3)-equivariant pose predictor (Stärk 2022). One-shot blind docking via keypoint matching, much faster than search-based docking.",
  "DiffDock":       "Diffusion-based blind docking (Corso 2023). Samples poses by iteratively denoising random ligand placements.",
  "PIPR":           "Sequence-only PPI classifier (Chen 2019). A siamese 1D-CNN per protein, concat + MLP for binary interaction prediction.",
  "retrieval / two-tower": "Architecture where two independent encoders embed both partners and the score is a cheap operation (dot, cosine) on their vectors. Scales to millions of candidates.",
  "interpolation":  "A study where the model is asked to predict for new pairs that resemble training pairs (e.g. a new ligand against a well-studied kinase). Random / scaffold splits are valid; numbers are higher but only apply to similar pairs.",
  "generalisation": "A study where the model has to work on proteins or chemotypes the training set never saw. Cold-target / cold-drug / time splits are required; numbers will look lower but they're real.",
};

// ─────────────────────────────────────────────────────────────────────
// Catalogues consumed by the Dataset screen's higher-level controls.
// ─────────────────────────────────────────────────────────────────────

// What partners are interacting in the pairs we train on. Multi-select:
// you can build a DTA-only dataset, a PPI-only dataset, or a combined one.
window.PS_BINDING_PARTNERS = [
  { id: "pl", label: "Protein – ligand",       short: "P–L",  sub: "small-molecule binding to a protein. Both affinity and interaction-only data is available." },
  { id: "pp", label: "Protein – protein",      short: "P–P",  sub: "two proteins binding each other. Mostly interaction-only; affinities (Kd / ΔΔG) for a smaller subset." },
  { id: "pna", label: "Protein – nucleic acid", short: "P–NA", sub: "protein – DNA or protein – RNA binding. Smaller corpus; mostly interaction-only." },
];

// What the model is being trained to predict. Multi-select; choosing more
// than one builds a multi-task dataset. These three are mutually orthogonal
// to the binding-partner axis.
window.PS_TASK_TYPES = [
  { id: "affinity",     label: "Affinity (regression)",        sub: "predict the binding strength as a continuous number (pKi / pKd / pIC50 / ΔG°). Needs labelled measurements." },
  { id: "interaction",  label: "Interaction (yes / no)",       sub: "predict whether the pair interacts at all. Boolean classification. Smaller label set but covers more sources." },
  { id: "unsupervised", label: "Self-supervised pretraining",  sub: "sequences + structures only, no labels. Used to pretrain encoders before downstream fine-tune." },
];

// How the clusterer merges pairs/proteins when multiple relationships fire.
// Crucially: with the default "union" merge, a chain like
//   A ~struct~ B, A1 = C's subunit, A2 = D's subunit, A1 ∈ E's Pfam family
// would pull A, B, C, D, E into one giant cluster via transitive closure.
// That mega-cluster usually swallows 30-60% of the dataset and makes the
// split useless. The other modes prevent that in different ways.
window.PS_MERGE_MODES = [
  { id: "union",      label: "Union (greedy transitive closure)",   short: "union",
    desc: "Default. If ANY active relationship links two pairs, they merge into one cluster. Simplest; most aggressive. Risks giant mega-clusters on dense data like PPI.",
    safe_for: ["pl"],     // sensible default for DTA
    warn_for: ["pp", "pna"],
    risk: "Mega-cluster prone",
  },
  { id: "per_subunit", label: "Per-subunit (cluster proteins, not pairs)", short: "per-subunit",
    desc: "Assign every PROTEIN to a cluster (by sequence / family / fold). A pair lands in the train/val/test split of one of its subunits — the smaller one wins, so a held-out protein truly tests cold-target generalisation. Recommended for PPI.",
    safe_for: ["pp", "pna"],
    warn_for: [],
    risk: "Clean, but doubles the protein-cluster bookkeeping",
  },
  { id: "strict_pair", label: "Strict pair-level only",                short: "strict-pair",
    desc: "Two pairs merge ONLY when the whole pair-vs-pair similarity (e.g. complex structural similarity) crosses the threshold. Shared subunits and shared Pfam are reported but don't merge. Smallest clusters; least aggressive — risks under-flagging leakage.",
    safe_for: ["pl", "pp"],
    warn_for: [],
    risk: "Under-flags leakage when one subunit is shared but the complex is novel",
  },
  { id: "score_weighted", label: "Score-weighted (advanced)",         short: "score",
    desc: "Each relationship contributes a leakage score; pairs merge only when the cumulative score crosses a threshold. Highest fidelity but harder to interpret. Score weights are configurable in Advanced → Eval & analytics.",
    safe_for: ["pl", "pp", "pna"],
    warn_for: [],
    risk: "Hardest to debug when clusters look wrong",
    tier: "beta",
  },
];

// What relationships between two pairs count as a "leak" for the splitter.
// Multi-select on the Splits screen. The clusterer unions every selected
// relationship — two pairs are merged into one leakage group iff they're
// related under ANY enabled relationship that crosses its threshold.
//
//   integrated — wired into the warehouse signatures index; selectable now.
//   planned    — agreed scope, ingestion pending; visible but disabled.
window.PS_CLUSTER_RELATIONSHIPS = [
  // ── Protein side ──────────────────────────────────────────────────
  { id: "seq_identity", label: "Sequence identity (MMseqs2)",     side: "protein", status: "integrated",
    desc: "Two proteins cluster if their MMseqs2 sequence identity ≥ threshold. The standard leakage signal.",
    defaultOn: true, defaultThreshold: 0.30, thresholdRange: [0.20, 0.95], thresholdStep: 0.05,
    tier: "release",
  },
  { id: "foldseek_3d", label: "Structural similarity (Foldseek 3Di)", side: "protein", status: "integrated",
    desc: "Two proteins cluster if their 3D fold matches under Foldseek's 3Di alphabet (TM-score ≥ threshold). Catches remote homologs that sequence misses.",
    defaultOn: false, defaultThreshold: 0.50, thresholdRange: [0.30, 0.95], thresholdStep: 0.05,
    tier: "release",
  },
  { id: "pfam_family",  label: "Same Pfam family (HMM)",          side: "protein", status: "integrated",
    desc: "Two proteins cluster if they share at least one Pfam domain. Catches family-level transfer.",
    defaultOn: false, defaultThreshold: null,
    tier: "release",
  },
  { id: "ortholog",     label: "Orthologs (OrthoDB)",             side: "protein", status: "planned",
    desc: "Cluster orthologous pairs across species (e.g. human BTK + mouse BTK). Prevents cross-species leakage in cold-target campaigns.",
    defaultOn: false, defaultThreshold: null,
    tier: "beta",
  },
  { id: "ec_class",     label: "Same EC class (enzymes)",         side: "protein", status: "planned",
    desc: "Group enzymes by Enzyme Commission number (3-digit). Functional-similarity proxy for enzyme datasets.",
    defaultOn: false, defaultThreshold: null,
    tier: "beta",
  },
  { id: "pocket_signature", label: "Conserved active site",       side: "protein", status: "planned",
    desc: "Cluster proteins by binding-pocket geometry (PocketBLAST signature). Catches scaffold-hopping leakage when sequence/Pfam differ but pocket is conserved.",
    defaultOn: false, defaultThreshold: null,
    tier: "beta",
  },
  // ── Ligand side ───────────────────────────────────────────────────
  { id: "ecfp_tanimoto", label: "ECFP4 Tanimoto",                 side: "ligand",  status: "integrated",
    desc: "Two ligands cluster if their Morgan-r2 (ECFP4) Tanimoto ≥ threshold. The standard ligand-side leakage signal.",
    defaultOn: true, defaultThreshold: 0.40, thresholdRange: [0.20, 0.95], thresholdStep: 0.05,
    tier: "release",
  },
  { id: "bemis_murcko",  label: "Bemis–Murcko scaffold",           side: "ligand", status: "integrated",
    desc: "Two ligands cluster if they share the same Bemis–Murcko core scaffold. Strict; useful as a hard scaffold split.",
    defaultOn: false, defaultThreshold: null,
    tier: "release",
  },
  { id: "mcs_overlap",   label: "Maximum common substructure",     side: "ligand", status: "planned",
    desc: "Cluster ligands with a large MCS overlap (≥ N atoms). Catches series of close analogs that fingerprints can miss.",
    defaultOn: false, defaultThreshold: null,
    tier: "beta",
  },
  { id: "rdkit_motif",   label: "Functional motif (RDKit SMARTS)", side: "ligand", status: "planned",
    desc: "Cluster ligands sharing a curated functional motif (e.g. covalent warhead, kinase hinge binder).",
    defaultOn: false, defaultThreshold: null,
    tier: "beta",
  },
];

// The single most important Splits-screen decision — what kind of question
// is this model being trained to answer? It dictates which split policy is
// honest and propagates a tone all the way through to Results / Compare.
//
//  interpolation  — fill in gaps in a well-covered space. e.g. predicting
//                   affinity for a new molecule against a heavily-studied
//                   kinase. Random / scaffold splits are valid. Numbers
//                   look high; generalisation beyond similar pairs is NOT
//                   tested.
//  generalization — predict for genuinely novel proteins or chemotypes.
//                   Cold-target / cold-drug / cold-pair / time splits are
//                   required. Numbers look lower but ARE indicative.
window.PS_DESIGN_OBJECTIVES = [
  { id: "generalization", label: "Generalisation (honest)",     short: "extrapolation",
    sub: "the model has to work on proteins or chemotypes the training set never saw. Required for new-target campaigns; numbers will look lower but they're real.",
    tone: "warn",
    recommendedPolicies: ["cluster", "cold-target", "cold-drug", "cold-pair", "time-split"],
    bannerTitle: "Generalisation study", bannerSub: "Metrics measured on held-out proteins / chemotypes / time bins — extrapolation context.",
  },
  { id: "interpolation",  label: "Interpolation (within known space)", short: "interpolation",
    sub: "the model fills gaps in a well-covered space — e.g. ranking new compounds against a heavily-studied kinase. Random / scaffold splits are fine here; numbers will look higher but only apply to similar pairs.",
    tone: "primary",
    recommendedPolicies: ["random", "scaffold"],
    bannerTitle: "Interpolation study", bannerSub: "Metrics measured on chemically-similar held-out pairs — only valid for new pairs that resemble the training distribution.",
  },
];

// ── Binding types (v4 — Goal screen) ─────────────────────────────────
// One decision drives the rest of the workflow. Picking a binding type:
//   - filters the Dataset screen's source picker
//   - names the Splits header + chooses the cluster axis
//   - filters the Pipeline preset gallery
//   - constrains the Features screen's compatibility checks
// ``status`` ∈ {available, partial, needs_ingest}; ``tier`` follows
// PS_TIERS. The bound-item counts are approximate (refreshed nightly
// from the warehouse).
window.PS_BINDING_TYPES = [
  {
    id: "pl_simple",
    label: "Protein + small molecule",
    what: "One protein, one ligand",
    desc: "Drug–target affinity (DTA), drug repurposing, and the bulk of medicinal-chemistry workloads.",
    use_case: "DTA / drug repurposing",
    items: 165318,
    unique: { proteins: 31204, ligands: 482910, complexes: null },
    sources: [
      { id: "davis",    label: "Davis",            n: 30000 },
      { id: "kiba",     label: "KIBA",             n: 118000 },
      { id: "gtopdb",   label: "Guide to Pharm.",  n: 3000 },
      { id: "pdbbind",  label: "PDBbind",          n: 15000 },
    ],
    labels: "regression + binary",
    coverage: { sequence: "full", structure: "partial", rosetta: "thin", pathway: "partial" },
    coverage_note: "62% of proteins have an AlphaFold structure cached. Rosetta REU available on Linux nodes only.",
    status: "available",
    icon: "molecule",
    tier: "release",
  },
  {
    id: "pl_cofactor",
    label: "Protein + ligand + cofactor",
    what: "One protein, ligand, and a cofactor (Mg²⁺, ATP, …)",
    desc: "Metalloenzymes, ATP-binders, redox enzymes — affinity depends on the cofactor as much as the ligand.",
    use_case: "Metalloenzymes, ATP-binders",
    items: 4182,
    unique: { proteins: 1204, ligands: 2840, complexes: 4182 },
    sources: [
      { id: "pdbbind_cof", label: "PDBbind cofactor subset", n: 4182 },
    ],
    labels: "regression",
    coverage: { sequence: "full", structure: "full", rosetta: "thin", pathway: "none" },
    coverage_note: "All entries have crystal structures. Rosetta REU Linux-only.",
    status: "available",
    icon: "atom",
    tier: "beta",
  },
  {
    id: "pp_binary",
    label: "Protein × protein (binary)",
    what: "Two proteins, label = interacts y/n",
    desc: "Interactome filling. Per-edge prediction; doesn't try to score affinity.",
    use_case: "Interactome filling",
    items: 1052000,
    unique: { proteins: 92104, ligands: null, complexes: null },
    sources: [
      { id: "hippie", label: "HIPPIE", n: 1000000 },
      { id: "huri",   label: "HuRI",   n: 52000 },
    ],
    labels: "binary",
    coverage: { sequence: "full", structure: "partial", rosetta: "thin", pathway: "partial" },
    coverage_note: "Sequences are universal. ~38% of pairs have a co-folded predicted structure.",
    status: "available",
    icon: "link",
    tier: "release",
  },
  {
    id: "pp_affinity",
    label: "Protein × protein affinity",
    what: "Two proteins, label = Kd / ΔG",
    desc: "Binding-affinity prediction for protein pairs. The right tool for binder design and de novo optimization.",
    use_case: "Binding optimization",
    items: null,
    unique: { proteins: null, ligands: null, complexes: null },
    sources: [
      { id: "skempi", label: "SKEMPI 2.0", n: 7085 },
    ],
    labels: "regression",
    coverage: { sequence: "full", structure: "partial", rosetta: "thin", pathway: "none" },
    coverage_note: "Needs SKEMPI ingest. Ingestion ETL is drafted; not yet running.",
    status: "needs_ingest",
    needs: "Ingest SKEMPI 2.0 to the warehouse. Owner: data@anvil. ETA: warehouse v2026.06.",
    icon: "link",
    tier: "beta_soon",
  },
  {
    id: "ab_ag",
    label: "Antibody × antigen",
    what: "Antibody chains + antigen",
    desc: "Therapeutic-antibody affinity and epitope prediction. Distinct enough from PPI that it gets its own front door.",
    use_case: "Therapeutic Ab design",
    items: null,
    unique: { proteins: null, ligands: null, complexes: null },
    sources: [
      { id: "sabdab", label: "SAbDab", n: 9120 },
    ],
    labels: "regression + binary",
    coverage: { sequence: "full", structure: "partial", rosetta: "thin", pathway: "none" },
    coverage_note: "Needs SAbDab ingest. ETA: warehouse v2026.06.",
    status: "needs_ingest",
    needs: "Ingest SAbDab + standardize antibody numbering (Kabat / Chothia).",
    icon: "ab",
    tier: "beta_soon",
  },
  {
    id: "complex_l",
    label: "Protein complex + ligand",
    what: "≥ 2 chains + ligand",
    desc: "Allosteric ligands, complex modulators, drugs that bind oligomeric proteins. Cofactor cards subset this.",
    use_case: "Allosteric, complex drugs",
    items: 6804,
    unique: { proteins: null, ligands: 4120, complexes: 6804 },
    sources: [
      { id: "pdbbind_multi", label: "PDBbind multi-chain subset", n: 6804 },
    ],
    labels: "regression",
    coverage: { sequence: "full", structure: "full", rosetta: "thin", pathway: "none" },
    coverage_note: "All multi-chain entries are structure-resolved.",
    status: "available",
    icon: "cluster",
    tier: "lab",
  },
  {
    id: "signaling",
    label: "Signalling-pathway pair",
    what: "Two proteins + pathway context",
    desc: "Network biology — predict whether two proteins act together in a known pathway. Uses HIPPIE + Reactome.",
    use_case: "Network biology",
    items: null,
    unique: { proteins: null, ligands: null, complexes: null },
    sources: [
      { id: "hippie",   label: "HIPPIE",   n: 1000000 },
      { id: "reactome", label: "Reactome", n: 12104 },
    ],
    labels: "binary",
    coverage: { sequence: "full", structure: "partial", rosetta: "none", pathway: "partial" },
    coverage_note: "HIPPIE is in; Reactome ingest is at 60%. We can ship without pathway features but the screen calls it out.",
    status: "partial",
    needs: "Finish Reactome ingest. Owner: data@anvil.",
    icon: "graph",
    tier: "beta_soon",
  },
];


// ── Feature catalogue (v4 — Features screen) ─────────────────────────
// Three axes: protein-side / ligand-side / interaction. Each row
// describes a featurizer the trainer can compute per training example.
// ``status`` ∈ {integrated, planned, needs_cache, platform_limited}
//   - integrated     → green chip, selectable
//   - planned        → warn chip, row disabled
//   - needs_cache    → warn chip, selectable; cache pass auto-queues at launch
//   - platform_limited → error chip, disabled with platform reason
// ``kind`` matches the .feat-badge variants in styles.css.
// ``preview`` is optional metadata for the per-row preview modal.
window.PS_FEATURES = {
  protein: [
    { id: "aa_comp",        kind: "tabular",   label: "AA composition",
      desc: "Twenty floats — frequency of each amino acid.",
      shape: "20-d vector / protein",
      status: "integrated", cost: "trivial",   default: true },
    { id: "esm2_650m",      kind: "embedding", label: "ESM-2 embedding (650M, frozen)",
      desc: "Per-token output from a 650M-param protein language model.",
      shape: "1280-d vector / protein",
      status: "integrated", cost: "moderate",  default: true,
      preview: { kind: "heatmap", title: "ESM-2 → 8 PCA dims for BTK[380..470]" } },
    // Fake-setta — Python-only ref2015-style 19-d score. Always works.
    // The id matches the backend featurizer id (protein_fakesetta) so
    // selection round-trips correctly between GUI and trainer.
    { id: "fakesetta",      kind: "tabular",   label: "Fake-setta (ref2015 surrogate)",
      desc: "19-d Python approximation of Rosetta ref2015 (fa_atr/rep/sol/elec + H-bonds + omega + ref + rama). NOT real Rosetta — same field names so the downstream model swaps to real Rosetta seamlessly when installed.",
      shape: "19-d vector / protein",
      status: "integrated", cost: "moderate" },
    // Real Rosetta REU — license-gated. Requires PyRosetta wheel or
    // Rosetta C++ binary AND ROSETTA_LICENSE_ACKNOWLEDGED=1.
    { id: "rosetta_reu",    kind: "tabular",   label: "Rosetta REU (real ref2015 — license-gated)",
      desc: "Genuine 19-d Rosetta ref2015 score. Requires a valid Rosetta Commons academic license + local install + license acknowledgement. Falls back to fake-setta when unavailable.",
      shape: "19-d vector / protein",
      status: "platform_limited", platform: "linux+license", cost: "heavy",
      reason: "Install PyRosetta locally per the Rosetta Commons Academic License, then set ROSETTA_LICENSE_ACKNOWLEDGED=1 in the environment." },
    { id: "reactome_path",  kind: "tabular",   label: "Reactome pathway one-hot",
      desc: "1 if the protein is annotated in pathway p, else 0.",
      shape: "2480-d sparse / protein",
      status: "needs_cache", cost: "fast",
      reason: "Reactome ingest queued — see Reactome featurizer in /api/v2/featurizers." },
    { id: "res_contact",    kind: "graph",     label: "Residue contact graph (≤ 8 Å)",
      desc: "Nodes = residues, edges = within 8 Å in 3D. Edge attributes carry distance.",
      shape: "graph: 23-d nodes, ≈ 6N edges",
      status: "integrated", cost: "moderate",  default: true,
      preview: { kind: "graph", title: "Residue contact graph — BTK kinase domain" } },
    { id: "atom_graph",     kind: "graph",     label: "Atom graph",
      desc: "All non-H atoms as nodes, covalent bonds as edges. Needed for atom-level GNNs.",
      shape: "graph: 7-d nodes, 3-d edges",
      status: "integrated", cost: "moderate" },
    { id: "dssp_ss",        kind: "tabular",   label: "Secondary structure (DSSP-lite)",
      desc: "One-hot of α / β / coil per residue.",
      shape: "3-d per residue",
      status: "integrated", cost: "trivial" },
  ],
  ligand: [
    { id: "ecfp4",          kind: "tabular",   label: "ECFP4 fingerprint",
      desc: "Extended-connectivity fingerprint, radius 2, folded to 2048 bits.",
      shape: "2048-d bit vector",
      status: "integrated", cost: "trivial",   default: true,
      preview: { kind: "bitmap", title: "ECFP4 — imatinib" } },
    { id: "maccs",          kind: "tabular",   label: "MACCS keys",
      desc: "166-bit predefined fingerprint covering common substructures.",
      shape: "166-d bit vector",
      status: "integrated", cost: "trivial" },
    { id: "chemberta",      kind: "embedding", label: "ChemBERTa embedding",
      desc: "Per-token output from a SMILES-pretrained transformer.",
      shape: "384-d vector",
      status: "integrated", cost: "moderate" },
    { id: "mol_graph_2d",   kind: "graph",     label: "2D molecular graph",
      desc: "Atoms as nodes, bonds as edges. The right input for GIN / GCN encoders.",
      shape: "graph: 9-d nodes, 3-d edges",
      status: "integrated", cost: "fast",      default: true,
      preview: { kind: "graph", title: "Mol graph — imatinib" } },
    { id: "unimol_3d",      kind: "embedding", label: "3D conformer + Uni-Mol",
      desc: "Conformer-aware embedding from Uni-Mol v2. Needs an ETKDG conformer first.",
      shape: "512-d vector",
      status: "integrated", cost: "moderate" },
    { id: "physchem",       kind: "tabular",   label: "Physchem descriptors",
      desc: "RDKit descriptors — logP, TPSA, HBA/HBD, rotatable bonds, QED, …",
      shape: "78-d vector",
      status: "integrated", cost: "trivial" },
  ],
  interaction: [
    { id: "iface_residues", kind: "tabular",   label: "Interface residue pairs",
      desc: "Pairs of residues within 5 Å across the protein–ligand interface.",
      shape: "variable-length list of (i, j)",
      status: "needs_cache", cost: "fast",
      reason: "Needs a PDB-resolved or AlphaFold-multimer structure." },
    { id: "hot_spots",      kind: "tabular",   label: "Hot-spot probability",
      desc: "Per-residue probability of being an interaction hot spot. From a separate ΔΔG model.",
      shape: "1-d per residue",
      status: "planned", cost: "moderate",
      reason: "Hot-spot predictor in development." },
    { id: "pose_contact",   kind: "map",       label: "Pose contact map",
      desc: "Pairwise residue–atom contact map from a docking pose.",
      shape: "L × L map",
      status: "needs_cache", cost: "heavy",
      reason: "Needs a docking pass first." },
  ],
};

// ── Flow-builder catalogues (v4 — Pipeline flow-builder mode) ────────
// Three new globals that drive the LabVIEW-style flow editor:
//   * PS_FLOW_PORT_TYPES — wire-type color contract (sequence/graph/…)
//   * PS_FLOW_BLOCKS     — roles × implementations by category
//   * PS_FLOW_BLOCK_INDEX — { block_id → block_def } lookup for the canvas
//   * PS_FLOW_PRESETS    — six curated starting compositions
//
// The flow-builder is additive — the existing template-first
// screen-pipeline.jsx still works untouched. A small mode toggle on
// the Pipeline screen flips between them.

window.PS_FLOW_PORT_TYPES = {
  sequence:  { label: "Sequence",        desc: "Tokens — protein AA or SMILES",          color: "var(--port-sequence)" },
  graph:     { label: "Graph",           desc: "Nodes + edges — mol or contact graph",   color: "var(--port-graph)" },
  embedding: { label: "Embedding",       desc: "Fixed-length learned vector",            color: "var(--port-embedding)" },
  map:       { label: "Map",             desc: "L × L pairwise representation",          color: "var(--port-map)" },
  scalar:    { label: "Scalar",          desc: "Predicted number(s)",                    color: "var(--port-scalar)" },
  pose:      { label: "Pose",            desc: "3-D coordinates of a binding pose",      color: "var(--port-pose)" },
  prob:      { label: "Probability",     desc: "0..1 — classifier output",                color: "var(--port-prob)" },
};

window.PS_FLOW_BLOCKS = {
  inputs: [
    { id: "in.protein_seq",     cat: "input", role: "Protein sequence",   feature_id: "esm2_650m",
      inputs: [], outputs: [{ port: "out", type: "sequence" }],
      impls: [{ id: "default", label: "Tokens", desc: "Direct from the dataset — one row per residue.", cost: 0.0, params: [] }] },
    { id: "in.protein_graph",   cat: "input", role: "Protein residue graph", feature_id: "res_contact",
      inputs: [], outputs: [{ port: "out", type: "graph" }],
      impls: [{ id: "default", label: "Contact ≤ 8 Å", desc: "Residue contact graph — nodes are residues, edges are pairs within 8 Å in 3-D.", cost: 0.0, params: [] }] },
    { id: "in.protein_emb",     cat: "input", role: "Protein ESM-2 embedding", feature_id: "esm2_650m",
      inputs: [], outputs: [{ port: "out", type: "embedding" }],
      impls: [{ id: "default", label: "Cached ESM-2", desc: "1280-d / token — cached embedding. Free per epoch; pays off once you train ≥ 2 architectures on the same proteins.", cost: 0.0, params: [] }] },
    { id: "in.protein_fakesetta", cat: "input", role: "Fake-setta REU vector", feature_id: "fakesetta", planned: true,
      inputs: [], outputs: [{ port: "out", type: "embedding" }],
      impls: [{ id: "default", label: "19-d ref2015-style", desc: "Python surrogate for Rosetta ref2015. Swaps to real Rosetta automatically when locally licensed.", cost: 0.0, planned: true, params: [] }] },
    { id: "in.ligand_graph",    cat: "input", role: "Ligand mol-graph",   feature_id: "mol_graph_2d",
      inputs: [], outputs: [{ port: "out", type: "graph" }],
      impls: [{ id: "default", label: "RDKit 2-D", desc: "Atoms + bonds. Stereo + aromaticity flags included.", cost: 0.0, params: [] }] },
    { id: "in.ligand_fp",       cat: "input", role: "Ligand ECFP4 fingerprint", feature_id: "ecfp4",
      inputs: [], outputs: [{ port: "out", type: "embedding" }],
      impls: [{ id: "default", label: "ECFP4 2048-bit", desc: "Standard cheminformatic fingerprint. Deterministic, no GPU.", cost: 0.0, params: [] }] },
    { id: "in.ligand_unimol",   cat: "input", role: "Ligand 3-D conformer (Uni-Mol)", feature_id: "unimol_3d", planned: true,
      inputs: [], outputs: [{ port: "out", type: "embedding" }],
      impls: [{ id: "default", label: "Uni-Mol 512-d", desc: "Conformer-aware embedding from Uni-Mol v2.", cost: 0.0, planned: true, params: [] }] },
    { id: "in.ligand_physchem", cat: "input", role: "Ligand physchem descriptors", feature_id: "physchem", planned: true,
      inputs: [], outputs: [{ port: "out", type: "embedding" }],
      impls: [{ id: "default", label: "RDKit descriptors", desc: "78-d vector — logP, TPSA, HBA/HBD, QED, rotatable bonds, …", cost: 0.0, planned: true, params: [] }] },
    { id: "in.contact_map",     cat: "input", role: "Interaction contact map", feature_id: "pose_contact",
      inputs: [], outputs: [{ port: "out", type: "map" }],
      impls: [{ id: "default", label: "Docking pose map", desc: "Needs a docking pass first; produces an L × L pairwise contact map.", cost: 0.0, params: [] }] },
    { id: "in.iface_pairs",     cat: "input", role: "Interface residue pairs", feature_id: "iface_residues", planned: true,
      inputs: [], outputs: [{ port: "out", type: "embedding" }],
      impls: [{ id: "default", label: "5 Å pair list", desc: "Pairs of residues within 5 Å across the interface. Needs a resolved or AF-multimer structure.", cost: 0.0, planned: true, params: [] }] },
  ],
  encoders: [
    { id: "enc.protein_seq",    cat: "encoder", role: "ProteinSequenceEncoder",
      inputs: [{ port: "in", types: ["sequence"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "cnn",         label: "CNN-1D",        desc: "1-D convolutions over AA tokens. Cheap, robust, DeepDTA-style.",
          paper: "DeepDTA · 2018", cost: 0.4,
          params: [{ key: "filters", kind: "int", default: 128 }, { key: "kernel", kind: "int", default: 8 }, { key: "layers", kind: "int", default: 3 }, { key: "dropout", kind: "float", default: 0.1 }, { key: "pool", kind: "enum", default: "mean", options: ["mean", "max", "attention"] }] },
        { id: "transformer", label: "Transformer",   desc: "Trainable encoder, no pretraining. Better on large datasets.",
          paper: "Vaswani · 2017", cost: 0.8,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "heads", kind: "int", default: 4 }, { key: "layers", kind: "int", default: 4 }, { key: "dropout", kind: "float", default: 0.1 }, { key: "max_len", kind: "int", default: 1024 }] },
        { id: "esm2_frozen", label: "ESM-2 (frozen)", desc: "Pretrained protein LM as a feature extractor. No fine-tune cost.",
          paper: "Lin et al · 2023", cost: 0.1,
          params: [{ key: "checkpoint", kind: "enum", default: "esm2_t33_650M", options: ["esm2_t12_35M", "esm2_t30_150M", "esm2_t33_650M", "esm2_t36_3B"] }, { key: "freeze", kind: "bool", default: true }, { key: "pool", kind: "enum", default: "mean", options: ["mean", "cls", "attention"] }, { key: "use_cache", kind: "bool", default: true }] },
        { id: "protbert",    label: "ProtBert (frozen)", desc: "BERT-style protein LM trained on UniRef100. Smaller VRAM footprint than ESM-2. Reads from the same in.protein_emb cache as ESM-2 — wire the cache, not raw tokens.",
          paper: "Elnaggar · 2021", cost: 0.2,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "prott5",      label: "ProtT5-XL (frozen)", desc: "T5 encoder pretrained on BFD. Strong on remote-homolog transfer.",
          paper: "Elnaggar · 2022", cost: 0.3, planned: true,
          params: [{ key: "freeze", kind: "bool", default: true }] },
        { id: "lstm_bi",     label: "BiLSTM",        desc: "Bidirectional LSTM over AA tokens. The pre-transformer baseline.",
          paper: "—", cost: 0.4,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "layers", kind: "int", default: 2 }, { key: "dropout", kind: "float", default: 0.2 }, { key: "embed_dim", kind: "int", default: 256 }] },
        { id: "identity",    label: "Identity",       desc: "Pass-through. Use when input is already an embedding.", paper: "—", cost: 0.0, params: [] },
      ] },
    { id: "enc.protein_graph",  cat: "encoder", role: "ProteinGraphEncoder",
      inputs: [{ port: "in", types: ["graph"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "gcn", label: "GCN",   desc: "Vanilla graph convolutions over the residue contact graph.",
          paper: "Kipf & Welling · 2017", cost: 0.4, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 3 }, { key: "dropout", kind: "float", default: 0.1 }, { key: "pool", kind: "enum", default: "mean", options: ["mean", "max", "sum", "attention"] }] },
        { id: "gin", label: "GIN",   desc: "Graph isomorphism network. Stronger expressivity than GCN.",
          paper: "Xu et al · 2019",     cost: 0.5, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 4 }, { key: "eps_trainable", kind: "bool", default: true }, { key: "pool", kind: "enum", default: "sum", options: ["mean", "max", "sum"] }] },
        { id: "gat", label: "GAT",   desc: "Graph attention. Helps when only some neighbours matter.",
          paper: "Veličković · 2018",   cost: 0.6, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "heads", kind: "int", default: 4 }, { key: "layers", kind: "int", default: 3 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "egnn", label: "E(n)-GNN", desc: "Equivariant GNN — respects 3-D rotations/translations of the structure.",
          paper: "Satorras · 2021", cost: 0.7, planned: true,
          params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 4 }] },
        { id: "schnet", label: "SchNet", desc: "Distance-aware continuous-filter convolutions. Good when bond geometry matters.",
          paper: "Schütt · 2018", cost: 0.6, planned: true,
          params: [{ key: "hidden", kind: "int", default: 128 }, { key: "n_filters", kind: "int", default: 128 }, { key: "n_gaussians", kind: "int", default: 50 }] },
        { id: "identity", label: "Identity", desc: "Pass-through.", paper: "—", cost: 0.0, params: [] },
      ] },
    { id: "enc.ligand_seq",     cat: "encoder", role: "LigandSequenceEncoder",
      inputs: [{ port: "in", types: ["sequence"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "smiles_cnn",  label: "SMILES-CNN",  desc: "1-D conv over SMILES tokens. The DeepDTA ligand side.",
          paper: "DeepDTA · 2018",  cost: 0.3, params: [{ key: "filters", kind: "int", default: 64 }, { key: "kernel", kind: "int", default: 5 }] },
        { id: "chemberta",   label: "ChemBERTa",   desc: "SMILES-pretrained transformer; frozen by default.",
          paper: "Chithrananda · 2020", cost: 0.5, params: [{ key: "freeze", kind: "bool", default: true }] },
        { id: "molformer",   label: "MolFormer",   desc: "Larger SMILES LM, stronger transfer.",
          paper: "Ross et al · 2022",  cost: 0.7, params: [{ key: "freeze", kind: "bool", default: true }] },
        { id: "identity",    label: "Identity",    desc: "Pass-through.", paper: "—", cost: 0.0, params: [] },
      ] },
    { id: "enc.ligand_graph",   cat: "encoder", role: "LigandGraphEncoder",
      inputs: [{ port: "in", types: ["graph"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "gin", label: "GIN", desc: "Strongest GNN baseline on molecular property tasks.",
          paper: "Xu et al · 2019", cost: 0.5, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 5 }] },
        { id: "gcn", label: "GCN", desc: "Cheaper baseline.", paper: "Kipf & Welling · 2017",
          cost: 0.4, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 3 }] },
        { id: "gat", label: "GAT", desc: "Per-edge attention.", paper: "Veličković · 2018",
          cost: 0.6, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "heads", kind: "int", default: 4 }] },
        { id: "identity", label: "Identity", desc: "Pass-through.", paper: "—", cost: 0.0, params: [] },
      ] },
    { id: "enc.tabular",        cat: "encoder", role: "TabularEncoder",
      inputs: [{ port: "in", types: ["embedding"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "mlp",      label: "MLP",      desc: "2-3 dense layers, ReLU. The default for tabular features.",
          paper: "—",      cost: 0.2, params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 2 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "xgboost",  label: "XGBoost",  desc: "Gradient-boosted trees. Strong out-of-the-box; no fine-tuning required.",
          paper: "Chen & Guestrin · 2016", cost: 0.3, params: [{ key: "n_estimators", kind: "int", default: 400 }, { key: "max_depth", kind: "int", default: 6 }, { key: "lr", kind: "float", default: 0.1 }] },
        { id: "catboost", label: "CatBoost", desc: "Handles categorical features natively.",
          paper: "Dorogush · 2018", cost: 0.3, params: [{ key: "iterations", kind: "int", default: 600 }, { key: "depth", kind: "int", default: 6 }] },
        { id: "identity", label: "Identity", desc: "Pass-through.", paper: "—", cost: 0.0, params: [] },
      ] },
    { id: "enc.interaction_map", cat: "encoder", role: "InteractionMapEncoder",
      inputs: [{ port: "in", types: ["map"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "cnn2d",     label: "CNN-2D",         desc: "2-D conv over the L × L map.", paper: "—", cost: 0.5, params: [{ key: "filters", kind: "int", default: 64 }] },
        { id: "triangle",  label: "TriangleUpdate", desc: "Evoformer block — refines pair representation by triangle inequality.",
          paper: "Jumper · 2021", cost: 0.9, params: [{ key: "iters", kind: "int", default: 3 }] },
        { id: "identity",  label: "Identity",       desc: "Pass-through.", paper: "—", cost: 0.0, params: [] },
      ] },
  ],
  fusion: [
    // Variable-arity fusion. Most impls take any N ≥ 1 inputs (concat,
    // weighted_mean, attention_pool, gated_sum, tabular_xgb); the
    // canonically binary impls (bilinear, cross_attn, two_tower_dot)
    // reject N ≠ 2 with a clear compile error. The block exposes 4 input
    // ports — wire only the ones you need; unconnected ports are ignored.
    { id: "fuse",             cat: "fusion",  role: "Fusion",
      inputs: [
        { port: "a", types: ["embedding"] },
        { port: "b", types: ["embedding"] },
        { port: "c", types: ["embedding"], optional: true },
        { port: "d", types: ["embedding"], optional: true },
      ],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "concat_mlp",    label: "Concat + MLP",          desc: "Concatenate every inbound embedding (any N) and pass through an MLP. The simplest variadic fusion.",
          paper: "—",                cost: 0.3, arity: "any",
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "layers", kind: "int", default: 2 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "weighted_mean", label: "Weighted mean",         desc: "Project each input to a shared dim, softmax-weight, then average. Naturally N-input. Cheap.",
          paper: "—",                cost: 0.2, arity: "any",
          params: [{ key: "hidden", kind: "int", default: 256 }] },
        { id: "attention_pool",label: "Attention pool",        desc: "Treat the N inputs as a length-N token set; self-attention mixes them. Generalises cross-attn to any N.",
          paper: "—",                cost: 0.6, arity: "any",
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "heads", kind: "int", default: 4 }, { key: "layers", kind: "int", default: 1 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "gated_sum",     label: "Gated sum",             desc: "Per-input sigmoid gate (input-dependent), projected sum. Learns to ignore an input on a per-example basis.",
          paper: "—",                cost: 0.3, arity: "any",
          params: [{ key: "hidden", kind: "int", default: 256 }] },
        { id: "bilinear",      label: "Bilinear attention",    desc: "Bilinear interaction with learned attention pooling — DrugBAN. Two inputs only.",
          paper: "Bai et al · 2023", cost: 0.5, arity: 2,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "heads", kind: "int", default: 4 }] },
        { id: "cross_attn",    label: "Cross-attention (a↔b)", desc: "Symmetric cross-attention over the two representations. Two inputs only; use attention_pool for N inputs.",
          paper: "—",                cost: 0.7, arity: 2,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "heads", kind: "int", default: 4 }, { key: "layers", kind: "int", default: 2 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "two_tower_dot", label: "Two-tower dot product", desc: "Independent towers, dot product to score. Cheap at serve time. Two inputs only.",
          paper: "ConPLex · 2023",   cost: 0.2, arity: 2,
          params: [{ key: "proj_dim", kind: "int", default: 256 }, { key: "temperature", kind: "float", default: 0.07 }, { key: "normalize", kind: "bool", default: true }] },
        { id: "joint_mp",      label: "Joint message-passing", desc: "Shared message-passing over the union graph. Heavy. (Planned: backend builder ships later stage.)",
          paper: "—",                cost: 0.8, planned: true, arity: 2,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "layers", kind: "int", default: 4 }] },
        { id: "tabular_xgb",   label: "XGBoost on concat",     desc: "Concatenate every inbound embedding and feed to XGBoost. Pair with head.regression/xgboost for the hybrid fit.",
          paper: "—",                cost: 0.3, arity: "any",
          params: [{ key: "n_estimators", kind: "int", default: 800 }, { key: "max_depth", kind: "int", default: 8 }] },
      ] },
  ],
  head: [
    { id: "head.regression",  cat: "head", role: "Regression",
      inputs: [{ port: "in", types: ["embedding"] }],
      outputs: [{ port: "out", type: "scalar" }],
      impls: [
        { id: "pki",   label: "pKi",   desc: "Predict pKi. Higher = stronger binding.", paper: "—", cost: 0.1, params: [{ key: "loss", kind: "enum", default: "huber", options: ["mse", "huber", "smooth_l1"] }] },
        { id: "pkd",   label: "pKd",   desc: "Predict pKd.",                            paper: "—", cost: 0.1, params: [{ key: "loss", kind: "enum", default: "mse",   options: ["mse", "huber"] }] },
        { id: "pic50", label: "pIC50", desc: "Predict pIC50 — assay-condition dependent.", paper: "—", cost: 0.1, params: [{ key: "loss", kind: "enum", default: "huber", options: ["mse", "huber"] }] },
        { id: "kd",    label: "Kd",    desc: "Predict Kd directly (nM). Use a log-target if range is wide.", paper: "—", cost: 0.1, params: [{ key: "log_target", kind: "bool", default: true }] },
        { id: "dg",    label: "ΔG",    desc: "Thermodynamic readout.", paper: "—", cost: 0.1, params: [] },
      ] },
    { id: "head.classifier",  cat: "head", role: "Binary classifier",
      inputs: [{ port: "in", types: ["embedding"] }],
      outputs: [{ port: "out", type: "prob" }],
      impls: [
        { id: "default", label: "Sigmoid",
          desc: "Single output, BCE loss. The standard binary-interaction head.",
          paper: "—", cost: 0.1,
          params: [{ key: "loss", kind: "enum", default: "bce", options: ["bce", "focal"] }, { key: "pos_weight", kind: "float", default: 1.0 }, { key: "dropout", kind: "float", default: 0.1 }] },
        { id: "calibrated", label: "Platt-calibrated sigmoid",
          desc: "Sigmoid + post-hoc Platt scaling. After torch training, the trainer fits 1-D logistic regression on val-set (logit, label) pairs and stashes σ(a·logit + b) calibration scalars in run.summary.platt_a / .platt_b.",
          paper: "Platt · 1999", cost: 0.1,
          params: [{ key: "hidden", kind: "int", default: 256 }, { key: "layers", kind: "int", default: 3 }, { key: "dropout", kind: "float", default: 0.2 }] },
      ] },
    { id: "head.multiclass", cat: "head", role: "Multi-class classifier",
      inputs: [{ port: "in", types: ["embedding"] }],
      outputs: [{ port: "out", type: "prob" }],
      impls: [
        { id: "softmax", label: "Softmax",
          desc: "K-way softmax + cross-entropy. Use when the label is a category (e.g. binder type, MoA class). Requires a categorical-target loader — surfaces a clear error until that loader ships.",
          paper: "—", cost: 0.1, planned: true,
          params: [{ key: "num_classes", kind: "int", default: 3 }, { key: "label_smoothing", kind: "float", default: 0.0 }, { key: "hidden", kind: "int", default: 256 }, { key: "dropout", kind: "float", default: 0.1 }] },
      ] },
    { id: "head.pose",        cat: "head", role: "Pose head",
      inputs: [{ port: "in", types: ["embedding"] }],
      outputs: [{ port: "out", type: "pose" }],
      impls: [
        { id: "default", label: "Coordinate MLP", desc: "Predicts (x, y, z) atom offsets.",
          paper: "EquiBind · 2022", cost: 0.6, params: [{ key: "max_atoms", kind: "int", default: 64 }] },
      ] },
    { id: "head.ranking",     cat: "head", role: "Ranking head",
      inputs: [{ port: "in", types: ["embedding"] }],
      outputs: [{ port: "out", type: "scalar" }],
      impls: [
        { id: "infonce", label: "InfoNCE", desc: "Contrastive ranking — positives are true binders, negatives are batch alternatives.",
          paper: "Oord · 2018", cost: 0.2, params: [{ key: "temperature", kind: "float", default: 0.07 }, { key: "n_negatives", kind: "int", default: 32 }] },
      ] },
  ],
  diagnostic: [
    { id: "diag.tap",         cat: "diagnostic", role: "TapInspector",
      inputs: [{ port: "in", types: ["embedding", "graph", "sequence", "map", "scalar", "prob"] }],
      outputs: [{ port: "out", type: "embedding" }],
      impls: [
        { id: "default", label: "Histogram + sample",
          desc: "Logs a value distribution and small activation samples each step. Pass-through; doesn't change training.",
          paper: "—", cost: 0.0, params: [{ key: "log_every_n_steps", kind: "int", default: 50 }, { key: "sample_size", kind: "int", default: 8 }] },
      ] },
  ],
};

// O(1) lookup by id, built once at load.
window.PS_FLOW_BLOCK_INDEX = {};
for (const group of Object.values(window.PS_FLOW_BLOCKS)) {
  for (const b of group) window.PS_FLOW_BLOCK_INDEX[b.id] = b;
}

// Curated presets — six starting compositions the user can load onto
// an empty canvas. Each lists nodes + edges in canvas coordinates.
window.PS_FLOW_PRESETS = [
  {
    id: "deepdta", label: "DeepDTA", blurb: "1-D CNN over protein sequence and SMILES; concat + MLP head. The 2018 baseline.",
    paper: "Öztürk et al, 2018", binding: "pl_simple", nodes: 5,
    layout: [
      { id: "n1", block_id: "in.protein_seq",  impl_id: "default",    x: 60,  y: 60 },
      { id: "n2", block_id: "in.ligand_fp",    impl_id: "default",    x: 60,  y: 220 },
      { id: "n3", block_id: "enc.protein_seq", impl_id: "cnn",        x: 320, y: 60 },
      { id: "n4", block_id: "fuse",            impl_id: "concat_mlp", x: 580, y: 140 },
      { id: "n5", block_id: "head.regression", impl_id: "pki",        x: 840, y: 140 },
    ],
    edges: [
      { from: "n1:out", to: "n3:in" }, { from: "n3:out", to: "n4:a" },
      { from: "n2:out", to: "n4:b" }, { from: "n4:out", to: "n5:in" },
    ],
  },
  {
    id: "graphdta", label: "GraphDTA", blurb: "Protein-1D CNN + ligand GNN; bilinear attention fusion.",
    paper: "Nguyen et al, 2021", binding: "pl_simple", nodes: 6,
    layout: [
      { id: "n1", block_id: "in.protein_seq",  impl_id: "default",   x: 60,  y: 60 },
      { id: "n2", block_id: "in.ligand_graph", impl_id: "default",   x: 60,  y: 220 },
      { id: "n3", block_id: "enc.protein_seq", impl_id: "cnn",       x: 320, y: 60 },
      { id: "n4", block_id: "enc.ligand_graph",impl_id: "gin",       x: 320, y: 220 },
      { id: "n5", block_id: "fuse",            impl_id: "bilinear",  x: 580, y: 140 },
      { id: "n6", block_id: "head.regression", impl_id: "pki",       x: 840, y: 140 },
    ],
    edges: [
      { from: "n1:out", to: "n3:in" }, { from: "n2:out", to: "n4:in" },
      { from: "n3:out", to: "n5:a" },  { from: "n4:out", to: "n5:b" },
      { from: "n5:out", to: "n6:in" },
    ],
  },
  {
    id: "drugban", label: "DrugBAN", blurb: "ESM-2 protein + GIN ligand; bilinear attention; pKd regression.",
    paper: "Bai et al, 2023", binding: "pl_simple", nodes: 6,
    layout: [
      { id: "n1", block_id: "in.protein_emb",  impl_id: "default",     x: 60,  y: 60 },
      { id: "n2", block_id: "in.ligand_graph", impl_id: "default",     x: 60,  y: 220 },
      { id: "n3", block_id: "enc.protein_seq", impl_id: "esm2_frozen", x: 320, y: 60 },
      { id: "n4", block_id: "enc.ligand_graph",impl_id: "gin",         x: 320, y: 220 },
      { id: "n5", block_id: "fuse",            impl_id: "bilinear",    x: 580, y: 140 },
      { id: "n6", block_id: "head.regression", impl_id: "pkd",         x: 840, y: 140 },
    ],
    edges: [
      { from: "n1:out", to: "n3:in" }, { from: "n2:out", to: "n4:in" },
      { from: "n3:out", to: "n5:a" },  { from: "n4:out", to: "n5:b" },
      { from: "n5:out", to: "n6:in" },
    ],
  },
  {
    id: "structgnn", label: "StructGNN-DTA", blurb: "Residue contact graph + ligand graph; cross-attention; pKi.",
    paper: "—", binding: "pl_simple", nodes: 6,
    layout: [
      { id: "n1", block_id: "in.protein_graph", impl_id: "default",    x: 60,  y: 60 },
      { id: "n2", block_id: "in.ligand_graph",  impl_id: "default",    x: 60,  y: 220 },
      { id: "n3", block_id: "enc.protein_graph",impl_id: "gat",        x: 320, y: 60 },
      { id: "n4", block_id: "enc.ligand_graph", impl_id: "gin",        x: 320, y: 220 },
      { id: "n5", block_id: "fuse",             impl_id: "cross_attn", x: 580, y: 140 },
      { id: "n6", block_id: "head.regression",  impl_id: "pki",        x: 840, y: 140 },
    ],
    edges: [
      { from: "n1:out", to: "n3:in" }, { from: "n2:out", to: "n4:in" },
      { from: "n3:out", to: "n5:a" },  { from: "n4:out", to: "n5:b" },
      { from: "n5:out", to: "n6:in" },
    ],
  },
  {
    id: "ppi_siamese", label: "PPI Siamese", blurb: "Two protein residue-graph towers, GAT encoders, two-tower dot product, binary head.",
    paper: "—", binding: "pp_binary", nodes: 6,
    layout: [
      { id: "n1", block_id: "in.protein_graph", impl_id: "default",       x: 60,  y: 60 },
      { id: "n2", block_id: "in.protein_graph", impl_id: "default",       x: 60,  y: 220 },
      { id: "n3", block_id: "enc.protein_graph",impl_id: "gat",           x: 320, y: 60 },
      { id: "n4", block_id: "enc.protein_graph",impl_id: "gat",           x: 320, y: 220 },
      { id: "n5", block_id: "fuse",             impl_id: "two_tower_dot", x: 580, y: 140 },
      { id: "n6", block_id: "head.classifier",  impl_id: "default",       x: 840, y: 140 },
    ],
    edges: [
      { from: "n1:out", to: "n3:in" }, { from: "n2:out", to: "n4:in" },
      { from: "n3:out", to: "n5:a" },  { from: "n4:out", to: "n5:b" },
      { from: "n5:out", to: "n6:in" },
    ],
  },
  {
    id: "conplex", label: "ConPLex two-tower", blurb: "Cached ESM-2 protein + ECFP4 ligand; two-tower dot; ranking head.",
    paper: "Singh et al, 2023", binding: "pl_simple", nodes: 6,
    layout: [
      { id: "n1", block_id: "in.protein_emb",   impl_id: "default",       x: 60,  y: 60 },
      { id: "n2", block_id: "in.ligand_fp",     impl_id: "default",       x: 60,  y: 220 },
      { id: "n3", block_id: "enc.protein_seq",  impl_id: "esm2_frozen",   x: 320, y: 60 },
      { id: "n4", block_id: "enc.tabular",      impl_id: "mlp",           x: 320, y: 220 },
      { id: "n5", block_id: "fuse",             impl_id: "two_tower_dot", x: 580, y: 140 },
      { id: "n6", block_id: "head.ranking",     impl_id: "infonce",       x: 840, y: 140 },
    ],
    edges: [
      { from: "n1:out", to: "n3:in" }, { from: "n2:out", to: "n4:in" },
      { from: "n3:out", to: "n5:a" },  { from: "n4:out", to: "n5:b" },
      { from: "n5:out", to: "n6:in" },
    ],
  },
];


// Top-bar feature bundles — curated shortcut chips (Brief Q9.2).
// Picking a bundle overwrites the selection; mutation silently
// deactivates the chip.
window.PS_FEATURE_BUNDLES = [
  { id: "dta_standard",    label: "DTA standard kit",        desc: "AA comp + ESM-2 + ECFP4 + mol graph",
    features: ["aa_comp", "esm2_650m", "ecfp4", "mol_graph_2d"] },
  { id: "dta_structural",  label: "DTA structural",          desc: "ESM-2 + residue graph + Uni-Mol + fake-setta",
    features: ["esm2_650m", "res_contact", "unimol_3d", "mol_graph_2d", "fakesetta"] },
  { id: "ppi_structural",  label: "PPI structural kit",      desc: "Two proteins, contact graph each + fake-setta",
    features: ["esm2_650m", "res_contact", "dssp_ss", "fakesetta"] },
  { id: "fast_baseline",   label: "Fast baseline",           desc: "Pure tabular — no embeddings",
    features: ["aa_comp", "ecfp4", "maccs", "physchem"] },
  { id: "research",        label: "Research (all-in)",       desc: "Every available feature (fake-setta until real Rosetta is installed)",
    features: ["aa_comp", "esm2_650m", "dssp_ss", "res_contact", "atom_graph",
               "fakesetta",
               "ecfp4", "chemberta", "mol_graph_2d", "unimol_3d", "physchem"] },
];


// Whether each binding-partner × task combination is even meaningful.
window.PS_PARTNER_TASK_SUPPORT = {
  pl_affinity:    { supported: true,  note: "the standard DTA setup" },
  pl_interaction: { supported: true,  note: "active/inactive binary classification from HTS" },
  pl_unsupervised:{ supported: true,  note: "sequence + SMILES pretraining" },
  pp_affinity:    { supported: true,  note: "Kd / ΔΔG-on-mutation; sparser than DTA" },
  pp_interaction: { supported: true,  note: "the bulk of PPI data" },
  pp_unsupervised:{ supported: true,  note: "sequence-only pretraining" },
  pna_affinity:   { supported: false, note: "few curated Kd measurements for protein–NA at scale; available in literature, not in our warehouse yet" },
  pna_interaction:{ supported: true,  note: "ChIP-seq / CLIP-seq derived" },
  pna_unsupervised:{ supported: true, note: "structure-only — RNA secondary structure / DNA motifs" },
};

// Target label representation — what the model is asked to predict.
// pK* values are unitless (negative log10 molar); ΔG° is energy.
window.PS_TARGET_REPRESENTATIONS = [
  { id: "pki",     label: "pKi",            unit: "pKi (unitless)",     desc: "Negative log10 of the inhibition constant Ki (in molar). Higher = stronger binding. pKi 9 ≈ 1 nM." },
  { id: "pkd",     label: "pKd",            unit: "pKd (unitless)",     desc: "Negative log10 of the dissociation constant Kd (in molar). Equilibrium affinity. pKd 8 ≈ 10 nM." },
  { id: "pic50",   label: "pIC50",          unit: "pIC50 (unitless)",   desc: "Negative log10 of the IC50 (in molar). Assay-condition-dependent; not interchangeable with Ki/Kd in general." },
  { id: "dG_kcal", label: "ΔG° (kcal·mol⁻¹)", unit: "kcal·mol⁻¹",       desc: "Binding free energy. ΔG° = −RT · ln(Kd). At 298 K, 1 pKi unit ≈ −1.36 kcal·mol⁻¹." },
  { id: "dG_kJ",   label: "ΔG° (kJ·mol⁻¹)",  unit: "kJ·mol⁻¹",          desc: "Same as above, in SI. At 298 K, 1 pKi unit ≈ −5.71 kJ·mol⁻¹." },
];

// How to handle the assay temperature when converting source measurements
// to a single target representation. Real assays span 277–310 K; without
// normalisation the Kd-to-ΔG conversion uses RT at whatever T was reported.
window.PS_TEMPERATURE_POLICIES = [
  { id: "as_reported",  label: "Use the temperature each source reports",   desc: "ΔG° = −RT · ln(Kd) at the assay temperature recorded in the source. Rows with missing T fall back to 298 K." },
  { id: "assume_298",   label: "Treat everything as 298 K (room T)",         desc: "Drop the per-row temperature and apply RT at 298 K for every conversion. The common convention; matches most published numbers." },
  { id: "normalise_310", label: "Normalise to 310 K (physiological)",        desc: "Scale ΔG° by 310/T_assay before training. Useful if the downstream application is in vivo." },
  { id: "weighted_avg",  label: "Per-source weighted average",                desc: "Each source's median assay T weighted by its sample count. Reproducible across re-runs." },
];

// Common organisms — used as a multi-select with a "select all" affordance.
window.PS_ORGANISMS = [
  { id: "human",      name: "Human (H. sapiens)",            taxid: 9606,    common: true },
  { id: "mouse",      name: "Mouse (M. musculus)",            taxid: 10090,   common: true },
  { id: "rat",        name: "Rat (R. norvegicus)",            taxid: 10116,   common: true },
  { id: "dog",        name: "Dog (C. familiaris)",            taxid: 9615,    common: true },
  { id: "monkey",     name: "Cynomolgus monkey (M. fascicularis)", taxid: 9541, common: true },
  { id: "zebrafish",  name: "Zebrafish (D. rerio)",           taxid: 7955,    common: false },
  { id: "fruitfly",   name: "Fruit fly (D. melanogaster)",    taxid: 7227,    common: false },
  { id: "celegans",   name: "C. elegans",                     taxid: 6239,    common: false },
  { id: "yeast",      name: "Yeast (S. cerevisiae)",          taxid: 559292,  common: false },
  { id: "ecoli",      name: "E. coli K-12",                   taxid: 83333,   common: false },
  { id: "sars_cov_2", name: "SARS-CoV-2",                     taxid: 2697049, common: false },
  { id: "mtb",        name: "M. tuberculosis H37Rv",          taxid: 83332,   common: false },
];

// Policy for structure files (PDB / mmCIF / pre-computed AlphaFold pickles).
// "Missing structure" is NOT a hard drop — fetch is just-in-time at example-
// build time; what changes is the disk policy for caching afterwards.
window.PS_STRUCTURE_FETCH_POLICIES = [
  { id: "fetch_and_cache",  label: "Fetch on demand, keep on disk",      desc: "Structures download lazily during example building, then stay in the warehouse cache for re-use. Best for repeat runs; uses the most disk." },
  { id: "fetch_and_evict",  label: "Fetch on demand, drop after build",  desc: "Same as above but the on-disk copies are deleted after example tensors are written. Saves disk; re-runs pay the download cost again." },
  { id: "must_be_local",    label: "Only use already-downloaded structures", desc: "Skip any pair whose structure isn't already in the local warehouse. Fastest builds; smaller dataset." },
  { id: "skip_structures",  label: "No structures at all (sequence-only run)", desc: "Use sequence + SMILES features only; structural featurizers fall back to disabled. Lightest." },
];

// ============================================================================
// PIPELINE BUILDER catalogs (Chunk 1 — data only; UI lands in screen-pipeline.jsx)
// ============================================================================
//
// A pipeline is a DAG of typed nodes. The TYPE SYSTEM below is what makes edges
// validatable — every output port emits exactly one type, every input port
// declares which types it accepts. Anything mismatched is either rejected at
// edit time or repaired by inserting a `preprocess` node.
//
// Display affordances (color / icon / short label) live alongside the id so
// the canvas can render port dots from one source of truth.
window.PS_PIPELINE_PORT_TYPES = {
  // ── Raw inputs ────────────────────────────────────────────────────
  aa_seq:           { label: "AA sequence",           short: "seq",   color: "#3b82f6", icon: "≡" },
  msa:              { label: "Multiple-seq alignment", short: "MSA",  color: "#1d4ed8", icon: "≣" },
  smiles_tokens:    { label: "SMILES tokens",          short: "smi",  color: "#10b981", icon: "S" },
  mol_graph_2d:     { label: "2D molecular graph",     short: "graph", color: "#059669", icon: "◇" },
  atom_cloud_3d:    { label: "3D atom cloud",          short: "3D",   color: "#a855f7", icon: "⋮⋮" },
  backbone_3d:      { label: "Protein backbone (3D)",   short: "bb",  color: "#9333ea", icon: "↺" },
  complex_3d:       { label: "Full-atom complex (3D)",  short: "cplx", color: "#7e22ce", icon: "⬢" },
  voxel:            { label: "3D voxel grid",           short: "vox", color: "#c084fc", icon: "▦" },
  surface_mesh:     { label: "Protein surface mesh",    short: "surf", color: "#ec4899", icon: "◈" },
  structure_tokens: { label: "Structure tokens (3Di)",  short: "3Di", color: "#db2777", icon: "✧" },
  descriptors:      { label: "Descriptors / fingerprint", short: "desc", color: "#f59e0b", icon: "♯" },
  // ── Learned representations ───────────────────────────────────────
  embedding_1d:     { label: "1D embedding",            short: "emb",  color: "#22d3ee", icon: "▬" },
  embedding_2d_pair:{ label: "Pairwise embedding",      short: "pair", color: "#0891b2", icon: "▦" },
  embedding_3d:     { label: "3D-equivariant embedding", short: "emb-3D", color: "#0e7490", icon: "▩" },
  contact_map:      { label: "Contact map",             short: "contacts", color: "#06b6d4", icon: "⊞" },
  // ── Predictions ───────────────────────────────────────────────────
  pose:             { label: "Predicted pose",          short: "pose", color: "#f97316", icon: "▶" },
  scalar:           { label: "Scalar prediction",       short: "y",   color: "#ef4444", icon: "•" },
  prob:             { label: "Probability / class",     short: "p",   color: "#dc2626", icon: "%" },
};

// Five categories with their identity (icon glyph + token + label).
// Decision F from PIPELINE_HANDOFF.md. Each glyph is a 12×12 SVG path string
// rendered inside a 16×16 badge. Identity surfaces in:
//   1. left edge stripe on canvas nodes
//   2. category icon badge in upper-left of node body
//   3. (future) palette group header
//   4. inspector popover header
// Tokens are defined in styles.css :root.
window.PS_PIPELINE_CATEGORIES = {
  input:      { label: "Input",      token: "--cat-input",      glyph: "M6 1.5v6.5 M3.5 5.5L6 8l2.5-2.5 M2 10h8" },
  preprocess: { label: "Preprocess", token: "--cat-preprocess", glyph: "M2 6h2 M6 6h4 M5 3l2 6 M3 8.5l1 1 1.5-1.5" },
  encoder:    { label: "Encoder",    token: "--cat-encoder",    glyph: "M2 2h3v3H2z M7 2h3v3H7z M2 7h3v3H2z M7 7h3v3H7z" },
  fusion:     { label: "Fusion",     token: "--cat-fusion",     glyph: "M2 3l4 3-4 3 M10 3L6 6l4 3" },
  head:       { label: "Head",       token: "--cat-head",       glyph: "M6 2a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M6 5a1 1 0 1 0 0 2 1 1 0 0 0 0-2z" },
};

// Every node in the builder. Categories drive the left-rail palette grouping.
//   inputs  []  : { port, types[] }   — types this port accepts
//   outputs []  : { port, type }      — type emitted
//   params  []  : config exposed in the inspector. kind = "int" | "float" | "enum" | "bool" | "text"
//   cost    : "low" | "mid" | "high" | "huge"   — used by the feasibility hint
//   refs    []  : short paper labels for the node tooltip
window.PS_PIPELINE_NODE_TYPES = [
  // ── INPUT (no inputs, only outputs) ────────────────────────────────
  { id: "in_aa_seq",         category: "input", label: "Protein sequence",       cost: "low",
    inputs: [], outputs: [{ port: "out", type: "aa_seq" }],
    params: [{ key: "max_len", kind: "int", default: 1024 }],
    refs: ["raw"] },
  { id: "in_msa",            category: "input", label: "MSA",                    cost: "low",
    inputs: [], outputs: [{ port: "out", type: "msa" }],
    params: [{ key: "depth", kind: "int", default: 512 }, { key: "tool", kind: "enum", default: "hhblits", options: ["hhblits", "mmseqs2", "jackhmmer"] }],
    refs: ["AlphaFold2"] },
  { id: "in_smiles",         category: "input", label: "Ligand SMILES",          cost: "low",
    partners_whitelist: ["pl"],
    inputs: [], outputs: [{ port: "tokens", type: "smiles_tokens" }, { port: "graph", type: "mol_graph_2d" }],
    params: [{ key: "tokenizer", kind: "enum", default: "chembl_bpe", options: ["chembl_bpe", "atomwise", "selfies"] }],
    refs: ["raw"] },
  { id: "in_ligand_3d",      category: "input", label: "Ligand 3D conformer",    cost: "low",
    partners_whitelist: ["pl"],
    inputs: [], outputs: [{ port: "out", type: "atom_cloud_3d" }],
    params: [{ key: "max_atoms", kind: "int", default: 64 }],
    refs: ["raw"] },
  { id: "in_protein_struct", category: "input", label: "Protein structure",      cost: "low",
    inputs: [], outputs: [{ port: "backbone", type: "backbone_3d" }, { port: "full_atom", type: "complex_3d" }],
    params: [{ key: "source", kind: "enum", default: "pdb_or_af", options: ["pdb_or_af", "pdb_only", "af2_only", "esmfold"] }],
    refs: ["PDB", "AlphaFold DB"] },
  { id: "in_pocket",         category: "input", label: "Binding pocket",         cost: "low",
    inputs: [], outputs: [{ port: "out", type: "atom_cloud_3d" }],
    params: [{ key: "radius_a", kind: "float", default: 10.0 }],
    refs: ["raw / cropped"] },
  { id: "in_surface",        category: "input", label: "Surface mesh",           cost: "low",
    inputs: [], outputs: [{ port: "out", type: "surface_mesh" }],
    params: [{ key: "resolution", kind: "float", default: 1.0 }],
    refs: ["MaSIF"] },
  { id: "in_structure_tokens", category: "input", label: "Structure tokens (3Di)", cost: "low",
    inputs: [], outputs: [{ port: "out", type: "structure_tokens" }],
    params: [],
    refs: ["Foldseek"] },
  { id: "in_descriptors",    category: "input", label: "Descriptors / fingerprint", cost: "low",
    partners_whitelist: ["pl"],
    inputs: [], outputs: [{ port: "out", type: "descriptors" }],
    params: [{ key: "kind", kind: "enum", default: "ecfp4", options: ["ecfp4", "maccs", "rdkit_2d", "physchem"] }],
    refs: ["RDKit"] },
  { id: "in_complex",        category: "input", label: "Protein–ligand complex", cost: "low",
    partners_whitelist: ["pl"],
    inputs: [], outputs: [{ port: "out", type: "complex_3d" }],
    params: [],
    refs: ["PDBbind"] },

  // ── PREPROCESS ─────────────────────────────────────────────────────
  { id: "pre_pocket_crop",   category: "preprocess", label: "Pocket cropper",     cost: "low",
    inputs: [{ port: "complex", types: ["complex_3d"] }], outputs: [{ port: "out", type: "atom_cloud_3d" }],
    params: [{ key: "radius_a", kind: "float", default: 10.0 }, { key: "include_water", kind: "bool", default: false }],
    refs: ["TankBind"], glossary: "pocket cropper",
    blurb: "Keeps only the protein atoms within `radius_a` Å of the ligand and drops the rest. Makes 3D models tractable on big proteins (kinases are 300+ residues; the pocket is 30)." },
  { id: "pre_conformer",     category: "preprocess", label: "Conformer generator", cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "smi", types: ["smiles_tokens", "mol_graph_2d"] }], outputs: [{ port: "out", type: "atom_cloud_3d" }],
    params: [{ key: "engine", kind: "enum", default: "rdkit_etkdg", options: ["rdkit_etkdg", "omega", "balloon"] }, { key: "n_conformers", kind: "int", default: 1 }],
    refs: ["RDKit", "OpenEye Omega"] },
  { id: "pre_protonate",     category: "preprocess", label: "Protonation",        cost: "low",
    inputs: [{ port: "in", types: ["atom_cloud_3d", "complex_3d"] }], outputs: [{ port: "out", type: "atom_cloud_3d" }],
    params: [{ key: "ph", kind: "float", default: 7.4 }],
    refs: ["Dimorphite-DL"] },
  { id: "pre_tautomer",      category: "preprocess", label: "Tautomer enumerator", cost: "low",
    partners_whitelist: ["pl"],
    inputs: [{ port: "smi", types: ["smiles_tokens"] }], outputs: [{ port: "out", type: "smiles_tokens" }],
    params: [{ key: "max_tautomers", kind: "int", default: 4 }],
    refs: ["RDKit"] },
  { id: "pre_charger",       category: "preprocess", label: "Partial-charge assignment", cost: "low",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["atom_cloud_3d"] }], outputs: [{ port: "out", type: "atom_cloud_3d" }],
    params: [{ key: "method", kind: "enum", default: "gasteiger", options: ["gasteiger", "am1bcc", "mmff94"] }],
    refs: ["RDKit"] },
  { id: "pre_relax",         category: "preprocess", label: "Structure relax / minimise", cost: "mid",
    inputs: [{ port: "cplx", types: ["complex_3d"] }], outputs: [{ port: "out", type: "complex_3d" }],
    params: [{ key: "engine", kind: "enum", default: "amber", options: ["amber", "openmm", "rosetta"] }, { key: "steps", kind: "int", default: 500 }],
    refs: ["OpenMM"] },
  { id: "pre_docking",       category: "preprocess", label: "Docking engine",     cost: "high",
    partners_whitelist: ["pl"],
    inputs: [{ port: "rec", types: ["backbone_3d", "complex_3d"] }, { port: "lig", types: ["atom_cloud_3d", "smiles_tokens"] }],
    outputs: [{ port: "pose", type: "pose" }],
    params: [{ key: "engine", kind: "enum", default: "vina", options: ["vina", "smina", "gnina", "diffdock"] }, { key: "exhaustiveness", kind: "int", default: 8 }],
    refs: ["AutoDock Vina", "Gnina", "DiffDock"],
    blurb: "Classical or ML docking. Searches for a low-energy ligand pose inside the pocket. Vina / smina are fast classical; gnina adds a CNN rescorer; diffdock is fully generative." },
  { id: "pre_retrieval_index", category: "preprocess", label: "Retrieval index",  cost: "mid",
    inputs: [{ port: "query", types: ["aa_seq", "smiles_tokens", "embedding_1d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "index", kind: "enum", default: "faiss_ivf", options: ["faiss_flat", "faiss_ivf", "hnsw"] }, { key: "top_k", kind: "int", default: 32 }],
    refs: ["FAISS", "ConPLex"] },
  { id: "pre_al_acquire",    category: "preprocess", label: "Active-learning selector", cost: "low",
    inputs: [{ port: "candidates", types: ["embedding_1d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "strategy", kind: "enum", default: "uncertainty", options: ["uncertainty", "diversity", "greedy", "thompson"] }],
    refs: ["BAL"] },

  // ── ENCODER ────────────────────────────────────────────────────────
  { id: "enc_cnn1d_protein", category: "encoder", label: "Protein 1D CNN",         cost: "low",
    inputs: [{ port: "in", types: ["aa_seq"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "filters", kind: "int", default: 128 }, { key: "kernel", kind: "int", default: 8 }],
    refs: ["DeepDTA"] },
  { id: "enc_cnn1d_smiles",  category: "encoder", label: "SMILES 1D CNN",         cost: "low",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["smiles_tokens"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "filters", kind: "int", default: 64 }, { key: "kernel", kind: "int", default: 6 }],
    refs: ["DeepDTA"] },
  { id: "enc_cnn2d_pair",    category: "encoder", label: "2D pair CNN",           cost: "mid",
    inputs: [{ port: "in", types: ["embedding_2d_pair", "contact_map"] }], outputs: [{ port: "out", type: "embedding_2d_pair" }],
    params: [{ key: "channels", kind: "int", default: 64 }],
    refs: ["DeepBindPoc"] },
  { id: "enc_cnn3d_voxel",   category: "encoder", label: "3D voxel CNN",          cost: "high",
    inputs: [{ port: "in", types: ["voxel"] }], outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "channels", kind: "int", default: 32 }],
    refs: ["Atomic CNN"] },
  { id: "enc_gnn_gcn",       category: "encoder", label: "GNN — GCN",             cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["mol_graph_2d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 3 }],
    refs: ["Kipf 2017"], glossary: "GCN",
    blurb: "Graph Convolutional Network — the simplest GNN. Each layer averages a node's features with its bonded neighbours'. Cheap baseline for molecule encoding." },
  { id: "enc_gnn_gat",       category: "encoder", label: "GNN — GAT",             cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["mol_graph_2d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "heads", kind: "int", default: 4 }],
    refs: ["Velickovic 2018"], glossary: "GAT",
    blurb: "Graph Attention Network — a GNN that learns attention weights over each atom's bonded neighbours instead of averaging them uniformly." },
  { id: "enc_gnn_gin",       category: "encoder", label: "GNN — GIN",             cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["mol_graph_2d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 5 }],
    refs: ["GraphDTA", "DrugBAN"], glossary: "GIN",
    blurb: "Graph Isomorphism Network — provably as expressive as the Weisfeiler-Lehman graph isomorphism test. Strong default ligand encoder in modern DTA pipelines." },
  { id: "enc_egnn",          category: "encoder", label: "EGNN",                   cost: "high",
    inputs: [{ port: "in", types: ["atom_cloud_3d", "complex_3d", "pose"] }], outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 4 }],
    refs: ["Satorras 2021"], glossary: "EGNN",
    blurb: "Equivariant Graph Neural Network. Operates on 3D atomic coordinates and is invariant under rotations and translations — physics is the same if you rotate the molecule." },
  { id: "enc_gvp",           category: "encoder", label: "GVP-GNN",               cost: "high",
    inputs: [{ port: "in", types: ["backbone_3d", "atom_cloud_3d"] }], outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "scalar_dim", kind: "int", default: 100 }, { key: "vector_dim", kind: "int", default: 16 }],
    refs: ["Jing 2021", "EquiBind"], glossary: "GVP-GNN",
    blurb: "Geometric Vector Perceptron GNN — mixes scalar and vector features so the network can reason about bond lengths AND bond orientations equivariantly. The protein side of EquiBind." },
  { id: "enc_se3_transformer", category: "encoder", label: "SE(3)-Transformer",   cost: "huge",
    inputs: [{ port: "in", types: ["atom_cloud_3d"] }], outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "degrees", kind: "int", default: 2 }],
    refs: ["Fuchs 2020"], glossary: "SE(3)",
    blurb: "Attention-based SE(3)-equivariant network. Combines transformer attention with spherical-harmonic features so attention itself respects 3D rotation symmetry." },
  { id: "enc_equiformer",    category: "encoder", label: "Equiformer",            cost: "huge",
    inputs: [{ port: "in", types: ["atom_cloud_3d"] }], outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "degrees", kind: "int", default: 2 }],
    refs: ["Liao 2023"], glossary: "Equiformer",
    blurb: "A more recent SE(3)-equivariant transformer that handles higher-degree spherical harmonics. State-of-the-art on small-molecule energy / property prediction." },
  { id: "enc_mace",          category: "encoder", label: "MACE",                  cost: "huge",
    inputs: [{ port: "in", types: ["atom_cloud_3d"] }], outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "correlation", kind: "int", default: 3 }],
    refs: ["Batatia 2022"], glossary: "MACE",
    blurb: "Higher-body-order equivariant message passing — each node update sees not just pairs but triples/quads of neighbours simultaneously. Currently the gold standard for ML potentials." },
  { id: "enc_evoformer",     category: "encoder", label: "Evoformer trunk",       cost: "huge",
    inputs: [{ port: "msa", types: ["msa"] }],
    outputs: [{ port: "single", type: "embedding_1d" }, { port: "pair", type: "embedding_2d_pair" }],
    params: [{ key: "blocks", kind: "int", default: 48 }, { key: "freeze", kind: "bool", default: true }],
    refs: ["AlphaFold2"], glossary: "Evoformer",
    blurb: "AlphaFold-2's trunk: 48 blocks of MSA-attention plus triangular pair updates. Produces a single (per-residue) and a pair (per-residue-pair) representation that drive every downstream prediction." },
  { id: "enc_esm2",          category: "encoder", label: "ESM-2 (PLM)",           cost: "high",
    inputs: [{ port: "in", types: ["aa_seq"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "checkpoint", kind: "enum", default: "esm2_t33_650M", options: ["esm2_t12_35M", "esm2_t30_150M", "esm2_t33_650M", "esm2_t36_3B"] }, { key: "freeze", kind: "bool", default: true }, { key: "lora", kind: "bool", default: false }],
    refs: ["Lin 2023"],
    glossary: "ESM-2",
    blurb: "Meta AI's protein language model. Reads an amino-acid sequence and produces a per-residue vector that encodes evolutionary + structural priors. The 650M checkpoint is the practical sweet spot." },
  { id: "enc_saprot",        category: "encoder", label: "SaProt (seq + 3Di)",     cost: "high",
    inputs: [{ port: "seq", types: ["aa_seq"] }, { port: "struct", types: ["structure_tokens"] }],
    outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "checkpoint", kind: "enum", default: "saprot_650M", options: ["saprot_35M", "saprot_650M"] }, { key: "lora", kind: "bool", default: false }],
    refs: ["Su 2024"], glossary: "SaProt",
    blurb: "A protein language model that consumes BOTH the sequence and a structure-token alphabet (3Di from Foldseek). When AlphaFold-predicted structures are available, beats sequence-only PLMs on cold-target tasks." },
  { id: "enc_ankh",          category: "encoder", label: "Ankh (PLM)",            cost: "high",
    inputs: [{ port: "in", types: ["aa_seq"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "checkpoint", kind: "enum", default: "ankh_large", options: ["ankh_base", "ankh_large"] }, { key: "freeze", kind: "bool", default: true }],
    refs: ["Elnaggar 2023"], glossary: "Ankh",
    blurb: "T5-style protein language model. Comparable to ESM-2 at roughly half the parameter count; faster to deploy." },
  { id: "enc_protbert",      category: "encoder", label: "ProtBERT",              cost: "mid",
    inputs: [{ port: "in", types: ["aa_seq"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "freeze", kind: "bool", default: true }],
    refs: ["MolTrans"], glossary: "ProtBERT",
    blurb: "Early protein language model (BERT trained on UniRef). Mostly outperformed by ESM-2 / Ankh, but cheap and useful as a baseline." },
  { id: "enc_molformer",     category: "encoder", label: "MolFormer (chem-LM)",   cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["smiles_tokens"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "freeze", kind: "bool", default: true }, { key: "lora", kind: "bool", default: false }],
    refs: ["Ross 2022"], glossary: "MolFormer",
    blurb: "IBM's chemistry language model — reads a SMILES string and produces a molecular embedding that captures common chemistry priors." },
  { id: "enc_chemberta",     category: "encoder", label: "ChemBERTa (chem-LM)",   cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["smiles_tokens"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "checkpoint", kind: "enum", default: "chemberta_77M", options: ["chemberta_10M", "chemberta_77M"] }, { key: "freeze", kind: "bool", default: true }],
    refs: ["Chithrananda 2020"], glossary: "ChemBERTa",
    blurb: "BERT pretrained on SMILES strings. Produces molecular embeddings; cheaper than MolFormer, broadly comparable quality." },
  { id: "enc_unimol",        category: "encoder", label: "Uni-Mol (3D chem-LM)",  cost: "high",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["atom_cloud_3d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "freeze", kind: "bool", default: true }],
    refs: ["Zhou 2023"], glossary: "Uni-Mol",
    blurb: "A 3D molecular transformer. Operates directly on atom-coordinate clouds rather than 2D graphs, so it sees conformer geometry." },
  { id: "enc_dmasif",        category: "encoder", label: "dMaSIF (surface)",      cost: "high",
    inputs: [{ port: "in", types: ["surface_mesh"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "radius_a", kind: "float", default: 9.0 }],
    refs: ["Sverrisson 2021"], glossary: "dMaSIF",
    blurb: "Differentiable molecular surface featuriser. Encodes the protein surface as per-vertex features over a mesh — useful when binding is surface-driven (e.g. PPI)." },
  { id: "enc_gnn_residue",   category: "encoder", label: "Protein residue GNN (GCN)", cost: "mid",
    inputs: [{ port: "in", types: ["aa_seq"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 3 }, { key: "contact_cutoff", kind: "float", default: 8.0 }],
    refs: ["Kipf 2017", "graph_features.py"], glossary: "residue GNN",
    blurb: "GCN over a per-residue graph. The backend builds the graph automatically: if an AlphaFold/PDB file is cached for the UniProt, it uses CA-CA contact edges (≤ 8 Å); otherwise it falls back to a sliding-window sequence graph so training never fails on missing structures. Reads 22-d residue features (20-d AA one-hot + position + has-structure flag)." },
  { id: "enc_inv_folding",   category: "encoder", label: "Inverse-folding GVP",   cost: "high",
    inputs: [{ port: "in", types: ["backbone_3d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [],
    refs: ["ESM-IF"] },
  { id: "enc_lora_adapter",  category: "encoder", label: "LoRA adapter (wrap PLM)", cost: "low",
    inputs: [{ port: "in", types: ["embedding_1d"] }], outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "rank", kind: "int", default: 8 }, { key: "alpha", kind: "int", default: 16 }],
    refs: ["Hu 2021"] },

  // ── FUSION ────────────────────────────────────────────────────────
  { id: "fuse_concat",       category: "fusion", label: "Concat + MLP",           cost: "low",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "hidden", kind: "int", default: 256 }, { key: "layers", kind: "int", default: 2 }, { key: "dropout", kind: "float", default: 0.1 }],
    refs: ["DeepDTA"] },
  { id: "fuse_dot",          category: "fusion", label: "Dot product (two-tower)", cost: "low",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "scalar" }],
    params: [{ key: "normalize", kind: "bool", default: true }],
    refs: ["ConPLex"] },
  { id: "fuse_cosine",       category: "fusion", label: "Cosine similarity",      cost: "low",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "scalar" }],
    params: [],
    refs: ["Siamese DTI"] },
  { id: "fuse_bilinear_attn", category: "fusion", label: "Bilinear attention",    cost: "mid",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "rank", kind: "int", default: 64 }],
    refs: ["DrugBAN"], glossary: "bilinear attention",
    blurb: "A trainable bilinear form xᵀWy that scores how strongly each protein position attends to each ligand atom. Lower-rank than full cross-attention, similar accuracy on DTA." },
  { id: "fuse_cross_attn",   category: "fusion", label: "Cross-attention",        cost: "mid",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "heads", kind: "int", default: 8 }, { key: "layers", kind: "int", default: 2 }],
    refs: ["MolTrans"], glossary: "cross-attention",
    blurb: "Transformer block where queries from one tower (protein) attend to keys/values from the other (ligand) — and vice-versa. The standard heavyweight fusion mechanism." },
  { id: "fuse_co_attn",      category: "fusion", label: "Co-attention",           cost: "mid",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "heads", kind: "int", default: 8 }],
    refs: ["HyperAttentionDTI"], glossary: "co-attention",
    blurb: "Symmetric cross-attention — both towers attend to each other simultaneously and share attention parameters. Slightly cheaper than full cross-attention." },
  { id: "fuse_gated",        category: "fusion", label: "Gated fusion",           cost: "mid",
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "embedding_1d" }],
    params: [{ key: "hidden", kind: "int", default: 256 }],
    refs: ["GatedFusion"] },
  { id: "fuse_outer_product", category: "fusion", label: "Outer-product interaction map", cost: "mid",
    partners_whitelist: ["pl"],
    inputs: [{ port: "a", types: ["embedding_1d"] }, { port: "b", types: ["embedding_1d"] }],
    outputs: [{ port: "out", type: "embedding_2d_pair" }],
    params: [],
    refs: ["MONN"], glossary: "outer-product interaction",
    blurb: "Builds a 2D interaction matrix (protein-position × ligand-position) by taking the outer product of the two towers' embeddings. Feeds a triangle-update or CNN downstream." },
  { id: "fuse_triangle",     category: "fusion", label: "Triangle update",        cost: "high",
    inputs: [{ port: "pair", types: ["embedding_2d_pair"] }],
    outputs: [{ port: "out", type: "embedding_2d_pair" }],
    params: [{ key: "blocks", kind: "int", default: 4 }],
    refs: ["AlphaFold2", "TankBind"], glossary: "triangle update",
    blurb: "AlphaFold's pair-stack operation: each edge (i,j) in the pair representation is updated using the other two edges of every triangle (i,j,k). The geometric inductive bias that makes folding work." },
  { id: "fuse_joint_graph",  category: "fusion", label: "Joint-graph message passing", cost: "high",
    partners_whitelist: ["pl"],
    inputs: [{ port: "lig", types: ["mol_graph_2d", "atom_cloud_3d"] }, { port: "prot", types: ["backbone_3d", "atom_cloud_3d"] }],
    outputs: [{ port: "out", type: "embedding_3d" }],
    params: [{ key: "hidden", kind: "int", default: 128 }, { key: "layers", kind: "int", default: 4 }],
    refs: ["IGN", "GIGN"], glossary: "joint-graph",
    blurb: "Builds one big graph containing protein atoms AND ligand atoms, with inter-atomic edges between them. Runs message passing on the whole thing so the model directly sees contact patterns." },
  { id: "fuse_keypoint",     category: "fusion", label: "Keypoint matching",      cost: "high",
    partners_whitelist: ["pl"],
    inputs: [{ port: "a", types: ["embedding_3d"] }, { port: "b", types: ["embedding_3d"] }],
    outputs: [{ port: "out", type: "pose" }],
    params: [{ key: "n_keypoints", kind: "int", default: 8 }],
    refs: ["EquiBind"], glossary: "keypoint matching",
    blurb: "Predict a small set of corresponding (protein-atom, ligand-atom) pairs and solve a rigid alignment from them — the trick that lets EquiBind do one-shot blind docking." },
  { id: "fuse_diffusion",    category: "fusion", label: "Equivariant diffusion denoiser", cost: "huge",
    partners_whitelist: ["pl"],
    inputs: [{ port: "in", types: ["complex_3d", "pose", "atom_cloud_3d"] }],
    outputs: [{ port: "out", type: "complex_3d" }],
    params: [{ key: "steps", kind: "int", default: 20 }, { key: "sigma", kind: "float", default: 1.0 }],
    refs: ["DiffDock"], glossary: "diffusion denoiser",
    blurb: "Starts from a random ligand placement and iteratively denoises it toward a valid bound pose, guided by the protein. The principle behind DiffDock." },

  // ── HEAD ──────────────────────────────────────────────────────────
  { id: "head_regression",   category: "head", label: "Affinity regression",      cost: "low",
    inputs: [{ port: "in", types: ["embedding_1d", "embedding_2d_pair", "embedding_3d", "scalar"] }],
    outputs: [{ port: "y", type: "scalar" }],
    params: [{ key: "target", kind: "enum", default: "pki", options: ["pki", "pkd", "pic50", "dG_kcal", "dG_kJ"] }, { key: "loss", kind: "enum", default: "mse", options: ["mse", "huber", "smooth_l1"] }],
    refs: ["std"] },
  { id: "head_dti_binary",   category: "head", label: "DTI binary classifier",    cost: "low",
    inputs: [{ port: "in", types: ["embedding_1d"] }],
    outputs: [{ port: "p", type: "prob" }],
    params: [{ key: "loss", kind: "enum", default: "bce", options: ["bce", "focal"] }],
    refs: ["std"] },
  { id: "head_ranking",      category: "head", label: "Ranking head",             cost: "low",
    inputs: [{ port: "in", types: ["embedding_1d", "scalar"] }],
    outputs: [{ port: "y", type: "scalar" }],
    params: [{ key: "loss", kind: "enum", default: "infonce", options: ["bpr", "infonce", "triplet"] }, { key: "temperature", kind: "float", default: 0.07 }],
    refs: ["ConPLex"],
    blurb: "Trains the model to score positive (binding) pairs higher than negatives. Used by retrieval / two-tower architectures. The exact loss (BPR vs InfoNCE vs triplet) trades off in-batch vs cross-batch contrast." },
  { id: "head_pose",         category: "head", label: "Pose head",                cost: "mid",
    inputs: [{ port: "in", types: ["embedding_3d", "embedding_2d_pair", "embedding_1d", "pose", "complex_3d"] }],
    outputs: [{ port: "pose", type: "pose" }],
    params: [{ key: "loss", kind: "enum", default: "fape", options: ["fape", "rmsd", "distance_map"] }],
    refs: ["AlphaFold2", "EquiBind"] },
  { id: "head_confidence",   category: "head", label: "Confidence (plDDT / pTM)", cost: "low",
    inputs: [{ port: "in", types: ["embedding_3d", "embedding_2d_pair", "embedding_1d"] }],
    outputs: [{ port: "score", type: "scalar" }],
    params: [{ key: "kind", kind: "enum", default: "plddt", options: ["plddt", "ptm", "iptm"] }],
    refs: ["AlphaFold2"], glossary: "plDDT",
    blurb: "AlphaFold-style confidence prediction. plDDT is per-residue (0–100); pTM is a single 0–1 score for the whole structure; ipTM scopes pTM to interface residues only." },
  { id: "head_multitask",    category: "head", label: "Multi-task aggregator",    cost: "low",
    inputs: [{ port: "in", types: ["embedding_1d"] }],
    outputs: [{ port: "y", type: "scalar" }],
    params: [{ key: "tasks", kind: "text", default: "pki,dti" }, { key: "balancing", kind: "enum", default: "uncertainty", options: ["equal", "uncertainty", "gradnorm"] }],
    refs: ["MT-DTI"] },
  { id: "head_contact_aux",  category: "head", label: "Contact-map auxiliary",    cost: "low",
    inputs: [{ port: "in", types: ["embedding_2d_pair"] }],
    outputs: [{ port: "contacts", type: "contact_map" }],
    params: [{ key: "weight", kind: "float", default: 0.1 }],
    refs: ["MONN"] },
  { id: "head_selectivity",  category: "head", label: "Selectivity head",         cost: "low",
    inputs: [{ port: "in", types: ["embedding_1d"] }],
    outputs: [{ port: "y", type: "scalar" }],
    params: [{ key: "n_targets", kind: "int", default: 8 }],
    refs: ["KinSelect"] },
];

// Helper for the screen-pipeline canvas: O(1) lookup by id.
window.PS_PIPELINE_NODE_INDEX = (() => {
  const m = {};
  for (const n of window.PS_PIPELINE_NODE_TYPES) m[n.id] = n;
  return m;
})();

// Human labels for stage roles used in the template `roles` map below. The
// StageStack component groups the per-role slots under these labels.
window.PS_PIPELINE_ROLE_LABELS = {
  protein_encoder: { label: "Protein encoder",       sub: "turns protein input into a learned representation" },
  ligand_encoder:  { label: "Ligand encoder",        sub: "turns the small-molecule input into a learned representation" },
  trunk:           { label: "Trunk (folding model)", sub: "MSA / pair-stack trunk; runs before the pose head" },
  retrieval:       { label: "Retrieval",             sub: "shortlist candidates against a pre-built index" },
  preprocess:      { label: "Preprocessing",         sub: "pocket cropping / docking / relax / etc. before encoding" },
  scorer:          { label: "Scorer",                sub: "post-fusion module that prepares features for the head" },
  fusion:          { label: "Fusion / interaction",  sub: "combines the two representations into a joint signal" },
  head:            { label: "Prediction head",       sub: "what the model is asked to output (affinity, pose, …)" },
};

// Named templates. Each is a wired DAG the user can load as a starting point.
// `objective_tag` matches PS_DESIGN_OBJECTIVES.id so Splits can highlight
// compatible templates. `partners_tag` lists the binding partners the template
// is meaningful for (PS_BINDING_PARTNERS.id). Coordinates are auto-layout
// hints (col, row) — the canvas re-snaps them on load.
//
// `roles` maps a role name (key into PS_PIPELINE_ROLE_LABELS) → array of
// template-node ids. Each entry is a swappable slot in the StageStack. The
// StageStack derives swap candidates from the current node's input/output
// types so types remain valid after a swap.
window.PS_PIPELINE_TEMPLATES = [
  {
    id: "deepdta", label: "DeepDTA",
    blurb: "Twin 1D-CNN over protein sequence and SMILES, concat → MLP regression. The canonical sequence-only DTA baseline.",
    objective_tag: "interpolation", partners_tag: ["pl"], cost: "low",
    refs: ["Öztürk 2018"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["se"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",   type: "in_aa_seq",         col: 0, row: 0 },
      { id: "s",   type: "in_smiles",         col: 0, row: 1 },
      { id: "pe",  type: "enc_cnn1d_protein", col: 1, row: 0 },
      { id: "se",  type: "enc_cnn1d_smiles",  col: 1, row: 1 },
      { id: "f",   type: "fuse_concat",       col: 2, row: 0.5 },
      { id: "h",   type: "head_regression",   col: 3, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:tokens", to: "se:in" },
      { from: "pe:out", to: "f:a" }, { from: "se:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "graphdta", label: "GraphDTA",
    blurb: "Protein 1D-CNN + ligand GIN graph encoder, concat → MLP. Adds 2D graph awareness on the ligand side.",
    objective_tag: "interpolation", partners_tag: ["pl"], cost: "low",
    refs: ["Nguyen 2021"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["ge"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",         col: 0, row: 0 },
      { id: "s",  type: "in_smiles",         col: 0, row: 1 },
      { id: "pe", type: "enc_cnn1d_protein", col: 1, row: 0 },
      { id: "ge", type: "enc_gnn_gin",       col: 1, row: 1 },
      { id: "f",  type: "fuse_concat",       col: 2, row: 0.5 },
      { id: "h",  type: "head_regression",   col: 3, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:graph", to: "ge:in" },
      { from: "pe:out", to: "f:a" }, { from: "ge:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "moltrans", label: "MolTrans",
    blurb: "Pretrained PLM (ProtBERT) + chemical LM (ChemBERTa) wired with cross-attention. Strong DTA baseline on warm splits.",
    objective_tag: "interpolation", partners_tag: ["pl"], cost: "mid",
    refs: ["Huang 2020"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["se"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",        col: 0, row: 0 },
      { id: "s",  type: "in_smiles",        col: 0, row: 1 },
      { id: "pe", type: "enc_protbert",     col: 1, row: 0 },
      { id: "se", type: "enc_chemberta",    col: 1, row: 1 },
      { id: "f",  type: "fuse_cross_attn",  col: 2, row: 0.5 },
      { id: "h",  type: "head_regression",  col: 3, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:tokens", to: "se:in" },
      { from: "pe:out", to: "f:a" }, { from: "se:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "drugban", label: "DrugBAN",
    blurb: "Protein 1D-CNN + ligand GIN, joined by bilinear-attention pooling. Strong on interpolation; widely cited.",
    objective_tag: "interpolation", partners_tag: ["pl"], cost: "mid",
    refs: ["Bai 2023"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["ge"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",          col: 0, row: 0 },
      { id: "s",  type: "in_smiles",          col: 0, row: 1 },
      { id: "pe", type: "enc_cnn1d_protein",  col: 1, row: 0 },
      { id: "ge", type: "enc_gnn_gin",        col: 1, row: 1 },
      { id: "f",  type: "fuse_bilinear_attn", col: 2, row: 0.5 },
      { id: "h",  type: "head_regression",    col: 3, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:graph", to: "ge:in" },
      { from: "pe:out", to: "f:a" }, { from: "ge:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "struct_gnn_dta", label: "StructGNN-DTA (protein graph + ligand graph)",
    blurb: "Both sides are GNNs: protein residue graph (GCN over CA-CA contacts from cached AlphaFold PDBs; sliding-window sequence fallback when no structure is cached), ligand 2D molecular graph (GIN). Bilinear-attention fusion. Lets the model reason about contact geometry on the protein side.",
    objective_tag: "generalization", partners_tag: ["pl"], cost: "mid",
    refs: ["graph_features.py", "Bai 2023"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["ge"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",          col: 0, row: 0 },
      { id: "s",  type: "in_smiles",          col: 0, row: 1 },
      { id: "pe", type: "enc_gnn_residue",    col: 1, row: 0 },
      { id: "ge", type: "enc_gnn_gin",        col: 1, row: 1 },
      { id: "f",  type: "fuse_bilinear_attn", col: 2, row: 0.5 },
      { id: "h",  type: "head_regression",    col: 3, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:graph", to: "ge:in" },
      { from: "pe:out", to: "f:a" }, { from: "ge:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "conplex", label: "ConPLex (retrieval / two-tower)",
    blurb: "ESM-2 protein tower + ChemBERTa ligand tower with contrastive ranking. Designed for cold-target generalisation.",
    objective_tag: "generalization", partners_tag: ["pl"], cost: "high",
    refs: ["Singh 2023"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["se"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",      col: 0, row: 0 },
      { id: "s",  type: "in_smiles",      col: 0, row: 1 },
      { id: "pe", type: "enc_esm2",       col: 1, row: 0 },
      { id: "se", type: "enc_chemberta",  col: 1, row: 1 },
      { id: "f",  type: "fuse_dot",       col: 2, row: 0.5 },
      { id: "h",  type: "head_ranking",   col: 3, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:tokens", to: "se:in" },
      { from: "pe:out", to: "f:a" }, { from: "se:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "saprot_dti", label: "SaProt-DTI (structure-aware PLM)",
    blurb: "SaProt fused 3Di structure-tokens with sequence; cross-attended with MolFormer. Cold-target friendly when AF2 structures exist.",
    objective_tag: "generalization", partners_tag: ["pl"], cost: "high",
    refs: ["Su 2024"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["se"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",            col: 0, row: 0 },
      { id: "st", type: "in_structure_tokens",  col: 0, row: 1 },
      { id: "s",  type: "in_smiles",            col: 0, row: 2 },
      { id: "pe", type: "enc_saprot",           col: 1, row: 0.5 },
      { id: "se", type: "enc_molformer",        col: 1, row: 2 },
      { id: "f",  type: "fuse_cross_attn",      col: 2, row: 1 },
      { id: "h",  type: "head_regression",      col: 3, row: 1 },
    ],
    edges: [
      { from: "p:out", to: "pe:seq" }, { from: "st:out", to: "pe:struct" },
      { from: "s:tokens", to: "se:in" },
      { from: "pe:out", to: "f:a" }, { from: "se:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "tankbind", label: "TankBind",
    blurb: "ESM-2 + ligand GIN feeding a triangle-update pair stack over pocket fragments. Joint pose + affinity prediction.",
    objective_tag: "generalization", partners_tag: ["pl"], cost: "huge",
    refs: ["Lu 2022"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["se"], preprocess: ["pc"], fusion: ["op", "tr"], head: ["hr", "hp"] },
    nodes: [
      { id: "p",  type: "in_aa_seq",         col: 0, row: 0 },
      { id: "ps", type: "in_protein_struct", col: 0, row: 1 },
      { id: "s",  type: "in_smiles",         col: 0, row: 2 },
      { id: "pc", type: "pre_pocket_crop",   col: 1, row: 1 },
      { id: "pe", type: "enc_esm2",          col: 1, row: 0 },
      { id: "se", type: "enc_gnn_gin",       col: 1, row: 2 },
      { id: "op", type: "fuse_outer_product", col: 2, row: 1 },
      { id: "tr", type: "fuse_triangle",     col: 3, row: 1 },
      { id: "hr", type: "head_regression",   col: 4, row: 0.5 },
      { id: "hp", type: "head_pose",         col: 4, row: 1.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "s:graph", to: "se:in" },
      { from: "ps:full_atom", to: "pc:complex" },
      { from: "pe:out", to: "op:a" }, { from: "se:out", to: "op:b" },
      { from: "op:out", to: "tr:pair" },
      { from: "tr:out", to: "hr:in" }, { from: "tr:out", to: "hp:in" },
    ],
  },
  {
    id: "equibind", label: "EquiBind",
    blurb: "GVP-GNN on protein backbone + EGNN on ligand, joined by keypoint matching for one-shot equivariant pose prediction.",
    objective_tag: "generalization", partners_tag: ["pl"], cost: "high",
    refs: ["Stärk 2022"],
    roles: { protein_encoder: ["pe"], ligand_encoder: ["le"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "ps", type: "in_protein_struct", col: 0, row: 0 },
      { id: "l",  type: "in_ligand_3d",      col: 0, row: 1 },
      { id: "pe", type: "enc_gvp",           col: 1, row: 0 },
      { id: "le", type: "enc_egnn",          col: 1, row: 1 },
      { id: "f",  type: "fuse_keypoint",     col: 2, row: 0.5 },
      { id: "h",  type: "head_pose",         col: 3, row: 0.5 },
    ],
    edges: [
      { from: "ps:backbone", to: "pe:in" }, { from: "l:out", to: "le:in" },
      { from: "pe:out", to: "f:a" }, { from: "le:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "diffdock_rescore", label: "DiffDock + rescore",
    blurb: "Generate poses with an equivariant diffusion denoiser, then rescore with a confidence head. Strong on novel pockets.",
    objective_tag: "generalization", partners_tag: ["pl"], cost: "huge",
    refs: ["Corso 2023"],
    roles: { preprocess: ["d"], fusion: ["df"], scorer: ["le"], head: ["hc", "hp"] },
    nodes: [
      { id: "ps", type: "in_protein_struct", col: 0, row: 0 },
      { id: "l",  type: "in_ligand_3d",      col: 0, row: 1 },
      { id: "d",  type: "pre_docking",       col: 1, row: 0.5 },
      { id: "df", type: "fuse_diffusion",    col: 2, row: 0.5 },
      { id: "le", type: "enc_egnn",          col: 3, row: 0 },
      { id: "hc", type: "head_confidence",   col: 4, row: 0.5 },
      { id: "hp", type: "head_pose",         col: 4, row: 1 },
    ],
    edges: [
      { from: "ps:backbone", to: "d:rec" }, { from: "l:out", to: "d:lig" },
      { from: "d:pose", to: "df:in" },
      { from: "df:out", to: "le:in" },
      { from: "le:out", to: "hc:in" }, { from: "df:out", to: "hp:in" },
    ],
  },
  {
    id: "afm_tuned", label: "AlphaFold-Multimer (fine-tuned)",
    blurb: "Evoformer trunk over an MSA, triangle-update pair stack, pose + pTM/ipTM confidence. The PPI-pose gold standard.",
    objective_tag: "generalization", partners_tag: ["pp", "pna"], cost: "huge",
    refs: ["Evans 2022"],
    roles: { trunk: ["ev"], fusion: ["tr"], head: ["hp", "hc"] },
    nodes: [
      { id: "msa", type: "in_msa",            col: 0, row: 0.5 },
      { id: "ev",  type: "enc_evoformer",     col: 1, row: 0.5 },
      { id: "tr",  type: "fuse_triangle",     col: 2, row: 1 },
      { id: "hp",  type: "head_pose",         col: 3, row: 0 },
      { id: "hc",  type: "head_confidence",   col: 3, row: 1.5 },
    ],
    edges: [
      { from: "msa:out", to: "ev:msa" },
      { from: "ev:pair", to: "tr:pair" },
      { from: "ev:single", to: "hp:in" }, { from: "tr:out", to: "hc:in" },
    ],
  },
  {
    id: "pipr", label: "PIPR (PPI siamese)",
    blurb: "Siamese 1D-CNN towers over both partner sequences, concat + MLP for binary PPI classification. Sequence-only PPI workhorse.",
    objective_tag: "interpolation", partners_tag: ["pp"], cost: "low",
    refs: ["Chen 2019"],
    roles: { protein_encoder: ["ae", "be"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "a",  type: "in_aa_seq",          col: 0, row: 0 },
      { id: "b",  type: "in_aa_seq",          col: 0, row: 1 },
      { id: "ae", type: "enc_cnn1d_protein",  col: 1, row: 0 },
      { id: "be", type: "enc_cnn1d_protein",  col: 1, row: 1 },
      { id: "f",  type: "fuse_concat",        col: 2, row: 0.5 },
      { id: "h",  type: "head_dti_binary",    col: 3, row: 0.5 },
    ],
    edges: [
      { from: "a:out", to: "ae:in" }, { from: "b:out", to: "be:in" },
      { from: "ae:out", to: "f:a" }, { from: "be:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "ppi_gnn_siamese", label: "PPI siamese GNN (residue-graph)",
    blurb: "Two protein residue graphs share a GCN encoder. 4-way fusion [a; b; |a−b|; a⊙b] → MLP → binary interaction probability. Pulls HIPPIE pairs (confidence ≥ 0.5) with 1:1 negative sampling; uses cached AlphaFold PDBs when available, sequence-only sliding-window graphs otherwise.",
    objective_tag: "interpolation", partners_tag: ["pp"], cost: "mid",
    refs: ["HIPPIE", "Hashemifar 2018"],
    roles: { protein_encoder: ["e"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "a",  type: "in_aa_seq",         col: 0, row: 0 },
      { id: "b",  type: "in_aa_seq",         col: 0, row: 1 },
      { id: "e",  type: "enc_gnn_residue",   col: 1, row: 0.5 },
      { id: "f",  type: "fuse_concat",       col: 2, row: 0.5 },
      { id: "h",  type: "head_dti_binary",   col: 3, row: 0.5 },
    ],
    edges: [
      { from: "a:out", to: "e:in" }, { from: "b:out", to: "e:in" },
      { from: "e:out", to: "f:a" },  { from: "e:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
  {
    id: "retrieval_cascade", label: "Retrieval → rerank cascade",
    blurb: "Cheap ESM-2 two-tower retrieves top-k candidates per query, then a cross-attention reranker scores them. Scales to millions of targets.",
    objective_tag: "interpolation", partners_tag: ["pl", "pp"], cost: "mid",
    refs: ["ConPLex", "ColBERT"],
    roles: { protein_encoder: ["pe"], retrieval: ["ri"], ligand_encoder: ["se"], fusion: ["f"], head: ["h"] },
    nodes: [
      { id: "p",   type: "in_aa_seq",         col: 0, row: 0 },
      { id: "s",   type: "in_smiles",         col: 0, row: 1 },
      { id: "pe",  type: "enc_esm2",          col: 1, row: 0 },
      { id: "ri",  type: "pre_retrieval_index", col: 2, row: 0 },
      { id: "se",  type: "enc_chemberta",     col: 1, row: 1 },
      { id: "f",   type: "fuse_cross_attn",   col: 3, row: 0.5 },
      { id: "h",   type: "head_ranking",      col: 4, row: 0.5 },
    ],
    edges: [
      { from: "p:out", to: "pe:in" }, { from: "pe:out", to: "ri:query" },
      { from: "s:tokens", to: "se:in" },
      { from: "ri:out", to: "f:a" }, { from: "se:out", to: "f:b" },
      { from: "f:out", to: "h:in" },
    ],
  },
];

window.PS_DATA = {
  warehouse: {
    // Updated after the 2026-05 expanded materialization (SIFTS + Swiss-Prot
    // full kingdoms + IntAct + STRING + Reactome + Pfam clans + M-CSA + AF).
    proteins: 574_627,                  // Swiss-Prot reviewed (574,627 entries)
    pdb_entries: 236_510,                // SIFTS-derived distinct PDB IDs
    structures: 968_580,
    ligands: 17_647,                     // Davis + KIBA + GtoPdb + PDBbind cleaned
    ligand_signatures: 5_794_554,
    protein_ligand_edges: 24_804,
    protein_protein_edges: 1_400_000,    // IntAct + STRING (700+) + Reactome
    motif_site_annotations: 3_549_332,   // Pfam + InterPro across all Swiss-Prot
    go_annotations: 3_358_100,
    residue_annotations: 1_946_075,
    clans: 812,
    pathways: 2_730,
    catalytic_sites: 5_248,
    leakage_groups: 11,
    sources: 18,
    last_consolidation: "2026-05-22 16:00 UTC",
  },

  sources: [
    // Each source carries:
    //  partners : which binding-partner kinds it covers (pl / pp / pna)
    //  tasks    : which task types its rows can fuel (affinity / interaction / unsupervised)
    //  status   : "integrated" — pulled into the warehouse; freely selectable.
    //             "planned"    — agreed scope, ingestion not done yet; shown but disabled.
    //             "future"     — long-tail, not on the roadmap; shown for completeness.
    // ── Already integrated into the warehouse ─────────────────────────
    { id: "bindingdb",    name: "BindingDB",         kind: "assay",     partners: ["pl"],         tasks: ["affinity", "interaction"],          status: "integrated", rows: 2_310_442,  picked: 1_842_109, updated: "2026-04-11", tier: "authoritative", desc: "Public binding affinities (Ki/Kd/IC50). ~2.3M curated measurements." },
    { id: "chembl",       name: "ChEMBL 36",         kind: "assay",     partners: ["pl"],         tasks: ["affinity", "interaction"],          status: "integrated", rows: 19_402_188, picked: 484_201,   updated: "2026-04-11", tier: "authoritative", desc: "EBI's drug-discovery DB. Largest assay archive; mixed confidence." },
    { id: "pdbbind",      name: "PDBbind",           kind: "structure", partners: ["pl"],         tasks: ["affinity"],                          status: "integrated", rows: 23_496,     picked: 18_211,    updated: "2026-04-11", tier: "authoritative", desc: "Co-crystal complexes with measured affinity. Gold standard for structure-aware DTA." },
    { id: "biolip",       name: "BioLiP2",           kind: "structure", partners: ["pl"],         tasks: ["affinity", "interaction"],          status: "integrated", rows: 711_204,    picked: 88_004,    updated: "2026-04-11", tier: "authoritative", desc: "Biologically relevant ligand-protein complexes from PDB." },
    { id: "intact",       name: "IntAct",           kind: "ppi",        partners: ["pp"],         tasks: ["interaction"],                       status: "integrated", rows: 1_204_887,  picked: 0,         updated: "2026-04-11", tier: "authoritative", desc: "EBI's curated PPI archive." },
    { id: "biogrid",      name: "BioGRID",          kind: "ppi",        partners: ["pp"],         tasks: ["interaction"],                       status: "integrated", rows: 2_488_103,  picked: 0,         updated: "2026-04-11", tier: "authoritative", desc: "Genetic + protein interactions across model organisms." },
    { id: "string",       name: "STRING",           kind: "ppi",        partners: ["pp"],         tasks: ["interaction"],                       status: "integrated", rows: 11_402_000, picked: 0,         updated: "2026-04-11", tier: "authoritative", desc: "Functional + physical interactions with confidence scores." },
    { id: "skempi",       name: "SKEMPI 2.0",       kind: "mutation",   partners: ["pp"],         tasks: ["affinity"],                          status: "integrated", rows: 7_085,      picked: 0,         updated: "2026-04-11", tier: "authoritative", desc: "PPI binding-affinity changes on mutation. Niche but strong for ΔΔG." },
    { id: "uniprot",      name: "UniProt / TrEMBL", kind: "sequence",   partners: ["pl","pp","pna"], tasks: ["unsupervised"],                  status: "integrated", rows: 262_440_545, picked: 102_488, updated: "2026-04-11", tier: "authoritative", desc: "The canonical protein sequence + annotation database." },
    { id: "alphafold",    name: "AlphaFold DB v2",  kind: "structure",  partners: ["pl","pp","pna"], tasks: ["unsupervised", "interaction", "affinity"], status: "integrated", rows: 214_000_000, picked: 60_001, updated: "2026-04-11", tier: "authoritative", desc: "Predicted structures for ~all known proteins. Best gap-filler." },
    { id: "rcsb",         name: "RCSB / PDBe",      kind: "structure",  partners: ["pl","pp","pna"], tasks: ["unsupervised", "interaction", "affinity"], status: "integrated", rows: 234_767, picked: 96_201, updated: "2026-04-11", tier: "authoritative", desc: "Experimental structures (X-ray, cryo-EM, NMR)." },
    { id: "chebi",        name: "ChEBI",            kind: "ligand",     partners: ["pl"],         tasks: ["unsupervised"],                      status: "integrated", rows: 188_002,    picked: 188_002,   updated: "2026-04-11", tier: "authoritative", desc: "Chemical Entities of Biological Interest — curated ontology + structures." },

    // ── Planned (warehouse ingestion not done yet) ────────────────────
    { id: "drugbank",     name: "DrugBank",          kind: "assay",     partners: ["pl"],         tasks: ["affinity", "interaction"],          status: "planned", rows: 15_201,     picked: 0, updated: "—", tier: "authoritative", desc: "Approved + investigational drugs with curated targets. Strong on FDA-approved." },
    { id: "gtopdb",       name: "GtoPdb (IUPHAR)",   kind: "assay",     partners: ["pl"],         tasks: ["affinity"],                          status: "integrated", rows: 23_904,     picked: 0, updated: "2026-05-14", tier: "authoritative", desc: "IUPHAR/BPS Guide to Pharmacology — high-confidence, expert-curated. 23.9 K binding interactions across 2.6 K UniProt targets; 78 % carry pK* affinities." },
    { id: "pdsp",         name: "PDSP Ki Database",  kind: "assay",     partners: ["pl"],         tasks: ["affinity"],                          status: "planned", rows: 76_400,     picked: 0, updated: "—", tier: "authoritative", desc: "Psychoactive Drug Screening Program — clean Ki values, mostly GPCR/transporters." },
    { id: "davis",        name: "Davis (DTA benchmark)", kind: "benchmark", partners: ["pl"],     tasks: ["affinity"],                          status: "integrated", rows: 30_056,     picked: 0, updated: "2026-05-14", tier: "authoritative", desc: "Davis 2011 — 442 kinases × 68 inhibitors, Kd in nM, normalized to pKd. The canonical DeepDTA training benchmark." },
    { id: "kiba",         name: "KIBA (DTA benchmark)",  kind: "benchmark", partners: ["pl"],     tasks: ["affinity"],                          status: "integrated", rows: 118_253,    picked: 0, updated: "2026-05-14", tier: "authoritative", desc: "Tang 2014 — 229 proteins × 2,111 ligands. Integrated KI/Kd/IC50 'KIBA score' kept raw for ranking-style models." },
    { id: "huri",         name: "HuRI",              kind: "ppi",      partners: ["pp"],         tasks: ["interaction"],                       status: "integrated", rows: 52_068,     picked: 0, updated: "2026-05-14", tier: "authoritative", desc: "Human Reference Interactome — 52.1 K systematically-tested binary Y2H PPIs across Ensembl gene pairs." },
    { id: "hippie",       name: "HIPPIE",            kind: "ppi",      partners: ["pp"],         tasks: ["interaction"],                       status: "integrated", rows: 1_044_882,  picked: 0, updated: "2026-05-14", tier: "authoritative", desc: "Integrated, scored human PPI dataset. 1.04 M unique Entrez-gene-pair interactions with confidence scores in [0,1] from MITAB merge of HPRD / BioGRID / IntAct / MINT and more." },
    { id: "corum",        name: "CORUM",             kind: "ppi",      partners: ["pp"],         tasks: ["interaction"],                       status: "planned", rows: 9_812,      picked: 0, updated: "—", tier: "authoritative", desc: "Mammalian protein complexes (curated, mass-spec-backed)." },
    { id: "3did",         name: "3did",              kind: "ppi",      partners: ["pp"],         tasks: ["interaction"],                       status: "integrated", rows: 20_644,     picked: 0, updated: "2026-05-14", tier: "authoritative", desc: "Domain–domain interactions observed in PDB. 20.6 K unique Pfam-Pfam DDIs supported by 1.01 M PDB observations." },
    { id: "pdb_redo",     name: "PDB-REDO",          kind: "structure", partners: ["pl","pp","pna"], tasks: ["unsupervised", "interaction", "affinity"], status: "planned", rows: 198_400, picked: 0, updated: "—", tier: "authoritative", desc: "Automatically re-refined PDB entries — cleaner geometry than raw RCSB." },
    { id: "alphafill",    name: "AlphaFill",         kind: "structure", partners: ["pl"],         tasks: ["unsupervised", "interaction"],     status: "planned", rows: 86_440,     picked: 0, updated: "—", tier: "authoritative", desc: "AlphaFold models with cofactors + ligands transplanted from PDB homologs." },
    { id: "scpdb",        name: "sc-PDB",            kind: "structure", partners: ["pl"],         tasks: ["unsupervised"],                     status: "planned", rows: 17_280,     picked: 0, updated: "—", tier: "authoritative", desc: "Druggable binding-site annotations on PDB structures." },
    { id: "pubchem",      name: "PubChem (compounds)", kind: "ligand",  partners: ["pl"],         tasks: ["unsupervised"],                     status: "planned", rows: 119_000_000, picked: 0, updated: "—", tier: "authoritative", desc: "NIH's compound archive. Huge; use only the subset linked to bioactivity." },
    { id: "zinc",         name: "ZINC22",            kind: "ligand",    partners: ["pl"],         tasks: ["unsupervised"],                     status: "future",  rows: 38_600_000_000, picked: 0, updated: "—", tier: "authoritative", desc: "Purchasable compound space for virtual screening; not for training labels." },
    { id: "opentargets",  name: "Open Targets",      kind: "annotation", partners: ["pl","pp"],   tasks: ["interaction"],                       status: "planned", rows: 5_812_400,  picked: 0, updated: "—", tier: "authoritative", desc: "Target–disease associations + evidence; useful for label stratification." },
    { id: "pharos",       name: "Pharos / IDG",      kind: "annotation", partners: ["pl"],        tasks: ["interaction"],                       status: "planned", rows: 21_810,     picked: 0, updated: "—", tier: "authoritative", desc: "NIH Illuminating the Druggable Genome — target development levels." },
    { id: "ttd",          name: "TTD",               kind: "annotation", partners: ["pl"],        tasks: ["interaction"],                       status: "planned", rows: 53_004,     picked: 0, updated: "—", tier: "beta",          desc: "Therapeutic Target Database — clinical-stage targets + drugs." },
  ],

  // Active project / run context for the prototype
  project: {
    name: "KinaseCore-v3",
    owner: "rosa.kw",
    created: "2026-05-02",
    description: "Pan-kinase Kd predictor with cross-protein generalization holdout."
  },

  run: {
    id: "run_4192_kc3",
    state: "training", // idle | training | done | failed
    started: "2026-05-13 09:14 UTC",
    eta: "12m 04s",
    epoch: 18,
    epochs: 40,
    gpu: "A100 80G ×2",
    cost_so_far: 4.71,
    cost_est_total: 9.82,
    batch: 96,
    lr: 3e-4,
  },

  // Leakage groups discovered during split-design
  // Pre-clustered leakage groups. `axis` flags whether the cluster was
  // formed on the protein side (MMseqs2 sequence identity), the ligand
  // side (ECFP4 Tanimoto), or as a joint protein × ligand hit. The
  // Splits-screen cluster-axis toggle filters edges by this field.
  leakage_groups: [
    { id: "lg-001", n: 1284, kind: "kinase ATP-pocket cluster",  axis: "protein", risk: "high", residues: 38, similarity: 0.91 },
    { id: "lg-002", n:  642, kind: "SH2 domain phosphopeptide",   axis: "protein", risk: "med",  residues: 18, similarity: 0.74 },
    { id: "lg-003", n:  411, kind: "GPCR class A orthosteric",    axis: "protein", risk: "low",  residues: 22, similarity: 0.51 },
    { id: "lg-004", n:  308, kind: "Serine protease catalytic",   axis: "protein", risk: "med",  residues: 14, similarity: 0.68 },
    { id: "lg-005", n:  221, kind: "Type-II kinase scaffold",     axis: "ligand",  risk: "high", residues: 19, similarity: 0.88 },
    { id: "lg-006", n:  187, kind: "HSP90 N-term ATPase",         axis: "protein", risk: "med",  residues: 24, similarity: 0.71 },
    { id: "lg-007", n:  142, kind: "BTK + Bruton-ibrutinib pair", axis: "joint",   risk: "low",  residues: 28, similarity: 0.49 },
    { id: "lg-008", n:   96, kind: "Quinazoline scaffold",        axis: "ligand",  risk: "med",  residues: 16, similarity: 0.65 },
    { id: "lg-009", n:   84, kind: "Aspartic protease flap",      axis: "protein", risk: "low",  residues: 12, similarity: 0.42 },
    { id: "lg-010", n:   62, kind: "Macrocyclic peptide ligand",  axis: "ligand",  risk: "low",  residues: 11, similarity: 0.39 },
    { id: "lg-011", n:   44, kind: "EGFR-erlotinib joint",        axis: "joint",   risk: "med",  residues: 14, similarity: 0.61 },
  ],

  // Hyperparameter / model choices
  featurizers: {
    protein: [
      { id: "esm2-650m",   name: "ESM-2 (650M)",   kind: "language", embed: 1280, cost: 1.0, picked: true },
      { id: "esm3-1.4b",   name: "ESM-3 (1.4B)",   kind: "language", embed: 1536, cost: 2.4, picked: false },
      { id: "saprot",      name: "SaProt-650M",    kind: "structure-aware", embed: 1280, cost: 1.3, picked: false },
      { id: "prot-bert",   name: "ProtBERT",       kind: "language", embed: 1024, cost: 0.7, picked: false },
      { id: "onehot-seq",  name: "One-hot",        kind: "baseline", embed: 21,   cost: 0.05, picked: false },
    ],
    ligand: [
      { id: "ecfp4-2048",  name: "ECFP4 (2048)",   kind: "fingerprint", dim: 2048, cost: 0.05, picked: false },
      { id: "molformer",   name: "MolFormer-XL",   kind: "language",    dim: 768,  cost: 0.6, picked: true },
      { id: "chemberta",   name: "ChemBERTa-77M",  kind: "language",    dim: 384,  cost: 0.3, picked: false },
      { id: "gin-zinc",    name: "GIN (ZINC pretr.)", kind: "graph",    dim: 300,  cost: 0.4, picked: false },
      { id: "unimol-v2",   name: "Uni-Mol v2",     kind: "3d",          dim: 512,  cost: 1.1, picked: false },
    ],
  },
  architectures: [
    { id: "cross-attn", name: "Cross-attention DTA", desc: "Protein↔ligand cross-attention with bilinear head", picked: true, params: "62M" },
    { id: "siamese",    name: "Siamese tower",       desc: "Independent encoders, late fusion (concat + MLP)", picked: false, params: "41M" },
    { id: "graph-bind", name: "GraphBind",           desc: "Structure-conditioned residue GNN + ligand graph", picked: false, params: "88M" },
    { id: "deeppurpose", name: "DeepPurpose CNN",    desc: "1D protein CNN + ligand CNN baseline",            picked: false, params: "12M" },
  ],

  // Training metrics — synthetic but reasonable
  training: {
    epochs: Array.from({ length: 40 }, (_, i) => {
      const x = i + 1;
      const train = 0.94 * Math.exp(-x / 11) + 0.22 + 0.01 * Math.sin(x);
      const val   = 0.96 * Math.exp(-x / 10) + 0.31 + 0.018 * Math.cos(x * 0.7);
      const r =   1 - 0.78 * Math.exp(-x / 9) - 0.005 * Math.sin(x);
      return { epoch: x, train_loss: train, val_loss: val, val_r2: r };
    }),
    // baseline (cross-attn previous run)
    baseline: Array.from({ length: 40 }, (_, i) => {
      const x = i + 1;
      const val = 0.96 * Math.exp(-x / 8) + 0.38 + 0.02 * Math.cos(x * 0.4);
      return { epoch: x, val_loss: val };
    })
  },

  // Result fixtures
  metrics: {
    rmse: 0.612, mae: 0.471, pearson: 0.872, spearman: 0.851, r2: 0.761,
    rmse_delta: -0.043, pearson_delta: +0.018,
    test_n: 18_402,
  },

  // ROC / PR / calibration sample points
  roc: Array.from({ length: 60 }, (_, i) => {
    const x = i / 60;
    return { fpr: x, tpr: Math.min(1, Math.pow(x, 0.35) + 0.04 * Math.sin(i)) };
  }),
  calibration: Array.from({ length: 10 }, (_, i) => {
    const x = (i + 0.5) / 10;
    return { pred: x, actual: Math.min(0.99, x + (i < 6 ? -0.04 : 0.03) + 0.02 * Math.sin(i)) };
  }),

  // Per-target error
  perTarget: [
    { uniprot: "P00533", name: "EGFR",       n: 2104, rmse: 0.51, bias: -0.04, status: "ok" },
    { uniprot: "P04637", name: "TP53",       n:  187, rmse: 0.94, bias: +0.21, status: "high-error" },
    { uniprot: "Q9Y233", name: "PDE10A",     n:  912, rmse: 0.62, bias: -0.02, status: "ok" },
    { uniprot: "P28482", name: "MAPK1",      n: 1488, rmse: 0.49, bias: -0.01, status: "ok" },
    { uniprot: "O60674", name: "JAK2",       n:  870, rmse: 0.58, bias: +0.06, status: "ok" },
    { uniprot: "P11362", name: "FGFR1",      n:  604, rmse: 0.72, bias: +0.11, status: "drift" },
    { uniprot: "P36888", name: "FLT3",       n:  421, rmse: 0.83, bias: +0.18, status: "high-error" },
    { uniprot: "P00519", name: "ABL1",       n:  988, rmse: 0.54, bias: -0.03, status: "ok" },
    { uniprot: "Q06187", name: "BTK",        n:  502, rmse: 0.67, bias: +0.07, status: "drift" },
    { uniprot: "P15056", name: "BRAF",       n:  712, rmse: 0.69, bias: +0.05, status: "ok" },
  ],

  // Recent runs / registry
  runs: [
    { id: "run_4192_kc3", name: "KinaseCore-v3", arch: "cross-attn", pearson: 0.872, rmse: 0.612, state: "training", started: "today",       gpu: "A100×2", cost: 9.82, kind: "regression" },
    { id: "run_4187_kc3", name: "kc3 — siamese baseline", arch: "siamese",   pearson: 0.831, rmse: 0.694, state: "done",     started: "2d ago",     gpu: "A100×1", cost: 4.10, kind: "regression" },
    { id: "run_4181_kc3", name: "kc3 — esm3 sweep #4",    arch: "cross-attn", pearson: 0.864, rmse: 0.622, state: "done",     started: "3d ago",     gpu: "A100×2", cost: 11.91, kind: "regression" },
    { id: "run_4172_gp1", name: "GPCR-pan-v1",            arch: "graph-bind", pearson: 0.788, rmse: 0.812, state: "done",     started: "5d ago",     gpu: "H100×1", cost: 14.02, kind: "regression" },
    { id: "run_4168_kc2", name: "KinaseCore-v2 (prod)",   arch: "cross-attn", pearson: 0.854, rmse: 0.641, state: "done",     started: "11d ago",    gpu: "A100×2", cost: 8.40, kind: "regression", tag: "prod" },
    { id: "run_4151_kc3", name: "kc3 — ablation no-3D",   arch: "cross-attn", pearson: 0.812, rmse: 0.708, state: "failed",   started: "14d ago",    gpu: "A100×2", cost: 2.10, kind: "regression" },
  ],

  // Sequence fixture — kinase-domain neighbourhood of BTK (UniProt Q06187),
  // residues ~410–525. The actual amino-acid string below is *illustrative
  // only* (a synthetic 116-residue placeholder); annotations use real BTK
  // residue numbering so the gatekeeper (T474), hinge (E475–M477) and
  // catalytic-loop HRD motif (≈D521) land at sane positions in the window.
  sequence: {
    uniprot: "Q06187",
    name: "BTK (illustrative residues 410–525)",
    illustrative: true,
    range: [410, 525],
    seq: "GAVKLKEPMEQDDGAFLLRKGGRFLIRENQQHYTYPVQVAPDVATYVRRSLEVDIPGDGSWQEGKLLNLRGSWVSAFMDYLERHGCTKLPSTTGAVKLKEPMEQDDGAFLLRKGG",
    annotations: [
      { from: 412, to: 418, kind: "ATP",  label: "P-loop (GxGxxG)" },
      { from: 474, to: 474, kind: "Gate", label: "Gatekeeper T474" },
      { from: 475, to: 477, kind: "Hinge", label: "Hinge E475–M477" },
      { from: 481, to: 481, kind: "Cys",  label: "Cys481 (covalent target)" },
      { from: 519, to: 522, kind: "HRD",  label: "Catalytic loop HRD (~D521)" },
    ]
  },

  // ───────── v2 additions ─────────

  // 5-tier catalog. 152 options total in production; we show a representative slice.
  catalog: {
    counts: { release: 68, beta: 52, beta_soon: 6, lab: 19, planned_inactive: 7 },
    options: [
      // task_types
      { id: "task.protein-protein",       group: "Task type",            label: "Protein–protein affinity",   tier: "release" },
      { id: "task.protein-ligand",        group: "Task type",            label: "Protein–ligand affinity",    tier: "beta" },
      { id: "task.protein-nucleic-acid",  group: "Task type",            label: "Protein–nucleic acid",       tier: "lab" },
      // model_families
      { id: "mf.multimodal_fusion",       group: "Model family",         label: "Multimodal fusion",          tier: "release" },
      { id: "mf.cross_attention",         group: "Model family",         label: "Cross-attention DTA",        tier: "release" },
      { id: "mf.graphsage",               group: "Model family",         label: "GraphSAGE",                  tier: "beta" },
      { id: "mf.edge_message_passing",    group: "Model family",         label: "Edge message passing",       tier: "lab" },
      { id: "mf.cnn",                     group: "Model family",         label: "1D CNN (DeepPurpose)",       tier: "planned_inactive", blocked_reason: "Replaced by transformer-based encoders. Keep older runs reproducible but not for new benchmarks." },
      // split_strategies
      { id: "sp.leakage_resistant",       group: "Split strategy",       label: "Leakage-aware cluster",      tier: "release" },
      { id: "sp.scaffold",                group: "Split strategy",       label: "Scaffold (ligand)",          tier: "release" },
      { id: "sp.cold_target",             group: "Split strategy",       label: "Cold target",                tier: "release" },
      { id: "sp.random",                  group: "Split strategy",       label: "Random",                     tier: "planned_inactive", blocked_reason: "Random splits inflate metrics 5–15 points on protein-binding benchmarks. Use only for sanity ablations — and even then, tag the run as such." },
      // preprocessing_modules
      { id: "pp.pyrosetta",               group: "Preprocessing",        label: "PyRosetta interface",        tier: "beta_soon", blocked_reason: "Native Rosetta runtime not installed on the cluster. Owner: infra@anvil. ETA: warehouse v2026.05.", reviewers: ["mira.s", "felix.t"] },
      { id: "pp.free_state",              group: "Preprocessing",        label: "Free-state comparison",      tier: "beta_soon", blocked_reason: "Materialization contract needs a scientific review (which delta to compute, which baseline). Owner: rosa.kw.", reviewers: ["rosa.kw"] },
      { id: "pp.standard_clean",          group: "Preprocessing",        label: "Standard cleanup",           tier: "release" },
      // dataset_refs
      { id: "ds.governed_blended_v2",     group: "Dataset reference",    label: "Governed PPI blended v2",    tier: "beta" },
      { id: "ds.governed_stage2",         group: "Dataset reference",    label: "Governed PPI Stage-2 candidate", tier: "beta_soon", blocked_reason: "Stage-2 candidate set is awaiting governance review on three of its source partitions. Owner: governance@anvil.", reviewers: ["governance"] },
      { id: "ds.governed_external_beta",  group: "Dataset reference",    label: "Governed PPI external-beta", tier: "beta_soon", blocked_reason: "External-beta candidate subset is gated pending data-use agreement renewal." },
      // architectures
      { id: "ar.cross_attn",              group: "Architecture",         label: "Cross-attention DTA",        tier: "release" },
      { id: "ar.siamese",                 group: "Architecture",         label: "Siamese tower",              tier: "release" },
      { id: "ar.graph_bind",              group: "Architecture",         label: "GraphBind",                  tier: "beta" },
      { id: "ar.deeppurpose",             group: "Architecture",         label: "DeepPurpose CNN",            tier: "planned_inactive", blocked_reason: "Superseded by transformer baselines. Will be removed in v2026.07 except for historical reproducibility." },
      // structure_source_policies
      { id: "ss.experimental_only",       group: "Structure source",     label: "Experimental only (PDB)",    tier: "release" },
      { id: "ss.experimental_or_predicted",group: "Structure source",    label: "Experimental or AlphaFold",  tier: "release" },
      { id: "ss.predicted_allowed",       group: "Structure source",     label: "Predicted allowed (lax)",    tier: "lab" },
      // evaluation_presets
      { id: "ev.regression_standard",     group: "Evaluation preset",    label: "Regression — standard",      tier: "release" },
      { id: "ev.ranking_focus",           group: "Evaluation preset",    label: "Ranking-focused metrics",    tier: "planned_inactive", blocked_reason: "Ranking-only evaluation drops calibration which we need for downstream triage decisions. Reopen if calibration is re-introduced." },
    ],
  },

  // Real-shaped dropped-rows breakdown for the Dataset preview funnel.
  preview: {
    totals: { candidates: 1796, after_filters: 598, final_selected: 192 },
    drop_reason_breakdown: {
      resolution:        17,
      missing_structure: 566,
      redundancy:        287,
      assay_quality:     124,
      activity_range:     21,
      organism_mismatch:   8,
    },
    drop_source_breakdown: [
      { src: "PDBbind v2020",            resolution: 17,  missing_structure: 380, redundancy: 81, assay_quality: 12 },
      { src: "Affinity Benchmark v5.5",  resolution: 0,   missing_structure: 19,  redundancy: 8,  assay_quality: 4 },
      { src: "SKEMPI v2.0",              resolution: 0,   missing_structure: 49,  redundancy: 6,  assay_quality: 11 },
      { src: "SAbDab",                   resolution: 0,   missing_structure: 29,  redundancy: 0,  assay_quality: 0 },
      { src: "BindingDB (filtered)",     resolution: 0,   missing_structure: 89,  redundancy: 192,assay_quality: 97 },
    ],
    // Sample of dropped rows. Real shape: "PDB:reason" tokens, expanded.
    dropped_rows: [
      { id: "1FFX", source: "PDBbind v2020",  reason: "resolution",       detail: "3.4 Å (cutoff 2.5)" },
      { id: "2ONL", source: "PDBbind v2020",  reason: "resolution",       detail: "3.1 Å" },
      { id: "4HMY", source: "PDBbind v2020",  reason: "resolution",       detail: "2.9 Å" },
      { id: "5KMV", source: "PDBbind v2020",  reason: "missing_structure",detail: "no ligand chain after cleanup" },
      { id: "3K1H", source: "SKEMPI v2.0",    reason: "missing_structure",detail: "mutant unmodeled" },
      { id: "2QWE", source: "PDBbind v2020",  reason: "redundancy",       detail: "kept 2QWF (same seq, better resolution)" },
      { id: "6ABC", source: "SAbDab",         reason: "missing_structure",detail: "epitope chain not resolved" },
      { id: "4XYZ", source: "BindingDB",      reason: "assay_quality",    detail: "confidence 4 (need ≥6)" },
      { id: "3PQR", source: "BindingDB",      reason: "redundancy",       detail: "duplicate of 3PQS — kept higher conf." },
      { id: "1ATN", source: "Aff. Bench 5.5", reason: "missing_structure",detail: "antibody mismatch in chain map" },
    ],
    sources_kept: [
      { src: "BindingDB",        kept: 109, share: 56.8 },
      { src: "ChEMBL 36",        kept:  41, share: 21.4 },
      { src: "PDBbind v2020",    kept:  28, share: 14.6 },
      { src: "BioLiP2",          kept:  10, share:  5.2 },
      { src: "Affinity Bench 5.5",kept:   4, share:  2.0 },
    ],
  },

  // Validator output — drives Recommendations / Blocker cards across screens.
  validator: {
    status: "blocked",
    items: [
      {
        level: "blocker",
        category: "preprocess_dependency",
        message: "Atom-level graphs require the `atom` node granularity.",
        action:  "Switch node granularity to `atom`, or pick a residue-level graph recipe.",
        related_fields: ["pipeline.graph_recipes", "pipeline.granularity"],
        location: "pipeline",
      },
      {
        level: "blocker",
        category: "beta_catalog",
        message: "PyRosetta preprocessing is gated until the native runtime ships (Coming soon).",
        action:  "Drop PyRosetta from preprocessing, or wait for warehouse v2026.05.",
        related_fields: ["pipeline.preprocessing"],
        location: "pipeline",
      },
      {
        level: "warning",
        category: "governed_subset_scope",
        message: "Cold-target test set covers only 6.8% of distinct targets.",
        action:  "Consider expanding the cold-target fraction to ≥10% for stronger generalization claims.",
        related_fields: ["split.cold_target_pct"],
        location: "split",
      },
      {
        level: "warning",
        category: "calibration",
        message: "ECE of 0.041 trends high at the upper tail (pKi ≥ 8).",
        action:  "Apply isotonic recalibration on the validation set before promoting.",
        related_fields: ["results.calibration"],
        location: "results",
      },
      {
        level: "info",
        category: "cost",
        message: "Embedding cache hit rate is 94% — staying on ESM-2 650M is free at this rate.",
        action:  "No change needed.",
        related_fields: ["pipeline.protein_featurizer"],
        location: "pipeline",
      },
    ],
  },

  // Hyperparameter sweep config (Pipeline → Sweep mode)
  sweep: {
    sampler: "Bayesian (TPE)",
    pruner:  "Median (warmup 5 epochs)",
    n_trials: 24,
    n_seeds: 3,
    per_trial_cost_usd: 4.10,
    space: [
      { param: "learning_rate",  kind: "log_uniform", lo: 1e-5, hi: 1e-3, current: "3e-4" },
      { param: "batch_size",     kind: "categorical", values: [32, 64, 96, 128] },
      { param: "weight_decay",   kind: "log_uniform", lo: 1e-4, hi: 1e-1, current: "0.01" },
      { param: "dropout",        kind: "uniform",     lo: 0.0,  hi: 0.3,   current: "0.10" },
      { param: "warmup_steps",   kind: "int_uniform", lo: 0,    hi: 2000,  current: "1000" },
      { param: "loss.huber_delta",kind:"uniform",     lo: 0.1,  hi: 0.6,   current: "0.30" },
    ],
  },

  // Multi-objective training: produce more than one head.
  // Generate two extra synthetic series.
  multiobj: [
    { id: "pki",       label: "Affinity (pKi · RMSE)", color: "var(--primary)",   yMax: 1.2, yMin: 0.2 },
    { id: "selectivity", label: "Off-target selectivity (AUC)", color: "var(--molecular)", yMax: 1.0, yMin: 0.5, invert: true },
    { id: "binary",    label: "Binder vs non-binder (AUC)",     color: "var(--signal)",    yMax: 1.0, yMin: 0.5, invert: true },
  ],

  // Promote screen
  promote: {
    candidate_run: "run_4192_kc3",
    current_prod:  "run_4168_kc2",
    reviewers: [
      { id: "mira.s",  name: "Mira S.",   role: "ML lead",        avatar: "MS", status: "approved",         when: "1h ago" },
      { id: "felix.t", name: "Felix T.",  role: "Chemistry",      avatar: "FT", status: "reviewing",        when: "in review" },
      { id: "anya.k",  name: "Anya K.",   role: "Governance",     avatar: "AK", status: "requested",        when: "—" },
      { id: "owen.r",  name: "Owen R.",   role: "Bench biology",  avatar: "OR", status: "changes-requested",when: "23h ago" },
    ],
    gates: [
      { id: "g1", label: "Pearson ≥ 0.85 on held-out test",       status: "pass",  detail: "0.872" },
      { id: "g2", label: "ECE ≤ 0.05",                            status: "pass",  detail: "0.041" },
      { id: "g3", label: "No high-risk overlap warnings",         status: "pass",  detail: "all resolved" },
      { id: "g4", label: "Leakage-aware split policy",            status: "pass",  detail: "cluster · sim 0.65" },
      { id: "g5", label: "Per-target RMSE ≤ 0.85",                status: "fail",  detail: "TP53 0.94, FLT3 0.83" },
      { id: "g6", label: "All blocker validator items resolved",  status: "fail",  detail: "2 open" },
      { id: "g7", label: "Cost ≤ $15 / run",                      status: "pass",  detail: "$9.82" },
      { id: "g8", label: "At least one bench-biology reviewer",   status: "wait",  detail: "Owen R. requested changes" },
    ],
    comments: [
      { who: "Mira S.",  when: "1h ago",  text: "LGTM — the calibration tail is a known issue, recalibrating offline before serving." },
      { who: "Owen R.",  when: "23h ago", text: "TP53 RMSE is a non-starter for our oncology screen. Can we hold off until we add the synthetic TP53 panel?", flag: "changes-requested" },
      { who: "Felix T.", when: "1d ago",  text: "Looking at the attention attribution now — covalent warhead inference looks sane on BTK." },
      { who: "rosa.kw",  when: "1d ago",  text: "Requesting promotion. Diff vs v2 prod attached.", flag: "request" },
    ],
    audit: [
      { ts: "2026-05-13 09:14", who: "rosa.kw", action: "requested promotion of run_4192_kc3" },
      { ts: "2026-05-11 14:02", who: "mira.s",  action: "promoted run_4168_kc2 to prod (current)" },
      { ts: "2026-04-29 10:50", who: "anya.k",  action: "demoted run_4151_kc3 (data error in source ETL)" },
      { ts: "2026-04-12 07:39", who: "system",  action: "warehouse v2026.04 published" },
    ],
  },

  // Warehouse releases (for the Releases tab on Reference library)
  releases: [
    { id: "v2026.05",            published: "2026-05-09 18:22", current: false, status: "available",
      delta: { sources_added: 3, rows_added: 4_215_891, families_changed: 7, leakage_groups_added: 2 } },
    { id: "v2026.04",            published: "2026-04-12 07:39", current: true,  status: "current",
      delta: { sources_added: 0, rows_added:    49_204, families_changed: 1, leakage_groups_added: 0 } },
    { id: "v2026.03",            published: "2026-03-30 22:15", current: false, status: "archived",
      delta: { sources_added: 1, rows_added: 1_204_887, families_changed: 4, leakage_groups_added: 1 } },
    { id: "v2026.02",            published: "2026-02-18 11:00", current: false, status: "archived",
      delta: { sources_added: 0, rows_added:   312_004, families_changed: 2, leakage_groups_added: 0 } },
  ],

  // Reference library — proteins tab (sample of 12)
  proteins_sample: [
    { uniprot: "P00533", name: "EGFR",   organism: "H. sapiens", len: 1210, pdbs: 142, family: "Tyrosine kinase",      tier: "release" },
    { uniprot: "Q06187", name: "BTK",    organism: "H. sapiens", len: 659,  pdbs:  42, family: "Tec kinase",           tier: "release" },
    { uniprot: "P00519", name: "ABL1",   organism: "H. sapiens", len: 1130, pdbs:  86, family: "Tyrosine kinase",      tier: "release" },
    { uniprot: "P11362", name: "FGFR1",  organism: "H. sapiens", len:  822, pdbs:  64, family: "Tyrosine kinase",      tier: "beta" },
    { uniprot: "P15056", name: "BRAF",   organism: "H. sapiens", len:  766, pdbs:  78, family: "Ser/Thr kinase",       tier: "release" },
    { uniprot: "P04637", name: "TP53",   organism: "H. sapiens", len:  393, pdbs:  31, family: "Transcription factor", tier: "lab" },
    { uniprot: "O60674", name: "JAK2",   organism: "H. sapiens", len: 1132, pdbs:  58, family: "Tyrosine kinase",      tier: "release" },
    { uniprot: "P28482", name: "MAPK1",  organism: "H. sapiens", len:  360, pdbs: 102, family: "Ser/Thr kinase",       tier: "release" },
    { uniprot: "P36888", name: "FLT3",   organism: "H. sapiens", len:  993, pdbs:  41, family: "Tyrosine kinase",      tier: "release" },
    { uniprot: "Q9Y233", name: "PDE10A", organism: "H. sapiens", len:  779, pdbs:  37, family: "Phosphodiesterase",    tier: "release" },
    { uniprot: "Q92731", name: "ESR2",   organism: "H. sapiens", len:  530, pdbs:  29, family: "Nuclear receptor",     tier: "beta" },
    { uniprot: "P49841", name: "GSK3B",  organism: "H. sapiens", len:  420, pdbs:  64, family: "Ser/Thr kinase",       tier: "release" },
  ],

  // Stale-artifact banner state
  staleBanner: {
    pinnedTo:    "v2026.04",
    available:   "v2026.05",
    sources_added: 3,
    rows_added:  4_215_891,
    dataset_id:  "ds_kc3_v3",
  },

  // Pre-computed split-metric fixture surfaced on the Splits screen.
  split_metrics: { cold_target_pct: 6.8, cold_drug_pct: 4.2, cold_pair_pct: 1.1 },

  // Project-level design choice — what kind of question the model is meant
  // to answer. Picked on the Splits screen; carries through to Results /
  // Compare / Promote as a banner so a reader knows whether the metrics
  // are interpolation-context or extrapolation-context.
  design_objective: "generalization",
};

// ─────────────────────────────────────────────────────────────────────
// PS_DEEP_SETTINGS — opinionated catalog of "advanced options" surfaced
// in the Pipeline / Training / Inference advanced panels.
//
// Each setting is rendered by a single generic `<SettingRow>` driver in
// `components/shared-v2.jsx`. To add a new setting: drop a row in the
// matching panel array below. To wire it to the backend later: read
// from `tweaks.advanced[panel_key][setting_key]` on launch.
//
// Tier values use the same 5-lane catalog as PS_TIERS (release / beta /
// beta_soon / lab / planned_inactive).
// ─────────────────────────────────────────────────────────────────────
window.PS_DEEP_SETTINGS = {
  structure_preparation: {
    label: "Structure preparation",
    sub: "Energy minimisation, hydrogen placement, ligand 3D prep before featurization.",
    groups: {
      engine: {
        label: "Preparation engine",
        items: [
          { key: "prep_engine", label: "Preparation engine", type: "select", default: "pyrosetta", options: ["pyrosetta", "openmm", "rdkit_only", "openbabel", "schrodinger_prepwizard"], tooltip: "Backend that performs energy min and hydrogen addition; OpenMM is faster for big systems, Schrödinger requires a license.", tier: "release" },
          { key: "openmm_forcefield", label: "OpenMM force field", type: "select", default: "amber14-all", options: ["amber14-all", "amber99sbildn", "charmm36", "openff-2.2.0"], tooltip: "Force field for OpenMM minimization of the protein. amber14-all is the safe default; openff-2.2.0 (Sage) for ligand-friendly biomolecular work.", tier: "release", show_if: { prep_engine: "openmm" } },
          { key: "hydrogen_engine", label: "Hydrogen placement", type: "select", default: "reduce", options: ["reduce", "pdb2pqr", "openmm_modeller", "pymol"], tooltip: "Tool to add and optimize hydrogens; Reduce is the community standard.", tier: "release" },
        ],
      },
      rosetta_scorefxn: {
        label: "Rosetta — score function",
        items: [
          { key: "scorefxn", label: "Score function", type: "select", default: "ref2015", options: ["ref2015", "ref2015_cart", "beta_nov16", "beta_nov16_cart", "talaris2014", "franklin2019", "ref2015_soft", "ligand", "ligand_soft_rep"], tooltip: "Rosetta energy function. ref2015 is the modern default; franklin2019 for membrane proteins; beta_nov16(_cart) for cartesian-space work; ligand / ligand_soft_rep for the receptor–ligand interface.", tier: "release" },
          { key: "ligand_scorefxn", label: "Ligand score function", type: "select", default: "ligand", options: ["ligand", "ligand_soft_rep", "ref2015", "beta_nov16"], tooltip: "Separate scorefxn for the ligand–receptor interface; ligand.wts has tuned h-bond and electrostatic terms.", tier: "release" },
          { key: "scorefxn_weights_override", label: "Weight overrides", type: "chips", default: [], tooltip: "Override individual score terms, e.g. fa_rep:0.55 or coordinate_constraint:1.0.", tier: "beta" },
        ],
      },
      rosetta_relax: {
        label: "Rosetta — relax & minimization",
        items: [
          { key: "relax_protocol", label: "Relax protocol", type: "select", default: "FastRelax", options: ["FastRelax", "ClassicRelax", "MinPack", "Idealize", "CartesianRelax", "DualspaceRelax", "MinMover"], tooltip: "How the structure is energy-minimized; FastRelax is the standard production choice.", tier: "release" },
          { key: "relax_cycles", label: "Relax cycles", type: "int", default: 5, min: 1, max: 25, tooltip: "Number of ramp cycles in FastRelax; 5 is standard, 1 is fast preview, 15+ for publication-quality.", tier: "release" },
          { key: "relax_script", label: "Relax ramp script", type: "select", default: "MonomerRelax2019", options: ["MonomerRelax2019", "default", "no_ref", "rosettacon2018", "legacy"], tooltip: "Built-in repulsive-ramping schedule; MonomerRelax2019 is the current recommended.", tier: "release" },
          { key: "cartesian_relax", label: "Cartesian relax", type: "bool", default: false, tooltip: "Relax in xyz space (better geometry, slower) vs torsion space.", tier: "release" },
          { key: "constrain_to_start_coords", label: "Constrain to start coords", type: "bool", default: true, tooltip: "Adds harmonic restraints to Cα atoms so the structure doesn't drift during relax.", tier: "release" },
          { key: "coord_constraint_stdev", label: "Coord constraint stdev (Å)", type: "float", default: 0.5, min: 0.1, max: 3.0, step: 0.1, tooltip: "Width of harmonic well restraining atoms to start positions; smaller = stiffer.", tier: "release" },
          { key: "min_tolerance", label: "Min tolerance", type: "float", default: 0.0001, min: 0.000001, max: 0.01, step: 0.00001, tooltip: "Convergence threshold for the minimizer; smaller = tighter min, longer runtime.", tier: "beta" },
          { key: "min_type", label: "Minimizer", type: "select", default: "lbfgs_armijo_nonmonotone", options: ["lbfgs_armijo_nonmonotone", "lbfgs_armijo", "dfpmin_armijo", "dfpmin", "linmin"], tooltip: "Numerical minimizer; lbfgs_armijo_nonmonotone is the modern default.", tier: "release" },
        ],
      },
      rosetta_packer: {
        label: "Rosetta — packer",
        items: [
          { key: "use_linmem_ig", label: "Linear-memory interaction graph", type: "bool", default: true, tooltip: "Lower-memory packer for large systems; turn off for highest accuracy on small targets.", tier: "release" },
          { key: "soft_rep", label: "Soft repulsive packing", type: "bool", default: false, tooltip: "Use softened Lennard-Jones during packing to let rotamers escape clashes.", tier: "release" },
          { key: "ex1_ex2", label: "Rotamer sampling (ex1/ex2)", type: "select", default: "ex1_ex2", options: ["none", "ex1", "ex1_ex2", "ex1_ex2_ex3", "ex1_ex2aro"], tooltip: "Extra subrotamers around χ1/χ2; more = better packing but quadratic cost.", tier: "release" },
          { key: "dunbrack_prob", label: "Dunbrack prob cutoff", type: "float", default: 0.95, min: 0.5, max: 0.99, step: 0.01, tooltip: "Cumulative probability cutoff for including rotamers from the Dunbrack library.", tier: "beta" },
        ],
      },
      rosetta_constraints: {
        label: "Rosetta — constraints",
        items: [
          { key: "constraint_types", label: "Constraint types", type: "multi-select", default: ["CoordinateConstraint"], options: ["AtomPair", "CoordinateConstraint", "AngleConstraint", "DihedralConstraint", "AmbiguousConstraint", "SiteConstraint", "LocalCoordinateConstraint"], tooltip: "Geometric restraints during relax/dock.", tier: "release" },
          { key: "constraint_weight", label: "Constraint weight", type: "float", default: 1.0, min: 0.0, max: 10.0, step: 0.1, tooltip: "Global multiplier on constraint score terms.", tier: "release" },
        ],
      },
      pose_prep: {
        label: "Pose preparation",
        items: [
          { key: "idealize_pose", label: "Idealize bond geometry", type: "bool", default: false, tooltip: "Snap bond lengths/angles to ideal values before relax; useful for crystal structures with strain.", tier: "release" },
          { key: "renumber_chains", label: "Renumber chains sequentially", type: "bool", default: true, tooltip: "Renumber residues 1..N per chain so downstream tools agree.", tier: "release" },
          { key: "extract_chains", label: "Chains to keep", type: "chips", default: ["A"], tooltip: "Chain IDs to retain; others are stripped before training.", tier: "release" },
          { key: "mutations", label: "Point mutations", type: "chips", default: [], tooltip: "Mutations to apply, e.g. A123V; uses Rosetta MutateResidue mover.", tier: "beta" },
          { key: "missing_residue_rebuild", label: "Missing-residue rebuild", type: "select", default: "none", options: ["none", "loophash", "remodel", "alphafold", "esmfold"], tooltip: "Strategy to fill gaps; alphafold/esmfold give best loops, loophash is fastest.", tier: "beta_soon" },
        ],
      },
      ligand_prep: {
        label: "Ligand 3D preparation",
        items: [
          { key: "ligand_protonation", label: "Ligand protonation", type: "select", default: "dimorphite_dl", options: ["none", "dimorphite_dl", "openbabel", "epik"], tooltip: "Generates physiologically relevant protonation states for the ligand.", tier: "release" },
          { key: "ligand_ph", label: "Target pH", type: "float", default: 7.4, min: 1.0, max: 14.0, step: 0.1, tooltip: "pH used for ligand protonation enumeration.", tier: "release" },
          { key: "tautomer_enumeration", label: "Tautomer enumeration", type: "bool", default: true, tooltip: "Generate alternate tautomers with RDKit/MolVS before featurization.", tier: "release" },
          { key: "stereo_enumeration", label: "Stereo enumeration", type: "select", default: "undefined_only", options: ["none", "undefined_only", "all"], tooltip: "Expand stereocenters; 'undefined_only' fills in unspecified centers without exploding defined ones.", tier: "release" },
          { key: "conformer_generator", label: "Conformer generator", type: "select", default: "rdkit_etkdgv3", options: ["rdkit_etkdgv3", "rdkit_etkdg", "openeye_omega", "balloon", "crest_xtb"], tooltip: "Algorithm for generating 3D conformers; ETKDGv3 is the modern RDKit default. CREST/xTB is metadynamics-based and more thorough.", tier: "release" },
          { key: "n_conformers", label: "Conformers per ligand", type: "int", default: 20, min: 1, max: 500, tooltip: "Number of low-energy 3D conformers to retain.", tier: "release" },
          { key: "ligand_ff_minimization", label: "Ligand FF minimization", type: "select", default: "mmff94s", options: ["none", "uff", "mmff94", "mmff94s", "gfn2_xtb"], tooltip: "Force field for cleaning up ligand geometry; MMFF94s is the standard.", tier: "release" },
          { key: "partial_charges", label: "Partial charge model", type: "select", default: "am1_bcc", options: ["gasteiger", "mmff94", "am1_bcc", "resp", "espaloma"], tooltip: "Charge assignment; AM1-BCC is the docking/MD standard, Gasteiger is fastest.", tier: "release" },
          { key: "docking_pre_pose", label: "Pre-docking engine", type: "select", default: "none", options: ["none", "vina", "smina", "gnina", "glide_stub", "diffdock"], tooltip: "Optional docking pass to generate a starting bound pose; DiffDock is ML-based.", tier: "beta" },
        ],
      },
    },
  },

  featurizer_advanced: {
    label: "Featurizer advanced",
    sub: "Per-encoder knobs: which model, which layer, how to pool, how to bound memory.",
    groups: {
      esm2: {
        label: "ESM-2 (protein)",
        items: [
          { key: "esm2_variant", label: "Variant", type: "select", default: "esm2_t33_650M_UR50D", options: ["esm2_t6_8M_UR50D", "esm2_t12_35M_UR50D", "esm2_t30_150M_UR50D", "esm2_t33_650M_UR50D", "esm2_t36_3B_UR50D", "esm2_t48_15B_UR50D"], tooltip: "Model size; 650M is the production default, 3B/15B for high-stakes runs.", tier: "release" },
          { key: "esm2_layer_choice", label: "Layer extraction", type: "select", default: "last", options: ["last", "last_4_mean", "all_mean", "specific"], tooltip: "Which transformer layer(s) to pool; last_4_mean often beats just-last on downstream tasks.", tier: "release" },
          { key: "esm2_specific_layer", label: "Specific layer index", type: "int", default: 33, min: 1, max: 48, tooltip: "Used when layer extraction is 'specific'.", tier: "beta", show_if: { esm2_layer_choice: "specific" } },
          { key: "esm2_pooling", label: "Pooling", type: "select", default: "mean", options: ["mean", "cls", "max", "attention", "per_residue"], tooltip: "How to collapse the per-token tensor into a protein vector; per_residue keeps the full LxD matrix.", tier: "release" },
          { key: "esm2_token_chunk", label: "Max tokens per microbatch", type: "int", default: 8192, min: 512, max: 65536, tooltip: "Token budget per microbatch to bound GPU memory.", tier: "release" },
          { key: "esm2_grad_checkpoint", label: "Gradient checkpointing", type: "bool", default: false, tooltip: "Trades compute for memory when fine-tuning ESM-2.", tier: "release" },
        ],
      },
      saprot: {
        label: "SaProt / ESM-3 (structure-aware)",
        items: [
          { key: "use_structure_tokens", label: "Use structure tokens", type: "bool", default: true, tooltip: "Enable SaProt/ESM-3 structure-aware tokens (requires Foldseek 3Di tokens).", tier: "beta" },
          { key: "saprot_variant", label: "SaProt variant", type: "select", default: "SaProt_650M_AF2", options: ["SaProt_35M_AF2", "SaProt_650M_AF2", "SaProt_650M_PDB"], tooltip: "Pretrained checkpoint; AF2 variants generalize better, PDB is more crystal-structure faithful.", tier: "beta" },
          { key: "esm3_layer", label: "ESM-3 layer", type: "int", default: 47, min: 1, max: 48, tooltip: "Transformer layer to extract.", tier: "lab" },
        ],
      },
      mol_lm: {
        label: "Molecule encoder",
        items: [
          { key: "mol_encoder", label: "Encoder", type: "select", default: "molformer_xl", options: ["molformer_xl", "chemberta_77m_mtr", "chemberta_77m_mlm", "unimol_v2", "molclr", "grover_large"], tooltip: "Pretrained SMILES/graph encoder; MolFormer-XL is the strong default.", tier: "release" },
          { key: "mol_pooling", label: "Pooling", type: "select", default: "mean", options: ["mean", "cls", "max", "attention"], tooltip: "Token-pooling strategy for the molecule encoder.", tier: "release" },
          { key: "mol_max_len", label: "Max SMILES tokens", type: "int", default: 256, min: 64, max: 1024, tooltip: "Truncation limit for SMILES tokenization.", tier: "release" },
        ],
      },
      gnn: {
        label: "Graph neural network",
        items: [
          { key: "gnn_node_granularity", label: "Node granularity", type: "select", default: "atom", options: ["atom", "residue", "fragment"], tooltip: "Whether graph nodes represent atoms, residues, or BRICS fragments.", tier: "release" },
          { key: "gnn_edge_cutoff_a", label: "Edge cutoff (Å)", type: "float", default: 5.0, min: 2.0, max: 12.0, step: 0.5, tooltip: "Distance threshold for spatial edges in the protein/ligand graph.", tier: "release" },
          { key: "gnn_knn", label: "k-NN edges", type: "int", default: 16, min: 4, max: 64, tooltip: "Number of nearest neighbors per node (alternative to radius cutoff).", tier: "release" },
          { key: "gnn_rbf_bins", label: "Gaussian RBF bins", type: "int", default: 16, min: 4, max: 64, tooltip: "Number of radial basis functions used to encode interatomic distances.", tier: "release" },
          { key: "gnn_line_graph", label: "Line-graph (edge features)", type: "bool", default: false, tooltip: "Adds an edge-graph layer that messages over bond pairs; better for angles, slower.", tier: "beta" },
        ],
      },
      handcrafted: {
        label: "Hand-crafted descriptors",
        items: [
          { key: "ecfp_radius", label: "ECFP radius", type: "int", default: 2, min: 1, max: 4, tooltip: "Morgan fingerprint radius; 2 is ECFP4, 3 is ECFP6.", tier: "release" },
          { key: "ecfp_nbits", label: "ECFP bits", type: "select", default: 2048, options: [512, 1024, 2048, 4096, 8192], tooltip: "Folded fingerprint length.", tier: "release" },
          { key: "use_maccs", label: "MACCS keys", type: "bool", default: false, tooltip: "Concatenate 167-bit MACCS structural keys.", tier: "release" },
          { key: "use_mordred", label: "Mordred descriptors", type: "bool", default: false, tooltip: "Include ~1800 Mordred 2D/3D descriptors; high-dim, useful for tabular baselines.", tier: "beta" },
        ],
      },
      three_d: {
        label: "3D / pocket features",
        items: [
          { key: "voxel_grid_a", label: "Voxel grid (Å)", type: "float", default: 1.0, min: 0.5, max: 2.5, step: 0.1, tooltip: "Voxel edge length for 3D-CNN pocket featurizers.", tier: "beta" },
          { key: "voxel_box_a", label: "Voxel box size (Å)", type: "int", default: 24, min: 12, max: 48, tooltip: "Cubic side length of the voxel grid centered on the pocket.", tier: "beta" },
          { key: "contact_map_threshold_a", label: "Contact-map cutoff (Å)", type: "float", default: 8.0, min: 4.0, max: 15.0, step: 0.5, tooltip: "Distance below which a residue pair counts as a contact.", tier: "release" },
          { key: "se3_invariant_features", label: "SE(3)-invariant features", type: "bool", default: true, tooltip: "Use rotation/translation-invariant geometric features (distances, angles, dihedrals).", tier: "beta" },
          { key: "pairwise_distance_bins", label: "Pairwise distance bins", type: "int", default: 32, min: 8, max: 64, tooltip: "Number of bins for discretizing residue-atom distances in the interaction tensor.", tier: "beta" },
        ],
      },
    },
  },

  training_advanced: {
    label: "Training advanced",
    sub: "Architecture, loss, regularisation, optimiser, precision, distributed, reproducibility.",
    groups: {
      head: {
        label: "Head architecture",
        items: [
          { key: "head_type", label: "Head", type: "select", default: "cross_attention", options: ["mlp", "bilinear", "siamese_cosine", "cross_attention", "perceiver", "gated_fusion"], tooltip: "Top-of-stack module that fuses protein and ligand embeddings.", tier: "release" },
          { key: "cross_attn_heads", label: "Cross-attn heads", type: "int", default: 8, min: 1, max: 32, tooltip: "Number of attention heads in the fusion module.", tier: "release", show_if: { head_type: "cross_attention" } },
          { key: "cross_attn_hidden", label: "Cross-attn hidden dim", type: "select", default: 512, options: [128, 256, 512, 768, 1024, 1536], tooltip: "Hidden dimensionality of the fusion module.", tier: "release", show_if: { head_type: "cross_attention" } },
          { key: "cross_attn_depth", label: "Cross-attn depth", type: "int", default: 4, min: 1, max: 12, tooltip: "Number of stacked cross-attention layers.", tier: "release", show_if: { head_type: "cross_attention" } },
          { key: "bilinear_rank", label: "Bilinear rank", type: "int", default: 64, min: 8, max: 512, tooltip: "Low-rank factorisation rank for bilinear pooling.", tier: "beta", show_if: { head_type: "bilinear" } },
        ],
      },
      loss: {
        label: "Loss & heads",
        items: [
          { key: "regression_loss", label: "Regression loss", type: "select", default: "huber", options: ["mse", "mae", "huber", "smooth_l1", "log_cosh"], tooltip: "Loss for pKd/pKi/IC50 regression; Huber is robust to assay outliers.", tier: "release" },
          { key: "huber_delta", label: "Huber delta", type: "float", default: 1.0, min: 0.1, max: 5.0, step: 0.1, tooltip: "Transition point between L2 and L1 in Huber loss (in log-affinity units).", tier: "release", show_if: { regression_loss: "huber" } },
          { key: "use_classification_head", label: "Active/inactive head", type: "bool", default: true, tooltip: "Adds a BCE head for binary activity (cutoff configurable).", tier: "release" },
          { key: "pairwise_ranking_loss", label: "Pairwise ranking loss", type: "bool", default: false, tooltip: "Adds a margin-ranking term so within-target ordering is preserved.", tier: "beta" },
          { key: "focal_gamma", label: "Focal-loss gamma", type: "float", default: 0.0, min: 0.0, max: 5.0, step: 0.1, tooltip: "Focal-loss focusing parameter for the classification head; 0 disables.", tier: "beta" },
          { key: "multitask_weights", label: "Multi-task weights", type: "chips", default: ["reg:1.0", "cls:0.3"], tooltip: "Per-task loss weights; 'auto' uses learned uncertainty weighting.", tier: "beta" },
        ],
      },
      regularization: {
        label: "Regularisation",
        items: [
          { key: "dropout", label: "Dropout", type: "float", default: 0.1, min: 0.0, max: 0.5, step: 0.05, tooltip: "Dropout probability in fusion and head layers.", tier: "release" },
          { key: "weight_decay", label: "Weight decay", type: "float", default: 0.01, min: 0.0, max: 0.5, step: 0.005, tooltip: "AdamW L2 regularisation strength.", tier: "release" },
          { key: "stochastic_depth", label: "Stochastic depth", type: "float", default: 0.0, min: 0.0, max: 0.5, step: 0.05, tooltip: "Per-layer drop probability for deep transformer heads.", tier: "beta" },
          { key: "label_smoothing", label: "Label smoothing", type: "float", default: 0.0, min: 0.0, max: 0.2, step: 0.01, tooltip: "Smoothing for the classification head.", tier: "beta" },
          { key: "mixup_alpha", label: "Mixup alpha", type: "float", default: 0.0, min: 0.0, max: 1.0, step: 0.05, tooltip: "Beta-distribution alpha for input mixup; 0 disables.", tier: "beta" },
          { key: "manifold_mixup", label: "Manifold mixup", type: "bool", default: false, tooltip: "Mix embeddings at a random intermediate layer instead of inputs.", tier: "lab" },
        ],
      },
      stability: {
        label: "Stability & precision",
        items: [
          { key: "grad_clip_norm", label: "Gradient clip (norm)", type: "float", default: 1.0, min: 0.0, max: 10.0, step: 0.1, tooltip: "Max global gradient L2 norm; 0 disables.", tier: "release" },
          { key: "grad_accum_steps", label: "Gradient accumulation", type: "int", default: 1, min: 1, max: 64, tooltip: "Microbatches accumulated before each optimiser step.", tier: "release" },
          { key: "amp_precision", label: "Mixed precision", type: "select", default: "bf16", options: ["fp32", "fp16", "bf16"], tooltip: "Parameter / activation dtype. bf16 is the default on Ampere+ (RTX 30/40/50, A100, H100); fp16 needs a loss scaler. TF32 is a separate matmul flag — see Reproducibility.", tier: "release" },
          { key: "tf32_matmul", label: "TF32 matmul (Ampere+)", type: "bool", default: true, tooltip: "Use TF32 tensor cores for fp32 matmul (sets torch.backends.cuda.matmul.allow_tf32 = True). Storage stays fp32; only matmul precision is reduced.", tier: "release" },
          { key: "ema_decay", label: "EMA decay", type: "float", default: 0.0, min: 0.0, max: 0.9999, step: 0.0001, tooltip: "Exponential moving average of weights; 0 disables.", tier: "beta" },
          { key: "swa", label: "Stochastic weight averaging", type: "bool", default: false, tooltip: "Average weights over the last fraction of training for better generalisation.", tier: "beta" },
          { key: "lookahead", label: "Lookahead optimizer wrapper", type: "bool", default: false, tooltip: "Wraps the inner optimiser with k-step lookahead.", tier: "lab" },
        ],
      },
      optimizer: {
        label: "Optimiser & schedule",
        items: [
          { key: "optimizer", label: "Optimiser", type: "select", default: "adamw", options: ["adamw", "adafactor", "lion", "sophia", "shampoo"], tooltip: "Optimiser family; AdamW is the safe default, Lion is more memory-efficient.", tier: "release" },
          { key: "adam_betas", label: "Adam betas", type: "chips", default: ["0.9", "0.999"], tooltip: "Momentum coefficients (β1, β2).", tier: "release" },
          { key: "adam_eps", label: "Adam epsilon", type: "float", default: 1e-8, min: 1e-12, max: 1e-4, step: 1e-9, tooltip: "Numerical stability constant in the optimiser.", tier: "beta" },
          { key: "scheduler", label: "LR scheduler", type: "select", default: "cosine_warmup", options: ["constant", "linear_warmup", "cosine_warmup", "onecycle", "cosine_restarts", "polynomial"], tooltip: "Learning rate schedule over training.", tier: "release" },
          { key: "warmup_steps", label: "Warmup steps", type: "int", default: 1000, min: 0, max: 50000, tooltip: "Linear warmup length before the main schedule.", tier: "release" },
          { key: "min_lr_ratio", label: "Min LR ratio", type: "float", default: 0.01, min: 0.0, max: 0.5, step: 0.005, tooltip: "Floor for the LR schedule, as a fraction of peak LR.", tier: "release" },
        ],
      },
      distributed: {
        label: "Distributed",
        items: [
          { key: "dist_strategy", label: "Distributed strategy", type: "select", default: "ddp", options: ["single", "ddp", "fsdp_full_shard", "fsdp_shard_grad_op", "fsdp_no_shard", "deepspeed_zero1", "deepspeed_zero2", "deepspeed_zero3"], tooltip: "Multi-GPU parallelism; FSDP / ZeRO-3 are needed for very large encoders.", tier: "release" },
          { key: "activation_offload", label: "Activation offload", type: "bool", default: false, tooltip: "Offload activations to CPU to fit larger models.", tier: "beta" },
          { key: "cpu_offload", label: "CPU parameter offload", type: "bool", default: false, tooltip: "ZeRO-3 / FSDP CPU offload; slower but enables much larger models.", tier: "beta" },
        ],
      },
      reproducibility: {
        label: "Reproducibility & compile",
        items: [
          { key: "seed_list", label: "Seeds", type: "chips", default: ["42"], tooltip: "Random seeds; multiple seeds spawn an ensemble.", tier: "release" },
          { key: "deterministic_ops", label: "Deterministic ops", type: "bool", default: false, tooltip: "Force deterministic CUDA kernels; slower but bit-reproducible.", tier: "release" },
          { key: "cudnn_benchmark", label: "cuDNN benchmark", type: "bool", default: true, tooltip: "Let cuDNN pick fastest kernels per input shape; off for reproducibility.", tier: "release" },
          { key: "torch_compile_mode", label: "torch.compile mode", type: "select", default: "default", options: ["off", "default", "reduce-overhead", "max-autotune"], tooltip: "PyTorch 2 graph compilation level; max-autotune is fastest but compiles slowly.", tier: "beta" },
        ],
      },
      monitoring: {
        label: "Monitoring & checkpoints",
        items: [
          { key: "eval_cadence_unit", label: "Eval cadence unit", type: "select", default: "epoch", options: ["step", "epoch", "minutes"], tooltip: "Whether evaluation is triggered by steps, epochs, or wall-clock minutes.", tier: "release" },
          { key: "eval_every", label: "Eval every N", type: "int", default: 1, min: 1, max: 10000, tooltip: "Frequency of validation runs (in the chosen unit).", tier: "release" },
          { key: "early_stop_metric", label: "Early-stop metric", type: "select", default: "val_pearson", options: ["val_loss", "val_rmse", "val_mae", "val_pearson", "val_spearman", "val_r2"], tooltip: "Which validation metric drives early stopping.", tier: "release" },
          { key: "early_stop_patience", label: "Early-stop patience", type: "int", default: 10, min: 1, max: 100, tooltip: "Eval cycles without improvement before stopping.", tier: "release" },
          { key: "early_stop_min_delta", label: "Min delta", type: "float", default: 0.001, min: 0.0, max: 0.1, step: 0.0001, tooltip: "Minimum improvement to count as progress.", tier: "release" },
          { key: "checkpoint_policy", label: "Checkpoint policy", type: "select", default: "best_and_last", options: ["every_n", "on_improvement", "best_and_last", "last_k", "all"], tooltip: "When to save weights; best_and_last is the standard.", tier: "release" },
          { key: "checkpoint_last_k", label: "Keep last k checkpoints", type: "int", default: 3, min: 1, max: 20, tooltip: "Rolling window of recent checkpoints to retain.", tier: "release" },
          { key: "experiment_tracker", label: "Experiment tracker", type: "multi-select", default: ["tensorboard"], options: ["tensorboard", "wandb", "mlflow", "neptune", "aim"], tooltip: "Where to stream metrics, configs, and artifacts.", tier: "release" },
          { key: "gpu_memory_profiling", label: "GPU memory profiling", type: "bool", default: false, tooltip: "Enable torch.cuda memory snapshots for OOM debugging.", tier: "beta" },
        ],
      },
    },
  },

  inference_advanced: {
    label: "Inference advanced",
    sub: "Ensembling, conformal calibration, MC dropout, attribution method.",
    groups: {
      ensemble: {
        label: "Ensembling",
        items: [
          { key: "ensemble_strategy", label: "Strategy", type: "select", default: "seed_ensemble", options: ["none", "seed_ensemble", "snapshot_ensemble", "deep_ensemble", "swag"], tooltip: "How to combine multiple checkpoints/seeds at inference.", tier: "release" },
          { key: "ensemble_n", label: "Members", type: "int", default: 5, min: 1, max: 20, tooltip: "Number of ensemble members.", tier: "release" },
          { key: "tta_enabled", label: "Test-time augmentation", type: "bool", default: false, tooltip: "Average predictions over conformer/tautomer augmentations at inference.", tier: "beta" },
          { key: "mc_dropout_passes", label: "MC dropout passes", type: "int", default: 0, min: 0, max: 100, tooltip: "Stochastic forward passes with dropout enabled for uncertainty; 0 disables.", tier: "beta" },
        ],
      },
      uncertainty: {
        label: "Uncertainty calibration",
        items: [
          { key: "temperature_scaling", label: "Temperature scaling", type: "bool", default: true, tooltip: "Calibrate the classification head with a single-parameter temperature on val set.", tier: "release" },
          { key: "conformal_alpha", label: "Conformal α (miscoverage)", type: "float", default: 0.1, min: 0.01, max: 0.5, step: 0.01, tooltip: "Miscoverage rate; α=0.1 yields 90% prediction intervals.", tier: "release" },
          { key: "conformal_calibration_source", label: "Calibration set", type: "select", default: "held_out_val", options: ["held_out_val", "cv_oof", "external_set"], tooltip: "Where the conformal calibration residuals come from.", tier: "release" },
          { key: "conformal_group_conditional", label: "Group-conditional conformal", type: "select", default: "per_target", options: ["marginal", "per_target", "per_scaffold_cluster", "per_protein_family"], tooltip: "Stratify conformal intervals so coverage holds within each group.", tier: "beta" },
        ],
      },
      attribution: {
        label: "Attribution",
        items: [
          { key: "attribution_method", label: "Method", type: "select", default: "integrated_gradients", options: ["none", "integrated_gradients", "saliency", "shap_kernel", "shap_deep", "captum_attention", "gnnexplainer"], tooltip: "How to attribute predictions back to atoms/residues. SHAP and IG are additive feature attributions; attention is NOT an attribution.", tier: "beta" },
          { key: "ig_steps", label: "IG steps", type: "int", default: 50, min: 8, max: 256, tooltip: "Number of Riemann steps for Integrated Gradients.", tier: "beta", show_if: { attribution_method: "integrated_gradients" } },
          { key: "shap_background_size", label: "SHAP background size", type: "int", default: 100, min: 10, max: 1000, tooltip: "Background sample size for KernelSHAP.", tier: "beta", show_if: { attribution_method: "shap_kernel" } },
        ],
      },
    },
  },

  eval_analytics: {
    label: "Eval & analytics",
    sub: "How metrics are sliced, how confidence intervals are computed, selectivity panel.",
    groups: {
      stratification: {
        label: "Stratification",
        items: [
          { key: "split_strategy", label: "Split strategy", type: "select", default: "cold_target", options: ["random", "scaffold", "cold_drug", "cold_target", "cold_pair", "orphan_drug", "time_split"], tooltip: "Cold-target is the realistic setting for a new protein; cold-pair is the hardest.", tier: "release" },
          { key: "per_stratum_metrics", label: "Per-stratum metrics", type: "bool", default: true, tooltip: "Compute metrics within each cluster/family/scaffold group separately.", tier: "release" },
          { key: "stratification_keys", label: "Stratification keys", type: "multi-select", default: ["protein_family", "scaffold"], options: ["protein_family", "ec_class", "pfam_clan", "scaffold", "tanimoto_cluster", "kinase_subfamily"], tooltip: "Grouping columns used for per-stratum analytics and heatmaps.", tier: "release" },
        ],
      },
      metrics: {
        label: "Metrics",
        items: [
          { key: "primary_metrics", label: "Primary metrics", type: "multi-select", default: ["rmse", "pearson", "spearman"], options: ["mae", "rmse", "pearson", "spearman", "r2", "ci", "rm2", "auroc", "auprc"], tooltip: "Metrics surfaced in the run summary; CI / rm² are DTA-community standards.", tier: "release" },
          { key: "bootstrap_n", label: "Paired bootstrap n", type: "int", default: 1000, min: 100, max: 10000, tooltip: "Bootstrap resamples for confidence intervals on metrics.", tier: "release" },
          { key: "ci_alpha", label: "CI α", type: "float", default: 0.05, min: 0.01, max: 0.2, step: 0.01, tooltip: "Significance level for bootstrap CIs (0.05 = 95% CI).", tier: "release" },
        ],
      },
      selectivity: {
        label: "Selectivity & follow-up",
        items: [
          { key: "selectivity_panel", label: "Selectivity panel", type: "select", default: "kinome_scanmax_468", options: ["none", "kinome_scanmax_468", "kinome_karaman_317", "gpcrome_presto_tango_320", "nuclear_receptors_48", "custom"], tooltip: "Built-in off-target panel for selectivity scoring. ScanMAX (468) is the standard commercial DiscoverX panel; Karaman 317 is the canonical academic kinome; PRESTO-Tango 320 is the standard non-olfactory GPCR functional panel.", tier: "beta" },
          { key: "custom_offtarget_list", label: "Custom off-target UniProts", type: "chips", default: [], tooltip: "UniProt accessions used when selectivity panel is 'custom'.", tier: "beta", show_if: { selectivity_panel: "custom" } },
          { key: "followup_assay", label: "Follow-up assay", type: "select", default: "tr_fret", options: ["tr_fret", "spr", "itc", "biolayer_interferometry", "thermal_shift", "alphascreen", "radioligand"], tooltip: "Wet-lab assay planned for top picks; affects throughput estimates and dynamic range.", tier: "beta" },
          { key: "followup_throughput", label: "Expected throughput / week", type: "int", default: 96, min: 8, max: 1536, tooltip: "How many compounds the wet lab can confirm per week; drives shortlist size.", tier: "beta" },
        ],
      },
    },
  },
};

// Build the canonical defaults object once; consumers do
// `Object.assign({}, PS_DEEP_DEFAULTS[panel], userOverrides)`.
window.PS_DEEP_DEFAULTS = (() => {
  const out = {};
  for (const [panelKey, panel] of Object.entries(window.PS_DEEP_SETTINGS)) {
    out[panelKey] = {};
    for (const group of Object.values(panel.groups)) {
      for (const item of group.items) {
        out[panelKey][item.key] = item.default;
      }
    }
  }
  return out;
})();
