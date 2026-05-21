"""Register a v2 view over the legacy ChEMBL/BindingDB/PubChem SMILES corpus.

The legacy warehouse already materialised ``ligand_chemistry_signatures``
with 5.79 M rows, of which ~4.28 M are unique canonical SMILES across
ChEMBL (2.86 M), BindingDB (1.35 M), PubChem (1.31 M), ChEBI, PDB-CCD,
ZINC, KEGG, DrugBank, IUPHAR. **No duplicate ingest, no extra download.**

This module exposes that corpus as a v2 catalog view so training-time
SMILES lookups (for any external ligand id we encounter) can be answered
from local parquet without touching the legacy DuckDB at all.

Output views:
    v2_ligand_smiles_corpus      (read-only view over legacy parquet)
        ligand_ref          "<namespace>:<source_ligand_id>"  (lower-cased ns)
        ligand_namespace    "ChEMBL" | "BindingDB" | "PubChem" | ...
        canonical_smiles    canonical SMILES (RDKit-canonicalised by legacy)
        canonical_smiles_hash
        snapshot_id

This is a thin shim — the actual data lives at the legacy parquet path
and is read on-demand by DuckDB.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name


_LEGACY_PARQUET = Path(
    "D:/ProteoSphere/reference_library/partitions/ligand_chemistry_signatures/"
    "snapshot_id=hardened-ligand-chemistry-all-local-2026-04-24/"
    "ligand_chemistry_signatures.parquet"
)


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


def register_smiles_corpus() -> dict:
    """Create the ``v2_ligand_smiles_corpus`` view. Idempotent."""
    if not _LEGACY_PARQUET.exists():
        return {"error": f"missing legacy parquet: {_LEGACY_PARQUET}"}

    pq_path = str(_LEGACY_PARQUET).replace("\\", "/")
    view = _safe_view_name("v2", "ligand_smiles_corpus")
    v2 = _v2()
    try:
        v2.execute(f"DROP VIEW IF EXISTS {view}")
        v2.execute(f"DROP TABLE IF EXISTS {view}")
        v2.execute(
            f"""
            CREATE VIEW {view} AS
            SELECT
                ligand_ref,
                ligand_namespace,
                canonical_smiles,
                canonical_smiles_hash,
                snapshot_id
            FROM read_parquet('{pq_path}')
            WHERE canonical_smiles IS NOT NULL
            """
        )
        n = v2.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        by_ns = v2.execute(
            f"SELECT ligand_namespace, count(*), count(DISTINCT canonical_smiles_hash) "
            f"FROM {view} GROUP BY ligand_namespace ORDER BY count(*) DESC"
        ).fetchall()
    finally:
        v2.close()

    out_dir = INGEST_ROOT / "normalized" / "similarity_signatures" / "v2_ligand_smiles_corpus"
    out_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "view_name": view,
        "legacy_parquet": str(_LEGACY_PARQUET),
        "n_rows": int(n),
        "by_namespace": [
            {"namespace": r[0], "rows": int(r[1]), "unique_smiles": int(r[2])}
            for r in by_ns
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit
