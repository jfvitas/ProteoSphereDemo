"""Leakage clusters — group accessions that must end up in the same split.

The scoring engine in :mod:`proteosphere.overlap.runner` answers "given
these two accession sets, what kinds of overlap are present?" The
clusterer answers a different question: "given a flat list of accessions
in my dataset, which ones MUST end up in the same train/val/test split
to prevent leakage at the tier(s) I care about?"

Mental model
------------

For each severity tier the user cares about, build an inverted index from
shared identifier (Pfam family, GO term, EC subclass, OrthoDB group, ...)
to the input accessions that share it. Every identifier bridging two or
more input accessions becomes a hard merge in a union-find structure.
After all tiers are processed, every connected component of the union
find is one *leakage cluster* — a set of accessions that must move
together when the dataset is split.

The output is a JSON-serialisable :class:`LeakageManifest` that the
splitter can consume as additional union-find edges.

Prevalence filter
-----------------

A Pfam family like the Ig domain has >100k worldwide members. If we used
that as a clustering edge, every antibody-like protein in the input
would collapse into a single mega-cluster, making a useful split
impossible. The ``min_specificity`` parameter caps how generic an
identifier can be before it's dropped: an identifier whose worldwide
member count exceeds the cap is too noisy to constrain on. Default cap
of 5,000 lets specific families (kinases, hexokinases, actins) constrain
but rejects "ATP binding" GO_MF or Ig-fold-class superfamilies.

Performance
-----------

One DuckDB query per (table, namespace) pair against the in-catalog
materialised tables. For 1,000 input accessions the entire clustering
pass takes a few seconds on warm cache; the cold-cache cost is the same
one-time penalty you pay for any catalog-backed operation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import duckdb

from proteosphere.config import Config

from .axes import _connect, _ec_subclass_prefix, _norm, _quote_list
from .prevalence import PREVALENCE_NAMESPACES, family_prevalence
from .tiers import SEVERITY_TIERS


# ---------------------------------------------------------------------------
# Tier -> sources used for clustering.
#
# Each entry is a (catalog_table, namespace_or_database) pair. Sources are
# queried with ONE SQL statement each; identifiers bridging >= 2 input
# accessions become union-find edges.
#
# Tiers that aren't cluster-friendly are kept as empty lists or handled with
# specialised helpers (e.g. ``domain_architecture`` needs an exact-set match
# rather than a single-identifier inverted index).
# ---------------------------------------------------------------------------
TIER_SOURCES: dict[str, list[tuple[str, str]]] = {
    "identity":              [],  # handled by accession dedup
    "direct_ortholog": [
        ("cross_references", "OrthoDB"),
        ("cross_references", "eggNOG"),
        ("cross_references", "OMA"),
        ("cross_references", "HOGENOM"),
        ("cross_references", "InParanoid"),
        ("cross_references", "TreeFam"),
        ("cross_references", "PANTHER"),
    ],
    "paralog_family": [
        ("protein_family_index",            "Pfam"),
        ("protein_family_index",            "InterPro"),
        ("protein_family_index",            "PANTHER"),
        ("protein_family_index",            "SMART"),
        ("protein_family_index",            "CDD"),
        ("protein_family_index",            "PIRSF"),
        ("protein_family_index",            "TIGRFAMs"),
        ("protein_family_index",            "SFLD"),
        ("protein_family_index",            "HAMAP"),
        ("protein_family_index",            "PRINTS"),
        ("structural_classification_index", "SCOPe.family"),
    ],
    "domain_architecture": [],  # handled by _cluster_by_domain_architecture
    "distant_homology": [
        ("protein_family_index",            "SUPFAM"),
        ("structural_classification_index", "CATH"),
        ("structural_classification_index", "SCOP2B"),
        ("structural_classification_index", "SCOPe.superfamily"),
        ("structural_classification_index", "CATH.homologous_superfamily"),
    ],
    "convergent_function": [
        ("function_class_index", "GO_MF"),
        ("function_class_index", "GO_BP"),
        ("function_class_index", "EC"),
        ("function_class_index", "EC3"),   # synthesised 3-level EC prefix
    ],
    "broad_fold": [
        ("protein_family_index",            "Gene3D"),
        ("structural_classification_index", "CATH.architecture"),
        ("structural_classification_index", "CATH.topology"),
        ("structural_classification_index", "SCOPe.fold"),
    ],
    "shared_motif": [
        ("protein_family_index", "PROSITE"),
    ],
    "shared_partial_architecture": [],  # pairwise Jaccard, not cluster-friendly
    "co_localization": [
        ("function_class_index", "GO_CC"),
    ],
    "shared_pathway": [
        ("cross_references", "Reactome"),
        ("cross_references", "KEGG"),
        ("cross_references", "BioCyc"),
    ],
    "shared_partner": [
        ("cross_references", "STRING"),
        ("cross_references", "BioGRID"),
    ],
}


# Default tiers to include if the caller doesn't override. We start with
# the four "strong leakage" tiers that almost every benchmark curator
# wants enforced. Convergent function / distant homology / broad fold /
# pathway / partner are opt-in because they're task-specific and can
# produce surprising mega-clusters via transitive union-find through
# broad GO terms or super-fold superfamilies. Opt in explicitly via
# ``tiers=`` for function-prediction or structure-prediction benchmarks.
DEFAULT_CLUSTER_TIERS: tuple[str, ...] = (
    "identity",
    "direct_ortholog",
    "paralog_family",
    "domain_architecture",
)

# Worldwide member-count cap above which an identifier is too generic to
# use as a clustering edge. 5000 keeps Pfam family-scale signals
# (actin PF00022 ~3500, serine-endopeptidase EC3 3.4.21 ~3600) while
# rejecting truly noisy super-families (Ig fold ~10k, kinase Pfam ~100k).
DEFAULT_MIN_SPECIFICITY = 5_000

# Per-namespace caps tighter than the global default. GO biological-
# process and cellular-component terms can be alarmingly broad
# ("regulation of cell cycle", "cytoplasm"); without tight caps they
# bridge kinases to actins via transitive union. GO molecular-function
# is usually more specific but still benefits from a slightly tighter
# cap than the global Pfam-friendly default.
PER_NAMESPACE_SPECIFICITY: dict[str, int] = {
    "GO_BP": 500,
    "GO_CC": 500,
    "GO_MF": 2_000,
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterSource:
    """One identifier that justified bridging accessions into a cluster."""
    tier: str           # severity tier the identifier maps to
    namespace: str      # data-source-specific tag (Pfam, eggNOG, GO_MF, ...)
    identifier: str     # the actual ID (PF00069, KOG0676, GO:0004252, ...)
    prevalence: int = 0 # worldwide-member count (0 = not measured)
    n_bridged: int = 0  # how many input accessions this source bridged


@dataclass
class LeakageCluster:
    """A set of accessions that must end up in the same split."""
    cluster_id: str
    members: list[str]
    sources: list[ClusterSource] = field(default_factory=list)


@dataclass
class LeakageManifest:
    """The full result of a clustering pass over a list of accessions."""
    input_accessions: list[str]
    tiers_used: list[str]
    min_specificity: int | None
    clusters: list[LeakageCluster]
    singletons: list[str]

    # ----- serialisation -------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "input_accessions": self.input_accessions,
            "tiers_used": list(self.tiers_used),
            "min_specificity": self.min_specificity,
            "cluster_count": len(self.clusters),
            "singleton_count": len(self.singletons),
            "clustered_accession_count": sum(len(c.members) for c in self.clusters),
            "clusters": [
                {
                    "cluster_id": c.cluster_id,
                    "size": len(c.members),
                    "members": c.members,
                    "via": [
                        {
                            "tier": s.tier,
                            "namespace": s.namespace,
                            "identifier": s.identifier,
                            "worldwide_members": s.prevalence,
                            "input_accessions_bridged": s.n_bridged,
                        }
                        for s in c.sources
                    ],
                }
                for c in self.clusters
            ],
            "singletons": self.singletons,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "LeakageManifest":
        clusters = [
            LeakageCluster(
                cluster_id=c["cluster_id"],
                members=list(c["members"]),
                sources=[
                    ClusterSource(
                        tier=v["tier"],
                        namespace=v["namespace"],
                        identifier=v["identifier"],
                        prevalence=v.get("worldwide_members", 0),
                        n_bridged=v.get("input_accessions_bridged", 0),
                    )
                    for v in c.get("via", [])
                ],
            )
            for c in payload.get("clusters", [])
        ]
        return cls(
            input_accessions=list(payload.get("input_accessions", [])),
            tiers_used=list(payload.get("tiers_used", [])),
            min_specificity=payload.get("min_specificity"),
            clusters=clusters,
            singletons=list(payload.get("singletons", [])),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "LeakageManifest":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def write_json(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    # ----- convenience lookups ------------------------------------------

    def accession_to_cluster(self) -> dict[str, str]:
        """Return ``{accession -> cluster_id}`` for clustered accessions."""
        out: dict[str, str] = {}
        for cluster in self.clusters:
            for acc in cluster.members:
                out[acc] = cluster.cluster_id
        return out

    def tier_counts(self) -> dict[str, int]:
        """Return ``{tier -> number_of_clusters_with_a_source_in_that_tier}``."""
        out: dict[str, int] = {}
        for cluster in self.clusters:
            tiers_seen = {s.tier for s in cluster.sources}
            for tier in tiers_seen:
                out[tier] = out.get(tier, 0) + 1
        return out


# ---------------------------------------------------------------------------
# Internal union-find that tracks source provenance per cluster
# ---------------------------------------------------------------------------


class _UnionFind:
    """Union-find over accessions, with source provenance attached per root."""

    def __init__(self, items: Iterable[str]) -> None:
        self.parent: dict[str, str] = {item: item for item in items}
        # Sources are stored ON the root; merge appends.
        self.sources: dict[str, list[ClusterSource]] = {item: [] for item in self.parent}

    def find(self, item: str) -> str:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: str, b: str) -> str:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return ra
        # Canonicalise on the lexicographically smaller root to keep output stable.
        if rb < ra:
            ra, rb = rb, ra
        self.parent[rb] = ra
        # Move sources from the absorbed root to the new root.
        self.sources.setdefault(ra, []).extend(self.sources.pop(rb, []))
        return ra

    def add_source(self, item: str, source: ClusterSource) -> None:
        root = self.find(item)
        self.sources.setdefault(root, []).append(source)

    def groups(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self.parent:
            root = self.find(item)
            out.setdefault(root, []).append(item)
        return out


# ---------------------------------------------------------------------------
# Per-source clustering helpers
# ---------------------------------------------------------------------------


def _fetch_source_rows(
    con: duckdb.DuckDBPyConnection,
    accs_clause: str,
    table: str,
    namespace: str,
) -> list[tuple[str, str]]:
    """Pull (accession, identifier) rows for a single source.

    Returns an empty list if the table doesn't exist or the query fails.
    """
    try:
        if table == "cross_references":
            return con.execute(
                f"SELECT accession, external_id FROM main.cross_references "
                f"WHERE accession IN ({accs_clause}) AND database = ?",
                [namespace],
            ).fetchall()
        if table == "protein_family_index":
            return con.execute(
                f"SELECT accession, identifier FROM main.protein_family_index "
                f"WHERE accession IN ({accs_clause}) AND namespace = ?",
                [namespace],
            ).fetchall()
        if table == "function_class_index":
            if namespace == "EC3":
                ec_rows = con.execute(
                    f"SELECT accession, identifier FROM main.function_class_index "
                    f"WHERE accession IN ({accs_clause}) AND namespace = 'EC'"
                ).fetchall()
                out: list[tuple[str, str]] = []
                for acc, ec in ec_rows:
                    prefix = _ec_subclass_prefix(ec)
                    if prefix:
                        out.append((acc, prefix))
                return out
            return con.execute(
                f"SELECT accession, identifier FROM main.function_class_index "
                f"WHERE accession IN ({accs_clause}) AND namespace = ?",
                [namespace],
            ).fetchall()
        if table == "structural_classification_index":
            return con.execute(
                f"SELECT identifier, classification_id "
                f"FROM main.structural_classification_index "
                f"WHERE identifier IN ({accs_clause}) "
                f"AND identifier_kind = 'uniprot_accession' AND namespace = ?",
                [namespace],
            ).fetchall()
    except Exception:
        pass
    return []


def _cluster_by_source(
    con: duckdb.DuckDBPyConnection,
    accs_norm: list[str],
    uf: _UnionFind,
    *,
    tier: str,
    table: str,
    namespace: str,
    warehouse_root: str,
    min_specificity: int | None,
) -> None:
    """Group input accessions by their shared identifier under one source.

    For every identifier that bridges >= 2 input accessions and survives
    the prevalence cap (when applicable), union those accessions in
    ``uf`` and record the source provenance.
    """
    accs_clause = _quote_list(accs_norm)
    rows = _fetch_source_rows(con, accs_clause, table, namespace)
    if not rows:
        return

    by_identifier: dict[str, set[str]] = {}
    for acc, ident in rows:
        if not ident:
            continue
        by_identifier.setdefault(ident, set()).add(acc)

    for identifier, accs in by_identifier.items():
        if len(accs) < 2:
            continue
        # Apply prevalence cap for namespaces where we know how to look it up.
        # Use the per-namespace cap (tighter) when defined, otherwise the
        # global ``min_specificity`` cap.
        prev = 0
        if min_specificity is not None and namespace in PREVALENCE_NAMESPACES:
            effective_cap = PER_NAMESPACE_SPECIFICITY.get(namespace, min_specificity)
            prev = family_prevalence(warehouse_root, namespace, identifier)
            if prev > effective_cap:
                continue
        ordered = sorted(accs)
        head = ordered[0]
        for tail in ordered[1:]:
            uf.union(head, tail)
        uf.add_source(
            head,
            ClusterSource(
                tier=tier, namespace=namespace, identifier=identifier,
                prevalence=prev, n_bridged=len(ordered),
            ),
        )


def _cluster_by_domain_architecture(
    con: duckdb.DuckDBPyConnection,
    accs_norm: list[str],
    uf: _UnionFind,
) -> None:
    """Two accessions share an architecture <=> identical Pfam set (>=2 domains).

    Skipped for accessions with fewer than two Pfam domains -- a single
    shared Pfam already fires under the ``paralog_family`` tier.
    """
    accs_clause = _quote_list(accs_norm)
    try:
        rows = con.execute(
            f"SELECT accession, "
            f"  STRING_AGG(DISTINCT identifier, '|' ORDER BY identifier) AS arch "
            f"FROM main.protein_family_index "
            f"WHERE namespace = 'Pfam' AND accession IN ({accs_clause}) "
            f"GROUP BY accession "
            f"HAVING COUNT(DISTINCT identifier) >= 2"
        ).fetchall()
    except Exception:
        return

    by_arch: dict[str, set[str]] = {}
    for acc, arch in rows:
        if not arch:
            continue
        by_arch.setdefault(arch, set()).add(acc)

    for arch, accs in by_arch.items():
        if len(accs) < 2:
            continue
        ordered = sorted(accs)
        head = ordered[0]
        for tail in ordered[1:]:
            uf.union(head, tail)
        uf.add_source(
            head,
            ClusterSource(
                tier="domain_architecture",
                namespace="Pfam_arch",
                identifier=arch,
                prevalence=0,
                n_bridged=len(ordered),
            ),
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_leakage_clusters(
    config: Config,
    accessions: Iterable[str],
    *,
    tiers: Iterable[str] | None = None,
    min_specificity: int | None = DEFAULT_MIN_SPECIFICITY,
    on_source_start: Callable[[str, str, str, int, int], None] | None = None,
    on_source_done: Callable[[str, str, str, int, int, int], None] | None = None,
) -> LeakageManifest:
    """Build leakage clusters from a flat list of accessions.

    Parameters
    ----------
    config
        Resolved :class:`Config` pointing at the warehouse.
    accessions
        UniProt accessions to cluster. Normalised internally (uppercase,
        dedup, sorted).
    tiers
        Severity tiers to include as clustering constraints. ``None``
        uses :data:`DEFAULT_CLUSTER_TIERS`. Unknown tier names raise
        :class:`ValueError`.
    min_specificity
        Worldwide-member-count cap for any single clustering identifier.
        Identifiers exceeding this cap are dropped (too generic). Pass
        ``None`` to disable the filter -- you'll get mega-clusters.
    on_source_start
        Optional callback fired just before each (tier, table, namespace)
        source is queried. Signature::

            on_source_start(tier, table, namespace,
                            source_index, total_sources)

        Useful for live progress displays in CLIs.
    on_source_done
        Optional callback fired after each source completes. Signature::

            on_source_done(tier, table, namespace,
                           source_index, total_sources, n_union_events)

        ``n_union_events`` counts how many identifiers actually unioned
        two or more input accessions (i.e. how productive the source was).

    Returns
    -------
    LeakageManifest
        One cluster per connected component of the union-find. Solo
        accessions land in :attr:`LeakageManifest.singletons`.
    """
    accs_norm = _norm(accessions)
    if not accs_norm:
        return LeakageManifest(
            input_accessions=[], tiers_used=list(tiers or DEFAULT_CLUSTER_TIERS),
            min_specificity=min_specificity, clusters=[], singletons=[],
        )

    tier_list = list(tiers) if tiers is not None else list(DEFAULT_CLUSTER_TIERS)
    valid = set(TIER_SOURCES)
    bad = [t for t in tier_list if t not in valid]
    if bad:
        raise ValueError(
            f"Unknown tier(s) {bad!r}. Valid tiers: {sorted(valid)}"
        )

    # Build a flat schedule of every (tier, table, namespace) source we'll
    # query, so progress callbacks can show "source 5 of 22" style counts.
    schedule: list[tuple[str, str, str]] = []
    for tier in tier_list:
        if tier == "identity":
            continue  # handled by accession dedup
        if tier == "domain_architecture":
            schedule.append((tier, "protein_family_index", "Pfam_arch"))
            continue
        for table, namespace in TIER_SOURCES.get(tier, []):
            schedule.append((tier, table, namespace))
    total_sources = len(schedule)

    uf = _UnionFind(accs_norm)
    warehouse_root = str(config.warehouse_root)

    con = _connect(config)
    try:
        for i, (tier, table, namespace) in enumerate(schedule, start=1):
            if on_source_start is not None:
                on_source_start(tier, table, namespace, i, total_sources)

            sources_before = sum(len(v) for v in uf.sources.values())
            if tier == "domain_architecture":
                _cluster_by_domain_architecture(con, accs_norm, uf)
            else:
                _cluster_by_source(
                    con, accs_norm, uf,
                    tier=tier, table=table, namespace=namespace,
                    warehouse_root=warehouse_root,
                    min_specificity=min_specificity,
                )
            sources_after = sum(len(v) for v in uf.sources.values())
            n_union_events = sources_after - sources_before

            if on_source_done is not None:
                on_source_done(
                    tier, table, namespace, i, total_sources, n_union_events,
                )
    finally:
        con.close()

    # Build the output. Sort clusters by size descending so the largest
    # leak components surface first; singletons go in their own bucket.
    groups = uf.groups()
    clusters: list[LeakageCluster] = []
    singletons: list[str] = []
    for members in groups.values():
        if len(members) == 1:
            singletons.append(members[0])
            continue
        clusters.append(LeakageCluster(
            cluster_id="",  # assigned after sorting
            members=sorted(members),
            sources=sorted(
                uf.sources.get(uf.find(members[0]), []),
                key=lambda s: (-s.n_bridged, s.tier, s.namespace, s.identifier),
            ),
        ))
    clusters.sort(key=lambda c: (-len(c.members), c.members[0]))
    for i, cluster in enumerate(clusters, start=1):
        cluster.cluster_id = f"c{i:05d}"
    singletons.sort()

    return LeakageManifest(
        input_accessions=accs_norm,
        tiers_used=tier_list,
        min_specificity=min_specificity,
        clusters=clusters,
        singletons=singletons,
    )


__all__ = [
    "TIER_SOURCES",
    "DEFAULT_CLUSTER_TIERS",
    "DEFAULT_MIN_SPECIFICITY",
    "ClusterSource",
    "LeakageCluster",
    "LeakageManifest",
    "compute_leakage_clusters",
]
