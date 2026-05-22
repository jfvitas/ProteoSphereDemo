"""Resolve Davis protein-name keys (mutation/phospho variants) to UniProt.

Davis keys look like:
  ABL1                  -> base gene, wildtype
  ABL1(E255K)           -> mutation
  ABL1(F317I)p          -> mutation + phospho variant
  ABL1(F317L)           -> mutation
  CDK4-cyclinD1         -> kinase + cyclin complex (use base gene CDK4)
  EGFR(L858R,T790M)     -> double mutant
  EPHB1                 -> wildtype
  p38-beta              -> alpha for ambiguous greek
  PKC-alpha             -> use base PRKCA via alias

Strategy:
  1. Regex extract base gene = first all-caps run of letters/digits
     before any '(' or '-'.
  2. Mutations between parentheses split on ','.
  3. Trailing 'p' = phospho variant flag.
  4. Resolve base gene to UniProt via davis_bridge_uniprot already-exact rows,
     then via v2_protein_entry (after Swiss-Prot ingest) by gene-name token.
  5. Materialize v2_davis_variant_resolution.
  6. Update davis_bridge_uniprot to ADD wt_fallback rows for every variant
     that resolves via the base gene's wildtype UniProt.

We're careful not to overwrite existing 'exact' rows; we add new rows with
confidence='wt_fallback'.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
WAREHOUSE = HERE / "demo_warehouse" / "catalog" / "v2.duckdb"
SNAPSHOT_ID = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

VARIANT_RE = re.compile(
    r"^"
    r"(?P<gene>[A-Za-z0-9]+)"
    r"(?:[-_/]?(?P<suffix>[A-Za-z0-9-]+))?"  # complex partner, e.g. CDK4-cyclinD1
    r"(?:\((?P<muts>[^)]+)\))?"  # mutations in parens
    r"(?P<phospho>p)?$"  # trailing phospho flag
)
MUT_TOKEN_RE = re.compile(r"^[A-Z]\d+[A-Z]$")  # e.g. E255K


def parse_davis_key(key: str) -> dict:
    """Return {gene, suffix, mutations, phospho}."""
    s = key.strip()
    # Special tokens: greek aliases
    aliases = {
        "p38-alpha": "MAPK14",
        "p38-beta": "MAPK11",
        "p38-gamma": "MAPK12",
        "p38-delta": "MAPK13",
        "JNK1": "MAPK8",
        "JNK2": "MAPK9",
        "JNK3": "MAPK10",
        "ERK1": "MAPK3",
        "ERK2": "MAPK1",
        "PKC-alpha": "PRKCA",
        "PKC-beta1": "PRKCB",
        "PKC-beta2": "PRKCB",
        "PKC-gamma": "PRKCG",
        "PKC-delta": "PRKCD",
        "PKC-epsilon": "PRKCE",
        "PKC-theta": "PRKCQ",
        "PKC-eta": "PRKCH",
        "PKC-iota": "PRKCI",
        "PKC-zeta": "PRKCZ",
        "S6K1": "RPS6KB1",
        "S6K2": "RPS6KB2",
        "Tyk2": "TYK2",
    }
    canonical = aliases.get(s)
    if canonical:
        return {"gene": canonical, "suffix": "", "mutations": [], "phospho": False, "raw": s}

    m = VARIANT_RE.match(s)
    if not m:
        # try simple split on first paren/dash
        m2 = re.match(r"^([A-Za-z0-9]+)", s)
        if m2:
            return {"gene": m2.group(1).upper(), "suffix": "", "mutations": [], "phospho": s.endswith("p"), "raw": s}
        return {"gene": s.upper(), "suffix": "", "mutations": [], "phospho": False, "raw": s}

    gene = m.group("gene").upper()
    suffix = (m.group("suffix") or "").lower()
    muts_text = m.group("muts") or ""
    mutations = []
    if muts_text:
        for tok in re.split(r"[,;]\s*", muts_text):
            tok = tok.strip()
            if MUT_TOKEN_RE.match(tok):
                mutations.append(tok)
    return {"gene": gene, "suffix": suffix, "mutations": mutations,
            "phospho": bool(m.group("phospho")), "raw": s}


def main() -> None:
    if not WAREHOUSE.exists():
        raise SystemExit(f"warehouse missing: {WAREHOUSE}")
    con = duckdb.connect(str(WAREHOUSE))

    # Pull all davis protein keys
    rows = con.execute("SELECT DISTINCT protein_key FROM davis_proteins").fetchall()
    print(f"[davis] {len(rows):,} davis protein keys")

    # Pull existing exact bridges (key -> uniprot)
    exact_bridge = {r[0]: r[1] for r in con.execute(
        "SELECT source_key, uniprot FROM davis_bridge_uniprot WHERE confidence='exact'"
    ).fetchall()}
    print(f"[davis] {len(exact_bridge):,} existing exact bridges")

    # Build gene -> UniProt lookup from existing exact bridges (gene name = source_key
    # parsed via regex; davis source_key is the same as protein_key, sometimes just gene)
    # Better: build from v2_protein_entry if available, else from existing exact rows.
    gene_to_uniprot: dict[str, str] = {}
    # Try v2_protein_entry first (added by Swiss-Prot ingest)
    try:
        for r in con.execute("""
            SELECT entry_name, uniprot, taxon_id
              FROM v2_protein_entry
             WHERE taxon_id = 9606
        """).fetchall():
            entry_name = r[0]
            uniprot = r[1]
            # entry_name like 'ABL1_HUMAN' or 'CDK4_HUMAN'
            if entry_name and "_HUMAN" in entry_name:
                gene = entry_name.split("_HUMAN")[0]
                gene_to_uniprot.setdefault(gene.upper(), uniprot)
    except Exception:
        pass

    # Also lift from existing davis exact bridges
    for k, u in exact_bridge.items():
        info = parse_davis_key(k)
        if not info["mutations"] and not info["suffix"]:
            gene_to_uniprot.setdefault(info["gene"], u)

    print(f"[davis] gene_to_uniprot lookup: {len(gene_to_uniprot):,} entries")

    # Resolve each davis key
    resolved = []
    by_status = {"exact_via_bridge": 0, "wt_fallback": 0, "unresolved": 0}
    for (key,) in rows:
        info = parse_davis_key(key)
        # 1. exact bridge hit
        u = exact_bridge.get(key)
        if u:
            resolved.append((key, info["gene"], u, ",".join(info["mutations"]),
                             info["phospho"], "exact"))
            by_status["exact_via_bridge"] += 1
            continue
        # 2. fall back to base gene wildtype UniProt
        u = gene_to_uniprot.get(info["gene"])
        if u:
            resolved.append((key, info["gene"], u, ",".join(info["mutations"]),
                             info["phospho"], "wt_fallback"))
            by_status["wt_fallback"] += 1
            continue
        resolved.append((key, info["gene"], None, ",".join(info["mutations"]),
                         info["phospho"], "unresolved"))
        by_status["unresolved"] += 1

    print(f"[davis] resolution: {by_status}")

    # Materialize v2_davis_variant_resolution
    con.execute("DROP TABLE IF EXISTS v2_davis_variant_resolution")
    con.execute("""
        CREATE TABLE v2_davis_variant_resolution (
            davis_key   VARCHAR,
            base_gene   VARCHAR,
            uniprot     VARCHAR,
            mutations   VARCHAR,
            phospho     BOOLEAN,
            confidence  VARCHAR,
            snapshot_id VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO v2_davis_variant_resolution VALUES (?,?,?,?,?,?,?)",
        [(k, g, u, m, p, c, SNAPSHOT_ID) for (k, g, u, m, p, c) in resolved],
    )

    # Extend davis_bridge_uniprot with wt_fallback rows for variants
    # Skip rows that already exist with same key + uniprot
    existing_keys = set(con.execute(
        "SELECT source_key || '|' || uniprot FROM davis_bridge_uniprot"
    ).fetchall())
    existing_keys = {r[0] for r in existing_keys}

    new_bridge_rows = []
    for (key, gene, u, m, p, conf) in resolved:
        if conf != "wt_fallback" or not u:
            continue
        ek = f"{key}|{u}"
        if ek in existing_keys:
            continue
        new_bridge_rows.append(("davis", key, "VariantResolution+wt_fallback",
                                u, "wt_fallback", SNAPSHOT_ID))

    if new_bridge_rows:
        con.executemany(
            "INSERT INTO davis_bridge_uniprot VALUES (?,?,?,?,?,?)",
            new_bridge_rows,
        )
        print(f"[davis] added {len(new_bridge_rows):,} wt_fallback rows to davis_bridge_uniprot")

    # Final stats
    final_exact = con.execute(
        "SELECT COUNT(DISTINCT source_key) FROM davis_bridge_uniprot WHERE confidence='exact'"
    ).fetchone()[0]
    final_any = con.execute(
        "SELECT COUNT(DISTINCT source_key) FROM davis_bridge_uniprot WHERE uniprot IS NOT NULL"
    ).fetchone()[0]
    print(f"[davis] davis_bridge_uniprot: {final_exact:,} exact keys / {final_any:,} keys with any UniProt")

    # Sample verifications
    samples = ['ABL1', 'ABL1(F317I)p', 'p38-alpha', 'PKC-alpha', 'CDK4-cyclinD1', 'EGFR(L858R,T790M)']
    print(f"[davis] sample resolutions:")
    for s in samples:
        rec = con.execute(
            "SELECT davis_key, base_gene, uniprot, mutations, phospho, confidence FROM v2_davis_variant_resolution WHERE davis_key=?",
            [s]
        ).fetchone()
        if rec:
            print(f"           {s} -> base={rec[1]} uniprot={rec[2]} muts={rec[3]} phos={rec[4]} conf={rec[5]}")
        else:
            print(f"           {s} -> NOT in davis_proteins")

    con.close()


if __name__ == "__main__":
    main()
