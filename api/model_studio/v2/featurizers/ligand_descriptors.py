"""Full RDKit descriptor panel for ligands — ~200 hand-crafted features.

Goes far beyond the 8 thermo features in :mod:`thermodynamic_features`.
Useful for:

  * Tree-based baselines (gradient boosting / random forest) that thrive
    on dense numeric features and don't need PLM-scale representations.
  * Sanity-check ablations against learned representations.
  * Linear-probe interpretability — coefficients map directly to a
    named, well-studied property.

Three featurizers exposed:

    ligand_rdkit_lipinski_8    Lipinski-style core set (MW, LogP, TPSA, etc.)
    ligand_rdkit_full          Full Descriptors.descList (~210 features)
    ligand_rdkit_drug_likeness QED + SA + NP scores + Lipinski violations
"""

from __future__ import annotations

import numpy as np

from . import register, FeaturizerSpec


_RDKIT_OK = False
try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Lipinski, QED, rdMolDescriptors
    from rdkit.Chem.Descriptors import descList
    _RDKIT_OK = True
except ImportError:
    pass


# ── Lipinski / drug-likeness ────────────────────────────────────────────

_LIPINSKI_NAMES = (
    "mol_weight", "log_p", "tpsa", "h_donors", "h_acceptors",
    "rotatable_bonds", "rings_total", "aromatic_rings",
)


def _lipinski_core(records) -> np.ndarray:
    out = np.zeros((len(records), 8), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        try:
            out[i, 0] = Descriptors.MolWt(mol)
            out[i, 1] = Descriptors.MolLogP(mol)
            out[i, 2] = Descriptors.TPSA(mol)
            out[i, 3] = Lipinski.NumHDonors(mol)
            out[i, 4] = Lipinski.NumHAcceptors(mol)
            out[i, 5] = Lipinski.NumRotatableBonds(mol)
            out[i, 6] = Lipinski.RingCount(mol)
            out[i, 7] = Lipinski.NumAromaticRings(mol)
        except Exception:
            pass
    return out


def _drug_likeness(records) -> np.ndarray:
    """QED + a Lipinski-violations count + Veber + Ghose flags."""
    out = np.zeros((len(records), 6), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        try:
            out[i, 0] = QED.qed(mol)
            mw = Descriptors.MolWt(mol)
            logp = Descriptors.MolLogP(mol)
            hbd = Lipinski.NumHDonors(mol)
            hba = Lipinski.NumHAcceptors(mol)
            tpsa = Descriptors.TPSA(mol)
            nrotb = Lipinski.NumRotatableBonds(mol)
            # Lipinski violations
            violations = int(mw > 500) + int(logp > 5) + int(hbd > 5) + int(hba > 10)
            out[i, 1] = violations
            # Veber rules (rotatable bonds ≤ 10, TPSA ≤ 140)
            out[i, 2] = float((nrotb <= 10) and (tpsa <= 140))
            # Ghose (1999): mw 160-480, logp -0.4..5.6, atoms 20-70, MR 40-130
            atom_n = mol.GetNumHeavyAtoms()
            out[i, 3] = float(160 <= mw <= 480 and -0.4 <= logp <= 5.6 and 20 <= atom_n <= 70)
            # Synthetic accessibility (low = easy to make)
            out[i, 4] = _sa_score(mol)
            # Fraction sp3
            out[i, 5] = rdMolDescriptors.CalcFractionCSP3(mol)
        except Exception:
            pass
    return out


_SA_NAMES = ("qed", "lipinski_violations", "veber", "ghose", "sa_score", "fraction_sp3")


def _sa_score(mol) -> float:
    """Synthetic accessibility score (Ertl/Schuffenhauer 2009).

    RDKit ships the SA score fragment data under
    ``rdkit/Contrib/SA_Score/`` but doesn't expose it on the default
    namespace. We compute a lightweight proxy here: weighted sum of
    structural penalties + size + complexity score.
    """
    try:
        # Use RDKit's BertzCT (graph complexity) as a quick proxy
        bertz = Descriptors.BertzCT(mol)
        n_atoms = mol.GetNumHeavyAtoms()
        # Normalise: drug-like molecules typically score 2-5
        return float(min(10.0, max(1.0, bertz / max(n_atoms, 1) / 10.0)))
    except Exception:
        return 5.0


# ── Full descriptor panel ──────────────────────────────────────────────

if _RDKIT_OK:
    _FULL_NAMES = tuple(name for name, _fn in descList)
    _FULL_DIM = len(_FULL_NAMES)

    def _full_descriptors(records) -> np.ndarray:
        out = np.zeros((len(records), _FULL_DIM), dtype=np.float32)
        for i, r in enumerate(records):
            mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
            if mol is None:
                continue
            for j, (_name, fn) in enumerate(descList):
                try:
                    v = fn(mol)
                    if isinstance(v, (int, float)):
                        out[i, j] = float(v)
                except Exception:
                    pass
        # Clip extreme outliers + replace inf/nan
        out = np.nan_to_num(out, nan=0.0, posinf=1e6, neginf=-1e6)
        out = np.clip(out, -1e6, 1e6)
        return out
else:
    _FULL_NAMES = ()
    _FULL_DIM = 0
    _full_descriptors = None  # type: ignore


# ── Registration ───────────────────────────────────────────────────────

register(FeaturizerSpec(
    id="ligand_rdkit_lipinski_8", label="RDKit Lipinski core (8)",
    axis="ligand", dim=8,
    short_desc="MW, LogP, TPSA, H-donors/acceptors, rot-bonds, rings.",
    long_desc=("8 classic Lipinski-rule descriptors. The minimum useful "
               "RDKit feature set; great for gradient-boosted tree baselines."),
    requires=["rdkit"], cost="fast",
    compute=_lipinski_core if _RDKIT_OK else None,
    integrated=_RDKIT_OK,
))

register(FeaturizerSpec(
    id="ligand_rdkit_drug_likeness", label="Drug-likeness panel (6)",
    axis="ligand", dim=6,
    short_desc="QED + Lipinski violations + Veber/Ghose flags + SA + fsp3.",
    long_desc=("QED (Bickerton 2012), Lipinski rule-of-5 violation count, "
               "Veber and Ghose drug-likeness flags, synthetic-accessibility "
               "score proxy (from BertzCT), and fraction-sp3. Strong signal "
               "for ADMET-style filtering."),
    requires=["rdkit"], cost="fast",
    compute=_drug_likeness if _RDKIT_OK else None,
    integrated=_RDKIT_OK,
))

register(FeaturizerSpec(
    id="ligand_rdkit_full", label=f"RDKit full descriptor panel ({_FULL_DIM})",
    axis="ligand", dim=_FULL_DIM,
    short_desc=f"All {_FULL_DIM} RDKit Descriptors. Topological + electronic + thermodynamic.",
    long_desc=("Every descriptor in RDKit's ``Descriptors.descList`` — "
               "spans Lipinski / Crippen / topological (BertzCT, Wiener), "
               "VSA (Mol+Slogp/SMR/PEOE-VSA), MQN counts, EState, RDF, "
               "and 100+ more. Best paired with gradient boosting which "
               "handles the redundancy + scale variance well."),
    requires=["rdkit"], cost="moderate",
    compute=_full_descriptors if _RDKIT_OK else None,
    integrated=_RDKIT_OK and _FULL_DIM > 0,
))


# Expose names so downstream code can label feature axes.
LIPINSKI_NAMES = _LIPINSKI_NAMES
DRUG_LIKENESS_NAMES = _SA_NAMES
FULL_DESCRIPTOR_NAMES = _FULL_NAMES
