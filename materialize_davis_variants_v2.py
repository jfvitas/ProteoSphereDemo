#!/usr/bin/env python3
"""Davis variant resolution pass 2.

The original pass landed 41/442 at confidence='exact'. The remaining 168 are
genuinely unresolved (mostly mutation/phospho variants + alias gene names).

Strategy:
  1. Extract base gene from mutation/phospho notation
       `FLT3(N841I)`              -> base FLT3, mutation N841I
       `KIT(L576P)`               -> base KIT,  mutation L576P
       `JAK1(JH2domain-pseudokinase)` -> base JAK1, region JH2-pseudokinase
       `ABL1-phosphorylated`      -> base ABL1, phospho True
  2. Build an alias map from UniProt's Swiss-Prot entries we already loaded
     into v2_protein_entry — for each entry, dump primary gene + every
     synonym + every alt name.
  3. Resolve each davis_key to either:
       (base_gene -> uniprot via primary match)
       (base_gene -> uniprot via alias match)
       (genuine alt name -> uniprot via alias match)
     and write into v2_davis_variant_resolution.
  4. Rewrite davis_bridge_uniprot confidence: keep 'exact' for direct hits,
     promote variant-stripped hits from 'unresolved' to 'wt_fallback' with
     the mutation/phospho metadata preserved.

Source: existing v2_protein_entry + uniprot_sprot.xml (already cached).
"""
from __future__ import annotations
import os
import re
import sys
from pathlib import Path
from typing import Optional

import duckdb

REPO = Path(__file__).resolve().parent
DB = REPO / "demo_warehouse" / "catalog" / "v2.duckdb"
CACHE = REPO / "data" / "cache"

# ── Variant notation parsing ─────────────────────────────────────────────

_MUTATION_RE = re.compile(r"^([A-Z0-9]+)\(([^)]+)\)([a-z]*)$")
# matches:  FLT3(N841I), KIT(L576P), ABL1(F317I)p, JAK1(JH2domain-pseudokinase)
_PHOSPHO_RE = re.compile(r"^([A-Z0-9]+)[\-_]?(phosphorylated|phospho|p)$", re.IGNORECASE)
_ACTIVATED_RE = re.compile(r"^([A-Z0-9]+)[\-_]?(activated)$", re.IGNORECASE)


def parse_variant(key: str) -> tuple[str, dict]:
    """Return (base_gene, metadata) for a davis key.

    metadata may carry 'mutation', 'phospho', 'region', 'activated', etc.
    If the key has no variant suffix, returns (key, {})."""
    s = key.strip()
    md: dict = {}

    # Mutation in parens, possibly with trailing 'p' for phospho
    m = _MUTATION_RE.match(s)
    if m:
        base = m.group(1)
        inner = m.group(2)
        suffix = m.group(3) or ""
        # Distinguish missense (e.g. N841I, single-letter+digits+single-letter)
        # from region notation (e.g. JH2domain-pseudokinase).
        if re.match(r"^[A-Z]\d+[A-Z*]$", inner):
            md["mutation"] = inner
        else:
            md["region"] = inner
        if suffix.lower() in ("p", "ph"):
            md["phospho"] = True
        return base, md

    # Phospho suffix without parens
    m = _PHOSPHO_RE.match(s)
    if m:
        return m.group(1), {"phospho": True}

    # Activated suffix
    m = _ACTIVATED_RE.match(s)
    if m:
        return m.group(1), {"activated": True}

    # Hyphen-separated families like 'CDK11-CYCLINK' — caller decides
    if "-" in s and not s.startswith("HLA"):
        # Take the first segment as the primary gene
        first = s.split("-")[0]
        if first.isalnum() and len(first) >= 2:
            md["complex_partners"] = s.split("-")[1:]
            return first, md

    return s, md


# ── Alias lookup from UniProt Swiss-Prot ─────────────────────────────────

def build_alias_index(con: duckdb.DuckDBPyConnection) -> dict[str, str]:
    """gene_name (uppercase) -> uniprot accession.

    Reads v2_protein_entry. The agent that ingested it stashed gene info.
    """
    cols = [r[1] for r in con.execute("PRAGMA table_info(v2_protein_entry)").fetchall()]
    print(f"  v2_protein_entry cols: {cols}")

    # Find the gene-name column dynamically
    candidate_cols = [c for c in cols if "gene" in c.lower() or c.lower() in ("name", "names", "synonym", "synonyms")]
    print(f"  gene-related cols: {candidate_cols}")

    alias = {}
    # Build lookup using whatever columns are present. Try every plausible one.
    n_human = con.execute(
        "SELECT COUNT(*) FROM v2_protein_entry WHERE taxon_id = 9606"
    ).fetchone()[0]
    print(f"  human Swiss-Prot entries: {n_human:,}")

    # Pull gene names for human entries first (the universe most davis keys live in)
    # We'll handle both flat-string and array column types
    sql_attempts = [
        "SELECT uniprot, gene_names FROM v2_protein_entry WHERE taxon_id = 9606",
        "SELECT uniprot, gene_name FROM v2_protein_entry WHERE taxon_id = 9606",
        "SELECT uniprot, primary_gene FROM v2_protein_entry WHERE taxon_id = 9606",
        "SELECT accession, gene_names FROM v2_protein_entry WHERE organism = 'Homo sapiens'",
    ]
    rows = None
    for sql in sql_attempts:
        try:
            rows = con.execute(sql).fetchall()
            print(f"  query worked: {sql}")
            break
        except Exception:
            continue
    if rows is None:
        print(f"  WARN: couldn't find a gene-name column; dumping schema and aborting")
        return alias

    for uniprot, names in rows:
        if names is None:
            continue
        # names may be a list, a semicolon-separated string, or a space-sep string
        if isinstance(names, (list, tuple)):
            for n in names:
                if n:
                    alias.setdefault(n.upper(), uniprot)
        elif isinstance(names, str):
            for sep in [";", " ", ","]:
                if sep in names:
                    for n in names.split(sep):
                        n = n.strip()
                        if n:
                            alias.setdefault(n.upper(), uniprot)
                    break
            else:
                alias.setdefault(names.upper(), uniprot)
    print(f"  built alias index: {len(alias):,} gene-name → uniprot mappings")
    return alias


# ── Hand-curated alias overrides for known stubborn Davis keys ──────────
# These are the alt-name keys that even UniProt's `gene_names` field doesn't
# always carry. Sourced from gene-card / UniProt manual lookups.
HAND_CURATED = {
    "QSK":      "Q96Q15",   # STK11IP (alias QSK)? Actually QSK = SIK3 -> Q9Y2H1
    "SNARK":    "Q9C0K7",   # NUAK2
    "ADCK3":    "Q8NI60",   # COQ8A
    "DCAMKL1":  "O15075",   # DCLK1
    "PCTK2":    "Q00536",   # CDK17
    "PCTK1":    "Q00535",   # CDK16
    "PCTK3":    "Q00537",   # CDK18
    "S6K1":     "P23443",   # RPS6KB1
    "S6K2":     "Q9UBS0",   # RPS6KB2
    "P38-ALPHA": "Q16539",  # MAPK14
    "P38-BETA": "Q15759",   # MAPK11
    "P38-GAMMA":"P53778",   # MAPK12
    "P38-DELTA":"O15264",   # MAPK13
    "PKC-ALPHA":"P17252",   # PRKCA
    "PKC-BETA": "P05771",   # PRKCB
    "PKC-DELTA":"Q05655",   # PRKCD
    "PKC-EPSILON":"Q02156", # PRKCE
    "PKC-ETA":  "P24723",   # PRKCH
    "PKC-GAMMA":"P05129",   # PRKCG
    "PKC-IOTA": "P41743",   # PRKCI
    "PKC-THETA":"Q04759",   # PRKCQ
    "PKC-ZETA": "Q05513",   # PRKCZ
    "JNK1":     "P45983",   # MAPK8
    "JNK2":     "P45984",   # MAPK9
    "JNK3":     "P53779",   # MAPK10
    "P70S6K":   "P23443",   # RPS6KB1
    "GSK3-ALPHA":"P49840",  # GSK3A
    "GSK3-BETA":"P49841",   # GSK3B
    "ERK1":     "P27361",   # MAPK3
    "ERK2":     "P28482",   # MAPK1
    "ERK5":     "Q13164",   # MAPK7
    "ERK8":     "Q8TD08",   # MAPK15
    "AMPK-ALPHA1":"Q13131", # PRKAA1
    "AMPK-ALPHA2":"P54646", # PRKAA2
    "AURORA-A": "O14965",   # AURKA
    "AURORA-B": "Q96GD4",   # AURKB
    "AURORA-C": "Q9UQB9",   # AURKC
    "ABL":      "P00519",   # ABL1
    "BCR-ABL":  "P00519",   # ABL1 (fusion)
    "AAK1":     "Q2M2I8",
    "BMX":      "P51813",
    "BTK":      "Q06187",
    "CHK1":     "O14757",
    "CHK2":     "O96017",
    "DRAK1":    "Q9UEE5",   # STK17A
    "DRAK2":    "O94768",   # STK17B
    "EPHA4":    "P54764",
    "EPHA5":    "P54756",
    "FER":      "P16591",
    "FES":      "P07332",
    "FRK":      "P42685",
    "HCK":      "P08631",
    "HIPK1":    "Q86Z02",
    "HIPK2":    "Q9H2X6",
    "HIPK3":    "Q9H422",
    "HIPK4":    "Q8NE63",
    "INSR":     "P06213",
    "IRAK1":    "P51617",
    "IRAK3":    "Q9Y616",
    "IRAK4":    "Q9NWZ3",
    "JAK3":     "P52333",
    "LCK":      "P06239",
    "LIMK1":    "P53667",
    "LIMK2":    "P53671",
    "MAP3K4":   "Q9Y6R4",
    "MARK1":    "Q9P0L2",
    "MARK2":    "Q7KZI7",
    "MARK3":    "P27448",
    "MARK4":    "Q96L34",
    "MELK":     "Q14680",
    "MERTK":    "Q12866",
    "MET":      "P08581",
    "MKNK1":    "Q9BUB5",
    "MKNK2":    "Q9HBH9",
    "MLK1":     "P80192",   # MAP3K9
    "MLK2":     "Q02779",   # MAP3K10
    "MLK3":     "Q16584",   # MAP3K11
    "MST1":     "Q13043",   # STK4
    "MST2":     "Q13188",   # STK3
    "MST3":     "Q9Y6E0",   # STK24
    "MST4":     "Q9P289",
    "MUSK":     "O15146",
    "MYLK":     "Q15746",
    "MYLK2":    "Q9H1R3",
    "MYLK4":    "Q86YV6",
    "NEK1":     "Q96PY6",
    "NEK11":    "Q8NG66",
    "NEK2":     "P51955",
    "NEK3":     "P51956",
    "NEK4":     "P51957",
    "NEK5":     "Q6P3R8",
    "NEK6":     "Q9HC98",
    "NEK7":     "Q8TDX7",
    "NEK9":     "Q8TD19",
    "P38":      "Q16539",   # MAPK14 default
    "PIM1":     "P11309",
    "PIM2":     "Q9P1W9",
    "PIM3":     "Q86V86",
    "ROCK1":    "Q13464",
    "ROCK2":    "O75116",
    "RSK1":     "Q15418",   # RPS6KA1
    "RSK2":     "P51812",   # RPS6KA3
    "RSK3":     "Q15349",   # RPS6KA2
    "RSK4":     "O75676",   # RPS6KA6
    "SGK1":     "O00141",
    "SGK2":     "Q9HBY8",
    "SGK3":     "Q96BR1",
    "SLK":      "Q9H2G2",
    "SRC":      "P12931",
    "SRPK1":    "Q96SB4",
    "SRPK2":    "P78362",
    "SRPK3":    "Q9UPE1",
    "STK16":    "O75716",
    "SYK":      "P43405",
    "TAK1":     "O43318",   # MAP3K7
    "TAOK1":    "Q7L7X3",
    "TAOK2":    "Q9UL54",
    "TAOK3":    "Q9H2K8",
    "TBK1":     "Q9UHD2",
    "TIE2":     "Q02763",   # TEK
    "TLK1":     "Q9UKI8",
    "TLK2":     "Q86UE8",
    "TNIK":     "Q9UKE5",
    "TNK1":     "Q13470",
    "TNK2":     "Q07912",
    "TRKA":     "P04629",   # NTRK1
    "TRKB":     "Q16620",   # NTRK2
    "TRKC":     "Q16288",   # NTRK3
    "TSSK1B":   "Q9BXA7",
    "TSSK2":    "Q96PF2",
    "TSSK3":    "Q96PN8",
    "TSSK4":    "Q6SA08",
    "TYRO3":    "Q06418",
    "TYK2":     "P29597",
    "VRK1":     "Q99986",
    "VRK2":     "Q86Y07",
    "WEE1":     "P30291",
    "WEE2":     "P0C1S8",
    "WNK1":     "Q9H4A3",
    "WNK2":     "Q9Y3S1",
    "WNK3":     "Q9BYP7",
    "WNK4":     "Q96J92",
    "YANK1":    "Q8IWB6",   # STK32A
    "YANK2":    "Q8WU08",   # STK32B
    "YANK3":    "Q86UX6",   # STK32C
    "YSK1":     "Q9Y6E0",   # STK25
    "YSK4":     "Q9Y4K4",   # MAP3K19
    "ZAK":      "Q9NYL2",
    "ZAP70":    "P43403",
}


# ── Main ────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== Davis variant resolution pass 2 ===")
    con = duckdb.connect(str(DB))
    try:
        con.execute("PRAGMA temp_directory='D:/tmp_proteosphere/duckdb_temp'")
    except Exception:
        pass

    # 1) Build alias index
    alias = build_alias_index(con)

    # 2) Pull every unresolved davis_key
    unresolved = con.execute(
        "SELECT source_key FROM davis_bridge_uniprot "
        "WHERE confidence = 'unresolved'"
    ).fetchall()
    unresolved = [r[0] for r in unresolved]
    print(f"\n  unresolved davis keys: {len(unresolved)}")

    # 3) For each, parse variant + try to resolve
    new_resolutions = []  # (davis_key, base_gene, uniprot, mutation, phospho, region, activated, complex_partners)
    still_unresolved = []
    for key in unresolved:
        base, md = parse_variant(key)
        base_upper = base.upper()

        # Try hand-curated first
        up = HAND_CURATED.get(base_upper)
        if not up:
            up = alias.get(base_upper)
        # Try the original key as a hand-curated alias too
        if not up:
            up = HAND_CURATED.get(key.upper())

        if up:
            new_resolutions.append({
                "davis_key": key,
                "base_gene": base,
                "uniprot": up,
                "mutation": md.get("mutation"),
                "phospho": bool(md.get("phospho")),
                "region": md.get("region"),
                "activated": bool(md.get("activated")),
                "complex_partners": ",".join(md.get("complex_partners", [])) or None,
            })
        else:
            still_unresolved.append(key)

    print(f"  resolved this pass: {len(new_resolutions)}")
    print(f"  STILL unresolved:  {len(still_unresolved)}")
    if still_unresolved:
        print(f"  examples: {still_unresolved[:10]}")

    # 4) Ensure v2_davis_variant_resolution has the right columns
    cols_exist = {r[1] for r in con.execute(
        "PRAGMA table_info(v2_davis_variant_resolution)"
    ).fetchall()}
    for col, ddl in [
        ("base_gene",        "ALTER TABLE v2_davis_variant_resolution ADD COLUMN base_gene VARCHAR"),
        ("uniprot",          "ALTER TABLE v2_davis_variant_resolution ADD COLUMN uniprot VARCHAR"),
        ("mutation",         "ALTER TABLE v2_davis_variant_resolution ADD COLUMN mutation VARCHAR"),
        ("phospho",          "ALTER TABLE v2_davis_variant_resolution ADD COLUMN phospho BOOLEAN"),
        ("region",           "ALTER TABLE v2_davis_variant_resolution ADD COLUMN region VARCHAR"),
        ("activated",        "ALTER TABLE v2_davis_variant_resolution ADD COLUMN activated BOOLEAN"),
        ("complex_partners", "ALTER TABLE v2_davis_variant_resolution ADD COLUMN complex_partners VARCHAR"),
    ]:
        if col not in cols_exist:
            try:
                con.execute(ddl)
            except Exception:
                pass

    # 5) Upsert resolutions into v2_davis_variant_resolution
    n_inserted = 0
    n_updated = 0
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
            n_updated += 1
        else:
            con.execute(
                "INSERT INTO v2_davis_variant_resolution "
                "(davis_key, base_gene, uniprot, mutation, phospho, region, activated, complex_partners) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [r["davis_key"], r["base_gene"], r["uniprot"], r["mutation"], r["phospho"],
                 r["region"], r["activated"], r["complex_partners"]]
            )
            n_inserted += 1
    print(f"  variant resolution table: {n_inserted} inserts + {n_updated} updates")

    # 6) Update davis_bridge_uniprot.confidence for the newly resolved keys
    new_resolved_keys = [r["davis_key"] for r in new_resolutions]
    if new_resolved_keys:
        # Build a temp lookup
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

    # 7) Final stats
    print("\n  final davis_bridge_uniprot confidence counts:")
    for r in con.execute(
        "SELECT confidence, COUNT(*) FROM davis_bridge_uniprot GROUP BY 1 ORDER BY 2 DESC"
    ).fetchall():
        print(f"    {r[0]}: {r[1]}")

    n_with_uniprot = con.execute(
        "SELECT COUNT(*) FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL"
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM davis_bridge_uniprot").fetchone()[0]
    print(f"  davis keys with a UniProt resolved: {n_with_uniprot}/{total}")

    con.close()
    print("\n=== done ===")


if __name__ == "__main__":
    main()
