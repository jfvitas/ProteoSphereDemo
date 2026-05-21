"""Prevalence-aware weighting.

Sharing a family with 50 members is a strong signal; sharing one with
50,000+ members (e.g. ATP binding GO term, or Ig superfamily) is mostly
noise. This module computes how prevalent a family is and converts that
into a [0, 1] multiplier applied on top of the per-axis default weight.

All thresholds are calibrated against the Swiss-Prot scale (~570k
entries). If you swap in a TrEMBL-scale index later, re-tune
``PREVALENCE_BRACKETS``.

Performance note
----------------

DuckDB's ``connect()`` against a 50 GB catalog has non-trivial setup cost
(~1-2s on cold OS cache). To avoid paying that cost on every prevalence
lookup, this module keeps a process-local connection cache keyed by
warehouse path; :func:`reset_connection_cache` closes them, useful for
tests and shutdown hooks.
"""
from __future__ import annotations

from functools import lru_cache

import duckdb

from proteosphere.config import Config


# ---------------------------------------------------------------------------
# Namespaces that should be prevalence-weighted. Family / fold / GO / EC
# axes all qualify; ortholog-group / pathway / interaction axes don't (their
# membership counts are already a meaningful signal on their own).
# ---------------------------------------------------------------------------
PREVALENCE_NAMESPACES: frozenset[str] = frozenset({
    # protein_family_index namespaces
    "Pfam", "InterPro", "SUPFAM", "PANTHER", "PROSITE",
    "SMART", "CDD", "Gene3D", "HAMAP", "PIRSF",
    "PRINTS", "TIGRFAMs", "SFLD",
    # function_class_index namespaces
    "GO_MF", "GO_BP", "GO_CC", "EC", "EC3",
    # structural_classification_index namespaces
    "CATH", "CATH.architecture", "CATH.topology",
    "CATH.homologous_superfamily",
    "SCOPe.fold", "SCOPe.superfamily", "SCOPe.family",
    "SCOP2B",
})


# (max_size, multiplier). Lookups walk this list in order; first bracket
# whose ``max_size`` is >= the family size wins. The tail (-1) entry is the
# catch-all for very large families.
PREVALENCE_BRACKETS: tuple[tuple[int, float], ...] = (
    (50,       1.00),
    (200,      0.85),
    (1_000,    0.65),
    (5_000,    0.45),
    (20_000,   0.25),
    (100_000,  0.10),
    (-1,       0.05),
)


# ---------------------------------------------------------------------------
# Number of shared identifiers to look up per axis when computing the
# minimum-prevalence factor. Capping at 5 keeps the cost bounded for axes
# that share dozens of family identifiers; the smallest family among the
# shared ones drives the signal.
# ---------------------------------------------------------------------------
PREVALENCE_LOOKUP_LIMIT = 5


# Process-local cache of read-only connections, keyed by warehouse root.
# Opening a DuckDB connection against a 50GB catalog takes ~1-2s on cold
# OS cache; we'd otherwise pay that cost for every single prevalence
# lookup. The cache is keyed by warehouse-root string so concurrent
# workflows targeting different warehouses each get their own connection.
_CONNECTION_CACHE: dict[str, duckdb.DuckDBPyConnection] = {}


def _get_connection(config_root: str) -> duckdb.DuckDBPyConnection:
    """Return the cached read-only connection for ``config_root``.

    Opens one on first call; reuses it thereafter. DuckDB connections are
    not safe to share across threads, but this module is called from the
    per-pair scoring loop on a single thread.
    """
    con = _CONNECTION_CACHE.get(config_root)
    if con is not None:
        return con
    config = Config.discover(warehouse_root=config_root)
    con = duckdb.connect(str(config.catalog_path()), read_only=True)
    con.execute("PRAGMA threads=8")
    _CONNECTION_CACHE[config_root] = con
    return con


def reset_connection_cache() -> None:
    """Close every cached connection. Use on shutdown / between test runs."""
    for con in _CONNECTION_CACHE.values():
        try:
            con.close()
        except Exception:
            pass
    _CONNECTION_CACHE.clear()
    family_prevalence.cache_clear()


@lru_cache(maxsize=8192)
def family_prevalence(config_root: str, namespace: str, identifier: str) -> int:
    """Count how many UniProt accessions are in (namespace, identifier).

    Lookups are cached so repeated queries against the same family don't
    re-scan the partition. Routes to the right index based on namespace:

    - ``GO_*`` and ``EC`` -> ``main.function_class_index``
    - ``EC3`` -> count where ``EC`` identifier starts with the prefix
    - ``CATH*`` and ``SCOP*`` -> ``main.structural_classification_index``
    - everything else -> ``main.protein_family_index``

    Returns 0 if the catalog table is missing for that namespace.
    """
    con = _get_connection(config_root)
    try:
        if namespace.startswith("GO_") or namespace == "EC":
            row = con.execute(
                "SELECT COUNT(DISTINCT accession) FROM main.function_class_index "
                "WHERE namespace = ? AND identifier = ?",
                [namespace, identifier],
            ).fetchone()
        elif namespace == "EC3":
            row = con.execute(
                "SELECT COUNT(DISTINCT accession) FROM main.function_class_index "
                "WHERE namespace = 'EC' AND identifier LIKE ?",
                [identifier + ".%"],
            ).fetchone()
        elif namespace.startswith("CATH") or namespace.startswith("SCOP"):
            row = con.execute(
                "SELECT COUNT(DISTINCT identifier) FROM main.structural_classification_index "
                "WHERE namespace = ? AND classification_id = ? "
                "AND identifier_kind = 'uniprot_accession'",
                [namespace, identifier],
            ).fetchone()
        else:
            row = con.execute(
                "SELECT COUNT(DISTINCT accession) FROM main.protein_family_index "
                "WHERE namespace = ? AND identifier = ?",
                [namespace, identifier],
            ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        # Catalog table may not exist for this namespace yet (e.g. brand
        # new partition not yet refreshed). Treat as "unknown / not in
        # warehouse" -> no prevalence penalty.
        return 0


def prevalence_factor(family_size: int) -> float:
    """Return a multiplier in [0, 1] for a given family size.

    Looked up from :data:`PREVALENCE_BRACKETS`. Sizes <=0 are treated as
    "unknown" and get the neutral 1.0 factor (don't penalize a family we
    can't measure).
    """
    if family_size <= 0:
        return 1.0
    for max_size, multiplier in PREVALENCE_BRACKETS:
        if max_size < 0 or family_size <= max_size:
            return multiplier
    return PREVALENCE_BRACKETS[-1][1]
