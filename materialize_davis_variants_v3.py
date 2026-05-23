#!/usr/bin/env python3
"""Davis variant resolution pass 3 — UniProt REST API for the remaining stragglers.

After v2's pass, 119 davis keys still don't resolve. Most are missense variants
of well-known kinases (FLT3, KIT, BRAF, EGFR, RET, ABL1, MET, etc.) whose base
gene names are real but my v2 hand-curated dict missed them. This pass queries
UniProt's REST API for each unresolved base gene and patches the rest.
"""
from __future__ import annotations
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import quote
import urllib.request
import urllib.error

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = REPO / "data" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

_MUTATION_RE = re.compile(r"^([A-Z0-9]+)\(([^)]+)\)([a-z]*)$")
_PHOSPHO_RE = re.compile(r"^([A-Z0-9]+)[\-_]?(phosphorylated|phospho|p)$", re.IGNORECASE)


def parse_variant(key):
    s = key.strip()
    md = {}
    m = _MUTATION_RE.match(s)
    if m:
        base = m.group(1)
        inner = m.group(2)
        suffix = m.group(3) or ""
        if re.match(r"^[A-Z]\d+[A-Z*]$", inner):
            md["mutation"] = inner
        else:
            md["region"] = inner
        if suffix.lower() in ("p", "ph"):
            md["phospho"] = True
        return base, md
    m = _PHOSPHO_RE.match(s)
    if m:
        return m.group(1), {"phospho": True}
    return s, md


# Hand-curated for common Davis kinase synonyms that UniProt won't always match by gene
HAND_CURATED = {
    "VEGFR2": "P35968",   # KDR
    "VEGFR1": "P17948",   # FLT1
    "VEGFR3": "P35916",   # FLT4
    "YES":    "P07947",   # YES1
    "FAK":    "Q05397",   # PTK2
    "FAK2":   "Q14289",   # PTK2B / PYK2
    "BLK":    "P51451",
    "FLT3":   "P36888",
    "KIT":    "P10721",
    "BRAF":   "P15056",
    "EGFR":   "P00533",
    "RET":    "P07949",
    "ABL1":   "P00519",
    "ABL2":   "P42684",
    "MET":    "P08581",
    "JAK1":   "P23458",
    "JAK2":   "O60674",
    "JAK3":   "P52333",
    "JAK4":   "P52333",  # not real — alias for JAK3 in some tables
    "ALK":    "Q9UM73",
    "ROS1":   "P08922",
    "PDGFRA": "P16234",
    "PDGFRB": "P09619",
    "FGFR1":  "P11362",
    "FGFR2":  "P21802",
    "FGFR3":  "P22607",
    "FGFR4":  "P22455",
    "RAF1":   "P04049",
    "ARAF":   "P10398",
    "PIK3CA": "P42336",
    "PIK3CB": "P42338",
    "PIK3CD": "O00329",
    "PIK3CG": "P48736",
    "MTOR":   "P42345",
    "ATM":    "Q13315",
    "ATR":    "Q13535",
    "DDR1":   "Q08345",
    "DDR2":   "Q16832",
    "EPHA1":  "P21709",
    "EPHA2":  "P29317",
    "EPHA3":  "P29320",
    "EPHA4":  "P54764",
    "EPHA5":  "P54756",
    "EPHA6":  "Q4KMQ2",
    "EPHA7":  "Q15375",
    "EPHA8":  "P29322",
    "EPHB1":  "P54762",
    "EPHB2":  "P29323",
    "EPHB3":  "P54753",
    "EPHB4":  "P54760",
    "EPHB6":  "O15197",
    "TGFBR1": "P36897",
    "TGFBR2": "P37173",
    "ACVR1":  "Q04771",
    "ACVR1B": "P36896",
    "ACVR2A": "P27037",
    "ACVR2B": "Q13705",
    "BMPR1A": "P36894",
    "BMPR1B": "O00238",
    "BMPR2":  "Q13873",
    "ROR1":   "Q01973",
    "ROR2":   "Q01974",
    "RYK":    "P34925",
    "STYK1":  "Q6J9G0",
    "MUSK":   "O15146",
    "AXL":    "P30530",
    "MERTK":  "Q12866",
    "TIE1":   "P35590",
    "FLT4":   "P35916",
    "CSF1R":  "P07333",
    "CSK":    "P41240",
    "ITK":    "Q08881",
    "TEC":    "P42680",
    "TXK":    "P42681",
    "BMX":    "P51813",
    "BTK":    "Q06187",
    "ZAP70":  "P43403",
    "SYK":    "P43405",
    "LYN":    "P07948",
    "FYN":    "P06241",
    "FGR":    "P09769",
    "HCK":    "P08631",
    "LCK":    "P06239",
    "SRC":    "P12931",
    "SRMS":   "Q9H3Y6",
    "FRK":    "P42685",
    "PTK6":   "Q13882",
    "MATK":   "P42679",
    "MAP4K1": "Q92918",
    "MAP4K2": "Q12851",
    "MAP4K3": "Q8IVH8",
    "MAP4K4": "O95819",
    "MAP4K5": "Q9Y4K4",
    "MINK1":  "Q8N4C8",
    "TNIK":   "Q9UKE5",
    "HGS":    "O14964",
    "NIM1":   "Q8IY84",
    "NIM1K":  "Q8IY84",
    "TLK1":   "Q9UKI8",
    "TLK2":   "Q86UE8",
    "BLK":    "P51451",
    "PRKACA": "P17612",
    "PRKACB": "P22694",
    "PRKACG": "P22612",
    "PRKACA-ALPHA": "P17612",
    "PRKAR1A": "P10644",
    "PRKAR2A": "P13861",
    "PRKAR2B": "P31323",
    "PRKAR1B": "P31321",
    "PRKACA1": "P17612",
}


def uniprot_lookup(gene: str, timeout=10) -> str | None:
    """Query UniProt REST for a human Swiss-Prot entry by gene name."""
    # Try exact gene-name match first, restricted to Swiss-Prot human
    cache_path = CACHE / f"uniprot_lookup_{gene}.json"
    if cache_path.exists():
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            return data.get("uniprot")
        except Exception:
            pass

    query = f"gene_exact:{gene} AND organism_id:9606 AND reviewed:true"
    url = (
        "https://rest.uniprot.org/uniprotkb/search?"
        f"query={quote(query)}&format=json&fields=accession,gene_names&size=1"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ProteoSphere/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"    REST lookup failed for {gene}: {exc}")
        return None

    results = data.get("results", [])
    if not results:
        # Fall back to broader synonym search
        query2 = f"gene:{gene} AND organism_id:9606 AND reviewed:true"
        url2 = (
            "https://rest.uniprot.org/uniprotkb/search?"
            f"query={quote(query2)}&format=json&fields=accession,gene_names&size=1"
        )
        try:
            req = urllib.request.Request(url2, headers={"User-Agent": "ProteoSphere/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
        except Exception:
            pass

    uniprot = results[0]["primaryAccession"] if results else None
    cache_path.write_text(json.dumps({"uniprot": uniprot, "gene": gene}),
                          encoding="utf-8")
    return uniprot


def main():
    print("=== Davis variant resolution pass 3 (UniProt REST) ===")
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    unresolved = [r[0] for r in con.execute(
        "SELECT source_key FROM davis_bridge_uniprot WHERE confidence = 'unresolved'"
    ).fetchall()]
    print(f"  unresolved: {len(unresolved)}")

    new_resolutions = []
    still_unresolved = []
    for key in unresolved:
        base, md = parse_variant(key)
        base_upper = base.upper()

        # Try hand-curated first (much faster than REST)
        up = HAND_CURATED.get(base_upper) or HAND_CURATED.get(key.upper())
        if not up:
            # REST lookup, rate-limited
            up = uniprot_lookup(base_upper)
            time.sleep(0.2)  # 5 req/s rate limit

        if up:
            new_resolutions.append({
                "davis_key": key,
                "base_gene": base,
                "uniprot": up,
                "mutation": md.get("mutation"),
                "phospho": bool(md.get("phospho")),
                "region": md.get("region"),
                "activated": bool(md.get("activated")),
                "complex_partners": None,
            })
        else:
            still_unresolved.append(key)

    print(f"  resolved: {len(new_resolutions)}")
    print(f"  STILL unresolved: {len(still_unresolved)}")
    if still_unresolved:
        for s in still_unresolved[:20]:
            print(f"    {s}")

    # Upsert
    n_ins = 0; n_up = 0
    for r in new_resolutions:
        existing = con.execute(
            "SELECT davis_key FROM v2_davis_variant_resolution WHERE davis_key = ?",
            [r["davis_key"]]
        ).fetchall()
        if existing:
            con.execute(
                "UPDATE v2_davis_variant_resolution SET "
                " base_gene=?, uniprot=?, mutation=?, phospho=?, region=?, activated=?, complex_partners=? "
                "WHERE davis_key=?",
                [r["base_gene"], r["uniprot"], r["mutation"], r["phospho"],
                 r["region"], r["activated"], r["complex_partners"], r["davis_key"]]
            )
            n_up += 1
        else:
            con.execute(
                "INSERT INTO v2_davis_variant_resolution "
                "(davis_key, base_gene, uniprot, mutation, phospho, region, activated, complex_partners) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [r["davis_key"], r["base_gene"], r["uniprot"], r["mutation"], r["phospho"],
                 r["region"], r["activated"], r["complex_partners"]]
            )
            n_ins += 1
    print(f"  variant table: {n_ins} inserts + {n_up} updates")

    # Update bridge
    if new_resolutions:
        con.execute("DROP TABLE IF EXISTS _davis_resolved")
        con.execute("CREATE TEMP TABLE _davis_resolved (davis_key VARCHAR PRIMARY KEY, uniprot VARCHAR)")
        con.executemany("INSERT INTO _davis_resolved VALUES (?, ?)",
                        [(r["davis_key"], r["uniprot"]) for r in new_resolutions])
        con.execute(
            "UPDATE davis_bridge_uniprot AS b "
            "SET uniprot = r.uniprot, confidence = 'wt_fallback' "
            "FROM _davis_resolved AS r "
            "WHERE b.source_key = r.davis_key AND b.confidence = 'unresolved'"
        )

    print("\n  final davis_bridge_uniprot confidence counts:")
    for r in con.execute(
        "SELECT confidence, COUNT(*) FROM davis_bridge_uniprot GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall():
        print(f"    {r[0]}: {r[1]}")

    n_with = con.execute(
        "SELECT COUNT(*) FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL"
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM davis_bridge_uniprot").fetchone()[0]
    print(f"  resolved: {n_with}/{total}")

    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
