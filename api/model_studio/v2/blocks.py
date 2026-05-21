"""Role × implementation block registry for the flow-style pipeline builder.

This module is the **canonical catalog** of every "block" the new
LabVIEW-style Pipeline screen can drop into a canvas. Each block declares:

* ``role``    — what the block *is* semantically (e.g.
                ``protein_seq_encoder``, ``fusion``, ``regression_head``).
                Roles define the I/O contract; multiple implementations
                may share a role and are freely interchangeable.
* ``impl``    — which concrete implementation backs the role (e.g.
                ``cnn1d``, ``transformer``, ``esm2_frozen`` for the
                protein-seq-encoder role).
* ``inputs``  — typed input ports. Connecting a mismatched type triggers
                a UI red-glow + a "suggest a bridge node" CTA.
* ``outputs`` — typed output ports.
* ``params``  — exposed hyperparameters (kind / default / range / enum
                options). Drives the right-sidebar inspector form.
* ``backend`` — ``torch`` / ``xgboost`` / ``catboost`` / ``sklearn``.
                Tells the trainer dispatcher which path to take.
* ``builder`` — optional callable ``(params: dict) → object`` that
                materialises the block (an nn.Module for torch, an
                xgboost.Booster for xgboost, etc.). ``None`` means the
                block is metadata-only for now (the executor isn't
                wired yet; this is the registry's first step).

Backwards compatibility: this module does NOT replace
``TEMPLATE_BUILDERS`` in ``models.py``. Existing templates keep working
exactly as before. The registry is *additive* — it gives the new flow
builder a type-checked, swappable vocabulary while leaving the
template-fork path untouched. Later, templates will be re-expressed as
preset block compositions (saved flow graphs) that the user can fork.

Why this exists:
    The existing ``data.js`` node catalogue (~78 entries) and the
    ``TEMPLATE_BUILDERS`` registry don't carry enough metadata to drive
    a flow-style editor. Specifically, neither knows:
        - That a "GNN ligand encoder" (GIN / GCN / GAT) is a *role* with
          three swap-compatible *implementations*; the GUI treated each
          as a separate node, so swapping one for another required
          re-wiring the edges.
        - The exact I/O dtype/shape contract (only port-name strings).
        - Whether a block is torch / xgboost / catboost — needed for the
          trainer's dispatch path.
    The new BlockSpec consolidates all of that and exposes it over
    ``/api/v2/blocks``.

Once the new flow builder is wired up, blocks will also be the unit of:
    - The "Features" screen's compatibility check ("does this block's
      inputs match the feature set the user picked?").
    - The hyperparameter sweep (every block declares which of its params
      are sweepable + their search space).
    - The Diagnostic tap (every wire can be probed by a TapInspector
      block — it inherits the type/shape of the wire it taps).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional


# ── Port + Param + BlockSpec data structures ─────────────────────────

@dataclass(frozen=True)
class Port:
    """A typed I/O port on a block.

    ``type`` is the wire type used by the flow-builder type-checker
    (same vocabulary as the legacy ``data.js`` PS_PIPELINE_NODE_TYPES):

        ``aa_seq``           protein sequence (int-tokenised, B×L)
        ``smiles_tokens``    SMILES (int-tokenised, B×L)
        ``mol_graph_2d``     ligand 2D molecular graph
                             (torch_geometric Batch)
        ``residue_graph``    protein residue contact graph
                             (torch_geometric Batch)
        ``atom_graph``       protein atom-level graph (planned)
        ``protein_embedding``  per-protein 1-d embedding (B×D)
        ``ligand_embedding``   per-ligand   1-d embedding (B×D)
        ``protein_token_embedding``  per-residue 2-d embedding (B×L×D)
        ``embedding_1d``     generic 1-d embedding
        ``embedding_2d_pair`` interaction map (B×L1×L2×D)
        ``contact_map``      protein-protein contact map
        ``scalar``           single scalar per example
        ``prob``             probability in [0,1]
        ``tabular``          tabular features (B×D float)
        ``descriptors``      ligand descriptor vector (B×D)
        ``fingerprint``      ECFP/MACCS bit vector (B×D)
        ``pose``             3D pose (B×N_atoms×3)
        ``complex_3d``       protein-ligand complex coordinates

    ``shape`` is a free-form human-readable hint (e.g. "B×L"). The
    runtime contract is enforced by ``type`` alone; ``shape`` is for
    the GUI's inspector panel.

    ``optional`` ports may be left unconnected; they default to None
    inside the block. Used for e.g. the GraphEncoder's edge-attr port.
    """
    name: str
    type: str
    shape: str = ""
    optional: bool = False
    description: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "shape": self.shape,
                "optional": self.optional, "description": self.description}


@dataclass(frozen=True)
class Param:
    """A user-tunable parameter on a block.

    ``kind`` ∈ {"int", "float", "bool", "enum", "text", "checkpoint"}.
    ``sweepable`` declares whether the hyperparameter-sweep loop is
    allowed to vary this. Discrete int ranges + bools + enums are the
    common cases; float ranges work too (log scale by default for lr-
    style params).
    """
    key: str
    kind: str
    default: Any
    label: str = ""
    description: str = ""
    options: Optional[list] = None       # for enums
    min: Optional[Any] = None            # for int / float
    max: Optional[Any] = None
    step: Optional[Any] = None
    log_scale: bool = False              # for float sweeps
    sweepable: bool = True

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop None-valued discriminators so the JSON payload stays compact.
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class BlockSpec:
    """A single block in the flow-builder palette."""
    id: str                              # globally unique, e.g. "protein_seq_cnn1d"
    role: str                            # semantic role; multiple impls per role
    impl: str                            # implementation key within the role
    label: str                           # human-readable display name
    category: str                        # "input" | "encoder" | "fusion" | "head" | "diagnostic"
    short_desc: str
    long_desc: str
    inputs: list[Port]
    outputs: list[Port]
    params: list[Param] = field(default_factory=list)
    backend: str = "torch"               # torch | xgboost | catboost | sklearn
    cost: str = "moderate"               # trivial | fast | moderate | heavy
    refs: list[str] = field(default_factory=list)
    builder: Optional[Callable] = None
    integrated: bool = True
    notes: str = ""

    def to_catalog_entry(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "impl": self.impl,
            "label": self.label,
            "category": self.category,
            "short_desc": self.short_desc,
            "long_desc": self.long_desc,
            "inputs":  [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "params":  [p.to_dict() for p in self.params],
            "backend": self.backend,
            "cost":    self.cost,
            "refs":    list(self.refs),
            "integrated": self.integrated,
            "notes":   self.notes,
        }


# ── Roles registry ────────────────────────────────────────────────────
# A ROLE is the *semantic position* of a block in the flow. The flow
# builder's "swap implementation" dropdown shows every BlockSpec with a
# matching ``role``. Roles MUST have a consistent I/O contract across
# all their implementations; if two impls have different I/O, they're
# different roles.

ROLES: dict[str, dict] = {
    # ── INPUT roles ──────────────────────────────────────────────────
    "input.protein_seq": {
        "label": "Protein sequence input",
        "category": "input",
        "out_type": "aa_seq",
        "description": "Reads a tokenised amino-acid sequence per record.",
    },
    "input.ligand_smiles": {
        "label": "Ligand SMILES input",
        "category": "input",
        "out_type": "smiles_tokens",
        "description": "Reads a tokenised SMILES string per record.",
    },
    "input.ligand_graph": {
        "label": "Ligand 2D graph input",
        "category": "input",
        "out_type": "mol_graph_2d",
        "description": "RDKit-derived molecular graph (atoms × bonds).",
    },
    "input.protein_residue_graph": {
        "label": "Protein residue graph input",
        "category": "input",
        "out_type": "residue_graph",
        "description": ("Per-residue protein graph (one node per CA). "
                        "When a cached PDB / AlphaFold structure is "
                        "available, edges are CA-CA contacts ≤ 8 Å; "
                        "otherwise a sliding-window sequence graph is used."),
    },
    "input.ligand_fingerprint": {
        "label": "Ligand fingerprint input",
        "category": "input",
        "out_type": "fingerprint",
        "description": "Bit-vector fingerprint (ECFP4 / MACCS / ...).",
    },
    "input.ligand_descriptors": {
        "label": "Ligand descriptor input",
        "category": "input",
        "out_type": "tabular",
        "description": "Tabular physchem / RDKit descriptors.",
    },
    "input.protein_embedding": {
        "label": "Protein PLM embedding input",
        "category": "input",
        "out_type": "protein_embedding",
        "description": ("Pre-computed protein embedding from a frozen "
                        "language model (ESM-2 / Ankh / ProtBERT). "
                        "Cached per UniProt so training stays fast."),
    },

    # ── ENCODER roles ───────────────────────────────────────────────
    "encoder.protein_seq": {
        "label": "Protein sequence encoder",
        "category": "encoder",
        "in_type": "aa_seq",
        "out_type": "protein_embedding",
        "description": ("Turns a tokenised amino-acid sequence into a "
                        "per-protein vector. Implementations: 1D-CNN "
                        "(cheap, DeepDTA-style), Transformer, "
                        "ESM-2-frozen, Identity (pass through if input "
                        "is already an embedding)."),
    },
    "encoder.protein_graph": {
        "label": "Protein graph encoder",
        "category": "encoder",
        "in_type": "residue_graph",
        "out_type": "protein_embedding",
        "description": ("Message-passing over residue contact graphs. "
                        "Implementations: GCN, GIN, GAT."),
    },
    "encoder.ligand_seq": {
        "label": "Ligand SMILES encoder",
        "category": "encoder",
        "in_type": "smiles_tokens",
        "out_type": "ligand_embedding",
        "description": ("Turns a tokenised SMILES into a per-ligand "
                        "vector. Implementations: 1D-CNN, ChemBERTa, "
                        "MolFormer, Identity."),
    },
    "encoder.ligand_graph": {
        "label": "Ligand graph encoder",
        "category": "encoder",
        "in_type": "mol_graph_2d",
        "out_type": "ligand_embedding",
        "description": "GNN over the molecular graph. Implementations: GIN, GCN, GAT.",
    },
    "encoder.tabular": {
        "label": "Tabular encoder",
        "category": "encoder",
        "in_type": "tabular",
        "out_type": "embedding_1d",
        "description": ("Encodes a dense feature vector. Implementations: "
                        "MLP (torch, trainable end-to-end), Identity. "
                        "XGBoost / CatBoost variants exist at the head/"
                        "fusion role, not here — they aren't differentiable "
                        "so they don't compose mid-graph."),
    },

    # ── FUSION roles ────────────────────────────────────────────────
    # Single role with several impls — they all take two embeddings and
    # emit one joint embedding. The "siamese" variant takes two of the
    # same type (PPI); the "asymmetric" variants take (protein, ligand)
    # explicitly. The role's contract is "two embeddings in, one out"
    # so the swap is safe in either case.
    "fusion": {
        "label": "Fusion",
        "category": "fusion",
        "in_types": ["protein_embedding", "ligand_embedding",
                     "embedding_1d", "embedding_1d"],
        "out_type": "embedding_1d",
        "description": ("Combines two encoded representations into a "
                        "joint embedding. Implementations: concat+MLP, "
                        "bilinear attention (DrugBAN), two-tower dot "
                        "product (ConPLex), four-way fusion "
                        "(siamese PPI: [a;b;|a−b|;a*b])."),
    },

    # ── HEAD roles ──────────────────────────────────────────────────
    "head.regression": {
        "label": "Regression head",
        "category": "head",
        "in_type": "embedding_1d",
        "out_type": "scalar",
        "description": ("Predicts a continuous label (pKi / pKd / pIC50 "
                        "/ Kd). Loss = MSE / Huber / Smooth-L1. "
                        "Implementations: MLP (torch, trainable end-to-"
                        "end), XGBoost, CatBoost."),
    },
    "head.binary": {
        "label": "Binary classification head",
        "category": "head",
        "in_type": "embedding_1d",
        "out_type": "prob",
        "description": ("Predicts an interaction probability (0/1 label). "
                        "Loss = BCE. Implementations: MLP, XGBoost, "
                        "CatBoost."),
    },
}


# ── Block registry ────────────────────────────────────────────────────

BLOCKS: dict[str, BlockSpec] = {}


def register(spec: BlockSpec) -> None:
    """Add a block to the registry. Validates the role is known."""
    if spec.role not in ROLES:
        raise ValueError(
            f"Block '{spec.id}' declares role '{spec.role}' which isn't "
            f"in ROLES. Add the role to ROLES first."
        )
    BLOCKS[spec.id] = spec


def get(block_id: str) -> Optional[BlockSpec]:
    return BLOCKS.get(block_id)


def list_by_role(role: str) -> list[BlockSpec]:
    """Return every BlockSpec that implements ``role``. Used by the
    flow builder's 'swap implementation' dropdown."""
    return [b for b in BLOCKS.values() if b.role == role]


def list_by_category(category: str) -> list[BlockSpec]:
    return [b for b in BLOCKS.values() if b.category == category]


def catalog() -> dict:
    """Serialisable catalog payload for ``GET /api/v2/blocks``.

    Returns:
        {
          "roles": { role_id: { label, category, in_type, out_type, description } },
          "blocks": [ block_catalog_entry, ... ],
          "by_role": { role_id: [block_id, ...] },
          "n_integrated": N,
          "n_total": N,
        }
    """
    blocks = sorted(BLOCKS.values(), key=lambda b: (b.category, b.role, b.impl))
    by_role: dict[str, list[str]] = {}
    for b in blocks:
        by_role.setdefault(b.role, []).append(b.id)
    return {
        "roles": ROLES,
        "blocks": [b.to_catalog_entry() for b in blocks],
        "by_role": by_role,
        "n_integrated": sum(1 for b in BLOCKS.values() if b.integrated),
        "n_total": len(BLOCKS),
    }


# ── Concrete block registrations ─────────────────────────────────────
# These wrap the existing nn.Module classes in models.py so the flow
# builder can compose them. Each builder receives a params dict and
# returns the assembled module — same interface that TEMPLATE_BUILDERS
# uses, but per-block instead of per-template.

# Input blocks (no builder — they're declarative; the trainer's loader
# is what produces the actual tensors).

register(BlockSpec(
    id="input.protein_seq.tokens",
    role="input.protein_seq",
    impl="char_tokens",
    label="Protein sequence (char tokens)",
    category="input",
    short_desc="Char-level tokenisation, max 1000 residues (DeepDTA-style).",
    long_desc=(
        "Reads each record's amino-acid sequence and char-tokenises it "
        "with the CHARPROTSET alphabet. Sequences longer than max_len "
        "are truncated from the right; shorter ones are zero-padded."
    ),
    inputs=[],
    outputs=[Port("out", "aa_seq", "B × max_len")],
    params=[Param("max_len", "int", 1000, label="Max residues", min=128, max=4096, step=128)],
    backend="torch", cost="trivial", refs=["DeepDTA"], integrated=True,
))

register(BlockSpec(
    id="input.ligand_smiles.tokens",
    role="input.ligand_smiles",
    impl="char_tokens",
    label="Ligand SMILES (char tokens)",
    category="input",
    short_desc="Char-level tokenisation, max 100 chars (DeepDTA-style).",
    long_desc="Char-tokenises SMILES strings with the CHARISOSMISET alphabet.",
    inputs=[],
    outputs=[Port("out", "smiles_tokens", "B × max_len")],
    params=[Param("max_len", "int", 100, label="Max SMILES length", min=64, max=512, step=16)],
    backend="torch", cost="trivial", refs=["DeepDTA"], integrated=True,
))

register(BlockSpec(
    id="input.ligand_graph.rdkit",
    role="input.ligand_graph",
    impl="rdkit_atom_graph",
    label="Ligand 2D graph (RDKit, 78-d atom features)",
    category="input",
    short_desc="GraphDTA-faithful 78-d atom features + bond edges.",
    long_desc=(
        "Parses each SMILES with RDKit and emits a torch_geometric Data "
        "object. Atom features: 44-d element one-hot + 11-d degree + "
        "11-d num-H + 11-d implicit valence + 1-d aromaticity = 78-d. "
        "Edges are bidirectional bonds; single-atom molecules get a "
        "self-loop so message passing has somewhere to go."
    ),
    inputs=[],
    outputs=[Port("out", "mol_graph_2d", "n_atoms × 78")],
    params=[],
    backend="torch", cost="fast", refs=["GraphDTA"], integrated=True,
))

register(BlockSpec(
    id="input.protein_residue_graph.contact",
    role="input.protein_residue_graph",
    impl="ca_contact_or_window",
    label="Protein residue graph (CA-contact or sequence-window)",
    category="input",
    short_desc="PDB-derived contact graph when cached; sequence fallback otherwise.",
    long_desc=(
        "Builds a per-residue graph. When a cached PDB / AlphaFold "
        "structure is available for the protein's UniProt, edges are "
        "CA-CA contacts within ``contact_cutoff`` Å. Otherwise falls "
        "back to a sliding-window sequence graph (linear backbone + "
        "non-bond edges to neighbours within ``window``). Node features "
        "are 23-d (20 AA one-hot + position + has_structure flag). "
        "Coverage is reported in the run summary's structure_coverage."
    ),
    inputs=[],
    outputs=[Port("out", "residue_graph", "n_residues × 23")],
    params=[
        Param("contact_cutoff", "float", 8.0, label="Contact cutoff (Å)",
              min=4.0, max=12.0, step=0.5),
        Param("max_residues", "int", 1024, label="Max residues",
              min=256, max=4096, step=256),
    ],
    backend="torch", cost="moderate", refs=["graph_features.py"], integrated=True,
))


# Protein-sequence encoders
def _build_protein_seq_cnn(params: dict):
    from .dataset import CHARPROTLEN
    from .models import CNNTower
    return CNNTower(
        vocab_size=CHARPROTLEN,
        embed_dim=int(params.get("embed_dim", 128)),
        num_filters=int(params.get("filters", 32)),
        kernel_size=int(params.get("kernel_size", 8)),
        max_len=int(params.get("max_len", 1000)),
    )

register(BlockSpec(
    id="encoder.protein_seq.cnn1d",
    role="encoder.protein_seq",
    impl="cnn1d",
    label="Protein 1D-CNN (DeepDTA)",
    category="encoder",
    short_desc="Embedding → 3-stage 1D-CNN → max pool.",
    long_desc=(
        "DeepDTA's paper-faithful protein tower. Token-embedding "
        "(``embed_dim``), then three Conv1d stages with widening "
        "channel count (``filters``, ``2·filters``, ``3·filters``), "
        "ReLU between, and an adaptive max-pool to collapse the "
        "length dim. Output is (B, 3·filters)."
    ),
    inputs=[Port("in", "aa_seq", "B × max_len")],
    outputs=[Port("out", "protein_embedding", "B × (3·filters)")],
    params=[
        Param("embed_dim", "int", 128, label="Embedding dim", min=32, max=512, step=32),
        Param("filters",   "int", 32,  label="CNN filters",   min=16, max=128, step=16),
        Param("kernel_size","int", 8,  label="Kernel size",   min=3,  max=16,  step=1),
    ],
    backend="torch", cost="fast", refs=["Öztürk 2018"], integrated=True,
    builder=_build_protein_seq_cnn,
))

register(BlockSpec(
    id="encoder.protein_seq.transformer",
    role="encoder.protein_seq",
    impl="transformer",
    label="Protein Transformer (MolTrans-lite)",
    category="encoder",
    short_desc="Untrained transformer towers, n_heads × n_layers.",
    long_desc=(
        "Simple non-pretrained transformer for protein sequences. "
        "Used by MolTrans-lite. Cheaper than ESM-2 but won't catch "
        "evolutionary patterns — for that use the ESM-2-frozen impl."
    ),
    inputs=[Port("in", "aa_seq", "B × max_len")],
    outputs=[Port("out", "protein_embedding", "B × embed_dim")],
    params=[
        Param("embed_dim", "int", 128, label="Embedding dim", min=64, max=512, step=64),
        Param("n_heads",   "int", 4,   label="Attention heads", min=2, max=16, step=2),
        Param("n_layers",  "int", 2,   label="Layers", min=1, max=8, step=1),
        Param("ff_dim",    "int", 256, label="FFN hidden", min=64, max=1024, step=64),
    ],
    backend="torch", cost="moderate", refs=["MolTrans"], integrated=True,
    # builder is provided indirectly by MolTransLite — the flow executor
    # will wire this when it lands.
    builder=None,
    notes="builder pending the flow executor — uses MolTransLite internals today",
))

register(BlockSpec(
    id="encoder.protein_seq.esm2_frozen",
    role="encoder.protein_seq",
    impl="esm2_frozen",
    label="ESM-2 (frozen)",
    category="encoder",
    short_desc="Lookup pre-cached ESM-2 embeddings; no fine-tuning.",
    long_desc=(
        "Treats ESM-2 as a frozen feature extractor — the trainer "
        "pre-computes one embedding per UniProt and caches it. "
        "Fast at training time; quality matches the 650M checkpoint "
        "without paying the inference cost every batch."
    ),
    inputs=[Port("in", "aa_seq", "B × max_len")],
    outputs=[Port("out", "protein_embedding", "B × 1280")],
    params=[
        Param("checkpoint", "enum", "esm2_t33_650M", label="ESM-2 checkpoint",
              options=["esm2_t12_35M", "esm2_t30_150M", "esm2_t33_650M", "esm2_t36_3B"],
              sweepable=False),
    ],
    backend="torch", cost="heavy", refs=["Lin 2023"], integrated=False,
    notes="awaiting the embedding-cache pipeline — placeholder for the flow builder",
))


# Protein-graph encoders (GCN / GIN / GAT)
def _build_protein_graph_gcn(params: dict):
    import torch.nn as nn
    from torch_geometric.nn import GCNConv
    from .graph_features import RESIDUE_FEAT_DIM
    layers = []
    in_dim = RESIDUE_FEAT_DIM
    hidden = int(params.get("hidden", 128))
    for _ in range(int(params.get("layers", 3))):
        layers.append(GCNConv(in_dim, hidden, add_self_loops=True))
        in_dim = hidden
    return nn.ModuleList(layers)

register(BlockSpec(
    id="encoder.protein_graph.gcn",
    role="encoder.protein_graph",
    impl="gcn",
    label="Protein GCN (residue graph)",
    category="encoder",
    short_desc="Stacked GCNConv with self-loops, global_mean_pool.",
    long_desc=(
        "Graph Convolutional Network over the protein residue graph. "
        "Each layer averages a residue's features with its in-contact "
        "neighbours'. Cheap, well-understood; the default protein-side "
        "GNN in StructGNN-DTA and PPI-Siamese."
    ),
    inputs=[Port("in", "residue_graph", "n_residues × 23")],
    outputs=[Port("out", "protein_embedding", "B × hidden")],
    params=[
        Param("hidden", "int", 128, label="Hidden dim", min=32, max=512, step=32),
        Param("layers", "int", 3,   label="GCN layers", min=1, max=6, step=1),
    ],
    backend="torch", cost="moderate", refs=["Kipf 2017", "graph_features.py"],
    integrated=True, builder=_build_protein_graph_gcn,
))

register(BlockSpec(
    id="encoder.protein_graph.gin",
    role="encoder.protein_graph",
    impl="gin",
    label="Protein GIN (residue graph)",
    category="encoder",
    short_desc="Graph Isomorphism Network — MLP-aggregated message passing.",
    long_desc=(
        "More expressive than GCN (provably as discriminative as the "
        "Weisfeiler-Lehman test). Uses a 2-layer MLP inside each "
        "message-passing step. Slightly slower than GCN but often "
        "stronger on protein graphs with rich node features."
    ),
    inputs=[Port("in", "residue_graph", "n_residues × 23")],
    outputs=[Port("out", "protein_embedding", "B × hidden")],
    params=[
        Param("hidden", "int", 128, label="Hidden dim", min=32, max=512, step=32),
        Param("layers", "int", 3,   label="GIN layers", min=1, max=6, step=1),
    ],
    backend="torch", cost="moderate", refs=["Xu 2019"], integrated=False,
    notes="pending wiring of a builder; the role already accepts it for swap",
))

register(BlockSpec(
    id="encoder.protein_graph.gat",
    role="encoder.protein_graph",
    impl="gat",
    label="Protein GAT (residue graph)",
    category="encoder",
    short_desc="Graph Attention Network — learns attention weights over neighbours.",
    long_desc=(
        "Each residue learns attention weights over its in-contact "
        "neighbours instead of averaging them uniformly. Captures "
        "the heterogeneity of interface vs core residues."
    ),
    inputs=[Port("in", "residue_graph", "n_residues × 23")],
    outputs=[Port("out", "protein_embedding", "B × (hidden·heads)")],
    params=[
        Param("hidden", "int", 64, label="Hidden dim per head", min=16, max=256, step=16),
        Param("heads",  "int", 4,  label="Attention heads",     min=1, max=16, step=1),
        Param("layers", "int", 3,  label="GAT layers",          min=1, max=6, step=1),
    ],
    backend="torch", cost="moderate", refs=["Velickovic 2018"], integrated=False,
    notes="pending builder; role accepts it",
))


# Ligand-sequence encoders
def _build_ligand_seq_cnn(params: dict):
    from .dataset import CHARISOSMILEN
    from .models import CNNTower
    return CNNTower(
        vocab_size=CHARISOSMILEN,
        embed_dim=int(params.get("embed_dim", 128)),
        num_filters=int(params.get("filters", 32)),
        kernel_size=int(params.get("kernel_size", 4)),
        max_len=int(params.get("max_len", 100)),
    )

register(BlockSpec(
    id="encoder.ligand_seq.cnn1d",
    role="encoder.ligand_seq",
    impl="cnn1d",
    label="SMILES 1D-CNN (DeepDTA)",
    category="encoder",
    short_desc="Mirror of the protein CNN tower, sized for SMILES.",
    long_desc=(
        "Same three-stage 1D-CNN architecture as the protein tower but "
        "with a smaller kernel (4 by default) and the SMILES alphabet. "
        "Produces (B, 3·filters)."
    ),
    inputs=[Port("in", "smiles_tokens", "B × max_len")],
    outputs=[Port("out", "ligand_embedding", "B × (3·filters)")],
    params=[
        Param("embed_dim",  "int", 128, label="Embedding dim", min=32, max=512, step=32),
        Param("filters",    "int", 32,  label="CNN filters",   min=16, max=128, step=16),
        Param("kernel_size","int", 4,   label="Kernel size",   min=2,  max=12,  step=1),
    ],
    backend="torch", cost="fast", refs=["Öztürk 2018"], integrated=True,
    builder=_build_ligand_seq_cnn,
))


# Ligand-graph encoders
def _build_ligand_graph_gin(params: dict):
    import torch.nn as nn
    from torch_geometric.nn import GINConv
    from .featurizers import ATOM_FEAT_DIM
    layers = []
    in_dim = ATOM_FEAT_DIM
    hidden = int(params.get("hidden", 128))
    for _ in range(int(params.get("layers", 3))):
        mlp = nn.Sequential(nn.Linear(in_dim, hidden), nn.ReLU(),
                            nn.Linear(hidden, hidden))
        layers.append(GINConv(mlp))
        in_dim = hidden
    return nn.ModuleList(layers)

register(BlockSpec(
    id="encoder.ligand_graph.gin",
    role="encoder.ligand_graph",
    impl="gin",
    label="Ligand GIN (GraphDTA atom features)",
    category="encoder",
    short_desc="Graph Isomorphism Network on RDKit-derived atom graphs.",
    long_desc=(
        "Provably as discriminative as the Weisfeiler-Lehman test — "
        "captures graph isomorphism subtleties that GCN misses. The "
        "default ligand encoder in GraphDTA, DrugBAN, and "
        "StructGNN-DTA."
    ),
    inputs=[Port("in", "mol_graph_2d", "n_atoms × 78")],
    outputs=[Port("out", "ligand_embedding", "B × hidden")],
    params=[
        Param("hidden", "int", 128, label="Hidden dim", min=32, max=512, step=32),
        Param("layers", "int", 3,   label="GIN layers", min=1, max=6, step=1),
    ],
    backend="torch", cost="moderate", refs=["GraphDTA", "DrugBAN"], integrated=True,
    builder=_build_ligand_graph_gin,
))


# Fusion blocks
def _build_fusion_concat_mlp(params: dict):
    import torch.nn as nn
    # Input dim is computed at wiring time (sum of both encoder outs);
    # this builder will be re-parameterised once the flow executor lands.
    return None     # placeholder

register(BlockSpec(
    id="fusion.concat_mlp",
    role="fusion",
    impl="concat_mlp",
    label="Concat + MLP",
    category="fusion",
    short_desc="Concatenate embeddings, run through an MLP trunk.",
    long_desc=(
        "Cheapest fusion: concatenate the two encoded vectors along "
        "the feature axis, then pass through a small MLP. The "
        "baseline DTA fusion (DeepDTA, MolTrans-lite)."
    ),
    inputs=[Port("a", "embedding_1d"), Port("b", "embedding_1d")],
    outputs=[Port("out", "embedding_1d", "B × hidden")],
    params=[
        Param("hidden",  "int",   1024, label="Hidden dim", min=64, max=2048, step=64),
        Param("layers",  "int",   3,    label="MLP layers", min=1, max=6, step=1),
        Param("dropout", "float", 0.1,  label="Dropout", min=0.0, max=0.5, step=0.05),
    ],
    backend="torch", cost="fast", refs=["DeepDTA"], integrated=True,
    builder=None, notes="builder pending the flow executor",
))

register(BlockSpec(
    id="fusion.bilinear_attn",
    role="fusion",
    impl="bilinear_attn",
    label="Bilinear attention pool (DrugBAN)",
    category="fusion",
    short_desc="Low-rank bilinear pooling with learned attention.",
    long_desc=(
        "Captures pairwise feature interactions via a low-rank "
        "bilinear form U^T p ⊙ V^T l, with an attention vector to "
        "weight the resulting features. Used by DrugBAN, StructGNN-"
        "DTA, and PPI-Siamese."
    ),
    inputs=[Port("a", "embedding_1d"), Port("b", "embedding_1d")],
    outputs=[Port("out", "embedding_1d", "B × k")],
    params=[
        Param("k",        "int", 256, label="Bilinear rank", min=64, max=1024, step=64),
        Param("attn_dim", "int", 64,  label="Attention dim", min=16, max=256, step=16),
    ],
    backend="torch", cost="moderate", refs=["Bai 2023"], integrated=True,
    builder=None, notes="materialised inside DrugBAN / StructGNN_DTA today",
))

register(BlockSpec(
    id="fusion.two_tower_dot",
    role="fusion",
    impl="two_tower_dot",
    label="Two-tower dot product (ConPLex)",
    category="fusion",
    short_desc="Cosine / dot-product between L2-normed embeddings.",
    long_desc=(
        "Two-tower retrieval-friendly fusion: project each tower to a "
        "shared dim and compute dot product. Used for ranking heads "
        "(InfoNCE) and as the ConPLex scoring head."
    ),
    inputs=[Port("a", "embedding_1d"), Port("b", "embedding_1d")],
    outputs=[Port("out", "scalar")],
    params=[
        Param("normalize", "bool", True, label="L2-normalise before dot"),
        Param("shared_dim","int",  256,  label="Shared projection dim", min=32, max=1024, step=32),
    ],
    backend="torch", cost="trivial", refs=["ConPLex"], integrated=True,
    builder=None,
))

register(BlockSpec(
    id="fusion.fourway_siamese",
    role="fusion",
    impl="fourway_siamese",
    label="Four-way siamese fusion ([a; b; |a−b|; a*b])",
    category="fusion",
    short_desc="Symmetric fusion for PPI: concat, abs-diff, hadamard.",
    long_desc=(
        "Builds a (4·hidden)-d joint vector by concatenating: a, b, "
        "|a−b| (element-wise abs difference), a*b (element-wise "
        "product). Standard PPI siamese fusion. Inherently symmetric "
        "in (a, b) which matches PPI semantics."
    ),
    inputs=[Port("a", "embedding_1d"), Port("b", "embedding_1d")],
    outputs=[Port("out", "embedding_1d", "B × (4·hidden)")],
    params=[],
    backend="torch", cost="trivial", refs=["Hashemifar 2018"], integrated=True,
    builder=None, notes="materialised inside PPI_GNN_Siamese today",
))


# Heads
register(BlockSpec(
    id="head.regression.mlp",
    role="head.regression",
    impl="mlp",
    label="MLP regression head",
    category="head",
    short_desc="Trainable MLP → 1 scalar; MSE / Huber / Smooth-L1.",
    long_desc=(
        "Standard regression head: 2–3 Linear layers, ReLU + dropout, "
        "outputs a single scalar. Loss is configurable. The bias is "
        "initialised to the train-label mean to avoid the early-epoch "
        "RMSE/Pearson co-climbing artifact."
    ),
    inputs=[Port("in", "embedding_1d")],
    outputs=[Port("out", "scalar")],
    params=[
        Param("hidden",  "int",   512,    label="Hidden dim", min=32, max=2048, step=32),
        Param("layers",  "int",   3,      label="MLP layers", min=1, max=6, step=1),
        Param("dropout", "float", 0.1,    label="Dropout", min=0.0, max=0.5, step=0.05),
        Param("loss",    "enum",  "mse",  label="Loss",
              options=["mse", "huber", "smooth_l1"], sweepable=False),
        Param("huber_delta", "float", 1.0, label="Huber δ", min=0.1, max=5.0, step=0.1),
    ],
    backend="torch", cost="trivial", refs=["std"], integrated=True,
    builder=None, notes="every torch template owns its head today",
))

register(BlockSpec(
    id="head.regression.xgboost",
    role="head.regression",
    impl="xgboost",
    label="XGBoost regression head",
    category="head",
    short_desc="Gradient-boosted regression on fixed embeddings.",
    long_desc=(
        "After all upstream torch encoders/fusion produce a frozen "
        "embedding, XGBoost is fit on the (embedding, label) pairs. "
        "Useful when you have ≤ a few hundred thousand training "
        "examples and want a strong baseline with no fine-tuning. "
        "NOT differentiable end-to-end — the trainer dispatches a "
        "fit-after-feature-extract path."
    ),
    inputs=[Port("in", "embedding_1d")],
    outputs=[Port("out", "scalar")],
    params=[
        Param("n_estimators", "int", 500, label="Trees", min=50, max=5000, step=50),
        Param("max_depth",    "int", 6,   label="Max depth", min=3, max=20, step=1),
        Param("learning_rate","float", 0.05, label="LR", min=0.001, max=0.5, step=0.005, log_scale=True),
        Param("subsample",    "float", 0.8, label="Subsample", min=0.5, max=1.0, step=0.05),
        Param("reg_lambda",   "float", 1.0, label="L2 reg", min=0.0, max=10.0, step=0.1, log_scale=True),
    ],
    backend="xgboost", cost="moderate", refs=["Chen 2016"], integrated=False,
    notes="awaiting the trainer's xgboost dispatch path",
))

register(BlockSpec(
    id="head.regression.catboost",
    role="head.regression",
    impl="catboost",
    label="CatBoost regression head",
    category="head",
    short_desc="Symmetric gradient boosting on fixed embeddings.",
    long_desc=(
        "Same dispatch as XGBoost head — fit on frozen embeddings "
        "after the torch graph runs. CatBoost is often a stronger "
        "default than XGBoost on dense embeddings and has built-in "
        "categorical handling, but is GPU-bound."
    ),
    inputs=[Port("in", "embedding_1d")],
    outputs=[Port("out", "scalar")],
    params=[
        Param("iterations",   "int", 1000, label="Iterations", min=100, max=10000, step=100),
        Param("depth",        "int", 6,    label="Depth", min=2, max=16, step=1),
        Param("learning_rate","float", 0.05, label="LR", min=0.001, max=0.5, step=0.005, log_scale=True),
        Param("l2_leaf_reg",  "float", 3.0,  label="L2 leaf reg", min=0.0, max=30.0, step=0.5),
    ],
    backend="catboost", cost="moderate", refs=["Prokhorenkova 2018"], integrated=False,
    notes="awaiting the trainer's catboost dispatch path",
))


register(BlockSpec(
    id="head.binary.mlp",
    role="head.binary",
    impl="mlp",
    label="MLP binary classifier",
    category="head",
    short_desc="Trainable MLP → logits; BCEWithLogitsLoss.",
    long_desc=(
        "Standard binary classification head: MLP trunk emits logits, "
        "loss is BCE-with-logits, bias initialised to logit(p_pos) so "
        "the head starts at the population prior. Used by the PPI "
        "siamese template."
    ),
    inputs=[Port("in", "embedding_1d")],
    outputs=[Port("out", "prob")],
    params=[
        Param("hidden",  "int",   256, label="Hidden dim", min=32, max=1024, step=32),
        Param("layers",  "int",   3,   label="MLP layers", min=1, max=6, step=1),
        Param("dropout", "float", 0.2, label="Dropout", min=0.0, max=0.5, step=0.05),
    ],
    backend="torch", cost="trivial", refs=["std"], integrated=True,
    builder=None, notes="materialised inside PPI_GNN_Siamese today",
))

register(BlockSpec(
    id="head.binary.xgboost",
    role="head.binary",
    impl="xgboost",
    label="XGBoost binary classifier",
    category="head",
    short_desc="Gradient-boosted binary classification on fixed embeddings.",
    long_desc="Same dispatch as the regression XGBoost head; objective=binary:logistic.",
    inputs=[Port("in", "embedding_1d")],
    outputs=[Port("out", "prob")],
    params=[
        Param("n_estimators", "int", 500, label="Trees", min=50, max=5000, step=50),
        Param("max_depth",    "int", 6,   label="Max depth", min=3, max=20, step=1),
        Param("learning_rate","float", 0.05, label="LR", min=0.001, max=0.5, step=0.005, log_scale=True),
    ],
    backend="xgboost", cost="moderate", refs=["Chen 2016"], integrated=False,
    notes="awaiting the trainer's xgboost dispatch path",
))


# ── Template → block-composition presets (read-only) ─────────────────
# Maps every existing TEMPLATE_BUILDERS entry to its equivalent
# composition of block IDs + wiring. Used by the new flow builder's
# "Load preset" menu to seed a canvas with the right blocks already
# wired. NOT a replacement for TEMPLATE_BUILDERS — the trainer still
# dispatches by template_id today; this just gives the GUI a way to
# show "what would DeepDTA look like in the flow builder?"

TEMPLATE_PRESETS: dict[str, dict] = {
    "deepdta": {
        "label": "DeepDTA (Öztürk 2018)",
        "blocks": [
            {"id": "p_in",  "block": "input.protein_seq.tokens",     "params": {"max_len": 1000}},
            {"id": "l_in",  "block": "input.ligand_smiles.tokens",   "params": {"max_len": 100}},
            {"id": "p_enc", "block": "encoder.protein_seq.cnn1d",    "params": {"filters": 32, "kernel_size": 8}},
            {"id": "l_enc", "block": "encoder.ligand_seq.cnn1d",     "params": {"filters": 32, "kernel_size": 4}},
            {"id": "fuse",  "block": "fusion.concat_mlp",            "params": {"hidden": 1024, "layers": 3}},
            {"id": "head",  "block": "head.regression.mlp",          "params": {"hidden": 1024, "layers": 3}},
        ],
        "wires": [
            {"from": "p_in.out",  "to": "p_enc.in"},
            {"from": "l_in.out",  "to": "l_enc.in"},
            {"from": "p_enc.out", "to": "fuse.a"},
            {"from": "l_enc.out", "to": "fuse.b"},
            {"from": "fuse.out",  "to": "head.in"},
        ],
    },
    "graphdta": {
        "label": "GraphDTA (Nguyen 2021)",
        "blocks": [
            {"id": "p_in",  "block": "input.protein_seq.tokens"},
            {"id": "l_in",  "block": "input.ligand_graph.rdkit"},
            {"id": "p_enc", "block": "encoder.protein_seq.cnn1d"},
            {"id": "l_enc", "block": "encoder.ligand_graph.gin",     "params": {"hidden": 128, "layers": 3}},
            {"id": "fuse",  "block": "fusion.concat_mlp",            "params": {"hidden": 1024, "layers": 3}},
            {"id": "head",  "block": "head.regression.mlp"},
        ],
        "wires": [
            {"from": "p_in.out",  "to": "p_enc.in"},
            {"from": "l_in.out",  "to": "l_enc.in"},
            {"from": "p_enc.out", "to": "fuse.a"},
            {"from": "l_enc.out", "to": "fuse.b"},
            {"from": "fuse.out",  "to": "head.in"},
        ],
    },
    "drugban": {
        "label": "DrugBAN (Bai 2023)",
        "blocks": [
            {"id": "p_in",  "block": "input.protein_seq.tokens"},
            {"id": "l_in",  "block": "input.ligand_graph.rdkit"},
            {"id": "p_enc", "block": "encoder.protein_seq.cnn1d"},
            {"id": "l_enc", "block": "encoder.ligand_graph.gin"},
            {"id": "fuse",  "block": "fusion.bilinear_attn",         "params": {"k": 256, "attn_dim": 64}},
            {"id": "head",  "block": "head.regression.mlp"},
        ],
        "wires": [
            {"from": "p_in.out",  "to": "p_enc.in"},
            {"from": "l_in.out",  "to": "l_enc.in"},
            {"from": "p_enc.out", "to": "fuse.a"},
            {"from": "l_enc.out", "to": "fuse.b"},
            {"from": "fuse.out",  "to": "head.in"},
        ],
    },
    "struct_gnn_dta": {
        "label": "StructGNN-DTA (residue graph + ligand graph)",
        "blocks": [
            {"id": "p_in",  "block": "input.protein_residue_graph.contact"},
            {"id": "l_in",  "block": "input.ligand_graph.rdkit"},
            {"id": "p_enc", "block": "encoder.protein_graph.gcn",    "params": {"hidden": 128, "layers": 3}},
            {"id": "l_enc", "block": "encoder.ligand_graph.gin"},
            {"id": "fuse",  "block": "fusion.bilinear_attn"},
            {"id": "head",  "block": "head.regression.mlp"},
        ],
        "wires": [
            {"from": "p_in.out",  "to": "p_enc.in"},
            {"from": "l_in.out",  "to": "l_enc.in"},
            {"from": "p_enc.out", "to": "fuse.a"},
            {"from": "l_enc.out", "to": "fuse.b"},
            {"from": "fuse.out",  "to": "head.in"},
        ],
    },
    "ppi_gnn_siamese": {
        "label": "PPI siamese GNN (residue graph)",
        "blocks": [
            {"id": "a_in",  "block": "input.protein_residue_graph.contact"},
            {"id": "b_in",  "block": "input.protein_residue_graph.contact"},
            # Same encoder for both inputs — the flow executor needs to
            # support shared-weight blocks; for now this preset is a
            # display-only stub.
            {"id": "enc",   "block": "encoder.protein_graph.gcn"},
            {"id": "fuse",  "block": "fusion.fourway_siamese"},
            {"id": "head",  "block": "head.binary.mlp"},
        ],
        "wires": [
            {"from": "a_in.out", "to": "enc.in"},
            {"from": "b_in.out", "to": "enc.in"},
            {"from": "enc.out",  "to": "fuse.a"},
            {"from": "enc.out",  "to": "fuse.b"},
            {"from": "fuse.out", "to": "head.in"},
        ],
    },
}


def list_presets() -> dict:
    """Catalog of template → block-composition presets for the GUI."""
    return {
        "items": [
            {"id": k, **{kk: vv for kk, vv in v.items()}}
            for k, v in TEMPLATE_PRESETS.items()
        ]
    }


# ── Type compatibility helper ────────────────────────────────────────
# Single source of truth for "can wire X to Y?" used by both the GUI
# (mirrored as a JS function) and any future server-side validation.
# Most types only match themselves, but a few aliases are allowed:
#   * protein_embedding / ligand_embedding / embedding_1d are
#     interchangeable as 1-d embeddings (they're the same shape)
#   * tabular ↔ embedding_1d (same shape, semantically different)

_TYPE_ALIASES: dict[str, set[str]] = {
    "embedding_1d":       {"embedding_1d", "protein_embedding", "ligand_embedding", "tabular"},
    "protein_embedding":  {"protein_embedding", "embedding_1d"},
    "ligand_embedding":   {"ligand_embedding", "embedding_1d"},
    "tabular":            {"tabular", "embedding_1d"},
}

def types_compatible(out_type: str, in_type: str) -> bool:
    """True if a wire from an output of ``out_type`` can feed an input
    of ``in_type``. Exact match always passes; otherwise checks the
    aliases table."""
    if out_type == in_type:
        return True
    return in_type in _TYPE_ALIASES.get(out_type, set())
