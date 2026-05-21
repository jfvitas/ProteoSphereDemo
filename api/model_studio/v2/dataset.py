"""Davis DTA dataset loader for DeepDTA training.

Davis et al. 2011 — 442 kinase sequences × 68 inhibitor SMILES, 30,056 pKd
labels. This is the canonical DeepDTA training benchmark.

Files (downloaded once into ``DAVIS_DIR``):
    proteins.txt     JSON: gene_symbol -> sequence
    ligands_iso.txt  JSON: chembl_id -> SMILES
    Y                pickled np.float32 (68, 442) Kd values in nM

Encoding follows the original DeepDTA paper (Öztürk 2018):
    - Sequences padded/truncated to MAX_SEQ_LEN (1000) and mapped to
      integers via CHARPROTSET (25 chars).
    - SMILES padded/truncated to MAX_SMI_LEN (100) and mapped via
      CHARISOSMISET (64 chars).
    - Labels: pKd = 9 - log10(Kd_nM). Davis Y=10000 (saturated) → pKd 5.0.
"""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset, DataLoader

# ── Paths ─────────────────────────────────────────────────────────────
# Pinned location for now; future warehouse loader will dispatch on
# pipeline.dataset.source instead.
DAVIS_DIR = Path("$PROTEOSPHERE_ROOT/data/datasets/davis")

# ── Token vocabularies (DeepDTA originals) ────────────────────────────
# Protein alphabet: 25 characters including standard 20 + 5 ambiguous / rare.
# Index 0 reserved for padding.
CHARPROTSET = {
    "A": 1, "C": 2, "B": 3, "E": 4, "D": 5, "G": 6, "F": 7, "I": 8, "H": 9,
    "K": 10, "M": 11, "L": 12, "O": 13, "N": 14, "Q": 15, "P": 16, "S": 17,
    "R": 18, "U": 19, "T": 20, "W": 21, "V": 22, "Y": 23, "X": 24, "Z": 25,
}
CHARPROTLEN = 25

# SMILES alphabet: 64 characters covering isomeric SMILES.
CHARISOSMISET = {
    "#": 29, "%": 30, ")": 31, "(": 1, "+": 32, "-": 33, ".": 2, "1": 34,
    "0": 3, "3": 35, "2": 4, "5": 36, "4": 5, "7": 37, "6": 6, "9": 38,
    "8": 7, "=": 39, "A": 40, "C": 8, "B": 41, "E": 42, "D": 9, "G": 43,
    "F": 10, "I": 44, "H": 11, "K": 45, "M": 46, "L": 12, "O": 47, "N": 13,
    "P": 48, "S": 49, "R": 14, "U": 50, "T": 15, "W": 51, "V": 16, "Y": 52,
    "[": 53, "Z": 17, "]": 54, "\\": 18, "a": 55, "c": 19, "b": 56, "e": 57,
    "d": 20, "g": 58, "f": 21, "i": 59, "h": 22, "m": 60, "l": 23, "o": 61,
    "n": 24, "s": 62, "r": 25, "u": 63, "t": 26, "y": 64, "x": 27, "@": 28,
}
CHARISOSMILEN = 64

MAX_SEQ_LEN = 1000
MAX_SMI_LEN = 100


def label_smiles(line: str, max_smi_len: int = MAX_SMI_LEN) -> np.ndarray:
    """Encode a SMILES string into a (max_smi_len,) int64 array."""
    x = np.zeros(max_smi_len, dtype=np.int64)
    for i, ch in enumerate(line[:max_smi_len]):
        x[i] = CHARISOSMISET.get(ch, 0)
    return x


def label_sequence(line: str, max_seq_len: int = MAX_SEQ_LEN) -> np.ndarray:
    """Encode an amino-acid sequence into a (max_seq_len,) int64 array."""
    x = np.zeros(max_seq_len, dtype=np.int64)
    for i, ch in enumerate(line[:max_seq_len]):
        x[i] = CHARPROTSET.get(ch.upper(), 0)
    return x


# ── Loader ─────────────────────────────────────────────────────────────

@dataclass
class DavisRecord:
    protein_idx: int       # row in protein list
    ligand_idx: int        # row in ligand list
    protein_name: str
    ligand_id: str
    sequence: str
    smiles: str
    pkd: float


def load_davis_records() -> tuple[list[DavisRecord], list[str], list[str]]:
    """Read the Davis files from disk and return one record per (P,L) pair
    that has a defined Kd. Returns (records, protein_names, ligand_ids).
    """
    if not DAVIS_DIR.exists():
        raise FileNotFoundError(
            f"Davis dataset not found at {DAVIS_DIR}. "
            "Run scripts/datasets/fetch_davis.py to download it (one-time)."
        )
    with open(DAVIS_DIR / "proteins.txt") as f:
        proteins = json.load(f)  # gene_symbol -> sequence
    with open(DAVIS_DIR / "ligands_iso.txt") as f:
        ligands = json.load(f)   # chembl_id -> SMILES
    with open(DAVIS_DIR / "Y", "rb") as f:
        Y = pickle.load(f, encoding="latin1")  # shape (n_ligands, n_proteins)

    protein_names = list(proteins.keys())
    ligand_ids = list(ligands.keys())
    records: list[DavisRecord] = []
    for li, lid in enumerate(ligand_ids):
        for pi, pn in enumerate(protein_names):
            kd_nm = float(Y[li, pi])
            if np.isnan(kd_nm) or kd_nm <= 0:
                continue
            pkd = 9.0 - np.log10(kd_nm)
            records.append(DavisRecord(
                protein_idx=pi, ligand_idx=li,
                protein_name=pn, ligand_id=lid,
                sequence=proteins[pn], smiles=ligands[lid],
                pkd=float(pkd),
            ))
    return records, protein_names, ligand_ids


# ── PyTorch Dataset ────────────────────────────────────────────────────

class DavisDataset(Dataset):
    """Tokenized Davis pairs ready for DeepDTA.

    __getitem__ returns three tensors:
        seq    int64  (MAX_SEQ_LEN,)
        smi    int64  (MAX_SMI_LEN,)
        y      float32 ()
    """
    def __init__(self, records: list[DavisRecord]):
        self.records = records
        # Pre-tokenize: this is small enough (~30K records × 1100 ints = ~32MB)
        # to keep in RAM and avoid re-tokenizing every epoch.
        self.seqs = np.stack([label_sequence(r.sequence) for r in records])
        self.smis = np.stack([label_smiles(r.smiles) for r in records])
        self.ys = np.array([r.pkd for r in records], dtype=np.float32)

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int):
        return (
            torch.from_numpy(self.seqs[idx]),
            torch.from_numpy(self.smis[idx]),
            torch.tensor(self.ys[idx], dtype=torch.float32),
        )


def make_loaders(
    *,
    batch_size: int = 256,
    val_frac: float = 0.1,
    test_frac: float = 0.1,
    seed: int = 4192,
    num_workers: int = 0,
) -> tuple[DataLoader, DataLoader, DataLoader, dict]:
    """Random-split Davis into train/val/test loaders.

    The split is by INDEX (not by protein or ligand) — this is the
    canonical DeepDTA "warm" split. For cold-target / cold-drug splits
    (the harder generalisation tasks) the runtime should compute the
    split off the records and pass three index lists instead.
    """
    records, prots, ligs = load_davis_records()
    n = len(records)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    n_test = int(n * test_frac)
    n_val  = int(n * val_frac)
    test_idx  = idx[:n_test]
    val_idx   = idx[n_test:n_test + n_val]
    train_idx = idx[n_test + n_val:]

    full = DavisDataset(records)
    train_ds = torch.utils.data.Subset(full, train_idx.tolist())
    val_ds   = torch.utils.data.Subset(full, val_idx.tolist())
    test_ds  = torch.utils.data.Subset(full, test_idx.tolist())

    train_loader = DataLoader(
        train_ds, batch_size=batch_size, shuffle=True,
        num_workers=num_workers, pin_memory=True, drop_last=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=batch_size, shuffle=False,
        num_workers=num_workers, pin_memory=True,
    )
    test_loader = DataLoader(
        test_ds, batch_size=batch_size, shuffle=False,
        num_workers=num_workers, pin_memory=True,
    )
    meta = {
        "n_records": n,
        "n_train": len(train_ds),
        "n_val": len(val_ds),
        "n_test": len(test_ds),
        "n_proteins": len(prots),
        "n_ligands": len(ligs),
        "pkd_range": (float(full.ys.min()), float(full.ys.max())),
    }
    return train_loader, val_loader, test_loader, meta
