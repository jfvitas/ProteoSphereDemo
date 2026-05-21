"""Cross-relationship leakage report for the Splits screen.

Reads the v2 catalog's signature views (sequence, ortholog, EC, motif,
Tanimoto, scaffold) and returns counts of leakage-creating relationships
across the v2 sources. Edges are NOT materialised — we use SQL self-joins
and aggregate row counts only.

Output shape (consumed by gui/model_studio_web_v2/screen-split.jsx):

    {
      "snapshot_at": "2026-05-15T15:30:00Z",
      "universe": { "n_uniprots": 57397, "n_ligands": 13540 },
      "relationships": [
        { "kind": "uniref50",     "edges_total": N, "edges_cross_source": N, "by_source_touch": {...} },
        { "kind": "uniref90",     ... },
        { "kind": "orthodb",      ... },
        { "kind": "ec3",          ... },
        { "kind": "interpro",     ... },
        { "kind": "pfam",         ... },
        { "kind": "tanimoto_0.4", ... },
        { "kind": "scaffold",     ... },
      ],
      "top_groups": [
        { "kind": "interpro", "id": "IPR017900", "label": "...", "n_uniprots": 42, "sources_touched": ["davis","kiba"] },
        ...
      ]
    }

The Splits screen consumes `relationships` for the leakage-group table
and `top_groups` for the "biggest hotspots" panel.
"""

from __future__ import annotations

import time
from typing import Any

import duckdb

from .catalog import _CATALOG_PATH, _safe_view_name


def _v2_ro() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(_CATALOG_PATH), read_only=True)


def _safe_count(con, sql: str, params: tuple = ()) -> int:
    try:
        r = con.execute(sql, params).fetchone()
        return int(r[0]) if r and r[0] is not None else 0
    except duckdb.Error:
        return 0


def _per_source_touch(con, view: str, join_key: str,
                      extra_where: str = "") -> dict[str, int]:
    """For each v2 source, count edges where either endpoint has that source."""
    out: dict[str, int] = {}
    where = f" AND {extra_where}" if extra_where else ""
    for src in ("gtopdb", "davis", "kiba", "huri", "hippie"):
        n = _safe_count(
            con,
            f"SELECT count(*) FROM {view} a JOIN {view} b "
            f"ON a.{join_key} = b.{join_key} AND a.uniprot < b.uniprot{where} "
            f"AND (a.source = ? OR b.source = ?)",
            (src, src),
        )
        out[src] = n
    return out


def _per_source_touch_ligand(con, view: str, join_key: str,
                             extra_where: str = "") -> dict[str, int]:
    """Same as _per_source_touch but for ligand entities (different PK)."""
    out: dict[str, int] = {}
    where = f" AND {extra_where}" if extra_where else ""
    for src in ("gtopdb", "davis", "kiba"):
        n = _safe_count(
            con,
            f"SELECT count(*) FROM {view} a JOIN {view} b "
            f"ON a.{join_key} = b.{join_key} AND a.ligand_ref < b.ligand_ref{where} "
            f"AND (a.source = ? OR b.source = ?)",
            (src, src),
        )
        out[src] = n
    return out


def leakage_report() -> dict[str, Any]:
    """Single read-only sweep over the signature views. ~5s on the
    current 57K-protein / 13K-ligand v2 warehouse."""
    seq_v   = _safe_view_name("v2", "sequence_cluster_membership")
    orth_v  = _safe_view_name("v2", "ortholog_cluster_membership")
    ec_v    = _safe_view_name("v2", "ec_class_membership")
    motif_v = _safe_view_name("v2", "motif_membership")
    tan_v   = _safe_view_name("v2", "ligand_tanimoto_edges")
    scaf_v  = _safe_view_name("v2", "ligand_scaffolds")

    con = _v2_ro()
    try:
        n_uniprots = _safe_count(
            con, f"SELECT count(DISTINCT uniprot) FROM {seq_v}")
        n_ligands = _safe_count(
            con, f"SELECT count(DISTINCT ligand_ref) FROM {scaf_v}")

        relationships: list[dict[str, Any]] = []

        # Sequence identity: uniref50/90/100
        for thresh in ("uniref50", "uniref90", "uniref100"):
            total = _safe_count(
                con,
                f"SELECT count(*) FROM {seq_v} a JOIN {seq_v} b "
                f"ON a.{thresh} = b.{thresh} AND a.uniprot < b.uniprot "
                f"AND a.{thresh} IS NOT NULL",
            )
            cross = _safe_count(
                con,
                f"SELECT count(*) FROM {seq_v} a JOIN {seq_v} b "
                f"ON a.{thresh} = b.{thresh} AND a.uniprot < b.uniprot "
                f"AND a.{thresh} IS NOT NULL AND a.source <> b.source",
            )
            relationships.append({
                "kind": thresh,
                "axis": "protein",
                "edges_total": total,
                "edges_cross_source": cross,
                "by_source_touch": _per_source_touch(con, seq_v, thresh,
                                                     f"a.{thresh} IS NOT NULL"),
            })

        # OrthoDB
        total = _safe_count(
            con,
            f"SELECT count(*) FROM {orth_v} a JOIN {orth_v} b "
            f"ON a.orthodb_cluster = b.orthodb_cluster AND a.uniprot < b.uniprot",
        )
        cross = _safe_count(
            con,
            f"SELECT count(*) FROM {orth_v} a JOIN {orth_v} b "
            f"ON a.orthodb_cluster = b.orthodb_cluster AND a.uniprot < b.uniprot "
            f"AND a.source <> b.source",
        )
        relationships.append({
            "kind": "orthodb",
            "axis": "protein",
            "edges_total": total,
            "edges_cross_source": cross,
            "by_source_touch": _per_source_touch(con, orth_v, "orthodb_cluster"),
        })

        # EC class — pick ec3 (sub-class level) as the default Splits granularity
        total = _safe_count(
            con,
            f"SELECT count(*) FROM {ec_v} a JOIN {ec_v} b "
            f"ON a.ec3 = b.ec3 AND a.uniprot < b.uniprot AND a.ec3 IS NOT NULL",
        )
        cross = _safe_count(
            con,
            f"SELECT count(*) FROM {ec_v} a JOIN {ec_v} b "
            f"ON a.ec3 = b.ec3 AND a.uniprot < b.uniprot AND a.ec3 IS NOT NULL "
            f"AND a.source <> b.source",
        )
        relationships.append({
            "kind": "ec3",
            "axis": "protein",
            "edges_total": total,
            "edges_cross_source": cross,
            "by_source_touch": _per_source_touch(con, ec_v, "ec3",
                                                 "a.ec3 IS NOT NULL"),
        })

        # Motif namespaces — InterPro + Pfam as separate signals
        for ns in ("InterPro", "Pfam"):
            kind = ns.lower()
            ns_filter = f"a.namespace = '{ns}' AND b.namespace = '{ns}'"
            total = _safe_count(
                con,
                f"SELECT count(*) FROM {motif_v} a JOIN {motif_v} b "
                f"ON a.identifier = b.identifier AND a.uniprot < b.uniprot "
                f"AND {ns_filter}",
            )
            cross = _safe_count(
                con,
                f"SELECT count(*) FROM {motif_v} a JOIN {motif_v} b "
                f"ON a.identifier = b.identifier AND a.uniprot < b.uniprot "
                f"AND {ns_filter} AND a.source <> b.source",
            )
            relationships.append({
                "kind": kind,
                "axis": "protein",
                "edges_total": total,
                "edges_cross_source": cross,
                "by_source_touch": _per_source_touch(
                    con, motif_v, "identifier", ns_filter),
            })

        # Tanimoto edges (already materialised)
        total = _safe_count(con, f"SELECT count(*) FROM {tan_v}")
        # Cross-source on tanimoto edges via ligand_ref source prefix
        # ligand_ref format is "ligand:<source>:<id>"
        cross = _safe_count(
            con,
            f"SELECT count(*) FROM {tan_v} "
            f"WHERE split_part(a_ref, ':', 2) <> split_part(b_ref, ':', 2)",
        )
        # Per-source touch
        ligand_touch: dict[str, int] = {}
        for src in ("gtopdb", "davis", "kiba"):
            n = _safe_count(
                con,
                f"SELECT count(*) FROM {tan_v} "
                f"WHERE split_part(a_ref, ':', 2) = ? OR split_part(b_ref, ':', 2) = ?",
                (src, src),
            )
            ligand_touch[src] = n
        relationships.append({
            "kind": "tanimoto_0.4",
            "axis": "ligand",
            "edges_total": total,
            "edges_cross_source": cross,
            "by_source_touch": ligand_touch,
        })

        # Bemis-Murcko scaffolds — same-scaffold ligand pairs, exclude acyclic
        total = _safe_count(
            con,
            f"SELECT count(*) FROM {scaf_v} a JOIN {scaf_v} b "
            f"ON a.scaffold_id = b.scaffold_id AND a.ligand_ref < b.ligand_ref "
            f"WHERE a.scaffold_smiles <> 'acyclic'",
        )
        cross = _safe_count(
            con,
            f"SELECT count(*) FROM {scaf_v} a JOIN {scaf_v} b "
            f"ON a.scaffold_id = b.scaffold_id AND a.ligand_ref < b.ligand_ref "
            f"WHERE a.scaffold_smiles <> 'acyclic' AND a.source <> b.source",
        )
        relationships.append({
            "kind": "scaffold",
            "axis": "ligand",
            "edges_total": total,
            "edges_cross_source": cross,
            "by_source_touch": _per_source_touch_ligand(
                con, scaf_v, "scaffold_id", "a.scaffold_smiles <> 'acyclic'"),
        })

        # Top groups (largest leakage hotspots) across protein-axis signatures
        top_groups: list[dict[str, Any]] = []
        # InterPro hotspots
        try:
            rows = con.execute(
                f"SELECT identifier, MAX(label) AS label, "
                f"  count(DISTINCT uniprot) AS n_u, "
                f"  list_distinct(list(source)) AS srcs "
                f"FROM {motif_v} WHERE namespace = 'InterPro' "
                f"GROUP BY identifier ORDER BY n_u DESC LIMIT 20"
            ).fetchall()
            for ident, label, n_u, srcs in rows:
                top_groups.append({
                    "kind": "interpro",
                    "id": ident,
                    "label": label or "",
                    "n_uniprots": int(n_u),
                    "sources_touched": [s for s in srcs if s],
                })
        except duckdb.Error:
            pass
        # UniRef50 hotspots
        try:
            rows = con.execute(
                f"SELECT uniref50, count(DISTINCT uniprot) AS n_u, "
                f"  list_distinct(list(source)) AS srcs "
                f"FROM {seq_v} WHERE uniref50 IS NOT NULL "
                f"GROUP BY uniref50 HAVING n_u >= 2 "
                f"ORDER BY n_u DESC LIMIT 10"
            ).fetchall()
            for cluster, n_u, srcs in rows:
                top_groups.append({
                    "kind": "uniref50",
                    "id": cluster,
                    "label": "",
                    "n_uniprots": int(n_u),
                    "sources_touched": [s for s in srcs if s],
                })
        except duckdb.Error:
            pass
        # OrthoDB hotspots
        try:
            rows = con.execute(
                f"SELECT orthodb_cluster, count(DISTINCT uniprot) AS n_u, "
                f"  list_distinct(list(source)) AS srcs "
                f"FROM {orth_v} GROUP BY orthodb_cluster "
                f"HAVING n_u >= 2 ORDER BY n_u DESC LIMIT 10"
            ).fetchall()
            for cluster, n_u, srcs in rows:
                top_groups.append({
                    "kind": "orthodb",
                    "id": cluster,
                    "label": "",
                    "n_uniprots": int(n_u),
                    "sources_touched": [s for s in srcs if s],
                })
        except duckdb.Error:
            pass

        top_groups.sort(key=lambda g: g["n_uniprots"], reverse=True)
        top_groups = top_groups[:25]
    finally:
        con.close()

    return {
        "snapshot_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "universe": {"n_uniprots": n_uniprots, "n_ligands": n_ligands},
        "relationships": relationships,
        "top_groups": top_groups,
    }
