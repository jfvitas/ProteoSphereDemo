"""Protein annotation featurizers — multi-hot encodings of warehouse
signature memberships.

These cost zero external compute: every signature view in the v2
catalog is already JIT-queryable. We just build a stable vocabulary
once per featurizer and emit indicator vectors.

Featurizers:

    protein_pfam_topk         Multi-hot Pfam family membership (top-K Pfam ids
                              by frequency in the v2 universe).
    protein_interpro_topk     Multi-hot InterPro family membership.
    protein_ec_class_one_hot  Multi-hot EC class membership at ec3 granularity.
    protein_orthodb_topk      Multi-hot OrthoDB cluster membership (top-K).
    protein_uniref50_id       Single-categorical UniRef50 cluster id
                              (one-hot, fits in 4096 dims even for the v2
                              universe).

The top-K cut keeps the vector dense enough to be useful for MLPs +
GBM. K is chosen so the vector stays under 1024 dims by default.
"""

from __future__ import annotations

import numpy as np
import duckdb

from . import register, FeaturizerSpec
from ..ingest.catalog import _CATALOG_PATH


# ── Helpers ────────────────────────────────────────────────────────────

def _v2_ro() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(_CATALOG_PATH), read_only=True)


def _topk_vocab(view: str, key_col: str, k: int, namespace_filter: str | None = None) -> dict[str, int]:
    """Returns {key: index} for the top-K most frequent values."""
    con = _v2_ro()
    try:
        where = ""
        if namespace_filter:
            where = f"WHERE namespace = '{namespace_filter}'"
        sql = (f"SELECT {key_col}, count(DISTINCT uniprot) AS n FROM {view} "
               f"{where} GROUP BY {key_col} ORDER BY n DESC LIMIT {k}")
        rows = con.execute(sql).fetchall()
    except duckdb.Error:
        rows = []
    finally:
        con.close()
    return {row[0]: i for i, row in enumerate(rows) if row[0]}


def _membership_map(view: str, key_col: str, namespace_filter: str | None = None) -> dict[str, set[str]]:
    """Returns {uniprot: {keys}} from a membership view."""
    con = _v2_ro()
    try:
        where = ""
        if namespace_filter:
            where = f"WHERE namespace = '{namespace_filter}'"
        rows = con.execute(
            f"SELECT uniprot, {key_col} FROM {view} {where}"
        ).fetchall()
    except duckdb.Error:
        rows = []
    finally:
        con.close()
    out: dict[str, set[str]] = {}
    for u, k in rows:
        if u and k:
            out.setdefault(u, set()).add(k)
    return out


# ── Featurizer factories ───────────────────────────────────────────────

# Vocab + membership cache lazy-loaded on first use. Re-loaded if
# the catalog path changes.
_cache: dict[str, dict] = {}


def _ensure_pfam(k: int = 512):
    key = f"pfam_{k}"
    if key not in _cache:
        vocab = _topk_vocab("v2_motif_membership", "identifier", k,
                            namespace_filter="Pfam")
        membership = _membership_map("v2_motif_membership", "identifier",
                                     namespace_filter="Pfam")
        _cache[key] = {"vocab": vocab, "membership": membership}
    return _cache[key]


def _ensure_interpro(k: int = 1024):
    key = f"interpro_{k}"
    if key not in _cache:
        vocab = _topk_vocab("v2_motif_membership", "identifier", k,
                            namespace_filter="InterPro")
        membership = _membership_map("v2_motif_membership", "identifier",
                                     namespace_filter="InterPro")
        _cache[key] = {"vocab": vocab, "membership": membership}
    return _cache[key]


def _ensure_ec3():
    key = "ec3"
    if key not in _cache:
        con = _v2_ro()
        try:
            rows = con.execute(
                "SELECT DISTINCT ec3 FROM v2_ec_class_membership "
                "WHERE ec3 IS NOT NULL ORDER BY ec3"
            ).fetchall()
            membership_rows = con.execute(
                "SELECT uniprot, ec3 FROM v2_ec_class_membership WHERE ec3 IS NOT NULL"
            ).fetchall()
        finally:
            con.close()
        vocab = {r[0]: i for i, r in enumerate(rows)}
        membership: dict[str, set[str]] = {}
        for u, ec in membership_rows:
            membership.setdefault(u, set()).add(ec)
        _cache[key] = {"vocab": vocab, "membership": membership}
    return _cache[key]


def _ensure_orthodb(k: int = 1024):
    key = f"orthodb_{k}"
    if key not in _cache:
        con = _v2_ro()
        try:
            rows = con.execute(
                f"SELECT orthodb_cluster, count(DISTINCT uniprot) AS n "
                f"FROM v2_ortholog_cluster_membership "
                f"GROUP BY orthodb_cluster ORDER BY n DESC LIMIT {k}"
            ).fetchall()
            membership_rows = con.execute(
                "SELECT uniprot, orthodb_cluster FROM v2_ortholog_cluster_membership"
            ).fetchall()
        finally:
            con.close()
        vocab = {r[0]: i for i, r in enumerate(rows) if r[0]}
        membership: dict[str, set[str]] = {}
        for u, c in membership_rows:
            if c in vocab:
                membership.setdefault(u, set()).add(c)
        _cache[key] = {"vocab": vocab, "membership": membership}
    return _cache[key]


def _multihot(records, ensure_fn, dim: int) -> np.ndarray:
    cache = ensure_fn()
    vocab, membership = cache["vocab"], cache["membership"]
    actual_dim = max(dim, len(vocab))
    out = np.zeros((len(records), actual_dim), dtype=np.float32)
    for i, r in enumerate(records):
        u = getattr(r, "uniprot", "") or ""
        for key in membership.get(u, ()):
            idx = vocab.get(key)
            if idx is not None and idx < actual_dim:
                out[i, idx] = 1.0
    return out[:, :dim]


def _pfam_compute(records, k: int = 512):
    return _multihot(records, lambda: _ensure_pfam(k), k)


def _interpro_compute(records, k: int = 1024):
    return _multihot(records, lambda: _ensure_interpro(k), k)


def _orthodb_compute(records, k: int = 1024):
    return _multihot(records, lambda: _ensure_orthodb(k), k)


def _ec3_compute(records):
    cache = _ensure_ec3()
    vocab, membership = cache["vocab"], cache["membership"]
    dim = len(vocab)
    out = np.zeros((len(records), dim), dtype=np.float32)
    for i, r in enumerate(records):
        u = getattr(r, "uniprot", "") or ""
        for ec in membership.get(u, ()):
            idx = vocab.get(ec)
            if idx is not None:
                out[i, idx] = 1.0
    return out


# Run the catalog probes once at import to size + register the ec3
# featurizer with its dynamic dim. If catalog is missing the registry
# falls back to compute=None.
_DEFAULT_PFAM_K = 512
_DEFAULT_INTERPRO_K = 1024
_DEFAULT_ORTHODB_K = 1024

try:
    _ec3_dim = len(_ensure_ec3()["vocab"])
    _CATALOG_OK = True
except Exception:
    _ec3_dim = 0
    _CATALOG_OK = False


register(FeaturizerSpec(
    id="protein_pfam_topk", label=f"Pfam family multi-hot (top-{_DEFAULT_PFAM_K})",
    axis="protein", dim=_DEFAULT_PFAM_K,
    short_desc="Multi-hot indicator of the protein's top-K most common Pfam families.",
    long_desc=("Multi-hot encoding of the top-512 Pfam families in the v2 "
               "universe. Captures functional family identity without paying "
               "a PLM compute cost. Sourced from v2_motif_membership."),
    requires=["v2_catalog"], cost="trivial",
    compute=(lambda rs: _pfam_compute(rs, _DEFAULT_PFAM_K)) if _CATALOG_OK else None,
    integrated=_CATALOG_OK,
))

register(FeaturizerSpec(
    id="protein_interpro_topk", label=f"InterPro multi-hot (top-{_DEFAULT_INTERPRO_K})",
    axis="protein", dim=_DEFAULT_INTERPRO_K,
    short_desc="Multi-hot indicator of the protein's top-K most common InterPro families.",
    long_desc=("Multi-hot encoding of the top-1024 InterPro families in the "
               "v2 universe (the most informative subset of the 4.34 M total "
               "InterPro annotations). Strong family-identity signal that "
               "complements PLM embeddings."),
    requires=["v2_catalog"], cost="trivial",
    compute=(lambda rs: _interpro_compute(rs, _DEFAULT_INTERPRO_K)) if _CATALOG_OK else None,
    integrated=_CATALOG_OK,
))

register(FeaturizerSpec(
    id="protein_ec3_multihot", label=f"EC sub-class multi-hot ({_ec3_dim})",
    axis="protein", dim=max(_ec3_dim, 1),
    short_desc="Multi-hot indicator over all observed EC sub-classes.",
    long_desc=("Multi-hot encoding of the protein's EC sub-class identifiers "
               "(ec3 level, e.g. 2.7.11). Captures catalytic-function identity "
               "for enzymes; non-enzymes get the zero vector."),
    requires=["v2_catalog"], cost="trivial",
    compute=_ec3_compute if _CATALOG_OK else None,
    integrated=_CATALOG_OK and _ec3_dim > 0,
))

register(FeaturizerSpec(
    id="protein_orthodb_topk", label=f"OrthoDB cluster multi-hot (top-{_DEFAULT_ORTHODB_K})",
    axis="protein", dim=_DEFAULT_ORTHODB_K,
    short_desc="Multi-hot indicator of the protein's top-K most common OrthoDB clusters.",
    long_desc=("Multi-hot encoding of the top-1024 OrthoDB ortholog groups "
               "in the v2 universe. Captures evolutionary lineage — proteins "
               "in the same ortholog group are likely to share function."),
    requires=["v2_catalog"], cost="trivial",
    compute=(lambda rs: _orthodb_compute(rs, _DEFAULT_ORTHODB_K)) if _CATALOG_OK else None,
    integrated=_CATALOG_OK,
))
