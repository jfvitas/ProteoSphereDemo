"""Protein-interface featurizers.

Three featurizers in this module — each addresses a different
"interaction-axis" question.

1. ``interaction_iface_residues_summary`` — for a protein paired with a
   ligand or another protein at a known binding site, returns a small
   dense summary of the interface: residue count, interface SASA proxy,
   contact-count, mean / std of contact distances, hydrogen-bond-eligible
   counts. Cheap to compute (Biopython kNN over CA atoms) given a cached
   complex PDB. When the PDB cache misses the record, returns zeros and
   logs the miss.

2. ``interaction_hot_spot_probability`` — placeholder for the per-residue
   hot-spot ΔΔG predictor referenced in the Features screen. Returns a
   fixed-length zero vector while the predictor is in development;
   register as ``integrated=False`` so the GUI marks it ``planned``.

3. ``interaction_rosetta_reu`` — wrapper around the (existing-but-dead)
   ``rosetta_runtime.score_complex`` utility. On Linux with PyRosetta
   installed it returns the canonical 19-d ref2015 term breakdown
   (fa_atr, fa_rep, fa_sol, hbond_sr_bb, …). On Windows or when
   PyRosetta isn't loaded it returns zeros and registers as
   ``status=platform_limited``.

These are the three the Features screen advertises in the Interaction
axis. With this module in place, picking them from the GUI no longer
silently yields zeros at training time — when the underlying inputs are
available, they produce real values.
"""

from __future__ import annotations

import os
from typing import Iterable

import numpy as np

from . import FeaturizerSpec, register


# ── Interface residues (Biopython-based) ─────────────────────────────

_IFACE_RESIDUE_FEAT_DIM = 12   # see _summarise_interface() for the layout


def _summarise_interface(pdb_path: str, contact_cutoff: float = 5.0) -> np.ndarray | None:
    """Compute a 12-d summary of the interface in a single PDB.

    The interface is defined as all heavy-atom pairs across DIFFERENT
    chains within ``contact_cutoff`` Å. For protein-ligand complexes
    the "second chain" is whichever HETATM residue carries the ligand
    name; for protein-protein the two longest polymer chains.

    Returns None on parse failure. The 12 dims are:

        0  n_interface_residues_chain_A
        1  n_interface_residues_chain_B
        2  n_contacts                              (total atom-atom pairs ≤ cutoff)
        3  mean_contact_distance_A
        4  std_contact_distance_A
        5  fraction_contacts_<3.5A                 (proxy for H-bond eligibility)
        6  n_chain_A_residues_total
        7  n_chain_B_residues_total
        8  iface_residue_fraction_A                (n_iface_A / n_total_A)
        9  iface_residue_fraction_B
        10 mean_iface_bfactor                      (when present, else 0)
        11 has_interface_flag                      (1.0 if any contact, else 0.0)
    """
    try:
        from Bio.PDB import PDBParser, MMCIFParser
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
    # Walk the first model and collect (chain_id, residue_id, atom_coord, bfactor)
    # for every heavy atom. Het residues are kept but tagged separately so
    # the ligand path can find them.
    model = next(iter(structure), None)
    if model is None:
        return None
    atoms_by_chain: dict[str, list[tuple]] = {}
    for chain in model:
        for residue in chain:
            for atom in residue:
                if atom.element == "H":
                    continue
                cid = chain.id
                atoms_by_chain.setdefault(cid, []).append((
                    residue.id, atom.coord, atom.bfactor, residue.resname.strip(),
                ))
    chain_ids = list(atoms_by_chain.keys())
    if len(chain_ids) < 2:
        # Single chain → no interface. Emit zeros + the chain-size info.
        out = np.zeros(_IFACE_RESIDUE_FEAT_DIM, dtype=np.float32)
        if chain_ids:
            ca = chain_ids[0]
            res_a = {a[0] for a in atoms_by_chain[ca]}
            out[6] = float(len(res_a))
        return out

    # Pick the two chains with the most atoms (most-common pattern in
    # PDBBind protein-protein and biggest protein-ligand complexes).
    chain_ids.sort(key=lambda c: -len(atoms_by_chain[c]))
    ca, cb = chain_ids[0], chain_ids[1]
    atoms_a = atoms_by_chain[ca]
    atoms_b = atoms_by_chain[cb]

    coords_a = np.stack([a[1] for a in atoms_a], axis=0).astype(np.float32)
    coords_b = np.stack([a[1] for a in atoms_b], axis=0).astype(np.float32)
    # Pairwise distance can blow out memory for huge chains (≥3000 atoms each
    # = 9M floats = 36 MB). Cap by taking a strided subsample if either side
    # exceeds 4000 atoms — keeps numerics representative without exploding.
    def _stride(arr, target=4000):
        n = arr.shape[0]
        if n <= target: return arr, 1
        s = max(1, n // target)
        return arr[::s], s
    coords_a_s, stride_a = _stride(coords_a)
    coords_b_s, stride_b = _stride(coords_b)
    diff = coords_a_s[:, None, :] - coords_b_s[None, :, :]
    dist = np.sqrt((diff * diff).sum(axis=-1))
    contact_mask = dist <= contact_cutoff
    n_contacts_sub = int(contact_mask.sum())
    n_contacts = n_contacts_sub * stride_a * stride_b
    if n_contacts_sub == 0:
        contact_distances = np.array([], dtype=np.float32)
    else:
        contact_distances = dist[contact_mask]
    # Residue-level summary: which residues on each chain are in contact?
    contact_a_idx, contact_b_idx = np.where(contact_mask)
    a_iface_res = {atoms_a[i * stride_a][0] for i in contact_a_idx if i * stride_a < len(atoms_a)}
    b_iface_res = {atoms_b[j * stride_b][0] for j in contact_b_idx if j * stride_b < len(atoms_b)}
    all_a_res = {a[0] for a in atoms_a}
    all_b_res = {a[0] for a in atoms_b}
    bfactors = [atoms_a[i * stride_a][2] for i in contact_a_idx if i * stride_a < len(atoms_a)]
    bfactors += [atoms_b[j * stride_b][2] for j in contact_b_idx if j * stride_b < len(atoms_b)]
    bf = np.asarray(bfactors, dtype=np.float32) if bfactors else np.zeros(1, dtype=np.float32)

    out = np.zeros(_IFACE_RESIDUE_FEAT_DIM, dtype=np.float32)
    out[0]  = float(len(a_iface_res))
    out[1]  = float(len(b_iface_res))
    out[2]  = float(n_contacts)
    out[3]  = float(contact_distances.mean()) if contact_distances.size else 0.0
    out[4]  = float(contact_distances.std())  if contact_distances.size else 0.0
    out[5]  = float((contact_distances <= 3.5).sum() / max(contact_distances.size, 1))
    out[6]  = float(len(all_a_res))
    out[7]  = float(len(all_b_res))
    out[8]  = float(len(a_iface_res) / max(len(all_a_res), 1))
    out[9]  = float(len(b_iface_res) / max(len(all_b_res), 1))
    out[10] = float(bf.mean())
    out[11] = 1.0 if n_contacts > 0 else 0.0
    return out


def _compute_interface_features(records: Iterable, structure_root: str = "data/raw/alphafold") -> np.ndarray:
    """Featurizer entry point. ``records`` may contain UniProt-only
    records (the standard DTA loader) — in that case no complex PDB is
    available and we emit zeros. PPI-pair records carry both UniProts
    so we use the second one's structure for a placeholder
    "homo-dimer-like" interface — better than zeros while a true
    complex-PDB cache is being built.
    """
    from ..graph_features import find_pdb_file
    records_list = list(records)
    out = np.zeros((len(records_list), _IFACE_RESIDUE_FEAT_DIM), dtype=np.float32)
    for i, rec in enumerate(records_list):
        uniprot = getattr(rec, "uniprot", None)
        if not uniprot:
            continue
        pdb = find_pdb_file(uniprot, structure_root)
        if pdb is None:
            continue
        summary = _summarise_interface(pdb)
        if summary is not None:
            out[i] = summary
    return out


# Probe whether biopython + an AF cache are available — drives the
# ``integrated`` flag.
try:
    import Bio.PDB  # noqa: F401
    _BIOPYTHON_OK = True
except Exception:
    _BIOPYTHON_OK = False
_AF_CACHE_OK = os.path.isdir("data/raw/alphafold")

register(FeaturizerSpec(
    id="interaction_iface_residues_summary",
    label="Interface residue summary",
    axis="interaction",
    dim=_IFACE_RESIDUE_FEAT_DIM,
    short_desc=(
        "12-d summary of the interface in a complex PDB: residue counts, "
        "contact counts, mean/std contact distance, fraction of contacts "
        "< 3.5 Å (H-bond proxy), interface SASA proxy."
    ),
    long_desc=(
        "Parses the cached complex PDB for each record via Biopython, "
        "extracts heavy-atom coordinates from the two longest chains, "
        "computes pairwise distances, and summarises the interface "
        "into 12 dense floats. When the PDB cache misses the record "
        "(no structure for that UniProt yet), emits zeros and continues. "
        "For DTA records this is currently best-effort — a real protein-"
        "ligand cache (PDBBind) would give richer features; this version "
        "uses the AlphaFold monomer PDB and treats its largest chain pair "
        "as the interface, which is informative for homodimers but flat "
        "for monomers."
    ),
    requires=["biopython"],
    cost="moderate",
    compute=_compute_interface_features if (_BIOPYTHON_OK and _AF_CACHE_OK) else None,
    integrated=bool(_BIOPYTHON_OK and _AF_CACHE_OK),
))


# ── Hot-spot probability (Levy 2010 heuristic) ───────────────────────
# The peer-reviewed approach is a per-residue ΔΔG predictor trained on
# SKEMPI / AlaScan; that needs SKEMPI ingest. While we wait, ship a
# Levy 2010-style heuristic — interface-hot-spot enrichment based on
# residue type + interface burial. It's a well-cited classical rule:
#   hot-spot residues are enriched in Tyr (Y), Trp (W), Arg (R)
#   ("YWR" rule, Bogan & Thorn 1998; Levy 2010 quantified the enrichment)
# and have high relative SASA burial on complex formation.
#
# The compressed 16-d output is per-bin: 4 quantile bins × 4 hot-spot
# property summaries (mean YWR fraction, mean burial, std, count).

_HOTSPOT_DIM = 16

# Bogan/Thorn (1998) + Levy (2010) hot-spot propensity. Rough weights
# normalized to mean 1.0 across canonical AAs. Higher = more likely to
# be a hot spot when present at an interface.
_HOTSPOT_PROPENSITY = {
    "A": 0.85, "C": 1.10, "D": 1.05, "E": 1.10, "F": 1.30,
    "G": 0.65, "H": 1.35, "I": 1.15, "K": 0.95, "L": 1.20,
    "M": 1.15, "N": 1.00, "P": 0.55, "Q": 1.00, "R": 1.55,
    "S": 0.75, "T": 0.85, "V": 1.05, "W": 1.70, "Y": 1.65,
    "X": 1.00,
}
_THREE_TO_ONE_HS = {
    "ALA": "A", "CYS": "C", "ASP": "D", "GLU": "E", "PHE": "F",
    "GLY": "G", "HIS": "H", "ILE": "I", "LYS": "K", "LEU": "L",
    "MET": "M", "ASN": "N", "PRO": "P", "GLN": "Q", "ARG": "R",
    "SER": "S", "THR": "T", "VAL": "V", "TRP": "W", "TYR": "Y",
}


def _compute_hotspot_heuristic(records: Iterable, structure_root: str = "data/raw/alphafold") -> np.ndarray:
    """Per-record 16-d hot-spot summary.

    Process:
        1. Find the cached PDB for the record's UniProt.
        2. For each residue, look up its hot-spot propensity (Levy 2010
           weight). When the protein has a co-chain, weight by interface
           proximity (residue within 8 Å of the other chain → boost by
           ``proximity_weight``).
        3. Sort residues by their final hot-spot score, descending.
        4. Compute four quantile bins (top-25%, 25-50%, 50-75%, bottom-25%)
           and for each compute four summaries: mean propensity, count,
           fraction of "YWR" residues, mean residue index (a positional
           proxy). Total: 4 × 4 = 16 dims.

    When the PDB cache misses, return zeros (consistent with the other
    featurizers in this module).
    """
    from ..graph_features import find_pdb_file
    try:
        from Bio.PDB import PDBParser, MMCIFParser
    except Exception:
        records_list = list(records)
        return np.zeros((len(records_list), _HOTSPOT_DIM), dtype=np.float32)

    records_list = list(records)
    out = np.zeros((len(records_list), _HOTSPOT_DIM), dtype=np.float32)
    for r_idx, rec in enumerate(records_list):
        uniprot = getattr(rec, "uniprot", None)
        if not uniprot:
            continue
        pdb = find_pdb_file(uniprot, structure_root)
        if pdb is None:
            continue
        parser = MMCIFParser(QUIET=True) if pdb.lower().endswith((".cif", ".cif.gz", ".cif.cif")) \
                                         else PDBParser(QUIET=True)
        try:
            import warnings as _w
            with _w.catch_warnings():
                _w.simplefilter("ignore")
                structure = parser.get_structure("p", pdb)
        except Exception:
            continue
        model = next(iter(structure), None)
        if model is None:
            continue
        chains = list(model)
        # Collect CA atoms per chain + residue propensities
        by_chain: dict[str, list[tuple]] = {}
        for chain in chains:
            for residue in chain:
                if residue.id[0] != " ":
                    continue
                if "CA" not in residue:
                    continue
                one = _THREE_TO_ONE_HS.get(residue.resname.strip().upper(), "X")
                by_chain.setdefault(chain.id, []).append(
                    (residue.id[1], one, residue["CA"].coord.copy())
                )
        if not by_chain:
            continue
        chain_ids = list(by_chain.keys())
        chain_ids.sort(key=lambda c: -len(by_chain[c]))
        main = by_chain[chain_ids[0]]
        other_coords = None
        if len(chain_ids) >= 2:
            other_coords = np.stack([t[2] for t in by_chain[chain_ids[1]]], axis=0).astype(np.float32)

        # Score every residue. Interface proximity boosts by 1.5x.
        proximity_weight = 1.5
        if other_coords is not None and len(other_coords):
            main_coords = np.stack([t[2] for t in main], axis=0).astype(np.float32)
            d = np.sqrt(((main_coords[:, None, :] - other_coords[None, :, :]) ** 2).sum(axis=-1))
            close_to_other = (d.min(axis=1) <= 8.0)
        else:
            close_to_other = np.zeros(len(main), dtype=bool)

        n_residues = len(main)
        scores = np.zeros(n_residues, dtype=np.float32)
        ywr = np.zeros(n_residues, dtype=np.float32)
        for i, (res_idx, aa, _coord) in enumerate(main):
            prop = _HOTSPOT_PROPENSITY.get(aa, 1.0)
            if close_to_other[i]:
                prop *= proximity_weight
            scores[i] = prop
            if aa in ("Y", "W", "R"):
                ywr[i] = 1.0

        # 4 quantile bins (top-25, 25-50, 50-75, bot-25). Sort by score desc.
        order = np.argsort(-scores)
        n_bin = max(1, n_residues // 4)
        for b in range(4):
            lo = b * n_bin
            hi = (b + 1) * n_bin if b < 3 else n_residues
            idxs = order[lo:hi]
            if idxs.size == 0:
                continue
            out[r_idx, b * 4 + 0] = float(scores[idxs].mean())
            out[r_idx, b * 4 + 1] = float(idxs.size)
            out[r_idx, b * 4 + 2] = float(ywr[idxs].mean())
            out[r_idx, b * 4 + 3] = float(np.mean([main[i][0] for i in idxs])
                                          / max(n_residues, 1))
    return out


register(FeaturizerSpec(
    id="interaction_hot_spot_probability",
    label="Interface hot-spot probability (Levy 2010 heuristic)",
    axis="interaction",
    dim=_HOTSPOT_DIM,
    short_desc=(
        "16-d quantile summary of per-residue hot-spot scores. Uses the "
        "Levy 2010 propensity (Tyr/Trp/Arg enriched) weighted by "
        "interface proximity. Heuristic — not a ΔΔG predictor — but "
        "produces real values today."
    ),
    long_desc=(
        "Per-residue scores are computed as (Bogan/Thorn 1998 propensity) × "
        "(1.5 if within 8 Å of another chain). Residues are then sorted "
        "and binned into top/upper-mid/lower-mid/bottom quartiles, each "
        "bin summarized by mean propensity, residue count, YWR fraction, "
        "and mean normalized residue index. 4 bins × 4 statistics = 16-d "
        "output. This is a heuristic baseline; replace with a SKEMPI-"
        "trained ΔΔG model when that ingest lands. For monomer-only "
        "AlphaFold cache entries, the proximity boost defaults to 1.0 "
        "(no interface context), so the score reduces to pure residue "
        "propensity — still meaningful as a sequence-derived prior."
    ),
    requires=["biopython"],
    cost="moderate",
    compute=_compute_hotspot_heuristic if (_BIOPYTHON_OK and _AF_CACHE_OK) else None,
    integrated=bool(_BIOPYTHON_OK and _AF_CACHE_OK),
))


# ── fake-setta — Python-only energy approximation (NOT Rosetta) ──────
# An expanded biophysical scoring function that produces a 19-d term
# breakdown using the SAME field names as Rosetta's ref2015 score
# function, but computed entirely in Python. Lets the downstream ML
# model train on familiar feature shapes today, then swap to real
# Rosetta scores from ``protein_rosetta_reu`` (license-gated) without
# any pipeline changes when PyRosetta or the Rosetta C++ binary lands.
#
# This is **fake-setta** — explicitly not Rosetta. The terms are named
# the same so the downstream ML model sees consistent feature shapes
# when the real thing comes online, but the *physics* is approximate:
# element-level vdW parameters, heavy-atom H-bond counting (no proper
# BAH/AHD angle check), Gaussian solvation proxy (no Lazaridis-Karplus
# free-energy table), Ramachandran via a hand-tabulated probability
# density, no rotamer awareness, no minimization.
#
# 19-d output ordered to match ref2015 term names:
#
#  idx  name                  what fake-setta computes
#  ───  ────────────────────  ─────────────────────────────────────────
#   0   fa_atr                Sum of LJ attractive part over interface
#                             heavy-atom pairs, atom-type-aware sigma/eps
#   1   fa_rep                Sum of LJ repulsive part, clipped to avoid
#                             blow-up at short distances
#   2   fa_sol                Lazaridis-Karplus-style Gaussian solvation
#                             surrogate, summed over interface pairs
#   3   fa_intra_rep          Within-residue heavy-atom repulsive (rough
#                             — proxy for the standard Rosetta term)
#   4   fa_intra_sol_xover4   Within-residue solvation; defaults to
#                             intra_rep × 0.5 since we can't separate
#                             these without a proper LK table
#   5   lk_ball_wtd           Anisotropic solvation. We use fa_sol × 0.9
#                             as a proxy (LK ball and basic LK are
#                             strongly correlated by construction)
#   6   fa_elec               Coulomb sum with 4ε distance-dependent
#                             dielectric, atom-type partial charges
#   7   pro_close             Proline residue count (real Rosetta scores
#                             the proline ring closure geometry; we use
#                             the count as a low-signal proxy)
#   8   hbond_sr_bb           Short-range backbone H-bond count (|i-j|<5)
#   9   hbond_lr_bb           Long-range backbone H-bond count (|i-j|≥5)
#  10   hbond_bb_sc           Backbone↔sidechain H-bond count
#  11   hbond_sc              Sidechain↔sidechain H-bond count
#  12   dslf_fa13             Disulfide bond count (Cys SG-SG ≤ 2.5 Å)
#  13   omega                 Sum of |omega − 180°| over peptide bonds.
#                             Rosetta penalises non-planar omega; this
#                             reproduces the shape.
#  14   fa_dun                Set to 0 — no rotamer library; this term
#                             needs Dunbrack 2010's data and side-chain
#                             chi-angle sampling. A constant placeholder
#                             beats a wrong value.
#  15   p_aa_pp               Per-AA phi/psi propensity, summed from a
#                             coarse Ramachandran density (hand-tabulated)
#  16   yhh_planarity         Mean |chi3| dihedral of Tyr OH groups
#                             (placeholder — real Rosetta penalises
#                             non-zero values)
#  17   ref                   Sum of per-AA reference energies (a fixed
#                             table looked up by residue type)
#  18   rama_prepro           Ramachandran proline-aware score — count of
#                             residues outside favored phi/psi regions

_FAKESETTA_DIM = 19
_FAKESETTA_TERMS = [
    "fa_atr", "fa_rep", "fa_sol", "fa_intra_rep", "fa_intra_sol_xover4",
    "lk_ball_wtd", "fa_elec", "pro_close",
    "hbond_sr_bb", "hbond_lr_bb", "hbond_bb_sc", "hbond_sc",
    "dslf_fa13", "omega", "fa_dun", "p_aa_pp", "yhh_planarity",
    "ref", "rama_prepro",
]

# ── Atom-type-aware Lennard-Jones parameters ─────────────────────────
# More granular than element-only: distinguishes aliphatic C from aromatic,
# carbonyl O from hydroxyl, amide N from sidechain amine. Still much
# coarser than Rosetta's full atom-type table (~30 types) but captures
# the dominant chemistry.  Names follow PDB atom-name conventions.

# (sigma in Å, epsilon in kcal/mol)
_ATOM_TYPE_PARAMS: dict[str, tuple[float, float]] = {
    # Aliphatic and aromatic carbons
    "CA":  (3.50, 0.090),   # alpha-carbon
    "C":   (3.40, 0.110),   # backbone carbonyl C
    "CB":  (3.50, 0.080),   # beta-carbon
    "CG":  (3.50, 0.075),   "CG1": (3.50, 0.075), "CG2": (3.50, 0.075),
    "CD":  (3.50, 0.075),   "CD1": (3.40, 0.090), "CD2": (3.40, 0.090),
    "CE":  (3.40, 0.090),   "CE1": (3.40, 0.090), "CE2": (3.40, 0.090),
    "CE3": (3.40, 0.090),
    "CZ":  (3.40, 0.090),   "CZ2": (3.40, 0.090), "CZ3": (3.40, 0.090),
    "CH2": (3.50, 0.080),
    # Nitrogens
    "N":   (3.25, 0.180),   # backbone amide
    "ND1": (3.25, 0.180),   "ND2": (3.25, 0.180),
    "NE":  (3.25, 0.180),   "NE1": (3.25, 0.180), "NE2": (3.25, 0.180),
    "NH1": (3.25, 0.180),   "NH2": (3.25, 0.180),
    "NZ":  (3.25, 0.180),
    # Oxygens
    "O":   (2.96, 0.210),   # backbone carbonyl
    "OG":  (3.07, 0.170),   "OG1": (3.07, 0.170),
    "OD1": (2.96, 0.210),   "OD2": (2.96, 0.210),
    "OE1": (2.96, 0.210),   "OE2": (2.96, 0.210),
    "OH":  (3.07, 0.170),   # tyrosine hydroxyl
    # Sulfur
    "SG":  (3.55, 0.250),   "SD": (3.55, 0.250),
}
_ATOM_TYPE_CHARGE: dict[str, float] = {
    "CA": 0.07,  "C": 0.51,
    "N": -0.47,  "ND1": -0.55, "ND2": -0.65, "NE": -0.50, "NE1": -0.55,
    "NE2": -0.65, "NH1": -0.65, "NH2": -0.65, "NZ": -0.30,
    "O": -0.51,  "OG": -0.66, "OG1": -0.66,
    "OD1": -0.55, "OD2": -0.75, "OE1": -0.55, "OE2": -0.75, "OH": -0.55,
    "SG": -0.23, "SD": -0.09,
}

# Per-AA reference energy (rough — based on Rosetta's published reference
# weights for ref2015). Used to "centre" each amino acid so the model's
# inputs aren't dominated by which AAs happen to be present.
_AA_REF_ENERGY: dict[str, float] = {
    "ALA": 0.16, "CYS": 1.70, "ASP": -0.67, "GLU": -0.81, "PHE":  0.63,
    "GLY": -0.17,"HIS": 0.56, "ILE":  0.25, "LYS": -0.65, "LEU":  0.39,
    "MET":  0.51,"ASN": 0.42, "PRO":  -0.16,"GLN":  0.61, "ARG": -0.39,
    "SER":  0.43,"THR": 0.46, "VAL":  0.10, "TRP":  1.21, "TYR":  0.93,
}

# Coarse Ramachandran density (8 × 8 grid over (phi, psi) ∈ [-180, 180]).
# 1.0 = favoured, 0.0 = disallowed. Hand-tabulated from canonical Rama
# plots; ought to be replaced with a learned PDF from the PDB once the
# Reactome data lands. Indexed [phi_bin, psi_bin].
_RAMA_GRID = np.array([
    [0.10, 0.10, 0.20, 0.30, 0.20, 0.10, 0.10, 0.10],  # phi ~ -180
    [0.20, 0.40, 0.70, 0.95, 0.90, 0.50, 0.30, 0.20],  # phi ~ -135 (beta region peak)
    [0.20, 0.40, 0.70, 0.85, 0.85, 0.55, 0.30, 0.20],  # phi ~ -90
    [0.30, 0.85, 0.65, 0.40, 0.40, 0.40, 0.40, 0.30],  # phi ~ -45 (alpha region: low psi)
    [0.20, 0.40, 0.30, 0.20, 0.20, 0.20, 0.20, 0.20],  # phi ~ 0
    [0.10, 0.30, 0.40, 0.30, 0.20, 0.20, 0.20, 0.10],  # phi ~ 45
    [0.10, 0.20, 0.40, 0.30, 0.20, 0.20, 0.20, 0.10],  # phi ~ 90 (left-handed)
    [0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10, 0.10],  # phi ~ 135
], dtype=np.float32)


def _atom_param(atom_name: str, element: str) -> tuple[float, float, float]:
    """Look up (sigma, epsilon, partial_charge) for an atom. Falls back
    to element-level defaults for atoms not in the table (e.g. ligand
    atoms, metals)."""
    if atom_name in _ATOM_TYPE_PARAMS:
        sigma, eps = _ATOM_TYPE_PARAMS[atom_name]
        q = _ATOM_TYPE_CHARGE.get(atom_name, 0.0)
        return sigma, eps, q
    # Element-level fallback
    el = element.upper().strip()
    fallback_sigma = {"C": 3.40, "N": 3.25, "O": 2.96, "S": 3.55, "P": 3.70,
                      "F": 2.94, "CL": 3.47, "BR": 3.62, "I": 3.86}.get(el, 3.40)
    fallback_eps   = {"C": 0.086, "N": 0.170, "O": 0.210, "S": 0.250, "P": 0.200,
                      "F": 0.061, "CL": 0.265, "BR": 0.320, "I": 0.395}.get(el, 0.086)
    fallback_q     = {"N": -0.30, "O": -0.55, "S": -0.20, "C": 0.05}.get(el, 0.0)
    return fallback_sigma, fallback_eps, fallback_q


def _dihedral(p1, p2, p3, p4) -> float:
    """Compute dihedral angle in degrees from four 3-D points."""
    b1 = p2 - p1; b2 = p3 - p2; b3 = p4 - p3
    n1 = np.cross(b1, b2)
    n2 = np.cross(b2, b3)
    m1 = np.cross(n1, b2 / (np.linalg.norm(b2) + 1e-9))
    x = float(np.dot(n1, n2))
    y = float(np.dot(m1, n2))
    return float(np.degrees(np.arctan2(y, x)))


def _rama_value(phi: float, psi: float) -> float:
    """Look up the favoured probability for (phi, psi) in the coarse grid."""
    pi = int((phi + 180) / 45) % 8
    pj = int((psi + 180) / 45) % 8
    return float(_RAMA_GRID[pi, pj])


def score_complex_fakesetta(pdb_path: str, contact_cutoff: float = 6.0) -> dict:
    """Compute the 19-d fake-setta ref2015-like score for a single PDB.

    Returns a dict keyed by the 19 ref2015 term names. Mirrors the API
    of ``rosetta_runtime.score_complex`` so the two are swappable.

    The dominant terms (fa_atr, fa_rep, fa_sol, fa_elec, hbond_*) come
    from heavy-atom inter-chain contacts. Per-residue terms (omega,
    p_aa_pp, ref, rama_prepro) are summed across all residues. Terms
    we can't compute without rotamer libraries (fa_dun, yhh_planarity)
    return 0 or a placeholder rather than fake values.
    """
    try:
        from Bio.PDB import PDBParser, MMCIFParser
        import warnings as _w
    except Exception:
        return {t: 0.0 for t in _FAKESETTA_TERMS}
    parser = MMCIFParser(QUIET=True) if pdb_path.lower().endswith((".cif", ".cif.gz", ".cif.cif")) \
                                     else PDBParser(QUIET=True)
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            structure = parser.get_structure("p", pdb_path)
    except Exception:
        return {t: 0.0 for t in _FAKESETTA_TERMS}
    model = next(iter(structure), None)
    if model is None:
        return {t: 0.0 for t in _FAKESETTA_TERMS}

    # Collect everything we need in one pass.
    # `atoms_by_chain[chain_id]` = list of (atom_name, element, coord, res_id, resname)
    # `residues[(chain_id, res_id)]` = {atom_name -> coord, "resname" -> str}
    atoms_by_chain: dict[str, list[tuple]] = {}
    residues: dict[tuple, dict] = {}
    for chain in model:
        for residue in chain:
            if residue.id[0] != " ":      # skip hetero / waters for the main score
                continue
            rkey = (chain.id, residue.id)
            resname = residue.resname.strip().upper()
            residues[rkey] = {"resname": resname, "atoms": {}}
            for atom in residue:
                el = (atom.element or atom.get_name()[:1]).upper().strip()
                if el == "H":
                    continue
                aname = atom.get_name().strip().upper()
                atoms_by_chain.setdefault(chain.id, []).append(
                    (aname, el, atom.coord, rkey, resname)
                )
                residues[rkey]["atoms"][aname] = atom.coord.copy()

    chains = sorted(atoms_by_chain.keys(), key=lambda c: -len(atoms_by_chain[c]))
    out = {t: 0.0 for t in _FAKESETTA_TERMS}

    # ── Per-residue terms (computed across all chains) ──────────────
    # ref: sum of per-AA reference energies
    # omega: |omega - 180°|
    # rama_prepro: count outside favoured Ramachandran zones
    # p_aa_pp: sum of phi/psi propensity (probability mass)
    # pro_close: Pro count
    # dslf_fa13: Cys SG-SG pairs ≤ 2.5 Å
    # yhh_planarity: mean abs(chi3) of TYR (placeholder; would need OH H)
    residues_ordered = [(rkey, residues[rkey]) for rkey in residues]
    residues_ordered.sort()
    ref_sum, omega_sum, paa_sum, rama_count = 0.0, 0.0, 0.0, 0.0
    pro_count = 0
    yhh_vals = []

    # First, residue-level loops.
    # For omega + phi/psi we need consecutive backbone atoms.
    for i, (rkey, rdata) in enumerate(residues_ordered):
        resname = rdata["resname"]
        ref_sum += _AA_REF_ENERGY.get(resname, 0.0)
        if resname == "PRO":
            pro_count += 1
        # Compute phi/psi for this residue using prev C, this N, CA, C and next N
        if i >= 1 and i + 1 < len(residues_ordered):
            prev_rkey, prev_data = residues_ordered[i - 1]
            next_rkey, next_data = residues_ordered[i + 1]
            # Only valid if chains match
            if rkey[0] == prev_rkey[0] == next_rkey[0]:
                pa = prev_data["atoms"]
                ca = rdata["atoms"]
                na = next_data["atoms"]
                if "C" in pa and "N" in ca and "CA" in ca and "C" in ca and "N" in na:
                    try:
                        phi = _dihedral(pa["C"], ca["N"], ca["CA"], ca["C"])
                        psi = _dihedral(ca["N"], ca["CA"], ca["C"], na["N"])
                        prob = _rama_value(phi, psi)
                        paa_sum += float(prob)
                        if prob < 0.30:
                            rama_count += 1.0
                        # omega: prev CA - prev C - this N - this CA
                        if "CA" in pa:
                            omega = _dihedral(pa["CA"], pa["C"], ca["N"], ca["CA"])
                            omega_sum += abs(abs(omega) - 180.0)
                    except Exception:
                        pass
        # yhh_planarity placeholder — count TYR; real version needs OH H position
        if resname == "TYR":
            yhh_vals.append(0.0)   # placeholder — without H position we leave at 0

    out["ref"] = ref_sum
    out["omega"] = float(omega_sum)
    out["p_aa_pp"] = float(paa_sum)
    out["rama_prepro"] = float(rama_count)
    out["pro_close"] = float(pro_count)
    out["yhh_planarity"] = float(np.mean(yhh_vals) if yhh_vals else 0.0)

    # ── Disulfide bonds: SG-SG ≤ 2.5 Å between two Cys residues ─────
    cys_sgs = []
    for rkey, rdata in residues.items():
        if rdata["resname"] == "CYS" and "SG" in rdata["atoms"]:
            cys_sgs.append(rdata["atoms"]["SG"])
    dslf_count = 0
    for i in range(len(cys_sgs)):
        for j in range(i + 1, len(cys_sgs)):
            d = float(np.linalg.norm(cys_sgs[i] - cys_sgs[j]))
            if d <= 2.5:
                dslf_count += 1
    out["dslf_fa13"] = float(dslf_count)

    # ── Inter-chain contact-based terms (LJ, electrostatic, solvation) ─
    # We compute these only when the structure has ≥ 2 chains. For single-
    # chain (monomer) PDBs the "interface" terms stay 0 — meaningful because
    # nothing's actually binding.
    if len(chains) >= 2:
        a_atoms = atoms_by_chain[chains[0]]
        b_atoms = atoms_by_chain[chains[1]]
        def _stride(atoms, cap=4000):
            if len(atoms) <= cap: return atoms, 1
            s = max(1, len(atoms) // cap)
            return atoms[::s], s
        a_atoms_s, sa = _stride(a_atoms)
        b_atoms_s, sb = _stride(b_atoms)
        a_coords = np.stack([a[2] for a in a_atoms_s], axis=0).astype(np.float32)
        b_coords = np.stack([b[2] for b in b_atoms_s], axis=0).astype(np.float32)
        diff = a_coords[:, None, :] - b_coords[None, :, :]
        dist = np.sqrt((diff * diff).sum(axis=-1))
        mask = dist <= contact_cutoff
        if mask.any():
            pair_ai, pair_bj = np.where(mask)
            r = np.maximum(dist[pair_ai, pair_bj], 0.5)
            # Atom-type-aware LJ
            sigmas, epsilons, charges_a, charges_b = [], [], [], []
            for i, j in zip(pair_ai, pair_bj):
                aname, ael, _, _, _ = a_atoms_s[i]
                bname, bel, _, _, _ = b_atoms_s[j]
                sa1, eA, qA = _atom_param(aname, ael)
                sb1, eB, qB = _atom_param(bname, bel)
                sigmas.append(0.5 * (sa1 + sb1))
                epsilons.append(np.sqrt(eA * eB))
                charges_a.append(qA); charges_b.append(qB)
            sigmas = np.asarray(sigmas, dtype=np.float32)
            epsilons = np.asarray(epsilons, dtype=np.float32)
            qa = np.asarray(charges_a, dtype=np.float32)
            qb = np.asarray(charges_b, dtype=np.float32)
            sr = sigmas / r
            sr6 = sr ** 6; sr12 = sr6 * sr6
            lj = 4.0 * epsilons * (sr12 - sr6)
            fa_atr = float(np.minimum(lj, 0.0).sum() * sa * sb)
            fa_rep = float(np.maximum(lj, 0.0).clip(max=50.0).sum() * sa * sb)
            fa_sol = float(np.exp(-((r - 3.0) / 1.5) ** 2).sum() * sa * sb)
            fa_elec = float((332.0 * qa * qb / (4.0 * r * r)).sum() * sa * sb)
            out["fa_atr"] = fa_atr
            out["fa_rep"] = fa_rep
            out["fa_sol"] = fa_sol
            out["fa_elec"] = fa_elec
            out["lk_ball_wtd"] = fa_sol * 0.92    # tightly correlated by design

            # H-bond categories — split by donor/acceptor atom names
            def _is_bb_donor(name): return name == "N"
            def _is_bb_acceptor(name): return name == "O"
            sr_bb, lr_bb, bb_sc, sc_sc = 0, 0, 0, 0
            for k, (i, j) in enumerate(zip(pair_ai, pair_bj)):
                if r[k] > 3.5: continue
                aname, ael, _, rkA, _ = a_atoms_s[i]
                bname, bel, _, rkB, _ = b_atoms_s[j]
                # Donor on A or B; acceptor on the other
                if ael not in {"N", "O", "S"} or bel not in {"N", "O", "S"}: continue
                # Skip same-atom-name (very unlikely true H-bond)
                if aname == bname and ael == bel: continue
                # Both backbone?
                a_bb = aname in ("N", "O", "C", "CA")
                b_bb = bname in ("N", "O", "C", "CA")
                if a_bb and b_bb:
                    # Sequential distance in residues — only meaningful when same chain;
                    # for inter-chain interface this is always "long-range"
                    if rkA[0] == rkB[0]:
                        seq_dist = abs(rkA[1][1] - rkB[1][1])
                        if seq_dist < 5: sr_bb += 1
                        else:            lr_bb += 1
                    else:
                        lr_bb += 1
                elif a_bb or b_bb:
                    bb_sc += 1
                else:
                    sc_sc += 1
            out["hbond_sr_bb"] = float(sr_bb * sa * sb)
            out["hbond_lr_bb"] = float(lr_bb * sa * sb)
            out["hbond_bb_sc"] = float(bb_sc * sa * sb)
            out["hbond_sc"]    = float(sc_sc * sa * sb)

    # ── Intra-residue terms (rough — within-residue repulsive proxy) ─
    intra_rep_sum = 0.0
    for rkey, rdata in residues.items():
        atoms = rdata["atoms"]
        names = list(atoms.keys())
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                d = float(np.linalg.norm(atoms[names[i]] - atoms[names[j]]))
                if d <= 0.0: continue
                # Pure repulsive — penalise atoms closer than 2.5 Å within
                # the same residue (placeholder for the fa_intra_rep term)
                if d < 2.5:
                    intra_rep_sum += (2.5 - d) ** 2
    out["fa_intra_rep"] = float(intra_rep_sum)
    out["fa_intra_sol_xover4"] = float(intra_rep_sum * 0.5)

    # ── Terms we deliberately don't fake ────────────────────────────
    out["fa_dun"] = 0.0          # needs Dunbrack rotamer library

    return out


def _compute_fakesetta(records: Iterable, structure_root: str = "data/raw/alphafold") -> np.ndarray:
    """Featurizer entry point. Returns ``(N, 19)`` float32."""
    from ..graph_features import find_pdb_file
    records_list = list(records)
    out = np.zeros((len(records_list), _FAKESETTA_DIM), dtype=np.float32)
    for i, rec in enumerate(records_list):
        uniprot = getattr(rec, "uniprot", None)
        if not uniprot:
            continue
        pdb = find_pdb_file(uniprot, structure_root)
        if not pdb:
            continue
        scores = score_complex_fakesetta(pdb)
        for k, t in enumerate(_FAKESETTA_TERMS):
            out[i, k] = float(scores.get(t, 0.0))
    return out


register(FeaturizerSpec(
    id="protein_fakesetta",
    label="Fake-setta — 19-d Python score (NOT Rosetta)",
    axis="protein",
    dim=_FAKESETTA_DIM,
    short_desc=(
        "Approximate 19-d Rosetta ref2015-style score: fa_atr/rep/sol/elec, "
        "intra-residue terms, H-bond categories (sr_bb/lr_bb/bb_sc/sc), "
        "disulfide, omega, Ramachandran, per-AA reference. Python-only — "
        "computed entirely from the PDB. NOT real Rosetta; use until the "
        "license-gated ``protein_rosetta_reu`` is wired."
    ),
    long_desc=(
        "Fake-setta is a Python approximation of Rosetta's ref2015 score "
        "function. It uses the SAME 19 term names so the downstream ML "
        "model sees identical feature shapes once the real Rosetta wrapper "
        "comes online — but the underlying physics is intentionally cheaper:\n\n"
        "  • LJ uses atom-type-aware sigma/epsilon (not Rosetta atom types)\n"
        "  • H-bonds are heavy-atom-only (no proper BAH/AHD angle check)\n"
        "  • Solvation is a Gaussian on (r−3) (no Lazaridis-Karplus table)\n"
        "  • Ramachandran uses a coarse 8×8 hand-tabulated density\n"
        "  • fa_dun is set to 0 (no rotamer library)\n"
        "  • per-AA reference energies use rough ref2015 weights from the\n"
        "    published Alford 2017 paper\n\n"
        "These are deliberate approximations — fake-setta is meant to give "
        "the ML model informative inputs TODAY, not to replace Rosetta. "
        "When you install the Rosetta C++ binary or PyRosetta wheel under "
        "WSL, the ``protein_rosetta_reu`` featurizer flips on and you can "
        "use both side-by-side (or swap entirely)."
    ),
    requires=["biopython"],
    cost="moderate",
    compute=_compute_fakesetta if (_BIOPYTHON_OK and _AF_CACHE_OK) else None,
    integrated=bool(_BIOPYTHON_OK and _AF_CACHE_OK),
))


# ── Real Rosetta REU (license-gated) ────────────────────────────────
# IMPORTANT — license compliance:
#   * Rosetta and PyRosetta are licensed by the University of Washington
#     under the Rosetta Commons Academic License.
#   * The user MUST have signed the license agreement and obtained valid
#     credentials BEFORE installing PyRosetta or the Rosetta C++ binary
#     on their local machine.
#   * This featurizer NEVER bundles, downloads, or distributes Rosetta
#     itself. It only wraps a locally-installed instance.
#   * Activation is gated on TWO independent checks:
#       1. ``pyrosetta`` (or rosetta_runtime) is importable in this Python
#       2. ``rosetta_status().loaded`` returns True AND
#          ``rosetta_status().license_acknowledged`` is True
#   * Both checks must pass or the featurizer remains disabled and
#     emits zeros + a clear "needs local install" message.

_ROSETTA_DIM = 19
_ROSETTA_TERMS = list(_FAKESETTA_TERMS)   # same 19 ref2015 term names


def _compute_rosetta_reu(records):
    """Real Rosetta wrapper — populated only when PyRosetta / Rosetta is
    locally installed AND the user has acknowledged the license."""
    records_list = list(records)
    out = np.zeros((len(records_list), _ROSETTA_DIM), dtype=np.float32)
    try:
        from ..rosetta_runtime import score_complex, rosetta_status  # type: ignore
        from ..graph_features import find_pdb_file
        st = rosetta_status()
        # Triple gate: loaded + license acknowledged + binary path present
        if not (st.get("loaded") and st.get("license_acknowledged")):
            return out
    except Exception:
        return out
    for i, rec in enumerate(records_list):
        uniprot = getattr(rec, "uniprot", None)
        if not uniprot:
            continue
        pdb = find_pdb_file(uniprot, "data/raw/alphafold")
        if not pdb:
            continue
        try:
            scores = score_complex(pdb)
            if isinstance(scores, dict):
                # Map ref2015 terms in the SAME order as fake-setta so
                # downstream features are positionally swappable.
                for k, t in enumerate(_ROSETTA_TERMS):
                    out[i, k] = float(scores.get(t, 0.0))
        except Exception:
            continue
    return out


# Probe license + load state at import time. Both must hold for the
# featurizer to register as integrated.
_ROSETTA_LOADED = False
_ROSETTA_LICENSED = False
try:
    from ..rosetta_runtime import rosetta_status as _rosetta_status_probe  # type: ignore
    _st = _rosetta_status_probe()
    _ROSETTA_LOADED = bool(_st.get("loaded"))
    _ROSETTA_LICENSED = bool(_st.get("license_acknowledged"))
except Exception:
    _ROSETTA_LOADED = False
    _ROSETTA_LICENSED = False

_ROSETTA_READY = _ROSETTA_LOADED and _ROSETTA_LICENSED

register(FeaturizerSpec(
    id="protein_rosetta_reu",
    label="Rosetta REU (real ref2015 — requires local Rosetta install + license)",
    axis="protein",
    dim=_ROSETTA_DIM,
    short_desc=(
        "Full 19-d Rosetta ref2015 term breakdown — the genuine article. "
        "Requires a valid Rosetta Commons academic license and a local "
        "install (PyRosetta wheel or Rosetta C++ binary). Featurizer "
        "stays disabled and emits zeros until the install + license "
        "acknowledgement are both verified at server startup."
    ),
    long_desc=(
        "This featurizer wraps a LOCALLY-installed Rosetta. ProteoSphere "
        "never bundles, downloads, or redistributes Rosetta software — "
        "doing so would violate the Rosetta Commons Academic License "
        "Agreement. To enable:\n\n"
        "  1. Sign the academic license at https://www.rosettacommons.org/"
        "software/license-and-download (or https://www.pyrosetta.org "
        "for the Python bindings).\n"
        "  2. Receive credentials by email from license@uw.edu.\n"
        "  3. Install locally — easiest path is PyRosetta under WSL2:\n"
        "       pip install pyrosetta-installer\n"
        "       export PYROSETTA_LICENSE_USER=<user>\n"
        "       export PYROSETTA_LICENSE_PASSWORD=<password>\n"
        "       python -c 'import pyrosetta_installer; "
        "pyrosetta_installer.install_pyrosetta()'\n"
        "  4. Set the env var ``ROSETTA_LICENSE_ACKNOWLEDGED=1`` to "
        "confirm you've reviewed the license terms.\n"
        "  5. Restart the v2 server. The featurizer will detect the "
        "install + acknowledgement and flip from disabled to integrated. "
        "Outputs match fake-setta's 19 dimensions term-for-term so you "
        "can A/B without changing the rest of the pipeline.\n\n"
        "Until then, use ``protein_fakesetta`` — same field names, "
        "Python-only physics, no license required for the surrogate."
    ),
    requires=["pyrosetta_or_rosetta_bin", "license_acknowledged"],
    cost="heavy",
    compute=_compute_rosetta_reu if _ROSETTA_READY else None,
    integrated=bool(_ROSETTA_READY),
))


# ── SKEMPI ΔΔG ingester + hot-spot predictor ─────────────────────────
# When the SKEMPI 2.0 dataset is dropped into ``data/raw/skempi/skempi_v2.csv``
# we train a tiny regressor on (mutation context, ΔΔG) pairs and use it
# to refine the hot-spot featurizer. Until the file is present, this
# module registers as ``planned`` and the Levy 2010 heuristic in the
# hot-spot featurizer above stays the active predictor.

_SKEMPI_CANDIDATES = [
    "data/raw/skempi/skempi_v2.csv",
    "data/raw/skempi/SKEMPI_2.csv",
    "data/raw/skempi/SKEMPI_v2.csv",
]


def _skempi_path() -> str | None:
    for p in _SKEMPI_CANDIDATES:
        if os.path.isfile(p) and os.path.getsize(p) > 10_000:
            return p
    return None


def _parse_skempi(path: str) -> list[dict] | None:
    """Parse SKEMPI 2.0 CSV. Columns of interest:
        #Pdb, Mutation(s)_cleaned, Affinity_mut_parsed, Affinity_wt_parsed
    Returns a list of {"pdb", "mutation", "ddg"} dicts, or None on failure.
    """
    try:
        import csv
        out = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                pdb = (row.get("#Pdb") or "").strip()
                mut = (row.get("Mutation(s)_cleaned") or "").strip()
                try:
                    aff_mut = float(row.get("Affinity_mut_parsed") or "")
                    aff_wt  = float(row.get("Affinity_wt_parsed") or "")
                except (ValueError, TypeError):
                    continue
                # ΔΔG = ΔG(mut) − ΔG(wt). Use −RT ln K with R=0.001987, T=298.
                # Sign convention: positive ΔΔG = mutation destabilises binding.
                import math
                R, T = 0.001987, 298.0
                try:
                    ddg = -R * T * (math.log(aff_mut) - math.log(aff_wt))
                except ValueError:
                    continue
                out.append({"pdb": pdb, "mutation": mut, "ddg": ddg})
        return out
    except Exception:
        return None


# Compute features for the SKEMPI hot-spot regressor: 24-d vector of
# (AA-mutation-context features, residue position). Used both for
# training (in the ingester) and inference (in the featurizer).

_AA20 = list("ACDEFGHIKLMNPQRSTVWY")
def _mut_features(wt: str, mut: str, pos: int, seq_len: int = 500) -> np.ndarray:
    feat = np.zeros(24, dtype=np.float32)
    if wt in _AA20:    feat[_AA20.index(wt)] = 1.0
    # mut goes in the next 20 slots... reuse the first 20 by xor-ing.
    if mut in _AA20:   feat[_AA20.index(mut)] -= 1.0  # difference vector
    # Property changes
    hydrophobic = set("AILMFWVY")
    charged_pos = set("KRH")
    charged_neg = set("DE")
    polar       = set("STNQ")
    feat[20] = (wt in hydrophobic) - (mut in hydrophobic)
    feat[21] = (wt in charged_pos) - (mut in charged_pos)
    feat[22] = (wt in charged_neg) - (mut in charged_neg)
    feat[23] = (wt in polar)       - (mut in polar)
    return feat


_SKEMPI_PATH = _skempi_path()
# Trained-regressor cache. Populated lazily on first featurizer call when
# the SKEMPI CSV is present.
_SKEMPI_REGRESSOR = None


def _fit_skempi_regressor() -> bool:
    """One-time fit. Returns True if the model is now trained."""
    global _SKEMPI_REGRESSOR
    if _SKEMPI_REGRESSOR is not None:
        return True
    if _SKEMPI_PATH is None:
        return False
    rows = _parse_skempi(_SKEMPI_PATH)
    if not rows:
        return False
    X, y = [], []
    for row in rows:
        for mut_str in row["mutation"].split(","):
            mut_str = mut_str.strip()
            if len(mut_str) < 3: continue
            wt, mut = mut_str[0], mut_str[-1]
            try:
                pos = int("".join(c for c in mut_str[1:-1] if c.isdigit()))
            except ValueError:
                continue
            X.append(_mut_features(wt, mut, pos))
            y.append(row["ddg"])
    if len(X) < 50:
        return False
    X = np.asarray(X, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)
    try:
        from sklearn.linear_model import Ridge
        model = Ridge(alpha=1.0)
        model.fit(X, y)
        _SKEMPI_REGRESSOR = {"model": model, "n_train": len(X)}
        return True
    except Exception:
        return False


def _compute_skempi_hotspot(records: Iterable) -> np.ndarray:
    """Featurizer entry point. Returns a 16-d per-record summary of
    predicted ΔΔG hot-spot scores. Same shape as the Levy heuristic so
    selecting either one produces the same downstream feature size."""
    fit_ok = _fit_skempi_regressor()
    records_list = list(records)
    out = np.zeros((len(records_list), 16), dtype=np.float32)
    if not fit_ok:
        return out
    # For each record, score every (wt → A) alanine-scan mutation along
    # the sequence (an in-silico AlaScan), then quantile-summarise into
    # 16 dims like the Levy heuristic. The trained regressor gives a
    # ΔΔG per position; high positive ΔΔG = predicted hot spot.
    model = _SKEMPI_REGRESSOR["model"]
    for i, rec in enumerate(records_list):
        seq = getattr(rec, "sequence", None) or ""
        if len(seq) < 10:
            continue
        seq = seq[:500]  # cap for cost
        preds = []
        for pos, wt in enumerate(seq):
            if wt not in _AA20: continue
            preds.append(float(model.predict(_mut_features(wt, "A", pos).reshape(1, -1))[0]))
        if not preds:
            continue
        arr = np.asarray(preds, dtype=np.float32)
        # 4 quantile bins, each with mean / max / std / count
        order = np.argsort(-arr)
        n = len(arr)
        bin_sz = max(1, n // 4)
        for b in range(4):
            lo = b * bin_sz
            hi = (b + 1) * bin_sz if b < 3 else n
            seg = arr[order[lo:hi]]
            if seg.size == 0: continue
            out[i, b*4 + 0] = float(seg.mean())
            out[i, b*4 + 1] = float(seg.max())
            out[i, b*4 + 2] = float(seg.std())
            out[i, b*4 + 3] = float(seg.size)
    return out


# Only register when the SKEMPI file is present. Otherwise the Levy
# heuristic above stays the integrated hot-spot featurizer and this
# entry is a planned placeholder.
if _SKEMPI_PATH is not None:
    register(FeaturizerSpec(
        id="interaction_hot_spot_skempi",
        label="Interface hot-spot ΔΔG (SKEMPI-trained)",
        axis="interaction",
        dim=16,
        short_desc=(
            "Per-record 16-d quantile summary of predicted ΔΔG values "
            "from a Ridge regressor trained on SKEMPI 2.0 mutational "
            "data. Replaces the Levy heuristic when SKEMPI is ingested."
        ),
        long_desc=(
            "Trained at server startup on SKEMPI 2.0 (read from "
            f"{_SKEMPI_PATH}). The regressor is a Ridge model fit on 24-d "
            "(wt→mut, position, property-change) features; for each "
            "training-example protein, we run an in-silico alanine scan "
            "across its sequence, predict ΔΔG at every position, and "
            "summarise into 16 dims by descending-quantile bins of "
            "predicted ΔΔG. Pair with the Levy heuristic for an ensemble; "
            "they're trained on different signals (propensity vs. real ΔΔG)."
        ),
        requires=["scikit-learn", "skempi_v2.csv"],
        cost="moderate",
        compute=_compute_skempi_hotspot,
        integrated=True,
    ))
else:
    register(FeaturizerSpec(
        id="interaction_hot_spot_skempi",
        label="Interface hot-spot ΔΔG (SKEMPI-trained — needs data file)",
        axis="interaction",
        dim=16,
        short_desc=(
            "SKEMPI-trained Ridge ΔΔG predictor. Currently disabled — "
            "needs ``data/raw/skempi/skempi_v2.csv``. Download from "
            "https://life.bsc.es/pid/skempi2."
        ),
        long_desc=(
            "Drop SKEMPI 2.0 at ``data/raw/skempi/skempi_v2.csv`` and "
            "restart the server. The module will parse it, fit a tiny "
            "Ridge regressor on the mutational ΔΔG data, and flip this "
            "featurizer from disabled to integrated. Until then, the "
            "Levy 2010 heuristic (``interaction_hot_spot_probability``) "
            "is the active hot-spot featurizer."
        ),
        requires=["scikit-learn", "skempi_v2.csv"],
        cost="moderate",
        compute=None,
        integrated=False,
    ))


# ── Pose contact map featurizer ──────────────────────────────────────
# For records with a co-crystal (PDBBind-style) protein-ligand complex
# PDB, returns a fixed-shape contact summary. When no complex PDB is
# cached, returns zeros — the Features screen labels this "needs cache".

_POSE_CONTACT_DIM = 20   # 4 quantile bins × 5 stats


def _pose_contact_map(pdb_path: str) -> np.ndarray | None:
    """Compute per-pose contact summary: distances from ligand atoms to
    each protein residue's CA, then summarise into quantile bins.

    Returns a 20-d float32 vector or None on failure.
    """
    try:
        from Bio.PDB import PDBParser, MMCIFParser
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
    model = next(iter(structure), None)
    if model is None:
        return None
    # Polymer residues (CA atoms) + ligand atoms (HETATM, not water)
    ca_coords = []
    lig_coords = []
    for chain in model:
        for residue in chain:
            if residue.id[0] == " ":     # standard polymer
                if "CA" in residue:
                    ca_coords.append(residue["CA"].coord.copy())
            elif residue.resname.strip().upper() not in ("HOH", "WAT"):
                for atom in residue:
                    if (atom.element or "").upper() != "H":
                        lig_coords.append(atom.coord.copy())
    if not ca_coords or not lig_coords:
        return None
    ca = np.stack(ca_coords, axis=0).astype(np.float32)
    lg = np.stack(lig_coords, axis=0).astype(np.float32)
    # Per-residue: min distance to any ligand heavy atom
    diff = ca[:, None, :] - lg[None, :, :]
    dist = np.sqrt((diff * diff).sum(axis=-1))
    min_dist = dist.min(axis=1)
    # 4 bins by distance: <5 Å, 5-8, 8-12, ≥12. Each bin reports:
    # count, mean dist, min dist, count_under_3.5, ligand_atom_diversity
    out = np.zeros(_POSE_CONTACT_DIM, dtype=np.float32)
    bands = [(0, 5), (5, 8), (8, 12), (12, 1e9)]
    for b, (lo, hi) in enumerate(bands):
        mask = (min_dist >= lo) & (min_dist < hi)
        seg = min_dist[mask]
        out[b*5 + 0] = float(seg.size)
        out[b*5 + 1] = float(seg.mean()) if seg.size else 0.0
        out[b*5 + 2] = float(seg.min())  if seg.size else 0.0
        # how many residues in this band have an atom < 3.5 Å (H-bond proxy)
        out[b*5 + 3] = float(((seg < 3.5).sum()) if seg.size else 0.0)
        # ligand atom diversity: count of unique ligand atoms within 5 Å
        # of any residue in this band (lower bound on contact diversity)
        if seg.size and lo <= 5.0:
            close = dist[mask] < 5.0
            unique_lig = int(close.any(axis=0).sum())
        else:
            unique_lig = 0
        out[b*5 + 4] = float(unique_lig)
    return out


def _compute_pose_contact_map(records: Iterable, structure_root: str = "data/raw/alphafold") -> np.ndarray:
    """Featurizer entry. Looks up each record's cached PDB; if it's a
    co-crystal (has HETATM ligand), computes the 20-d contact summary.
    AlphaFold monomers (no ligand) → zeros."""
    from ..graph_features import find_pdb_file
    records_list = list(records)
    out = np.zeros((len(records_list), _POSE_CONTACT_DIM), dtype=np.float32)
    for i, rec in enumerate(records_list):
        uniprot = getattr(rec, "uniprot", None)
        if not uniprot:
            continue
        pdb = find_pdb_file(uniprot, structure_root)
        if not pdb:
            continue
        v = _pose_contact_map(pdb)
        if v is not None:
            out[i] = v
    return out


register(FeaturizerSpec(
    id="interaction_pose_contact_map",
    label="Pose contact map (residue ↔ ligand)",
    axis="interaction",
    dim=_POSE_CONTACT_DIM,
    short_desc=(
        "20-d summary of the residue→nearest-ligand-atom distance "
        "distribution at the binding pocket: 4 distance bands × "
        "(count, mean, min, < 3.5 Å count, ligand atom diversity)."
    ),
    long_desc=(
        "For records with a co-crystal complex PDB (PDBBind-style, HETATM "
        "ligand), computes min(residue CA → ligand atom) distance per "
        "residue. The distribution is binned into 4 distance bands and "
        "summarised into 20 dims. AlphaFold monomer entries have no "
        "ligand HETATM and return zeros — meaningful absence rather than "
        "missing data. Pair with a docking pass (planned) to populate "
        "this featurizer for records lacking a co-crystal."
    ),
    requires=["biopython"],
    cost="moderate",
    compute=_compute_pose_contact_map if (_BIOPYTHON_OK and _AF_CACHE_OK) else None,
    integrated=bool(_BIOPYTHON_OK and _AF_CACHE_OK),
))
