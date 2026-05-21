"""Molecular-graph featurization (SMILES → torch_geometric.data.Data).

Moved into the ``featurizers`` package (was a standalone file at
``api/model_studio/v2/featurizers.py`` that got shadowed once the
package directory landed). Keeping it as a regular submodule makes
``from .featurizers import ATOM_FEAT_DIM, smiles_to_graph`` work
without any importlib magic.

The atom feature set is the GraphDTA paper's (Nguyen 2021):
    44  atom-symbol one-hot (with explicit unknown bucket)
    11  degree one-hot (0..10)
    11  num-H one-hot (0..10)
    11  implicit-valence one-hot (0..10)
     1  aromatic flag
Total: 78 dims = ``ATOM_FEAT_DIM``.
"""

from __future__ import annotations

from typing import List


# Public so downstream nn modules can size their first layer correctly
# without importing rdkit.
ATOM_FEAT_DIM = 78


_ATOM_SYMBOLS = [
    "C", "N", "O", "S", "F", "Si", "P", "Cl", "Br", "Mg", "Na", "Ca",
    "Fe", "As", "Al", "I", "B", "V", "K", "Tl", "Yb", "Sb", "Sn", "Ag",
    "Pd", "Co", "Se", "Ti", "Zn", "H", "Li", "Ge", "Cu", "Au", "Ni",
    "Cd", "In", "Mn", "Zr", "Cr", "Pt", "Hg", "Pb", "Unknown",
]


def _onehot(value, choices) -> List[int]:
    """One-hot encoding with an explicit Unknown bucket as the final slot."""
    if value not in choices:
        value = choices[-1]
    return [int(value == c) for c in choices]


def _implicit_valence(atom) -> int:
    """RDKit's GetImplicitValence is deprecated in 2025 builds; the modern
    call is GetValence(ValenceType.IMPLICIT). Use the new API when present.
    """
    try:
        from rdkit import Chem
        return atom.GetValence(Chem.ValenceType.IMPLICIT)
    except (AttributeError, TypeError):
        return atom.GetImplicitValence()


def _atom_features(atom) -> List[int]:
    """78-d atom feature vector, GraphDTA-faithful."""
    return (
        _onehot(atom.GetSymbol(), _ATOM_SYMBOLS)                               # 44
        + _onehot(atom.GetDegree(), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])         # 11
        + _onehot(atom.GetTotalNumHs(), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])     # 11
        + _onehot(_implicit_valence(atom), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10])  # 11
        + [int(atom.GetIsAromatic())]                                          # 1
    )


def smiles_to_graph(smi: str):
    """Returns a torch_geometric.data.Data with .x (atoms, ATOM_FEAT_DIM)
    and .edge_index (2, n_bonds*2). Returns None if RDKit can't parse the
    SMILES.
    """
    from rdkit import Chem
    import torch
    from torch_geometric.data import Data

    mol = Chem.MolFromSmiles(smi)
    if mol is None or mol.GetNumAtoms() == 0:
        return None
    atoms = mol.GetAtoms()
    x = torch.tensor([_atom_features(a) for a in atoms], dtype=torch.float32)
    src: list[int] = []
    dst: list[int] = []
    for b in mol.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        src.extend([i, j])
        dst.extend([j, i])
    if not src:
        # Single-atom molecule — add a self-loop so message passing has
        # something to do (and downstream layers don't NaN).
        src = [0]
        dst = [0]
    edge_index = torch.tensor([src, dst], dtype=torch.long)
    return Data(x=x, edge_index=edge_index)
