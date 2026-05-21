"""GtoPdb (IUPHAR/BPS Guide to Pharmacology) parser.

Reads the 3 TSV files the downloader pulled and emits 3 parquet fragments:

    interactions.parquet
        edge_id, protein_ref, ligand_ref, target_id, ligand_id,
        affinity_value (numeric pK*), affinity_kind (pKi|pKd|pIC50|pEC50|...),
        affinity_unit (pK or M), species, primary_target, action, action_type,
        endogenous, approved, original_affinity_relation, pubmed_id,
        snapshot_id, source

    ligands.parquet
        ligand_id, ligand_ref, name, smiles, inchi, inchikey,
        chembl_id, pubchem_cid, uniprot_id (for biologics), type,
        approved, withdrawn, snapshot_id, source

    targets.parquet
        target_id, target_ref, target_name, target_type,
        family_id, family_name, human_uniprot, hgnc_symbol,
        species_uniprots (jsonish: {human, rat, mouse}),
        snapshot_id, source

ref schemes:
    target_ref  = "protein:<UniProt>"  (when Human SwissProt present)
                  else "gtopdb:<target_id>"
    ligand_ref  = "ligand:gtopdb:<ligand_id>"
    edge_id     = f"protein_ligand:gtopdb:{target_id}:{ligand_id}:{aff_kind}"

Output goes under
    <ingest_root>/normalized/ligand_assay/gtopdb/<snapshot_id>/

Parquet writer uses pyarrow when available, else falls back to plain JSONL
(so the parser still runs on a thin install). The catalog consolidation
step prefers parquet but accepts JSONL.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..state import SourceState, INGEST_ROOT
from . import register_parser, ParseResult

# Optional pyarrow path
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    _HAS_ARROW = True
except Exception:
    _HAS_ARROW = False


SOURCE_ID = "gtopdb"
SOURCE_LABEL = "IUPHAR/BPS Guide to Pharmacology"
_SUB_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """GtoPdb embeds <sub>/<sup> tags in target names. Strip for ref keys."""
    return _SUB_TAG.sub("", s or "")


def _parse_float(v: str) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _read_tsv(path: Path):
    """GtoPdb TSVs prepend a `# GtoPdb Version: …` comment line before the
    header row. csv.DictReader can't skip comments, so we do it manually.
    Lines also use Windows CRLF on some downloads — universal newlines."""
    with open(path, encoding="utf-8", newline="") as f:
        first = f.readline()
        if not first.startswith('"#') and not first.startswith("#"):
            f.seek(0)
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            yield row


def parse_interactions(path: Path, snapshot_id: str) -> list[dict]:
    """One row per (target × ligand × affinity) triple."""
    out: list[dict] = []
    for row in _read_tsv(path):
        target_id = (row.get("Target ID") or "").strip()
        ligand_id = (row.get("Ligand ID") or "").strip()
        if not target_id or not ligand_id:
            continue
        uniprot = (row.get("Target UniProt ID") or "").strip()
        # Some rows have ' | '-separated uniprots for complexes; pick first
        if "|" in uniprot:
            uniprot = uniprot.split("|", 1)[0].strip()
        target_ref = f"protein:{uniprot}" if uniprot else f"gtopdb:{target_id}"
        ligand_ref = f"ligand:gtopdb:{ligand_id}"

        aff_units = (row.get("Affinity Units") or "").strip()
        aff_value = _parse_float(row.get("Affinity Median"))
        aff_high  = _parse_float(row.get("Affinity High"))
        aff_low   = _parse_float(row.get("Affinity Low"))
        aff_relation = (row.get("Original Affinity Relation") or "").strip() or "="
        # Normalise pK* values; convert raw nM into pK if needed (some rows have units in nm originally)
        kind = aff_units.lower() if aff_units else None  # "pki" | "pkd" | "pic50" | "pec50" | "pkb" | "pa2" | ""
        if aff_value is None:
            # Try the nM fields if pK is missing
            raw_med = _parse_float(row.get("Original Affinity Median nm"))
            if raw_med is not None and raw_med > 0:
                import math
                aff_value = 9.0 - math.log10(raw_med)
                kind = "pkd_from_kd_nm" if (row.get("Original Affinity Units") or "").lower() == "kd" else (
                       "pki_from_ki_nm" if (row.get("Original Affinity Units") or "").lower() == "ki" else
                       "pic50_from_ic50_nm")
        edge_id = f"protein_ligand:{SOURCE_ID}:{target_id}:{ligand_id}:{kind or 'noaff'}"
        out.append({
            "edge_id":      edge_id,
            "protein_ref":  target_ref,
            "ligand_ref":   ligand_ref,
            "target_id":    target_id,
            "ligand_id":    ligand_id,
            "uniprot":      uniprot or None,
            "affinity_value": aff_value,
            "affinity_kind":  kind,
            "affinity_high":  aff_high,
            "affinity_low":   aff_low,
            "affinity_relation": aff_relation,
            "original_units": (row.get("Original Affinity Units") or None),
            "species":      (row.get("Target Species") or None),
            "ligand_species": (row.get("Ligand Species") or None),
            "primary_target": (row.get("Primary Target") or "").lower() == "true",
            "endogenous":   (row.get("Endogenous") or "").lower() == "true",
            "approved":     (row.get("Approved") or "").lower() == "true",
            "ligand_type":  (row.get("Ligand Type") or None),
            "action":       (row.get("Action") or None),
            "action_type":  (row.get("Type") or None),  # Inhibitor/Agonist/etc.
            "selectivity":  (row.get("Selectivity") or None),
            "assay_description": (row.get("Assay Description") or None),
            "receptor_site": (row.get("Receptor Site") or None),
            "pubmed_id":    (row.get("PubMed ID") or None),
            "snapshot_id":  snapshot_id,
            "source":       SOURCE_ID,
        })
    return out


def parse_ligands(path: Path, snapshot_id: str) -> list[dict]:
    out: list[dict] = []
    for row in _read_tsv(path):
        lid = (row.get("Ligand ID") or "").strip()
        if not lid:
            continue
        out.append({
            "ligand_id":   lid,
            "ligand_ref":  f"ligand:{SOURCE_ID}:{lid}",
            "name":        (row.get("Name") or None),
            "type":        (row.get("Type") or None),
            "approved":    (row.get("Approved") or "").lower() == "true",
            "withdrawn":   (row.get("Withdrawn") or "").lower() == "true",
            "labelled":    (row.get("Labelled") or "").lower() == "true",
            "radioactive": (row.get("Radioactive") or "").lower() == "true",
            "smiles":      (row.get("SMILES") or None),
            "inchi":       (row.get("InChI") or None),
            "inchikey":    (row.get("InChIKey") or None),
            "iupac_name":  (row.get("IUPAC name") or None),
            "inn":         (row.get("INN") or None),
            "synonyms":    (row.get("Synonyms") or None),
            "pubchem_sid": (row.get("PubChem SID") or None),
            "pubchem_cid": (row.get("PubChem CID") or None),
            "chembl_id":   (row.get("ChEMBL ID") or None),
            "uniprot_id":  (row.get("UniProt ID") or None),    # for biologic ligands
            "antibacterial": (row.get("Antibacterial") or "").lower() == "true",
            "snapshot_id": snapshot_id,
            "source":      SOURCE_ID,
        })
    return out


def parse_targets(path: Path, snapshot_id: str) -> list[dict]:
    out: list[dict] = []
    for row in _read_tsv(path):
        tid = (row.get("Target id") or "").strip()
        if not tid:
            continue
        human_up = (row.get("Human SwissProt") or "").strip() or None
        out.append({
            "target_id":     tid,
            "target_ref":    f"protein:{human_up}" if human_up else f"gtopdb:{tid}",
            "target_name":   _strip_html(row.get("Target name") or ""),
            "target_type":   (row.get("Type") or None),
            "family_id":     (row.get("Family id") or None),
            "family_name":   (row.get("Family name") or None),
            "hgnc_symbol":   (row.get("HGNC symbol") or None),
            "hgnc_name":     (row.get("HGNC name") or None),
            "human_uniprot": human_up,
            "rat_uniprot":   (row.get("Rat SwissProt") or None),
            "mouse_uniprot": (row.get("Mouse SwissProt") or None),
            "human_genetic_locus": (row.get("Human genetic localisation") or None),
            "snapshot_id":   snapshot_id,
            "source":        SOURCE_ID,
        })
    return out


# ── Writers ──────────────────────────────────────────────────────────

def _write_parquet(rows: list[dict], path: Path) -> Path:
    """Write parquet if pyarrow is available, else JSONL with a `.jsonl`
    suffix. Both formats are accepted by the catalog consolidation step.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # Empty fragment — still emit a 0-row file so the catalog knows we ran
        path.write_bytes(b"")
        return path
    if _HAS_ARROW:
        # Normalise to a column-oriented table — pyarrow needs every column
        # to have a uniform type across rows. Mixed-type columns become
        # string-or-null.
        keys = list(rows[0].keys())
        cols: dict[str, list] = {k: [] for k in keys}
        for r in rows:
            for k in keys:
                cols[k].append(r.get(k))
        table = pa.table(cols)
        pq.write_table(table, path, compression="zstd")
        return path
    else:
        jsonl = path.with_suffix(".jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return jsonl


# ── Entry point ──────────────────────────────────────────────────────

def _parse_impl(state: SourceState, *, snapshot_dir: Path | None = None) -> ParseResult:
    src_dir = Path(state.local_path)
    if not src_dir.exists():
        return ParseResult(
            source_id=SOURCE_ID, snapshot_id=state.snapshot_id,
            row_counts={}, output_files={},
            provenance={},
            errors=[f"local_path does not exist: {src_dir}"],
        )
    out_dir = snapshot_dir or (INGEST_ROOT / "normalized" / "ligand_assay" / SOURCE_ID / state.snapshot_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    interactions = parse_interactions(src_dir / "interactions.tsv", state.snapshot_id)
    ligands      = parse_ligands(     src_dir / "ligands.tsv",      state.snapshot_id)
    targets      = parse_targets(     src_dir / "targets_and_families.tsv", state.snapshot_id)

    paths = {
        "interactions": str(_write_parquet(interactions, out_dir / "interactions.parquet")),
        "ligands":      str(_write_parquet(ligands,      out_dir / "ligands.parquet")),
        "targets":      str(_write_parquet(targets,      out_dir / "targets.parquet")),
    }

    # Provenance claim — what the catalog consolidation step ingests.
    provenance = {
        "claim_type": "ingest",
        "source_id": SOURCE_ID,
        "source_label": SOURCE_LABEL,
        "snapshot_id": state.snapshot_id,
        "sha256": state.sha256,
        "input_paths": [
            str(src_dir / "interactions.tsv"),
            str(src_dir / "ligands.tsv"),
            str(src_dir / "targets_and_families.tsv"),
        ],
        "output_paths": list(paths.values()),
        "row_counts": {
            "interactions": len(interactions),
            "ligands": len(ligands),
            "targets": len(targets),
        },
        "license": "CC-BY-4.0 (open)",
        "url_base": "https://www.guidetopharmacology.org/",
    }
    # Drop a manifest.json next to the parquet fragments for offline inspection
    (out_dir / "manifest.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")

    return ParseResult(
        source_id=SOURCE_ID,
        snapshot_id=state.snapshot_id,
        row_counts=provenance["row_counts"],
        output_files=paths,
        provenance=provenance,
        warnings=([] if _HAS_ARROW else ["pyarrow not installed; emitted JSONL instead of parquet"]),
    )


register_parser(SOURCE_ID, _parse_impl)
