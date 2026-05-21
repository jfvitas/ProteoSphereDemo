"""Residue-level protein graphs for GNN templates.

Produces a torch_geometric ``Data`` object per protein with:

    .x           (n_residues, RESIDUE_FEAT_DIM)  float32 node features
    .edge_index  (2, n_edges)                    long  bidirectional
    .edge_attr   (n_edges, 1)                    float32 distance / weight

Two construction paths, picked automatically:

1. **PDB-derived contact graph** (preferred).
   When a PDB / mmCIF file is cached under ``structure_root/`` (e.g.
   ``data/raw/alphafold/<snapshot>/<snapshot>/<UNIPROT>/<UNIPROT>.pdb.pdb``
   from the AlphaFold ingester), we parse it with Biopython, extract
   each residue's CA coordinate, and add an edge between any two
   residues with CA-CA distance ≤ ``contact_cutoff`` Å (default 8 Å,
   matching the ``features/structure_graphs.py`` ResidueGraph default).
   Edge weight = inverse distance.

2. **Sequence-only fallback** (always works).
   When no structure is available we build a "sliding-window" graph:
   linear backbone edges (i, i+1) plus short-range non-bonded edges
   to (i+2) and (i+3) so a GCN can still learn local sequence context.
   Used when ``structure_root`` is missing or a UniProt has no cache hit.

The node-feature vector is **22-dim**:
    20  one-hot amino-acid identity (canonical 20 + 'X' last bucket)
     1  normalised sequence position (0..1)
     1  has_structure flag (1.0 when path #1 was used; 0.0 for path #2)

We deliberately keep features small so the encoder's first layer stays
cheap. Richer features (secondary-structure, SASA, pseudo-Bfactor)
would slot in here without breaking downstream batching.

NOTE: The featurizer caches per-uniprot graphs once at dataset load
(228 KIBA / 442 Davis proteins) so each epoch reuses them — important
because torch_geometric.data.Data construction is non-trivial on the
Python side.
"""

from __future__ import annotations

import os
import glob
from functools import lru_cache
from typing import Optional

import numpy as np

# 20 canonical AAs in the order used by every ML protein pipeline that's
# trained on UniProt. 'X' / unknown / non-standard residues land in the
# last bucket so we never raise on rare residues.
_AA_ORDER = "ACDEFGHIKLMNPQRSTVWY"   # 20 chars
_AA_TO_IDX = {a: i for i, a in enumerate(_AA_ORDER)}
_THREE_TO_ONE = {
    "ALA": "A", "CYS": "C", "ASP": "D", "GLU": "E", "PHE": "F",
    "GLY": "G", "HIS": "H", "ILE": "I", "LYS": "K", "LEU": "L",
    "MET": "M", "ASN": "N", "PRO": "P", "GLN": "Q", "ARG": "R",
    "SER": "S", "THR": "T", "VAL": "V", "TRP": "W", "TYR": "Y",
    # Selenocysteine, pyrrolysine, unknowns map to X (the 21st bucket).
    "SEC": "X", "PYL": "X", "ASX": "X", "GLX": "X", "XAA": "X", "UNK": "X",
}

# 20 AA channels + 1 unknown bucket = 21, plus position + has_structure = 23.
# Keep the constant publicly accessible so the GNN's first layer can size to it.
RESIDUE_FEAT_DIM = 23


def _aa_onehot(letter: str) -> np.ndarray:
    """One-hot in R^21 (20 canonical + 1 unknown)."""
    v = np.zeros(21, dtype=np.float32)
    idx = _AA_TO_IDX.get(letter)
    if idx is None:
        v[20] = 1.0
    else:
        v[idx] = 1.0
    return v


def _node_features(sequence: str, has_structure: bool) -> np.ndarray:
    """Build (n_residues, RESIDUE_FEAT_DIM) node feature matrix."""
    n = len(sequence)
    if n == 0:
        return np.zeros((1, RESIDUE_FEAT_DIM), dtype=np.float32)
    feats = np.zeros((n, RESIDUE_FEAT_DIM), dtype=np.float32)
    inv = 1.0 / max(n - 1, 1)
    has_struct_val = 1.0 if has_structure else 0.0
    for i, c in enumerate(sequence):
        feats[i, :21] = _aa_onehot(c)
        feats[i, 21]  = i * inv          # normalised position in [0, 1]
        feats[i, 22]  = has_struct_val
    return feats


# ── Sequence-only fallback graph ───────────────────────────────────────

def sequence_to_linear_graph(
    sequence: str,
    *,
    window: int = 3,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a (linear-backbone + short-range non-bond) edge index for a
    protein sequence with no structure data.

    Edges include the bonded backbone (i, i+1) and the next ``window``
    sequence neighbours on each side (i, i+2), (i, i+3), ...  Distance
    weights are 1/|i-j| so the GCN biases more weight to the immediate
    neighbour as it would in a real chain.

    Returns:
        edge_index (2, n_edges) int64, edge_attr (n_edges, 1) float32,
        and a length-N node feature matrix.
    """
    n = len(sequence)
    if n == 0:
        return (
            np.zeros((2, 0), dtype=np.int64),
            np.zeros((0, 1), dtype=np.float32),
            _node_features(sequence, has_structure=False),
        )
    src: list[int] = []
    dst: list[int] = []
    w:   list[float] = []
    for i in range(n):
        for k in range(1, window + 1):
            j = i + k
            if j >= n:
                break
            src.extend([i, j])
            dst.extend([j, i])
            inv_d = 1.0 / float(k)
            w.extend([inv_d, inv_d])
    if not src:
        # Single residue — self-loop so message passing has somewhere to go.
        src, dst, w = [0], [0], [1.0]
    edge_index = np.array([src, dst], dtype=np.int64)
    edge_attr  = np.array(w, dtype=np.float32).reshape(-1, 1)
    feats      = _node_features(sequence, has_structure=False)
    return edge_index, edge_attr, feats


# ── PDB-derived contact graph ──────────────────────────────────────────

def find_pdb_file(uniprot: str, structure_root: Optional[str]) -> Optional[str]:
    """Locate a cached PDB/CIF for the given UniProt.

    Looks under ``structure_root`` for any file matching
    ``**/<UNIPROT>/<UNIPROT>.pdb*`` or ``**/<UNIPROT>.pdb*``. The
    AlphaFold ingester writes paths like
    ``data/raw/alphafold/<snapshot>/<snapshot>/<UNIPROT>/<UNIPROT>.pdb.pdb``
    so the recursive glob handles the timestamped-snapshot layout.
    Returns the first match (deterministic by directory order).
    """
    if not uniprot or not structure_root:
        return None
    if not os.path.isdir(structure_root):
        return None
    # Most common case first — direct uniprot directory.
    patterns = [
        os.path.join(structure_root, "**", uniprot, f"{uniprot}.pdb*"),
        os.path.join(structure_root, "**", uniprot, f"{uniprot}.cif*"),
        os.path.join(structure_root, "**", f"{uniprot}.pdb*"),
        os.path.join(structure_root, "**", f"{uniprot}.cif*"),
    ]
    for pat in patterns:
        hits = sorted(glob.iglob(pat, recursive=True))
        for h in hits:
            # Filter out empty / corrupt placeholder files.
            try:
                if os.path.getsize(h) > 200:
                    return h
            except OSError:
                continue
    return None


def pdb_to_residue_graph(
    pdb_path: str,
    *,
    contact_cutoff: float = 8.0,
    sequence_hint: Optional[str] = None,
) -> Optional[tuple[np.ndarray, np.ndarray, np.ndarray, str]]:
    """Parse a PDB/CIF, extract per-residue CA coordinates, build a
    contact graph with edges between residues whose CA-CA distance is
    ≤ ``contact_cutoff`` Å.

    Returns ``(edge_index, edge_attr, node_features, parsed_sequence)``
    on success, or ``None`` if parsing fails.

    Edge weight is inverse distance in Å.

    Backbone bonds (i, i+1) are always included so the graph stays
    connected even at small cutoffs. Self-loops are NOT added — GCNConv
    in torch_geometric adds them itself if needed.
    """
    try:
        from Bio.PDB import PDBParser, MMCIFParser
        from Bio.PDB.PDBExceptions import PDBConstructionWarning
        import warnings as _w
    except Exception:
        return None
    parser = MMCIFParser(QUIET=True) if pdb_path.lower().endswith((".cif", ".cif.gz", ".cif.cif")) \
                                     else PDBParser(QUIET=True)
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = parser.get_structure("p", pdb_path)
    except Exception:
        return None
    # Walk the first model, first chain (AlphaFold monomers are single-chain).
    residues_ca: list[tuple[str, np.ndarray]] = []
    for model in structure:
        for chain in model:
            for residue in chain:
                # Skip heteroatoms / waters
                if residue.id[0] != " ":
                    continue
                if "CA" not in residue:
                    continue
                resname = (residue.resname or "").strip().upper()
                one_letter = _THREE_TO_ONE.get(resname, "X")
                ca = residue["CA"].coord  # numpy array length 3
                residues_ca.append((one_letter, np.asarray(ca, dtype=np.float32)))
            break  # only the first chain
        break       # only the first model
    if not residues_ca:
        return None
    sequence = "".join(rc[0] for rc in residues_ca)
    coords   = np.stack([rc[1] for rc in residues_ca], axis=0)  # (N, 3)
    n = len(sequence)

    # Pairwise distances (N×N) — fine for monomers up to a few thousand residues.
    # Memory: 1000² × 4B = 4 MB. AlphaFold monomers cap around 2700 residues.
    diff = coords[:, None, :] - coords[None, :, :]      # (N, N, 3)
    dist = np.sqrt((diff * diff).sum(axis=-1))          # (N, N)
    # Mask: contacts within cutoff, excluding self.
    contact = (dist <= contact_cutoff) & (dist > 0.0)
    # Always include backbone (i, i+1).
    if n >= 2:
        np.fill_diagonal(contact, False)
        i_indices = np.arange(n - 1)
        contact[i_indices, i_indices + 1] = True
        contact[i_indices + 1, i_indices] = True
    src, dst = np.where(contact)
    if src.size == 0:
        # Degenerate; bail to sequence-only fallback
        return None
    weights = 1.0 / np.maximum(dist[src, dst], 0.5)     # cap at 2.0 for adjacent atoms
    edge_index = np.stack([src, dst], axis=0).astype(np.int64)
    edge_attr  = weights.astype(np.float32).reshape(-1, 1)
    feats      = _node_features(sequence, has_structure=True)
    return edge_index, edge_attr, feats, sequence


# ── Top-level entry point ──────────────────────────────────────────────

def protein_residue_graph(
    *,
    sequence: str,
    uniprot: Optional[str] = None,
    structure_root: Optional[str] = None,
    contact_cutoff: float = 8.0,
    max_residues: int = 1024,
):
    """Build a torch_geometric ``Data`` for one protein.

    Tries the PDB-derived contact graph first (when a structure is
    cached for ``uniprot`` under ``structure_root``), falls back to a
    sequence-only sliding-window graph otherwise.

    The ``max_residues`` truncation caps GPU memory on edge cases
    (very long disordered proteins). Truncating at residue ``max_residues``
    matches what the CNN protein tower does (1000 tokens by default).

    Returns a ``Data`` object with ``.x``, ``.edge_index``,
    ``.edge_attr``, plus a ``.has_structure`` boolean tensor scalar
    that the trainer can log to track structure coverage.
    """
    import torch
    from torch_geometric.data import Data

    edge_index = None
    edge_attr  = None
    feats      = None
    used_structure = False

    pdb_path = find_pdb_file(uniprot, structure_root) if (uniprot and structure_root) else None
    if pdb_path is not None:
        result = pdb_to_residue_graph(pdb_path, contact_cutoff=contact_cutoff,
                                      sequence_hint=sequence)
        if result is not None:
            edge_index, edge_attr, feats, _parsed_seq = result
            used_structure = True

    if feats is None:
        edge_index, edge_attr, feats = sequence_to_linear_graph(sequence)

    # Truncate if needed
    if feats.shape[0] > max_residues:
        feats = feats[:max_residues]
        # Drop edges that point past the cap
        mask = (edge_index[0] < max_residues) & (edge_index[1] < max_residues)
        edge_index = edge_index[:, mask]
        edge_attr  = edge_attr[mask]

    data = Data(
        x=torch.from_numpy(feats),
        edge_index=torch.from_numpy(edge_index),
        edge_attr=torch.from_numpy(edge_attr),
    )
    data.has_structure = torch.tensor(1.0 if used_structure else 0.0, dtype=torch.float32)
    data.n_residues    = torch.tensor(feats.shape[0], dtype=torch.long)
    return data


# Default location for the AlphaFold cache produced by execution/acquire/alphafold_*.
# Callers can override via the ``structure_root`` arg, but this default lets
# the dataset wrapper Just Work on this machine without configuration.
DEFAULT_STRUCTURE_ROOT = "data/raw/alphafold"


@lru_cache(maxsize=4096)
def cached_protein_residue_graph(
    sequence: str,
    uniprot: Optional[str] = None,
    structure_root: Optional[str] = None,
    contact_cutoff: float = 8.0,
):
    """Memoised wrapper. The Dataset wrapper calls this once per unique
    UniProt so 30K Davis records reuse 442 graphs instead of rebuilding
    them every epoch.
    """
    return protein_residue_graph(
        sequence=sequence,
        uniprot=uniprot,
        structure_root=structure_root or DEFAULT_STRUCTURE_ROOT,
        contact_cutoff=contact_cutoff,
    )
