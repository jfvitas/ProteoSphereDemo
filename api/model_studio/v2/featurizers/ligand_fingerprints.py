"""Ligand fingerprint featurizers.

Six fingerprint families wired in, all via RDKit:

    ecfp4_2048    Morgan radius=2, 2048 bits — the canonical DTI fingerprint
    ecfp6_2048    Morgan radius=3, 2048 bits — sharper but more sensitive to atoms
    fcfp4_2048    Feature-class Morgan r=2 — pharmacophoric flavor of ECFP
    maccs_166     MACCS keys 166 bit — small + drug-screening favorite
    atompair_2048 Atom-pair fingerprint — captures topological distances
    tt_2048       Topological torsion — angle-pattern fingerprint
    rdkit_2048    RDKit's path-based topological fingerprint
    avalon_512    Avalon fingerprint — popular in Lipinski-era cheminformatics

All are returned as 0/1 dense float32 arrays.
"""

from __future__ import annotations

from typing import Iterable

import numpy as np

from . import register, FeaturizerSpec


_RDKIT_OK = False
try:
    from rdkit import Chem, DataStructs
    from rdkit.Chem import AllChem, MACCSkeys, rdFingerprintGenerator
    try:
        from rdkit.Avalon import pyAvalonTools
        _HAS_AVALON = True
    except Exception:
        _HAS_AVALON = False
    _RDKIT_OK = True
except ImportError:
    _HAS_AVALON = False


def _bits_to_array(bv, n_bits: int) -> np.ndarray:
    arr = np.zeros((n_bits,), dtype=np.uint8)
    DataStructs.ConvertToNumpyArray(bv, arr)
    return arr.astype(np.float32)


def _mk_morgan(records, *, radius: int, n_bits: int, use_features: bool) -> np.ndarray:
    gen = rdFingerprintGenerator.GetMorganGenerator(
        radius=radius, fpSize=n_bits,
        atomInvariantsGenerator=(rdFingerprintGenerator.GetMorganFeatureAtomInvGen()
                                 if use_features else None),
    )
    out = np.zeros((len(records), n_bits), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        out[i] = _bits_to_array(gen.GetFingerprint(mol), n_bits)
    return out


def _mk_maccs(records) -> np.ndarray:
    out = np.zeros((len(records), 167), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        out[i] = _bits_to_array(MACCSkeys.GenMACCSKeys(mol), 167)
    # MACCS bit 0 is always 0; trim to 166 bits is the convention but
    # keeping 167 to match RDKit's vector and let the model decide.
    return out


def _mk_atompair(records, *, n_bits: int) -> np.ndarray:
    gen = rdFingerprintGenerator.GetAtomPairGenerator(fpSize=n_bits)
    out = np.zeros((len(records), n_bits), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        out[i] = _bits_to_array(gen.GetFingerprint(mol), n_bits)
    return out


def _mk_tt(records, *, n_bits: int) -> np.ndarray:
    gen = rdFingerprintGenerator.GetTopologicalTorsionGenerator(fpSize=n_bits)
    out = np.zeros((len(records), n_bits), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        out[i] = _bits_to_array(gen.GetFingerprint(mol), n_bits)
    return out


def _mk_rdkit_topological(records, *, n_bits: int) -> np.ndarray:
    gen = rdFingerprintGenerator.GetRDKitFPGenerator(fpSize=n_bits)
    out = np.zeros((len(records), n_bits), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        out[i] = _bits_to_array(gen.GetFingerprint(mol), n_bits)
    return out


def _mk_avalon(records, *, n_bits: int) -> np.ndarray:
    if not _HAS_AVALON:
        return np.zeros((len(records), n_bits), dtype=np.float32)
    out = np.zeros((len(records), n_bits), dtype=np.float32)
    for i, r in enumerate(records):
        mol = Chem.MolFromSmiles(getattr(r, "smiles", "") or "")
        if mol is None:
            continue
        out[i] = _bits_to_array(pyAvalonTools.GetAvalonFP(mol, nBits=n_bits), n_bits)
    return out


# ── Registration ───────────────────────────────────────────────────────

def _registered(label: str, dim: int, fn, *, fid: str, short: str, long: str,
                cost: str = "fast") -> None:
    register(FeaturizerSpec(
        id=fid, label=label, axis="ligand", dim=dim,
        short_desc=short, long_desc=long,
        requires=["rdkit"], cost=cost,
        compute=fn if _RDKIT_OK else None,
        integrated=_RDKIT_OK,
    ))


if _RDKIT_OK:
    _registered("ECFP4 / Morgan-r2 (2048)", 2048,
                lambda rs: _mk_morgan(rs, radius=2, n_bits=2048, use_features=False),
                fid="ligand_ecfp4_2048",
                short="The canonical DTI fingerprint. ~10% on-bit density.",
                long=("Morgan circular fingerprint at radius 2 (ECFP4). 2048-bit "
                      "folded. Used as the protein-ligand-similarity backbone in "
                      "the v2 catalog already. Best general-purpose choice."))

    _registered("ECFP6 / Morgan-r3 (2048)", 2048,
                lambda rs: _mk_morgan(rs, radius=3, n_bits=2048, use_features=False),
                fid="ligand_ecfp6_2048",
                short="Higher-resolution ECFP. Better for SAR work.",
                long=("Morgan circular fingerprint at radius 3. Captures larger "
                      "atom neighbourhoods than ECFP4 — better for structure-"
                      "activity-relationship modelling, more sensitive to small "
                      "structural changes."))

    _registered("FCFP4 (2048)", 2048,
                lambda rs: _mk_morgan(rs, radius=2, n_bits=2048, use_features=True),
                fid="ligand_fcfp4_2048",
                short="Pharmacophore-flavored Morgan fingerprint.",
                long=("Feature-class Morgan fingerprint at radius 2. Atom features "
                      "encode pharmacophore classes (donor, acceptor, etc.) instead "
                      "of atom identity — generalises better across chemotypes."))

    _registered("MACCS keys (167)", 167, _mk_maccs,
                fid="ligand_maccs_167",
                short="166 + dummy structural keys. Drug-screening classic.",
                long=("MACCS structural keys — 166 pharmacophore-driven SMARTS "
                      "patterns + bit 0. Tiny, fast, and the only fingerprint "
                      "small enough to use as a hand-readable hash."))

    _registered("Atom-pair fingerprint (2048)", 2048,
                lambda rs: _mk_atompair(rs, n_bits=2048),
                fid="ligand_atompair_2048",
                short="Pairwise atom + path-distance hashes.",
                long=("Atom-pair fingerprint — encodes (atom_i, atom_j, path_len) "
                      "triples. Complements ECFP because it captures long-range "
                      "topology where ECFP captures local neighbourhoods."))

    _registered("Topological torsion (2048)", 2048,
                lambda rs: _mk_tt(rs, n_bits=2048),
                fid="ligand_tt_2048",
                short="Four-atom torsion pattern fingerprint.",
                long=("Topological torsion fingerprint — captures sequences of "
                      "four consecutive bonded atoms. Picks up dihedral/torsion "
                      "patterns that ECFP misses."))

    _registered("RDKit topological (2048)", 2048,
                lambda rs: _mk_rdkit_topological(rs, n_bits=2048),
                fid="ligand_rdkit_2048",
                short="RDKit's path-based default fingerprint.",
                long=("RDKit's built-in topological fingerprint — Daylight-style "
                      "path-based. A good all-rounder when you don't want any of "
                      "the Morgan / atom-pair specialisations."))

    if _HAS_AVALON:
        _registered("Avalon fingerprint (512)", 512,
                    lambda rs: _mk_avalon(rs, n_bits=512),
                    fid="ligand_avalon_512",
                    short="Compact Lipinski-era fingerprint.",
                    long=("Avalon fingerprint — small 512-bit hash with strong "
                          "performance on ADMET-style tasks. Cheap baseline."))
