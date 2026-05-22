"""Materialize cross-species interaction corpora.

Sources:
  * IntAct  (CC-BY 4.0) — intact.zip from data/raw/intact/current/intact/
  * BioGRID (MIT)        — cache/biogrid/BIOGRID-ORGANISM-LATEST.mitab.zip
  * STRING  (CC-BY 4.0)  — cache/string/<taxid>.protein.links.v12.0.txt.gz
                            (combined_score >= 700 filter)
  * Reactome (CC0)       — cache/reactome/UniProt2Reactome_PE_Pathway.txt

Creates:
  v2_intact_interactions      (uniprot_a, uniprot_b, interaction_type,
                               detection_method, source_db, pmid,
                               confidence_score, taxon_a, taxon_b)
  v2_biogrid_interactions     (uniprot_a, uniprot_b, interaction_type,
                               evidence, organism_taxid, pmid)
  v2_string_interactions      (uniprot_a, uniprot_b, combined_score,
                               organism_taxid)
  v2_reactome_pathway_membership(uniprot, pathway_id, pathway_name,
                                 top_level_pathway, evidence, taxon)

Idempotent (drops + recreates each table).
"""
from __future__ import annotations

import csv
import gzip
import io
import re
import time
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = Path("D:/documents/ProteoSphereV2/cache")
INTACT_ZIP = Path("D:/documents/ProteoSphereV2/data/raw/intact/current/intact/intact.zip")
BIOGRID_ZIP = CACHE / "biogrid" / "BIOGRID-ORGANISM-LATEST.mitab.zip"
STRING_DIR = CACHE / "string"
REACTOME_FILE = CACHE / "reactome" / "UniProt2Reactome_PE_Pathway.txt"
STRING_ALIASES = STRING_DIR / "all_organisms.aliases.v12.0.txt.gz"
SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
STRING_CUTOFF = 700  # combined_score
INTACT_MI_CUTOFF = 0.45  # IntAct confidence


def _uniprot_from_mitab_id(s: str) -> str | None:
    if not s:
        return None
    # Format: "uniprotkb:P12345" possibly with "|extra"
    for tok in s.split("|"):
        if tok.lower().startswith("uniprotkb:"):
            acc = tok.split(":", 1)[1]
            # strip isoform suffix
            acc = acc.split("-")[0]
            if re.fullmatch(r"[A-Z][A-Z0-9]{5,9}", acc.upper()):
                return acc.upper()
    return None


def _taxon_from_mitab(s: str) -> int | None:
    if not s:
        return None
    m = re.search(r"taxid:(-?\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _confidence_from_mitab(s: str) -> float | None:
    if not s:
        return None
    for tok in s.split("|"):
        if tok.lower().startswith("intact-miscore:"):
            try:
                return float(tok.split(":", 1)[1])
            except (ValueError, IndexError):
                pass
    return None


def _pmid_from_mitab(s: str) -> str:
    if not s:
        return ""
    for tok in s.split("|"):
        if tok.lower().startswith("pubmed:"):
            return tok.split(":", 1)[1]
    return ""


def _label_from_mitab(s: str) -> str:
    if not s or s == "-":
        return ""
    # MI:0915(physical association) -> physical association
    m = re.match(r"^[^(]+\(([^)]+)\)$", s)
    if m:
        return m.group(1)
    return s


def ingest_intact(con: duckdb.DuckDBPyConnection) -> None:
    if not INTACT_ZIP.exists():
        print(f"[intact] missing {INTACT_ZIP} -- skipping")
        return
    print(f"[intact] reading {INTACT_ZIP}")

    con.execute("DROP TABLE IF EXISTS v2_intact_interactions")
    con.execute("""
        CREATE TABLE v2_intact_interactions (
            uniprot_a        VARCHAR,
            uniprot_b        VARCHAR,
            interaction_type VARCHAR,
            detection_method VARCHAR,
            source_db        VARCHAR,
            pmid             VARCHAR,
            confidence_score DOUBLE,
            taxon_a          INTEGER,
            taxon_b          INTEGER,
            snapshot_id      VARCHAR
        )
    """)

    n_kept = 0
    n_seen = 0
    t0 = time.time()
    rows_batch: list[tuple] = []

    with zipfile.ZipFile(INTACT_ZIP, "r") as zf:
        # Find the main MITAB file
        member = None
        for nm in zf.namelist():
            if nm.endswith("intact.txt"):
                member = nm
                break
        if not member:
            print(f"[intact] couldn't find intact.txt in {INTACT_ZIP}")
            return
        print(f"[intact] streaming {member}")
        with zf.open(member, "r") as raw:
            txt = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            header = txt.readline()  # discard header
            for line in txt:
                n_seen += 1
                parts = line.split("\t")
                if len(parts) < 15:
                    continue
                # MITAB 2.7 columns:
                # 0: ID A, 1: ID B, 6: detection method, 8: pmid,
                # 9: taxon A, 10: taxon B, 11: interaction type,
                # 12: source db, 14: confidence score
                u_a = _uniprot_from_mitab_id(parts[0])
                u_b = _uniprot_from_mitab_id(parts[1])
                if not u_a or not u_b:
                    continue
                conf = _confidence_from_mitab(parts[14])
                if conf is not None and conf < INTACT_MI_CUTOFF:
                    continue
                rows_batch.append((
                    u_a, u_b,
                    _label_from_mitab(parts[11]),
                    _label_from_mitab(parts[6]),
                    _label_from_mitab(parts[12]),
                    _pmid_from_mitab(parts[8]),
                    conf,
                    _taxon_from_mitab(parts[9]),
                    _taxon_from_mitab(parts[10]),
                    SNAPSHOT_ID,
                ))
                n_kept += 1
                if len(rows_batch) >= 50000:
                    _bulk_insert(con, "v2_intact_interactions", rows_batch,
                                 ["uniprot_a", "uniprot_b", "interaction_type", "detection_method",
                                  "source_db", "pmid", "confidence_score", "taxon_a", "taxon_b", "snapshot_id"])
                    rows_batch = []
                if n_seen % 500000 == 0:
                    print(f"[intact] seen {n_seen:,} / kept {n_kept:,} ({time.time()-t0:.1f}s)", flush=True)
        if rows_batch:
            _bulk_insert(con, "v2_intact_interactions", rows_batch,
                         ["uniprot_a", "uniprot_b", "interaction_type", "detection_method",
                          "source_db", "pmid", "confidence_score", "taxon_a", "taxon_b", "snapshot_id"])

    print(f"[intact] kept {n_kept:,} / seen {n_seen:,} in {time.time()-t0:.1f}s")


def _bulk_insert(con, table, rows, cols):
    df = pd.DataFrame(rows, columns=cols)
    con.register("_tmp_iact", df)
    con.execute(f"INSERT INTO {table} SELECT * FROM _tmp_iact")
    con.unregister("_tmp_iact")


def _build_entrez_to_uniprot() -> dict[str, str]:
    """Use Swiss-Prot DR GeneID cross-refs to build Entrez->UniProt."""
    print(f"[biogrid] building Entrez Gene -> UniProt from Swiss-Prot DR")
    out: dict[str, str] = {}
    t0 = time.time()
    from lxml import etree
    NS = "{https://uniprot.org/uniprot}"
    sprot_xml = Path("D:/documents/ProteoSphereV2/cache/uniprot_sprot.xml")
    if not sprot_xml.exists():
        return out
    ctx = etree.iterparse(str(sprot_xml), events=("end",), tag=NS + "entry",
                          huge_tree=True, recover=True)
    for ev, elem in ctx:
        try:
            accs = elem.findall(NS + "accession")
            primary = (accs[0].text or "") if accs else ""
            if not primary:
                continue
            for dr in elem.findall(NS + "dbReference"):
                if dr.get("type") == "GeneID":
                    gid = dr.get("id") or ""
                    if gid:
                        out.setdefault(gid, primary)
        finally:
            elem.clear(keep_tail=True)
            while elem.getprevious() is not None:
                del elem.getparent()[0]
    print(f"[biogrid] {len(out):,} Entrez Gene IDs mapped to UniProt in {time.time()-t0:.1f}s")
    return out


def ingest_biogrid(con: duckdb.DuckDBPyConnection) -> None:
    if not BIOGRID_ZIP.exists():
        print(f"[biogrid] missing {BIOGRID_ZIP} -- skipping")
        return
    print(f"[biogrid] reading {BIOGRID_ZIP}")
    entrez_to_uni = _build_entrez_to_uniprot()

    con.execute("DROP TABLE IF EXISTS v2_biogrid_interactions")
    con.execute("""
        CREATE TABLE v2_biogrid_interactions (
            uniprot_a        VARCHAR,
            uniprot_b        VARCHAR,
            interaction_type VARCHAR,
            evidence         VARCHAR,
            organism_taxid   INTEGER,
            pmid             VARCHAR,
            snapshot_id      VARCHAR
        )
    """)

    n_kept = 0
    n_seen = 0
    t0 = time.time()
    rows_batch: list[tuple] = []

    with zipfile.ZipFile(BIOGRID_ZIP, "r") as zf:
        for member in zf.namelist():
            if not member.endswith(".mitab.txt"):
                continue
            with zf.open(member, "r") as raw:
                txt = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
                header = txt.readline()
                for line in txt:
                    n_seen += 1
                    parts = line.split("\t")
                    if len(parts) < 15:
                        continue
                    # BioGRID MITAB: cols 0,1 are entrez gene/locuslink IDs.
                    # Map via the Swiss-Prot DR GeneID dict.
                    def _entrez_id(s: str) -> str:
                        # "entrez gene/locuslink:1234"
                        for tok in (s or "").split("|"):
                            if tok.startswith("entrez gene/locuslink:"):
                                return tok.split(":", 1)[1].strip()
                        return ""
                    eid_a = _entrez_id(parts[0])
                    eid_b = _entrez_id(parts[1])
                    u_a = entrez_to_uni.get(eid_a) if eid_a else None
                    u_b = entrez_to_uni.get(eid_b) if eid_b else None
                    if not u_a or not u_b:
                        continue
                    rows_batch.append((
                        u_a, u_b,
                        _label_from_mitab(parts[11]),
                        _label_from_mitab(parts[6]),
                        _taxon_from_mitab(parts[9]) or _taxon_from_mitab(parts[10]),
                        _pmid_from_mitab(parts[8]),
                        SNAPSHOT_ID,
                    ))
                    n_kept += 1
                    if len(rows_batch) >= 50000:
                        _bulk_insert(con, "v2_biogrid_interactions", rows_batch,
                                     ["uniprot_a", "uniprot_b", "interaction_type", "evidence",
                                      "organism_taxid", "pmid", "snapshot_id"])
                        rows_batch = []
                    if n_seen % 500000 == 0:
                        print(f"[biogrid] seen {n_seen:,} / kept {n_kept:,} ({time.time()-t0:.1f}s)", flush=True)
        if rows_batch:
            _bulk_insert(con, "v2_biogrid_interactions", rows_batch,
                         ["uniprot_a", "uniprot_b", "interaction_type", "evidence",
                          "organism_taxid", "pmid", "snapshot_id"])

    print(f"[biogrid] kept {n_kept:,} / seen {n_seen:,} in {time.time()-t0:.1f}s")


def ingest_string(con: duckdb.DuckDBPyConnection) -> None:
    """Map STRING protein IDs to UniProt via Swiss-Prot's STRING dbReference
    cross-refs (much faster than parsing the 107 MB all_organisms.aliases file)."""
    if not STRING_DIR.exists():
        print(f"[string] missing {STRING_DIR} -- skipping")
        return
    print(f"[string] building STRING_id -> UniProt map from Swiss-Prot DR (re-parsing XML)")
    string_to_uni: dict[str, str] = {}
    t0 = time.time()
    # Stream Swiss-Prot XML, extracting only STRING dbReferences (super fast)
    from lxml import etree
    NS = "{https://uniprot.org/uniprot}"
    sprot_xml = Path("D:/documents/ProteoSphereV2/cache/uniprot_sprot.xml")
    if sprot_xml.exists():
        ctx = etree.iterparse(str(sprot_xml), events=("end",), tag=NS + "entry",
                              huge_tree=True, recover=True)
        for ev, elem in ctx:
            try:
                primary = ""
                accs = elem.findall(NS + "accession")
                if accs:
                    primary = accs[0].text or ""
                if not primary:
                    continue
                for dr in elem.findall(NS + "dbReference"):
                    if dr.get("type") == "STRING":
                        sid = dr.get("id") or ""
                        if sid:
                            string_to_uni.setdefault(sid, primary)
            finally:
                elem.clear(keep_tail=True)
                while elem.getprevious() is not None:
                    del elem.getparent()[0]
        print(f"[string] {len(string_to_uni):,} STRING IDs mapped to UniProt via Swiss-Prot DR in {time.time()-t0:.1f}s")
    else:
        print(f"[string] missing decompressed Swiss-Prot XML; STRING mapping empty")

    con.execute("DROP TABLE IF EXISTS v2_string_interactions")
    con.execute("""
        CREATE TABLE v2_string_interactions (
            uniprot_a       VARCHAR,
            uniprot_b       VARCHAR,
            combined_score  INTEGER,
            organism_taxid  INTEGER,
            snapshot_id     VARCHAR
        )
    """)

    total_kept = 0
    files = sorted(STRING_DIR.glob("*.protein.links.v12.0.txt.gz"))
    print(f"[string] {len(files)} organism files")
    for f in files:
        taxid = int(f.name.split(".")[0])
        n_kept = 0
        rows_batch: list[tuple] = []
        with gzip.open(f, "rt", encoding="utf-8", errors="replace") as fh:
            header = fh.readline()
            for line in fh:
                # protein1 protein2 combined_score (space-sep)
                parts = line.rstrip().split(" ")
                if len(parts) < 3:
                    continue
                try:
                    score = int(parts[2])
                except ValueError:
                    continue
                if score < STRING_CUTOFF:
                    continue
                u_a = string_to_uni.get(parts[0])
                u_b = string_to_uni.get(parts[1])
                if not u_a or not u_b:
                    continue
                # Avoid duplicate (a,b) (b,a) by keeping only sorted pairs
                if u_a > u_b:
                    u_a, u_b = u_b, u_a
                rows_batch.append((u_a, u_b, score, taxid, SNAPSHOT_ID))
                n_kept += 1
                if len(rows_batch) >= 50000:
                    _bulk_insert(con, "v2_string_interactions", rows_batch,
                                 ["uniprot_a", "uniprot_b", "combined_score", "organism_taxid", "snapshot_id"])
                    rows_batch = []
        if rows_batch:
            _bulk_insert(con, "v2_string_interactions", rows_batch,
                         ["uniprot_a", "uniprot_b", "combined_score", "organism_taxid", "snapshot_id"])
        # Dedupe per-organism
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE _string_dedup AS
            SELECT DISTINCT uniprot_a, uniprot_b, MAX(combined_score) combined_score,
                   {taxid} AS organism_taxid, MAX(snapshot_id) snapshot_id
            FROM v2_string_interactions
            WHERE organism_taxid = {taxid}
            GROUP BY uniprot_a, uniprot_b
        """)
        con.execute(f"DELETE FROM v2_string_interactions WHERE organism_taxid = {taxid}")
        con.execute("INSERT INTO v2_string_interactions SELECT * FROM _string_dedup")
        per_org = con.execute(f"SELECT COUNT(*) FROM v2_string_interactions WHERE organism_taxid={taxid}").fetchone()[0]
        print(f"[string]   taxid={taxid}: kept {per_org:,} edges")
        total_kept += per_org

    print(f"[string] total: {total_kept:,} edges")


def ingest_reactome(con: duckdb.DuckDBPyConnection) -> None:
    if not REACTOME_FILE.exists():
        print(f"[reactome] missing {REACTOME_FILE} -- skipping")
        return
    print(f"[reactome] reading {REACTOME_FILE}")

    con.execute("DROP TABLE IF EXISTS v2_reactome_pathway_membership")
    con.execute("""
        CREATE TABLE v2_reactome_pathway_membership (
            uniprot          VARCHAR,
            pathway_id       VARCHAR,
            pathway_name     VARCHAR,
            top_level_pathway VARCHAR,
            evidence         VARCHAR,
            taxon            VARCHAR,
            snapshot_id      VARCHAR
        )
    """)

    n = 0
    rows_batch: list[tuple] = []
    t0 = time.time()
    with open(REACTOME_FILE, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            # Cols: Source DB ID, PhysicalEntity, Reactome ID, URL, PhysicalEntity Name,
            #       Pathway ID, URL, Pathway Name, Evidence, Species
            if len(parts) < 8:
                continue
            src_id = parts[0]
            # strip uniprot isoform suffix
            uniprot = src_id.split("-")[0].upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9]{5,9}", uniprot):
                continue
            pathway_id = parts[3] if len(parts) > 3 else ""
            # Sample layout: column 1 PE Reactome ID; col 3 Pathway Reactome ID;
            # col 5 Pathway URL; col 6 Pathway name; col 7 Evidence code; col 8 Species
            # Stable cols:
            #   parts[0]  Source DB ID (UniProt acc)
            #   parts[3]  Reactome pathway ID
            #   parts[5]  Pathway name (column 6)
            pathway_id = parts[3]
            pathway_name = parts[5] if len(parts) > 5 else ""
            evidence = parts[6] if len(parts) > 6 else ""
            taxon = parts[7] if len(parts) > 7 else ""
            rows_batch.append((uniprot, pathway_id, pathway_name, "", evidence, taxon, SNAPSHOT_ID))
            n += 1
            if len(rows_batch) >= 50000:
                _bulk_insert(con, "v2_reactome_pathway_membership", rows_batch,
                             ["uniprot", "pathway_id", "pathway_name", "top_level_pathway",
                              "evidence", "taxon", "snapshot_id"])
                rows_batch = []
    if rows_batch:
        _bulk_insert(con, "v2_reactome_pathway_membership", rows_batch,
                     ["uniprot", "pathway_id", "pathway_name", "top_level_pathway",
                      "evidence", "taxon", "snapshot_id"])
    print(f"[reactome] kept {n:,} rows in {time.time()-t0:.1f}s")


def main() -> None:
    if not WAREHOUSE.exists():
        raise SystemExit(f"warehouse missing: {WAREHOUSE}")
    con = duckdb.connect(str(WAREHOUSE))

    ingest_intact(con)
    ingest_biogrid(con)
    ingest_reactome(con)
    ingest_string(con)

    # Final sizes
    for tbl in ("v2_intact_interactions", "v2_biogrid_interactions",
                "v2_string_interactions", "v2_reactome_pathway_membership"):
        n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        print(f"[interactions] {tbl}: {n:,} rows")

    con.close()


if __name__ == "__main__":
    main()
