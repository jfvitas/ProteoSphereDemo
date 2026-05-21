"""Thermodynamic + entropy features for proteins and ligands.

Captures the entropy contributions that pure sequence/graph encoders
typically miss. These are concatenation features that any of the
templates can consume via a small linear projection layer.

Ligand-side (computed via RDKit):
    n_rotatable_bonds        — conformational entropy proxy. Each rotatable
                               bond contributes ~0.3–0.6 kcal/mol of TΔS
                               on binding (Mammen 1998). Strong drug-likeness
                               signal.
    tpsa                     — topological polar surface area (Å²). Proxy
                               for desolvation cost; high TPSA → more polar
                               surface to dehydrate when buried.
    logp                     — octanol/water partition. Drives hydrophobic
                               effect; ΔG_hydrophobic ≈ -0.05 kcal/mol · Å²
                               of buried nonpolar surface.
    mw                       — molecular weight; combines with logp into
                               Lipinski-style drug-likeness signals.
    n_h_donors / n_h_acc     — counts of explicit H-bond donors/acceptors.
                               Each ~1.5–3 kcal/mol enthalpic + entropic
                               cost on burial.
    fraction_sp3            — saturated-carbon fraction. High fsp3 → 3D
                               shape, lower flatness; correlates with
                               ligand efficiency.
    qed                      — Bickerton et al. quantitative drug-likeness.
                               Bundles many of the above.

Protein-side (sequence-derived; no MSA needed):
    karplus_flexibility_mean — mean of the Karplus-Schulz B-factor
                               flexibility scale across the sequence.
                               Higher = more conformationally flexible
                               (more entropy cost on rigid binding).
    karplus_flexibility_max  — peak local flexibility — proxy for the
                               "induced fit" cost of the most flexible region.
    hydropathy_mean          — Kyte-Doolittle mean. Hydrophobic proteins
                               bury more nonpolar surface on folding +
                               binding.
    pi_iep_estimate          — isoelectric-point estimate from residue
                               counts. Affects salt-bridge contributions.
    disorder_estimate        — fraction of residues from
                               disorder-promoting alphabet (TOP-IDP scale
                               by Campen et al. 2008). Disorder costs
                               entropy on binding-induced folding.

All features are normalised to roughly comparable scales so the
downstream MLP doesn't have to learn the variance ratios.
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Ligand features ─────────────────────────────────────────────────────

LIGAND_FEATURE_NAMES = (
    "n_rotatable_bonds",
    "tpsa",
    "logp",
    "mw",
    "n_h_donors",
    "n_h_acc",
    "fraction_sp3",
    "qed",
)
LIGAND_FEATURE_DIM = len(LIGAND_FEATURE_NAMES)


def compute_ligand_thermo_features(smiles: str) -> list[float] | None:
    """Returns an 8-vector of normalised features, or None on parse failure."""
    try:
        from rdkit import Chem
        from rdkit.Chem import Descriptors, Lipinski, QED, rdMolDescriptors
    except ImportError:
        return None
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        n_rot = Lipinski.NumRotatableBonds(mol)
        tpsa  = Descriptors.TPSA(mol)
        logp  = Descriptors.MolLogP(mol)
        mw    = Descriptors.MolWt(mol)
        n_hd  = Lipinski.NumHDonors(mol)
        n_ha  = Lipinski.NumHAcceptors(mol)
        fsp3  = rdMolDescriptors.CalcFractionCSP3(mol)
        qed   = QED.qed(mol)
    except Exception:
        return None
    # Normalisation — chosen so each component is roughly in [-2, 2]
    # for typical drug-like molecules.
    return [
        n_rot / 5.0,             # typical 0–10
        tpsa  / 100.0,           # typical 20–140
        logp  / 4.0,             # typical -1 to 5
        (mw - 350) / 200.0,      # typical 200–600
        n_hd  / 3.0,             # 0–5 most drugs
        n_ha  / 5.0,             # 0–10 most drugs
        fsp3,                    # already in [0, 1]
        qed,                     # already in [0, 1]
    ]


# ── Protein features ───────────────────────────────────────────────────

# Karplus-Schulz B-factor flexibility scale (1985) — empirical flexibility
# values derived from PDB B-factors. Higher = more flexible residue.
_KARPLUS_FLEXIBILITY = {
    "A": 0.984, "R": 1.008, "N": 1.048, "D": 1.068, "C": 0.906,
    "E": 1.094, "Q": 1.037, "G": 1.031, "H": 0.950, "I": 0.927,
    "L": 0.935, "K": 1.102, "M": 0.952, "F": 0.915, "P": 1.049,
    "S": 1.046, "T": 0.997, "W": 0.904, "Y": 0.929, "V": 0.931,
}

# Kyte-Doolittle hydropathy (1982)
_KYTE_DOOLITTLE = {
    "A":  1.8, "R": -4.5, "N": -3.5, "D": -3.5, "C":  2.5,
    "E": -3.5, "Q": -3.5, "G": -0.4, "H": -3.2, "I":  4.5,
    "L":  3.8, "K": -3.9, "M":  1.9, "F":  2.8, "P": -1.6,
    "S": -0.8, "T": -0.7, "W": -0.9, "Y": -1.3, "V":  4.2,
}

# Lehninger pKa values for ionisable side chains + termini (for pI estimate)
_PKA_SIDE = {"D": 3.65, "E": 4.25, "C": 8.33, "Y": 10.07,
             "H": 6.0,  "K": 10.5, "R": 12.4}
_PKA_NTERM = 9.69
_PKA_CTERM = 2.34

# Campen TOP-IDP scale: residues most associated with intrinsic disorder.
# Coarse-grain version: residues with TOP-IDP > 0.1 are "disorder-promoting".
_DISORDER_PROMOTING = set("PQGSAEK")

PROTEIN_FEATURE_NAMES = (
    "karplus_flexibility_mean",
    "karplus_flexibility_max",
    "hydropathy_mean",
    "pi_iep_estimate",
    "disorder_estimate",
    "seq_length_norm",
)
PROTEIN_FEATURE_DIM = len(PROTEIN_FEATURE_NAMES)


def _windowed_max(values: list[float], window: int = 9) -> float:
    """Highest mean over a sliding window of ``window`` residues — proxy
    for the most-flexible local region rather than a single residue spike.
    """
    if not values:
        return 0.0
    if len(values) <= window:
        return sum(values) / len(values)
    best = 0.0
    cur = sum(values[:window])
    best = cur / window
    for i in range(window, len(values)):
        cur += values[i] - values[i - window]
        best = max(best, cur / window)
    return best


def _estimate_pi(seq: str) -> float:
    """Crude bisection-based pI estimate. Good to ~0.2 pH units, which is
    plenty for a feature input."""
    counts = {}
    for aa in seq:
        counts[aa] = counts.get(aa, 0) + 1
    def net_charge(ph: float) -> float:
        pos = 1.0 / (1 + 10 ** (ph - _PKA_NTERM))
        for aa, pka in (("K", _PKA_SIDE["K"]), ("R", _PKA_SIDE["R"]), ("H", _PKA_SIDE["H"])):
            n = counts.get(aa, 0)
            pos += n / (1 + 10 ** (ph - pka))
        neg = 1.0 / (1 + 10 ** (_PKA_CTERM - ph))
        for aa, pka in (("D", _PKA_SIDE["D"]), ("E", _PKA_SIDE["E"]),
                        ("C", _PKA_SIDE["C"]), ("Y", _PKA_SIDE["Y"])):
            n = counts.get(aa, 0)
            neg += n / (1 + 10 ** (pka - ph))
        return pos - neg
    lo, hi = 0.0, 14.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if net_charge(mid) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def compute_protein_thermo_features(sequence: str) -> list[float] | None:
    """Returns a 6-vector of normalised features."""
    if not sequence:
        return None
    seq = sequence.upper()
    n = len(seq)
    flex_values = [_KARPLUS_FLEXIBILITY.get(aa, 1.0) for aa in seq]
    hyd_values  = [_KYTE_DOOLITTLE.get(aa, 0.0)    for aa in seq]
    flex_mean = sum(flex_values) / n
    flex_max  = _windowed_max(flex_values, window=9)
    hyd_mean  = sum(hyd_values) / n
    pi        = _estimate_pi(seq)
    disorder  = sum(1 for aa in seq if aa in _DISORDER_PROMOTING) / n
    seq_norm  = min(1.0, n / 1000.0)
    # Normalise to roughly [-2, 2]
    return [
        (flex_mean - 1.0) * 10.0,         # mean is ~1.0 across residues
        (flex_max  - 1.0) * 10.0,
        hyd_mean / 2.0,                    # range ~[-2, 2] -> ~[-1, 1]
        (pi - 7.0) / 3.0,                  # centered on neutral
        (disorder - 0.4) * 5.0,            # typical 0.3–0.5
        seq_norm,
    ]


# ── Joint convenience ──────────────────────────────────────────────────

def thermo_feature_dim() -> int:
    """Combined dimension (ligand + protein) for downstream embedders."""
    return LIGAND_FEATURE_DIM + PROTEIN_FEATURE_DIM


def joint_thermo_features(sequence: str, smiles: str) -> list[float] | None:
    """Returns (PROTEIN_FEATURE_DIM + LIGAND_FEATURE_DIM,) or None if
    either side fails to parse."""
    lig = compute_ligand_thermo_features(smiles)
    prot = compute_protein_thermo_features(sequence)
    if lig is None or prot is None:
        return None
    return prot + lig
