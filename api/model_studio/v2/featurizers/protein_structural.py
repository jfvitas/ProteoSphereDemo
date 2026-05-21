"""Sequence-derived structural / biophysical featurizers.

These compute purely from the AA sequence — no MSA, no PDB. Faster than
ESM and complementary in signal:

    protein_thermo_6         Karplus flexibility + hydropathy + pI + disorder
                             (the same 6-vector used by thermo_features).
    protein_aa_composition_20 20-d amino acid frequency vector.
    protein_dipeptide_400    400-d dipeptide frequency vector. Captures
                             local-order biases that the AA-composition
                             alone misses.
    protein_secondary_structure_3
                             3-d helix/sheet/coil propensity (sequence-only
                             approximation: per-residue Chou-Fasman scores
                             averaged + clipped).
    protein_pseudo_sasa      4-d hydrophobic / polar / aromatic surface
                             proportions used as a SASA proxy.

All vectors are stable + cheap (microseconds per record).
"""

from __future__ import annotations

import numpy as np

from . import register, FeaturizerSpec
from ..thermodynamic_features import (
    compute_protein_thermo_features, PROTEIN_FEATURE_NAMES,
)


# 20 standard amino acids
_AA = "ACDEFGHIKLMNPQRSTVWY"
_AA_INDEX = {aa: i for i, aa in enumerate(_AA)}

# Chou-Fasman α-helix, β-sheet, coil propensities
_CF_HELIX = {
    "A": 1.42, "R": 0.98, "N": 0.67, "D": 1.01, "C": 0.70,
    "E": 1.39, "Q": 1.11, "G": 0.57, "H": 1.00, "I": 1.08,
    "L": 1.41, "K": 1.16, "M": 1.45, "F": 1.13, "P": 0.57,
    "S": 0.77, "T": 0.83, "W": 1.08, "Y": 0.69, "V": 1.06,
}
_CF_SHEET = {
    "A": 0.83, "R": 0.93, "N": 0.89, "D": 0.54, "C": 1.19,
    "E": 0.37, "Q": 1.10, "G": 0.75, "H": 0.87, "I": 1.60,
    "L": 1.30, "K": 0.74, "M": 1.05, "F": 1.38, "P": 0.55,
    "S": 0.75, "T": 1.19, "W": 1.37, "Y": 1.47, "V": 1.70,
}
_CF_COIL = {
    aa: 3.0 - _CF_HELIX[aa] - _CF_SHEET[aa] for aa in _AA
}


# Surface-burial proxies (Kyte-Doolittle hydropathy mean already
# computed elsewhere; here we count specific functional groups)
_HYDROPHOBIC = set("AILMFPWV")
_POLAR       = set("NQSTY")
_AROMATIC    = set("FWY")
_CHARGED     = set("DEKRH")


def _thermo_compute(records) -> np.ndarray:
    out = np.zeros((len(records), 6), dtype=np.float32)
    for i, r in enumerate(records):
        v = compute_protein_thermo_features(getattr(r, "sequence", "") or "")
        if v is None:
            continue
        out[i] = v
    return out


def _aa_composition(records) -> np.ndarray:
    out = np.zeros((len(records), 20), dtype=np.float32)
    for i, r in enumerate(records):
        seq = (getattr(r, "sequence", "") or "").upper()
        if not seq:
            continue
        for aa in seq:
            j = _AA_INDEX.get(aa)
            if j is not None:
                out[i, j] += 1.0
        out[i] /= max(len(seq), 1)
    return out


def _dipeptide(records) -> np.ndarray:
    out = np.zeros((len(records), 400), dtype=np.float32)
    for i, r in enumerate(records):
        seq = (getattr(r, "sequence", "") or "").upper()
        if len(seq) < 2:
            continue
        n_di = 0
        for a, b in zip(seq, seq[1:]):
            ia = _AA_INDEX.get(a); ib = _AA_INDEX.get(b)
            if ia is None or ib is None:
                continue
            out[i, ia * 20 + ib] += 1.0
            n_di += 1
        if n_di:
            out[i] /= n_di
    return out


def _secondary_struct(records) -> np.ndarray:
    out = np.zeros((len(records), 3), dtype=np.float32)
    for i, r in enumerate(records):
        seq = (getattr(r, "sequence", "") or "").upper()
        if not seq:
            continue
        h = sum(_CF_HELIX.get(a, 1.0) for a in seq) / len(seq)
        s = sum(_CF_SHEET.get(a, 1.0) for a in seq) / len(seq)
        c = sum(_CF_COIL.get(a, 1.0)  for a in seq) / len(seq)
        # Clip + normalise
        total = max(h + s + c, 1e-6)
        out[i, 0] = h / total
        out[i, 1] = s / total
        out[i, 2] = c / total
    return out


def _pseudo_sasa(records) -> np.ndarray:
    """Composition-based proxy for buried/exposed surface area.

    A real SASA needs a 3D structure. As a proxy we use the four
    functional-group proportions (hydrophobic / polar / aromatic /
    charged) which strongly correlate with the buried-vs-exposed
    pattern in folded proteins.
    """
    out = np.zeros((len(records), 4), dtype=np.float32)
    for i, r in enumerate(records):
        seq = (getattr(r, "sequence", "") or "").upper()
        if not seq:
            continue
        n = len(seq)
        out[i, 0] = sum(1 for a in seq if a in _HYDROPHOBIC) / n
        out[i, 1] = sum(1 for a in seq if a in _POLAR)       / n
        out[i, 2] = sum(1 for a in seq if a in _AROMATIC)    / n
        out[i, 3] = sum(1 for a in seq if a in _CHARGED)     / n
    return out


# ── Registration ───────────────────────────────────────────────────────

register(FeaturizerSpec(
    id="protein_thermo_6", label="Thermo proxies (6)",
    axis="protein", dim=6,
    short_desc="Karplus flexibility, hydropathy, pI, disorder, length.",
    long_desc=("Sequence-derived 6-vector: Karplus-Schulz flexibility mean "
               "+ windowed-max, Kyte-Doolittle hydropathy mean, pI estimate, "
               "TOP-IDP disorder fraction, normalised length."),
    requires=[], cost="trivial",
    compute=_thermo_compute,
    integrated=True,
))

register(FeaturizerSpec(
    id="protein_aa_composition_20", label="Amino-acid composition (20)",
    axis="protein", dim=20,
    short_desc="Frequency of each standard amino acid.",
    long_desc=("20-d amino acid frequency vector. Cheap baseline that "
               "captures the gross compositional bias (e.g. high-cysteine "
               "secreted proteins, glycine-rich loops)."),
    requires=[], cost="trivial",
    compute=_aa_composition,
    integrated=True,
))

register(FeaturizerSpec(
    id="protein_dipeptide_400", label="Dipeptide composition (400)",
    axis="protein", dim=400,
    short_desc="Frequency of each 2-gram amino acid pair.",
    long_desc=("400-d dipeptide frequency vector. Captures sequential "
               "biases the AA composition alone misses — local-order "
               "preferences that approximate short-range secondary "
               "structure signal."),
    requires=[], cost="fast",
    compute=_dipeptide,
    integrated=True,
))

register(FeaturizerSpec(
    id="protein_secondary_structure_3", label="Secondary structure propensity (3)",
    axis="protein", dim=3,
    short_desc="Chou-Fasman helix / sheet / coil propensity averages.",
    long_desc=("Sequence-mean Chou-Fasman propensities for α-helix, "
               "β-sheet, and coil. Sequence-only approximation of the "
               "DSSP secondary-structure distribution — close enough for "
               "feature engineering, much cheaper than PSIPRED."),
    requires=[], cost="trivial",
    compute=_secondary_struct,
    integrated=True,
))

register(FeaturizerSpec(
    id="protein_pseudo_sasa_4", label="Surface-residue proxy (4)",
    axis="protein", dim=4,
    short_desc="Hydrophobic / polar / aromatic / charged fractions.",
    long_desc=("4-d functional-group proportions as a SASA proxy. A real "
               "SASA needs a 3D structure (DSSP / FreeSASA); this is the "
               "best you can do without one."),
    requires=[], cost="trivial",
    compute=_pseudo_sasa,
    integrated=True,
))
