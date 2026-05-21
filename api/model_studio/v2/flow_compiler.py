"""Compile a user-built flow graph (from the GUI) into a trainable nn.Module.

The Flow Builder screen produces a graph spec that looks like::

    {
      "nodes": [
        {"id": "n1", "block_id": "in.protein_seq",  "impl_id": "default", "params": {}},
        {"id": "n2", "block_id": "in.ligand_graph", "impl_id": "default", "params": {}},
        {"id": "n3", "block_id": "enc.protein_seq", "impl_id": "cnn",     "params": {"filters": 32, "kernel": 8}},
        {"id": "n4", "block_id": "enc.ligand_graph","impl_id": "gin",     "params": {"hidden": 128}},
        {"id": "n5", "block_id": "fuse",            "impl_id": "concat_mlp", "params": {"hidden": 1024}},
        {"id": "n6", "block_id": "head.regression", "impl_id": "pki",     "params": {"loss": "mse"}}
      ],
      "edges": [
        {"from": "n1:out", "to": "n3:in"},
        {"from": "n2:out", "to": "n4:in"},
        {"from": "n3:out", "to": "n5:a"},
        {"from": "n4:out", "to": "n5:b"},
        {"from": "n5:out", "to": "n6:in"}
      ]
    }

This module:
    1. Validates the graph (DAG, no orphan inputs, head present).
    2. Topologically orders the nodes.
    3. Looks each (block_id, impl_id) up in a dispatch table and builds
       an nn.Module for it.
    4. Returns a ``FlowModule`` that, at forward time, takes the raw
       (protein, ligand, y) batch the dataset produces and routes
       tensors through the user's graph.

This is the **minimum-viable executor** — it supports the most common
block/impl combinations that map to existing nn.Module classes in
``models.py`` and ``graph_features.py``. Unsupported combinations
raise ``FlowCompileError`` with a clear message so the GUI can surface
"this combination needs the next backend update".

Supported today:
    Input blocks
        in.protein_seq    → consumes (B, L) int token tensor from loader
        in.ligand_graph   → consumes torch_geometric Batch from loader
        in.protein_graph  → consumes torch_geometric Batch from loader
        in.ligand_fp      → consumes (B, D) float tensor (ECFP)
        in.protein_emb    → consumes (B, D) float tensor (ESM-2 cache)
    Encoders
        enc.protein_seq.cnn        → DeepDTA CNN tower
        enc.protein_seq.identity   → pass-through
        enc.protein_graph.{gcn,gin} → residue-graph GNN
        enc.protein_graph.identity → pass-through
        enc.ligand_seq.smiles_cnn  → DeepDTA SMILES CNN tower
        enc.ligand_graph.{gin,gcn} → mol-graph GNN
        enc.ligand_graph.identity  → pass-through
        enc.tabular.mlp            → small MLP
        enc.tabular.identity       → pass-through
    Fusion
        fuse.concat_mlp     → concat + MLP
        fuse.bilinear       → bilinear attention (DrugBAN-style)
        fuse.two_tower_dot  → cosine/dot product
    Heads
        head.regression.{pki,pkd,pic50,kd,dg}  → single-output MLP (MSE/Huber)
        head.classifier.default                → single-output MLP (BCE-with-logits)

Not yet wired (raises FlowCompileError with helpful text):
    enc.protein_seq.transformer    → needs the MolTransLite seq tower
    enc.protein_seq.esm2_frozen    → needs the embedding cache
    enc.ligand_seq.{chemberta,molformer} → needs pretrained-LM wiring
    enc.interaction_map.*          → needs contact-map loader path
    fuse.cross_attn / joint_mp     → planned
    fuse.tabular_xgb               → needs the xgboost dispatch path
    head.pose / head.ranking       → planned
    head.regression.{...}.xgboost/catboost → needs xgboost/catboost dispatch
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


class FlowCompileError(RuntimeError):
    """Raised when a flow graph can't be compiled into a trainable module."""


# ── Per-impl factory dispatch ────────────────────────────────────────
# Maps (block_id, impl_id) → factory(params, in_dims) → nn.Module + out_dim.
#
# Each factory returns: (module, out_dim, in_kind)
#   module: the nn.Module
#   out_dim: integer feature dimension of the output (None for graphs / non-1d)
#   in_kind: what this block consumes — used by the executor to dispatch the
#            right tensor from the loader batch.

def _build_protein_seq_cnn(params: dict) -> tuple[nn.Module, int, str]:
    from .dataset import CHARPROTLEN
    from .models import CNNTower
    m = CNNTower(
        vocab_size=CHARPROTLEN,
        embed_dim=int(params.get("embed_dim", 128)),
        num_filters=int(params.get("filters", 32)),
        kernel_size=int(params.get("kernel", params.get("kernel_size", 8))),
        max_len=1000,
    )
    return m, m.out_dim, "protein_seq"


def _build_ligand_seq_cnn(params: dict) -> tuple[nn.Module, int, str]:
    from .dataset import CHARISOSMILEN
    from .models import CNNTower
    m = CNNTower(
        vocab_size=CHARISOSMILEN,
        embed_dim=int(params.get("embed_dim", 128)),
        num_filters=int(params.get("filters", 32)),
        kernel_size=int(params.get("kernel", params.get("kernel_size", 4))),
        max_len=100,
    )
    return m, m.out_dim, "ligand_seq"


def _build_protein_graph_gcn(params: dict) -> tuple[nn.Module, int, str]:
    from .graph_features import RESIDUE_FEAT_DIM
    try:
        from torch_geometric.nn import GCNConv, global_mean_pool
    except ImportError as exc:
        raise FlowCompileError("torch_geometric is required for protein-graph GCN") from exc
    hidden = int(params.get("hidden", 128))
    layers = int(params.get("layers", 3))

    class _ProtGCN(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList()
            in_d = RESIDUE_FEAT_DIM
            for _ in range(layers):
                self.layers.append(GCNConv(in_d, hidden, add_self_loops=True))
                in_d = hidden
            self.out_dim = hidden

        def forward(self, batch):
            x, ei, b = batch.x, batch.edge_index, batch.batch
            for layer in self.layers:
                x = F.relu(layer(x, ei))
            return global_mean_pool(x, b)

    m = _ProtGCN()
    return m, hidden, "protein_graph"


def _build_protein_graph_gin(params: dict) -> tuple[nn.Module, int, str]:
    from .graph_features import RESIDUE_FEAT_DIM
    try:
        from torch_geometric.nn import GINConv, global_mean_pool
    except ImportError as exc:
        raise FlowCompileError("torch_geometric is required for protein-graph GIN") from exc
    hidden = int(params.get("hidden", 128))
    layers = int(params.get("layers", 3))

    class _ProtGIN(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList()
            in_d = RESIDUE_FEAT_DIM
            for _ in range(layers):
                mlp = nn.Sequential(nn.Linear(in_d, hidden), nn.ReLU(),
                                    nn.Linear(hidden, hidden))
                self.layers.append(GINConv(mlp))
                in_d = hidden
            self.out_dim = hidden

        def forward(self, batch):
            x, ei, b = batch.x, batch.edge_index, batch.batch
            for layer in self.layers:
                x = F.relu(layer(x, ei))
            return global_mean_pool(x, b)

    m = _ProtGIN()
    return m, hidden, "protein_graph"


def _build_ligand_graph_gin(params: dict) -> tuple[nn.Module, int, str]:
    from .featurizers import ATOM_FEAT_DIM
    try:
        from torch_geometric.nn import GINConv, global_mean_pool
    except ImportError as exc:
        raise FlowCompileError("torch_geometric is required for ligand-graph GIN") from exc
    hidden = int(params.get("hidden", 128))
    layers = int(params.get("layers", 3))

    class _LigGIN(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList()
            in_d = ATOM_FEAT_DIM
            for _ in range(layers):
                mlp = nn.Sequential(nn.Linear(in_d, hidden), nn.ReLU(),
                                    nn.Linear(hidden, hidden))
                self.layers.append(GINConv(mlp))
                in_d = hidden
            self.out_dim = hidden

        def forward(self, batch):
            x, ei, b = batch.x, batch.edge_index, batch.batch
            for layer in self.layers:
                x = F.relu(layer(x, ei))
            return global_mean_pool(x, b)

    m = _LigGIN()
    return m, hidden, "ligand_graph"


def _build_ligand_graph_gcn(params: dict) -> tuple[nn.Module, int, str]:
    from .featurizers import ATOM_FEAT_DIM
    try:
        from torch_geometric.nn import GCNConv, global_mean_pool
    except ImportError as exc:
        raise FlowCompileError("torch_geometric is required for ligand-graph GCN") from exc
    hidden = int(params.get("hidden", 128))
    layers = int(params.get("layers", 3))

    class _LigGCN(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList()
            in_d = ATOM_FEAT_DIM
            for _ in range(layers):
                self.layers.append(GCNConv(in_d, hidden, add_self_loops=True))
                in_d = hidden
            self.out_dim = hidden

        def forward(self, batch):
            x, ei, b = batch.x, batch.edge_index, batch.batch
            for layer in self.layers:
                x = F.relu(layer(x, ei))
            return global_mean_pool(x, b)

    m = _LigGCN()
    return m, hidden, "ligand_graph"


def _build_protein_seq_transformer(params: dict) -> tuple[nn.Module, int, str]:
    """Small Transformer encoder over AA tokens. Trained from scratch."""
    from .dataset import CHARPROTLEN
    hidden  = int(params.get("hidden", 128))
    heads   = int(params.get("heads", 4))
    layers  = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.1))
    max_len = int(params.get("max_len", 1024))

    class _ProtTransformer(nn.Module):
        def __init__(self):
            super().__init__()
            # Token + learned positional embedding, then a stack of
            # TransformerEncoderLayer modules. Heads must divide hidden.
            if hidden % max(heads, 1) != 0:
                # Round heads down to a divisor so torch's MHA accepts it.
                actual_heads = max(1, hidden // (hidden // heads or 1))
            else:
                actual_heads = heads
            self.tok = nn.Embedding(CHARPROTLEN + 1, hidden, padding_idx=0)
            self.pos = nn.Embedding(max_len + 1, hidden)
            enc_layer = nn.TransformerEncoderLayer(
                d_model=hidden, nhead=actual_heads,
                dim_feedforward=4 * hidden, dropout=dropout,
                batch_first=True, activation="gelu",
                norm_first=True,
            )
            self.enc = nn.TransformerEncoder(enc_layer, num_layers=layers)
            self.out_dim = hidden
            self.max_len = max_len

        def forward(self, tokens):
            # tokens: (B, L). Truncate/pad to max_len.
            B, L = tokens.shape
            L = min(L, self.max_len)
            tokens = tokens[:, :L]
            pos = torch.arange(L, device=tokens.device).unsqueeze(0).expand(B, L)
            x = self.tok(tokens) + self.pos(pos)
            # Pad mask: token 0 is pad in our char vocab.
            mask = (tokens == 0)
            x = self.enc(x, src_key_padding_mask=mask)
            # Mean pool over non-pad positions.
            valid = (~mask).float().unsqueeze(-1)
            pooled = (x * valid).sum(1) / valid.sum(1).clamp(min=1.0)
            return pooled

    m = _ProtTransformer()
    return m, hidden, "protein_seq"


def _build_protein_seq_esm2_frozen(params: dict, in_dim: Optional[int]) -> tuple[nn.Module, int, str]:
    """ESM-2 frozen encoder.

    Two real-world wirings:
      1. Cached embeddings (``in.protein_emb`` → this encoder). The input
         is already a (B, 1280) float tensor. We project to ``hidden`` via
         a small MLP (the "head" the cached embedding feeds).
      2. Raw tokens (``in.protein_seq`` → this encoder). We'd need to run
         ``esm.pretrained.esm2_*()`` on the fly which is expensive and
         requires fair-esm. For the MVP we raise a clear FlowCompileError
         when the upstream isn't ``in.protein_emb`` and instead point the
         user to the cached path.
    """
    hidden  = int(params.get("hidden", 256))
    dropout = float(params.get("dropout", 0.1))
    if in_dim is None:
        # Upstream isn't a declared-dim embedding block — most likely
        # the GUI wired this encoder to `in.protein_seq` and the
        # `_autoroute_embedding_encoders` step in compile_flow didn't
        # rewrite it for whatever reason (e.g. older flow_spec coming
        # in via a sweep or a checkpoint reload). Rather than crashing
        # compile, assume the canonical ESM-2 650M width (1280) so the
        # projection MLP gets built. The loader-shape resolver also
        # auto-routes to a `protein_emb`-producing loader for this case
        # so the runtime tensor matches. If the runtime tensor turns
        # out to be a token stream the forward pass will surface a
        # clear shape mismatch — but most launches go through the
        # autoroute and never hit this fallback.
        in_dim = 1280
    body = [
        nn.LayerNorm(in_dim),
        nn.Linear(in_dim, hidden),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden, hidden),
    ]
    return nn.Sequential(*body), hidden, "protein_emb"


def _build_protein_seq_lstm_bi(params: dict) -> tuple[nn.Module, int, str]:
    """Bidirectional LSTM over AA tokens. The pre-transformer baseline."""
    from .dataset import CHARPROTLEN
    hidden  = int(params.get("hidden", 256))
    layers  = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.2))
    embed_dim = int(params.get("embed_dim", hidden))

    class _ProtBiLSTM(nn.Module):
        def __init__(self):
            super().__init__()
            self.tok = nn.Embedding(CHARPROTLEN + 1, embed_dim, padding_idx=0)
            self.lstm = nn.LSTM(
                input_size=embed_dim, hidden_size=hidden,
                num_layers=layers, batch_first=True,
                bidirectional=True,
                dropout=dropout if layers > 1 else 0.0,
            )
            self.out_dim = 2 * hidden  # bidirectional concat

        def forward(self, tokens):
            x = self.tok(tokens)                       # (B, L, E)
            x, _ = self.lstm(x)                         # (B, L, 2*H)
            # Mean pool over non-pad positions (token 0 is pad).
            mask = (tokens != 0).float().unsqueeze(-1)
            return (x * mask).sum(1) / mask.sum(1).clamp(min=1.0)

    return _ProtBiLSTM(), 2 * hidden, "protein_seq"


def _build_protein_seq_protbert(params: dict, in_dim: Optional[int]) -> tuple[nn.Module, int, str]:
    """ProtBert (Rostlab/prot_bert) frozen encoder, mirrored to ESM-2's
    cached pattern. Requires upstream ``in.protein_emb`` carrying a
    1024-d (ProtBert) cached embedding — on-the-fly forward is too
    slow for the trainer loop without a separate embedding cache.

    For now, ProtBert and ESM-2 differ only in their canonical
    embedding width (1024 vs 1280); the head MLP is the same shape.
    Users who want true on-the-fly ProtBert can wire a future
    in.protein_protbert_emb block + cache.
    """
    if in_dim is None:
        # Same fallback as esm2_frozen: ProtBert's canonical embedding
        # width is 1024. Assume it so compile proceeds; autoroute should
        # have rewritten the upstream input to in.protein_emb so the
        # runtime tensor matches.
        in_dim = 1024
    hidden  = int(params.get("hidden", 256))
    dropout = float(params.get("dropout", 0.1))
    body = [
        nn.LayerNorm(in_dim),
        nn.Linear(in_dim, hidden),
        nn.GELU(),
        nn.Dropout(dropout),
        nn.Linear(hidden, hidden),
    ]
    return nn.Sequential(*body), hidden, "protein_emb"


def _build_protein_graph_gat(params: dict) -> tuple[nn.Module, int, str]:
    from .graph_features import RESIDUE_FEAT_DIM
    try:
        from torch_geometric.nn import GATConv, global_mean_pool
    except ImportError as exc:
        raise FlowCompileError("torch_geometric is required for protein-graph GAT") from exc
    hidden  = int(params.get("hidden", 128))
    heads   = int(params.get("heads", 4))
    layers  = int(params.get("layers", 3))
    dropout = float(params.get("dropout", 0.1))

    class _ProtGAT(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList()
            in_d = RESIDUE_FEAT_DIM
            for i in range(layers):
                # Multi-head concat for intermediate layers, average for last.
                concat = i < layers - 1
                self.layers.append(GATConv(
                    in_d, hidden, heads=heads, concat=concat, dropout=dropout,
                    add_self_loops=True,
                ))
                in_d = hidden * heads if concat else hidden
            self.out_dim = hidden

        def forward(self, batch):
            x, ei, b = batch.x, batch.edge_index, batch.batch
            for layer in self.layers:
                x = F.elu(layer(x, ei))
            return global_mean_pool(x, b)

    return _ProtGAT(), hidden, "protein_graph"


def _build_ligand_graph_gat(params: dict) -> tuple[nn.Module, int, str]:
    from .featurizers import ATOM_FEAT_DIM
    try:
        from torch_geometric.nn import GATConv, global_mean_pool
    except ImportError as exc:
        raise FlowCompileError("torch_geometric is required for ligand-graph GAT") from exc
    hidden  = int(params.get("hidden", 128))
    heads   = int(params.get("heads", 4))
    layers  = int(params.get("layers", 3))
    dropout = float(params.get("dropout", 0.1))

    class _LigGAT(nn.Module):
        def __init__(self):
            super().__init__()
            self.layers = nn.ModuleList()
            in_d = ATOM_FEAT_DIM
            for i in range(layers):
                concat = i < layers - 1
                self.layers.append(GATConv(
                    in_d, hidden, heads=heads, concat=concat, dropout=dropout,
                    add_self_loops=True,
                ))
                in_d = hidden * heads if concat else hidden
            self.out_dim = hidden

        def forward(self, batch):
            x, ei, b = batch.x, batch.edge_index, batch.batch
            for layer in self.layers:
                x = F.elu(layer(x, ei))
            return global_mean_pool(x, b)

    return _LigGAT(), hidden, "ligand_graph"


def _build_tabular_mlp(params: dict, in_dim: int) -> tuple[nn.Module, int, str]:
    hidden = int(params.get("hidden", 128))
    layers = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.1))
    body = []
    d = in_dim
    for _ in range(layers):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden
    m = nn.Sequential(*body) if body else nn.Identity()
    return m, hidden, "tabular"


def _build_tabular_booster_encoder(params: dict, in_dim: Optional[int],
                                   backend: str) -> tuple[nn.Module, int, str]:
    """Encoder that "is" a gradient-boosted model on the tabular input.

    The torch graph keeps a thin MLP so the upstream loader's tensor
    flows differentiably through downstream torch blocks during the
    first training phase. After torch training, the trainer fits a real
    XGBoost / CatBoost on the cached encoder *input* (i.e. the raw
    feature tensor) and stores the trained booster on the module so the
    downstream head can use the booster's predictions at inference time.

    For the MVP we surface this as a regular MLP encoder — using the
    HybridBoosterHead at the head stage gives the same end-to-end
    behaviour with less compiler surface area. This builder exists so
    enc.tabular/xgboost compiles cleanly; the actual booster fit lives
    in the hybrid head path.
    """
    try:
        if backend == "xgboost":
            import xgboost  # noqa: F401
        elif backend == "catboost":
            import catboost  # noqa: F401
    except ImportError as exc:
        raise FlowCompileError(
            f"{backend} is not installed. `pip install {backend}` and re-launch, "
            "or switch to enc.tabular/mlp."
        ) from exc
    # Reuse the MLP body — the user's intent of "boost the tabular input"
    # is satisfied at the head stage via head.regression/xgboost.
    return _build_tabular_mlp(params, in_dim or 128)


def _build_concat_mlp_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    """Variadic concat-then-MLP fusion. Works for any N ≥ 1 inputs."""
    hidden = int(params.get("hidden", 256))
    layers = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.1))
    body = []
    d = sum(in_dims)
    for _ in range(layers):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden

    class _ConcatFusion(nn.Module):
        def __init__(self):
            super().__init__()
            self.mlp = nn.Sequential(*body) if body else nn.Identity()

        def forward(self, *xs):
            return self.mlp(torch.cat(list(xs), dim=-1))

    return _ConcatFusion(), hidden


def _build_weighted_mean_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    """Learned-softmax-weighted mean of N input embeddings.

    Each input is first projected to ``hidden`` via its own Linear so
    they share a dimension. A learnable scalar per input goes through
    softmax to produce mixing weights; the output is the weighted mean.
    Cheap, gradient-friendly, and naturally handles any N.
    """
    hidden = int(params.get("hidden", 256))
    n_in   = len(in_dims)

    class _WeightedMean(nn.Module):
        def __init__(self):
            super().__init__()
            self.projs = nn.ModuleList([nn.Linear(d, hidden) for d in in_dims])
            self.logits = nn.Parameter(torch.zeros(n_in))
            self.out_dim = hidden

        def forward(self, *xs):
            if len(xs) != n_in:
                raise RuntimeError(
                    f"weighted_mean fusion expected {n_in} inputs, got {len(xs)}"
                )
            w = torch.softmax(self.logits, dim=0)               # (N,)
            z = torch.stack([self.projs[i](xs[i]) for i in range(n_in)], dim=0)  # (N, B, hidden)
            return (w.view(n_in, 1, 1) * z).sum(dim=0)          # (B, hidden)

    return _WeightedMean(), hidden


def _build_attention_pool_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    """Self-attention pool over N input embeddings.

    Each input is projected to ``hidden`` and stacked into a length-N
    "token sequence". A small MultiheadAttention layer mixes them, and
    the output is the mean-pooled result. This generalises cross-attn
    to N inputs without needing pairwise modules.
    """
    hidden  = int(params.get("hidden", 256))
    heads   = int(params.get("heads", 4))
    layers  = int(params.get("layers", 1))
    dropout = float(params.get("dropout", 0.1))
    if hidden % max(heads, 1) != 0:
        heads = max(1, hidden // (hidden // heads or 1))

    class _AttnPool(nn.Module):
        def __init__(self):
            super().__init__()
            self.projs = nn.ModuleList([nn.Linear(d, hidden) for d in in_dims])
            self.attn_layers = nn.ModuleList([
                nn.MultiheadAttention(hidden, heads, dropout=dropout, batch_first=True)
                for _ in range(layers)
            ])
            self.ln = nn.LayerNorm(hidden)
            self.out_dim = hidden

        def forward(self, *xs):
            tokens = [self.projs[i](xs[i]).unsqueeze(1) for i in range(len(xs))]
            tok = torch.cat(tokens, dim=1)                  # (B, N, hidden)
            for layer in self.attn_layers:
                attn_out, _ = layer(tok, tok, tok)
                tok = self.ln(tok + attn_out)
            return tok.mean(dim=1)                          # (B, hidden)

    return _AttnPool(), hidden


def _build_gated_sum_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    """Per-input sigmoid gate, then projected sum.

    For each input x_i, compute a scalar gate g_i = σ(w_i · x_i + b_i),
    project x_i to ``hidden`` via its own Linear, and output Σ g_i · z_i.
    Gates are input-dependent (unlike weighted_mean's static weights),
    so the fusion can learn to ignore an input on a per-example basis.
    """
    hidden = int(params.get("hidden", 256))

    class _GatedSum(nn.Module):
        def __init__(self):
            super().__init__()
            self.projs = nn.ModuleList([nn.Linear(d, hidden) for d in in_dims])
            self.gates = nn.ModuleList([nn.Linear(d, 1)      for d in in_dims])
            self.out_dim = hidden

        def forward(self, *xs):
            out = None
            for i, x in enumerate(xs):
                g = torch.sigmoid(self.gates[i](x))         # (B, 1)
                z = self.projs[i](x)                        # (B, hidden)
                contrib = g * z
                out = contrib if out is None else out + contrib
            return out

    return _GatedSum(), hidden


def _build_bilinear_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    if len(in_dims) != 2:
        raise FlowCompileError(
            f"fuse/bilinear is a 2-input fusion (bilinear form requires exactly "
            f"two operands); got {len(in_dims)} inputs. Use fuse/concat_mlp, "
            f"fuse/weighted_mean, fuse/attention_pool, or fuse/gated_sum for "
            f"N-input fusion."
        )
    from .models import _BilinearAttentionPool
    a_dim, b_dim = in_dims[0], in_dims[1]
    k = int(params.get("k", params.get("hidden", 256)))
    attn_dim = int(params.get("attn_dim", 64))

    pool = _BilinearAttentionPool(p_dim=a_dim, l_dim=b_dim, k=k, attn_dim=attn_dim)

    class _BinaryWrap(nn.Module):
        """Adapts the binary _BilinearAttentionPool to the variadic
        forward(*xs) signature the FlowModule now calls fusion with.
        """
        def __init__(self):
            super().__init__()
            self.inner = pool

        def forward(self, *xs):
            return self.inner(xs[0], xs[1])

    return _BinaryWrap(), k


def _build_tabular_xgb_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    """Hybrid fusion that "is" an XGBoost regression on (a ‖ b).

    The torch graph keeps the upstream encoders trainable; this fusion
    block emits the concatenated (B, a_dim+b_dim) embedding so a
    downstream regression head can MLP it during torch training. After
    torch training, the trainer detects the downstream head as a
    HybridBoosterHead and fits XGBoost on the concatenated feature.

    Practically: pair this with ``head.regression/xgboost`` to get the
    full DrugBAN-tabular workflow (encoders + booster on concat).

    NOTE: this is the SAME pattern as enc.tabular/xgboost but applied to
    two-input fusion. The fusion module itself is a pass-through concat;
    the booster magic lives in the head.
    """
    try:
        import xgboost  # noqa: F401
    except ImportError as exc:
        raise FlowCompileError(
            "XGBoost is not installed. Install with `pip install xgboost`."
        ) from exc
    total = sum(in_dims)

    class _ConcatPass(nn.Module):
        out_dim = total
        def forward(self, *xs):
            return torch.cat(list(xs), dim=-1)

    return _ConcatPass(), total


def _build_cross_attention_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    """Symmetric cross-attention fusion. 2-input only.

    Each side gets projected to a shared hidden space, then both ways of
    cross-attention (a-on-b and b-on-a) are computed and concatenated.
    Mean-pooled output is a single (B, hidden) embedding for the head.
    Cross-attention is naturally binary; for N-input use the
    ``attention_pool`` impl which treats inputs as a length-N token set.
    """
    if len(in_dims) != 2:
        raise FlowCompileError(
            f"fuse/cross_attn is a 2-input fusion (symmetric a<->b attention); "
            f"got {len(in_dims)} inputs. Use fuse/attention_pool for "
            f"N-input self-attention fusion."
        )
    a_dim, b_dim = in_dims[0], in_dims[1]
    hidden  = int(params.get("hidden", 256))
    heads   = int(params.get("heads", 4))
    layers  = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.1))
    if hidden % max(heads, 1) != 0:
        heads = max(1, hidden // (hidden // heads or 1))

    class _CrossAttnFusion(nn.Module):
        def __init__(self):
            super().__init__()
            self.proj_a = nn.Linear(a_dim, hidden)
            self.proj_b = nn.Linear(b_dim, hidden)
            self.ln_a   = nn.LayerNorm(hidden)
            self.ln_b   = nn.LayerNorm(hidden)
            self.a_to_b = nn.ModuleList([
                nn.MultiheadAttention(hidden, heads, dropout=dropout, batch_first=True)
                for _ in range(layers)
            ])
            self.b_to_a = nn.ModuleList([
                nn.MultiheadAttention(hidden, heads, dropout=dropout, batch_first=True)
                for _ in range(layers)
            ])
            self.ff = nn.Sequential(
                nn.Linear(2 * hidden, hidden), nn.GELU(),
                nn.Dropout(dropout), nn.Linear(hidden, hidden),
            )
            self.out_dim = hidden

        def forward(self, *xs):
            a, b = xs[0], xs[1]
            # Embeddings come in as (B, D_a) and (B, D_b). To do attention
            # we need (B, L, hidden) with L=1 — single "token" per side.
            # That makes cross-attention act like a learned gating.
            qa = self.ln_a(self.proj_a(a)).unsqueeze(1)   # (B, 1, hidden)
            qb = self.ln_b(self.proj_b(b)).unsqueeze(1)
            for layer_ab, layer_ba in zip(self.a_to_b, self.b_to_a):
                a2b, _ = layer_ab(qa, qb, qb)
                b2a, _ = layer_ba(qb, qa, qa)
                qa = qa + a2b
                qb = qb + b2a
            # Concat the two updated representations + project.
            fused = torch.cat([qa.squeeze(1), qb.squeeze(1)], dim=-1)
            return self.ff(fused)

    return _CrossAttnFusion(), hidden


def _build_two_tower_dot_fusion(params: dict, in_dims: list[int]) -> tuple[nn.Module, int]:
    if len(in_dims) != 2:
        raise FlowCompileError(
            f"fuse/two_tower_dot is a 2-input fusion (single dot product); "
            f"got {len(in_dims)} inputs. Use fuse/weighted_mean or "
            f"fuse/gated_sum for N-input compositions."
        )
    a_dim, b_dim = in_dims[0], in_dims[1]
    proj = int(params.get("proj_dim", params.get("shared_dim", 256)))
    normalize = bool(params.get("normalize", True))

    class _DotTower(nn.Module):
        def __init__(self):
            super().__init__()
            self.a = nn.Linear(a_dim, proj, bias=False)
            self.b = nn.Linear(b_dim, proj, bias=False)
            self.normalize = normalize

        def forward(self, *xs):
            ua = self.a(xs[0])
            ub = self.b(xs[1])
            if self.normalize:
                ua = F.normalize(ua, dim=-1)
                ub = F.normalize(ub, dim=-1)
            return (ua * ub).sum(dim=-1, keepdim=True)  # (B, 1)

    # Dot fusion's "embedding dim" is 1 — it emits a scalar score per pair.
    return _DotTower(), 1


def _build_regression_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """Returns (module, loss_name). Single-output Linear with a small trunk."""
    hidden = int(params.get("hidden", 512))
    layers = int(params.get("layers", 3))
    dropout = float(params.get("dropout", 0.1))
    loss = str(params.get("loss", "mse"))
    body = []
    d = in_dim
    for _ in range(max(0, layers - 1)):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden
    body += [nn.Linear(d, 1)]
    return nn.Sequential(*body), loss


def _build_classifier_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """Returns (module, loss_name='bce_with_logits') with a 1-d logit output."""
    hidden = int(params.get("hidden", 256))
    layers = int(params.get("layers", 3))
    dropout = float(params.get("dropout", 0.2))
    body = []
    d = in_dim
    for _ in range(max(0, layers - 1)):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden
    body += [nn.Linear(d, 1)]
    return nn.Sequential(*body), "bce_with_logits"


def _build_calibrated_classifier_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """Sigmoid + (deferred) Platt scaling.

    Architecturally identical to the default sigmoid classifier — the
    only difference at training time is post-hoc: after the torch loop,
    the trainer should fit Platt's logistic on (logit, label) pairs of
    the val set, producing two scalars (a, b) such that the calibrated
    probability is σ(a · logit + b). For the MVP we return the same
    module + loss name; the trainer wires the post-fit step opportun-
    istically when the user picks this impl (loss_name carries the
    "calibrated" marker so the trainer knows to run Platt).
    """
    hidden = int(params.get("hidden", 256))
    layers = int(params.get("layers", 3))
    dropout = float(params.get("dropout", 0.2))
    body = []
    d = in_dim
    for _ in range(max(0, layers - 1)):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden
    body += [nn.Linear(d, 1)]
    # Loss name "bce_with_logits_platt" tells the trainer to also run
    # Platt scaling on the val set after the torch loop completes.
    return nn.Sequential(*body), "bce_with_logits_platt"


def _build_multiclass_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """K-way softmax head. Returns (module, "cross_entropy")."""
    num_classes     = int(params.get("num_classes", 3))
    hidden          = int(params.get("hidden", 256))
    layers          = int(params.get("layers", 2))
    dropout         = float(params.get("dropout", 0.1))
    body = []
    d = in_dim
    for _ in range(max(0, layers - 1)):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden
    body += [nn.Linear(d, num_classes)]
    return nn.Sequential(*body), "cross_entropy"


def _build_ranking_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """InfoNCE / ranking head.

    The torch portion is identical to a single-scalar regression head; the
    distinguishing behaviour lives in the loss function, which the trainer
    swaps in based on ``loss_name`` ("infonce"). The head simply produces
    a scalar similarity score per pair — the loss arranges positives + in-
    batch negatives. When the upstream fusion is ``two_tower_dot`` the
    score is already a dot product; otherwise we run a small MLP head.
    """
    hidden  = int(params.get("hidden", 256))
    layers  = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.1))
    if in_dim <= 1:
        # Upstream is already a dot product score.
        return nn.Identity(), "infonce"
    body = []
    d = in_dim
    for _ in range(max(0, layers - 1)):
        body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
        d = hidden
    body += [nn.Linear(d, 1)]
    return nn.Sequential(*body), "infonce"


class _DiagTap(nn.Module):
    """Inline diagnostic pass-through that records a small running summary
    of the tensor flowing through it.

    The trainer can dig out a list of taps via :func:`find_diag_taps`
    after each epoch and emit a `diag` event so the GUI can show
    in-pipeline activation health (mean, std, min, max, fraction NaN).

    ``log_every_n_steps`` and ``sample_size`` are recorded as attributes
    so a future trainer pass can choose how often to flush + how many
    activations to ship to the GUI. For now we just track running
    statistics so they're available at end-of-epoch.
    """
    def __init__(self, log_every_n_steps: int = 50, sample_size: int = 8,
                 node_id: str = ""):
        super().__init__()
        self.log_every_n_steps = int(log_every_n_steps)
        self.sample_size       = int(sample_size)
        self.node_id           = node_id
        self.last_mean: float | None = None
        self.last_std:  float | None = None
        self.last_min:  float | None = None
        self.last_max:  float | None = None
        self.last_nan_frac: float = 0.0
        self.n_observations: int = 0

    def forward(self, x):
        # Only sample on training-mode forward to keep eval cheap.
        if self.training and isinstance(x, torch.Tensor) and x.numel() > 0:
            with torch.no_grad():
                self.last_mean    = float(x.float().mean())
                self.last_std     = float(x.float().std())
                self.last_min     = float(x.float().min())
                self.last_max     = float(x.float().max())
                self.last_nan_frac = float(torch.isnan(x).float().mean())
            self.n_observations += 1
        return x


def find_diag_taps(module: nn.Module) -> list:
    """Walk the model tree and return every _DiagTap instance — used by
    the trainer to emit per-epoch diagnostic events.
    """
    out = []
    for m in module.modules():
        if isinstance(m, _DiagTap):
            out.append(m)
    return out


def _build_pose_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """Coordinate-MLP pose head — predicts (x, y, z) per atom, padded to
    ``max_atoms``. Returns (module, "pose_mse"). Loss is L2 on coordinates
    masked by the per-example atom count; the trainer needs that mask, so
    this head is gated on the loader producing pose targets — which today
    it doesn't, so launching with this head raises FlowCompileError.
    """
    raise FlowCompileError(
        "head.pose is surfaced in the GUI but not yet wired into the trainer "
        "(pose-target loaders ship in the next backend stage). Choose a "
        "regression / classifier / ranking head for now."
    )


# Input blocks declare what KIND of tensor they consume from the loader batch.
# The FlowModule's forward() uses this to route the right input tensor.
# Two protein-side kinds — ``protein_seq`` is char-tokenised; ``protein_emb``
# is a pre-computed 1280-d ESM-2 vector served by the ESM-2 dataset loader.
_INPUT_KINDS = {
    "in.protein_seq":         "protein_seq",
    "in.ligand_smiles":       "ligand_seq",
    "in.ligand_fp":           "ligand_tabular",
    "in.ligand_graph":        "ligand_graph",
    "in.protein_graph":       "protein_graph",
    "in.protein_emb":         "protein_emb",
    "in.ligand_descriptors":  "ligand_tabular",
    "in.ligand_unimol":       "ligand_tabular",
    "in.ligand_physchem":     "ligand_tabular",
    "in.protein_fakesetta":   "protein_emb",   # routed like a (B, D) tabular tensor
    "in.contact_map":         "contact_map",
    # NOTE: in.iface_pairs is GUI-only — its loader isn't built yet. Adding
    # the block to a flow will fail to compile until the loader ships.
}

# For embedding-shaped inputs we know the feature dim up front. Encoders
# wired from these inputs can size themselves correctly without inferring
# from the upstream's out_dim (which is None for non-tabular inputs).
# The dim numbers match the ProteoSphere featurizer registry:
#   ESM-2 650M       → 1280
#   ECFP4            → 2048
#   Uni-Mol v2       → 512
#   RDKit physchem   → 78
#   Fake-setta       → 19  (ref2015-style energy vector)
_INPUT_DIMS: dict[str, int] = {
    "in.protein_emb":         1280,
    "in.ligand_fp":           2048,
    "in.ligand_unimol":       512,
    "in.ligand_physchem":     78,
    "in.protein_fakesetta":   19,
}


# Maps (block_id, impl_id) → callable building the module.
# Each builder is responsible for raising FlowCompileError when it can't build.
_BUILDERS: dict[str, Callable] = {
    # encoder.protein_seq
    ("enc.protein_seq", "cnn"):           lambda p, _i: _build_protein_seq_cnn(p),
    ("enc.protein_seq", "transformer"):   lambda p, _i: _build_protein_seq_transformer(p),
    ("enc.protein_seq", "esm2_frozen"):   _build_protein_seq_esm2_frozen,
    ("enc.protein_seq", "lstm_bi"):       lambda p, _i: _build_protein_seq_lstm_bi(p),
    ("enc.protein_seq", "protbert"):      _build_protein_seq_protbert,
    ("enc.protein_seq", "identity"):      lambda p, in_dim: (nn.Identity(), in_dim if in_dim else 1280, "protein_emb" if (in_dim or 0) > 0 else "protein_seq"),
    # encoder.protein_graph
    ("enc.protein_graph", "gcn"):       lambda p, _i: _build_protein_graph_gcn(p),
    ("enc.protein_graph", "gin"):       lambda p, _i: _build_protein_graph_gin(p),
    ("enc.protein_graph", "gat"):       lambda p, _i: _build_protein_graph_gat(p),
    ("enc.protein_graph", "identity"):  lambda p, in_dim: (nn.Identity(), in_dim, "protein_graph"),
    # encoder.ligand_seq
    ("enc.ligand_seq", "smiles_cnn"):   lambda p, _i: _build_ligand_seq_cnn(p),
    ("enc.ligand_seq", "identity"):     lambda p, in_dim: (nn.Identity(), in_dim, "ligand_seq"),
    # encoder.ligand_graph
    ("enc.ligand_graph", "gin"):        lambda p, _i: _build_ligand_graph_gin(p),
    ("enc.ligand_graph", "gcn"):        lambda p, _i: _build_ligand_graph_gcn(p),
    ("enc.ligand_graph", "gat"):        lambda p, _i: _build_ligand_graph_gat(p),
    ("enc.ligand_graph", "identity"):   lambda p, in_dim: (nn.Identity(), in_dim, "ligand_graph"),
    # encoder.tabular
    ("enc.tabular", "mlp"):             lambda p, in_dim: _build_tabular_mlp(p, in_dim or 128),
    ("enc.tabular", "xgboost"):         lambda p, in_dim: _build_tabular_booster_encoder(p, in_dim, "xgboost"),
    ("enc.tabular", "catboost"):        lambda p, in_dim: _build_tabular_booster_encoder(p, in_dim, "catboost"),
    ("enc.tabular", "identity"):        lambda p, in_dim: (nn.Identity(), in_dim, "tabular"),
    # fusion  — all variadic-capable; the binary impls reject N≠2 with a
    # clear FlowCompileError. New variadic impls (weighted_mean,
    # attention_pool, gated_sum) accept any N≥1.
    ("fuse", "concat_mlp"):             _build_concat_mlp_fusion,
    ("fuse", "bilinear"):               _build_bilinear_fusion,
    ("fuse", "two_tower_dot"):          _build_two_tower_dot_fusion,
    ("fuse", "cross_attn"):             _build_cross_attention_fusion,
    ("fuse", "tabular_xgb"):            _build_tabular_xgb_fusion,
    ("fuse", "weighted_mean"):          _build_weighted_mean_fusion,
    ("fuse", "attention_pool"):         _build_attention_pool_fusion,
    ("fuse", "gated_sum"):              _build_gated_sum_fusion,
}

def _build_xgboost_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """XGBoost head — hybrid fit-after-feature-extract training path.

    Strategy:
        Phase 1 (torch): the upstream encoders + fusion train as usual
                         with a temporary MLP regression head. The MLP
                         is *only* there to give the encoders gradients.
        Phase 2 (post-torch): the trainer extracts the fused embedding
                         for every training example, fits XGBoost on
                         (embedding, label), and replaces the torch
                         head with the booster at predict time.

    The builder returns a placeholder ``_XGBoostHead`` nn.Module that
    *looks* like a regression head during torch training (forward pass
    returns a learnable scalar) but carries a flag the trainer reads to
    trigger the post-torch booster fit.
    """
    try:
        import xgboost  # noqa: F401
    except ImportError as exc:
        raise FlowCompileError(
            "XGBoost is not installed. Install with `pip install xgboost` and re-launch."
        ) from exc
    hidden = int(params.get("hidden", 256))
    layers = int(params.get("layers", 2))
    dropout = float(params.get("dropout", 0.1))
    head = _HybridBoosterHead(
        in_dim=in_dim, hidden=hidden, layers=layers, dropout=dropout,
        backend="xgboost", params=dict(params),
    )
    return head, "mse"


def _build_catboost_head(params: dict, in_dim: int) -> tuple[nn.Module, str]:
    """CatBoost head — same hybrid dispatch as XGBoost."""
    try:
        import catboost  # noqa: F401
    except ImportError as exc:
        raise FlowCompileError(
            "CatBoost is not installed. Install with `pip install catboost` and re-launch."
        ) from exc
    hidden = int(params.get("hidden", 256))
    layers = int(params.get("layers", 2))
    head = _HybridBoosterHead(
        in_dim=in_dim, hidden=hidden, layers=layers, dropout=0.1,
        backend="catboost", params=dict(params),
    )
    return head, "mse"


class _HybridBoosterHead(nn.Module):
    """Dual-mode head: an MLP during torch training; a fitted booster at predict.

    Lifecycle:
        1. The compiler instantiates this. The internal MLP gives the
           upstream encoders something to push gradients into during
           ``training.py``'s normal training loop.
        2. After torch training finishes, the trainer extracts the
           (embedding, label) pairs from the training set and calls
           ``self.fit_booster(...)``.
        3. From that point on, ``self.predict_with_booster(...)`` is
           used in place of forward() at test time.

    The trainer detects hybrid heads by walking the model tree at the
    end of torch training and looking for ``_HybridBoosterHead`` instances.
    """
    def __init__(self, in_dim: int, hidden: int, layers: int, dropout: float,
                 backend: str, params: dict):
        super().__init__()
        # MLP for the torch phase (gives upstream encoders gradients).
        body = []
        d = in_dim
        for _ in range(max(0, layers - 1)):
            body += [nn.Linear(d, hidden), nn.ReLU(), nn.Dropout(dropout)]
            d = hidden
        body += [nn.Linear(d, 1)]
        self.mlp = nn.Sequential(*body)
        # Booster metadata — used by the trainer's post-torch fit.
        self.backend = backend                  # "xgboost" | "catboost"
        self.booster_params = dict(params)
        self.booster = None                     # populated after fit_booster()
        # Mark the upstream input dim so the trainer can verify
        # extracted embeddings match.
        self.in_dim = in_dim

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Forward semantics depend on whether the booster has been fit.

        * Before fit: returns MLP outputs so loss + gradient flow normally
          through the upstream torch graph.
        * After fit: returns booster predictions wrapped in a torch tensor
          on the same device as the input. The booster is sklearn-style
          so it runs on CPU; we move the predictions back to the GPU for
          consistency with the rest of evaluate()'s tensor pipeline.
        """
        if self.booster is None:
            return self.mlp(x)
        # Detach + move to CPU for booster inference, then wrap result.
        emb = x.detach().cpu().numpy()
        preds = self.booster.predict(emb).astype("float32")
        return torch.from_numpy(preds).to(x.device).unsqueeze(-1)

    def fit_booster(self, embeddings: np.ndarray, labels: np.ndarray) -> dict:
        """Phase-2 fit. Returns a small status dict for the run summary."""
        import numpy as _np
        embeddings = _np.asarray(embeddings, dtype=_np.float32)
        labels = _np.asarray(labels, dtype=_np.float32)
        if self.backend == "xgboost":
            import xgboost as xgb
            p = self.booster_params
            self.booster = xgb.XGBRegressor(
                n_estimators=int(p.get("n_estimators", 500)),
                max_depth=int(p.get("max_depth", 6)),
                learning_rate=float(p.get("learning_rate", p.get("lr", 0.05))),
                subsample=float(p.get("subsample", 0.8)),
                reg_lambda=float(p.get("reg_lambda", 1.0)),
                tree_method="hist",
                n_jobs=-1, verbosity=0,
            )
            self.booster.fit(embeddings, labels)
            return {"backend": "xgboost",
                    "n_estimators": self.booster.n_estimators,
                    "n_train": int(len(embeddings))}
        elif self.backend == "catboost":
            import catboost as cb
            p = self.booster_params
            self.booster = cb.CatBoostRegressor(
                iterations=int(p.get("iterations", 1000)),
                depth=int(p.get("depth", 6)),
                learning_rate=float(p.get("learning_rate", p.get("lr", 0.05))),
                l2_leaf_reg=float(p.get("l2_leaf_reg", 3.0)),
                allow_writing_files=False, verbose=False,
            )
            self.booster.fit(embeddings, labels)
            return {"backend": "catboost",
                    "iterations": self.booster.tree_count_,
                    "n_train": int(len(embeddings))}
        else:
            raise FlowCompileError(f"Unknown booster backend '{self.backend}'.")

    def predict_with_booster(self, embeddings: np.ndarray) -> np.ndarray:
        """Inference path. Falls back to MLP forward if booster isn't fit yet."""
        import numpy as _np
        if self.booster is None:
            with torch.no_grad():
                t = torch.from_numpy(_np.asarray(embeddings, dtype=_np.float32))
                return self.mlp(t).cpu().numpy().squeeze(-1)
        return _np.asarray(self.booster.predict(_np.asarray(embeddings, dtype=_np.float32)))


# Heads return (module, loss_name) — except the boosted stubs which raise.
_HEAD_BUILDERS: dict[str, Callable] = {
    "head.regression":  _build_regression_head,
    "head.classifier":  _build_classifier_head,
    "head.multiclass":  _build_multiclass_head,
    "head.ranking":     _build_ranking_head,
    "head.pose":        _build_pose_head,
}

# Per-impl head dispatch: when impl_id picks a non-default builder, look
# it up here BEFORE falling back to the per-role default. The xgboost/
# catboost builders return _HybridBoosterHead instances which the
# trainer detects post-torch-training and fits a real booster on.
_HEAD_IMPL_BUILDERS: dict[tuple[str, str], Callable] = {
    ("head.regression", "xgboost"):  _build_xgboost_head,
    ("head.regression", "catboost"): _build_catboost_head,
    ("head.classifier", "xgboost"):  _build_xgboost_head,
    ("head.classifier", "calibrated"): _build_calibrated_classifier_head,
}


def find_hybrid_heads(module: nn.Module) -> list:
    """Walk an nn.Module tree and return every _HybridBoosterHead instance.

    Used by the trainer at end-of-training to detect hybrid runs and
    trigger the booster-fit phase.
    """
    out = []
    for m in module.modules():
        if isinstance(m, _HybridBoosterHead):
            out.append(m)
    return out


# ── The compiled module ──────────────────────────────────────────────

class FlowModule(nn.Module):
    """An nn.Module assembled from a user-built flow graph.

    Forward signature is loader-driven: the trainer hands the model
    everything in the batch tuple, and the FlowModule picks the right
    tensor for each input block via ``input_kinds``.

    Limitations of this MVP:
        * Inputs must already exist in the loader batch (no custom
          loader composition yet — pick a feature set the loader
          knows how to produce).
        * One fusion block, one head, sequential graph. The flow
          builder enforces this in the UI; the compiler does too.
    """

    def __init__(self, modules_by_id: dict[str, nn.Module],
                 topo_order: list[str],
                 node_meta: dict[str, dict],
                 edges: list[dict],
                 head_node_id: str,
                 loss_name: str,
                 input_kinds: dict[str, str]):
        super().__init__()
        self.flow_modules = nn.ModuleDict(modules_by_id)
        self.topo_order = topo_order
        self.node_meta = node_meta            # node_id → {block_id, impl_id, cat}
        self.edges = edges                    # list of {from, to}
        self.head_node_id = head_node_id
        self.loss_name = loss_name            # "mse" / "huber" / "bce_with_logits"
        self.input_kinds = input_kinds        # node_id → "protein_seq" / "ligand_graph" / ...

    def forward(self, *batch_args, **batch_kwargs):
        """Routes the batch tuple/dict to each input block, then walks
        the topological order, calling each block's module.

        Accepts either keyword args (loader returns a dict) or positional
        args in the canonical (protein, ligand, label) order. The trainer
        always passes positional. Each input block declares an
        ``input_kind`` and we pull the matching positional tensor.
        """
        # Build per-input-kind tensor map.
        # The trainer's batch unpacks to:
        #   2-tuple loaders: (feats, y) → kind="tabular"
        #   3-tuple loaders: (seq_or_graph_a, smi_or_graph_b, y) →
        #       kind depends on whether the loader is the char-token
        #       loader, the graph loader, or the struct-graph loader.
        # The flow compiler caller (see training.py wiring below) is
        # responsible for matching the loader to the chosen input
        # blocks; if they mismatch the forward will fail loudly.
        if batch_kwargs:
            kind_to_tensor = batch_kwargs
        else:
            # Positional: assume the user picked exactly the kinds the
            # loader emits, in loader-emit order.
            kind_to_tensor = {}
            i = 0
            for node_id in self.topo_order:
                cat = self.node_meta[node_id]["cat"]
                if cat != "input": continue
                if i >= len(batch_args):
                    raise FlowCompileError(
                        f"Flow has more input blocks than the loader produces "
                        f"({len(batch_args)} tensors in the batch). Pick a feature "
                        f"set that emits one tensor per input block."
                    )
                kind_to_tensor[node_id] = batch_args[i]
                i += 1

        # Walk topo. For each non-input node, gather upstream outputs by
        # port name, then call the module with the appropriate signature.
        outputs: dict[str, torch.Tensor] = {}
        # Map "node_id:port_name" → upstream tensor for fast lookup
        inbound: dict[str, list[tuple[str, str]]] = {}  # node_id → [(port_name, upstream_tensor_key)]
        for e in self.edges:
            from_node, from_port = e["from"].split(":")
            to_node,   to_port   = e["to"].split(":")
            inbound.setdefault(to_node, []).append((to_port, from_node))

        for node_id in self.topo_order:
            meta = self.node_meta[node_id]
            cat = meta["cat"]
            if cat == "input":
                # Just expose the loader tensor under this node's id.
                outputs[node_id] = kind_to_tensor[node_id]
                continue
            module = self.flow_modules[node_id]
            ports = inbound.get(node_id, [])
            if cat == "encoder":
                # Single input.
                if not ports:
                    raise FlowCompileError(f"Encoder '{node_id}' has no inbound edge.")
                upstream = outputs[ports[0][1]]
                outputs[node_id] = module(upstream)
            elif cat == "fusion":
                # N inputs in port-name order — sort the inbound list by
                # port name (a, b, c, …) so the variadic forward(*xs)
                # gets a deterministic order regardless of edge insertion.
                if not ports:
                    raise FlowCompileError(f"Fusion '{node_id}' has no inbound edges.")
                ports_sorted = sorted(ports, key=lambda pu: pu[0])
                xs = [outputs[src] for _, src in ports_sorted]
                outputs[node_id] = module(*xs)
            elif cat == "head":
                if not ports:
                    raise FlowCompileError(f"Head '{node_id}' has no inbound edge.")
                upstream = outputs[ports[0][1]]
                outputs[node_id] = module(upstream)
            elif cat == "diagnostic":
                # Diagnostic tap: passes the tensor through but calls
                # the module so it can record running stats. Skip if no
                # inbound edge — the validator should have caught that.
                if not ports:
                    continue
                upstream = outputs[ports[0][1]]
                outputs[node_id] = module(upstream) if isinstance(upstream, torch.Tensor) else upstream
            else:
                raise FlowCompileError(f"Unknown block category '{cat}' on node '{node_id}'.")

        head_out = outputs[self.head_node_id]
        # Heads emit (B, 1) — squeeze for the loss.
        if head_out.dim() == 2 and head_out.size(-1) == 1:
            head_out = head_out.squeeze(-1)
        return head_out


# ── Per-impl param schemas ─────────────────────────────────────────────
# Each entry maps a param key to a {kind, min, max, options, ...} rule.
# `kind` is one of int / float / bool / enum. Bounds are inclusive.
# Missing keys are allowed (each impl supplies defaults).
#
# We don't enumerate EVERY param — just the high-impact ones where bad
# values produce confusing torch errors. The rest are validated by the
# builder itself (e.g. `enum` mismatch).
_PARAM_SCHEMAS: dict[str, dict[str, dict]] = {
    "_common_int_positive":  {"min": 1,    "max": 1_000_000, "kind": "int"},
    "_common_dropout":       {"min": 0.0,  "max": 0.99,      "kind": "float"},
    "_common_lr":            {"min": 1e-10,"max": 100.0,     "kind": "float"},
    "_common_temperature":   {"min": 1e-4, "max": 100.0,     "kind": "float"},
}

_PARAM_RULES: dict[str, dict[str, str]] = {
    # param key → name of the _PARAM_SCHEMAS entry to apply
    "filters":         "_common_int_positive",
    "kernel":          "_common_int_positive",
    "kernel_size":     "_common_int_positive",
    "layers":          "_common_int_positive",
    "hidden":          "_common_int_positive",
    "embed_dim":       "_common_int_positive",
    "heads":           "_common_int_positive",
    "max_len":         "_common_int_positive",
    "n_estimators":    "_common_int_positive",
    "iterations":      "_common_int_positive",
    "max_depth":       "_common_int_positive",
    "depth":           "_common_int_positive",
    "num_classes":     "_common_int_positive",
    "proj_dim":        "_common_int_positive",
    "shared_dim":      "_common_int_positive",
    "k":               "_common_int_positive",
    "attn_dim":        "_common_int_positive",
    "fp_bits":         "_common_int_positive",
    "fp_radius":       "_common_int_positive",
    "log_every_n_steps":"_common_int_positive",
    "sample_size":     "_common_int_positive",
    "n_negatives":     "_common_int_positive",
    "n_filters":       "_common_int_positive",
    "n_gaussians":     "_common_int_positive",
    "max_atoms":       "_common_int_positive",
    "iters":           "_common_int_positive",
    "n_estimators":    "_common_int_positive",
    "map_size":        "_common_int_positive",
    "dropout":         "_common_dropout",
    "label_smoothing": "_common_dropout",
    "lr":              "_common_lr",
    "learning_rate":   "_common_lr",
    "weight_decay":    {"min": 0.0, "max": 100.0, "kind": "float"},
    "temperature":     "_common_temperature",
    "subsample":       {"min": 0.0, "max": 1.0,   "kind": "float"},
    "reg_lambda":      {"min": 0.0, "max": 1e6,   "kind": "float"},
    "l2_leaf_reg":     {"min": 0.0, "max": 1e6,   "kind": "float"},
    "pos_weight":      {"min": 0.0, "max": 1e6,   "kind": "float"},
}


def _validate_node_params(node: dict) -> None:
    """Raise FlowCompileError if the node's params dict has bad values.

    Type-checks (int/float/bool) and range-checks the common keys
    against `_PARAM_RULES`. Strings + enum-typed params pass through
    untouched — the builder validates those itself (e.g. an unknown
    `pool='banana'` would fail in nn.Linear, but the builder usually
    handles enum mismatches with a clear message).
    """
    block_id = node.get("block_id", "?")
    impl_id  = node.get("impl_id", "default")
    params   = node.get("params") or {}
    nid      = node.get("id", "?")
    for key, val in params.items():
        rule = _PARAM_RULES.get(key)
        if rule is None:
            continue
        if isinstance(rule, str):
            rule = _PARAM_SCHEMAS[rule]
        kind = rule.get("kind")
        mn   = rule.get("min")
        mx   = rule.get("max")
        if kind == "int":
            # Accept ints + bool (which is an int subclass) + floats that
            # are integer-valued (sometimes JSON serializes 8 as 8.0).
            try:
                vint = int(val)
                if isinstance(val, float) and float(vint) != float(val):
                    raise ValueError("non-integer float")
            except (TypeError, ValueError):
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}' must be "
                    f"an integer; got {val!r} ({type(val).__name__})."
                )
            if mn is not None and vint < mn:
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}'={vint} "
                    f"is below the minimum {mn}."
                )
            if mx is not None and vint > mx:
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}'={vint} "
                    f"is above the maximum {mx}."
                )
        elif kind == "float":
            try:
                vf = float(val)
            except (TypeError, ValueError):
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}' must be "
                    f"a number; got {val!r} ({type(val).__name__})."
                )
            if vf != vf:  # NaN check (NaN != NaN)
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}' is NaN."
                )
            if mn is not None and vf < mn:
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}'={vf} "
                    f"is below the minimum {mn}."
                )
            if mx is not None and vf > mx:
                raise FlowCompileError(
                    f"Node '{nid}' ({block_id}/{impl_id}) param '{key}'={vf} "
                    f"is above the maximum {mx}."
                )


# Encoder impls that REQUIRE a pre-computed embedding upstream. Wiring
# them to a raw `in.protein_seq` token stream is a common GUI mistake —
# the encoder either has no on-the-fly forward at all (esm2_frozen,
# protbert in this build) or would be ruinously slow if it did. The
# normalizer below auto-rewrites such edges to use the cached
# `in.protein_emb` block instead.
_EMB_REQUIRING_PROTEIN_ENCODERS: set[tuple[str, str]] = {
    ("enc.protein_seq", "esm2_frozen"),
    ("enc.protein_seq", "protbert"),
}


def _autoroute_embedding_encoders(flow_spec: dict) -> tuple[dict, list[str]]:
    """Rewrite raw `in.protein_seq → enc.protein_seq/{esm2_frozen,protbert}`
    edges to use the cached `in.protein_emb` block instead.

    Why: those encoders need a (B, 1280) float embedding upstream — on-the-fly
    ESM-2/ProtBert forward isn't wired into the flow compiler (and would be
    far too slow for a trainer loop). The cached ESM-2 embedding is the
    canonical path and is what every preset uses.

    Strategy:
      - For each esm2_frozen / protbert encoder node, find its incoming
        edges. If the source is an `in.protein_seq` block, swap the source
        for an `in.protein_emb` node.
      - If that `in.protein_seq` block has no OTHER downstream consumers,
        promote it in-place to `in.protein_emb` (just relabel its block_id).
        Otherwise, fork: insert a fresh `in.protein_emb` node and rewire
        only the relevant edge to it.
      - Returns the (possibly new) flow_spec dict and a list of human-
        readable notes describing every rewrite (so the trainer can log
        them and the user sees what happened).
    """
    import copy
    nodes = flow_spec.get("nodes") or []
    edges = flow_spec.get("edges") or []
    if not nodes or not edges:
        return flow_spec, []

    # Find encoder nodes that need a cached embedding upstream.
    enc_nodes = {
        n["id"]: n for n in nodes
        if (n.get("block_id"), n.get("impl_id")) in _EMB_REQUIRING_PROTEIN_ENCODERS
    }
    if not enc_nodes:
        return flow_spec, []

    # Index for quick lookup.
    nodes_by_id = {n["id"]: n for n in nodes}

    # Count how many OUTGOING edges each input has (excluding the edge to
    # the embedding-requiring encoder) so we know whether to relabel
    # in-place or fork a new node.
    outgoing_by_src: dict[str, list[dict]] = {}
    for e in edges:
        src = e["from"].split(":")[0]
        outgoing_by_src.setdefault(src, []).append(e)

    new_spec = copy.deepcopy(flow_spec)
    new_nodes = new_spec["nodes"]
    new_edges = new_spec["edges"]
    notes: list[str] = []

    # Iterate over each incoming edge to a requiring-encoder. We modify
    # new_nodes / new_edges in-place — keyed on node id (stable across
    # the iteration since we never delete, only add or relabel).
    new_node_counter = 0
    for e in list(new_edges):
        src_id = e["from"].split(":")[0]
        dst_id = e["to"].split(":")[0]
        if dst_id not in enc_nodes:
            continue
        src = next((n for n in new_nodes if n["id"] == src_id), None)
        if src is None or src.get("block_id") != "in.protein_seq":
            continue
        # Decide: relabel in place, or fork a new in.protein_emb node?
        other_consumers = [
            other for other in outgoing_by_src.get(src_id, [])
            if other is not e
            and other["to"].split(":")[0] != dst_id
        ]
        if not other_consumers:
            # Safe to relabel in place — no other downstream uses the
            # raw sequence anyway.
            src["block_id"] = "in.protein_emb"
            # Drop incompatible params (e.g. max_len for tokenisation).
            src.pop("params", None)
            notes.append(
                f"auto-routed input '{src_id}' from in.protein_seq → in.protein_emb "
                f"(cached ESM-2 embedding) to feed "
                f"{enc_nodes[dst_id].get('block_id')}/{enc_nodes[dst_id].get('impl_id')}"
            )
        else:
            # Fork: clone the input as a fresh in.protein_emb node and
            # rewire just this edge to it.
            new_node_counter += 1
            new_id = f"{src_id}__emb{new_node_counter}"
            # Ensure id uniqueness in case the user already has one.
            existing_ids = {n["id"] for n in new_nodes}
            while new_id in existing_ids:
                new_node_counter += 1
                new_id = f"{src_id}__emb{new_node_counter}"
            new_nodes.append({
                "id": new_id,
                "block_id": "in.protein_emb",
                "impl_id": src.get("impl_id", "default"),
            })
            e["from"] = f"{new_id}:{e['from'].split(':',1)[1]}" if ":" in e["from"] else new_id
            notes.append(
                f"auto-routed encoder '{dst_id}' to a cached ESM-2 embedding "
                f"(forked new input '{new_id}'); the original '{src_id}' still "
                f"feeds other consumers"
            )

    return new_spec, notes


# Fusion impls that require EXACTLY two inputs (bilinear forms,
# pairwise dot products, symmetric cross-attention). Wiring them to a
# 3+ input flow is a common GUI mistake — the auto-route below promotes
# them to a compatible N-input fusion so the compile doesn't fail.
_TWO_INPUT_ONLY_FUSIONS: set[tuple[str, str]] = {
    ("fuse", "bilinear"),
    ("fuse", "cross_attn"),
    ("fuse", "two_tower_dot"),
}

# When we need to promote a 2-input fusion to N-input, this is the
# replacement. ``concat_mlp`` is the safe default: it accepts any
# number of inputs, learns its own mixing weights, and degrades to a
# simple linear projection when N=2 (so the rewrite never makes a
# 2-input flow worse). Users who want a different N-input behaviour
# (weighted_mean / attention_pool / gated_sum) can still set it
# explicitly on the GUI; the autoroute only fires when the current
# impl is structurally incompatible.
_FUSION_N_INPUT_REPLACEMENT: dict[tuple[str, str], tuple[str, str]] = {
    ("fuse", "bilinear"):      ("fuse", "concat_mlp"),
    ("fuse", "cross_attn"):    ("fuse", "concat_mlp"),
    ("fuse", "two_tower_dot"): ("fuse", "concat_mlp"),
}


def _autoroute_incompatible_fusions(flow_spec: dict) -> tuple[dict, list[str]]:
    """Rewrite fusion impls that can't accept the number of inputs the
    flow actually has. Mirrors `_autoroute_embedding_encoders` in spirit:
    catch a structurally impossible wiring at compile time and rewrite
    to something that works, rather than fail with a confusing error.

    For each fuse node:
      * Count the incoming edges (= number of operands).
      * If the node's impl is in `_TWO_INPUT_ONLY_FUSIONS` and N != 2,
        promote it to its N-input replacement (concat_mlp by default).

    Returns ``(flow_spec, notes)``. Notes describe each rewrite so the
    trainer can surface them in the run log.
    """
    import copy
    nodes = flow_spec.get("nodes") or []
    edges = flow_spec.get("edges") or []
    if not nodes or not edges:
        return flow_spec, []

    # Index incoming edge counts per node.
    inbound_count: dict[str, int] = {}
    for e in edges:
        dst = e["to"].split(":")[0]
        inbound_count[dst] = inbound_count.get(dst, 0) + 1

    rewrites: list[tuple[str, tuple[str, str], tuple[str, str], int]] = []
    for n in nodes:
        impl_pair = (n.get("block_id", ""), n.get("impl_id", ""))
        if impl_pair not in _TWO_INPUT_ONLY_FUSIONS:
            continue
        n_in = inbound_count.get(n["id"], 0)
        if n_in == 2:
            continue  # The user's wiring is compatible.
        replacement = _FUSION_N_INPUT_REPLACEMENT.get(impl_pair)
        if replacement is None:
            continue
        rewrites.append((n["id"], impl_pair, replacement, n_in))

    if not rewrites:
        return flow_spec, []

    new_spec = copy.deepcopy(flow_spec)
    notes: list[str] = []
    for nid, old_impl, new_impl, n_in in rewrites:
        for n in new_spec["nodes"]:
            if n.get("id") == nid:
                n["block_id"] = new_impl[0]
                n["impl_id"] = new_impl[1]
                break
        notes.append(
            f"auto-promoted fusion '{nid}' from {old_impl[0]}/{old_impl[1]} "
            f"to {new_impl[0]}/{new_impl[1]} — {old_impl[0]}/{old_impl[1]} "
            f"requires exactly 2 inputs, this flow gave it {n_in}. "
            f"concat_mlp accepts any N and is the safe default; pick "
            f"weighted_mean / attention_pool / gated_sum explicitly on "
            f"the GUI if you want a different N-input behaviour."
        )
    return new_spec, notes


def compile_flow(flow_spec: dict) -> FlowModule:
    """Take a {nodes, edges} graph from the GUI and return a FlowModule.

    Raises FlowCompileError on any issue (cycles, unsupported impls,
    missing inputs, multiple heads, etc.).
    """
    # Normalize before everything else — this is the canonical place to
    # silently fix common wirings the user couldn't reasonably know to
    # set up themselves (cached ESM-2 embeddings, etc.). Notes are
    # surfaced via flow_spec["_autoroute_notes"] so the trainer can
    # log them after compile succeeds.
    flow_spec, _autoroute_notes = _autoroute_embedding_encoders(flow_spec)
    if _autoroute_notes:
        flow_spec.setdefault("_autoroute_notes", []).extend(_autoroute_notes)
    # Second autoroute pass: any fusion node whose impl can't handle
    # the actual number of incoming edges gets promoted to a compatible
    # N-input fusion. Runs AFTER the embedding-encoder pass so the
    # final edge counts reflect any node additions from that step.
    flow_spec, _fusion_notes = _autoroute_incompatible_fusions(flow_spec)
    if _fusion_notes:
        flow_spec.setdefault("_autoroute_notes", []).extend(_fusion_notes)
    nodes = flow_spec.get("nodes") or []
    edges = flow_spec.get("edges") or []
    if not nodes:
        raise FlowCompileError("Flow has no nodes.")

    # ── Per-node param schema validation ─────────────────────────────
    # Catch typos like `filters=-5` or `dropout=2.0` at compile time
    # rather than letting the trainer hit an opaque torch error in
    # epoch 1. The rules below are intentionally lenient — they only
    # flag impossible values, not all of stylistic preference (e.g.
    # `hidden=10000` is allowed even though it's wasteful). The goal
    # is "no silent disasters", not "best-practice enforcement".
    for n in nodes:
        _validate_node_params(n)

    # Reach into the GUI catalogue is impractical here (the catalogue is
    # JSON living in data.js). Instead we use the block_id + impl_id from
    # the node spec directly + the local _BUILDERS / _HEAD_BUILDERS tables.

    # ── Topological order ────────────────────────────────────────
    adj: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    indeg: dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in edges:
        f = e["from"].split(":")[0]
        t = e["to"].split(":")[0]
        adj[f].append(t)
        indeg[t] = indeg.get(t, 0) + 1
    queue = [nid for nid, d in indeg.items() if d == 0]
    topo: list[str] = []
    while queue:
        u = queue.pop(0)
        topo.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    if len(topo) != len(nodes):
        raise FlowCompileError("Flow graph contains a cycle (cannot topo-sort).")

    # ── Reject orphan inputs ────────────────────────────────────
    # An input block with no outgoing edge would be loaded by the
    # dataset (consuming memory + CPU) but never consumed by the
    # FlowModule. Worse, it can confuse `loader_shape_for_flow` into
    # picking the wrong loader for the active topology. Require every
    # `in.*` block to feed at least one downstream node.
    outgoing: dict[str, int] = {n["id"]: 0 for n in nodes}
    for e in edges:
        outgoing[e["from"].split(":")[0]] = outgoing.get(e["from"].split(":")[0], 0) + 1
    for n in nodes:
        if n["block_id"].startswith("in.") and outgoing.get(n["id"], 0) == 0:
            raise FlowCompileError(
                f"Input block '{n['id']}' ({n['block_id']}) has no outgoing "
                f"edge — it would load data the model never consumes. Wire it "
                f"to an encoder, or remove the node."
            )

    # ── Build each node's module ────────────────────────────────
    node_meta: dict[str, dict] = {}
    out_dims: dict[str, int] = {}        # node_id → output dim (for input dim of next block)
    modules_by_id: dict[str, nn.Module] = {}
    input_kinds: dict[str, str] = {}     # node_id → kind string for forward routing
    head_node_id: Optional[str] = None
    loss_name: Optional[str] = None

    for nid in topo:
        node = next(n for n in nodes if n["id"] == nid)
        block_id = node["block_id"]
        impl_id  = node.get("impl_id", "default")
        params   = node.get("params") or {}
        cat = _category_for_block_id(block_id)
        node_meta[nid] = {"block_id": block_id, "impl_id": impl_id, "cat": cat}

        if cat == "input":
            kind = _INPUT_KINDS.get(block_id)
            if not kind:
                raise FlowCompileError(
                    f"Input block '{block_id}' is not yet wired in the compiler. "
                    f"Supported: {sorted(_INPUT_KINDS)}"
                )
            input_kinds[nid] = kind
            # Embedding-shaped inputs declare their dim up front so
            # downstream encoders (e.g. enc.protein_seq/esm2_frozen,
            # enc.tabular/mlp) can size their projection layers correctly.
            # Token / graph / map inputs leave out_dim=None because their
            # output shape is only meaningful after the encoder.
            # Per-node `params` can override the default dim — e.g.
            # in.ligand_fp with `params.fp_bits: 1024` flags that the
            # loader was reconfigured for a 1024-bit Morgan fingerprint
            # and the downstream MLP needs to size to 1024, not 2048.
            declared_dim = _INPUT_DIMS.get(block_id)
            if block_id == "in.ligand_fp" and "fp_bits" in params:
                declared_dim = int(params["fp_bits"])
            elif block_id == "in.protein_emb" and "emb_dim" in params:
                declared_dim = int(params["emb_dim"])
            out_dims[nid] = declared_dim
            continue

        # Collect upstream out_dims for this node so the builder can size
        # itself correctly (fusion / head / tabular MLP need this).
        # For fusion blocks we gather ALL inbound edges (a, b, c, …),
        # ordered by destination port name so the FlowModule.forward
        # passes them in a deterministic order. For encoder/head we
        # take the first inbound (they're single-input by design).
        inbound_with_port: list[tuple[str, Optional[int]]] = []
        for e in edges:
            to_node, to_port = e["to"].split(":")
            if to_node == nid:
                from_node = e["from"].split(":")[0]
                inbound_with_port.append((to_port, out_dims.get(from_node)))
        # Sort fusion inputs by port name so {a, b, c, d} arrive in order.
        inbound_with_port.sort(key=lambda x: x[0])
        upstream_dims: list[Optional[int]] = [d for _, d in inbound_with_port]
        a_dim = upstream_dims[0] if upstream_dims else None
        b_dim = upstream_dims[1] if len(upstream_dims) > 1 else None

        if cat == "head":
            # Per-impl override (xgboost / catboost stubs) takes precedence
            # over the role's default builder.
            impl_builder = _HEAD_IMPL_BUILDERS.get((block_id, impl_id))
            builder = impl_builder or _HEAD_BUILDERS.get(block_id)
            if builder is None:
                raise FlowCompileError(
                    f"Head '{block_id}' is not yet wired. Supported heads: "
                    f"{sorted(_HEAD_BUILDERS)}"
                )
            in_dim = a_dim or 256
            module, head_loss = builder(params, in_dim)
            modules_by_id[nid] = module
            head_node_id = nid
            loss_name = head_loss
            out_dims[nid] = 1
            continue

        if cat == "fusion":
            builder = _BUILDERS.get((block_id, impl_id))
            if builder is None:
                raise FlowCompileError(
                    f"Fusion impl '{impl_id}' for block '{block_id}' isn't wired yet. "
                    f"Try 'concat_mlp' / 'bilinear' / 'two_tower_dot' / 'cross_attn' / "
                    f"'weighted_mean' / 'attention_pool' / 'gated_sum'."
                )
            if not upstream_dims:
                raise FlowCompileError(f"Fusion '{nid}' has no inbound edges.")
            # Default unknown dims to 256 (graph-shaped inputs leave dim
            # unknown until their encoder runs, but for fusion the
            # upstream IS an encoder so this is rarely triggered).
            resolved = [d if d is not None else 256 for d in upstream_dims]
            module, out_dim = builder(params, resolved)
            modules_by_id[nid] = module
            out_dims[nid] = out_dim
            continue

        if cat == "encoder":
            builder = _BUILDERS.get((block_id, impl_id))
            if builder is None:
                raise FlowCompileError(
                    f"Encoder impl '{impl_id}' for block '{block_id}' isn't wired yet. "
                    f"See flow_compiler.py for the supported (block_id, impl_id) pairs."
                )
            module, out_dim, _kind = builder(params, a_dim)
            modules_by_id[nid] = module
            out_dims[nid] = out_dim
            continue

        if cat == "diagnostic":
            # Real diagnostic tap: passes the tensor through unchanged
            # but records running statistics so the trainer can emit
            # a `diag` event per epoch (the GUI's Smart Insights card
            # picks these up).
            tap = _DiagTap(
                log_every_n_steps=int((params or {}).get("log_every_n_steps", 50)),
                sample_size=int((params or {}).get("sample_size", 8)),
                node_id=nid,
            )
            modules_by_id[nid] = tap
            out_dims[nid] = a_dim
            continue

        raise FlowCompileError(f"Block '{block_id}' has unknown category '{cat}'.")

    if head_node_id is None:
        raise FlowCompileError("Flow has no head — nothing to train.")

    return FlowModule(
        modules_by_id=modules_by_id,
        topo_order=topo,
        node_meta=node_meta,
        edges=edges,
        head_node_id=head_node_id,
        loss_name=loss_name,
        input_kinds=input_kinds,
    )


def _category_for_block_id(block_id: str) -> str:
    """Recover the block category from its id prefix."""
    if block_id.startswith("in."):    return "input"
    if block_id.startswith("enc."):   return "encoder"
    if block_id.startswith("fuse"):   return "fusion"
    if block_id.startswith("head."):  return "head"
    if block_id.startswith("diag."):  return "diagnostic"
    raise FlowCompileError(f"Cannot infer category for block_id '{block_id}'.")


# ── Loader-shape compatibility helper ────────────────────────────────
# When the trainer is given a flow spec, it has to pick a loader that
# emits the right kinds of tensors. This function inspects the spec's
# input blocks and returns a short string the trainer can match on.

def loader_shape_for_flow(flow_spec: dict) -> str:
    """Return one of:
        ``"seq_smi"``       — DeepDTA-style (protein_seq + ligand SMILES)
        ``"seq_fp"``        — protein_seq + ligand_fp (Morgan ECFP4 floats)
        ``"seq_graph"``     — GraphDTA-style (protein_seq + ligand_graph)
        ``"struct_graph"``  — StructGNN-DTA (protein_graph + ligand_graph)
        ``"pp_graph"``      — PPI-Siamese (two protein graphs)
        ``"pp_seq"``        — PPI two-tower (two protein seq) — planned
        ``"pp_emb"``        — PPI two-tower on cached ESM-2 embeddings
        ``"esm_smi"``       — protein_emb (cached ESM-2) + ligand SMILES
        ``"esm_fp"``        — protein_emb (cached ESM-2) + ligand_fp (ECFP4)
        ``"esm_graph"``     — protein_emb (cached ESM-2) + ligand graph
        ``"esm_tabular"``   — protein_emb + ligand tabular (unimol/physchem)
        ``"multi"``         — 3+ inputs (per-block loader emits one tensor
                              per input node in topo order)
        ``"unsupported"``   — fall back to standard warehouse loader
    """
    if not flow_spec or not flow_spec.get("nodes"):
        return "unsupported"
    # Apply the same auto-routing the compiler will apply, so the loader
    # shape reflects the rewritten flow (e.g. esm2_frozen wired to
    # in.protein_seq becomes a protein_emb input and loader shape
    # collapses to esm_smi / esm_fp / esm_graph). Without this the
    # trainer would log "unsupported" and pick the default warehouse
    # loader, which wouldn't produce a protein_emb tensor.
    flow_spec, _ = _autoroute_embedding_encoders(flow_spec)
    # Look at the raw block_ids rather than just kinds — lets us
    # distinguish protein_seq vs protein_emb cleanly.
    block_ids = [n.get("block_id", "") for n in flow_spec["nodes"]
                 if n.get("block_id", "").startswith("in.")]
    has = set(block_ids)
    # 3+ inputs → multi-feature loader. The dedicated 2-input loaders
    # below all assume exactly two input tensors; anything else needs
    # the generic per-block loader that emits one tensor per input
    # node in the flow's topo order.
    if len(block_ids) >= 3:
        return "multi"
    # Two protein-embedding inputs → PP-ESM (cached two-tower PPI).
    if block_ids.count("in.protein_emb") == 2 and not any(
        b.startswith("in.ligand") for b in block_ids
    ):
        return "pp_emb"
    # ESM-2 cached embedding combos
    if "in.protein_emb" in has:
        if "in.ligand_graph" in has:                 return "esm_graph"
        if "in.ligand_fp" in has:                    return "esm_fp"
        if "in.ligand_smiles" in has:                return "esm_smi"
        # Treat unimol / physchem / descriptors as the ligand-tabular
        # variant. There's no dedicated loader for these yet so we
        # surface "unsupported" — the trainer will fall back to the
        # standard warehouse loader and the flow compile will likely
        # then fail with a clearer dim-mismatch error.
        if "in.ligand_unimol" in has or "in.ligand_physchem" in has \
           or "in.ligand_descriptors" in has:
            return "esm_tabular"
    # Standard combos
    input_kinds = []
    for n in flow_spec["nodes"]:
        kind = _INPUT_KINDS.get(n.get("block_id", ""))
        if kind:
            input_kinds.append(kind)
    kinds = tuple(sorted(input_kinds))
    if kinds == ("ligand_seq", "protein_seq"):       return "seq_smi"
    # protein_seq + ligand_fp/unimol/physchem → seq_fp loader (emits
    # seq_tokens + float fingerprint, NOT int SMILES tokens). Routing
    # to seq_smi here was a real bug: the ligand-side tabular MLP would
    # receive int64 tokens and crash with "mat1 and mat2 must have the
    # same dtype, but got Long and Float".
    if kinds == ("ligand_tabular", "protein_seq"):   return "seq_fp"
    if kinds == ("ligand_graph", "protein_seq"):     return "seq_graph"
    if kinds == ("ligand_graph", "protein_graph"):   return "struct_graph"
    if kinds == ("protein_graph", "protein_graph"):  return "pp_graph"
    if kinds == ("protein_seq", "protein_seq"):      return "pp_seq"
    return "unsupported"
