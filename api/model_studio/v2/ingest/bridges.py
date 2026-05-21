"""Identity bridges — join new source IDs to canonical UniProt accessions.

The legacy warehouse (D:\\ProteoSphere\\reference_library\\catalog\\reference_library.duckdb)
already has a 953-million-row ``cross_references`` table covering Ensembl,
GeneID (Entrez), Gene_Name, HGNC, OrthoDB, STRING, PDB, RefSeq, KEGG, etc.

We do NOT duplicate those. We READ from them and emit thin per-source bridge
tables under E:\\.../normalized/bridges/<source>_uniprot/<snapshot>/bridge.parquet.

Each bridge maps the source's primary identifier to (potentially multiple)
canonical UniProt accessions, with provenance:
    source_id       e.g. "huri" | "hippie" | "davis"
    source_key      the source's identifier as stored in its parquet
    bridge_via      "Ensembl" | "GeneID" | "Gene_Name" | "HGNC" | "direct"
    uniprot         UniProt accession (the canonical anchor)
    confidence      "exact" | "ambiguous" (1 vs many UniProts for the key)
    snapshot_id

Catalog consolidation joins these into the existing reference_library
catalog at promotion time. Until then they live as v2 parquet + DuckDB views.

Why this is small:
    HuRI:   52,068 unique Ensembl genes → ~50K bridge rows
    HIPPIE: ~30K unique Entrez gene IDs → ~30K bridge rows
    Davis:  442 kinase symbols          → ~442 bridge rows
    KIBA:   229 UniProt IDs (already direct) → 229 bridge rows
    gtopdb: 2,581 UniProt accessions (direct) + 7,147 ChEMBL ligand xrefs
    3did:   Pfam keys are already canonical — no bridge needed
    Total ~95K rows × 64 bytes ≈ 6 MB on disk.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

from .state import INGEST_ROOT
from .catalog import _CATALOG_PATH, _safe_view_name


LEGACY_CATALOG = Path("D:/ProteoSphere/reference_library/catalog/reference_library.duckdb")


def _legacy() -> duckdb.DuckDBPyConnection:
    """Read-only handle to the legacy warehouse. Reused per call."""
    if not LEGACY_CATALOG.exists():
        raise FileNotFoundError(
            f"Legacy warehouse not found at {LEGACY_CATALOG}. "
            "Bridges can't resolve without it."
        )
    return duckdb.connect(str(LEGACY_CATALOG), read_only=True)


def _v2() -> duckdb.DuckDBPyConnection:
    """Read/write handle to the v2 catalog. Same one catalog.py uses."""
    _CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(_CATALOG_PATH))


# ── Per-source bridge builders ────────────────────────────────────────

def bridge_huri(snapshot_id: str | None = None) -> dict:
    """HuRI uses unversioned Ensembl gene ids (ENSG00000123456).
    Legacy stores versioned form (ENSG00000123456.5); we strip the version
    on the legacy side for the join."""
    return _build_bridge(
        source_id="huri",
        select_keys_sql="""
            SELECT DISTINCT a_ensembl_gene AS source_key FROM huri_interactions
            UNION
            SELECT DISTINCT b_ensembl_gene FROM huri_interactions
        """,
        bridge_via="Ensembl",
        legacy_filter="database = 'Ensembl'",
        legacy_key_expr="split_part(external_id, '.', 1)",   # strip version
        snapshot_id=snapshot_id,
    )


def bridge_hippie(snapshot_id: str | None = None) -> dict:
    """HIPPIE primary key is Entrez gene → cross_references.database='GeneID'."""
    return _build_bridge(
        source_id="hippie",
        select_keys_sql="""
            SELECT DISTINCT a_entrez_gene AS source_key FROM hippie_interactions
            UNION
            SELECT DISTINCT b_entrez_gene FROM hippie_interactions
        """,
        bridge_via="GeneID",
        legacy_filter="database = 'GeneID'",
        snapshot_id=snapshot_id,
    )


def bridge_davis(snapshot_id: str | None = None) -> dict:
    """Davis kinase gene symbols → HUMAN Swiss-Prot UniProt only.

    HGNC in legacy is keyed by numeric `HGNC:NNNN`, not symbols. Gene_Name
    matches symbols but yields cross-species TrEMBL hits (e.g. AAK1 → many
    A0A* TrEMBL entries from other organisms). We restrict to:
        * taxon_id = 9606 (human, via proteins table join)
        * accession NOT LIKE 'A0A%' (Swiss-Prot reviewed, not TrEMBL)
    """
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    v2 = _v2()
    try:
        keys = [r[0] for r in v2.execute(
            "SELECT DISTINCT protein_key FROM davis_proteins"
        ).fetchall() if r[0]]
    finally:
        v2.close()
    if not keys:
        return _emit_bridge("davis", [], snapshot_id)
    legacy = _legacy()
    out_rows: list[dict] = []
    try:
        from collections import defaultdict
        group: dict[str, set[str]] = defaultdict(set)
        BATCH = 50_000
        for start in range(0, len(keys), BATCH):
            batch = keys[start:start + BATCH]
            placeholders = ", ".join(["?"] * len(batch))
            sql = (
                "SELECT xr.external_id AS source_key, xr.accession AS uniprot "
                "FROM cross_references xr "
                "JOIN proteins p ON p.accession = xr.accession "
                "WHERE xr.database = 'Gene_Name' "
                "  AND p.taxon_id = 9606 "
                "  AND NOT xr.accession LIKE 'A0A%' "
                f" AND xr.external_id IN ({placeholders})"
            )
            for sk, up in legacy.execute(sql, batch).fetchall():
                group[sk].add(up)
        for sk in keys:
            uniprots = group.get(sk, set())
            if not uniprots:
                out_rows.append({"source_id": "davis", "source_key": sk,
                                  "bridge_via": "Gene_Name+taxon9606+SwissProt",
                                  "uniprot": None, "confidence": "unresolved",
                                  "snapshot_id": snapshot_id})
            elif len(uniprots) == 1:
                (up,) = uniprots
                out_rows.append({"source_id": "davis", "source_key": sk,
                                  "bridge_via": "Gene_Name+taxon9606+SwissProt",
                                  "uniprot": up, "confidence": "exact",
                                  "snapshot_id": snapshot_id})
            else:
                for up in sorted(uniprots):
                    out_rows.append({"source_id": "davis", "source_key": sk,
                                      "bridge_via": "Gene_Name+taxon9606+SwissProt",
                                      "uniprot": up, "confidence": "ambiguous",
                                      "snapshot_id": snapshot_id})
    finally:
        legacy.close()
    return _emit_bridge("davis", out_rows, snapshot_id)


def bridge_kiba(snapshot_id: str | None = None) -> dict:
    """KIBA uses UniProt keys already — direct bridge with no legacy join."""
    v2 = _v2()
    try:
        keys = [r[0] for r in v2.execute(
            "SELECT DISTINCT protein_key FROM kiba_proteins"
        ).fetchall()]
    finally:
        v2.close()
    # Direct: source_key == uniprot, no lookup
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rows = [
        {"source_id": "kiba", "source_key": k, "bridge_via": "direct",
         "uniprot": k, "confidence": "exact", "snapshot_id": snapshot_id}
        for k in keys if k
    ]
    return _emit_bridge("kiba", rows, snapshot_id)


def bridge_gtopdb(snapshot_id: str | None = None) -> dict:
    """gtopdb already stores UniProt directly in its `uniprot` column.
    Bridge is identity; we emit a row per unique non-null UniProt for audit."""
    v2 = _v2()
    try:
        keys = [r[0] for r in v2.execute(
            "SELECT DISTINCT uniprot FROM gtopdb_interactions WHERE uniprot IS NOT NULL"
        ).fetchall()]
    finally:
        v2.close()
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    rows = [
        {"source_id": "gtopdb", "source_key": k, "bridge_via": "direct",
         "uniprot": k, "confidence": "exact", "snapshot_id": snapshot_id}
        for k in keys
    ]
    return _emit_bridge("gtopdb", rows, snapshot_id)


def bridge_threedid(snapshot_id: str | None = None) -> dict:
    """3did identifies via Pfam (PF<digits>.<version>). Legacy warehouse's
    `protein_family_index` already maps Pfam → UniProt at scale.
    We emit a bridge of just (Pfam → exists in legacy?) to flag the join."""
    v2 = _v2()
    try:
        pfams = [r[0] for r in v2.execute("""
            SELECT DISTINCT pfam_a_root AS pf FROM s_3did_domain_pairs
            UNION
            SELECT DISTINCT pfam_b_root FROM s_3did_domain_pairs
        """).fetchall()]
    finally:
        v2.close()
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    # Probe legacy for each Pfam — we don't materialize the full UniProt list
    # (some Pfams have 100K+ members), just record member count + provenance
    # so training-time materialisation knows what to pull JIT.
    legacy = _legacy()
    try:
        # Single batched query — ~20K Pfams, IN-list fits in DuckDB.
        from collections import defaultdict
        counts: dict[str, int] = defaultdict(int)
        BATCH = 5_000
        for start in range(0, len(pfams), BATCH):
            batch = pfams[start:start + BATCH]
            placeholders = ", ".join(["?"] * len(batch))
            sql = (
                "SELECT identifier, count(*) AS n "
                f"FROM protein_family_index "
                f"WHERE namespace = 'Pfam' AND identifier IN ({placeholders}) "
                "GROUP BY identifier"
            )
            for pf, n in legacy.execute(sql, batch).fetchall():
                counts[pf] = int(n)
        rows = [{
            "source_id": "3did",
            "source_key": pf,
            "bridge_via": "Pfam",
            "uniprot": None,                       # set-membership; resolved JIT
            "confidence": "exact" if counts.get(pf, 0) > 0 else "unresolved",
            "n_legacy_members": counts.get(pf, 0),
            "snapshot_id": snapshot_id,
        } for pf in pfams]
    finally:
        legacy.close()
    return _emit_bridge("3did", rows, snapshot_id)


# ── Generic builder ───────────────────────────────────────────────────

def _build_bridge(*, source_id: str, select_keys_sql: str,
                  bridge_via: str, legacy_filter: str,
                  legacy_key_expr: str = "external_id",
                  snapshot_id: str | None = None) -> dict:
    """Generic 2-step bridge: collect distinct source keys from v2, then
    join each to legacy cross_references where database == bridge_via.

    `legacy_key_expr` lets the caller specify a transform applied to the
    legacy column for the join — e.g. ``split_part(external_id, '.', 1)``
    strips version suffixes off Ensembl IDs before matching.
    """
    snapshot_id = snapshot_id or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    v2 = _v2()
    try:
        keys = [r[0] for r in v2.execute(select_keys_sql).fetchall() if r[0]]
    finally:
        v2.close()
    if not keys:
        return _emit_bridge(source_id, [], snapshot_id)
    # Resolve in batches against the legacy warehouse
    legacy = _legacy()
    out_rows: list[dict] = []
    try:
        # Single big IN-list query — DuckDB happily ingests ~100K-element lists.
        # For bigger sources we'd batch in chunks of 50K.
        BATCH = 50_000
        for start in range(0, len(keys), BATCH):
            batch = keys[start:start + BATCH]
            # Use a parameter list with DuckDB's array-IN.
            placeholders = ", ".join(["?"] * len(batch))
            sql = (
                f"SELECT {legacy_key_expr} AS source_key, accession AS uniprot "
                f"FROM cross_references "
                f"WHERE {legacy_filter} AND {legacy_key_expr} IN ({placeholders})"
            )
            matches = legacy.execute(sql, batch).fetchall()
            # Group by source_key to detect ambiguity (one key → many UniProts)
            from collections import defaultdict
            group: dict[str, set[str]] = defaultdict(set)
            for sk, up in matches:
                group[sk].add(up)
            for sk in batch:
                uniprots = group.get(sk, set())
                if not uniprots:
                    out_rows.append({
                        "source_id": source_id, "source_key": sk,
                        "bridge_via": bridge_via, "uniprot": None,
                        "confidence": "unresolved",
                        "snapshot_id": snapshot_id,
                    })
                elif len(uniprots) == 1:
                    (up,) = uniprots
                    out_rows.append({
                        "source_id": source_id, "source_key": sk,
                        "bridge_via": bridge_via, "uniprot": up,
                        "confidence": "exact",
                        "snapshot_id": snapshot_id,
                    })
                else:
                    # Ambiguous: emit one row per UniProt with `ambiguous` flag
                    for up in sorted(uniprots):
                        out_rows.append({
                            "source_id": source_id, "source_key": sk,
                            "bridge_via": bridge_via, "uniprot": up,
                            "confidence": "ambiguous",
                            "snapshot_id": snapshot_id,
                        })
    finally:
        legacy.close()
    return _emit_bridge(source_id, out_rows, snapshot_id)


# ── Writer + catalog registration ─────────────────────────────────────

def _emit_bridge(source_id: str, rows: list[dict], snapshot_id: str) -> dict:
    """Write parquet (or jsonl fallback) and register as a v2 catalog view."""
    out_dir = INGEST_ROOT / "normalized" / "bridges" / f"{source_id}_uniprot" / snapshot_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "bridge.parquet"
    if rows:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
            keys = list(rows[0].keys())
            cols: dict[str, list] = {k: [] for k in keys}
            for r in rows:
                for k in keys:
                    cols[k].append(r.get(k))
            pq.write_table(pa.table(cols), out_path, compression="zstd")
        except Exception:
            out_path = out_dir / "bridge.jsonl"
            with open(out_path, "w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    else:
        out_path.write_bytes(b"")
    # Register a DuckDB view named "<source>_bridge_uniprot"
    view_name = _safe_view_name(source_id, "bridge_uniprot")
    if rows:
        v2 = _v2()
        try:
            v2.execute(f"DROP VIEW IF EXISTS {view_name}")
            v2.execute(f"DROP TABLE IF EXISTS {view_name}")
            v2.execute(f"CREATE VIEW {view_name} AS SELECT * FROM read_parquet('{str(out_path).replace(chr(92), '/')}')")
        finally:
            v2.close()
    # Audit summary
    n = len(rows)
    n_resolved = sum(1 for r in rows if r.get("confidence") == "exact")
    n_ambig = sum(1 for r in rows if r.get("confidence") == "ambiguous")
    n_unres = sum(1 for r in rows if r.get("confidence") == "unresolved")
    audit = {
        "source_id": source_id,
        "snapshot_id": snapshot_id,
        "n_rows": n,
        "n_keys_exact": n_resolved,
        "n_keys_ambiguous": n_ambig,
        "n_keys_unresolved": n_unres,
        "output_path": str(out_path),
        "view_name": view_name if rows else None,
    }
    (out_dir / "manifest.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit


# ── One-shot driver ───────────────────────────────────────────────────

ALL_BRIDGE_BUILDERS = {
    "huri":   bridge_huri,
    "hippie": bridge_hippie,
    "davis":  bridge_davis,
    "kiba":   bridge_kiba,
    "gtopdb": bridge_gtopdb,
    "3did":   bridge_threedid,
}


def build_all(snapshot_id: str | None = None) -> list[dict]:
    out = []
    for sid, fn in ALL_BRIDGE_BUILDERS.items():
        try:
            audit = fn(snapshot_id=snapshot_id)
            out.append(audit)
        except Exception as exc:
            out.append({
                "source_id": sid,
                "error": f"{type(exc).__name__}: {exc}",
            })
    return out
