"""DeepDTA in PyTorch.

Faithful port of Öztürk 2018 "DeepDTA: deep drug-target binding affinity
prediction". The model is two parallel 1D-CNN towers (protein, ligand)
followed by global max pool, concat, three fully-connected layers, scalar
regression output.

This file is the canonical reference for how a Pipeline-tab template gets
turned into a torch nn.Module. Future templates (GraphDTA, MolTrans, etc.)
each add their own module here.
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F

from .dataset import CHARPROTLEN, CHARISOSMILEN


class CNNTower(nn.Module):
    """A 1D-CNN tower as used by DeepDTA. Embedding → three Conv1d with
    increasing filter counts → global max pool. Inputs are int token
    sequences, outputs are (B, out_dim) representations.
    """
    def __init__(
        self,
        vocab_size: int,
        embed_dim: int,
        num_filters: int,
        kernel_size: int,
        max_len: int,
    ):
        super().__init__()
        self.embed = nn.Embedding(vocab_size + 1, embed_dim, padding_idx=0)
        # Three Conv1d stages with widening channel count (paper-faithful).
        self.conv1 = nn.Conv1d(embed_dim, num_filters,     kernel_size)
        self.conv2 = nn.Conv1d(num_filters, num_filters * 2, kernel_size)
        self.conv3 = nn.Conv1d(num_filters * 2, num_filters * 3, kernel_size)
        self.out_dim = num_filters * 3
        self.max_len = max_len

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, L) int64
        h = self.embed(x)                # (B, L, E)
        h = h.transpose(1, 2)            # (B, E, L) for Conv1d
        h = F.relu(self.conv1(h))
        h = F.relu(self.conv2(h))
        h = F.relu(self.conv3(h))
        h = F.adaptive_max_pool1d(h, 1)  # (B, C, 1)
        return h.squeeze(-1)             # (B, C)


class DeepDTA(nn.Module):
    """The full DeepDTA architecture. Twin CNN towers + concat + MLP head."""
    def __init__(
        self,
        *,
        protein_filters: int = 32,
        protein_kernel: int = 8,
        ligand_filters: int = 32,
        ligand_kernel: int = 4,
        embed_dim: int = 128,
        head_hidden: int = 1024,
        head_dropout: float = 0.1,
        seq_len: int = 1000,
        smi_len: int = 100,
    ):
        super().__init__()
        self.protein_tower = CNNTower(
            vocab_size=CHARPROTLEN, embed_dim=embed_dim,
            num_filters=protein_filters, kernel_size=protein_kernel,
            max_len=seq_len,
        )
        self.ligand_tower = CNNTower(
            vocab_size=CHARISOSMILEN, embed_dim=embed_dim,
            num_filters=ligand_filters, kernel_size=ligand_kernel,
            max_len=smi_len,
        )
        joint_dim = self.protein_tower.out_dim + self.ligand_tower.out_dim
        self.head = nn.Sequential(
            nn.Linear(joint_dim, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden // 2),
            nn.ReLU(),
            nn.Linear(head_hidden // 2, 1),
        )

    def forward(self, seq: torch.Tensor, smi: torch.Tensor) -> torch.Tensor:
        p = self.protein_tower(seq)
        l = self.ligand_tower(smi)
        h = torch.cat([p, l], dim=-1)
        y = self.head(h).squeeze(-1)
        return y


class BaselineMLP(nn.Module):
    """Quick smoke-test architecture — bag-of-tokens + MLP.

    Encoders: mean-embed the token sequences down to fixed vectors (no
    convolutions, no positional info). Cheap to instantiate, runs an epoch
    of Davis in seconds on GPU. Useful to verify the data pipe before
    spending compute on heavier models.
    """
    def __init__(
        self,
        *,
        embed_dim: int = 64,
        head_hidden: int = 256,
        head_dropout: float = 0.1,
    ):
        super().__init__()
        self.protein_embed = nn.Embedding(CHARPROTLEN + 1, embed_dim, padding_idx=0)
        self.ligand_embed  = nn.Embedding(CHARISOSMILEN + 1, embed_dim, padding_idx=0)
        self.head = nn.Sequential(
            nn.Linear(embed_dim * 2, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden // 2),
            nn.ReLU(),
            nn.Linear(head_hidden // 2, 1),
        )

    def forward(self, seq: torch.Tensor, smi: torch.Tensor) -> torch.Tensor:
        p = self.protein_embed(seq).mean(dim=1)
        l = self.ligand_embed(smi).mean(dim=1)
        h = torch.cat([p, l], dim=-1)
        return self.head(h).squeeze(-1)


class TabularFeatureMLP(nn.Module):
    """Architecture that consumes ANY combination of featurizers from the
    registry as one big concatenated tabular input.

    Inputs:
        feats — (B, total_dim) float32 tensor. The trainer concatenates
                whatever the run's featurizer list produces.

    Architecture:
        BN → Linear → ReLU → Dropout → Linear → ReLU → Dropout → Linear
        Optional: separate ligand-side / protein-side input branches that
        are concatenated before the trunk. Controlled by the
        ``ligand_dim`` and ``protein_dim`` hparams.
    """
    def __init__(
        self,
        *,
        input_dim: int,
        ligand_dim: int | None = None,
        protein_dim: int | None = None,
        trunk_hidden: int = 512,
        n_trunk_layers: int = 3,
        dropout: float = 0.2,
        use_input_norm: bool = True,
    ):
        super().__init__()
        # If the caller declared the ligand/protein split, build two
        # input MLPs that project each axis into a fixed dim before
        # concatenation — gives the model a chance to learn axis-specific
        # transformations on the raw featurizer outputs.
        self.use_split = ligand_dim is not None and protein_dim is not None
        if self.use_split:
            proj_dim = max(64, trunk_hidden // 4)
            self.ligand_proj = nn.Sequential(
                nn.LayerNorm(ligand_dim),
                nn.Linear(ligand_dim, proj_dim),
                nn.ReLU(),
            )
            self.protein_proj = nn.Sequential(
                nn.LayerNorm(protein_dim),
                nn.Linear(protein_dim, proj_dim),
                nn.ReLU(),
            )
            trunk_in = proj_dim * 2
        else:
            self.input_norm = nn.LayerNorm(input_dim) if use_input_norm else nn.Identity()
            trunk_in = input_dim

        # Trunk MLP
        layers: list[nn.Module] = []
        prev = trunk_in
        for _ in range(n_trunk_layers):
            layers.extend([
                nn.Linear(prev, trunk_hidden),
                nn.ReLU(),
                nn.Dropout(dropout),
            ])
            prev = trunk_hidden
        layers.append(nn.Linear(prev, 1))
        self.trunk = nn.Sequential(*layers)
        self.ligand_dim = ligand_dim
        self.protein_dim = protein_dim
        self.input_dim = input_dim

    def forward(self, feats: torch.Tensor) -> torch.Tensor:
        if self.use_split and self.ligand_dim and self.protein_dim:
            l = self.ligand_proj(feats[:, :self.ligand_dim])
            p = self.protein_proj(feats[:, self.ligand_dim:self.ligand_dim + self.protein_dim])
            h = torch.cat([l, p], dim=-1)
        else:
            h = self.input_norm(feats)
        return self.trunk(h).squeeze(-1)


class ConPLex(nn.Module):
    """Two-tower contrastive-style DTA: ESM-2 protein tower + ChemBERTa
    ligand tower, joined by a learned dot-product over a shared latent
    space. Faithful to the Singh 2023 paper's geometry except both
    towers are FROZEN to their pretrained weights (we just learn the
    projection heads + dot-product temperature). Saves a ton of compute
    vs full fine-tuning and matches the published recipe.

    Inputs at forward time:
        prot_embed — (B, 320) ESM-2 8M mean-pool embedding
        lig_embed  — (B, 768) ChemBERTa mean-pool embedding
    """
    def __init__(
        self,
        *,
        protein_emb_dim: int = 320,
        ligand_emb_dim:  int = 768,
        shared_dim:      int = 256,
        dropout:         float = 0.1,
        use_dot_product: bool = True,
    ):
        super().__init__()
        self.protein_proj = nn.Sequential(
            nn.LayerNorm(protein_emb_dim),
            nn.Linear(protein_emb_dim, shared_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(shared_dim, shared_dim),
        )
        self.ligand_proj = nn.Sequential(
            nn.LayerNorm(ligand_emb_dim),
            nn.Linear(ligand_emb_dim, shared_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(shared_dim, shared_dim),
        )
        # Learned temperature for the dot product
        self.log_tau = nn.Parameter(torch.zeros(1))
        # MLP head for non-dot-product mode (lets the user swap the
        # ranking objective for a regression head if the run is
        # actually trying to predict pKd values).
        self.use_dot_product = use_dot_product
        self.head = nn.Sequential(
            nn.Linear(shared_dim * 2, shared_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(shared_dim, 1),
        )

    def forward(self, prot_embed: torch.Tensor, lig_embed: torch.Tensor) -> torch.Tensor:
        p = self.protein_proj(prot_embed)
        l = self.ligand_proj(lig_embed)
        if self.use_dot_product:
            # Cosine-style similarity scaled by learned temperature
            p_n = nn.functional.normalize(p, dim=-1)
            l_n = nn.functional.normalize(l, dim=-1)
            score = (p_n * l_n).sum(dim=-1) * torch.exp(self.log_tau)
            return score
        h = torch.cat([p, l], dim=-1)
        return self.head(h).squeeze(-1)


class ThermoMLP(nn.Module):
    """Thermodynamic-feature-only MLP — uses 14 hand-crafted entropy +
    drug-likeness features (8 ligand + 6 protein) instead of learning
    representations from tokens.

    Inputs:
        feats — (B, 14) float32 tensor of joint thermo features from
                :func:`thermodynamic_features.joint_thermo_features`.

    Useful as a sanity-check baseline: if this model has comparable
    metrics to DeepDTA on cold-target splits, the token/CNN models
    aren't actually learning anything beyond the trivial physico-chemical
    signal.
    """
    def __init__(
        self,
        *,
        n_features: int = 14,
        head_hidden: int = 128,
        head_dropout: float = 0.1,
    ):
        super().__init__()
        self.head = nn.Sequential(
            nn.Linear(n_features, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, 1),
        )

    def forward(self, feats: torch.Tensor) -> torch.Tensor:
        return self.head(feats).squeeze(-1)


class MolTransLite(nn.Module):
    """Twin transformer towers, randomly initialised (no PLM weights).

    A real MolTrans uses ProtBERT + ChemBERTa pretrained weights.
    This is the "lite" variant: same architecture shape but trained from
    scratch, so it fits in a Davis run on one GPU without downloading
    multi-GB checkpoints. Useful for ablations and as a fallback when the
    pretrained weights aren't available.

    Each tower:  Embedding → 2 TransformerEncoder layers → mean-pool over tokens.
    Fusion: cross-attention from protein → ligand, then a small MLP head.
    """
    def __init__(
        self,
        *,
        embed_dim: int = 128,
        n_heads: int = 4,
        n_layers: int = 2,
        ff_dim: int = 256,
        head_hidden: int = 256,
        head_dropout: float = 0.1,
        seq_len: int = 1000,
        smi_len: int = 100,
    ):
        super().__init__()
        self.embed_dim = embed_dim
        # Protein side
        self.p_embed = nn.Embedding(CHARPROTLEN + 1, embed_dim, padding_idx=0)
        self.p_pos   = nn.Embedding(seq_len, embed_dim)
        self.p_enc   = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(
                d_model=embed_dim, nhead=n_heads, dim_feedforward=ff_dim,
                dropout=head_dropout, batch_first=True, activation="gelu",
            ),
            num_layers=n_layers,
        )
        # Ligand side
        self.l_embed = nn.Embedding(CHARISOSMILEN + 1, embed_dim, padding_idx=0)
        self.l_pos   = nn.Embedding(smi_len, embed_dim)
        self.l_enc   = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(
                d_model=embed_dim, nhead=n_heads, dim_feedforward=ff_dim,
                dropout=head_dropout, batch_first=True, activation="gelu",
            ),
            num_layers=n_layers,
        )
        # Cross-attention (protein attends to ligand)
        self.cross_attn = nn.MultiheadAttention(
            embed_dim=embed_dim, num_heads=n_heads,
            dropout=head_dropout, batch_first=True,
        )
        self.head = nn.Sequential(
            nn.Linear(embed_dim * 2, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, 1),
        )
        self._seq_len, self._smi_len = seq_len, smi_len

    def _encode(self, x: torch.Tensor, embed: nn.Embedding, pos: nn.Embedding,
                enc: nn.TransformerEncoder, max_len: int) -> tuple[torch.Tensor, torch.Tensor]:
        B, L = x.shape
        pos_ids = torch.arange(L, device=x.device).unsqueeze(0).expand(B, L)
        h = embed(x) + pos(pos_ids)
        pad_mask = (x == 0)            # (B, L) bool, True = pad
        out = enc(h, src_key_padding_mask=pad_mask)
        return out, pad_mask

    def forward(self, seq: torch.Tensor, smi: torch.Tensor) -> torch.Tensor:
        p_h, p_mask = self._encode(seq, self.p_embed, self.p_pos, self.p_enc, self._seq_len)
        l_h, l_mask = self._encode(smi, self.l_embed, self.l_pos, self.l_enc, self._smi_len)
        # Cross-attend: protein tokens query ligand context
        cx, _ = self.cross_attn(p_h, l_h, l_h, key_padding_mask=l_mask)
        # Mean-pool (mask-aware)
        p_pool = _masked_mean(cx,   ~p_mask)
        l_pool = _masked_mean(l_h,  ~l_mask)
        h = torch.cat([p_pool, l_pool], dim=-1)
        return self.head(h).squeeze(-1)


def _masked_mean(x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    """Mean of x along dim=1, weighted by mask. x: (B, L, D), mask: (B, L) bool."""
    m = mask.float().unsqueeze(-1)
    s = (x * m).sum(dim=1)
    d = m.sum(dim=1).clamp(min=1e-6)
    return s / d


# ── GraphDTA (Nguyen 2021) — GIN ligand tower + 1D-CNN protein tower ───
# This branch only loads if torch_geometric is installed. It's an
# optional heavy import, gated at construction time.

class GraphDTA(nn.Module):
    """Protein 1D-CNN + ligand GIN graph encoder, concat → MLP.

    Architecture follows the GraphDTA paper (Nguyen et al. 2021):
        Protein side: same CNN tower as DeepDTA
        Ligand side:  3-layer GIN over the molecular graph, global mean pool
        Fusion:       concat → 3-layer MLP regression head

    Inputs at forward time are different from sequence-only models — the
    ligand side takes a torch_geometric.data.Batch instead of a token
    tensor. The trainer's collate_fn handles the dispatch.
    """
    def __init__(
        self,
        *,
        protein_filters: int = 32,
        protein_kernel: int = 8,
        gin_hidden: int = 128,
        gin_layers: int = 3,
        embed_dim: int = 128,
        head_hidden: int = 1024,
        head_dropout: float = 0.1,
    ):
        super().__init__()
        from .featurizers import ATOM_FEAT_DIM
        try:
            from torch_geometric.nn import GINConv, global_mean_pool
        except ImportError as exc:
            raise RuntimeError(
                "GraphDTA requires torch_geometric. Install it via "
                "`pip install torch_geometric` or pick a different template."
            ) from exc

        # Protein CNN tower (reuse the DeepDTA pattern)
        self.protein_tower = CNNTower(
            vocab_size=CHARPROTLEN, embed_dim=embed_dim,
            num_filters=protein_filters, kernel_size=protein_kernel,
            max_len=1000,
        )

        # Ligand GIN tower — 3 GINConv layers, each with a 2-layer MLP
        # internal aggregator, expanding from ATOM_FEAT_DIM → gin_hidden.
        gin_layer_list = []
        in_dim = ATOM_FEAT_DIM
        for _ in range(gin_layers):
            mlp = nn.Sequential(
                nn.Linear(in_dim, gin_hidden),
                nn.ReLU(),
                nn.Linear(gin_hidden, gin_hidden),
            )
            gin_layer_list.append(GINConv(mlp))
            in_dim = gin_hidden
        self.gin_layers = nn.ModuleList(gin_layer_list)
        self._global_mean_pool = global_mean_pool

        joint_dim = self.protein_tower.out_dim + gin_hidden
        self.head = nn.Sequential(
            nn.Linear(joint_dim, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden // 2),
            nn.ReLU(),
            nn.Linear(head_hidden // 2, 1),
        )

    def forward(self, seq: torch.Tensor, graph) -> torch.Tensor:
        """seq: (B, 1000) int; graph: torch_geometric.data.Batch."""
        p = self.protein_tower(seq)
        x, ei, batch = graph.x, graph.edge_index, graph.batch
        for layer in self.gin_layers:
            x = F.relu(layer(x, ei))
        l = self._global_mean_pool(x, batch)   # (B, gin_hidden)
        h = torch.cat([p, l], dim=-1)
        return self.head(h).squeeze(-1)


# ── DrugBAN (Bai 2023) — CNN + GIN + bilinear attention fusion ─────────

class _BilinearAttentionPool(nn.Module):
    """Low-rank bilinear attention pooling.

    Given protein representation ``p`` of shape (B, Dp) and ligand
    representation ``l`` of shape (B, Dl), compute a joint embedding via
    a learned bilinear form (factorised into U ∈ (Dp, k) and V ∈ (Dl, k))
    plus a learned attention vector q ∈ (k,):

        joint = Σ_k softmax(qᵀ tanh(Uᵀp ⊙ Vᵀl))[k] · (Uᵀp ⊙ Vᵀl)

    Captures pairwise feature interactions that a plain concat MLP would
    have to discover layer-by-layer.
    """
    def __init__(self, p_dim: int, l_dim: int, k: int = 256, attn_dim: int = 64):
        super().__init__()
        self.U = nn.Linear(p_dim, k, bias=False)
        self.V = nn.Linear(l_dim, k, bias=False)
        self.attn_q = nn.Linear(k, attn_dim)
        self.attn_out = nn.Linear(attn_dim, 1, bias=False)
        self.k = k

    def forward(self, p: torch.Tensor, l: torch.Tensor) -> torch.Tensor:
        # (B, k)
        u = self.U(p)
        v = self.V(l)
        joint = torch.tanh(u * v)               # (B, k)
        attn = self.attn_out(torch.tanh(self.attn_q(joint)))   # (B, 1)
        attn = torch.softmax(attn.expand_as(joint), dim=-1)    # (B, k)
        return (attn * joint).sum(dim=-1, keepdim=False).unsqueeze(-1).expand_as(joint) * joint
        # Note: the conventional formulation gives a (B, k) vector after the
        # weighted sum across the k dimension, but here we keep the
        # element-wise weighted joint vector for the head MLP to consume.


class DrugBAN(nn.Module):
    """Protein 1D-CNN + ligand GIN, joined by low-rank bilinear attention
    pooling. Faithful to the Bai 2023 architecture except the bilinear
    layer is parameter-light (low-rank factorisation k=256 by default).
    """
    def __init__(
        self,
        *,
        protein_filters: int = 32,
        protein_kernel: int = 8,
        gin_hidden: int = 128,
        gin_layers: int = 3,
        embed_dim: int = 128,
        bilinear_k: int = 256,
        attn_dim: int = 64,
        head_hidden: int = 512,
        head_dropout: float = 0.1,
    ):
        super().__init__()
        from .featurizers import ATOM_FEAT_DIM
        try:
            from torch_geometric.nn import GINConv, global_mean_pool
        except ImportError as exc:
            raise RuntimeError(
                "DrugBAN requires torch_geometric. Install it via "
                "`pip install torch_geometric` or pick a different template."
            ) from exc

        # Protein CNN tower (same as DeepDTA / GraphDTA)
        self.protein_tower = CNNTower(
            vocab_size=CHARPROTLEN, embed_dim=embed_dim,
            num_filters=protein_filters, kernel_size=protein_kernel,
            max_len=1000,
        )
        # Ligand GIN tower
        gin_layer_list: list[nn.Module] = []
        in_dim = ATOM_FEAT_DIM
        for _ in range(gin_layers):
            mlp = nn.Sequential(
                nn.Linear(in_dim, gin_hidden),
                nn.ReLU(),
                nn.Linear(gin_hidden, gin_hidden),
            )
            gin_layer_list.append(GINConv(mlp))
            in_dim = gin_hidden
        self.gin_layers = nn.ModuleList(gin_layer_list)
        self._global_mean_pool = global_mean_pool

        self.bilinear = _BilinearAttentionPool(
            p_dim=self.protein_tower.out_dim,
            l_dim=gin_hidden,
            k=bilinear_k,
            attn_dim=attn_dim,
        )
        self.head = nn.Sequential(
            nn.Linear(bilinear_k, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden // 2),
            nn.ReLU(),
            nn.Linear(head_hidden // 2, 1),
        )

    def forward(self, seq: torch.Tensor, graph) -> torch.Tensor:
        p = self.protein_tower(seq)
        x, ei, batch = graph.x, graph.edge_index, graph.batch
        for layer in self.gin_layers:
            x = F.relu(layer(x, ei))
        l = self._global_mean_pool(x, batch)
        joint = self.bilinear(p, l)              # (B, bilinear_k)
        return self.head(joint).squeeze(-1)


# ── StructGNN_DTA — protein residue-graph GCN + ligand mol-graph GIN ─
# A first-class structure-aware DTA template. Both sides are GNNs:
#   Protein: residue-level graph built by ``graph_features.protein_residue_graph``
#            (PDB-derived contact edges when AlphaFold cache hits, sliding-
#            window fallback otherwise). Encoded with GCNConv layers +
#            global_mean_pool.
#   Ligand:  2D molecular graph (GraphDTA atom features), GIN encoder.
#   Fusion:  low-rank bilinear (DrugBAN-style) then a 3-layer MLP head.
#
# The protein side intentionally uses GCN (cheap, well-understood) rather
# than GIN — GIN's discriminative power is more relevant for chemistry
# graphs where graph isomorphism actually distinguishes molecules; for
# residue contact graphs the local averaging of GCN is a fine default.

class StructGNN_DTA(nn.Module):
    """Protein residue-graph GCN + ligand mol-graph GIN, bilinear fusion.

    Forward takes ``(prot_graph_batch, lig_graph_batch)`` — two
    torch_geometric Batches produced by ``struct_graph_collate`` in
    ``dataset_warehouse.py``. Returns a (B,) tensor of pKd / kiba_score.
    """

    def __init__(
        self,
        *,
        residue_feat_dim: int = 23,
        prot_hidden: int = 128,
        prot_layers: int = 3,
        gin_hidden: int = 128,
        gin_layers: int = 3,
        bilinear_k: int = 256,
        head_hidden: int = 512,
        head_dropout: float = 0.1,
    ):
        super().__init__()
        from .featurizers import ATOM_FEAT_DIM
        try:
            from torch_geometric.nn import GCNConv, GINConv, global_mean_pool
        except ImportError as exc:
            raise RuntimeError(
                "StructGNN_DTA requires torch_geometric. Install via "
                "`pip install torch_geometric` or pick a different template."
            ) from exc

        # Protein GCN tower
        prot_layer_list = []
        in_dim = residue_feat_dim
        for _ in range(prot_layers):
            prot_layer_list.append(GCNConv(in_dim, prot_hidden, add_self_loops=True))
            in_dim = prot_hidden
        self.prot_gnn = nn.ModuleList(prot_layer_list)

        # Ligand GIN tower (same recipe as GraphDTA)
        gin_layer_list = []
        in_dim = ATOM_FEAT_DIM
        for _ in range(gin_layers):
            mlp = nn.Sequential(
                nn.Linear(in_dim, gin_hidden),
                nn.ReLU(),
                nn.Linear(gin_hidden, gin_hidden),
            )
            gin_layer_list.append(GINConv(mlp))
            in_dim = gin_hidden
        self.lig_gnn = nn.ModuleList(gin_layer_list)

        self._global_mean_pool = global_mean_pool

        # Low-rank bilinear fusion (DrugBAN-style) — reuses _BilinearAttentionPool
        # which is already defined above in this file for DrugBAN.
        self.bilinear = _BilinearAttentionPool(
            p_dim=prot_hidden, l_dim=gin_hidden,
            k=bilinear_k, attn_dim=64,
        )

        # Regression head
        self.head = nn.Sequential(
            nn.Linear(bilinear_k, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden // 2),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden // 2, 1),
        )

    def forward(self, prot_graph, lig_graph) -> torch.Tensor:
        # Protein: GCN message passing over residue contact graph
        x, ei, batch = prot_graph.x, prot_graph.edge_index, prot_graph.batch
        for layer in self.prot_gnn:
            x = F.relu(layer(x, ei))
        p = self._global_mean_pool(x, batch)        # (B, prot_hidden)

        # Ligand: GIN over molecular graph
        x, ei, batch = lig_graph.x, lig_graph.edge_index, lig_graph.batch
        for layer in self.lig_gnn:
            x = F.relu(layer(x, ei))
        l = self._global_mean_pool(x, batch)        # (B, gin_hidden)

        joint = self.bilinear(p, l)                  # (B, bilinear_k)
        return self.head(joint).squeeze(-1)


# ── PPI_GNN_Siamese — two protein residue graphs through a shared GCN ─
# Predicts a binary interaction probability for a pair of proteins.
# Architecture:
#   GCN tower (shared weights) → global_mean_pool per protein
#   Fusion = concat(a, b, |a-b|, a*b) → MLP → sigmoid
#   Loss   = BCEWithLogitsLoss in the trainer (we return raw logits here)
#
# The 4-way fusion (concat / absdiff / hadamard) is a standard PPI
# baseline (Hashemifar 2018, Sun 2017). It gives the head richer
# information than a simple dot product without adding many parameters.

class PPI_GNN_Siamese(nn.Module):
    def __init__(
        self,
        *,
        residue_feat_dim: int = 23,
        hidden: int = 128,
        n_layers: int = 3,
        head_hidden: int = 256,
        head_dropout: float = 0.2,
    ):
        super().__init__()
        try:
            from torch_geometric.nn import GCNConv, global_mean_pool
        except ImportError as exc:
            raise RuntimeError(
                "PPI_GNN_Siamese requires torch_geometric."
            ) from exc
        layer_list = []
        in_dim = residue_feat_dim
        for _ in range(n_layers):
            layer_list.append(GCNConv(in_dim, hidden, add_self_loops=True))
            in_dim = hidden
        self.gnn = nn.ModuleList(layer_list)
        self._global_mean_pool = global_mean_pool
        # 4× hidden: [a; b; |a-b|; a*b]
        self.head = nn.Sequential(
            nn.Linear(4 * hidden, head_hidden),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden, head_hidden // 2),
            nn.ReLU(),
            nn.Dropout(head_dropout),
            nn.Linear(head_hidden // 2, 1),
        )

    def _encode(self, graph) -> torch.Tensor:
        x, ei, batch = graph.x, graph.edge_index, graph.batch
        for layer in self.gnn:
            x = F.relu(layer(x, ei))
        return self._global_mean_pool(x, batch)        # (B, hidden)

    def forward(self, graph_a, graph_b) -> torch.Tensor:
        a = self._encode(graph_a)
        b = self._encode(graph_b)
        # Symmetric 4-way fusion (Hashemifar 2018 / Sun 2017 baseline).
        joint = torch.cat([a, b, (a - b).abs(), a * b], dim=-1)
        return self.head(joint).squeeze(-1)            # logits


def count_parameters(m: nn.Module) -> int:
    """Trainable param count, used for the run-summary."""
    return sum(p.numel() for p in m.parameters() if p.requires_grad)


# Template → builder dispatcher. Each entry returns an nn.Module from an
# (optional) effective_config payload. Adding a new architecture is just
# a new entry here + an nn.Module class above.
def _build_deepdta(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    se = nodes.get("se", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return DeepDTA(
        protein_filters=int(pe.get("filters", 32)),
        protein_kernel =int(pe.get("kernel",   8)),
        ligand_filters =int(se.get("filters", 32)),
        ligand_kernel  =int(se.get("kernel",   4)),
        head_hidden    =int(f.get("hidden",  1024)),
        head_dropout   =float(f.get("dropout", 0.1)),
    )


def _build_baseline_mlp(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return BaselineMLP(
        embed_dim    =int(pe.get("embed_dim", 64)),
        head_hidden  =int(f.get("hidden",  256)),
        head_dropout =float(f.get("dropout", 0.1)),
    )


def _build_moltrans_lite(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    se = nodes.get("se", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return MolTransLite(
        embed_dim   =int(pe.get("embed_dim", se.get("embed_dim", 128))),
        n_heads     =int(pe.get("heads",     se.get("heads",       4))),
        n_layers    =int(pe.get("layers",    se.get("layers",      2))),
        ff_dim      =int(pe.get("ff_dim",    se.get("ff_dim",    256))),
        head_hidden =int(f.get("hidden",   256)),
        head_dropout=float(f.get("dropout", 0.1)),
    )


def _build_graphdta(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    ge = nodes.get("ge", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return GraphDTA(
        protein_filters=int(pe.get("filters", 32)),
        protein_kernel =int(pe.get("kernel",   8)),
        gin_hidden     =int(ge.get("hidden", 128)),
        gin_layers     =int(ge.get("layers",   3)),
        embed_dim      =int(pe.get("embed_dim", 128)),
        head_hidden    =int(f.get("hidden", 1024)),
        head_dropout   =float(f.get("dropout", 0.1)),
    )


def _build_tabular_feature_mlp(cfg: dict) -> nn.Module:
    """Build a TabularFeatureMLP. Caller is expected to pass the total
    concatenated feature dim via cfg["nodes"]["f"]["params"]["input_dim"]
    OR via ligand_dim + protein_dim (the trainer fills those in once it
    knows which featurizers are selected)."""
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    f = nodes.get("f", {}).get("params", {})
    input_dim   = int(f.get("input_dim", 0))
    ligand_dim  = f.get("ligand_dim")
    protein_dim = f.get("protein_dim")
    if not input_dim and (ligand_dim or protein_dim):
        input_dim = int(ligand_dim or 0) + int(protein_dim or 0)
    if input_dim <= 0:
        # Sentinel — trainer will rebuild with the actual dim once features are known.
        input_dim = 16
    return TabularFeatureMLP(
        input_dim     =input_dim,
        ligand_dim    =int(ligand_dim)  if ligand_dim  is not None else None,
        protein_dim   =int(protein_dim) if protein_dim is not None else None,
        trunk_hidden  =int(f.get("hidden", 512)),
        n_trunk_layers=int(f.get("layers", 3)),
        dropout       =float(f.get("dropout", 0.2)),
    )


def _build_conplex(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    se = nodes.get("se", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return ConPLex(
        protein_emb_dim=int(pe.get("emb_dim", 320)),
        ligand_emb_dim =int(se.get("emb_dim", 768)),
        shared_dim     =int(f.get("shared_dim", 256)),
        dropout        =float(f.get("dropout", 0.1)),
        use_dot_product=bool(f.get("dot_product", True)),
    )


def _build_thermo_mlp(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    f = nodes.get("f", {}).get("params", {})
    from .thermodynamic_features import thermo_feature_dim
    return ThermoMLP(
        n_features  =thermo_feature_dim(),
        head_hidden =int(f.get("hidden", 128)),
        head_dropout=float(f.get("dropout", 0.1)),
    )


def _build_drugban(cfg: dict) -> nn.Module:
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    ge = nodes.get("ge", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return DrugBAN(
        protein_filters=int(pe.get("filters", 32)),
        protein_kernel =int(pe.get("kernel",   8)),
        gin_hidden     =int(ge.get("hidden", 128)),
        gin_layers     =int(ge.get("layers",   3)),
        embed_dim      =int(pe.get("embed_dim", 128)),
        bilinear_k     =int(f.get("k",  256)),
        attn_dim       =int(f.get("attn_dim", 64)),
        head_hidden    =int(f.get("hidden", 512)),
        head_dropout   =float(f.get("dropout", 0.1)),
    )


def _build_struct_gnn_dta(cfg: dict) -> nn.Module:
    """StructGNN_DTA: protein residue-graph GCN + ligand mol-graph GIN.

    Slot conventions matching the data.js template:
        pe → protein encoder (the protein GCN tower)
        ge → ligand encoder (the GIN tower)
        f  → fusion + head hyper-params
    """
    from .graph_features import RESIDUE_FEAT_DIM
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    pe = nodes.get("pe", {}).get("params", {})
    ge = nodes.get("ge", {}).get("params", {})
    f  = nodes.get("f",  {}).get("params", {})
    return StructGNN_DTA(
        residue_feat_dim=RESIDUE_FEAT_DIM,
        prot_hidden  =int(pe.get("hidden", 128)),
        prot_layers  =int(pe.get("layers",   3)),
        gin_hidden   =int(ge.get("hidden", 128)),
        gin_layers   =int(ge.get("layers",   3)),
        bilinear_k   =int(f.get("k",       256)),
        head_hidden  =int(f.get("hidden",  512)),
        head_dropout =float(f.get("dropout", 0.1)),
    )


TEMPLATE_BUILDERS: dict[str, callable] = {
    "deepdta":      _build_deepdta,
    "baseline_mlp": _build_baseline_mlp,
    "thermo_mlp":   _build_thermo_mlp,      # 14-dim thermo/entropy features only
    "tabular_mlp":  _build_tabular_feature_mlp,  # any combination of registry featurizers
    "moltrans":     _build_moltrans_lite,   # MolTrans-lite (no pretrained PLM)
    "graphdta":     _build_graphdta,        # GIN + CNN, requires torch_geometric
    "drugban":      _build_drugban,         # CNN + GIN + bilinear attention pooling
    "conplex":      _build_conplex,         # ESM-2 + ChemBERTa two-tower
    # Structure-aware GNN: protein residue contact graph (GCN) + ligand mol
    # graph (GIN). Uses cached AlphaFold PDBs when present, falls back to
    # sequence-only sliding-window graphs otherwise.
    "struct_gnn_dta": _build_struct_gnn_dta,
    # Siamese protein GCN for binary PPI prediction (HIPPIE / HuRI). Two
    # residue graphs share an encoder; 4-way fusion + MLP head emits
    # logits for BCEWithLogitsLoss.
    "ppi_gnn_siamese": "_build_ppi_gnn_siamese",   # patched below
}


def _build_ppi_gnn_siamese(cfg: dict) -> nn.Module:
    from .graph_features import RESIDUE_FEAT_DIM
    nodes = {n["slot_id"]: n for n in cfg.get("nodes", [])}
    e = nodes.get("e", {}).get("params", {})
    f = nodes.get("f", {}).get("params", {})
    return PPI_GNN_Siamese(
        residue_feat_dim=RESIDUE_FEAT_DIM,
        hidden       =int(e.get("hidden", 128)),
        n_layers     =int(e.get("layers",   3)),
        head_hidden  =int(f.get("hidden", 256)),
        head_dropout =float(f.get("dropout", 0.2)),
    )


# Patch the string placeholder with the real callable now that it exists.
TEMPLATE_BUILDERS["ppi_gnn_siamese"] = _build_ppi_gnn_siamese


def model_for_template(template_id: str, effective_config: dict | None = None) -> nn.Module:
    """Map a v2 pipeline template id → a torch nn.Module.

    Two paths:

    * **Named templates** (deepdta / graphdta / drugban / struct_gnn_dta /
      ppi_gnn_siamese / etc.) — dispatched through ``TEMPLATE_BUILDERS``.
    * **Flow templates** (template_id == "flow") — the user built the
      graph in the GUI's flow editor; the effective config carries a
      ``flow`` field with ``{nodes, edges}``. We compile that graph into
      a trainable nn.Module on the fly via ``flow_compiler.compile_flow``.

    Unknown template ids raise NotImplementedError; the HTTP launch
    handler converts that into a 501 with a crisp next-step message.
    """
    if template_id == "flow":
        from .flow_compiler import compile_flow, FlowCompileError
        flow = (effective_config or {}).get("flow") or {}
        if not flow.get("nodes"):
            raise NotImplementedError(
                "Template 'flow' requires effective_config.flow = "
                "{nodes: [...], edges: [...]}. Build your graph in the "
                "Pipeline (flow) screen first."
            )
        try:
            return compile_flow(flow)
        except FlowCompileError as exc:
            raise NotImplementedError(f"Flow compile failed: {exc}") from exc
    builder = TEMPLATE_BUILDERS.get(template_id)
    if builder is None:
        raise NotImplementedError(
            f"Template '{template_id}' is not yet implemented in the v2 backend. "
            f"Wired templates: {sorted(TEMPLATE_BUILDERS)}. Add an nn.Module class "
            "and a builder entry in api/model_studio/v2/models.py."
        )
    return builder(effective_config or {})
