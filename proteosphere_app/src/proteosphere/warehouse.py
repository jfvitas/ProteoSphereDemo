from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .io import read_json
from .model import clean_text

try:  # DuckDB is optional for metadata-only public bundles.
    import duckdb
except Exception:  # pragma: no cover - exercised on hosts without DuckDB.
    duckdb = None


LOCAL_PATH_MARKERS = (
    "D:/",
    "D:\\",
    "D:\\\\",
    "E:/",
    "E:\\",
    "E:\\\\",
    "C:/",
    "C:\\",
    "C:\\\\",
    "bio-agent-lab",
    "CSTEMP",
)


@dataclass
class ProteinResolution:
    accession: str
    protein_ref: str = ""
    uniref100: str = ""
    uniref90: str = ""
    uniref50: str = ""
    resolved: bool = False


@dataclass
class LigandResolution:
    query: str
    ligand_ref: str = ""
    ligand_id: str = ""
    exact_identity_group: str = ""
    chemical_series_group: str = ""
    canonical_smiles_hash: str = ""
    resolved: bool = False


@dataclass
class StructureChainResolution:
    pdb_id: str
    chain_id: str
    protein_ref: str = ""
    accession: str = ""
    uniref100: str = ""
    uniref90: str = ""
    uniref50: str = ""
    resolved: bool = False


@dataclass
class Warehouse:
    root: Path
    catalog_path: Path | None
    manifest: dict[str, Any] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    catalog_available: bool = False
    duckdb_available: bool = False
    table_names: set[str] = field(default_factory=set)

    @classmethod
    def open(cls, root: str | Path) -> "Warehouse":
        base = Path(root).resolve()
        manifest_path = base / "warehouse_manifest.json"
        summary_path = base / "warehouse_summary.json"
        catalog_path = base / "catalog" / "reference_library.duckdb"
        manifest = read_json(manifest_path) if manifest_path.exists() else {}
        summary = read_json(summary_path) if summary_path.exists() else {}
        duckdb_available = duckdb is not None
        table_names: set[str] = set()
        catalog_available = False
        if duckdb_available and catalog_path.exists():
            try:
                with duckdb.connect(str(catalog_path), read_only=True) as con:
                    table_names = {
                        str(row[0])
                        for row in con.execute(
                            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()"
                        ).fetchall()
                    }
                catalog_available = True
            except Exception:
                catalog_available = False
        return cls(
            root=base,
            catalog_path=catalog_path if catalog_path.exists() else None,
            manifest=manifest,
            summary=summary,
            catalog_available=catalog_available,
            duckdb_available=duckdb_available,
            table_names=table_names,
        )

    def connect(self):
        if duckdb is None:
            raise RuntimeError("DuckDB is not installed.")
        if self.catalog_path is None:
            raise FileNotFoundError("reference_library.duckdb was not found.")
        return duckdb.connect(str(self.catalog_path), read_only=True)

    def resolve_proteins(self, accessions: list[str]) -> dict[str, ProteinResolution]:
        unique = sorted({clean_text(item) for item in accessions if clean_text(item)})
        resolved = {item: ProteinResolution(accession=item) for item in unique}
        if not unique or not self.catalog_available or "proteins" not in self.table_names:
            return resolved
        prefix1 = sorted({item[:1] for item in unique if item})
        prefix2 = sorted({item[:2] for item in unique if len(item) >= 2})
        if not prefix1 or not prefix2:
            return resolved
        placeholders = ",".join("?" for _ in unique)
        p1_placeholders = ",".join("?" for _ in prefix1)
        p2_placeholders = ",".join("?" for _ in prefix2)
        with self.connect() as con:
            rows = con.execute(
                f"""
                SELECT accession, protein_ref, uniref100_cluster, uniref90_cluster, uniref50_cluster
                FROM proteins
                WHERE accession_prefix1 IN ({p1_placeholders})
                  AND accession_prefix2 IN ({p2_placeholders})
                  AND accession IN ({placeholders})
                """,
                (*prefix1, *prefix2, *unique),
            ).fetchall()
        for accession, protein_ref, uniref100, uniref90, uniref50 in rows:
            resolved[str(accession)] = ProteinResolution(
                accession=str(accession),
                protein_ref=clean_text(protein_ref),
                uniref100=clean_text(uniref100),
                uniref90=clean_text(uniref90),
                uniref50=clean_text(uniref50),
                resolved=True,
            )
        return resolved

    def resolve_ligands(self, ligand_ids: list[str]) -> dict[str, LigandResolution]:
        unique = sorted({clean_text(item) for item in ligand_ids if clean_text(item)})
        resolved = {item: LigandResolution(query=item) for item in unique}
        if not unique or not self.catalog_available:
            return resolved
        with self.connect() as con:
            if "ligands" in self.table_names:
                placeholders = ",".join("?" for _ in unique)
                params = (*unique, *unique, *[item.lower() for item in unique], *[item.lower() for item in unique])
                rows = con.execute(
                    f"""
                    SELECT ligand_id, ligand_ref
                    FROM ligands
                    WHERE ligand_id IN ({placeholders})
                       OR ligand_ref IN ({placeholders})
                       OR lower(ligand_id) IN ({placeholders})
                       OR lower(ligand_ref) IN ({placeholders})
                    """,
                    params,
                ).fetchall()
                for ligand_id, ligand_ref in rows:
                    for query in unique:
                        if query in {str(ligand_id), str(ligand_ref)} or query.lower() in {
                            str(ligand_id).lower(),
                            str(ligand_ref).lower(),
                        }:
                            resolved[query] = LigandResolution(
                                query=query,
                                ligand_ref=clean_text(ligand_ref),
                                ligand_id=clean_text(ligand_id),
                                resolved=True,
                            )
            if "ligand_chemistry_signatures" in self.table_names:
                placeholders = ",".join("?" for _ in unique)
                rows = con.execute(
                    f"""
                    SELECT ligand_ref, ligand_label, exact_ligand_identity_group,
                           chemical_series_group, canonical_smiles_hash
                    FROM ligand_chemistry_signatures
                    WHERE ligand_ref IN ({placeholders})
                       OR ligand_label IN ({placeholders})
                       OR lower(ligand_ref) IN ({placeholders})
                       OR lower(ligand_label) IN ({placeholders})
                    """,
                    (*unique, *unique, *[item.lower() for item in unique], *[item.lower() for item in unique]),
                ).fetchall()
                for ligand_ref, ligand_label, exact_group, series_group, smiles_hash in rows:
                    for query in unique:
                        if query in {str(ligand_ref), str(ligand_label)} or query.lower() in {
                            str(ligand_ref).lower(),
                            str(ligand_label).lower(),
                        }:
                            current = resolved[query]
                            resolved[query] = LigandResolution(
                                query=query,
                                ligand_ref=current.ligand_ref or clean_text(ligand_ref),
                                ligand_id=current.ligand_id,
                                exact_identity_group=clean_text(exact_group),
                                chemical_series_group=clean_text(series_group),
                                canonical_smiles_hash=clean_text(smiles_hash),
                                resolved=True,
                            )
        return resolved

    def resolve_structure_chains(
        self, requests: list[tuple[str, str]]
    ) -> dict[tuple[str, str], StructureChainResolution]:
        unique = sorted({(clean_text(pdb).upper(), clean_text(chain).upper()) for pdb, chain in requests if clean_text(pdb) and clean_text(chain)})
        resolved = {
            item: StructureChainResolution(pdb_id=item[0], chain_id=item[1]) for item in unique
        }
        if not unique or not self.catalog_available or "structure_units" not in self.table_names:
            return resolved
        pdb_values = sorted({pdb for pdb, _ in unique})
        chain_values = sorted({chain for _, chain in unique})
        pdb_placeholders = ",".join("?" for _ in pdb_values)
        chain_placeholders = ",".join("?" for _ in chain_values)
        with self.connect() as con:
            rows = con.execute(
                f"""
                SELECT upper(su.structure_id), upper(su.chain_id), su.protein_ref,
                       p.accession, p.uniref100_cluster, p.uniref90_cluster, p.uniref50_cluster
                FROM structure_units su
                LEFT JOIN proteins p ON su.protein_ref = p.protein_ref
                WHERE upper(su.structure_id) IN ({pdb_placeholders})
                  AND upper(su.chain_id) IN ({chain_placeholders})
                """,
                (*pdb_values, *chain_values),
            ).fetchall()
        for pdb_id, chain_id, protein_ref, accession, uniref100, uniref90, uniref50 in rows:
            key = (str(pdb_id), str(chain_id))
            if key in resolved:
                resolved[key] = StructureChainResolution(
                    pdb_id=key[0],
                    chain_id=key[1],
                    protein_ref=clean_text(protein_ref),
                    accession=clean_text(accession),
                    uniref100=clean_text(uniref100),
                    uniref90=clean_text(uniref90),
                    uniref50=clean_text(uniref50),
                    resolved=True,
                )
        return resolved

    def table_counts(self) -> dict[str, int | str]:
        counts: dict[str, int | str] = {}
        if not self.catalog_available:
            return counts
        with self.connect() as con:
            for table_name in sorted(self.table_names):
                if table_name.startswith("duckdb_") or table_name.startswith("sqlite_"):
                    continue
                try:
                    counts[table_name] = int(con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
                except Exception as exc:
                    counts[table_name] = f"error: {exc}"
        return counts

    def catalog_path_findings(self) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if not self.catalog_available:
            return findings
        checks = {
            "structure_units": ["structure_file_path"],
            "protein_protein_edges": ["reference_file"],
            "protein_ligand_edges": ["reference_file"],
            "ligand_chemistry_signatures": ["source_artifact"],
            "similarity_signatures": ["source_artifact"],
            "warehouse_sources": ["canonical_root", "consolidation_target", "source_locator"],
        }
        with self.connect() as con:
            for table_name, columns in checks.items():
                if table_name not in self.table_names:
                    continue
                available_columns = {
                    str(row[0])
                    for row in con.execute(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                        [table_name],
                    ).fetchall()
                }
                for column in columns:
                    if column not in available_columns:
                        continue
                    predicate = " OR ".join(f"{column} LIKE ?" for _ in LOCAL_PATH_MARKERS)
                    params = [f"{marker}%" if marker.endswith(("/", "\\")) else f"%{marker}%" for marker in LOCAL_PATH_MARKERS]
                    count = int(
                        con.execute(
                            f"SELECT COUNT(*) FROM {table_name} WHERE {column} IS NOT NULL AND ({predicate})",
                            params,
                        ).fetchone()[0]
                    )
                    if count:
                        sample = con.execute(
                            f"SELECT {column} FROM {table_name} WHERE {column} IS NOT NULL AND ({predicate}) LIMIT 3",
                            params,
                        ).fetchall()
                        findings.append(
                            {
                                "table": table_name,
                                "column": column,
                                "count": count,
                                "sample": [row[0] for row in sample],
                            }
                        )
        return findings
