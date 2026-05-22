"""Auxiliary annotation axes: Pfam clans, M-CSA catalytic residues, AlphaFold models.

Sources:
  * Pfam clans:    ftp.ebi.ac.uk/pub/databases/Pfam/current_release/Pfam-A.clans.tsv.gz
                   License: CC0
  * M-CSA:         ebi.ac.uk/thornton-srv/m-csa/api/entries/?format=json
                   License: CC0
  * AlphaFold:     alphafold.ebi.ac.uk URL templates (deterministic per UniProt)
                   License: CC-BY 4.0

Creates:
  v2_pfam_clan_membership(pfam_id, clan_id, clan_name, pfam_name)
  v2_mcsa_catalytic_sites(uniprot, position, residue, role, mcsa_id, ec, pdb_template)
  v2_alphafold_models(uniprot, model_id, version, pdb_url, cif_url, pae_url)

Idempotent.
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = Path("D:/documents/ProteoSphereV2/cache")
PFAM_CLANS = CACHE / "pfam" / "Pfam-A.clans.tsv.gz"
MCSA_JSON = CACHE / "mcsa" / "mcsa_entries.json"
SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def ingest_pfam_clans(con: duckdb.DuckDBPyConnection) -> None:
    if not PFAM_CLANS.exists():
        print(f"[pfam-clans] missing {PFAM_CLANS} -- skipping")
        return
    print(f"[pfam-clans] reading {PFAM_CLANS}")

    con.execute("DROP TABLE IF EXISTS v2_pfam_clan_membership")
    con.execute("""
        CREATE TABLE v2_pfam_clan_membership (
            pfam_id     VARCHAR,
            clan_id     VARCHAR,
            clan_name   VARCHAR,
            pfam_name   VARCHAR,
            pfam_description VARCHAR,
            snapshot_id VARCHAR
        )
    """)

    rows = []
    with gzip.open(PFAM_CLANS, "rt", encoding="utf-8", errors="replace") as fh:
        # Pfam-A.clans.tsv columns:
        # pfam_id  clan_id  clan_name  pfam_short_name  pfam_description
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            pf_id, clan_id, clan_name, pfam_name, descr = parts[0], parts[1], parts[2], parts[3], parts[4]
            rows.append((pf_id, clan_id, clan_name, pfam_name, descr, SNAPSHOT_ID))
    # Bulk insert via DataFrame
    import pandas as pd
    df = pd.DataFrame(rows, columns=["pfam_id", "clan_id", "clan_name", "pfam_name", "pfam_description", "snapshot_id"])
    con.register("_pfam_tmp", df)
    con.execute("INSERT INTO v2_pfam_clan_membership SELECT * FROM _pfam_tmp")
    con.unregister("_pfam_tmp")
    with_clans = con.execute(
        "SELECT COUNT(*) FROM v2_pfam_clan_membership WHERE clan_id IS NOT NULL AND clan_id != ''"
    ).fetchone()[0]
    distinct_clans = con.execute(
        "SELECT COUNT(DISTINCT clan_id) FROM v2_pfam_clan_membership WHERE clan_id IS NOT NULL AND clan_id != ''"
    ).fetchone()[0]
    print(f"[pfam-clans] {len(rows):,} Pfam rows; {with_clans:,} have clans; {distinct_clans:,} distinct clans")


def ingest_mcsa(con: duckdb.DuckDBPyConnection) -> None:
    if not MCSA_JSON.exists():
        print(f"[mcsa] missing {MCSA_JSON} -- skipping")
        return
    print(f"[mcsa] reading {MCSA_JSON}")

    con.execute("DROP TABLE IF EXISTS v2_mcsa_catalytic_sites")
    con.execute("""
        CREATE TABLE v2_mcsa_catalytic_sites (
            uniprot       VARCHAR,
            position      INTEGER,
            residue       VARCHAR,
            role          VARCHAR,
            mcsa_id       VARCHAR,
            ec            VARCHAR,
            pdb_template  VARCHAR,
            snapshot_id   VARCHAR
        )
    """)

    with open(MCSA_JSON, "r", encoding="utf-8", errors="replace") as fh:
        data = json.load(fh)

    rows = []
    entries = data if isinstance(data, list) else data.get("results", data.get("entries", []))
    if not isinstance(entries, list):
        print(f"[mcsa] unexpected JSON structure")
        return
    print(f"[mcsa] {len(entries):,} entries in JSON")
    for entry in entries:
        mcsa_id = str(entry.get("mcsa_id", ""))
        ec = ""
        ecs = entry.get("all_ecs", [])
        if ecs and isinstance(ecs, list):
            if isinstance(ecs[0], dict):
                ec = ecs[0].get("ec_number", ecs[0].get("code", "")) or ""
            else:
                ec = str(ecs[0])
        ref_uni = entry.get("reference_uniprot_id") or ""
        for r in entry.get("residues", []):
            roles_summary = r.get("roles_summary", "")
            roles_list = r.get("roles", []) or []
            roles_str = roles_summary or ", ".join(
                rs.get("role_type", rs.get("role", "")) for rs in roles_list if isinstance(rs, dict)
            )
            pdb_template = ""
            for rc in r.get("residue_chains", []):
                if rc.get("is_reference"):
                    pdb_template = rc.get("pdb_id", "")
                    break
            # residue_sequences gives uniprot + position
            for rs in r.get("residue_sequences", []):
                uni = (rs.get("uniprot_id") or ref_uni or "").upper().split("-")[0]
                try:
                    pos = int(rs.get("resid") or 0)
                except (ValueError, TypeError):
                    pos = 0
                code = rs.get("code", "")
                if uni and pos:
                    rows.append((uni, pos, code, roles_str, mcsa_id, ec, pdb_template, SNAPSHOT_ID))

    if rows:
        con.executemany(
            "INSERT INTO v2_mcsa_catalytic_sites VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
    print(f"[mcsa] {len(rows):,} catalytic residue rows / "
          f"{con.execute('SELECT COUNT(DISTINCT uniprot) FROM v2_mcsa_catalytic_sites').fetchone()[0]:,} distinct UniProts")


def ingest_alphafold(con: duckdb.DuckDBPyConnection) -> None:
    """Materialize per-Swiss-Prot AlphaFold URLs (deterministic template, no per-entry fetch)."""
    # AF DB v4 URL template: https://alphafold.ebi.ac.uk/files/AF-{acc}-F1-model_v4.pdb
    con.execute("DROP TABLE IF EXISTS v2_alphafold_models")
    con.execute("""
        CREATE TABLE v2_alphafold_models (
            uniprot     VARCHAR,
            model_id    VARCHAR,
            version     VARCHAR,
            pdb_url     VARCHAR,
            cif_url     VARCHAR,
            pae_url     VARCHAR,
            snapshot_id VARCHAR
        )
    """)
    # Source UniProts: every Swiss-Prot entry
    has_entry = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='v2_protein_entry'"
    ).fetchone()[0]
    if has_entry == 0:
        print("[alphafold] v2_protein_entry not present; deriving UniProt list from v2_motif_membership")
        uniprots = [r[0] for r in con.execute(
            "SELECT DISTINCT uniprot FROM v2_motif_membership"
        ).fetchall()]
    else:
        uniprots = [r[0] for r in con.execute(
            "SELECT DISTINCT uniprot FROM v2_protein_entry"
        ).fetchall()]
    print(f"[alphafold] generating URL stubs for {len(uniprots):,} UniProts")
    rows = []
    for u in uniprots:
        if not u:
            continue
        rows.append((
            u,
            f"AF-{u}-F1",
            "v4",
            f"https://alphafold.ebi.ac.uk/files/AF-{u}-F1-model_v4.pdb",
            f"https://alphafold.ebi.ac.uk/files/AF-{u}-F1-model_v4.cif",
            f"https://alphafold.ebi.ac.uk/files/AF-{u}-F1-predicted_aligned_error_v4.json",
            SNAPSHOT_ID,
        ))
    # Chunked insert
    import pandas as pd
    for i in range(0, len(rows), 50000):
        df = pd.DataFrame(rows[i:i+50000],
                          columns=["uniprot", "model_id", "version",
                                   "pdb_url", "cif_url", "pae_url", "snapshot_id"])
        con.register("_af_tmp", df)
        con.execute("INSERT INTO v2_alphafold_models SELECT * FROM _af_tmp")
        con.unregister("_af_tmp")
    print(f"[alphafold] {con.execute('SELECT COUNT(*) FROM v2_alphafold_models').fetchone()[0]:,} model rows")


def main() -> None:
    if not WAREHOUSE.exists():
        raise SystemExit(f"warehouse missing: {WAREHOUSE}")
    con = duckdb.connect(str(WAREHOUSE))
    ingest_pfam_clans(con)
    ingest_mcsa(con)
    ingest_alphafold(con)
    con.close()


if __name__ == "__main__":
    main()
