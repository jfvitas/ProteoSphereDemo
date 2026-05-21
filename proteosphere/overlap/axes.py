"""Per-axis overlap discovery functions.

Each function takes a (config, test, comparison) triple and returns the
axes it can detect, as ``{axis_name: CoMembershipResult}``. They're called
sequentially by :func:`runner.discover_pair_axes`, but advanced callers
can invoke them individually if they want to compute a subset.

All discovery functions follow the same contract:

- Open a single read-only DuckDB connection
- Issue one (or two) bulk queries that pull every relevant row for the
  combined accession set, then partition test/comparison in Python
- Close the connection
- Return only axes where both sides have at least one shared identifier

The bulk-and-partition pattern replaces 13+ individual scans of the
billion-row family index with a single scan, which is the difference
between minutes and seconds per pair.
"""
from __future__ import annotations

from typing import Iterable

import duckdb

from proteosphere.config import Config

from .report import CoMembershipResult


# ---------------------------------------------------------------------------
# Domain-architecture matching parameters
# ---------------------------------------------------------------------------
# Minimum Jaccard similarity for the partial-architecture axis to fire.
PARTIAL_ARCH_JACCARD_THRESHOLD = 0.5
# Minimum number of shared Pfam domains in the intersection (anti-noise).
PARTIAL_ARCH_MIN_INTERSECTION = 2
# Minimum domain count before "exact match" counts as a real architecture
# (a single shared Pfam isn't an "architecture" — that's just paralogy).
FULL_ARCH_MIN_DOMAINS = 2

# Cap how many shared examples we report per axis for compact display.
MAX_EXAMPLES = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _connect(config: Config) -> duckdb.DuckDBPyConnection:
    """Open a read-only DuckDB connection to the catalog with 8 threads."""
    con = duckdb.connect(str(config.catalog_path()), read_only=True)
    con.execute("PRAGMA threads=8")
    return con


def _norm(accs: Iterable[str]) -> list[str]:
    """Normalize an iterable of accessions: trim, uppercase, dedupe, sort."""
    return sorted({str(a).strip().upper() for a in accs if str(a).strip()})


def _quote_list(items: Iterable[str]) -> str:
    """Render an iterable of strings as a SQL ``IN (...)`` value list.

    Inputs come from the normalized accession set (uppercase A-Z0-9 plus
    ``-`` and ``_``) or from identifiers we read out of the catalog, so
    SQL injection isn't a real risk; we still escape single quotes.
    """
    return ", ".join("'" + str(i).replace("'", "''") + "'" for i in items)


def _has_table(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """Check whether a table exists in the connected catalog."""
    return bool(con.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = ? LIMIT 1",
        [table_name],
    ).fetchall())


def _emit_co_membership(
    *,
    axis: str,
    namespace: str,
    weight: float,
    test_ids: set[str],
    comp_ids: set[str],
) -> CoMembershipResult | None:
    """Build a CoMembershipResult from two id sets, or None if no overlap."""
    if not test_ids or not comp_ids:
        return None
    shared = test_ids & comp_ids
    if not shared:
        return None
    sorted_shared = sorted(shared)
    return CoMembershipResult(
        axis=axis,
        namespace=namespace,
        weight=weight,
        shared_ids=sorted_shared,
        test_co_member_count=len(test_ids),
        comparison_co_member_count=len(comp_ids),
        overlap_count=len(shared),
        examples=sorted_shared[:MAX_EXAMPLES],
    )


# ---------------------------------------------------------------------------
# Cross-reference axes (ortholog / pathway / interaction)
# ---------------------------------------------------------------------------


# database -> (axis_name, default_weight)
_ORTHOLOG_DBS: dict[str, tuple[str, float]] = {
    "OrthoDB":     ("shared_orthodb_group",     0.6),
    "eggNOG":      ("shared_eggnog_group",      0.5),
    "OMA":         ("shared_oma_group",         0.6),
    "HOGENOM":     ("shared_hogenom_group",     0.4),
    "InParanoid":  ("shared_inparanoid_group",  0.5),
    "PANTHER":     ("shared_panther_family",    0.4),
    "TreeFam":     ("shared_treefam_group",     0.5),
}

_PATHWAY_DBS: dict[str, tuple[str, float]] = {
    "Reactome":  ("shared_reactome_pathway", 0.35),
    "KEGG":      ("shared_kegg_pathway",     0.35),
    "BioCyc":    ("shared_biocyc_pathway",   0.30),
}

_INTERACTION_DBS: dict[str, tuple[str, float]] = {
    "STRING":    ("shared_string_network",   0.30),
    "BioGRID":   ("shared_biogrid_network",  0.30),
}


def shared_ortholog_membership(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """Find shared OrthoDB / eggNOG / OMA / HOGENOM / InParanoid groups."""
    test_accs = _norm(test)
    comp_accs = _norm(comparison)
    if not test_accs or not comp_accs:
        return {}

    out: dict[str, CoMembershipResult] = {}
    con = _connect(config)
    try:
        test_clause = _quote_list(test_accs)
        comp_clause = _quote_list(comp_accs)
        for db, (axis, weight) in _ORTHOLOG_DBS.items():
            test_ids = {r[0] for r in con.execute(
                f"SELECT external_id FROM main.cross_references "
                f"WHERE accession IN ({test_clause}) AND database='{db}'"
            ).fetchall()}
            if not test_ids:
                continue
            comp_ids = {r[0] for r in con.execute(
                f"SELECT external_id FROM main.cross_references "
                f"WHERE accession IN ({comp_clause}) AND database='{db}'"
            ).fetchall()}
            shared = test_ids & comp_ids
            if not shared:
                continue
            # Count members on the test side for the overlap_count metric.
            sh_clause = _quote_list(shared)
            n_test = con.execute(
                f"SELECT COUNT(DISTINCT accession) FROM main.cross_references "
                f"WHERE database='{db}' AND external_id IN ({sh_clause})"
            ).fetchone()[0]
            sorted_shared = sorted(shared)
            out[axis] = CoMembershipResult(
                axis=axis,
                namespace=db,
                weight=weight,
                shared_ids=sorted_shared,
                test_co_member_count=len(test_ids),
                comparison_co_member_count=len(comp_ids),
                overlap_count=int(n_test),
                examples=sorted_shared[:MAX_EXAMPLES],
            )
    finally:
        con.close()
    return out


def _shared_xref_axis(
    config: Config,
    test: Iterable[str],
    comparison: Iterable[str],
    db_to_axis: dict[str, tuple[str, float]],
) -> dict[str, CoMembershipResult]:
    """Generic helper for cross_references-based axes (pathway, interaction)."""
    test_accs = _norm(test)
    comp_accs = _norm(comparison)
    if not test_accs or not comp_accs:
        return {}
    out: dict[str, CoMembershipResult] = {}
    con = _connect(config)
    try:
        test_clause = _quote_list(test_accs)
        comp_clause = _quote_list(comp_accs)
        for db, (axis, weight) in db_to_axis.items():
            test_ids = {r[0] for r in con.execute(
                f"SELECT external_id FROM main.cross_references "
                f"WHERE accession IN ({test_clause}) AND database='{db}'"
            ).fetchall()}
            if not test_ids:
                continue
            comp_ids = {r[0] for r in con.execute(
                f"SELECT external_id FROM main.cross_references "
                f"WHERE accession IN ({comp_clause}) AND database='{db}'"
            ).fetchall()}
            res = _emit_co_membership(
                axis=axis, namespace=db, weight=weight,
                test_ids=test_ids, comp_ids=comp_ids,
            )
            if res is not None:
                out[axis] = res
    finally:
        con.close()
    return out


def shared_pathway_membership(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """Reactome / KEGG / BioCyc pathway co-membership."""
    return _shared_xref_axis(config, test, comparison, _PATHWAY_DBS)


def shared_interaction_network(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """STRING / BioGRID interaction-network co-membership."""
    return _shared_xref_axis(config, test, comparison, _INTERACTION_DBS)


# ---------------------------------------------------------------------------
# Family / domain axes from protein_family_index + motif_domain_site_annotations
# ---------------------------------------------------------------------------


# namespace -> (axis_name, default_weight). Hardcoded weights; profiles can
# override via ``TaskProfile.weight_overrides``.
_FAMILY_NAMESPACES: dict[str, tuple[str, float]] = {
    "Pfam":      ("shared_pfam_family",      0.40),
    "InterPro":  ("shared_interpro_family",  0.40),
    "PROSITE":   ("shared_prosite_motif",    0.30),
    "SUPFAM":    ("shared_supfam_fold",      0.30),
    "SMART":     ("shared_smart_family",     0.30),
    "PANTHER":   ("shared_panther_family",   0.40),
    "CDD":       ("shared_cdd_domain",       0.30),
    "Gene3D":    ("shared_gene3d_topology",  0.25),
    "HAMAP":     ("shared_hamap_signature",  0.30),
    "PIRSF":     ("shared_pirsf_family",     0.30),
    "PRINTS":    ("shared_prints_family",    0.25),
    "TIGRFAMs":  ("shared_tigrfams_family",  0.30),
    "SFLD":      ("shared_sfld_family",      0.30),
    "ELM":       ("shared_elm_motif",        0.25),
}


def shared_motif_domain_family(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """Detect Pfam / InterPro / PROSITE / SUPFAM / SMART co-membership.

    Two-source query:
    - The new ``protein_family_index`` partition (Phase B; full Swiss-Prot
      coverage with all DR records to Pfam/InterPro/SUPFAM/SMART/PROSITE/
      PANTHER/CDD/Gene3D/HAMAP/PIRSF/PRINTS/etc.). This is the primary
      source — covers every Swiss-Prot entry with its full family
      annotations.
    - The legacy ``motif_domain_site_annotations`` partition for the
      curated subset that has per-residue spans + ELM motif annotations.
    """
    test_accs = _norm(test)
    comp_accs = _norm(comparison)
    if not test_accs or not comp_accs:
        return {}

    all_accs = list({*test_accs, *comp_accs})
    test_set = set(test_accs)
    comp_set = set(comp_accs)
    accs_clause = _quote_list(all_accs)
    refs_clause = _quote_list(f"protein:{a}" for a in all_accs)
    out: dict[str, CoMembershipResult] = {}
    con = _connect(config)
    try:
        has_family_table = _has_table(con, "protein_family_index")
        family_path = config.family_partition("protein_family_index")
        has_family_parquet = family_path.is_file()

        # ONE scan pulls every (accession, namespace, identifier) row for
        # our accessions of interest; we partition into test/comp in Python.
        # This replaces 13+ individual scans of the 1B-row partition.
        rows: list[tuple[str, str, str]] = []
        if has_family_table:
            rows.extend(con.execute(
                f"SELECT accession, namespace, identifier "
                f"FROM main.protein_family_index "
                f"WHERE accession IN ({accs_clause})"
            ).fetchall())
        elif has_family_parquet:
            family_src = str(family_path).replace("\\", "/")
            rows.extend(con.execute(
                f"SELECT accession, namespace, identifier "
                f"FROM read_parquet('{family_src}') "
                f"WHERE accession IN ({accs_clause})"
            ).fetchall())

        # Legacy curated supplement (per-residue spans + ELM motifs)
        legacy_rows = con.execute(
            f"SELECT REPLACE(owner_summary_id, 'protein:', ''), namespace, identifier "
            f"FROM main.motif_domain_site_annotations "
            f"WHERE owner_summary_id IN ({refs_clause})"
        ).fetchall()
        rows.extend(legacy_rows)
    finally:
        con.close()

    # Group by namespace, then by accession, then by identifier set.
    by_ns: dict[str, dict[str, set[str]]] = {}
    for acc, ns, ident in rows:
        if ns not in _FAMILY_NAMESPACES or not ident:
            continue
        by_ns.setdefault(ns, {}).setdefault(acc, set()).add(ident)

    for ns, (axis, weight) in _FAMILY_NAMESPACES.items():
        per_acc = by_ns.get(ns, {})
        if not per_acc:
            continue
        test_ids: set[str] = set()
        comp_ids: set[str] = set()
        for acc, ids in per_acc.items():
            if acc in test_set:
                test_ids |= ids
            if acc in comp_set:
                comp_ids |= ids
        res = _emit_co_membership(
            axis=axis, namespace=ns, weight=weight,
            test_ids=test_ids, comp_ids=comp_ids,
        )
        if res is not None:
            out[axis] = res
    return out


# ---------------------------------------------------------------------------
# Structural-classification axes (CATH / SCOP / SCOPe)
# ---------------------------------------------------------------------------


_STRUCTURAL_NAMESPACES: dict[str, tuple[str, float]] = {
    "CATH":                       ("shared_cath_homologous_superfamily", 0.45),
    "SCOP2B":                     ("shared_scop_superfamily",            0.45),
    "SCOPe.fold":                 ("shared_scope_fold",                  0.30),
    "SCOPe.superfamily":          ("shared_scope_superfamily",           0.45),
    "SCOPe.family":               ("shared_scope_family",                0.50),
    "CATH.architecture":          ("shared_cath_architecture",           0.20),
    "CATH.topology":              ("shared_cath_topology",               0.40),
    "CATH.homologous_superfamily": ("shared_cath_h_superfamily",         0.50),
}


def shared_structural_classification(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """SCOP / SCOPe / CATH fold / superfamily co-membership.

    Reads from the catalog table when available, falls back to the parquet
    partition. Returns nothing if the partition isn't installed.
    """
    test_accs = _norm(test)
    comp_accs = _norm(comparison)
    if not test_accs or not comp_accs:
        return {}
    try:
        path = config.family_partition("structural_classification_index")
    except KeyError:
        return {}
    if not path.is_file():
        return {}

    all_accs = list({*test_accs, *comp_accs})
    test_set = set(test_accs)
    comp_set = set(comp_accs)
    accs_clause = _quote_list(all_accs)

    con = _connect(config)
    try:
        has_catalog_table = _has_table(con, "structural_classification_index")
        if has_catalog_table:
            rows = con.execute(
                f"SELECT identifier, namespace, classification_id "
                f"FROM main.structural_classification_index "
                f"WHERE identifier_kind='uniprot_accession' "
                f"AND identifier IN ({accs_clause})"
            ).fetchall()
        else:
            src = str(path).replace("\\", "/")
            rows = con.execute(
                f"SELECT identifier, namespace, classification_id "
                f"FROM read_parquet('{src}') "
                f"WHERE identifier_kind='uniprot_accession' "
                f"AND identifier IN ({accs_clause})"
            ).fetchall()
    finally:
        con.close()

    by_ns: dict[str, dict[str, set[str]]] = {}
    for acc, ns, cid in rows:
        if ns not in _STRUCTURAL_NAMESPACES or not cid:
            continue
        by_ns.setdefault(ns, {}).setdefault(acc, set()).add(cid)

    out: dict[str, CoMembershipResult] = {}
    for ns, (axis, weight) in _STRUCTURAL_NAMESPACES.items():
        per_acc = by_ns.get(ns, {})
        if not per_acc:
            continue
        test_ids: set[str] = set()
        comp_ids: set[str] = set()
        for acc, ids in per_acc.items():
            if acc in test_set:
                test_ids |= ids
            if acc in comp_set:
                comp_ids |= ids
        res = _emit_co_membership(
            axis=axis, namespace=ns, weight=weight,
            test_ids=test_ids, comp_ids=comp_ids,
        )
        if res is not None:
            out[axis] = res
    return out


# ---------------------------------------------------------------------------
# Function-class axis (GO + EC, with EC subclass for convergent function)
# ---------------------------------------------------------------------------


_FUNCTION_NAMESPACES: dict[str, tuple[str, float]] = {
    "GO_MF":  ("shared_go_mf",     0.55),
    "GO_BP":  ("shared_go_bp",     0.30),
    "GO_CC":  ("shared_go_cc",     0.20),
    "EC":     ("shared_ec_number", 0.55),
}

# EC at 3-level resolution. Catches convergent function: trypsin
# (3.4.21.4) and subtilisin (3.4.21.62) share NO sequence/structure but
# both have the EC subclass 3.4.21 (serine endopeptidase). Synthesized
# in Python; not a separate namespace in the partition.
_EC_SUBCLASS_AXIS = ("shared_ec_subclass", 0.45)


def _ec_subclass_prefix(ec: str) -> str | None:
    """Return the 3-level EC prefix (e.g. ``3.4.21``) or None if invalid."""
    parts = ec.split(".")
    if len(parts) >= 3 and all(p and p != "-" for p in parts[:3]):
        return ".".join(parts[:3])
    return None


def shared_function_class(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """GO molecular function / biological process / cellular component / EC.

    Emits a synthetic ``EC3`` namespace for 3-level EC prefixes so that
    convergent-function pairs (different fold, same enzyme activity at the
    sub-class level) can be detected as a separate axis.
    """
    test_accs = _norm(test)
    comp_accs = _norm(comparison)
    if not test_accs or not comp_accs:
        return {}
    try:
        path = config.family_partition("function_class_index")
    except KeyError:
        return {}
    if not path.is_file():
        return {}

    all_accs = list({*test_accs, *comp_accs})
    test_set = set(test_accs)
    comp_set = set(comp_accs)
    accs_clause = _quote_list(all_accs)

    con = _connect(config)
    try:
        has_catalog_table = _has_table(con, "function_class_index")
        if has_catalog_table:
            rows = con.execute(
                f"SELECT accession, namespace, identifier "
                f"FROM main.function_class_index "
                f"WHERE accession IN ({accs_clause})"
            ).fetchall()
        else:
            src = str(path).replace("\\", "/")
            rows = con.execute(
                f"SELECT accession, namespace, identifier "
                f"FROM read_parquet('{src}') "
                f"WHERE accession IN ({accs_clause})"
            ).fetchall()
    finally:
        con.close()

    by_ns: dict[str, dict[str, set[str]]] = {}
    for acc, ns, ident in rows:
        if not ident:
            continue
        if ns == "EC":
            by_ns.setdefault("EC", {}).setdefault(acc, set()).add(ident)
            ec3 = _ec_subclass_prefix(ident)
            if ec3 is not None:
                by_ns.setdefault("EC3", {}).setdefault(acc, set()).add(ec3)
            continue
        if ns not in _FUNCTION_NAMESPACES:
            continue
        by_ns.setdefault(ns, {}).setdefault(acc, set()).add(ident)

    # GO + EC + EC3 axes
    function_axes = dict(_FUNCTION_NAMESPACES)
    function_axes["EC3"] = _EC_SUBCLASS_AXIS

    out: dict[str, CoMembershipResult] = {}
    for ns, (axis, weight) in function_axes.items():
        per_acc = by_ns.get(ns, {})
        if not per_acc:
            continue
        test_ids: set[str] = set()
        comp_ids: set[str] = set()
        for acc, ids in per_acc.items():
            if acc in test_set:
                test_ids |= ids
            if acc in comp_set:
                comp_ids |= ids
        res = _emit_co_membership(
            axis=axis, namespace=ns, weight=weight,
            test_ids=test_ids, comp_ids=comp_ids,
        )
        if res is not None:
            out[axis] = res
    return out


# ---------------------------------------------------------------------------
# Domain-architecture axis (full set vs partial set of Pfam domains)
# ---------------------------------------------------------------------------


def shared_domain_architecture(
    config: Config, test: Iterable[str], comparison: Iterable[str],
) -> dict[str, CoMembershipResult]:
    """Detect when two proteins share a full Pfam domain architecture.

    Two distinct axes are emitted:

    - ``shared_full_domain_architecture``: at least one test protein has
      EXACTLY the same set of Pfam domains as a comparison protein, and
      both have at least :data:`FULL_ARCH_MIN_DOMAINS` domains. Strong
      evidence of paralog/orthologous role.
    - ``shared_partial_domain_architecture``: weaker — Jaccard similarity
      >= :data:`PARTIAL_ARCH_JACCARD_THRESHOLD` and intersection size
      >= :data:`PARTIAL_ARCH_MIN_INTERSECTION`.

    Domain-architecture matching distinguishes "shares one promiscuous Ig
    domain" (low signal, paralog_family tier) from "exact same multi-domain
    composition" (high signal, domain_architecture tier).
    """
    test_accs = _norm(test)
    comp_accs = _norm(comparison)
    if not test_accs or not comp_accs:
        return {}

    test_clause = _quote_list(test_accs)
    comp_clause = _quote_list(comp_accs)
    con = _connect(config)
    try:
        # Pull each accession's complete Pfam set, joined into a pipe-
        # delimited string. STRING_AGG runs server-side; we split client-
        # side into a frozenset for set-op comparisons.
        test_arch = {r[0]: frozenset(r[1].split("|")) for r in con.execute(
            f"""
            SELECT accession, STRING_AGG(DISTINCT identifier, '|' ORDER BY identifier)
            FROM main.protein_family_index
            WHERE namespace='Pfam' AND accession IN ({test_clause})
            GROUP BY accession
            """
        ).fetchall()}
        comp_arch = {r[0]: frozenset(r[1].split("|")) for r in con.execute(
            f"""
            SELECT accession, STRING_AGG(DISTINCT identifier, '|' ORDER BY identifier)
            FROM main.protein_family_index
            WHERE namespace='Pfam' AND accession IN ({comp_clause})
            GROUP BY accession
            """
        ).fetchall()}
    finally:
        con.close()
    if not test_arch or not comp_arch:
        return {}

    full_matches: list[tuple[str, str, frozenset[str]]] = []
    partial_matches: list[tuple[str, str, float]] = []
    for ta, t_set in test_arch.items():
        for ca, c_set in comp_arch.items():
            if not t_set or not c_set:
                continue
            if t_set == c_set and len(t_set) >= FULL_ARCH_MIN_DOMAINS:
                full_matches.append((ta, ca, t_set))
                continue
            inter = t_set & c_set
            union = t_set | c_set
            jaccard = (len(inter) / len(union)) if union else 0.0
            if (jaccard >= PARTIAL_ARCH_JACCARD_THRESHOLD
                    and len(inter) >= PARTIAL_ARCH_MIN_INTERSECTION):
                partial_matches.append((ta, ca, jaccard))

    out: dict[str, CoMembershipResult] = {}
    if full_matches:
        seen_archs = sorted({"+".join(sorted(arch)) for _, _, arch in full_matches})
        out["shared_full_domain_architecture"] = CoMembershipResult(
            axis="shared_full_domain_architecture",
            namespace="Pfam_arch",
            weight=0.60,
            shared_ids=seen_archs,
            test_co_member_count=len(test_arch),
            comparison_co_member_count=len(comp_arch),
            overlap_count=len(full_matches),
            examples=seen_archs[:5],
        )
    if partial_matches:
        out["shared_partial_domain_architecture"] = CoMembershipResult(
            axis="shared_partial_domain_architecture",
            namespace="Pfam_arch",
            weight=0.30,
            shared_ids=[f"{a}~{b}:J={j:.2f}" for a, b, j in partial_matches[:MAX_EXAMPLES]],
            test_co_member_count=len(test_arch),
            comparison_co_member_count=len(comp_arch),
            overlap_count=len(partial_matches),
            examples=[f"{a}~{b}" for a, b, _ in partial_matches[:5]],
        )
    return out
