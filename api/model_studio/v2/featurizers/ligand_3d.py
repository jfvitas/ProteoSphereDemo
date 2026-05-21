"""3D conformer-derived ligand features.

Generates a single ETKDG conformer per SMILES (fast — sub-second for
drug-sized molecules) and computes shape + electrostatic descriptors:

  PMI1, PMI2, PMI3       principal moments of inertia
  NPR1, NPR2             normalised PMIs (Sauer + Schwarz 2003) — describe
                         where the molecule sits on the rod-disk-sphere
                         shape triangle
  asphericity             κ from gyration tensor — how non-spherical
  eccentricity            ε from gyration tensor — how elongated
  radius_of_gyration      mass-weighted radius
  inertia_shape_factor    PMI2 / PMI3 — rod vs disc indicator
  spherocity_index        spherocity from RDKit

Two featurizers:

    ligand_3d_shape_8           the eight shape descriptors above
    ligand_3d_with_charges_10   shape + total partial charge + dipole moment
                                proxy via Gasteiger charges
"""

from __future__ import annotations

import numpy as np

from . import register, FeaturizerSpec


_RDKIT_OK = False
try:
    from rdkit import Chem
    from rdkit.Chem import AllChem, Descriptors3D
    _RDKIT_OK = True
except ImportError:
    pass


def _conformer(mol):
    """Embed a single ETKDG conformer + UFF-optimise. Returns the mol
    with conformer attached, or None on failure."""
    try:
        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        if AllChem.EmbedMolecule(mol, params) != 0:
            return None
        try:
            AllChem.UFFOptimizeMolecule(mol, maxIters=200)
        except Exception:
            pass
        return mol
    except Exception:
        return None


def _shape_descriptors(records) -> np.ndarray:
    """Eight shape descriptors per ligand."""
    out = np.zeros((len(records), 8), dtype=np.float32)
    for i, r in enumerate(records):
        smi = getattr(r, "smiles", "") or ""
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        mol3d = _conformer(mol)
        if mol3d is None:
            continue
        try:
            out[i, 0] = Descriptors3D.PMI1(mol3d)
            out[i, 1] = Descriptors3D.PMI2(mol3d)
            out[i, 2] = Descriptors3D.PMI3(mol3d)
            out[i, 3] = Descriptors3D.NPR1(mol3d)
            out[i, 4] = Descriptors3D.NPR2(mol3d)
            out[i, 5] = Descriptors3D.Asphericity(mol3d)
            out[i, 6] = Descriptors3D.Eccentricity(mol3d)
            out[i, 7] = Descriptors3D.RadiusOfGyration(mol3d)
        except Exception:
            pass
    return out


def _shape_plus_charges(records) -> np.ndarray:
    """Shape descriptors + Gasteiger-charge-derived totals (10-dim)."""
    out = np.zeros((len(records), 10), dtype=np.float32)
    for i, r in enumerate(records):
        smi = getattr(r, "smiles", "") or ""
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        mol3d = _conformer(mol)
        if mol3d is None:
            continue
        try:
            out[i, 0] = Descriptors3D.PMI1(mol3d)
            out[i, 1] = Descriptors3D.PMI2(mol3d)
            out[i, 2] = Descriptors3D.PMI3(mol3d)
            out[i, 3] = Descriptors3D.NPR1(mol3d)
            out[i, 4] = Descriptors3D.NPR2(mol3d)
            out[i, 5] = Descriptors3D.Asphericity(mol3d)
            out[i, 6] = Descriptors3D.Eccentricity(mol3d)
            out[i, 7] = Descriptors3D.RadiusOfGyration(mol3d)
            # Gasteiger charges
            AllChem.ComputeGasteigerCharges(mol3d, throwOnParamFailure=False)
            charges = [
                float(a.GetDoubleProp("_GasteigerCharge"))
                for a in mol3d.GetAtoms()
                if a.HasProp("_GasteigerCharge")
            ]
            charges = [c for c in charges if not (np.isnan(c) or np.isinf(c))]
            if charges:
                out[i, 8] = float(sum(c for c in charges if c < 0))  # total negative charge
                out[i, 9] = float(sum(c for c in charges if c > 0))  # total positive charge
        except Exception:
            pass
    return out


# ── Registration ───────────────────────────────────────────────────────

register(FeaturizerSpec(
    id="ligand_3d_shape_8", label="3D shape descriptors (8)",
    axis="ligand", dim=8,
    short_desc="PMI, NPR, asphericity, eccentricity, radius of gyration.",
    long_desc=("Eight shape descriptors computed on a single UFF-optimised "
               "ETKDG conformer. Captures the rod-disc-sphere position of "
               "the molecule. Adds ~0.5 s/ligand at featurization time."),
    requires=["rdkit"], cost="moderate",
    compute=_shape_descriptors if _RDKIT_OK else None,
    integrated=_RDKIT_OK,
))

register(FeaturizerSpec(
    id="ligand_3d_shape_charges_10", label="3D shape + Gasteiger charges (10)",
    axis="ligand", dim=10,
    short_desc="3D shape + total positive/negative partial charges.",
    long_desc=("Shape (8) + total positive and negative Gasteiger partial "
               "charges (2). Adds electrostatic context to the shape "
               "vector — useful when charge complementarity is part of "
               "the binding mode."),
    requires=["rdkit"], cost="moderate",
    compute=_shape_plus_charges if _RDKIT_OK else None,
    integrated=_RDKIT_OK,
))
