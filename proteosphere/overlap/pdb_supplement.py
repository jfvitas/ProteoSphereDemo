"""Local supplements for PDB -> UniProt resolution.

The catalog's ``structure_units`` table is built from PDBe SIFTS, which:

1. Doesn't index every TrEMBL-only entry.
2. Routes obsolete PDB IDs to dead ends (RCSB Data API returns null for
   obsolete entries, so the SIFTS build skips them).
3. Naturally has no entries for proteins that don't exist in UniProt --
   antibody Fab/Fv fragments, computationally designed proteins, and
   synthetic chimeras.

This module loads three small TSV files shipped with the package:

* ``pdb_resolution_supplement.tsv`` -- explicit PDB -> UniProt mappings
  for entries the local SIFTS snapshot misses (typically TrEMBL).
* ``pdb_obsolete_redirects.tsv`` -- old PDB ID -> superseding PDB ID
  for entries that have been replaced.
* ``pdb_unresolvable_known.tsv`` -- known-unresolvable PDBs with a
  ``category`` (``antibody_fab`` / ``designed`` / ``chimera`` /
  ``peptide``) and human-readable description, so the CLI can show the
  user *why* a PDB couldn't resolve rather than silently dropping it.

All three files are user-editable; the loader picks up additions on
the next run with no rebuild needed.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"
_SUPPLEMENT_TSV = _DATA_DIR / "pdb_resolution_supplement.tsv"
_OBSOLETE_TSV = _DATA_DIR / "pdb_obsolete_redirects.tsv"
_UNRESOLVABLE_TSV = _DATA_DIR / "pdb_unresolvable_known.tsv"


@dataclass(frozen=True)
class UnresolvableReason:
    """Why a known-unresolvable PDB can't be mapped to UniProt."""
    category: str
    description: str


def _iter_tsv_rows(path: Path):
    """Yield row dicts from a TSV, skipping comment lines."""
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        # The TSV files have a # comment convention. Strip those before
        # handing the rest to csv.DictReader.
        clean_lines: list[str] = []
        for line in fh:
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            clean_lines.append(line.rstrip("\n"))
    if not clean_lines:
        return
    reader = csv.DictReader(clean_lines, delimiter="\t")
    yield from reader


@lru_cache(maxsize=1)
def load_supplement() -> dict[str, list[str]]:
    """Return ``{pdb_upper: [uniprot, ...]}`` from the supplement TSV."""
    out: dict[str, list[str]] = {}
    for row in _iter_tsv_rows(_SUPPLEMENT_TSV):
        pdb = (row.get("pdb_id") or "").strip().upper()
        accs = (row.get("uniprot_accessions") or "").strip()
        if not pdb or not accs:
            continue
        out[pdb] = [a.strip() for a in accs.split(";") if a.strip()]
    return out


@lru_cache(maxsize=1)
def load_obsolete_redirects() -> dict[str, str]:
    """Return ``{old_pdb_upper: new_pdb_upper}`` from the redirects TSV."""
    out: dict[str, str] = {}
    for row in _iter_tsv_rows(_OBSOLETE_TSV):
        old = (row.get("old_pdb_id") or "").strip().upper()
        new = (row.get("new_pdb_id") or "").strip().upper()
        if old and new:
            out[old] = new
    return out


@lru_cache(maxsize=1)
def load_unresolvable_known() -> dict[str, UnresolvableReason]:
    """Return ``{pdb_upper: UnresolvableReason}`` from the unresolvable TSV."""
    out: dict[str, UnresolvableReason] = {}
    for row in _iter_tsv_rows(_UNRESOLVABLE_TSV):
        pdb = (row.get("pdb_id") or "").strip().upper()
        if not pdb:
            continue
        out[pdb] = UnresolvableReason(
            category=(row.get("category") or "").strip(),
            description=(row.get("description") or "").strip(),
        )
    return out


def apply_obsolete_redirect(pdb: str) -> tuple[str, str | None]:
    """If ``pdb`` is in the obsolete-redirects map, return its successor.

    Returns ``(effective_pdb, original_pdb_if_redirected)``. When no
    redirect applies the second value is None.
    """
    pdb_upper = pdb.strip().upper()
    redirects = load_obsolete_redirects()
    if pdb_upper in redirects:
        return redirects[pdb_upper], pdb_upper
    return pdb_upper, None


def supplement_uniprots(pdb: str) -> list[str]:
    """Return supplementary UniProt accessions for a PDB (may be empty)."""
    return load_supplement().get(pdb.strip().upper(), [])


def unresolvable_reason(pdb: str) -> UnresolvableReason | None:
    """Return the known-unresolvable reason for a PDB, if listed."""
    return load_unresolvable_known().get(pdb.strip().upper())


@dataclass
class ResolutionDetail:
    """Per-PDB resolution outcome, surfaced to the CLI for transparency."""
    requested_pdb: str
    effective_pdb: str       # after applying obsolete redirect
    uniprots: list[str]
    sources: list[str]       # e.g. ["structure_units", "supplement"]
    redirected_from: str | None = None
    unresolvable_reason: UnresolvableReason | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.uniprots)


def merge_resolutions(
    pdb_ids: Iterable[str],
    sifts_map: dict[str, list[str]],
) -> list[ResolutionDetail]:
    """Combine the SIFTS-based ``sifts_map`` with our local supplements.

    Parameters
    ----------
    pdb_ids
        The PDB IDs the user asked about (case-insensitive; uppercased).
    sifts_map
        ``{pdb_upper: [uniprot, ...]}`` from the local ``structure_units``
        table. The caller has already queried it.

    Returns
    -------
    list of :class:`ResolutionDetail`, one per input PDB, with redirects
    applied, supplements merged, and unresolvable reasons attached when
    relevant.
    """
    details: list[ResolutionDetail] = []
    for raw in pdb_ids:
        pdb = raw.strip().upper()
        effective, redirected_from = apply_obsolete_redirect(pdb)
        uniprots: list[str] = []
        sources: list[str] = []
        # 1) Local structure_units
        from_sifts = sifts_map.get(effective, [])
        if from_sifts:
            uniprots.extend(from_sifts)
            sources.append("structure_units")
        # 2) Supplement
        from_supplement = supplement_uniprots(effective)
        if from_supplement:
            # Avoid duplicates while keeping order.
            for acc in from_supplement:
                if acc not in uniprots:
                    uniprots.append(acc)
            sources.append("supplement")
        # 3) Known-unresolvable annotation (informational only -- never
        #    overrides a real mapping).
        reason = unresolvable_reason(effective)
        if not uniprots and reason is None and redirected_from:
            # Was redirected but the successor isn't in our catalog --
            # rare but possible. Mark category=stale_redirect.
            reason = UnresolvableReason(
                category="redirect_target_missing",
                description=f"Redirected from {redirected_from} to {effective}, "
                            f"but {effective} is not in the local catalog.",
            )
        details.append(ResolutionDetail(
            requested_pdb=pdb,
            effective_pdb=effective,
            uniprots=uniprots,
            sources=sources,
            redirected_from=redirected_from,
            unresolvable_reason=reason,
        ))
    return details


def reset_cache() -> None:
    """Drop the in-process caches so TSV edits take effect immediately."""
    load_supplement.cache_clear()
    load_obsolete_redirects.cache_clear()
    load_unresolvable_known.cache_clear()
