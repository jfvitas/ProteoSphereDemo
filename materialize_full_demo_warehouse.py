"""Materialize the FULL set of relationship axes into the demo warehouse.

This is run AFTER ``build_demo_warehouse.py`` has produced the baseline
``demo_warehouse/catalog/v2.duckdb`` with the basic Davis/KIBA/GtoPdb/
HIPPIE/HuRI bridges + EC/ortholog/sequence-cluster memberships.

It adds every remaining relationship axis the ProteoSphere ingest
pipeline has source data for:

  * ``v2_motif_membership``     — Pfam + InterPro + ELM + MegaMotifBase
                                  + Motivated Proteins family memberships
                                  for every UniProt in the warehouse, read
                                  from the legacy reference_library
                                  motif_domain_site_annotations table.
  * ``v2_scaffold_membership``  — Bemis-Murcko scaffolds for every ligand
                                  in davis_ligands + kiba_ligands +
                                  gtopdb_ligands, computed with RDKit.
  * ``v2_scaffold_edges_summary`` — counts of scaffold-shared ligand
                                  pairs (full pairwise edge list is
                                  JIT-derivable from membership).
  * ``v2_globin_family_members`` — external structural homologs added
                                  for the manuscript's headline globin
                                  example (P02185 sperm whale myoglobin,
                                  P69905+P68871 human hemoglobin, etc.).
  * ``v2_pdb_uniprot``          — PDB ID -> UniProt accession xref for
                                  every UniProt in the warehouse, read
                                  from the legacy cross_references table.
  * ``papers_rows``             — row-level rosters for every paper in
                                  the manuscript (~58 CSV files), with
                                  paper_id, family, dataset, split,
                                  and a normalized payload column.
  * ``papers_metadata``         — per-paper header metadata (provenance,
                                  source URLs, sha256 hashes).
  * ``pinder_plinder_audit``    — PINDER vs PLINDER cross-audit results
                                  across 10 leakage axes (direct_pdb,
                                  accession_root, uniref{50,90,100},
                                  pfam_family, interpro_family,
                                  ligand_exact_identity, ligand_chemical_series,
                                  ligand_canonical_smiles_hash).
  * ``pinder_plinder_axis_overlap`` — per-pair-per-axis overlap counts
                                  for the cross-audit, so the Library
                                  tab can plot leakage by axis.

Inputs:
  * ``demo_warehouse/catalog/v2.duckdb`` — written by build_demo_warehouse.py
  * ``D:/ProteoSphere/reference_library/catalog/reference_library.duckdb``
    (the legacy warehouse — used READ-ONLY).
  * ``D:/documents/ProteoSphereV2/docs/manuscripts/proteosphere_paper/datasets/*_rows.csv``
  * ``D:/documents/ProteoSphereV2/artifacts/status/pinder_plinder_*.json``

Usage::

    cd build/release-repos/proteosphere-model-studio
    python materialize_full_demo_warehouse.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
from pathlib import Path

import duckdb


HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"

# Read-only inputs on the maintainer machine.
LEGACY_CATALOG = Path("D:/ProteoSphere/reference_library/catalog/reference_library.duckdb")
PROTEOSPHERE_ROOT = Path("D:/documents/ProteoSphereV2")
PAPERS_ROOT = PROTEOSPHERE_ROOT / "docs" / "manuscripts" / "proteosphere_paper" / "datasets"
AUDIT_ROOT = PROTEOSPHERE_ROOT / "artifacts" / "status"

SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _open_warehouse() -> duckdb.DuckDBPyConnection:
    if not WAREHOUSE.exists():
        raise SystemExit(
            f"Demo warehouse not found at {WAREHOUSE}. "
            "Run build_demo_warehouse.py first."
        )
    return duckdb.connect(str(WAREHOUSE))


def _open_legacy() -> duckdb.DuckDBPyConnection:
    if not LEGACY_CATALOG.exists():
        raise SystemExit(
            f"Legacy reference_library not found at {LEGACY_CATALOG}. "
            "Cannot materialize motif memberships without it."
        )
    return duckdb.connect(str(LEGACY_CATALOG), read_only=True)


def _collect_uniprot_universe(con: duckdb.DuckDBPyConnection) -> set[str]:
    """Every UniProt accession that appears anywhere in the demo warehouse."""
    unis: set[str] = set()
    sources = [
        ("davis_bridge_uniprot", "uniprot"),
        ("kiba_bridge_uniprot", "uniprot"),
        ("gtopdb_bridge_uniprot", "uniprot"),
        ("hippie_bridge_uniprot", "uniprot"),
        ("huri_bridge_uniprot", "uniprot"),
        ("s_3did_bridge_uniprot", "uniprot"),
        ("v2_ec_class_membership", "uniprot"),
        ("v2_ortholog_cluster_membership", "uniprot"),
        ("v2_sequence_cluster_membership", "uniprot"),
    ]
    for tbl, col in sources:
        try:
            for (acc,) in con.execute(
                f"SELECT DISTINCT {col} FROM {tbl} WHERE {col} IS NOT NULL"
            ).fetchall():
                if acc:
                    unis.add(acc.strip())
        except Exception as exc:
            print(f"  [warn] could not read {tbl}.{col}: {exc}")

    # gtopdb_targets has direct human/rat/mouse UniProt columns.
    for col in ("human_uniprot", "rat_uniprot", "mouse_uniprot"):
        try:
            for (acc,) in con.execute(
                f"SELECT DISTINCT {col} FROM gtopdb_targets WHERE {col} IS NOT NULL"
            ).fetchall():
                if acc:
                    unis.add(acc.strip())
        except Exception:
            pass

    # External homologs we explicitly want represented so cross-species
    # globin / kinase comparisons land hits in motif_membership.
    EXTERNAL_HOMOLOGS = {
        # Globins (Pfam PF00042)
        "P02185",   # sperm whale myoglobin (1MBO)
        "P69905",   # human hemoglobin alpha (1HHO)
        "P68871",   # human hemoglobin beta (1HHO)
        "P02042",   # human hemoglobin delta
        "P02100",   # human hemoglobin epsilon
        "P69892",   # human hemoglobin gamma-2
        "P02144",   # human myoglobin (3RGK)
        # Kinases not already in Davis/KIBA
        "P00533",   # EGFR
        "P00519",   # ABL1
        "P15056",   # BRAF
        "P04637",   # TP53 (control non-kinase)
    }
    unis.update(EXTERNAL_HOMOLOGS)
    return unis


def _drop_if_exists(con: duckdb.DuckDBPyConnection, name: str) -> None:
    con.execute(f"DROP VIEW IF EXISTS {name}")
    con.execute(f"DROP TABLE IF EXISTS {name}")


# ---------------------------------------------------------------------
# Materializers
# ---------------------------------------------------------------------
def materialize_motif_membership(
    con: duckdb.DuckDBPyConnection,
    universe: set[str],
) -> dict:
    """Extract Pfam/InterPro/ELM/etc. for every UniProt in the universe.

    Strategy: ATTACH the legacy warehouse read-only, build a TEMP universe
    table, then run the DuckDB regex_extract pipeline from
    api/model_studio/v2/ingest/motif_signatures.py over the ATTACHed
    motif_domain_site_annotations.
    """
    print(f"[motif] universe={len(universe):,} UniProts")
    legacy_uri = str(LEGACY_CATALOG).replace("\\", "/")
    con.execute(f"ATTACH IF NOT EXISTS '{legacy_uri}' AS legacy (READ_ONLY)")

    con.execute("DROP TABLE IF EXISTS _v2_universe")
    con.execute("CREATE TEMP TABLE _v2_universe (uniprot VARCHAR)")
    accs = sorted(universe)
    for i in range(0, len(accs), 5_000):
        batch = accs[i : i + 5_000]
        con.executemany(
            "INSERT INTO _v2_universe VALUES (?)", [(a,) for a in batch]
        )
    con.execute("CREATE INDEX idx_uv ON _v2_universe(uniprot)")

    _drop_if_exists(con, "v2_motif_membership")
    con.execute(
        f"""
        CREATE TABLE v2_motif_membership AS
        WITH owners AS (
          SELECT
            CASE owner_record_type
              WHEN 'protein' THEN regexp_extract(owner_summary_id,
                                                '^protein:([^:]+)$', 1)
              WHEN 'structure_unit' THEN regexp_extract(owner_summary_id,
                                                       '([^:]+)$', 1)
              ELSE NULL
            END AS uniprot,
            namespace, identifier, label
          FROM legacy.motif_domain_site_annotations
          WHERE owner_record_type IN ('protein','structure_unit')
            AND identifier IS NOT NULL
        )
        SELECT DISTINCT
          o.uniprot,
          o.namespace,
          o.identifier,
          COALESCE(o.label, '') AS label,
          '{SNAPSHOT_ID}'       AS snapshot_id
        FROM owners o
        JOIN _v2_universe u ON u.uniprot = o.uniprot
        WHERE o.uniprot IS NOT NULL AND o.uniprot <> ''
        """
    )
    con.execute("DROP TABLE _v2_universe")

    n = con.execute("SELECT COUNT(*) FROM v2_motif_membership").fetchone()[0]
    by_ns = dict(
        con.execute(
            "SELECT namespace, COUNT(*) FROM v2_motif_membership GROUP BY namespace"
        ).fetchall()
    )
    print(f"[motif] rows={n:,}  by_namespace={by_ns}")

    con.execute("DETACH legacy")
    return {"rows": n, "by_namespace": by_ns}


def materialize_pdb_uniprot(
    con: duckdb.DuckDBPyConnection,
    universe: set[str],
) -> dict:
    """PDB ID <-> UniProt for every UniProt in the universe.

    Lets the Library tab answer "what PDB structures cover P02185?"
    and lets joins on PDB ID work end-to-end.
    """
    print(f"[pdb_uniprot] universe={len(universe):,} UniProts")
    legacy_uri = str(LEGACY_CATALOG).replace("\\", "/")
    con.execute(f"ATTACH IF NOT EXISTS '{legacy_uri}' AS legacy (READ_ONLY)")

    con.execute("DROP TABLE IF EXISTS _v2_universe")
    con.execute("CREATE TEMP TABLE _v2_universe (uniprot VARCHAR)")
    accs = sorted(universe)
    for i in range(0, len(accs), 5_000):
        batch = accs[i : i + 5_000]
        con.executemany(
            "INSERT INTO _v2_universe VALUES (?)", [(a,) for a in batch]
        )
    con.execute("CREATE INDEX idx_uv2 ON _v2_universe(uniprot)")

    _drop_if_exists(con, "v2_pdb_uniprot")
    con.execute(
        f"""
        CREATE TABLE v2_pdb_uniprot AS
        SELECT
          c.accession    AS uniprot,
          c.external_id  AS pdb_id,
          c.snapshot_id  AS source_snapshot_id,
          '{SNAPSHOT_ID}' AS snapshot_id
        FROM legacy.cross_references c
        JOIN _v2_universe u ON u.uniprot = c.accession
        WHERE c.database = 'PDB'
        """
    )
    con.execute("DROP TABLE _v2_universe")

    n = con.execute("SELECT COUNT(*) FROM v2_pdb_uniprot").fetchone()[0]
    distinct_pdb = con.execute(
        "SELECT COUNT(DISTINCT pdb_id) FROM v2_pdb_uniprot"
    ).fetchone()[0]
    distinct_uniprot = con.execute(
        "SELECT COUNT(DISTINCT uniprot) FROM v2_pdb_uniprot"
    ).fetchone()[0]
    print(
        f"[pdb_uniprot] rows={n:,}  distinct_pdb={distinct_pdb:,}  "
        f"distinct_uniprot={distinct_uniprot:,}"
    )

    con.execute("DETACH legacy")
    return {"rows": n, "distinct_pdb": distinct_pdb, "distinct_uniprot": distinct_uniprot}


def materialize_scaffold_membership(
    con: duckdb.DuckDBPyConnection,
) -> dict:
    """Bemis-Murcko scaffolds for every ligand in davis/kiba/gtopdb."""
    try:
        from rdkit import Chem, RDLogger
        from rdkit.Chem.Scaffolds import MurckoScaffold
        RDLogger.DisableLog("rdApp.*")
    except ImportError:
        print("[scaffold] RDKit not available — skipping")
        return {"error": "rdkit_unavailable"}

    # Pull ligand_ref + smiles from every benchmark ligand table.
    rows: list[tuple[str, str, str]] = []  # (ligand_ref, source, smiles)
    for src, tbl, ref_col, smi_col in [
        ("davis", "davis_ligands", "ligand_ref", "smiles"),
        ("kiba", "kiba_ligands", "ligand_ref", "smiles"),
        ("gtopdb", "gtopdb_ligands", "ligand_ref", "smiles"),
    ]:
        try:
            for ref, smi in con.execute(
                f"SELECT {ref_col}, {smi_col} FROM {tbl} "
                f"WHERE {smi_col} IS NOT NULL AND {smi_col} <> ''"
            ).fetchall():
                rows.append((ref, src, smi))
        except Exception as exc:
            print(f"  [warn] {tbl}: {exc}")
    print(f"[scaffold] input ligands: {len(rows):,}")

    scaffolds = []
    scaffold_counts: dict[str, int] = {}
    failures = 0
    for ref, src, smi in rows:
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            failures += 1
            continue
        try:
            scaffold = MurckoScaffold.GetScaffoldForMol(mol)
            ss = Chem.MolToSmiles(scaffold, canonical=True) if scaffold else ""
        except Exception:
            failures += 1
            continue
        if not ss:
            ss = "acyclic"
        sid = hashlib.sha1(ss.encode("utf-8")).hexdigest()[:16]
        scaffolds.append(
            {
                "ligand_ref": ref,
                "source": src,
                "canonical_smiles": smi,
                "scaffold_smiles": ss,
                "scaffold_id": sid,
                "snapshot_id": SNAPSHOT_ID,
            }
        )
        scaffold_counts[sid] = scaffold_counts.get(sid, 0) + 1

    _drop_if_exists(con, "v2_scaffold_membership")
    con.execute(
        """
        CREATE TABLE v2_scaffold_membership (
            ligand_ref       VARCHAR,
            source           VARCHAR,
            canonical_smiles VARCHAR,
            scaffold_smiles  VARCHAR,
            scaffold_id      VARCHAR,
            snapshot_id      VARCHAR
        )
        """
    )
    if scaffolds:
        # Insert in chunks.
        for i in range(0, len(scaffolds), 1000):
            batch = scaffolds[i : i + 1000]
            con.executemany(
                "INSERT INTO v2_scaffold_membership VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["ligand_ref"],
                        r["source"],
                        r["canonical_smiles"],
                        r["scaffold_smiles"],
                        r["scaffold_id"],
                        r["snapshot_id"],
                    )
                    for r in batch
                ],
            )

    # Summary table: per-scaffold counts.
    _drop_if_exists(con, "v2_scaffold_edges_summary")
    con.execute(
        """
        CREATE TABLE v2_scaffold_edges_summary AS
        SELECT
            scaffold_id,
            ANY_VALUE(scaffold_smiles) AS scaffold_smiles,
            COUNT(*)                    AS n_ligands,
            COUNT(DISTINCT source)      AS n_sources,
            STRING_AGG(DISTINCT source, ',') AS sources
        FROM v2_scaffold_membership
        WHERE scaffold_smiles <> 'acyclic'
        GROUP BY scaffold_id
        HAVING COUNT(*) >= 2
        ORDER BY n_ligands DESC
        """
    )

    n = con.execute("SELECT COUNT(*) FROM v2_scaffold_membership").fetchone()[0]
    n_unique = len(scaffold_counts)
    n_shared = sum(1 for c in scaffold_counts.values() if c >= 2)
    print(
        f"[scaffold] rows={n:,}  unique_scaffolds={n_unique:,}  "
        f"shared_by_2+={n_shared:,}  parse_failures={failures}"
    )
    return {
        "rows": n,
        "unique_scaffolds": n_unique,
        "shared_scaffolds": n_shared,
        "parse_failures": failures,
    }


def materialize_globin_family_members(con: duckdb.DuckDBPyConnection) -> dict:
    """Explicit reference table linking globin-family external homologs.

    Even when an external UniProt (e.g. P02185 sperm whale myoglobin)
    isn't in any DTI/PPI benchmark, we keep an explicit globin reference
    so the manuscript's headline 1MBO <-> 1HHO example is reachable from
    the warehouse via shared Pfam PF00042 (Globin) annotations.
    """
    # Authoritative globin family roster (Pfam PF00042, InterPro IPR000971
    # "Globin"). UniProt accessions + canonical PDB IDs.
    members = [
        # uniprot, common_name, organism, canonical_pdb, family_class
        ("P02185", "Myoglobin",                "Physeter catodon (sperm whale)", "1MBO", "myoglobin"),
        ("P02144", "Myoglobin",                "Homo sapiens",                   "3RGK", "myoglobin"),
        ("P02192", "Myoglobin",                "Equus caballus (horse)",         "1YMB", "myoglobin"),
        ("P69905", "Hemoglobin subunit alpha", "Homo sapiens",                   "1HHO", "hemoglobin"),
        ("P68871", "Hemoglobin subunit beta",  "Homo sapiens",                   "1HHO", "hemoglobin"),
        ("P02042", "Hemoglobin subunit delta", "Homo sapiens",                   "2HHD", "hemoglobin"),
        ("P02100", "Hemoglobin subunit epsilon", "Homo sapiens",                 "1A9W", "hemoglobin"),
        ("P69892", "Hemoglobin subunit gamma-2", "Homo sapiens",                 "1FDH", "hemoglobin"),
        ("P02008", "Hemoglobin subunit alpha (chicken)", "Gallus gallus",        "1HBR", "hemoglobin"),
        ("P02091", "Hemoglobin subunit beta (mouse)",    "Mus musculus",         "3HRW", "hemoglobin"),
        ("P15445", "Hemoglobin subunit alpha-T",         "Petromyzon marinus",   "2LHB", "hemoglobin"),
        ("P02216", "Leghemoglobin",                       "Glycine max",          "1LH1", "leghemoglobin"),
        ("P02226", "Hemoglobin (lupin)",                  "Lupinus luteus",       "2LH7", "leghemoglobin"),
        ("P02240", "Hemoglobin I",                        "Lucina pectinata",     "1FLP", "hemoglobin"),
        ("P02246", "Hemoglobin",                          "Caenorhabditis elegans","2BK9","hemoglobin"),
        ("P15160", "Neuroglobin",                         "Homo sapiens",         "1OJ6", "neuroglobin"),
        ("Q9NPG2", "Neuroglobin",                         "Homo sapiens",         "1OJ6", "neuroglobin"),
        ("Q9BWE0", "Cytoglobin",                          "Homo sapiens",         "1V5H", "cytoglobin"),
        ("P02208", "Hemoglobin (lamprey)",                "Petromyzon marinus",   "2LHB", "hemoglobin"),
    ]

    _drop_if_exists(con, "v2_globin_family_members")
    con.execute(
        """
        CREATE TABLE v2_globin_family_members (
            uniprot        VARCHAR,
            common_name    VARCHAR,
            organism       VARCHAR,
            canonical_pdb  VARCHAR,
            family_class   VARCHAR,
            pfam_family    VARCHAR,
            interpro_family VARCHAR,
            snapshot_id    VARCHAR
        )
        """
    )
    for acc, name, org, pdb, cls in members:
        con.execute(
            "INSERT INTO v2_globin_family_members VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (acc, name, org, pdb, cls, "PF00042", "IPR000971", SNAPSHOT_ID),
        )
    n = con.execute("SELECT COUNT(*) FROM v2_globin_family_members").fetchone()[0]
    print(f"[globin_family] rows={n}")
    return {"rows": n}


def materialize_papers_rows(con: duckdb.DuckDBPyConnection) -> dict:
    """Load every <paper>_rows.csv into a single normalized table."""
    csv_files = sorted(PAPERS_ROOT.glob("*_rows.csv"))
    if not csv_files:
        print(f"[papers] no _rows.csv files under {PAPERS_ROOT}")
        return {"rows": 0}
    print(f"[papers] found {len(csv_files)} CSVs")

    _drop_if_exists(con, "papers_rows")
    _drop_if_exists(con, "papers_metadata")
    con.execute(
        """
        CREATE TABLE papers_rows (
            paper_id      VARCHAR,
            family        VARCHAR,
            row_index     BIGINT,
            dataset       VARCHAR,
            split         VARCHAR,
            drug_id       VARCHAR,
            drug_smiles   VARCHAR,
            target_id     VARCHAR,
            target_seq_sha8 VARCHAR,
            affinity_value DOUBLE,
            pdb_id        VARCHAR,
            protein_id    VARCHAR,
            pair_partner_id VARCHAR,
            payload_json  VARCHAR,
            snapshot_id   VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE papers_metadata (
            paper_id    VARCHAR,
            family      VARCHAR,
            n_rows      BIGINT,
            csv_path    VARCHAR,
            generated_at VARCHAR,
            source_lines VARCHAR,  -- newline-joined source URLs / proofs
            snapshot_id  VARCHAR
        )
        """
    )

    total_rows = 0
    paper_count = 0
    insert_buffer: list[tuple] = []

    def _flush():
        if not insert_buffer:
            return
        con.executemany(
            "INSERT INTO papers_rows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            insert_buffer,
        )
        insert_buffer.clear()

    def _coerce_float(v):
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _coerce_int(v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    for csv_path in csv_files:
        paper_id = csv_path.stem.replace("_rows", "")
        # Parse comment-header for metadata, then DictReader the rest.
        comment_lines: list[str] = []
        family = None
        generated_at = None
        with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
            while True:
                pos = f.tell()
                ln = f.readline()
                if not ln:
                    break
                if ln.startswith("#"):
                    stripped = ln.strip()
                    comment_lines.append(stripped)
                    if stripped.startswith("# paper_id:"):
                        paper_id = stripped.split(":", 1)[1].strip()
                    elif stripped.startswith("# family:"):
                        family = stripped.split(":", 1)[1].strip()
                    elif stripped.startswith("# generated_at:"):
                        generated_at = stripped.split(":", 1)[1].strip()
                else:
                    f.seek(pos)
                    break
            try:
                reader = csv.DictReader(f)
                local_rows = 0
                for idx, row in enumerate(reader):
                    insert_buffer.append(
                        (
                            paper_id,
                            family or "",
                            idx,
                            row.get("dataset") or "",
                            row.get("split") or "",
                            row.get("drug_id") or "",
                            row.get("drug_smiles") or "",
                            row.get("target_id") or "",
                            row.get("target_sequence_sha8") or "",
                            _coerce_float(row.get("affinity_value")),
                            row.get("pdb_id") or "",
                            row.get("protein_id") or "",
                            row.get("pair_partner_id") or "",
                            json.dumps(row, ensure_ascii=False),
                            SNAPSHOT_ID,
                        )
                    )
                    local_rows += 1
                    if len(insert_buffer) >= 5000:
                        _flush()
            except Exception as exc:
                print(f"  [warn] {csv_path.name}: {exc}")
                local_rows = 0
        con.execute(
            "INSERT INTO papers_metadata VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                paper_id,
                family or "",
                local_rows,
                str(csv_path.relative_to(PROTEOSPHERE_ROOT)).replace("\\", "/"),
                generated_at or "",
                "\n".join(comment_lines),
                SNAPSHOT_ID,
            ),
        )
        total_rows += local_rows
        paper_count += 1

    _flush()
    n = con.execute("SELECT COUNT(*) FROM papers_rows").fetchone()[0]
    print(f"[papers] papers={paper_count}  total_rows={n:,}")
    return {"papers": paper_count, "rows": n}


def materialize_pinder_plinder_audit(con: duckdb.DuckDBPyConnection) -> dict:
    """Load PINDER/PLINDER expanded cross-audit summary as a queryable table."""
    summary = AUDIT_ROOT / "pinder_plinder_cross_audit_expanded_summary.json"
    if not summary.exists():
        print(f"[pinder_plinder] no summary at {summary}")
        return {"rows": 0}
    data = json.loads(summary.read_text(encoding="utf-8"))
    audits = data.get("audits") or {}

    _drop_if_exists(con, "pinder_plinder_audit")
    _drop_if_exists(con, "pinder_plinder_axis_overlap")
    con.execute(
        """
        CREATE TABLE pinder_plinder_audit (
            comparison      VARCHAR PRIMARY KEY,
            verdict         VARCHAR,
            composite       DOUBLE,
            axes_attempted  VARCHAR,
            axes_unavailable VARCHAR,
            snapshot_id     VARCHAR
        )
        """
    )
    con.execute(
        """
        CREATE TABLE pinder_plinder_axis_overlap (
            comparison       VARCHAR,
            axis             VARCHAR,
            overlap_count    BIGINT,
            overlap_fraction DOUBLE,
            snapshot_id      VARCHAR
        )
        """
    )

    n_aud = 0
    n_ovl = 0
    axes_attempted = ",".join(data.get("axes_attempted", []))
    for cmp_name, body in audits.items():
        con.execute(
            "INSERT INTO pinder_plinder_audit VALUES (?, ?, ?, ?, ?, ?)",
            (
                cmp_name,
                body.get("verdict") or "",
                float(body.get("composite") or 0.0),
                axes_attempted,
                ",".join(body.get("axes_unavailable", [])),
                SNAPSHOT_ID,
            ),
        )
        n_aud += 1
        for axis, axis_body in (body.get("axes") or {}).items():
            con.execute(
                "INSERT INTO pinder_plinder_axis_overlap VALUES (?, ?, ?, ?, ?)",
                (
                    cmp_name,
                    axis,
                    int(axis_body.get("overlap_count") or 0),
                    float(axis_body.get("overlap_fraction") or 0.0),
                    SNAPSHOT_ID,
                ),
            )
            n_ovl += 1
    print(f"[pinder_plinder] audits={n_aud}  axis_overlap_rows={n_ovl}")
    return {"audits": n_aud, "axis_overlap_rows": n_ovl}


# ---------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------
def main() -> int:
    if not WAREHOUSE.exists():
        raise SystemExit(f"missing warehouse: {WAREHOUSE}")
    print(f"Warehouse: {WAREHOUSE}")
    print(f"Legacy:    {LEGACY_CATALOG}")
    print(f"Snapshot:  {SNAPSHOT_ID}")
    print()

    con = _open_warehouse()
    try:
        universe = _collect_uniprot_universe(con)
        print(f"UniProt universe: {len(universe):,}")
        stats: dict[str, dict] = {"snapshot_id": SNAPSHOT_ID,
                                  "universe_size": len(universe)}

        print()
        stats["motif"] = materialize_motif_membership(con, universe)
        print()
        stats["pdb_uniprot"] = materialize_pdb_uniprot(con, universe)
        print()
        stats["scaffold"] = materialize_scaffold_membership(con)
        print()
        stats["globin_family"] = materialize_globin_family_members(con)
        print()
        stats["papers"] = materialize_papers_rows(con)
        print()
        stats["pinder_plinder"] = materialize_pinder_plinder_audit(con)
        print()

        # Stamp run in ingest_runs (run_id, source_id, snapshot_id,
        # registered_at, row_counts, output_files, sha256, license).
        con.execute(
            """
            INSERT INTO ingest_runs
              (run_id, source_id, snapshot_id, registered_at,
               row_counts, output_files, sha256, license)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO NOTHING
            """,
            (
                f"materialize_full_{SNAPSHOT_ID}",
                "demo_warehouse_materialize_full",
                SNAPSHOT_ID,
                time.time(),
                json.dumps(stats),
                "v2_motif_membership,v2_pdb_uniprot,v2_scaffold_membership,"
                "v2_scaffold_edges_summary,v2_globin_family_members,"
                "papers_rows,papers_metadata,pinder_plinder_audit,"
                "pinder_plinder_axis_overlap",
                None,
                "CC-BY-4.0/CC0-mixed",
            ),
        )
    finally:
        con.close()

    # Write a manifest next to the warehouse for provenance.
    manifest_path = HERE / "demo_warehouse" / "materialize_manifest.json"
    manifest_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    sz = WAREHOUSE.stat().st_size / 1e6
    print(f"Done. v2.duckdb is now {sz:.1f} MB")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
