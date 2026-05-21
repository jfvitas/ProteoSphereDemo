"""Warehouse-backed identifier resolution for paper-format inputs.

Many published paper datasets ship identifiers other than UniProt
accessions: PDB IDs alone (DeepTGIN, Struct2Graph, PDBbind core), full
amino-acid sequences (GraphDTA, DeepDTA), or gene names / cross-references
(D-SCRIPT FlyBase IDs). This module attempts to bridge those identifiers
to canonical UniProt accessions using the warehouse.

Resolver coverage:

- ``resolve_pdb_to_accessions(pdb_id)`` -> ``list[str]``:
  Uses ``structure_units`` and ``proteins`` to expand a PDB ID into the
  set of UniProt accessions for its chains. Fully supported by the
  condensed warehouse.

- ``resolve_sequence_to_accession(sequence)`` -> ``list[str]``:
  Hash the input sequence with MD5 and look up matching accessions in the
  ``sequence_index`` partition (Tier-1: Swiss-Prot only, ~570k entries,
  ~25 MB on disk). Covers ~99% of paper-cited proteins.

- ``resolve_gene_name_to_accession(name)`` -> ``list[str]``:
  First-pass lookup against the ``cross_references`` partition (HGNC,
  MGI, RGD, FlyBase, WormBase, SGD, ZFIN, TAIR, Gene_Name, GeneID,
  RefSeq, Ensembl, PDB). Falls back to ``entry_name`` parsing for
  Swiss-Prot canonical entries.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

from proteosphere.config import Config
from proteosphere.warehouse import _norm_acc


_AA_CLEAN_RE = re.compile(r"[^A-Z]")


def _hash_sequence(seq: str) -> str:
    """MD5 of an amino-acid sequence, after upper-casing and stripping
    everything that isn't a letter A-Z."""
    cleaned = _AA_CLEAN_RE.sub("", str(seq).upper())
    return hashlib.md5(cleaned.encode("ascii")).hexdigest()

# Format of UniProt entry names: GENE_SPECIES (e.g., "EGFR_HUMAN", "ABL1_HUMAN")
ENTRY_NAME_RE = re.compile(r"^([A-Z0-9]{1,16})_([A-Z]{2,5})$")


@dataclass
class ResolutionResult:
    requested: str
    resolved: list[str]  # UniProt accessions
    method: str  # "pdb_expansion" | "entry_name_match" | "sequence_hash" | "unresolved"
    note: str = ""


@dataclass
class ResolutionReport:
    results: list[ResolutionResult] = field(default_factory=list)
    coverage_fraction: float = 0.0
    method_counts: dict[str, int] = field(default_factory=dict)

    def add(self, r: ResolutionResult) -> None:
        self.results.append(r)
        self.method_counts[r.method] = self.method_counts.get(r.method, 0) + 1

    def finalize(self) -> None:
        n = len(self.results) or 1
        resolved_ok = sum(1 for r in self.results if r.resolved)
        self.coverage_fraction = resolved_ok / n


# ---------------------------------------------------------------------------
# PDB-ID -> UniProt accession expansion
# ---------------------------------------------------------------------------


def _load_pdb_expansion_index(config: Config) -> dict[str, list[str]]:
    """Build a {pdb_id_uppercase: [accession, ...]} mapping from the warehouse.

    Uses ``structure_units.structure_id`` (the PDB ID) joined to
    ``structure_units.protein_ref`` (which is "protein:<accession>"), grouped.
    """
    su_path = config.family_partition("structure_units")
    table = pq.read_table(
        su_path, columns=["structure_id", "protein_ref"]
    ).to_pandas()
    table = table.dropna(subset=["structure_id", "protein_ref"])
    table["pdb"] = table["structure_id"].astype(str).str.upper()
    table["acc"] = table["protein_ref"].astype(str).str.replace("protein:", "", regex=False)
    table = table[table["acc"].str.match(r"^[A-Z0-9]+$", na=False)]
    grouped = table.groupby("pdb")["acc"].apply(lambda xs: sorted(set(xs))).to_dict()
    return grouped


@lru_cache(maxsize=1)
def _cached_pdb_index_for(warehouse_root: str) -> dict[str, list[str]]:
    config = Config.discover(warehouse_root=warehouse_root)
    return _load_pdb_expansion_index(config)


def resolve_pdbs_to_accessions(
    config: Config, pdb_ids: Iterable[str]
) -> dict[str, list[str]]:
    """Look up UniProt accessions for a set of PDB IDs."""
    index = _cached_pdb_index_for(str(config.warehouse_root))
    out: dict[str, list[str]] = {}
    for raw in pdb_ids:
        if not raw:
            continue
        key = str(raw).strip().upper()
        if key in index:
            out[key] = index[key]
        else:
            out[key] = []
    return out


# ---------------------------------------------------------------------------
# Sequence hash -> accession lookup (Tier 1)
# ---------------------------------------------------------------------------


def _lookup_hashes_via_duckdb(
    config: Config, partition_family: str, hashes: set[str]
) -> dict[str, list[str]]:
    """Look up a set of MD5 hashes against either ``sequence_index`` or
    ``domain_sequence_index`` using DuckDB predicate pushdown."""
    if not hashes:
        return {}
    import duckdb

    column = "sequence_md5" if partition_family == "sequence_index" else "domain_md5"
    try:
        path = config.family_partition(partition_family)
    except KeyError:
        return {}
    if not path.is_file():
        return {}
    src = str(path).replace("\\", "/")
    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    keys_df = pd.DataFrame({"h": list(hashes)})
    con.register("_h", keys_df)
    rows = con.execute(
        f"""
        SELECT h.h AS hash, xr.accession
        FROM _h h
        INNER JOIN read_parquet('{src}') xr ON xr.{column} = h.h
        """
    ).fetchall()
    con.close()
    out: dict[str, list[str]] = {}
    for h, acc in rows:
        out.setdefault(str(h), []).append(str(acc))
    return out


# kept for backward compatibility but no longer used by resolve_sequences_to_accessions
@lru_cache(maxsize=1)
def _sequence_hash_index(warehouse_root: str) -> dict[str, list[str]]:
    config = Config.discover(warehouse_root=warehouse_root)
    seq_path = config.family_partition("sequence_index")
    if not seq_path.is_file():
        return {}
    table = pq.read_table(seq_path, columns=["accession", "sequence_md5"]).to_pandas()
    out: dict[str, list[str]] = {}
    for acc, h in zip(table["accession"], table["sequence_md5"]):
        out.setdefault(str(h), []).append(str(acc))
    return out


@lru_cache(maxsize=1)
def _domain_hash_index(warehouse_root: str) -> dict[str, list[str]]:
    """Map ``domain_md5 -> [accession, ...]`` for substring resolution."""
    config = Config.discover(warehouse_root=warehouse_root)
    try:
        path = config.family_partition("domain_sequence_index")
    except KeyError:
        return {}
    if not path.is_file():
        return {}
    table = pq.read_table(path, columns=["accession", "domain_md5"]).to_pandas()
    out: dict[str, list[str]] = {}
    for acc, h in zip(table["accession"], table["domain_md5"]):
        out.setdefault(str(h), []).append(str(acc))
    return out


def resolve_sequences_to_accessions(
    config: Config, sequences: Iterable[str]
) -> dict[str, list[str]]:
    """Look up UniProt accessions for raw amino-acid sequences.

    Two-stage resolution via DuckDB predicate pushdown (avoids loading
    a 203M-row index into Python memory):

    1. Full-sequence MD5 against ``sequence_index`` (Tier-3 covers all of UniProt).
    2. Domain-level MD5 against ``domain_sequence_index`` for inputs that
       didn't match step 1.
    """
    seqs = [s for s in sequences if s]
    if not seqs:
        return {}
    # Hash every input once
    hash_to_raws: dict[str, list[str]] = {}
    for raw in seqs:
        h = _hash_sequence(raw)
        hash_to_raws.setdefault(h, []).append(raw)

    # Stage 1: full-sequence lookup
    full_hits = _lookup_hashes_via_duckdb(config, "sequence_index", set(hash_to_raws))

    # Stage 2: domain lookup for inputs that missed stage 1
    missing = {h for h in hash_to_raws if h not in full_hits}
    domain_hits = _lookup_hashes_via_duckdb(config, "domain_sequence_index", missing) if missing else {}

    out: dict[str, list[str]] = {raw: [] for raw in seqs}
    for h, accs in full_hits.items():
        for raw in hash_to_raws.get(h, []):
            out[raw] = accs
    for h, accs in domain_hits.items():
        for raw in hash_to_raws.get(h, []):
            out[raw] = accs
    return out


# ---------------------------------------------------------------------------
# Cross-reference lookup (Tier 1: gene names, FlyBase, MGI, etc.)
# ---------------------------------------------------------------------------


def _normalize_xref_key(raw: str) -> str:
    s = str(raw).strip().upper()
    if "." in s:
        head, _, tail = s.partition(".")
        if head.isdigit():
            s = tail
    return s


def resolve_external_ids(
    config: Config,
    ids: Iterable[str],
    databases: Iterable[str] | None = None,
) -> dict[str, list[str]]:
    """Resolve gene names / FlyBase / HGNC / etc. to UniProt accessions.

    Uses DuckDB to scan the cross_references parquet with predicate
    pushdown. Avoids building a 189M-row dictionary in memory. A batch
    of N input IDs is answered with a single DuckDB query that hits
    only the row groups that contain matching external_ids.
    """
    raw_ids = [r for r in ids if r]
    if not raw_ids:
        return {}

    import duckdb

    # Map normalized key back to all raw inputs that produced it
    norm_to_raws: dict[str, list[str]] = {}
    for raw in raw_ids:
        key = _normalize_xref_key(raw)
        if key:
            norm_to_raws.setdefault(key, []).append(raw)

    xref_path = config.family_partition("cross_references")
    if not xref_path.is_file() or not norm_to_raws:
        return {raw: [] for raw in raw_ids}

    db_filter = set(databases) if databases else None
    src = str(xref_path).replace("\\", "/")

    con = duckdb.connect()
    con.execute("PRAGMA threads=8")
    # Register the input keys as a Pandas DataFrame view (fast, zero-copy)
    keys_df = pd.DataFrame({"key": list(norm_to_raws.keys())})
    con.register("_q", keys_df)
    if db_filter:
        db_clause = "AND xr.database IN (" + ",".join(
            "'" + d.replace("'", "''") + "'" for d in db_filter
        ) + ")"
    else:
        db_clause = ""
    rows = con.execute(
        f"""
        SELECT q.key, xr.database, xr.accession
        FROM _q q
        INNER JOIN read_parquet('{src}') xr
          ON xr.external_id = q.key
        {db_clause}
        """
    ).fetchall()
    con.close()

    by_key: dict[str, set[str]] = {}
    for key, _db, acc in rows:
        by_key.setdefault(key, set()).add(acc)

    out: dict[str, list[str]] = {raw: [] for raw in raw_ids}
    for key, accs in by_key.items():
        for raw in norm_to_raws.get(key, []):
            out[raw] = sorted(accs)
    return out


# ---------------------------------------------------------------------------
# Gene-name / entry-name lookup (best-effort, fallback to entry_name parsing)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _entry_name_index(warehouse_root: str) -> dict[str, list[str]]:
    """Map gene-name prefix (e.g., "EGFR") -> list of UniProt accessions."""
    config = Config.discover(warehouse_root=warehouse_root)
    proteins_path = config.family_partition("proteins")
    table = pq.read_table(proteins_path, columns=["accession", "entry_name"]).to_pandas()
    table = table.dropna(subset=["entry_name", "accession"])
    table["entry_name"] = table["entry_name"].astype(str).str.upper()
    table["accession"] = table["accession"].astype(str)
    out: dict[str, list[str]] = {}
    for entry, acc in zip(table["entry_name"], table["accession"]):
        m = ENTRY_NAME_RE.match(entry)
        if not m:
            continue
        gene = m.group(1)
        out.setdefault(gene, []).append(acc)
    return out


def resolve_gene_names_to_accessions(
    config: Config, names: Iterable[str], species_filter: str | None = None
) -> dict[str, list[str]]:
    """Look up UniProt accessions for a set of gene names.

    Two-stage resolution:
    1. Cross-references partition (HGNC, MGI, RGD, FlyBase, WormBase, SGD,
       ZFIN, TAIR, Gene_Name, GeneID, RefSeq, Ensembl) — fast, comprehensive.
    2. Entry-name parsing (``GENE_SPECIES`` form) — fallback for entries
       whose cross-references happen not to be in the condensed index.
    """
    names_list = [str(n).strip() for n in names if n]
    if not names_list:
        return {}

    out = resolve_external_ids(config, names_list)

    # Fallback: entry-name parsing for any names that didn't resolve
    entry_index = _entry_name_index(str(config.warehouse_root))
    for raw in names_list:
        if out.get(raw):
            continue
        key = raw.upper().split(".")[-1]
        if key in entry_index:
            out[raw] = entry_index[key]
    return out


# ---------------------------------------------------------------------------
# Public entry: build a fully-resolved canonical DataFrame
# ---------------------------------------------------------------------------


def expand_pdb_only_dataframe(
    df: pd.DataFrame,
    pdb_column: str,
    config: Config,
) -> tuple[pd.DataFrame, ResolutionReport]:
    """Expand a single-column PDB DataFrame to one row per (pdb_id, accession).

    Used when an input file (DeepTGIN, Struct2Graph, PDBbind core lists)
    ships PDB IDs alone. The audit needs accessions; this function fans them
    out via the warehouse.
    """
    pdb_ids = df[pdb_column].dropna().astype(str).str.upper().unique().tolist()
    expanded = resolve_pdbs_to_accessions(config, pdb_ids)
    report = ResolutionReport()

    rows = []
    for _, src_row in df.iterrows():
        pdb_id = str(src_row[pdb_column]).strip().upper()
        accs = expanded.get(pdb_id, [])
        if not accs:
            report.add(ResolutionResult(pdb_id, [], "unresolved", "no warehouse record"))
            continue
        report.add(ResolutionResult(pdb_id, accs, "pdb_expansion"))
        for acc in accs:
            row = src_row.to_dict()
            row["primary_accession"] = acc
            row["pdb_id"] = pdb_id
            rows.append(row)
    report.finalize()
    return pd.DataFrame(rows), report


def expand_pdb_pair_dataframe(
    df: pd.DataFrame,
    pdb_a_column: str,
    pdb_b_column: str,
    config: Config,
) -> tuple[pd.DataFrame, ResolutionReport]:
    """Expand a Struct2Graph-style PDB-pair table to chain-grounded accession pairs.

    Returns one row per (pdb_a, pdb_b, acc_a, acc_b) tuple. Reports coverage
    so the caller can decide whether to proceed.
    """
    a_ids = df[pdb_a_column].dropna().astype(str).str.upper().unique().tolist()
    b_ids = df[pdb_b_column].dropna().astype(str).str.upper().unique().tolist()
    expanded = resolve_pdbs_to_accessions(config, set(a_ids) | set(b_ids))
    report = ResolutionReport()

    rows = []
    for _, src_row in df.iterrows():
        a = str(src_row[pdb_a_column]).strip().upper()
        b = str(src_row[pdb_b_column]).strip().upper()
        a_accs = expanded.get(a, [])
        b_accs = expanded.get(b, [])
        if not a_accs or not b_accs:
            report.add(
                ResolutionResult(
                    f"{a}--{b}",
                    [],
                    "unresolved",
                    f"a_accs={len(a_accs)}, b_accs={len(b_accs)}",
                )
            )
            continue
        report.add(ResolutionResult(f"{a}--{b}", a_accs + b_accs, "pdb_expansion"))
        for ax in a_accs:
            for bx in b_accs:
                rows.append(
                    {
                        "pdb_a": a,
                        "pdb_b": b,
                        "uniprot_a": ax,
                        "uniprot_b": bx,
                        **{
                            k: v
                            for k, v in src_row.to_dict().items()
                            if k not in (pdb_a_column, pdb_b_column)
                        },
                    }
                )
    report.finalize()
    return pd.DataFrame(rows), report
