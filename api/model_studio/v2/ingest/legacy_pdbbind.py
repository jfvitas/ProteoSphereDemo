"""Expose legacy PDBbind data as a v2 catalog view (zero-copy).

The legacy warehouse holds PDBbind interactions in
``D:\\ProteoSphere\\reference_library\\partitions\\protein_ligand_edges\\``
as a parquet partition. All 24,804 rows are PDBbind co-crystal complexes
with binding measurements. We don't re-ingest — same pattern as the
ChEMBL/BindingDB SMILES corpus: just register a read-only view over the
parquet so the v2 GUI's "live" overlay can attribute rows to PDBbind on
the Dataset screen.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name


_LEGACY_PARQUET = Path(
    "D:/ProteoSphere/reference_library/partitions/protein_ligand_edges/"
    "snapshot_id=full-local-backbone-2026-04-10/protein_ligand_edges.parquet"
)


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


def register_pdbbind_view() -> dict:
    """Create ``pdbbind_interactions`` view in the v2 catalog. Idempotent."""
    if not _LEGACY_PARQUET.exists():
        return {"error": f"missing legacy parquet: {_LEGACY_PARQUET}"}

    pq_path = str(_LEGACY_PARQUET).replace("\\", "/")
    view = _safe_view_name("pdbbind", "interactions")
    v2 = _v2()
    try:
        v2.execute(f"DROP VIEW IF EXISTS {view}")
        v2.execute(f"DROP TABLE IF EXISTS {view}")
        v2.execute(
            f"""
            CREATE VIEW {view} AS
            SELECT
                edge_id,
                structure_id  AS pdb_id,
                protein_ref,
                ligand_ref,
                binding_measurement_raw,
                complex_type,
                commentary,
                reference_file,
                snapshot_id
            FROM read_parquet('{pq_path}')
            """
        )
        n = v2.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        sample_complex_types = v2.execute(
            f"SELECT complex_type, count(*) FROM {view} GROUP BY complex_type ORDER BY count(*) DESC LIMIT 5"
        ).fetchall()
    finally:
        v2.close()

    out_dir = INGEST_ROOT / "normalized" / "legacy_views" / "pdbbind"
    out_dir.mkdir(parents=True, exist_ok=True)
    audit = {
        "view_name":      view,
        "legacy_parquet": str(_LEGACY_PARQUET),
        "n_rows":         int(n),
        "complex_types":  [{"type": ct, "n": int(c)} for ct, c in sample_complex_types],
        "notes":          ("Zero-copy view; PDBbind data lives in legacy warehouse "
                           "but is queryable from v2 catalog. Structures are JIT-fetched "
                           "from PDBe at training time, not stored in the warehouse."),
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit
