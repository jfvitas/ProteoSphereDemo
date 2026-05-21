"""Motif / domain / family membership signatures.

The legacy ``motif_domain_site_annotations`` table holds 5.69 M domain
and motif annotations across these namespaces:

    InterPro            4,343,739
    Pfam                1,061,620
    Motivated Proteins    261,670
    MegaMotifBase          17,662
    ELM                     2,797

Most rows (5.66 M) are owned by ``structure_unit:pdb:<PDB>:<chain>:<acc>``
records (one annotation per PDB chain). 5,594 are owned by
``protein:<acc>`` directly. The protein UniProt is the last colon-separated
segment for structure_unit rows.

This module decomposes those owners to (uniprot, namespace, identifier)
triples, restricted to the v2 universe. Two proteins sharing an InterPro
or Pfam id are functional/structural analogues — a leakage edge for
DTI splits and a hard-positive signal for some training objectives.

Output (one parquet, registered as ``v2_motif_membership``):
    uniprot          UniProt accession
    source           v2 source that introduced the UniProt
    namespace        InterPro | Pfam | ELM | MegaMotifBase | Motivated Proteins
    identifier       e.g. "IPR017900"
    label            description (often present for Pfam/InterPro)
    snapshot_id

Edges (JIT):
    SELECT a.uniprot, b.uniprot, a.namespace, a.identifier
    FROM v2_motif_membership a JOIN v2_motif_membership b
      ON a.namespace = b.namespace AND a.identifier = b.identifier
     AND a.uniprot < b.uniprot
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name
from .bridges import LEGACY_CATALOG
from .sequence_signatures import collect_uniprot_universe


def _legacy() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(LEGACY_CATALOG), read_only=True)


def _v2() -> duckdb.DuckDBPyConnection:
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


def _extract_uniprot(owner_id: str, owner_record_type: str) -> str | None:
    """Pull the UniProt accession out of an owner_summary_id."""
    if not owner_id:
        return None
    parts = owner_id.split(":")
    if owner_record_type == "protein" and len(parts) >= 2:
        return parts[1]
    if owner_record_type == "structure_unit" and len(parts) >= 5:
        # structure_unit:pdb:<PDB>:<chain>:<acc>
        return parts[-1]
    return None


def build_motif_membership(snapshot_id: str | None = None) -> dict:
    """Single-pass over motif annotations: extract UniProt from owner_id
    via DuckDB string ops, then INNER JOIN against an in-memory v2
    universe table. Much faster than batched IN/LIKE filters.
    """
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    universe = collect_uniprot_universe()
    if not universe:
        return {"error": "no v2 UniProts; run bridges first"}

    legacy = _legacy()
    rows: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()
    stats = {
        "universe_size": len(universe),
        "uniprots_with_motif": 0,
        "total_memberships": 0,
        "by_namespace": {},
    }
    try:
        # Register v2 universe as an in-memory table on the legacy
        # connection so we can JOIN. DuckDB exposes register() for arrow /
        # pandas; here we just build a temp table from a VALUES literal
        # in chunks of 5K.
        legacy.execute("CREATE TEMP TABLE v2_universe (uniprot VARCHAR)")
        accs = list(universe.keys())
        for i in range(0, len(accs), 5_000):
            batch = accs[i:i + 5_000]
            legacy.executemany(
                "INSERT INTO v2_universe VALUES (?)",
                [(a,) for a in batch],
            )
        legacy.execute("CREATE INDEX idx_v2u ON v2_universe(uniprot)")

        # Extract UniProt from owner_summary_id and INNER JOIN universe.
        #   protein:<acc>                          → split[1]
        #   structure_unit:pdb:<PDB>:<chain>:<acc> → last segment
        # DuckDB regex_extract gives us a single rule.
        sql = """
            WITH owners AS (
              SELECT
                CASE owner_record_type
                  WHEN 'protein' THEN regexp_extract(owner_summary_id, '^protein:([^:]+)$', 1)
                  WHEN 'structure_unit' THEN regexp_extract(owner_summary_id, '([^:]+)$', 1)
                  ELSE NULL
                END AS uniprot,
                namespace, identifier, label
              FROM motif_domain_site_annotations
              WHERE owner_record_type IN ('protein','structure_unit')
                AND identifier IS NOT NULL
            )
            SELECT DISTINCT o.uniprot, o.namespace, o.identifier, o.label
            FROM owners o
            JOIN v2_universe u ON u.uniprot = o.uniprot
            WHERE o.uniprot IS NOT NULL AND o.uniprot <> ''
        """
        result_rows = legacy.execute(sql).fetchall()
        for acc, ns, ident, label in result_rows:
            sources = universe.get(acc, [])
            for src in sources or [None]:
                key = (acc, src or "", ns or "", ident)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "uniprot": acc,
                    "source": src,
                    "namespace": ns,
                    "identifier": ident,
                    "label": label or "",
                    "snapshot_id": snapshot_id,
                })

        stats["total_memberships"] = len(rows)
        stats["uniprots_with_motif"] = len({r["uniprot"] for r in rows})
        for r in rows:
            stats["by_namespace"][r["namespace"]] = stats["by_namespace"].get(r["namespace"], 0) + 1
    finally:
        legacy.close()

    out_dir = (INGEST_ROOT / "normalized" / "similarity_signatures"
               / "v2_motif_membership" / snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = _write_parquet(rows, out_dir / "membership.parquet")

    view_name = _safe_view_name("v2", "motif_membership")
    if rows:
        v2 = _v2()
        try:
            v2.execute(f"DROP VIEW IF EXISTS {view_name}")
            v2.execute(f"DROP TABLE IF EXISTS {view_name}")
            v2.execute(
                f"CREATE VIEW {view_name} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')"
            )
        finally:
            v2.close()

    audit = {
        "snapshot_id": snapshot_id,
        "output_path": str(out_path),
        "n_membership_rows": len(rows),
        "stats": stats,
        "view_name": view_name if rows else None,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


def _write_parquet(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_bytes(b"")
        return path
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        pq.write_table(pa.table(cols), path, compression="zstd")
        return path
    except Exception:
        jsonl = path.with_suffix(".jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        return jsonl
