"""Warehouse query layer for the freestanding ProteoSphere package.

A thin pyarrow-based reader over the family partitions defined in
:mod:`proteosphere.config`. The reader is path-relative and never assumes
``D:`` or ``E:``. Callers pass a :class:`Config` instance.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyarrow.parquet as pq

from proteosphere.config import Config


def _norm_acc(s: object) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    text = str(s).strip().upper()
    if not text or text in {"NONE", "NAN", "NA"}:
        return ""
    if "-" in text:
        head, _, tail = text.partition("-")
        if not tail.isdigit():
            return head
    return text


@dataclass
class UniRefRecord:
    accession: str
    uniref100: str
    uniref90: str
    uniref50: str
    uniparc: str
    taxon: str


class Warehouse:
    """Query interface over the freestanding warehouse partitions."""

    def __init__(self, config: Config) -> None:
        self.config = config

    # ----------------------------------------------------------- protein API

    def lookup_uniref(self, accessions: Iterable[str]) -> dict[str, UniRefRecord]:
        """Resolve UniRef100/90/50 + UniParc + taxon for a set of accessions.

        Uses the hydrated similarity_signatures partition if present (faster);
        otherwise falls back to filtered read on the proteins partition.
        """
        normalized = {_norm_acc(a) for a in accessions if _norm_acc(a)}
        if not normalized:
            return {}

        sig_path = self.config.family_partition("similarity_signatures")
        if sig_path.is_file() and sig_path.stat().st_size > 1024:
            return self._lookup_uniref_from_signatures(normalized, sig_path)
        return self._lookup_uniref_from_proteins(normalized)

    def _lookup_uniref_from_signatures(
        self, accessions: set[str], path: Path
    ) -> dict[str, UniRefRecord]:
        table = pq.read_table(
            path,
            columns=[
                "accession",
                "uniref100_signature",
                "uniref90_signature",
                "uniref50_signature",
                "uniparc_signature",
                "taxon_signature",
            ],
            filters=[("accession", "in", list(accessions))],
        )
        df = table.to_pandas()
        out: dict[str, UniRefRecord] = {}
        for _, row in df.iterrows():
            acc = _norm_acc(row["accession"])
            out[acc] = UniRefRecord(
                accession=acc,
                uniref100=str(row.get("uniref100_signature") or ""),
                uniref90=str(row.get("uniref90_signature") or ""),
                uniref50=str(row.get("uniref50_signature") or ""),
                uniparc=str(row.get("uniparc_signature") or ""),
                taxon=str(row.get("taxon_signature") or ""),
            )
        return out

    def _lookup_uniref_from_proteins(self, accessions: set[str]) -> dict[str, UniRefRecord]:
        path = self.config.family_partition("proteins")
        table = pq.read_table(
            path,
            columns=[
                "accession",
                "uniref100_cluster",
                "uniref90_cluster",
                "uniref50_cluster",
                "uniparc_id",
                "taxon_id",
            ],
            filters=[("accession", "in", list(accessions))],
        )
        df = table.to_pandas()
        out: dict[str, UniRefRecord] = {}
        for _, row in df.iterrows():
            acc = _norm_acc(row["accession"])
            taxon = row.get("taxon_id")
            taxon_sig = f"taxon:{taxon}" if pd.notna(taxon) else ""
            out[acc] = UniRefRecord(
                accession=acc,
                uniref100=str(row.get("uniref100_cluster") or ""),
                uniref90=str(row.get("uniref90_cluster") or ""),
                uniref50=str(row.get("uniref50_cluster") or ""),
                uniparc=str(row.get("uniparc_id") or ""),
                taxon=taxon_sig,
            )
        return out

    # -------------------------------------------------------------- ligand API

    def lookup_ligand_chemistry(
        self, ligand_refs: Iterable[str]
    ) -> dict[str, dict[str, str]]:
        """Look up ligand chemistry signatures (canonical_smiles, scaffold) per ligand_ref."""
        refs = {str(r).strip() for r in ligand_refs if str(r).strip()}
        if not refs:
            return {}
        path = self.config.family_partition("ligand_chemistry_signatures")
        table = pq.read_table(
            path,
            columns=[
                "ligand_ref",
                "canonical_smiles",
                "canonical_smiles_hash",
                "exact_ligand_identity_group",
                "chemical_series_group",
            ],
            filters=[("ligand_ref", "in", list(refs))],
        )
        df = table.to_pandas()
        out: dict[str, dict[str, str]] = {}
        for _, row in df.iterrows():
            ref = str(row["ligand_ref"])
            out[ref] = {
                "canonical_smiles": str(row.get("canonical_smiles") or ""),
                "canonical_smiles_hash": str(row.get("canonical_smiles_hash") or ""),
                "exact_ligand_identity_group": str(row.get("exact_ligand_identity_group") or ""),
                "chemical_series_group": str(row.get("chemical_series_group") or ""),
            }
        return out

    # ---------------------------------------------------------- audit helper

    def cluster_set_at_level(
        self, accessions: Iterable[str], level: str
    ) -> tuple[set[str], int]:
        """Map accessions -> cluster IDs at a UniRef level.

        Returns (cluster_set, unresolved_count).
        """
        if level not in {"uniref100", "uniref90", "uniref50"}:
            raise ValueError(f"Unsupported UniRef level: {level}")
        records = self.lookup_uniref(accessions)
        clusters: set[str] = set()
        unresolved = 0
        for a in accessions:
            norm = _norm_acc(a)
            r = records.get(norm)
            cluster_id = getattr(r, level, "") if r else ""
            if cluster_id:
                clusters.add(cluster_id)
            else:
                unresolved += 1
        return clusters, unresolved

    # ------------------------------------------------------------ smoke tests

    def smoke_test(self) -> dict[str, object]:
        """Verify the warehouse can answer the basic queries without errors."""
        results: dict[str, object] = {
            "warehouse_root": str(self.config.warehouse_root),
            "checks": {},
        }
        # Catalog presence
        results["checks"]["catalog_present"] = self.config.catalog_path().is_file()
        # Each family partition presence
        for family in [
            "proteins",
            "pdb_entries",
            "structure_units",
            "ligand_chemistry_signatures",
            "motif_domain_site_annotations",
            "pathway_roles",
            "materialization_routes",
            "leakage_groups",
            "similarity_signatures",
        ]:
            try:
                p = self.config.family_partition(family)
                results["checks"][f"{family}_partition_present"] = p.is_file()
                if p.is_file():
                    meta = pq.read_metadata(p)
                    results["checks"][f"{family}_row_count"] = meta.num_rows
            except Exception as exc:  # pragma: no cover
                results["checks"][f"{family}_error"] = str(exc)
        # End-to-end UniRef lookup on a known accession
        test_accs = {"P00533", "P04637", "P53350"}
        records = self.lookup_uniref(test_accs)
        results["checks"]["uniref_smoke_resolved"] = len(records)
        results["checks"]["uniref_smoke_sample"] = {
            a: {
                "uniref100": records[a].uniref100,
                "uniref90": records[a].uniref90,
                "uniref50": records[a].uniref50,
            }
            for a in records
        }
        return results
