"""Protein-axis pathway-membership featurizer (Reactome).

Reads Reactome's ``UniProt2Reactome.txt`` mapping and returns a
multi-hot vector per protein indicating which of the top-K most
frequent pathways it belongs to. Useful as a tabular feature for
tabular_mlp / thermo_mlp — captures functional context that pure
sequence-derived features can't see.

Expected input file:
    data/raw/reactome/UniProt2Reactome.txt  (tab-separated, the public
                                              Reactome download)

Columns (per the Reactome download spec):
    1. UniProt Accession        (e.g. "P04637")
    2. Reactome Pathway ID      (e.g. "R-HSA-69620")
    3. URL                      (ignored)
    4. Event Name               (e.g. "Cell Cycle Checkpoints")
    5. Evidence Code            (e.g. "TAS", "IEA")
    6. Species                  (e.g. "Homo sapiens")

Download: https://reactome.org/download/current/UniProt2Reactome.txt
(Or the human-specific UniProt2Reactome_PE_All_Levels.txt for more
fine-grained pathway membership.)

When the file is missing the featurizer registers itself with
``integrated=False`` so it shows up in the catalog with a clear "data
not yet downloaded" note rather than silently disappearing.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Iterable

import numpy as np

from . import FeaturizerSpec, register


_REACTOME_PATH_CANDIDATES = [
    "data/raw/reactome/UniProt2Reactome.txt",
    "data/raw/reactome/UniProt2Reactome_All_Levels.txt",
    "data/canonical/reactome/uniprot2reactome.tsv",
]

# How many top-frequency pathways to include in the multi-hot vector.
# 512 is a sane default: covers the top human pathways without blowing
# out the feature dim. Tunable via the spec's params.
_TOP_K_PATHWAYS = 512

# Module-level caches built once and reused across calls.
_LOADED = False
_PATHWAY_IDS: list[str] = []                  # ordered, dim = len(...)
_UNIPROT_TO_PATHWAY_SET: dict[str, set[str]] = {}
_FILE_USED: str | None = None


def _locate_file() -> str | None:
    for cand in _REACTOME_PATH_CANDIDATES:
        if os.path.isfile(cand) and os.path.getsize(cand) > 1024:
            return cand
    return None


def _load_pathways() -> None:
    """Parse the Reactome dump and pick the top-K most-frequent pathways.

    Idempotent — repeated calls are O(1) after the first.
    """
    global _LOADED, _PATHWAY_IDS, _UNIPROT_TO_PATHWAY_SET, _FILE_USED
    if _LOADED:
        return
    path = _locate_file()
    _FILE_USED = path
    if path is None:
        _LOADED = True
        return
    # Count pathway occurrences first → pick top-K → second pass to
    # populate the per-uniprot membership sets restricted to top-K.
    pathway_counts: Counter = Counter()
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            uniprot, pathway_id = parts[0].strip(), parts[1].strip()
            if not uniprot or not pathway_id:
                continue
            pathway_counts[pathway_id] += 1
    top_k = [pid for pid, _ in pathway_counts.most_common(_TOP_K_PATHWAYS)]
    top_k_set = set(top_k)
    _PATHWAY_IDS = top_k
    membership: dict[str, set[str]] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            uniprot, pathway_id = parts[0].strip(), parts[1].strip()
            if pathway_id not in top_k_set:
                continue
            membership.setdefault(uniprot, set()).add(pathway_id)
    _UNIPROT_TO_PATHWAY_SET = membership
    _LOADED = True


def _compute(records: Iterable) -> np.ndarray:
    """Multi-hot pathway membership for each record.

    Records may be dicts or dataclasses; we read ``uniprot``. Unknown
    UniProts get a zero vector — they're informative as a "no pathway
    annotation" signal.
    """
    _load_pathways()
    dim = len(_PATHWAY_IDS) or 1
    records_list = list(records)
    out = np.zeros((len(records_list), dim), dtype=np.float32)
    if not _PATHWAY_IDS:
        return out
    pid_to_col = {pid: i for i, pid in enumerate(_PATHWAY_IDS)}
    for row_i, rec in enumerate(records_list):
        u = getattr(rec, "uniprot", None) or (rec.get("uniprot") if isinstance(rec, dict) else None)
        if not u:
            continue
        pset = _UNIPROT_TO_PATHWAY_SET.get(u)
        if not pset:
            continue
        for pid in pset:
            j = pid_to_col.get(pid)
            if j is not None:
                out[row_i, j] = 1.0
    return out


# Probe the filesystem at import time so the catalog reports the right
# integrated/dim. We don't actually load the file here — only at first
# compute call — but we do check if it's findable.
_path_seen = _locate_file()

register(FeaturizerSpec(
    id="protein_reactome_pathways",
    label="Reactome pathway membership",
    axis="protein",
    dim=_TOP_K_PATHWAYS,
    short_desc=(
        f"Multi-hot membership of the top-{_TOP_K_PATHWAYS} Reactome pathways "
        f"(per UniProt). Captures functional context that sequence-only "
        f"features can't see."
    ),
    long_desc=(
        "Loads UniProt2Reactome.txt (download from https://reactome.org/"
        "download/current/UniProt2Reactome.txt and drop into "
        "data/raw/reactome/). Counts pathway frequencies, keeps the top "
        f"{_TOP_K_PATHWAYS}, and returns a {_TOP_K_PATHWAYS}-d multi-hot "
        "vector per protein indicating which of those pathways the UniProt "
        "is annotated to. Combine with sequence-only featurizers via "
        "tabular_mlp for a functional context-aware tabular model."
        + ("" if _path_seen else
           "  ⚠ Data file not yet downloaded — featurizer will return "
           "zero-vectors until you provide UniProt2Reactome.txt at one of: "
           + ", ".join(_REACTOME_PATH_CANDIDATES))
    ),
    requires=["reactome_data"],
    cost="trivial",
    compute=_compute if _path_seen else None,
    integrated=bool(_path_seen),
))
