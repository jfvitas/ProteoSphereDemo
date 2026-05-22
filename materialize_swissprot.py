"""Stream-parse uniprot_sprot.xml.gz and materialize all major annotation axes.

Strategy: write intermediate parquet shards (memory-bounded), then bulk-load
each shard via DuckDB's COPY/read_parquet (vectorized, multi-core).

Extends or creates:
  * v2_motif_membership          (Pfam, InterPro)              [extend]
  * v2_sequence_cluster_membership (UniRef50/90/100)            [extend]
  * v2_ortholog_cluster_membership (OrthoDB)                    [extend]
  * v2_ec_class_membership         (EC numbers)                 [extend]
  * v2_pdb_uniprot                 (PDB xrefs)                  [extend]
  * v2_go_membership               (GO terms with aspect)       [new]
  * v2_residue_annotations         (active/binding/modified)    [new]
  * v2_protein_entry               (organism + name)            [new]
"""

from __future__ import annotations

import gc
import gzip
import re
import shutil
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree

HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
SPROT_XML_GZ = Path("D:/documents/ProteoSphereV2/data/raw/uniprot/current/uniprot/uniprot_sprot.xml.gz")
SPROT_XML = Path("D:/documents/ProteoSphereV2/cache/uniprot_sprot.xml")
SHARD_ROOT = Path("D:/documents/ProteoSphereV2/cache/sprot_shards")
SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

NS = "{https://uniprot.org/uniprot}"  # NOTE: https in actual XML (UniProt 2026.x)
SHARD_EVERY = 20000  # entries per shard

FEATURE_TYPES_TO_KEEP = {
    "active site", "binding site", "metal ion-binding site", "site",
    "modified residue", "lipid moiety-binding region", "glycosylation site",
}


class ShardWriter:
    def __init__(self, root: Path, name: str, schema: pa.Schema):
        self.root = root / name
        self.root.mkdir(parents=True, exist_ok=True)
        # Clean any old shards
        for f in self.root.glob("*.parquet"):
            f.unlink()
        self.schema = schema
        self.cols = [f.name for f in schema]
        self.buf: list[tuple] = []
        self.shard_idx = 0
        self.total_rows = 0

    def append_row(self, *vals):
        self.buf.append(vals)

    def flush_if_needed(self, force: bool = False):
        if not self.buf:
            return
        if not force and len(self.buf) < 100000:
            return
        df = pd.DataFrame(self.buf, columns=self.cols)
        table = pa.Table.from_pandas(df, schema=self.schema, preserve_index=False)
        shard_path = self.root / f"shard_{self.shard_idx:05d}.parquet"
        pq.write_table(table, shard_path, compression="zstd")
        self.shard_idx += 1
        self.total_rows += len(self.buf)
        self.buf.clear()
        del df, table

    def close(self):
        self.flush_if_needed(force=True)


def main() -> None:
    if not SPROT_XML.exists():
        raise SystemExit(f"Swiss-Prot XML missing: {SPROT_XML}")
    if not WAREHOUSE.exists():
        raise SystemExit(f"warehouse missing: {WAREHOUSE}")

    # Wipe shard dir
    if SHARD_ROOT.exists():
        shutil.rmtree(SHARD_ROOT)
    SHARD_ROOT.mkdir(parents=True)

    # Define schemas
    schemas = {
        "motif": pa.schema([
            ("uniprot", pa.string()), ("namespace", pa.string()),
            ("identifier", pa.string()), ("label", pa.string()),
            ("snapshot_id", pa.string())]),
        "seq": pa.schema([
            ("uniprot", pa.string()), ("source", pa.string()),
            ("uniref100", pa.string()), ("uniref90", pa.string()),
            ("uniref50", pa.string()), ("uniparc", pa.string()),
            ("taxon", pa.string()), ("snapshot_id", pa.string())]),
        "orth": pa.schema([
            ("uniprot", pa.string()), ("source", pa.string()),
            ("orthodb_cluster", pa.string()), ("snapshot_id", pa.string())]),
        "ec": pa.schema([
            ("uniprot", pa.string()), ("source", pa.string()),
            ("ec4", pa.string()), ("ec3", pa.string()),
            ("ec2", pa.string()), ("label", pa.string()),
            ("snapshot_id", pa.string())]),
        "pdb": pa.schema([
            ("pdb_id", pa.string()), ("chain", pa.string()),
            ("uniprot", pa.string()), ("sp_beg", pa.int32()),
            ("sp_end", pa.int32()), ("source", pa.string()),
            ("snapshot_id", pa.string())]),
        "go": pa.schema([
            ("uniprot", pa.string()), ("go_id", pa.string()),
            ("aspect", pa.string()), ("evidence_code", pa.string()),
            ("snapshot_id", pa.string())]),
        "res": pa.schema([
            ("uniprot", pa.string()), ("position", pa.int32()),
            ("end_position", pa.int32()), ("feature_type", pa.string()),
            ("description", pa.string()), ("snapshot_id", pa.string())]),
        "entry": pa.schema([
            ("uniprot", pa.string()), ("secondary_accs", pa.string()),
            ("entry_name", pa.string()), ("recommended_name", pa.string()),
            ("organism", pa.string()), ("taxon_id", pa.int32()),
            ("sequence_length", pa.int32()), ("snapshot_id", pa.string())]),
    }
    writers = {k: ShardWriter(SHARD_ROOT, k, v) for k, v in schemas.items()}

    con = duckdb.connect(str(WAREHOUSE))

    # New tables (drop+create)
    con.execute("DROP TABLE IF EXISTS v2_go_membership")
    con.execute("""
        CREATE TABLE v2_go_membership (
            uniprot       VARCHAR, go_id VARCHAR, aspect VARCHAR,
            evidence_code VARCHAR, snapshot_id VARCHAR)
    """)
    con.execute("DROP TABLE IF EXISTS v2_residue_annotations")
    con.execute("""
        CREATE TABLE v2_residue_annotations (
            uniprot       VARCHAR, position INTEGER, end_position INTEGER,
            feature_type  VARCHAR, description VARCHAR, snapshot_id VARCHAR)
    """)
    con.execute("DROP TABLE IF EXISTS v2_protein_entry")
    con.execute("""
        CREATE TABLE v2_protein_entry (
            uniprot          VARCHAR, secondary_accs VARCHAR,
            entry_name       VARCHAR, recommended_name VARCHAR,
            organism         VARCHAR, taxon_id INTEGER,
            sequence_length  INTEGER, snapshot_id VARCHAR)
    """)
    con.close()

    print(f"[sprot] streaming {SPROT_XML} (ns={NS})", flush=True)
    t0 = time.time()
    n_entries = 0

    # lxml.iterparse streams the decompressed XML file directly.
    if not SPROT_XML.exists():
        print(f"[sprot] decompressing {SPROT_XML_GZ} -> {SPROT_XML}", flush=True)
        with gzip.open(str(SPROT_XML_GZ), "rb") as src, open(SPROT_XML, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1 << 20)
    ctx = etree.iterparse(str(SPROT_XML), events=("end",), tag=NS + "entry",
                          huge_tree=True, recover=True)
    if True:
        for event, elem in ctx:
            try:
                accs = [a.text for a in elem.findall(NS + "accession") if a.text]
                if not accs:
                    continue
                primary = accs[0]
                secondary = ",".join(accs[1:])

                name_elem = elem.find(NS + "name")
                entry_name = name_elem.text if name_elem is not None else ""

                rec_name = ""
                pname = elem.find(NS + "protein")
                if pname is not None:
                    rn = pname.find(NS + "recommendedName")
                    if rn is not None:
                        fn = rn.find(NS + "fullName")
                        if fn is not None and fn.text:
                            rec_name = fn.text

                organism_name = ""
                taxon_id = None
                org = elem.find(NS + "organism")
                if org is not None:
                    sci = org.find(NS + "name[@type='scientific']")
                    if sci is not None and sci.text:
                        organism_name = sci.text
                    tax = org.find(NS + "dbReference[@type='NCBI Taxonomy']")
                    if tax is not None:
                        try:
                            taxon_id = int(tax.get("id"))
                        except (TypeError, ValueError):
                            taxon_id = None

                seq_elem = elem.find(NS + "sequence")
                seq_len = None
                if seq_elem is not None:
                    try:
                        seq_len = int(seq_elem.get("length") or 0) or None
                    except ValueError:
                        seq_len = None

                writers["entry"].append_row(
                    primary, secondary, entry_name, rec_name, organism_name, taxon_id, seq_len, SNAPSHOT_ID)

                ec_nums = []
                if pname is not None:
                    for ec in pname.iter(NS + "ecNumber"):
                        if ec.text:
                            ec_nums.append(ec.text.strip())

                uniref50 = uniref90 = uniref100 = None
                pfam_ids = []
                interpro_ids = []
                interpro_names = {}
                orthodb = None
                pdb_xrefs = []
                go_terms = []

                for dr in elem.findall(NS + "dbReference"):
                    typ = dr.get("type")
                    dr_id = dr.get("id")
                    if not dr_id:
                        continue
                    if typ == "Pfam":
                        pfam_ids.append(dr_id)
                    elif typ == "InterPro":
                        interpro_ids.append(dr_id)
                        nm = dr.find(NS + "property[@type='entry name']")
                        if nm is not None:
                            interpro_names[dr_id] = nm.get("value") or ""
                    elif typ == "UniRef50":
                        uniref50 = dr_id
                    elif typ == "UniRef90":
                        uniref90 = dr_id
                    elif typ == "UniRef100":
                        uniref100 = dr_id
                    elif typ == "OrthoDB":
                        orthodb = dr_id
                    elif typ == "PDB":
                        chains_elem = dr.find(NS + "property[@type='chains']")
                        chains = chains_elem.get("value") if chains_elem is not None else ""
                        pdb_xrefs.append((dr_id, chains))
                    elif typ == "GO":
                        aspect = ""
                        evidence = ""
                        for p in dr.findall(NS + "property"):
                            ptype = p.get("type")
                            pval = p.get("value")
                            if ptype == "term" and pval and ":" in pval:
                                aspect = pval.split(":", 1)[0]
                            elif ptype == "evidence":
                                evidence = pval or ""
                        go_terms.append((dr_id, aspect, evidence))

                for pf in pfam_ids:
                    writers["motif"].append_row(primary, "Pfam", pf, "", SNAPSHOT_ID)
                for ip in interpro_ids:
                    writers["motif"].append_row(primary, "InterPro", ip, interpro_names.get(ip, ""), SNAPSHOT_ID)

                if any([uniref50, uniref90, uniref100]):
                    writers["seq"].append_row(
                        primary, "uniprot_sprot",
                        uniref100 or "", uniref90 or "", uniref50 or "", "",
                        f"taxon:{taxon_id}" if taxon_id else "",
                        SNAPSHOT_ID)

                if orthodb:
                    writers["orth"].append_row(primary, "uniprot_sprot", orthodb, SNAPSHOT_ID)

                for ec in ec_nums:
                    parts = ec.split(".")
                    while len(parts) < 4:
                        parts.append("")
                    ec1, ec2_, ec3, ec4 = parts[0], parts[1], parts[2], parts[3]
                    ec2_full = f"{ec1}.{ec2_}" if ec2_ else ec1
                    ec3_full = f"{ec1}.{ec2_}.{ec3}" if ec3 else ec2_full
                    writers["ec"].append_row(primary, "uniprot_sprot", ec, ec3_full, ec2_full, "", SNAPSHOT_ID)

                for (pdb_id, chains) in pdb_xrefs:
                    chain_letter = ""
                    sp_beg = 0
                    sp_end = 0
                    m = re.match(r"^([A-Za-z0-9/]+)=(\d+)-(\d+)$", chains or "")
                    if m:
                        chain_letter = m.group(1).split("/")[0]
                        sp_beg = int(m.group(2))
                        sp_end = int(m.group(3))
                    writers["pdb"].append_row(pdb_id.lower(), chain_letter, primary, sp_beg, sp_end, "UniProt-DR", SNAPSHOT_ID)

                for (go_id, aspect, ev) in go_terms:
                    writers["go"].append_row(primary, go_id, aspect, ev, SNAPSHOT_ID)

                for feat in elem.findall(NS + "feature"):
                    ftype = feat.get("type") or ""
                    if ftype not in FEATURE_TYPES_TO_KEEP:
                        continue
                    desc = feat.get("description") or ""
                    pos_elem = feat.find(NS + "location/" + NS + "position")
                    begin_elem = feat.find(NS + "location/" + NS + "begin")
                    end_elem = feat.find(NS + "location/" + NS + "end")
                    pos = None
                    epos = None
                    if pos_elem is not None:
                        try:
                            pos = int(pos_elem.get("position") or 0)
                        except (ValueError, TypeError):
                            pos = None
                    elif begin_elem is not None and end_elem is not None:
                        try:
                            pos = int(begin_elem.get("position") or 0)
                            epos = int(end_elem.get("position") or 0)
                        except (ValueError, TypeError):
                            pos = None
                    if pos:
                        writers["res"].append_row(primary, pos, epos, ftype, desc[:500], SNAPSHOT_ID)

                n_entries += 1
                if n_entries % SHARD_EVERY == 0:
                    for w in writers.values():
                        w.flush_if_needed(force=True)
                    elapsed = time.time() - t0
                    rate = n_entries / max(elapsed, 0.01)
                    sizes = ",".join(f"{k}={w.total_rows:,}" for k, w in writers.items())
                    print(f"[sprot] {n_entries:,} entries in {elapsed:.1f}s ({rate:.0f}/s) | {sizes}", flush=True)
                    gc.collect()
            finally:
                # lxml: release the entry + previous siblings the root holds
                elem.clear(keep_tail=True)
                while elem.getprevious() is not None:
                    del elem.getparent()[0]

    for w in writers.values():
        w.close()

    elapsed = time.time() - t0
    print(f"[sprot] parse done: {n_entries:,} entries in {elapsed:.1f}s", flush=True)
    for k, w in writers.items():
        print(f"[sprot]   {k}: {w.total_rows:,} rows in {w.shard_idx} shards", flush=True)

    # Now bulk-load each shard set into DuckDB
    print(f"[sprot] bulk loading shards into DuckDB", flush=True)
    con = duckdb.connect(str(WAREHOUSE))
    tbl_map = {
        "motif": ("v2_motif_membership", ["uniprot", "namespace", "identifier", "label", "snapshot_id"]),
        "seq": ("v2_sequence_cluster_membership",
                ["uniprot", "source", "uniref100", "uniref90", "uniref50", "uniparc", "taxon", "snapshot_id"]),
        "orth": ("v2_ortholog_cluster_membership",
                 ["uniprot", "source", "orthodb_cluster", "snapshot_id"]),
        "ec": ("v2_ec_class_membership",
               ["uniprot", "source", "ec4", "ec3", "ec2", "label", "snapshot_id"]),
        "pdb": ("v2_pdb_uniprot",
                ["pdb_id", "chain", "uniprot", "sp_beg", "sp_end", "source", "snapshot_id"]),
        "go": ("v2_go_membership", ["uniprot", "go_id", "aspect", "evidence_code", "snapshot_id"]),
        "res": ("v2_residue_annotations",
                ["uniprot", "position", "end_position", "feature_type", "description", "snapshot_id"]),
        "entry": ("v2_protein_entry",
                  ["uniprot", "secondary_accs", "entry_name", "recommended_name",
                   "organism", "taxon_id", "sequence_length", "snapshot_id"]),
    }
    for k, (table, cols) in tbl_map.items():
        shard_dir = SHARD_ROOT / k
        shards = list(shard_dir.glob("*.parquet"))
        if not shards:
            print(f"[sprot]   {table}: no shards", flush=True)
            continue
        glob = str(shard_dir / "*.parquet").replace("\\", "/")
        sql = f"INSERT INTO {table} ({','.join(cols)}) SELECT {','.join(cols)} FROM read_parquet('{glob}')"
        before = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.execute(sql)
        after = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"[sprot]   {table}: {before:,} -> {after:,} (+{after-before:,})", flush=True)

    # Final summary
    for tbl in ("v2_motif_membership", "v2_sequence_cluster_membership", "v2_ortholog_cluster_membership",
                "v2_ec_class_membership", "v2_pdb_uniprot", "v2_go_membership",
                "v2_residue_annotations", "v2_protein_entry"):
        n = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
        nd = con.execute(f"SELECT COUNT(DISTINCT uniprot) FROM {tbl}").fetchone()[0]
        print(f"[sprot] {tbl}: {n:,} rows / {nd:,} distinct UniProts", flush=True)

    yeast = con.execute("""
      SELECT 'motif' axis, COUNT(*) FROM v2_motif_membership WHERE uniprot='P60010'
      UNION ALL SELECT 'seq_cluster', COUNT(*) FROM v2_sequence_cluster_membership WHERE uniprot='P60010'
      UNION ALL SELECT 'orth', COUNT(*) FROM v2_ortholog_cluster_membership WHERE uniprot='P60010'
      UNION ALL SELECT 'ec', COUNT(*) FROM v2_ec_class_membership WHERE uniprot='P60010'
      UNION ALL SELECT 'pdb', COUNT(*) FROM v2_pdb_uniprot WHERE uniprot='P60010'
      UNION ALL SELECT 'go', COUNT(*) FROM v2_go_membership WHERE uniprot='P60010'
      UNION ALL SELECT 'entry', COUNT(*) FROM v2_protein_entry WHERE uniprot='P60010'
    """).fetchall()
    print(f"[sprot] yeast actin P60010 coverage:", flush=True)
    for r in yeast:
        print(f"           {r[0]}: {r[1]}", flush=True)

    con.close()


if __name__ == "__main__":
    main()
