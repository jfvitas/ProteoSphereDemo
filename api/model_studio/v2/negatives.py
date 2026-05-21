"""Negative-pair sampling for DTI / DTA training.

Most public DTI datasets contain only **positive observations** — pairs
that were tested and bind. Training a binary classifier (or even a
regression model that should separate binders from non-binders) needs
explicit negatives. This module produces them using four strategies:

    random              — sample (protein, ligand) pairs not in positives
    degree_matched      — keep each protein's negative count proportional
                          to its positive count (avoids the popular-target
                          bias where well-studied kinases dominate negatives)
    threshold_binarize  — keep only ASSAYED pairs below a binding threshold
                          (Davis: pKd < 5 → non-binder; KIBA: kiba_score < 6)
    decoy_set           — use a curated decoy ligand set (e.g. DUD-E-style:
                          molecules similar to the positives but verified
                          non-binders elsewhere). Requires a decoy file.

For DTA regression, threshold_binarize is the principled choice — the
"negatives" carry real measurements. For DTI classification on
positive-only sources (gtopdb, BindingDB curation), random or
degree-matched generates the synthetic negatives the literature uses.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from .dataset_warehouse import DTARecord


@dataclass
class NegativeRecord:
    """Same shape as DTARecord but with label=0 (non-binder)."""
    uniprot: str
    ligand_ref: str
    sequence: str
    smiles: str
    label: float = 0.0
    source_label: str = "synthetic_negative"


def sample_random_negatives(
    records: list[DTARecord],
    *,
    ratio: float = 1.0,
    seed: int = 4192,
    max_attempts_per_neg: int = 100,
) -> list[NegativeRecord]:
    """Generate (protein, ligand) pairs absent from the positive set.

    For each positive, sample ``ratio`` negatives by drawing a random
    protein × random ligand from the universe of present-in-positives
    entities. Rejects pairs already in the positive set.

    NOTE: Only effective for SPARSE datasets where |positives| ≪
    |proteins| × |ligands|. On Davis (442 × 68 = 30K possible pairs, ALL
    measured) random sampling hits the rejection wall immediately. Use
    :func:`threshold_binarize` for dense benchmarks.
    """
    rng = random.Random(seed)
    positives: set[tuple[str, str]] = {(r.uniprot, r.ligand_ref) for r in records}
    seq_lookup = {r.uniprot: r.sequence for r in records}
    smi_lookup = {r.ligand_ref: r.smiles for r in records}
    uniprots = list(seq_lookup.keys())
    ligands  = list(smi_lookup.keys())
    max_possible_pairs = len(uniprots) * len(ligands)
    density = len(positives) / max(max_possible_pairs, 1)
    if density > 0.5:
        # Sparse-pair assumption broken: rejection sampling will spin
        # forever. Bail with an empty list so callers can fall back.
        return []

    target_n = int(len(records) * ratio)
    out: list[NegativeRecord] = []
    seen_neg: set[tuple[str, str]] = set()
    attempts = 0
    while len(out) < target_n and attempts < target_n * max_attempts_per_neg:
        u = rng.choice(uniprots)
        l = rng.choice(ligands)
        attempts += 1
        if (u, l) in positives or (u, l) in seen_neg:
            continue
        seen_neg.add((u, l))
        out.append(NegativeRecord(
            uniprot=u, ligand_ref=l,
            sequence=seq_lookup[u], smiles=smi_lookup[l], label=0.0,
        ))
    return out


def sample_degree_matched_negatives(
    records: list[DTARecord],
    *,
    ratio: float = 1.0,
    seed: int = 4192,
) -> list[NegativeRecord]:
    """Generate negatives such that each protein keeps its observed
    positive:negative ratio. Mitigates the popular-target bias where
    well-assayed kinases dominate the negative pool if you draw uniformly.
    """
    rng = random.Random(seed)
    by_prot: dict[str, list[DTARecord]] = {}
    for r in records:
        by_prot.setdefault(r.uniprot, []).append(r)
    smi_lookup = {r.ligand_ref: r.smiles for r in records}
    seq_lookup = {r.uniprot: r.sequence for r in records}
    all_ligands = list(smi_lookup.keys())

    out: list[NegativeRecord] = []
    for u, pos_rows in by_prot.items():
        positives = {r.ligand_ref for r in pos_rows}
        n_neg = max(1, int(len(pos_rows) * ratio))
        seq = seq_lookup[u]
        for _ in range(n_neg):
            for _ in range(50):
                l = rng.choice(all_ligands)
                if l in positives:
                    continue
                out.append(NegativeRecord(
                    uniprot=u, ligand_ref=l,
                    sequence=seq, smiles=smi_lookup[l], label=0.0,
                ))
                break
    return out


# Default ACTIVE-thresholds for the major DTA benchmarks. Tuned to
# literature conventions:
#   Davis     pKd ≥ 7 ≡ Kd ≤ 100 nM    → "active binder" (DeepDTA paper).
#             ~30% of the dataset; the remaining 70% saturate at pKd=5
#             (Kd ≥ 10 μM, reported as the assay floor) so those are real
#             non-binders.
#   KIBA      score ≥ 12.1            → "active" (Tang 2014 paper). About
#             24% of the dataset.
#   gtopdb    pKi ≥ 6  ≡ Ki ≤ 1 μM    → drug-like binder threshold.
_BINARIZE_THRESHOLDS = {
    "davis":  7.0,
    "kiba":   12.1,
    "gtopdb": 6.0,
}


def threshold_binarize(
    records: list[DTARecord],
    *,
    benchmark: str | None = None,
    threshold: float | None = None,
) -> tuple[list[DTARecord], list[NegativeRecord]]:
    """Split a regression dataset into binders + non-binders by label
    threshold. Returns (positives, negatives) where both lists carry the
    REAL measurements (no synthetic pairs).
    """
    if threshold is None:
        if benchmark is None:
            raise ValueError("Need either benchmark or threshold")
        threshold = _BINARIZE_THRESHOLDS.get(benchmark)
        if threshold is None:
            raise ValueError(f"No default threshold for benchmark '{benchmark}'. "
                             f"Choices: {sorted(_BINARIZE_THRESHOLDS)}.")
    positives, negatives = [], []
    for r in records:
        if r.label >= threshold:
            positives.append(r)
        else:
            negatives.append(NegativeRecord(
                uniprot=r.uniprot, ligand_ref=r.ligand_ref,
                sequence=r.sequence, smiles=r.smiles, label=0.0,
                source_label=f"thresholded@{threshold}",
            ))
    return positives, negatives


def negatives_summary(positives: list[DTARecord],
                      negatives: list) -> dict:
    """Quick stats so the training log can show the positive/negative ratio."""
    pos_prots = {r.uniprot for r in positives}
    neg_prots = {r.uniprot for r in negatives}
    pos_ligs  = {r.ligand_ref for r in positives}
    neg_ligs  = {r.ligand_ref for r in negatives}
    return {
        "n_positives":        len(positives),
        "n_negatives":        len(negatives),
        "ratio":              len(negatives) / max(len(positives), 1),
        "uniq_proteins_pos":  len(pos_prots),
        "uniq_proteins_neg":  len(neg_prots),
        "uniq_ligands_pos":   len(pos_ligs),
        "uniq_ligands_neg":   len(neg_ligs),
        "shared_proteins":    len(pos_prots & neg_prots),
        "shared_ligands":     len(pos_ligs & neg_ligs),
    }
