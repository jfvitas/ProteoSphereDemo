"""Read-only backend for the Reference Library screen.

The library pane in the GUI (`gui/model_studio_web_v2/components/screen-library.jsx`)
needs paginated + searchable views over the warehouse. This module
exposes those views as small typed functions; the matching HTTP routes
live in `handlers.py` (`/api/v2/library/<family>`).

Family registry
---------------
Each family below is queryable through the same envelope:

    library_rows(family, *, q="", page=1, per_page=50, tier="any")
        → {"family": str, "page": int, "per_page": int, "total": int,
           "rows": list[dict], "schema": list[str], "live": bool}

`live` is True when the row payload came from the v2 DuckDB catalog,
False when the function fell back to bundled fixtures (e.g. the
catalog isn't populated yet on a fresh checkout). Either way the row
shape is stable per family so the GUI doesn't have to branch.

Why a fallback at all? The same screen has to work on (a) a fully
ingested warehouse, (b) a half-ingested one mid-build, (c) a fresh
checkout with nothing materialised yet. Surfacing the bundled
proteosphere-lite preview when the catalog is empty keeps the demo
flow honest — every cell on screen still corresponds to a real
record, just from the offline preview rather than a live SQL view.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Catalog connection helpers from the ingest module. Re-used here so
# the library screen sees exactly the same row counts as the ingest
# CLI's `status` command.
try:
    from .ingest.catalog import _connect, summary as _ingest_summary  # type: ignore
    _CATALOG_AVAILABLE = True
except Exception:
    _connect = None  # type: ignore[assignment]
    _ingest_summary = None  # type: ignore[assignment]
    _CATALOG_AVAILABLE = False

# Bundled fallback fixtures. Same set the GUI uses today via window.PS_DATA;
# importing them here lets the backend serve them under the unified
# envelope so the GUI can stop hardcoding them.
_FALLBACK_PROTEINS: list[dict[str, Any]] = [
    # UniProt header + warehouse-derived metadata. These match the
    # canonical kinase panel the splitter / training screens already
    # show, so a fresh checkout still demonstrates end-to-end joins.
    {"uniprot": "Q06187", "name": "BTK",   "organism": "Homo sapiens",   "len": 659,
     "pdbs": 47, "family": "Tec kinase",       "tier": "release"},
    {"uniprot": "P00533", "name": "EGFR",  "organism": "Homo sapiens",   "len": 1210,
     "pdbs": 192, "family": "RTK / ErbB",      "tier": "release"},
    {"uniprot": "P00519", "name": "ABL1",  "organism": "Homo sapiens",   "len": 1130,
     "pdbs": 64,  "family": "Abl kinase",       "tier": "release"},
    {"uniprot": "O60674", "name": "JAK2",  "organism": "Homo sapiens",   "len": 1132,
     "pdbs": 41,  "family": "Janus kinase",     "tier": "release"},
    {"uniprot": "P15056", "name": "BRAF",  "organism": "Homo sapiens",   "len": 766,
     "pdbs": 38,  "family": "Raf kinase",       "tier": "release"},
    {"uniprot": "P36888", "name": "FLT3",  "organism": "Homo sapiens",   "len": 993,
     "pdbs": 21,  "family": "RTK / FLT",        "tier": "release"},
    {"uniprot": "P28482", "name": "MAPK1", "organism": "Homo sapiens",   "len": 360,
     "pdbs": 56,  "family": "MAPK",             "tier": "release"},
    {"uniprot": "P11362", "name": "FGFR1", "organism": "Homo sapiens",   "len": 822,
     "pdbs": 78,  "family": "RTK / FGFR",       "tier": "release"},
]

_FALLBACK_LIGANDS: list[dict[str, Any]] = [
    {"id": "lig_ibrutinib",     "name": "Ibrutinib",     "mw": 440.5, "qed": 0.51, "n_pairs":  72, "source": "PDBbind",   "tier": "release"},
    {"id": "lig_acalabrutinib", "name": "Acalabrutinib", "mw": 465.5, "qed": 0.55, "n_pairs":  58, "source": "BindingDB", "tier": "release"},
    {"id": "lig_imatinib",      "name": "Imatinib",      "mw": 493.6, "qed": 0.49, "n_pairs": 184, "source": "PDBbind",   "tier": "release"},
    {"id": "lig_erlotinib",     "name": "Erlotinib",     "mw": 393.4, "qed": 0.61, "n_pairs": 142, "source": "ChEMBL",    "tier": "release"},
    {"id": "lig_ruxolitinib",   "name": "Ruxolitinib",   "mw": 306.4, "qed": 0.72, "n_pairs":  88, "source": "BindingDB", "tier": "release"},
    {"id": "lig_vemurafenib",   "name": "Vemurafenib",   "mw": 489.9, "qed": 0.41, "n_pairs":  64, "source": "PDBbind",   "tier": "release"},
    {"id": "lig_dabrafenib",    "name": "Dabrafenib",    "mw": 519.6, "qed": 0.38, "n_pairs":  52, "source": "ChEMBL",    "tier": "beta"},
    {"id": "lig_evobrutinib",   "name": "Evobrutinib",   "mw": 429.5, "qed": 0.55, "n_pairs":  12, "source": "ChEMBL",    "tier": "lab"},
]

_FALLBACK_EDGES: list[dict[str, Any]] = [
    {"protein": "BTK (Q06187)",   "ligand": "Ibrutinib (1IJ)",    "act": "Kd",   "value": "0.50 nM", "src": "BindingDB", "year": 2013},
    {"protein": "EGFR (P00533)",  "ligand": "Erlotinib (ERL)",    "act": "Ki",   "value": "8.92 nM", "src": "BindingDB", "year": 2018},
    {"protein": "ABL1 (P00519)",  "ligand": "Imatinib (STI)",     "act": "Ki",   "value": "37 nM",   "src": "ChEMBL",    "year": 2002},
    {"protein": "JAK2 (O60674)",  "ligand": "Ruxolitinib (RUX)",  "act": "Ki",   "value": "2.80 nM", "src": "ChEMBL",    "year": 2019},
    {"protein": "BRAF (P15056)",  "ligand": "Vemurafenib (VEM)",  "act": "Ki",   "value": "31 nM",   "src": "ChEMBL",    "year": 2010},
    {"protein": "FLT3 (P36888)",  "ligand": "Gilteritinib",       "act": "Kd",   "value": "0.29 nM", "src": "PDBbind",   "year": 2020},
    {"protein": "MAPK1 (P28482)", "ligand": "SCH-772984",         "act": "IC50", "value": "20 nM",   "src": "BindingDB", "year": 2013},
    {"protein": "FGFR1 (P11362)", "ligand": "Erdafitinib",        "act": "Ki",   "value": "1.20 nM", "src": "BindingDB", "year": 2020},
]

_FALLBACK_STRUCTURES: list[dict[str, Any]] = [
    {"pdb": "4ZLZ",     "title": "BTK + covalent ibrutinib",      "resolution": "1.55 A", "year": 2015, "ligand": "1E8", "method": "X-ray"},
    {"pdb": "3GEN",     "title": "BTK apo kinase domain",          "resolution": "2.30 A", "year": 2009, "ligand": "-",   "method": "X-ray"},
    {"pdb": "1IEP",     "title": "ABL1 + imatinib (Gleevec)",      "resolution": "2.10 A", "year": 2002, "ligand": "STI", "method": "X-ray"},
    {"pdb": "1M17",     "title": "EGFR + erlotinib",                "resolution": "2.60 A", "year": 2002, "ligand": "AQ4", "method": "X-ray"},
    {"pdb": "5VC4",     "title": "JAK2 + ruxolitinib",              "resolution": "1.85 A", "year": 2017, "ligand": "RUX", "method": "X-ray"},
    {"pdb": "AF-Q06187","title": "BTK . AlphaFold",                 "resolution": "-",      "year": 2022, "ligand": "-",   "method": "Predicted"},
]

_FALLBACK_MOTIFS: list[dict[str, Any]] = [
    {"name": "Pkinase (PF00069)",  "src": "Pfam",    "n": 612408, "ex": "BTK . Q06187"},
    {"name": "HRD catalytic loop", "src": "ELM",     "n": 188402, "ex": "BTK . 540-542"},
    {"name": "P-loop (Gly-rich)",  "src": "Prosite", "n":  94201, "ex": "BTK . 411-416"},
    {"name": "SH2 phosphopeptide", "src": "Pfam",    "n": 208991, "ex": "STAT3 . P40763"},
    {"name": "DFG motif",          "src": "ELM",     "n":  74002, "ex": "BTK . 539-541"},
]

# Per-family map: column-list used for both response schema reporting
# AND for the q (search) filter. Search runs against the joined string
# of every column listed below so the GUI's single search box behaves
# like a "find anywhere in row" filter without needing per-family
# query DSL knowledge.
_SCHEMA: dict[str, list[str]] = {
    "proteins":   ["uniprot", "name", "organism", "len", "pdbs", "family", "tier"],
    "ligands":    ["id", "name", "mw", "qed", "n_pairs", "source", "tier"],
    "edges":      ["protein", "ligand", "act", "value", "src", "year"],
    "structures": ["pdb", "title", "resolution", "method", "ligand", "year"],
    "motifs":     ["name", "src", "n", "ex"],
    "sources":    ["id", "name", "kind", "rows", "updated", "scope"],
    "releases":   ["id", "version", "published", "current", "status",
                   "n_sources", "n_rows", "n_leakage_groups"],
}


# Cached union of every protein-bearing view in the v2 catalog. Rebuilt
# once per Python process; the underlying parquet partitions don't
# change without a server restart so this is safe to memoise. Lookup
# is O(1) per accession after the first call.
_PROTEIN_INVENTORY_CACHE: dict[str, Any] | None = None


def _build_protein_inventory(con) -> dict[str, Any]:
    """Union every protein-bearing view in the v2 catalog into one
    {accession → metadata} dict.

    Sources scanned:
      * `*_proteins`            (davis_proteins, kiba_proteins) — full
                                metadata (sequence, length) at the
                                benchmark's own keying (gene symbol for
                                Davis, UniProt accession for KIBA).
      * `*_bridge_uniprot`      (davis/kiba/hippie/huri/gtopdb/3did) —
                                cross-source bridges that map a source's
                                native ID to a UniProt accession.
      * `gtopdb_targets`        — direct human/rat/mouse UniProt
                                accessions plus HGNC symbol and family.

    The previous backend only counted ``*_proteins`` views (442+229 =
    671). The bridge tables add ~85,000 more UniProt accessions across
    HIPPIE / HuRI / GtoPdb that the Library tab was completely hiding.

    Each accession's record carries:
        uniprot, sources(set), len, name, organism, family, tier
    Where ``sources`` is the set of benchmark labels that touch this
    accession — surfacing both "what's available" AND "where it came
    from" in one place.
    """
    inv: dict[str, dict[str, Any]] = {}
    def _upsert(acc: str, **kw: Any) -> None:
        if not acc or acc == "None":
            return
        r = inv.setdefault(acc, {
            "uniprot": acc, "sources": set(), "len": 0,
            "name": "", "organism": "", "family": "", "tier": "release",
        })
        for k, v in kw.items():
            if k == "sources" and v:
                r["sources"].update(v if isinstance(v, (set, list, tuple)) else [v])
            elif v and not r.get(k):
                r[k] = v

    # Helper: list every relation (view OR table) matching a LIKE
    # pattern. The demo warehouse materialises source views as tables
    # for self-containment (no parquet partition dependency), so we
    # have to look at both `duckdb_views()` AND `duckdb_tables()`.
    # The full v2 catalog uses views; this query gives back both.
    def _relations_matching(pattern: str) -> list[str]:
        rels: list[str] = []
        for q in (
            f"SELECT view_name AS n FROM duckdb_views() "
            f"WHERE view_name LIKE '{pattern}' AND internal=false",
            f"SELECT table_name AS n FROM duckdb_tables() "
            f"WHERE table_name LIKE '{pattern}' AND internal=false",
        ):
            try:
                for (n,) in con.execute(q).fetchall():
                    rels.append(n)
            except Exception:
                pass
        # Dedupe while preserving order.
        seen = set()
        out: list[str] = []
        for n in rels:
            if n not in seen:
                seen.add(n)
                out.append(n)
        return out

    # Pass 1 — `*_proteins` relations (carry sequence_length we want)
    try:
        proteins_rels = _relations_matching("%_proteins")
        for vname in proteins_rels:
            rows = con.execute(
                f"SELECT protein_key, protein_ref, sequence_length, source "
                f"FROM {vname}"
            ).fetchall()
            for pkey, pref, slen, src in rows:
                _upsert(str(pkey or "").strip(),
                        sources=[str(src or vname.replace("_proteins",""))],
                        len=int(slen) if slen else 0,
                        name=str(pref or pkey or ""))
    except Exception:
        pass

    # Pass 2 — `*_bridge_uniprot` relations (canonical UniProt mappings)
    try:
        bridge_views = [(n,) for n in _relations_matching("%_bridge_uniprot")]
        for (vname,) in bridge_views:
            rows = con.execute(
                f"SELECT source_id, source_key, uniprot, confidence "
                f"FROM {vname} WHERE uniprot IS NOT NULL"
            ).fetchall()
            for src_id, src_key, uni, conf in rows:
                acc = str(uni or "").strip()
                if not acc:
                    continue
                _upsert(acc,
                        sources=[str(src_id or vname.replace("_bridge_uniprot",""))],
                        name=str(src_key or ""),
                        tier="release" if conf == "exact" else "beta")
    except Exception:
        pass

    # Pass 3 — gtopdb_targets (carries explicit family + HGNC name)
    try:
        rows = con.execute(
            "SELECT human_uniprot, target_name, family_name, hgnc_symbol "
            "FROM gtopdb_targets WHERE human_uniprot IS NOT NULL"
        ).fetchall()
        for huni, tname, fname, hgnc in rows:
            _upsert(str(huni or "").strip(),
                    sources=["gtopdb"],
                    name=str(tname or hgnc or ""),
                    family=str(fname or ""))
    except Exception:
        pass

    # Materialise sources to comma-joined string + sorted accession list
    # for the GUI paginator.
    accs_sorted = sorted(inv.keys())
    return {
        "accessions": accs_sorted,
        "by_acc": inv,
        "total": len(accs_sorted),
    }


def _live_proteins(con, q: str, page: int, per_page: int, tier: str) -> tuple[list[dict[str, Any]], int]:
    """Paginated browse over the v2 catalog's full protein inventory —
    `*_proteins` + every `*_bridge_uniprot` + gtopdb_targets. Returns
    rows in the canonical GUI schema {uniprot, name, organism, len,
    pdbs, family, tier}.

    Previously this returned only ~671 rows because it counted just
    the two `*_proteins` views. The new union surfaces every UniProt
    accession the v2 catalog has touched across HIPPIE / HuRI / GtoPdb
    / Davis / KIBA — order of magnitude more (typically ~85k).
    """
    global _PROTEIN_INVENTORY_CACHE
    try:
        if _PROTEIN_INVENTORY_CACHE is None:
            _PROTEIN_INVENTORY_CACHE = _build_protein_inventory(con)
        inv = _PROTEIN_INVENTORY_CACHE
        accs = inv["accessions"]
        by_acc = inv["by_acc"]

        # Apply q filter: substring match on accession + name fields,
        # case-insensitive.
        if q:
            needle = q.lower()
            def _matches(acc: str) -> bool:
                if needle in acc.lower():
                    return True
                r = by_acc[acc]
                if needle in (r.get("name") or "").lower():
                    return True
                if needle in (r.get("family") or "").lower():
                    return True
                return False
            filtered = [a for a in accs if _matches(a)]
        else:
            filtered = accs
        # Apply tier filter (best-effort — most rows are 'release').
        if tier and tier != "any":
            tier_low = tier.lower()
            filtered = [a for a in filtered
                         if (by_acc[a].get("tier","release")).lower() == tier_low]

        total = len(filtered)
        offset = max(0, (page - 1) * per_page)
        page_accs = filtered[offset:offset + per_page]
        out: list[dict[str, Any]] = []
        for acc in page_accs:
            r = by_acc[acc]
            out.append({
                "uniprot":  acc,
                "name":     r.get("name") or acc,
                "organism": r.get("organism", ""),
                "len":      r.get("len") or 0,
                "pdbs":     0,
                "family":   ", ".join(sorted(r.get("sources", set()))) if r.get("sources") else (r.get("family") or ""),
                "tier":     r.get("tier", "release"),
            })
        return out, total
    except Exception:
        return [], 0


def _match_q(row: dict[str, Any], q: str, cols: list[str]) -> bool:
    """Case-insensitive substring match across `cols` for fallback rows."""
    if not q:
        return True
    needle = q.lower()
    for c in cols:
        v = row.get(c)
        if v is None:
            continue
        if needle in str(v).lower():
            return True
    return False


def _filter_tier(row: dict[str, Any], tier: str) -> bool:
    if not tier or tier == "any":
        return True
    return str(row.get("tier", "")).lower() == tier.lower()


def _full_warehouse_proteins_info() -> dict[str, Any]:
    """Probe the main ProteoSphere warehouse's `proteins` partition for
    its row count + reachability. Used to expose the honest "X cataloged
    · Y in full warehouse" badge on the Library tab without trying to
    enumerate 262 million UniProt accessions (which is an OOM hazard).

    Returns:
        {
          "reachable": bool,
          "total":     int  (rows in partition, or 0 if unreachable),
          "path":      str  (resolved partition file path),
          "schema":    list[str]  (column names),
        }

    Cached per Python process — the partition doesn't change without a
    warehouse rebuild + server restart, and `pq.read_metadata` is fast
    even on a 14GB parquet.
    """
    global _FULL_WAREHOUSE_INFO_CACHE
    if _FULL_WAREHOUSE_INFO_CACHE is not None:
        return _FULL_WAREHOUSE_INFO_CACHE
    out: dict[str, Any] = {"reachable": False, "total": 0, "path": "", "schema": []}
    try:
        # Import the main proteosphere package lazily so this v2 module
        # doesn't hard-require the reference_library checkout to be on
        # PYTHONPATH at boot.
        from proteosphere import Config  # type: ignore[import-not-found]
        import pyarrow.parquet as pq
        cfg = Config.discover()
        p = cfg.family_partition("proteins")
        if p.is_file():
            md = pq.read_metadata(p)
            schema = pq.read_schema(p)
            out = {
                "reachable": True,
                "total": int(md.num_rows),
                "path": str(p),
                "schema": [f.name for f in schema],
            }
    except Exception:
        pass
    _FULL_WAREHOUSE_INFO_CACHE = out
    return out


def _full_warehouse_proteins_lookup(accessions: list[str]) -> list[dict[str, Any]]:
    """Fast filtered lookup of specific UniProt accessions in the main
    warehouse's proteins partition. Uses pyarrow's predicate pushdown
    (`filters=[("accession", "in", ...)]`) so we never materialise more
    than the matching rows — typical query reads <1k rows even though
    the partition is 262M rows total.

    Returns canonical row dicts ready for the Library schema:
        {uniprot, name, organism, len, pdbs, family, tier}
    """
    if not accessions:
        return []
    info = _full_warehouse_proteins_info()
    if not info["reachable"]:
        return []
    try:
        import pyarrow.parquet as pq
        tbl = pq.read_table(
            info["path"],
            columns=["accession", "entry_name", "taxon_id",
                     "uniref50_cluster", "uniref90_cluster"],
            filters=[("accession", "in", list(accessions))],
        )
        df = tbl.to_pandas()
        out: list[dict[str, Any]] = []
        for _, r in df.iterrows():
            out.append({
                "uniprot":  str(r["accession"]),
                "name":     str(r.get("entry_name") or r["accession"]),
                "organism": str(r.get("taxon_id") or ""),
                "len":      0,           # not in this partition
                "pdbs":     0,
                "family":   str(r.get("uniref50_cluster") or ""),
                "tier":     "release",
                "source_origin": "full_warehouse",
            })
        return out
    except Exception:
        return []


# Cached protein-partition info so we only pq.read_metadata once
# (still cheap, but no need to do it per request).
_FULL_WAREHOUSE_INFO_CACHE: dict[str, Any] | None = None


def library_rows(
    family: str,
    *,
    q: str = "",
    page: int = 1,
    per_page: int = 50,
    tier: str = "any",
) -> dict[str, Any]:
    """Return a paginated chunk of rows for one library family.

    Single entry point so the HTTP handler stays simple: it just maps
    the family path-param + query string to this call. Per-family
    branching lives here.
    """
    page = max(1, int(page))
    per_page = max(1, min(500, int(per_page)))
    family = family.lower()
    if family not in _SCHEMA:
        return {
            "family": family, "page": page, "per_page": per_page,
            "total": 0, "rows": [], "schema": [], "live": False,
            "error": f"Unknown family '{family}'. "
                     f"Known: {sorted(_SCHEMA)}",
        }

    rows: list[dict[str, Any]] = []
    total = 0
    live = False

    # Live catalog path for the families we know how to query.
    full_warehouse_extras: list[dict[str, Any]] = []
    if _CATALOG_AVAILABLE and family == "proteins":
        try:
            con = _connect()
            try:
                rows, total = _live_proteins(con, q, page, per_page, tier)
            finally:
                con.close()
            live = bool(rows)
        except Exception:
            live = False
        # When the user enters an explicit search query that LOOKS like
        # a UniProt accession ([A-NR-Z][0-9][A-Z0-9]{3}[0-9]) and we
        # didn't already surface it from the v2 catalog, punch through
        # to the main warehouse partition for a fast filtered lookup
        # (262M-row scan is impossible; predicate-pushdown lookup is
        # cheap). This lets the user pull up ANY UniProt accession
        # the warehouse knows about, even if it's never been touched
        # by a benchmark in the v2 catalog.
        if q and family == "proteins":
            import re as _re
            cataloged_ids = {r.get("uniprot","") for r in rows}
            # Extract every token in q that looks like an accession.
            cand = [tok for tok in _re.findall(
                r"[A-NR-Z][0-9][A-Z0-9]{3}[0-9](?:-[0-9]+)?", q.upper()
            ) if tok not in cataloged_ids]
            if cand:
                full_warehouse_extras = _full_warehouse_proteins_lookup(cand)

    # Sources tab: pull from the catalog's `summary()` if possible (this
    # is the same call the ingest status CLI uses).
    if not live and family == "sources" and _CATALOG_AVAILABLE:
        try:
            summ = _ingest_summary() or {}
            rows_raw = summ.get("ingest_runs") or []
            for r in rows_raw:
                rc = r.get("row_counts") or {}
                total_rows = sum(int(v) for v in rc.values() if isinstance(v, (int, float)))
                rows.append({
                    "id":      r.get("source_id", ""),
                    "name":    r.get("source_id", "").replace("_", " ").title(),
                    "kind":    "ingest_run",
                    "rows":    total_rows,
                    "updated": r.get("registered_at", ""),
                    "scope":   r.get("snapshot_id", ""),
                })
            total = len(rows)
            live = True
        except Exception:
            live = False

    # Fallback to bundled fixtures for any family we couldn't serve from
    # the catalog. The fixtures are intentionally small (≤ 10 rows each)
    # so client-side pagination is cheap.
    if not live:
        if family == "proteins":
            pool = _FALLBACK_PROTEINS
        elif family == "ligands":
            pool = _FALLBACK_LIGANDS
        elif family == "edges":
            pool = _FALLBACK_EDGES
        elif family == "structures":
            pool = _FALLBACK_STRUCTURES
        elif family == "motifs":
            pool = _FALLBACK_MOTIFS
        elif family == "sources":
            # Stub fixtures so a fresh checkout shows the source-coverage
            # matrix even without catalog state.
            pool = [
                {"id": "uniprot",   "name": "UniProt",          "kind": "protein",     "rows": 262_440_545, "updated": "2026-04-12", "scope": "public_redistributable"},
                {"id": "rcsb_pdbe", "name": "RCSB / PDBe",       "kind": "structure",   "rows":     968_580, "updated": "2026-04-12", "scope": "public_redistributable"},
                {"id": "alphafold", "name": "AlphaFold DB",      "kind": "structure",   "rows": 214_000_000, "updated": "2026-03-28", "scope": "public_redistributable"},
                {"id": "intact",    "name": "IntAct",            "kind": "ppi",         "rows":   1_184_201, "updated": "2026-04-09", "scope": "public_redistributable"},
                {"id": "string",    "name": "STRING",            "kind": "ppi",         "rows":  20_000_000, "updated": "2026-04-11", "scope": "internal_only"},
                {"id": "bindingdb", "name": "BindingDB",         "kind": "affinity",    "rows":   2_900_000, "updated": "2026-04-08", "scope": "internal_only"},
                {"id": "pdbbind",   "name": "PDBbind",           "kind": "affinity",    "rows":      19_443, "updated": "2026-04-07", "scope": "restricted"},
                {"id": "elm",       "name": "ELM",               "kind": "motif",       "rows":     262_840, "updated": "2026-04-05", "scope": "public_redistributable"},
            ]
        elif family == "releases":
            pool = [
                {"id": "rel_v2026.04", "version": "v2026.04", "published": "2026-04-12", "current": True,  "status": "ready",
                 "n_sources": 12, "n_rows": 326_482_905, "n_leakage_groups": 8_142},
                {"id": "rel_v2026.03", "version": "v2026.03", "published": "2026-03-08", "current": False, "status": "ready",
                 "n_sources": 11, "n_rows": 318_204_117, "n_leakage_groups": 7_881},
                {"id": "rel_v2026.02", "version": "v2026.02", "published": "2026-02-15", "current": False, "status": "ready",
                 "n_sources": 11, "n_rows": 312_007_004, "n_leakage_groups": 7_690},
                {"id": "rel_v2026.01", "version": "v2026.01", "published": "2026-01-09", "current": False, "status": "ready",
                 "n_sources": 10, "n_rows": 305_002_180, "n_leakage_groups": 7_488},
            ]
        else:
            pool = []
        cols = _SCHEMA[family]
        filtered = [r for r in pool if _match_q(r, q, cols) and _filter_tier(r, tier)]
        total = len(filtered)
        start = (page - 1) * per_page
        rows = filtered[start:start + per_page]

    # Family-level metadata. For `proteins` we surface the FULL warehouse
    # count (typically 262M+ UniProt accessions) separately from the
    # cataloged total (typically ~85k) so the GUI can show both
    # numbers honestly. Other families return their cataloged count as
    # the only number, since they don't have a parallel warehouse-side
    # partition.
    meta: dict[str, Any] = {}
    if family == "proteins":
        info = _full_warehouse_proteins_info()
        meta = {
            "cataloged_total":      total,
            "full_warehouse_total": info["total"],
            "full_warehouse_reachable": info["reachable"],
            "full_warehouse_lookup_notes":
                "Pass `q=<UniProt accession>` to look up any of the "
                f"{info['total']:,} UniProt entries even if they're "
                f"not in the v2 catalog yet."
                if info["reachable"] else
                "Full warehouse partition not reachable from this "
                "process; only v2 catalog accessions are searchable.",
        }
        # Append any warehouse-only matches (deduped against rows already
        # in the page via uniprot identity).
        if full_warehouse_extras:
            seen = {r.get("uniprot","") for r in rows}
            new_rows = [r for r in full_warehouse_extras if r.get("uniprot") not in seen]
            rows = rows + new_rows
            total += len(new_rows)

    return {
        "family":   family,
        "page":     page,
        "per_page": per_page,
        "total":    total,
        "rows":     rows,
        "schema":   _SCHEMA[family],
        "live":     live,
        "meta":     meta,
    }


def warehouse_schema_sql() -> str:
    """Render a DuckDB CREATE-statement schema dump for the warehouse.

    Used by the "Export schema" button on the Library screen. We
    introspect the live catalog when available and emit a synthetic
    template otherwise so the button always returns something
    actionable.
    """
    out: list[str] = [
        "-- ProteoSphere v2 warehouse schema",
        "-- Generated by api/model_studio/v2/library.py:warehouse_schema_sql()",
        "-- One CREATE TABLE per warehouse family + one CREATE VIEW per ingested source.",
        "",
    ]
    if _CATALOG_AVAILABLE:
        try:
            con = _connect()
            try:
                # Live introspection: list all user-defined tables + views
                # and emit their CREATE statements via duckdb's metadata.
                tables = con.execute(
                    "SELECT table_name FROM duckdb_tables() "
                    "WHERE internal = false ORDER BY table_name"
                ).fetchall()
                for (tn,) in tables:
                    cols = con.execute(
                        f"PRAGMA table_info('{tn}')"
                    ).fetchall()
                    out.append(f"CREATE TABLE {tn} (")
                    for i, c in enumerate(cols):
                        nullable = "" if c[3] else " NOT NULL"
                        comma = "," if i < len(cols) - 1 else ""
                        out.append(f"    {c[1]:<32} {c[2]}{nullable}{comma}")
                    out.append(");")
                    out.append("")
                views = con.execute(
                    "SELECT view_name, sql FROM duckdb_views() "
                    "WHERE internal = false ORDER BY view_name"
                ).fetchall()
                for vn, sql in views:
                    out.append(f"-- VIEW: {vn}")
                    out.append(f"CREATE OR REPLACE VIEW {vn} AS {sql};")
                    out.append("")
                return "\n".join(out)
            finally:
                con.close()
        except Exception as exc:
            out.append(f"-- Catalog introspection failed: {exc}")
            out.append("-- Falling back to template.")
            out.append("")
    # Template (used when no catalog OR introspection failed).
    out.extend([
        "CREATE TABLE proteins (",
        "    uniprot       VARCHAR NOT NULL,",
        "    name          VARCHAR,",
        "    organism      VARCHAR,",
        "    len           INTEGER,",
        "    pdbs          INTEGER,",
        "    family        VARCHAR,",
        "    tier          VARCHAR",
        ");",
        "",
        "CREATE TABLE ligands (",
        "    id            VARCHAR NOT NULL,",
        "    name          VARCHAR,",
        "    mw            DOUBLE,",
        "    qed           DOUBLE,",
        "    n_pairs       INTEGER,",
        "    source        VARCHAR,",
        "    tier          VARCHAR",
        ");",
        "",
        "-- Edges, structures, motifs, leakage_groups, sources, releases follow",
        "-- the same shape as the GUI's library rows; see",
        "-- api/model_studio/v2/library.py:_SCHEMA for the full per-family",
        "-- column list.",
    ])
    return "\n".join(out)


# Per-row external-source URL resolution for the Detail drawer's
# "View source" action. Maps a (family, row) tuple to an HTTPS URL
# pointing at the canonical upstream record. None ⇒ no known source.
def source_url(family: str, row: dict[str, Any]) -> str | None:
    """Best-effort: return the upstream URL for one library row."""
    family = (family or "").lower()
    if family == "proteins":
        u = row.get("uniprot") or row.get("id")
        if u: return f"https://www.uniprot.org/uniprotkb/{u}/entry"
    if family == "structures":
        p = row.get("pdb")
        if p:
            if str(p).startswith("AF-"):
                acc = str(p).split("-", 1)[1].split("-")[0]
                return f"https://alphafold.ebi.ac.uk/entry/{acc}"
            return f"https://www.rcsb.org/structure/{p}"
    if family == "ligands":
        # BindingDB / PDBbind don't expose stable URLs by ligand_id; the
        # PDB chemical-component code (when present) is the best public
        # anchor. The GUI's ligand rows don't carry chem-comp codes yet,
        # so we punt to a search URL.
        n = row.get("name")
        if n: return f"https://pubchem.ncbi.nlm.nih.gov/#query={n}"
    if family == "sources":
        sid = (row.get("id") or "").lower()
        UPSTREAM = {
            "uniprot":    "https://www.uniprot.org",
            "rcsb_pdbe":  "https://www.rcsb.org",
            "alphafold":  "https://alphafold.ebi.ac.uk",
            "intact":     "https://www.ebi.ac.uk/intact",
            "string":     "https://string-db.org",
            "bindingdb":  "https://www.bindingdb.org",
            "pdbbind":    "http://www.pdbbind.org.cn",
            "elm":        "http://elm.eu.org",
            "biogrid":    "https://thebiogrid.org",
            "chembl":     "https://www.ebi.ac.uk/chembl",
        }
        if sid in UPSTREAM:
            return UPSTREAM[sid]
    return None
