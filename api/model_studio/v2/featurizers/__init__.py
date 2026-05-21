"""Featurizer registry — every named featurizer the Pipeline screen can
mix into a training run is registered here, with a uniform interface
(``compute(records) -> np.ndarray``) and explicit metadata.

The registry lives in two layers:

1.  ``FEATURIZERS``: a dict of ``{featurizer_id: FeaturizerSpec}`` where
    each spec describes the kind ("ligand"/"protein"/"interaction"),
    the output dimension, whether it requires external compute
    (download / GPU / RDKit), and the function that produces vectors.

2.  ``CATALOG``: the JSON-serialisable summary the GUI consumes via
    ``GET /api/v2/featurizers``. Lists every featurizer with a one-line
    description, expected dim, axis, and resource requirements.

A featurizer's ``compute`` callable receives a list of records (with
``uniprot``, ``sequence``, ``ligand_ref``, ``smiles`` fields) and
returns an ``(N, D)`` float32 numpy array. NaN-handling is the
featurizer's job; callers can assume the output is dense + finite.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Callable, Iterable

import numpy as np


@dataclass
class FeaturizerSpec:
    """Metadata + compute callable for one featurizer."""
    id: str
    label: str
    axis: str                    # "ligand" | "protein" | "interaction"
    dim: int                     # output dimension per record
    short_desc: str
    long_desc: str
    requires: list[str]          # tags: "rdkit" | "esm" | "transformers"
    cost: str                    # "trivial" | "fast" | "moderate" | "heavy"
    compute: Callable | None     # None if unavailable on this install
    integrated: bool             # if False, module imported but compute=None

    def to_catalog_entry(self) -> dict:
        d = asdict(self)
        d.pop("compute", None)
        return d


FEATURIZERS: dict[str, FeaturizerSpec] = {}


def register(spec: FeaturizerSpec) -> None:
    """Add a featurizer to the registry. Last write wins."""
    FEATURIZERS[spec.id] = spec


def get(featurizer_id: str) -> FeaturizerSpec | None:
    return FEATURIZERS.get(featurizer_id)


def list_by_axis(axis: str) -> list[FeaturizerSpec]:
    return [s for s in FEATURIZERS.values() if s.axis == axis]


def catalog() -> dict:
    """Serializable summary for /api/v2/featurizers."""
    items = [s.to_catalog_entry() for s in FEATURIZERS.values()]
    by_axis: dict[str, list[dict]] = {}
    for it in items:
        by_axis.setdefault(it["axis"], []).append(it)
    n_integrated = sum(1 for s in FEATURIZERS.values() if s.integrated)
    return {
        "n_featurizers": len(items),
        "n_integrated":  n_integrated,
        "by_axis":       by_axis,
        "items":         items,
    }


def compute_features(
    featurizer_ids: list[str],
    records: list,
) -> dict[str, np.ndarray]:
    """Compute named features for a record list. Returns
    ``{featurizer_id: (N, D)_array}``. Skips disabled featurizers
    (with a warning) so the caller can mix integrated + planned IDs.
    """
    out: dict[str, np.ndarray] = {}
    for fid in featurizer_ids:
        spec = FEATURIZERS.get(fid)
        if spec is None or spec.compute is None:
            continue
        try:
            arr = spec.compute(records)
            if not isinstance(arr, np.ndarray):
                arr = np.asarray(arr, dtype=np.float32)
            if arr.shape[0] != len(records):
                raise ValueError(
                    f"Featurizer '{fid}' returned {arr.shape[0]} rows, "
                    f"expected {len(records)}"
                )
            out[fid] = arr.astype(np.float32, copy=False)
        except Exception as exc:  # noqa: BLE001
            # Don't crash the whole run on one failed featurizer; surface
            # via an empty array + the caller can decide.
            out[fid] = np.zeros((len(records), spec.dim), dtype=np.float32)
            print(f"[featurizers] {fid} failed: {exc}", flush=True)
    return out


# ── Side-effect imports — each submodule self-registers via `register()` ──
from . import ligand_fingerprints  # noqa: F401, E402
from . import ligand_descriptors   # noqa: F401, E402
from . import ligand_3d            # noqa: F401, E402
from . import ligand_plm           # noqa: F401, E402
from . import protein_plm          # noqa: F401, E402
from . import protein_annotations  # noqa: F401, E402
from . import protein_structural   # noqa: F401, E402
from . import protein_pathways     # noqa: F401, E402  # Reactome pathway multi-hot
from . import protein_interface    # noqa: F401, E402  # v4: interface residues, hot-spot, biophys surrogate
# embeddings.py self-registers a "protein_esm2_650m_cache" entry via the
# register() call inside the module. Import it last so the package is
# fully loaded when its side-effect runs.
from .. import embeddings as _embeddings  # noqa: F401, E402


# ── Molecular-graph featurization re-export ─────────────────────────
# ``smiles_to_graph`` and ``ATOM_FEAT_DIM`` used to live in a sibling
# file ``api/model_studio/v2/featurizers.py`` that got shadowed once
# this package directory landed. They now live in
# ``featurizers/molecular_graph.py`` as a proper submodule and are
# re-exported here so ``from .featurizers import smiles_to_graph`` and
# ``from .featurizers import ATOM_FEAT_DIM`` keep working for
# ``models.py`` (GraphDTA / DrugBAN / StructGNN_DTA) and
# ``dataset_warehouse.py`` (WarehouseGraphDataset / WarehouseStructGraphDataset).
from .molecular_graph import (
    ATOM_FEAT_DIM,
    smiles_to_graph,
)  # noqa: F401, E402
