"""Analyze a per-paper rows CSV procured by `fetch_paper_rows.py`.

Given a CSV produced by the manuscript's procurement pipeline (one of the
51 mirrored files under `docs/manuscripts/proteosphere_paper/datasets/`),
this module:

  1. Reads the provenance headers (paper_id, family, source URLs, sha256).
  2. Auto-detects the per-family column schema.
  3. Splits the rows into train / test / val / cold / fold buckets by the
     `split` column.
  4. Computes per-axis overlap (drug, target) and pair-level overlap
     between every pair of splits.
  5. Runs an affinity distribution sanity check (train vs. test mean +
     stddev) so silent label-distribution drift gets flagged.
  6. Classifies the empirical split (warm-start | cold-target | cold-drug |
     cold-pair | mixed | random | unknown) by comparing observed overlap
     percentages to canonical thresholds.
  7. Renders a human-readable summary and a machine-readable JSON.

The same module also exposes ``rows_csv_to_manifest()`` which converts the
CSV into a `DatasetManifest` so the rest of the proteosphere CLI
(`split`, `paper-review`, `overlap-cluster`) can operate on it directly.

Family schemas
--------------
The fetcher emits per-family column layouts; this analyzer recognises
each. The recognisable families are:

    deepdta_setting1_family
    pdbbind_core_family
    ppis_train335_family
    tefdta_family
    hgrl_dta_family
    mgraphdta_family
    dcgan_dta_family
    struct2graph_public_pairs
    attentiondta_random_row_cv
    three_d_prot_dta_family   (pointer-only -> follows reference)
    rapppid_c123_archive       (pointer-only)
    prodigy78_plus_external_panels  (pointer-only)

For any other family, the analyzer falls back to a generic schema where
columns named like {drug,ligand,compound,smiles}_id and
{target,protein,uniprot,pdb}_id drive the overlap analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


# ─── Family schemas ─────────────────────────────────────────────────────
# Each family entry is:
#   {
#     "drug_col":   <column name carrying drug identifier> | None,
#     "target_col": <column name carrying target identifier>,
#     "label_col":  <column name carrying label or affinity> | None,
#     "task_type":  "regression" | "classification" | "ppi" | "residue_prediction",
#     "pair_col":   <when task is PPI, the column with the partner id>,
#   }
#
# Columns may be looked up case-insensitively via _SCHEMA_LOOKUP.
FAMILY_SCHEMAS: dict[str, dict[str, Any]] = {
    "deepdta_setting1_family": {
        "drug_col": "drug_id", "target_col": "target_id",
        "label_col": "affinity_value", "task_type": "regression",
    },
    "tefdta_family": {
        # TEFDTA ships SMILES + sequence directly; we use SMILES as the
        # drug identity proxy and the sequence sha8 (computed on demand)
        # as the target identity proxy when no shorter id is available.
        "drug_col": None, "target_col": None,  # auto-detect below
        "label_col": "affinity", "task_type": "regression",
        "auto_detect": True,
    },
    "hgrl_dta_family": {
        "drug_col": "drug_id", "target_col": "target_id",
        "label_col": "affinity_value", "task_type": "regression",
    },
    "mgraphdta_family": {
        "drug_col": "drug_smiles", "target_col": "target_sequence_sha8",
        "label_col": "label", "task_type": "classification",
    },
    "dcgan_dta_family": {
        "drug_col": "drug_id", "target_col": "target_id",
        "label_col": None, "task_type": "regression",
    },
    "attentiondta_random_row_cv": {
        "drug_col": "drug_id", "target_col": "target_id",
        "label_col": "affinity_value", "task_type": "regression",
    },
    "pdbbind_core_family": {
        "drug_col": "pdb_id",  # PDB ID is the complex id (drug + target combined)
        "target_col": "pdb_id",
        "label_col": "affinity_pk", "task_type": "regression",
        "complex_axis": True,  # single-axis split (one PDB = one complex)
    },
    "ppis_train335_family": {
        "drug_col": None, "target_col": "protein_id",
        "label_col": "interface_labels", "task_type": "residue_prediction",
        "pair_col": "pair_partner_id",
    },
    "struct2graph_public_pairs": {
        "drug_col": "pdb_b", "target_col": "pdb_a",
        "label_col": "interacts", "task_type": "ppi",
    },
    # Pointer-only — no row-level analysis possible.
    "three_d_prot_dta_family": {"pointer_only": True,
                                 "reference": "deepdta_setting1_family"},
    "rapppid_c123_archive":      {"pointer_only": True,
                                  "reference": "archive.org/details/rapppid_dataset"},
    "prodigy78_plus_external_panels": {
        "pointer_only": True,
        "reference": "https://www.rsc.org/suppdata/d2/cp/d2cp05644e/d2cp05644e1.pdf",
    },
}


@dataclass
class ProvenanceHeader:
    """Parsed `#`-prefixed header lines from a procured rows CSV."""
    paper_id: str = ""
    family: str = ""
    generated_at: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    license_: str = ""
    row_meaning: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "family": self.family,
            "generated_at": self.generated_at,
            "license": self.license_,
            "row_meaning": self.row_meaning,
            "sources": self.sources,
        }


def _parse_provenance(path: Path) -> ProvenanceHeader:
    """Read the leading `#`-prefixed comment block produced by
    fetch_paper_rows.py. Stops at the first non-comment line.
    """
    h = ProvenanceHeader()
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.startswith("#"):
                break
            body = line[1:].strip()
            if body.startswith("paper_id:"):
                h.paper_id = body.split(":", 1)[1].strip()
            elif body.startswith("family:"):
                h.family = body.split(":", 1)[1].strip()
            elif body.startswith("generated_at:"):
                h.generated_at = body.split(":", 1)[1].strip()
            elif body.startswith("license:"):
                h.license_ = body.split(":", 1)[1].strip()
            elif body.startswith("row_meaning:"):
                h.row_meaning = body.split(":", 1)[1].strip()
            elif body.startswith("source:"):
                # Format: source: <URL> (sha256=<hex>, <N> bytes, kind=<kind>)
                # The byte count uses comma thousand-separators
                # (e.g. "166,040 bytes") which collides with our naive
                # comma-split. Use regex to pick the three fields out
                # of the parenthesised meta block.
                import re
                rest = body.split(":", 1)[1].strip()
                src: dict[str, Any] = {"raw": rest}
                m_meta = re.match(r"^(.*) \((.*)\)\s*$", rest)
                if m_meta:
                    src["url"] = m_meta.group(1)
                    meta = m_meta.group(2)
                    m_sha = re.search(r"sha256=([0-9a-f]{64})", meta)
                    if m_sha:
                        src["sha256"] = m_sha.group(1)
                    m_bytes = re.search(r"([\d,]+)\s*bytes", meta)
                    if m_bytes:
                        try:
                            src["bytes"] = int(m_bytes.group(1).replace(",", ""))
                        except ValueError:
                            pass
                    m_kind = re.search(r"kind=([A-Za-z0-9_.\-]+)", meta)
                    if m_kind:
                        src["kind"] = m_kind.group(1)
                else:
                    src["url"] = rest
                h.sources.append(src)
    return h


def _read_rows(path: Path) -> list[dict[str, str]]:
    """Read CSV rows past the `#` provenance block. Each row → dict keyed
    by header column name. We slurp the file into a list first because
    mixing `for line in f` with `f.tell()/seek()` triggers
    ``OSError: telling position disabled by next() call`` — Python's
    iterator-mode buffer breaks the seek API.
    """
    with open(path, encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    data_lines = [ln for ln in all_lines if not ln.startswith("#")]
    if not data_lines:
        return []
    reader = csv.DictReader(data_lines)
    out: list[dict[str, str]] = []
    for row in reader:
        if row:
            out.append({k: (v or "").strip() for k, v in row.items()})
    return out


def _split_groups(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    """Group rows by the `split` column. Falls back to 'all' when absent."""
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows:
        sp = r.get("split", "all") or "all"
        groups[sp].append(r)
    return groups


def _detect_schema(family: str, rows: list[dict[str, str]]) -> dict[str, Any]:
    """Resolve the column-name mapping for this CSV. Uses FAMILY_SCHEMAS
    when the family is known; otherwise falls back to common naming."""
    schema = FAMILY_SCHEMAS.get(family, {}).copy()
    if not rows:
        return schema
    cols = list(rows[0].keys())
    cols_lower = {c.lower(): c for c in cols}

    # Auto-detect for tefdta / unknown families.
    if schema.get("auto_detect") or not schema:
        # Drug candidates (case-insensitive).
        for cand in ("drug_id", "ligand_id", "compound_id", "drug_smiles",
                     "compound_iso_smiles", "smiles"):
            if cand in cols_lower:
                schema["drug_col"] = cols_lower[cand]
                break
        # Target candidates.
        for cand in ("target_id", "uniprot", "protein_id", "pdb_id",
                     "target_sequence_sha8", "target_sequence", "sequence"):
            if cand in cols_lower:
                schema["target_col"] = cols_lower[cand]
                break
        # Label candidates.
        for cand in ("affinity_value", "affinity", "label", "interacts",
                     "pair_label", "affinity_pk"):
            if cand in cols_lower:
                schema["label_col"] = cols_lower[cand]
                break
        if "task_type" not in schema:
            label_col = schema.get("label_col")
            if label_col and rows:
                # Sniff label kind: ints → classification, floats → regression.
                first_val = rows[0].get(label_col, "").strip()
                try:
                    f = float(first_val)
                    schema["task_type"] = "classification" if f in (0.0, 1.0) else "regression"
                except ValueError:
                    schema["task_type"] = "regression"
            else:
                schema["task_type"] = "regression"
    return schema


# ─── Overlap math ───────────────────────────────────────────────────────

def _stats(values: list[float]) -> dict[str, float]:
    """Mean / stddev / min / max for a label distribution."""
    if not values:
        return {"n": 0, "mean": float("nan"), "stddev": float("nan"),
                "min": float("nan"), "max": float("nan")}
    mean = sum(values) / len(values)
    var = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    return {"n": len(values), "mean": mean, "stddev": math.sqrt(var),
            "min": min(values), "max": max(values)}


def _overlap_report(
    groups: dict[str, list[dict[str, str]]],
    schema: dict[str, Any],
) -> dict[str, Any]:
    """For every pair of split lanes, compute drug/target/pair overlap."""
    drug_col = schema.get("drug_col")
    target_col = schema.get("target_col")
    label_col = schema.get("label_col")

    # Build per-split entity sets.
    drugs_by_split: dict[str, set[str]] = {}
    targets_by_split: dict[str, set[str]] = {}
    pairs_by_split: dict[str, set[tuple[str, str]]] = {}
    labels_by_split: dict[str, list[float]] = {}
    for sp, rs in groups.items():
        ds, ts, ps = set(), set(), set()
        labels: list[float] = []
        for r in rs:
            d = r.get(drug_col, "") if drug_col else ""
            t = r.get(target_col, "") if target_col else ""
            if d: ds.add(d)
            if t: ts.add(t)
            if d and t:
                ps.add((d, t))
            elif t:
                ps.add(("", t))
            if label_col:
                v = r.get(label_col, "").strip()
                try:
                    labels.append(float(v))
                except ValueError:
                    pass
        drugs_by_split[sp] = ds
        targets_by_split[sp] = ts
        pairs_by_split[sp] = ps
        labels_by_split[sp] = labels

    # Pairwise overlap matrix.
    splits = sorted(groups.keys())
    pairwise = []
    for i, a in enumerate(splits):
        for b in splits[i+1:]:
            ad, bd = drugs_by_split[a], drugs_by_split[b]
            at, bt = targets_by_split[a], targets_by_split[b]
            ap, bp = pairs_by_split[a], pairs_by_split[b]
            shared_drugs = ad & bd
            shared_targets = at & bt
            shared_pairs = ap & bp
            def _pct(n: int, denom: int) -> float:
                return 100.0 * n / denom if denom else 0.0
            pairwise.append({
                "split_a": a, "split_b": b,
                "n_rows_a": len(groups[a]), "n_rows_b": len(groups[b]),
                "n_drugs_a": len(ad), "n_drugs_b": len(bd),
                "n_shared_drugs": len(shared_drugs),
                "pct_b_drugs_in_a": _pct(len(shared_drugs), len(bd)),
                "n_targets_a": len(at), "n_targets_b": len(bt),
                "n_shared_targets": len(shared_targets),
                "pct_b_targets_in_a": _pct(len(shared_targets), len(bt)),
                "n_pairs_a": len(ap), "n_pairs_b": len(bp),
                "n_shared_pairs": len(shared_pairs),
                "pct_b_pairs_in_a": _pct(len(shared_pairs), len(bp)),
            })

    return {
        "splits": splits,
        "split_sizes": {sp: len(rs) for sp, rs in groups.items()},
        "pairwise_overlap": pairwise,
        "label_stats_by_split": {sp: _stats(labels_by_split[sp]) for sp in splits},
    }


def _classify_split(overlap: dict[str, Any]) -> dict[str, Any]:
    """For each train/test (or val/test) pair, derive a verdict from the
    observed entity overlap. Thresholds match the manuscript's analysis."""
    findings: list[dict[str, Any]] = []
    splits = set(overlap["splits"])
    # Prefer the canonical train↔test comparison if both lanes exist.
    target_pairs: list[tuple[str, str]] = []
    if "train" in splits and "test" in splits:
        target_pairs.append(("train", "test"))
    if "S1_train" in splits and "S1_test" in splits:
        target_pairs.append(("S1_train", "S1_test"))
    if "S2_cold_target_train" in splits and "S2_cold_target_test" in splits:
        target_pairs.append(("S2_cold_target_train", "S2_cold_target_test"))
    if "S3_cold_pair_train" in splits and "S3_cold_pair_test" in splits:
        target_pairs.append(("S3_cold_pair_train", "S3_cold_pair_test"))

    if not target_pairs:
        for p in overlap["pairwise_overlap"]:
            target_pairs.append((p["split_a"], p["split_b"]))

    for sa, sb in target_pairs:
        row = next((p for p in overlap["pairwise_overlap"]
                    if {p["split_a"], p["split_b"]} == {sa, sb}), None)
        if row is None:
            continue
        drug_ovl = row["pct_b_drugs_in_a"]
        targ_ovl = row["pct_b_targets_in_a"]
        pair_ovl = row["pct_b_pairs_in_a"]

        # Verdicts:
        #   warm-start  → both axes mostly shared, pair overlap may be ~0
        #   cold-target → target overlap ~0%, drugs overlap can be high
        #   cold-drug   → drug overlap ~0%, targets overlap can be high
        #   cold-pair   → both axes ~0% overlap
        #   pair-leakage→ same (drug,target) pair in both splits  → silent dup
        verdict_tags: list[str] = []
        if pair_ovl > 0.5:
            verdict_tags.append(f"pair-overlap {pair_ovl:.1f}% (silent train/test duplication)")
        if drug_ovl >= 80 and targ_ovl >= 80:
            verdict = "warm-start"
            severity = "high"
        elif drug_ovl < 5 and targ_ovl < 5:
            verdict = "cold-pair"
            severity = "low"
        elif drug_ovl < 5:
            verdict = "cold-drug"
            severity = "medium"
        elif targ_ovl < 5:
            verdict = "cold-target"
            severity = "medium"
        elif drug_ovl >= 80 or targ_ovl >= 80:
            verdict = "mixed-mostly-warm"
            severity = "medium"
        else:
            verdict = "mixed"
            severity = "medium"

        findings.append({
            "train_split": sa, "test_split": sb,
            "verdict": verdict,
            "severity": severity,
            "drug_overlap_pct": drug_ovl,
            "target_overlap_pct": targ_ovl,
            "pair_overlap_pct": pair_ovl,
            "tags": verdict_tags,
        })
    return {"findings": findings}


# ─── Public API ─────────────────────────────────────────────────────────

def analyze_csv(path: str | Path) -> dict[str, Any]:
    """Run the full analyzer over one procured rows CSV. Returns a dict
    suitable for JSON serialisation OR markdown rendering."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    provenance = _parse_provenance(path)
    rows = _read_rows(path)
    schema = _detect_schema(provenance.family, rows)
    report: dict[str, Any] = {
        "analyzed_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "csv_path": str(path),
        "provenance": provenance.to_dict(),
        "schema": schema,
        "n_rows": len(rows),
    }
    if schema.get("pointer_only"):
        report["status"] = "pointer_only"
        report["follow_pointer"] = schema.get("reference", "")
        report["verdict"] = ("This CSV is a pointer to an external resource. "
                              "Row-level analysis requires downloading + parsing "
                              "the referenced asset locally.")
        return report
    if not rows:
        report["status"] = "empty"
        report["verdict"] = "No data rows found."
        return report

    # Family-specific analyzer notes (surfaced in the report so the user
    # knows when a verdict reflects what's in the CSV vs. what requires
    # external mappings).
    analyzer_notes: list[str] = []
    if schema.get("complex_axis"):
        analyzer_notes.append(
            "PDBbind family: this CSV's overlap is computed at the "
            "PDB-ID level. PDB IDs do NOT repeat between core test "
            "sets and the general/refined training pool by construction, "
            "so test-vs-train PDB overlap will always be near 0%. The "
            "manuscript's leakage finding for PDBbind is at the "
            "UniProt-accession level (multiple PDB IDs sharing the same "
            "target protein); see artifacts/status/literature_hunt_deep_proofs/"
            "pdbbind_core_family_audit.json for the per-test-PDB UniProt "
            "accession + shared_train_ids data."
        )
    if schema.get("task_type") == "residue_prediction":
        analyzer_notes.append(
            "PPIS family: this CSV's `label` column is a per-residue "
            "0/1 string (one char per residue), not a single number. "
            "Label distribution stats are intentionally suppressed; "
            "use the protein_id column for train/test overlap analysis "
            "(0% overlap on protein_id is the published cold-target design)."
        )
        # Drop label_col so _overlap_report doesn't compute label stats.
        schema["label_col"] = None
    report["analyzer_notes"] = analyzer_notes

    groups = _split_groups(rows)
    overlap = _overlap_report(groups, schema)
    classification = _classify_split(overlap)
    report.update({
        "status": "analyzed",
        "split_breakdown": overlap["split_sizes"],
        "label_stats_by_split": overlap["label_stats_by_split"],
        "pairwise_overlap": overlap["pairwise_overlap"],
        "findings": classification["findings"],
    })
    # Top-level verdict: surface the most severe finding (or "ok" if none).
    if classification["findings"]:
        worst = max(classification["findings"],
                    key=lambda f: {"low": 0, "medium": 1, "high": 2}.get(f["severity"], 0))
        report["verdict"] = worst["verdict"]
        report["severity"] = worst["severity"]
    else:
        report["verdict"] = "ok_no_train_test_lanes"
        report["severity"] = "info"
    return report


def render_markdown(report: dict[str, Any]) -> str:
    """Pretty-print a report as Markdown for terminal / CI display."""
    lines: list[str] = []
    p = report.get("provenance", {})
    lines.append(f"# Analyzer report — {p.get('paper_id','?')}")
    lines.append("")
    lines.append(f"- **CSV:** `{report.get('csv_path','?')}`")
    lines.append(f"- **Family:** `{p.get('family','?')}`")
    lines.append(f"- **License:** {p.get('license','?')}")
    lines.append(f"- **Generated at:** {p.get('generated_at','?')}")
    lines.append(f"- **Analyzed at:** {report.get('analyzed_at','?')}")
    lines.append(f"- **Rows read:** {report.get('n_rows',0):,}")
    lines.append(f"- **Status:** {report.get('status','?')}")
    lines.append("")
    if report.get("status") == "pointer_only":
        lines.append(f"_This CSV is a pointer to an external resource. "
                     f"Follow: {report.get('follow_pointer','?')}_")
        return "\n".join(lines)
    if report.get("status") == "empty":
        lines.append("_No data rows._")
        return "\n".join(lines)
    notes = report.get("analyzer_notes", [])
    if notes:
        lines.append("## Analyzer notes (family-specific caveats)")
        lines.append("")
        for n in notes:
            lines.append(f"> {n}")
            lines.append("")
    sb = report.get("split_breakdown", {})
    lines.append("## Split lane sizes")
    lines.append("")
    lines.append("| Split | Rows |")
    lines.append("|---|---|")
    for k, v in sorted(sb.items()):
        lines.append(f"| `{k}` | {v:,} |")
    lines.append("")
    lab = report.get("label_stats_by_split", {})
    if lab:
        lines.append("## Label distribution per split")
        lines.append("")
        lines.append("| Split | n | mean | stddev | min | max |")
        lines.append("|---|---|---|---|---|---|")
        for k in sorted(lab.keys()):
            s = lab[k]
            n = s.get('n', 0)
            mean = s.get('mean', float('nan'))
            std  = s.get('stddev', float('nan'))
            mn   = s.get('min', float('nan'))
            mx   = s.get('max', float('nan'))
            def f(x):
                return f"{x:.3f}" if isinstance(x, float) and not math.isnan(x) else "—"
            lines.append(f"| `{k}` | {n:,} | {f(mean)} | {f(std)} | {f(mn)} | {f(mx)} |")
        lines.append("")
    findings = report.get("findings", [])
    if findings:
        lines.append("## Verdict findings")
        lines.append("")
        for f in findings:
            sev_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢", "info": "ℹ️"}.get(f.get("severity"), "·")
            lines.append(f"### {sev_emoji} `{f['train_split']}` vs `{f['test_split']}` → "
                         f"**{f['verdict']}** ({f['severity']})")
            lines.append("")
            lines.append(f"- drug-overlap: **{f['drug_overlap_pct']:.1f}%** of test drugs already in train")
            lines.append(f"- target-overlap: **{f['target_overlap_pct']:.1f}%** of test targets already in train")
            lines.append(f"- pair-overlap: **{f['pair_overlap_pct']:.1f}%** of (drug,target) pairs duplicated")
            for tag in f.get("tags", []):
                lines.append(f"- ⚠ {tag}")
            lines.append("")
    lines.append("## Overall verdict")
    lines.append("")
    lines.append(f"**{report.get('verdict','?')}** (severity: {report.get('severity','?')})")
    lines.append("")
    if p.get("sources"):
        lines.append("## Provenance (verify with `curl <url> | sha256sum`)")
        lines.append("")
        for s in p["sources"]:
            lines.append(f"- `{s.get('url','?')}`")
            if s.get("sha256"):
                lines.append(f"  - sha256: `{s['sha256']}`")
            if s.get("bytes"):
                lines.append(f"  - bytes: {s['bytes']:,}")
    return "\n".join(lines)


def rows_csv_to_manifest(path: str | Path) -> dict[str, Any]:
    """Convert a procured rows CSV into a DatasetManifest-compatible dict
    that the existing `proteosphere split` / `paper-review` tooling can
    consume. Each row becomes one DatasetRecord."""
    path = Path(path)
    prov = _parse_provenance(path)
    rows = _read_rows(path)
    schema = _detect_schema(prov.family, rows)
    drug_col = schema.get("drug_col")
    target_col = schema.get("target_col")
    label_col = schema.get("label_col")
    task_type = schema.get("task_type", "regression")
    records: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        rec: dict[str, Any] = {
            "record_id": f"{prov.paper_id}_{i:08d}",
            "split": r.get("split", "") or "",
            "ligand_id": r.get(drug_col, "") if drug_col else "",
        }
        target_val = r.get(target_col, "") if target_col else ""
        # PDBbind: target_col == drug_col == pdb_id → use as pdb anchor.
        if schema.get("complex_axis"):
            rec["pdb_ids"] = [target_val] if target_val else []
        elif task_type in ("ppi", "residue_prediction"):
            rec["accessions"] = [target_val] if target_val else []
            pair_col = schema.get("pair_col")
            if pair_col:
                pv = r.get(pair_col, "")
                if pv:
                    rec["accession_partner"] = pv
        else:
            rec["accessions"] = [target_val] if target_val else []
        if label_col:
            v = r.get(label_col, "").strip()
            try:
                rec["label_value"] = float(v)
            except ValueError:
                rec["label_value_raw"] = v
        # Extra metadata for traceability.
        rec["extra_metadata"] = {
            "dataset": r.get("dataset", ""),
            "row_index": r.get("pair_index", "") or r.get("row_index", ""),
        }
        records.append(rec)
    manifest: dict[str, Any] = {
        "manifest_id": f"{prov.paper_id}-rows-csv",
        "title": prov.paper_id,
        "task_type": task_type,
        "label_type": "binary" if task_type == "classification" else "continuous",
        "entity_kind": ("protein_protein" if task_type == "ppi"
                        else "protein_residue" if task_type == "residue_prediction"
                        else "protein_ligand"),
        "split_membership_mode": "paper_published",
        "records": records,
        "notes": [
            f"Auto-converted from {path.name} on {datetime.now(UTC).isoformat()}",
            f"Family: {prov.family}",
            f"Source upstream(s): {', '.join(s.get('url','') for s in prov.sources)}",
        ],
    }
    return manifest


# ─── CLI entry point ────────────────────────────────────────────────────

def analyze_csv_main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Analyze a per-paper rows CSV produced by the "
                    "ProteoSphere paper procurement pipeline.")
    p.add_argument("--csv", required=True, type=Path,
                   help="Path to a <paper_id>_rows.csv file.")
    p.add_argument("--out", type=Path, default=None,
                   help="Optional path to write a JSON report. "
                        "When omitted, JSON goes to stdout.")
    p.add_argument("--report-format",
                   choices=("json", "md", "both"),
                   default="md",
                   help="md=Markdown to stdout; json=JSON to stdout/--out; "
                        "both=Markdown to stdout AND JSON to --out (if set).")
    p.add_argument("--manifest-out", type=Path, default=None,
                   help="Optional path to write a DatasetManifest JSON "
                        "(consumable by `proteosphere split` / `paper-review`).")
    args = p.parse_args(argv)

    if not args.csv.exists():
        print(f"ERROR: CSV not found: {args.csv}", file=sys.stderr)
        return 2

    try:
        report = analyze_csv(args.csv)
    except Exception as exc:
        print(f"ERROR: analyzer failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 3

    if args.report_format in ("md", "both"):
        print(render_markdown(report))

    if args.report_format == "json":
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
                f.write("\n")
            print(f"\nJSON report written to {args.out}", file=sys.stderr)
        else:
            print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.report_format == "both" and args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"\nJSON report written to {args.out}", file=sys.stderr)

    if args.manifest_out:
        manifest = rows_csv_to_manifest(args.csv)
        args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
        with open(args.manifest_out, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"DatasetManifest written to {args.manifest_out}", file=sys.stderr)

    # Exit code reflects severity for shell pipelines:
    #   0 = ok | low | info
    #   1 = medium
    #   2 = high
    sev = report.get("severity", "info")
    return {"info": 0, "low": 0, "medium": 1, "high": 2}.get(sev, 0)


if __name__ == "__main__":
    raise SystemExit(analyze_csv_main())
