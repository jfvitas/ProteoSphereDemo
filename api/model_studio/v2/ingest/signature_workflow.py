"""One-shot driver that rebuilds every v2 cross-relationship signature.

Run order matters: ligand fingerprints first (scaffolds depend on them),
then everything else. Sequence materialisation is optional + slow, so it
is gated behind a flag.

Idempotent: each builder writes to a fresh timestamped snapshot folder
and re-points the v2 catalog view. Old snapshots are kept for audit.

Usage:
    from api.model_studio.v2.ingest.signature_workflow import rebuild_all
    rebuild_all(materialise_sequences=False)
"""

from __future__ import annotations

import time
import traceback
from typing import Callable


def _run(step_name: str, fn: Callable[[], dict]) -> dict:
    t0 = time.time()
    try:
        out = fn()
        out = dict(out) if isinstance(out, dict) else {"result": out}
        out["_step"] = step_name
        out["_elapsed_s"] = round(time.time() - t0, 2)
        out["_ok"] = "error" not in out
        return out
    except Exception as exc:
        return {
            "_step": step_name,
            "_elapsed_s": round(time.time() - t0, 2),
            "_ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=3),
        }


def rebuild_all(*,
                materialise_sequences: bool = False,
                max_uniprots: int | None = None) -> dict:
    """Rebuild every v2 signature in dependency order.

    Args:
        materialise_sequences: also fetch Swiss-Prot AA strings via REST.
                              Adds ~5 min wall time + ~12 MB on E:.
        max_uniprots: cap for sequence fetch (smoke tests).
    """
    from . import (
        signatures,
        sequence_signatures,
        ortholog_signatures,
        ec_signatures,
        motif_signatures,
        scaffold_signatures,
        legacy_smiles,
    )

    steps: list[dict] = []
    # 1. Ligand fingerprints (canonical SMILES + ECFP4 on-bit lists)
    steps.append(_run("fingerprints", signatures.compute_fingerprints))
    # 2. Ligand Tanimoto edges (depends on fingerprints)
    steps.append(_run("tanimoto_edges", signatures.compute_tanimoto_edges))
    # 3. Bemis-Murcko scaffolds (depends on fingerprints view)
    steps.append(_run("scaffolds", scaffold_signatures.build_scaffolds))
    # 4. Protein-side signatures (depend on bridge_uniprot views)
    steps.append(_run("uniref_membership", sequence_signatures.build_membership))
    steps.append(_run("ortholog_membership", ortholog_signatures.build_ortholog_membership))
    steps.append(_run("ec_membership", ec_signatures.build_ec_membership))
    steps.append(_run("motif_membership", motif_signatures.build_motif_membership))
    # 5. Legacy SMILES corpus view (zero-copy)
    steps.append(_run("legacy_smiles_view", legacy_smiles.register_smiles_corpus))

    if materialise_sequences:
        from . import sequence_materialize
        steps.append(_run(
            "protein_sequences",
            lambda: sequence_materialize.materialise_sequences(
                max_uniprots=max_uniprots,
            ),
        ))

    return {
        "ok": all(s.get("_ok") for s in steps),
        "n_steps": len(steps),
        "total_elapsed_s": round(sum(s.get("_elapsed_s", 0) for s in steps), 2),
        "steps": steps,
    }
