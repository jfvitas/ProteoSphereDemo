"""Schema-inferring ingestion for arbitrary tabular dataset files.

Accepts CSV, TSV, XLSX, and JSON inputs with arbitrary column names and
column orderings, and converts them to ProteoSphere's canonical row schema
for downstream audit. Inference is deterministic: column-name patterns and
value-format heuristics combine into a confidence score per (column,
canonical_field) pair, and the highest-scoring assignment wins.

Three task families are supported:

- ``ppi`` (protein-protein interaction): expects two protein accessions per
  row, optionally a PDB ID, optional partition label.
- ``pl`` / ``pli`` (protein-ligand interaction): expects one protein
  accession + one ligand identifier + optional affinity, optional PDB.
- ``dta`` (drug-target affinity): same shape as ``pl``; treated identically.

Usage::

    from proteosphere.ingest import infer_dataset, ingest_to_canonical

    # Inspect what the inferrer thinks without writing anything
    report = infer_dataset("my_dataset.csv")
    print(report.task_family, report.column_mapping, report.confidence)

    # Convert to canonical and write a Parquet file ready for audit
    out_path, report = ingest_to_canonical(
        "my_dataset.csv",
        output_path="canonical.parquet",
        task_family=None,  # auto-detect
    )

The CLI form lives at ``python -m proteosphere ingest <path>``.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

# ---------------------------------------------------------------------------
# Canonical schema definitions
# ---------------------------------------------------------------------------

# Each canonical field is detected through:
#   1. Name patterns (regexes against the lowercased column name)
#   2. Value patterns (regexes against the column's string values)
#   3. Value-kind validators (function returning bool)
# A column-to-field score is the weighted sum of name match and value match.

UNIPROT_ACCESSION_RE = re.compile(
    r"^([OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9][A-Z][A-Z0-9]{2}[0-9]([A-Z][A-Z0-9]{2}[0-9])?)(-\d+)?$",
    re.IGNORECASE,
)
PDB_ID_RE = re.compile(r"^[0-9][A-Z0-9]{3}$", re.IGNORECASE)
INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
CHEMBL_RE = re.compile(r"^CHEMBL\d+$", re.IGNORECASE)
PDB_CCD_RE = re.compile(r"^[A-Z0-9]{1,5}$")
SMILES_CHARS_RE = re.compile(r"^[A-Za-z0-9@\+\-\[\]\(\)/\\\.=#%\$:\*]+$")
SPLIT_VALUES = {"train", "val", "validation", "test", "dev", "holdout"}
ISO_DATE_RE = re.compile(r"^(?P<y>\d{4})[-/](?P<m>\d{1,2})[-/](?P<d>\d{1,2})$")
YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def _lower(s: Any) -> str:
    return str(s).strip().lower()


def _val_is_uniprot(v: Any) -> bool:
    return bool(UNIPROT_ACCESSION_RE.match(str(v).strip().upper()))


def _val_is_pdb(v: Any) -> bool:
    return bool(PDB_ID_RE.match(str(v).strip().upper()))


def _val_is_inchikey(v: Any) -> bool:
    return bool(INCHIKEY_RE.match(str(v).strip().upper()))


def _val_is_chembl(v: Any) -> bool:
    return bool(CHEMBL_RE.match(str(v).strip()))


_CCD_EXCLUDE_WORDS = {
    # Common split / partition labels
    "TRAIN", "TEST", "VAL", "DEV", "FOLD", "SET", "EVAL",
    # Boolean-ish
    "TRUE", "FALSE", "YES", "NO", "NA", "NAN", "NONE", "NULL",
    # Common header words that look like CCDs
    "ID", "PDB", "TYPE", "CLASS", "GROUP", "TAG", "KEY", "NAME",
}


def _val_is_pdb_ccd(v: Any) -> bool:
    s = str(v).strip().upper()
    if not PDB_CCD_RE.match(s):
        return False
    # CCDs are at least 2 characters; one-char strings are usually chain IDs.
    if len(s) < 2:
        return False
    # exclude pure numerics (year, count) and common false positives
    if s.isdigit():
        return False
    # PDB-style 4-character codes (digit-prefixed alphanumeric) are rejected
    # here so they don't shadow pdb_id detection. True ligand CCDs are
    # almost always 1-3 chars (ATP, GDP, MG, NAD) or 5 chars (rarely).
    if PDB_ID_RE.match(s):
        return False
    # Chain identifiers (single letter or letter+digit like "A1", "B2")
    # frequently appear in PINDER-style indexes; reject them.
    if len(s) <= 2 and s[0].isalpha() and (len(s) == 1 or s[1].isdigit()):
        return False
    # Common English-looking strings that match the CCD shape but are
    # virtually never used as ligand identifiers in practice.
    if s in _CCD_EXCLUDE_WORDS:
        return False
    return True


def _val_is_uniprot_or_list(v: Any) -> bool:
    """Accept either a single UniProt accession or a delimited list of them.

    PLINDER's ``system_pocket_UniProt`` is semicolon-delimited; PINDER and
    most CSV exports are single-valued. Returning True for both means the
    inferrer can identify accession-bearing columns regardless of which
    convention was used.
    """
    s = str(v).strip()
    if not s:
        return False
    # Try direct match first
    if _val_is_uniprot(s):
        return True
    # Try splitting on common multi-value delimiters
    for delim in (";", ","):
        if delim in s:
            parts = [p.strip() for p in s.split(delim) if p.strip()]
            if parts and all(_val_is_uniprot(p) for p in parts):
                return True
    return False


def _val_looks_smiles(v: Any) -> bool:
    s = str(v).strip()
    if len(s) < 6 or len(s) > 500:
        return False
    if not SMILES_CHARS_RE.match(s):
        return False
    # Require real SMILES structural markers (parens, brackets, double bond)
    # rather than just letters+slashes — that lets things like "-logKd/Ki"
    # (an affinity-label header) sneak through as SMILES.
    has_structure = "=" in s or "(" in s or "[" in s or "%" in s
    if not has_structure:
        return False
    if not any(ch.isalpha() for ch in s):
        return False
    # Reject strings that contain affinity-label fragments (logKd, IC50, etc.)
    s_lower = s.lower()
    for fragment in ("logkd", "logki", "ic50", "ec50", "kd_n", "ki_n"):
        if fragment in s_lower:
            return False
    return True


def _val_is_split(v: Any) -> bool:
    return _lower(v) in SPLIT_VALUES


def _val_is_date(v: Any) -> bool:
    s = str(v).strip()
    if ISO_DATE_RE.match(s):
        return True
    if YEAR_RE.match(s):
        return True
    return False


def _val_is_numeric(v: Any) -> bool:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# Protein sequence: long string of standard amino acid letters
AA_LETTERS_RE = re.compile(r"^[ACDEFGHIKLMNPQRSTVWYXBZUO]+$")


def _val_looks_protein_sequence(v: Any) -> bool:
    s = str(v).strip().upper()
    if len(s) < 30:
        return False
    if len(s) > 100000:
        return False
    return bool(AA_LETTERS_RE.match(s))


# Gene name / cross-reference identifier (HUGO, FlyBase, MGI, etc.)
GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9_\-\.]{0,15}$")
FLYBASE_RE = re.compile(r"^\d+\.[A-Z]{2,5}\d+$|^FB[A-Z]{2}\d+$")


def _val_looks_gene_name(v: Any) -> bool:
    s = str(v).strip().upper()
    if not s or _val_is_uniprot(s):
        return False
    # FlyBase / D-SCRIPT style: "7227.FBpp0079304" or "FBpp0079304"
    if FLYBASE_RE.match(s):
        return True
    # HUGO-like: 1-16 alphanumeric uppercase, often starts with a letter,
    # may contain digits / underscores. Excludes pure numerics.
    if not GENE_SYMBOL_RE.match(s):
        return False
    if s.isdigit():
        return False
    if s in _CCD_EXCLUDE_WORDS:
        return False
    return True


CANONICAL_FIELDS: dict[str, dict[str, Any]] = {
    # PPI fields
    "uniprot_a": {
        "kind": "protein_accession",
        "name_patterns": [
            r"^uniprot[_-]?a$",
            r"^uniprot_?r$",
            r"^chain_?a$",
            r"^protein_?a$",
            r"^accession_?a$",
            r"^prot1$",
            r"^p1$",
            r"^left",
            r"^receptor$",
            r"first.*uniprot",
            r"first.*protein",
        ],
        "value_validator": _val_is_uniprot_or_list,
        "task_families": {"ppi"},
    },
    "uniprot_b": {
        "kind": "protein_accession",
        "name_patterns": [
            r"^uniprot[_-]?b$",
            r"^uniprot_?l$",
            r"^chain_?b$",
            r"^protein_?b$",
            r"^accession_?b$",
            r"^prot2$",
            r"^p2$",
            r"^right",
            r"^ligand_chain$",
            r"second.*uniprot",
            r"second.*protein",
        ],
        "value_validator": _val_is_uniprot_or_list,
        "task_families": {"ppi"},
    },
    # PL/DTA fields
    "primary_accession": {
        "kind": "protein_accession",
        "name_patterns": [
            r"^uniprot$",
            r"^uniprot_?id$",
            r"^accession$",
            r"^protein$",
            r"^protein_?id$",
            r"^target$",
            r"^target_?id$",
            r"^pocket_?protein$",
            r"^pocket_?uniprot$",
            r"\bprotein\b",
            r"\btarget\b",
        ],
        "value_validator": _val_is_uniprot_or_list,
        "task_families": {"pl"},
    },
    "ligand_id": {
        "kind": "ligand",
        "name_patterns": [
            r"^ligand$",
            r"^ligand_?id$",
            r"^drug$",
            r"^drug_?id$",
            r"^compound$",
            r"^compound_?id$",
            r"^molecule$",
            r"^chembl_?id$",
            r"^pdb_?ccd$",
            r"^ccd$",
            r"^inchikey$",
            r"^inchi_?key$",
            r"^smiles$",
            r"\bdrug\b",
            r"\bligand\b",
            r"\bcompound\b",
        ],
        "value_validator": lambda v: (
            _val_is_inchikey(v)
            or _val_is_chembl(v)
            or _val_is_pdb_ccd(v)
            or _val_looks_smiles(v)
        ),
        "task_families": {"pl"},
    },
    "affinity_value": {
        "kind": "numeric",
        "name_patterns": [
            r"^affinity$",
            r"^kd$",
            r"^ki$",
            r"^ic50$",
            r"^pic50$",
            r"^pkd$",
            r"^pki$",
            r"^activity$",
            r"^binding[_-]?affinity$",
            r"\baffinity\b",
            r"\bactivity\b",
            r"^label$",
            r"^value$",
            r"^score$",
        ],
        "value_validator": _val_is_numeric,
        "task_families": {"pl", "ppi"},
    },
    # Common fields
    "pdb_id": {
        "kind": "pdb_id",
        "name_patterns": [
            r"^pdb$",
            r"^pdb_?id$",
            r"^pdb_?code$",
            r"^entry_?pdb_?id$",
            r"^structure_?id$",
        ],
        "value_validator": _val_is_pdb,
        "task_families": {"ppi", "pl"},
    },
    "split": {
        "kind": "categorical",
        "name_patterns": [
            r"^split$",
            r"^partition$",
            r"^fold$",
            r"^set$",
            r"^subset$",
            r"^stage$",
        ],
        "value_validator": _val_is_split,
        "task_families": {"ppi", "pl"},
    },
    "deposition_date": {
        "kind": "date",
        "name_patterns": [
            r"^deposit",
            r"^release",
            r"^date$",
            r"^year$",
            r"\bdate\b",
            r"^entry_release_date$",
        ],
        "value_validator": _val_is_date,
        "task_families": {"ppi", "pl"},
    },
    "row_id": {
        "kind": "identifier",
        "name_patterns": [r"^id$", r"^row_?id$", r"^system_?id$", r"^uid$", r"^index$"],
        "value_validator": lambda v: True,
        "task_families": {"ppi", "pl"},
    },
    # Resolver-required fields: detected here so the caller can dispatch to
    # the right resolution step. Audits cannot use these directly.
    "protein_sequence": {
        "kind": "raw_sequence",
        "name_patterns": [
            r"^seq$",
            r"^sequence$",
            r"^protein_?sequence$",
            r"^target_?sequence$",
            r"^aa_?seq$",
            r"\bsequence\b",
        ],
        "value_validator": _val_looks_protein_sequence,
        "task_families": {"ppi", "pl"},
    },
    "ligand_smiles": {
        "kind": "raw_smiles",
        "name_patterns": [
            r"^smiles$",
            r"^canonical_smiles$",
            r"^iso_?smiles$",
            r"^compound_?iso_?smiles$",
            r"\bsmiles\b",
        ],
        "value_validator": _val_looks_smiles,
        "task_families": {"pl"},
    },
    "gene_name_a": {
        "kind": "gene_name",
        "name_patterns": [
            r"^gene_?a$",
            r"^gene_?name_?a$",
            r"^symbol_?a$",
            r"^prot1_?id$",
        ],
        "value_validator": _val_looks_gene_name,
        "task_families": {"ppi"},
        "name_required": True,
    },
    "gene_name_b": {
        "kind": "gene_name",
        "name_patterns": [
            r"^gene_?b$",
            r"^gene_?name_?b$",
            r"^symbol_?b$",
            r"^prot2_?id$",
        ],
        "value_validator": _val_looks_gene_name,
        "task_families": {"ppi"},
        "name_required": True,
    },
    "gene_name": {
        "kind": "gene_name",
        "name_patterns": [
            r"^gene$",
            r"^gene_?name$",
            r"^symbol$",
            r"^hgnc$",
            r"^flybase$",
        ],
        "value_validator": _val_looks_gene_name,
        "task_families": {"pl"},
        "name_required": True,
    },
}


@dataclass
class ColumnInference:
    column: str
    canonical_field: str
    name_score: float
    value_score: float
    confidence: float
    notes: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    source_path: str
    source_format: str
    row_count: int
    detected_columns: list[str]
    task_family: str
    column_mapping: dict[str, str]  # canonical_field -> source_column
    column_inferences: list[ColumnInference]
    confidence: float
    warnings: list[str]
    canonical_columns: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_format": self.source_format,
            "row_count": self.row_count,
            "detected_columns": self.detected_columns,
            "task_family": self.task_family,
            "column_mapping": self.column_mapping,
            "column_inferences": [
                {
                    "column": c.column,
                    "canonical_field": c.canonical_field,
                    "name_score": c.name_score,
                    "value_score": c.value_score,
                    "confidence": c.confidence,
                    "notes": c.notes,
                }
                for c in self.column_inferences
            ],
            "confidence": self.confidence,
            "warnings": self.warnings,
            "canonical_columns": self.canonical_columns,
        }


# ---------------------------------------------------------------------------
# File loaders (format detection)
# ---------------------------------------------------------------------------


def _sniff_delimiter(path: Path) -> str | None:
    """Best-effort delimiter detection for headerless or odd-shaped text files."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            sample = "".join([fh.readline() for _ in range(8)])
    except OSError:
        return None
    candidates = [("\t", sample.count("\t")), (",", sample.count(",")), (";", sample.count(";")), ("|", sample.count("|"))]
    candidates.sort(key=lambda x: -x[1])
    if candidates and candidates[0][1] > 0:
        return candidates[0][0]
    # Whitespace fallback (multiple spaces between columns)
    if any(line.strip() and len(line.split()) > 1 for line in sample.splitlines()):
        return r"\s+"
    return None


def _looks_like_data_row(values: list[str]) -> bool:
    """Heuristic: return True if a row looks like data, False if it looks like a header.

    A row "looks like data" if any of its cells contains a UniProt accession,
    a PDB ID, a long integer, a SMILES-shaped string, or a known gene/FlyBase
    identifier. Headers tend to be short alphabetic descriptors.
    """
    for v in values:
        s = str(v).strip()
        if not s:
            continue
        if _val_is_uniprot(s):
            return True
        if _val_is_pdb(s):
            return True
        if FLYBASE_RE.match(s.upper()):
            return True
        if _val_is_chembl(s) or _val_is_inchikey(s):
            return True
        if _val_looks_protein_sequence(s):
            return True
        if _val_looks_smiles(s):
            return True
        # numeric-looking values that aren't 1-3 digits (which could be a column index)
        try:
            float(s)
            if not (s.isdigit() and len(s) <= 3):
                return True
        except (ValueError, TypeError):
            pass
    return False


def _read_csv_with_header_inference(
    path: Path, sep: str = ","
) -> pd.DataFrame:
    """Read a CSV/TSV with auto-detected presence of a header row.

    Reads the first line and sniffs whether it looks like data. If yes,
    re-read with header=None and synthesize ``col_0``, ``col_1``, ...
    """
    # Peek at first two rows
    try:
        head = pd.read_csv(path, sep=sep, nrows=1, header=None, engine="python")
        first_row = [str(v) for v in head.iloc[0].tolist()]
    except Exception:
        return pd.read_csv(path, sep=sep, engine="python")
    if _looks_like_data_row(first_row):
        df = pd.read_csv(path, sep=sep, header=None, engine="python")
        df.columns = [f"col_{i}" for i in range(len(df.columns))]
        return df
    return pd.read_csv(path, sep=sep, engine="python")


def _read_text_with_sniff(path: Path) -> pd.DataFrame:
    """Read a .txt or .dat file by sniffing delimiter and header."""
    delim = _sniff_delimiter(path) or "\t"
    try:
        return _read_csv_with_header_inference(path, sep=delim)
    except (pd.errors.ParserError, ValueError, UnicodeDecodeError):
        # Last-ditch: try header=None with python engine
        try:
            df = pd.read_csv(path, sep=delim, header=None, engine="python")
            df.columns = [f"col_{i}" for i in range(len(df.columns))]
            return df
        except Exception as exc:
            raise ValueError(f"Could not parse text file {path}: {exc}") from exc


def _load_dataframe(source: str | Path) -> tuple[pd.DataFrame, str]:
    """Read CSV/TSV/TXT/XLSX/JSON into a DataFrame; return (df, format)."""
    path = Path(source)
    suffix = path.suffix.lower().lstrip(".")
    if suffix in {"csv"}:
        return _read_csv_with_header_inference(path, sep=","), "csv"
    if suffix in {"tsv", "tab"}:
        return _read_csv_with_header_inference(path, sep="\t"), "tsv"
    if suffix in {"txt", "dat"}:
        return _read_text_with_sniff(path), "txt"
    if suffix in {"xlsx", "xlsm", "xls"}:
        try:
            return pd.read_excel(path), "xlsx"
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "openpyxl is required for xlsx ingestion. "
                "Install with `pip install openpyxl`."
            ) from e
    if suffix in {"json", "jsonl", "ndjson"}:
        text = path.read_text(encoding="utf-8")
        # JSON Lines if every non-blank line is a JSON object
        lines = [l for l in text.splitlines() if l.strip()]
        try:
            if suffix in {"jsonl", "ndjson"} or (
                lines and all(l.lstrip().startswith("{") for l in lines)
            ):
                return pd.read_json(path, lines=True), "jsonl"
        except ValueError:
            pass
        # Otherwise: plain JSON. Could be a list of dicts or a dict of arrays
        # or a nested structure with a "rows" or "data" key.
        payload = json.loads(text)
        if isinstance(payload, list):
            return pd.DataFrame(payload), "json"
        if isinstance(payload, dict):
            for key in ("rows", "data", "records", "items", "entries"):
                if key in payload and isinstance(payload[key], list):
                    return pd.DataFrame(payload[key]), "json"
            # dict-of-columns layout
            try:
                return pd.DataFrame(payload), "json"
            except ValueError:
                pass
        raise ValueError(
            f"Unable to coerce JSON at {path} into a tabular DataFrame. "
            "Expected a list of objects, a dict with a 'rows'/'data' key, "
            "or a dict-of-columns mapping."
        )
    if suffix in {"parquet", "pq"}:
        return pd.read_parquet(path), "parquet"
    raise ValueError(
        f"Unsupported input format: {path.suffix!r}. Supported: csv, tsv, "
        f"xlsx, xlsm, xls, json, jsonl, ndjson, parquet."
    )


# ---------------------------------------------------------------------------
# Inference engine
# ---------------------------------------------------------------------------


def _normalize_column_name(column: str) -> str:
    """Normalize a column name for pattern matching: lowercase, replace
    non-word characters with underscores, collapse runs of underscores."""
    name = re.sub(r"\W+", "_", str(column).strip().lower()).strip("_")
    name = re.sub(r"_+", "_", name)
    return name


def _name_score(column: str, patterns: list[str]) -> float:
    """Score 0..1 for how strongly a column name matches a list of patterns.

    Tries the lowercased raw name first, then a normalized form where
    non-word characters become underscores. ``"PDB Code"`` becomes
    ``"pdb_code"``; ``"Protein A"`` becomes ``"protein_a"``.
    """
    raw = _lower(column)
    norm = _normalize_column_name(column)
    best = 0.0
    for pat in patterns:
        for candidate in (raw, norm):
            if re.search(pat, candidate):
                # Anchored patterns get a higher score than substring matches
                score = 1.0 if pat.startswith("^") else 0.7
                best = max(best, score)
                break
    return best


def _value_score(values: pd.Series, validator) -> tuple[float, int]:
    """Fraction of non-null values that pass the validator. Returns (frac, sampled)."""
    sample = values.dropna().head(200)
    if len(sample) == 0:
        return 0.0, 0
    hits = sum(1 for v in sample if validator(v))
    return hits / len(sample), len(sample)


def _pretest_task_family(df: pd.DataFrame, columns: list[str]) -> str:
    """Decide task family by counting protein-shaped vs ligand-shaped columns.

    A column counts as "protein-shaped" if at least 50% of its non-null
    values pass ``_val_is_uniprot``. Ligand-shaped columns pass
    ``_val_is_inchikey`` / ``_val_is_chembl`` / ``_val_is_pdb_ccd`` /
    ``_val_looks_smiles`` (and don't pass the protein test). PDB-shaped
    columns pass ``_val_is_pdb`` (and don't pass the protein test).

    - 2+ protein-shaped columns and 0 ligand-shaped -> ppi
    - 1+ protein-shaped and 1+ ligand-shaped -> pl
    - 0 protein-shaped -> pl (default; the audit will surface a warning)
    """
    protein_cols = 0
    ligand_cols = 0
    sequence_cols = 0
    smiles_cols = 0
    gene_cols = 0
    pdb_cols = 0
    for col in columns:
        sample = df[col].dropna().head(50)
        if len(sample) == 0:
            continue
        prot_hits = sum(1 for v in sample if _val_is_uniprot_or_list(v))
        if prot_hits / len(sample) >= 0.5:
            protein_cols += 1
            continue
        seq_hits = sum(1 for v in sample if _val_looks_protein_sequence(v))
        if seq_hits / len(sample) >= 0.5:
            sequence_cols += 1
            continue
        smiles_hits = sum(1 for v in sample if _val_looks_smiles(v))
        unique_count = sample.astype(str).str.strip().nunique()
        if smiles_hits / len(sample) >= 0.5 and unique_count >= 5:
            smiles_cols += 1
            continue
        pdb_hits = sum(1 for v in sample if _val_is_pdb(v))
        if pdb_hits / len(sample) >= 0.5:
            pdb_cols += 1
            continue
        gene_hits = sum(1 for v in sample if _val_looks_gene_name(v))
        if gene_hits / len(sample) >= 0.5 and unique_count >= 5:
            gene_cols += 1
            continue
        lig_hits = sum(
            1
            for v in sample
            if _val_is_inchikey(v)
            or _val_is_chembl(v)
            or _val_is_pdb_ccd(v)
        )
        if lig_hits / len(sample) >= 0.5 and unique_count >= 5:
            ligand_cols += 1

    # SMILES is the most definitive PL signal: a column of canonical SMILES
    # strings would not appear in a PPI dataset.
    if smiles_cols >= 1:
        return "pl"
    # Effective protein column count (uniprot, sequences, or gene names)
    eff_protein = protein_cols + sequence_cols + gene_cols
    # Effective ligand column count (canonical IDs)
    eff_ligand = ligand_cols
    if eff_protein >= 2 and eff_ligand == 0:
        return "ppi"
    if eff_protein >= 1 and eff_ligand >= 1:
        return "pl"
    if pdb_cols >= 2 and eff_ligand == 0 and eff_protein == 0:
        # Struct2Graph / DIPS-style PDB-pair PPI inputs.
        return "ppi"
    if pdb_cols >= 1 and eff_ligand == 0 and eff_protein == 0:
        # PDBbind core / DeepTGIN style: PDB ID is the only ID.
        return "pl"
    return "pl"  # safe default


def infer_dataset(
    source: str | Path,
    *,
    task_family_hint: str | None = None,
    minimum_value_fraction: float = 0.5,
    minimum_confidence: float = 0.4,
) -> IngestReport:
    """Infer the canonical schema of a dataset file.

    Returns a report describing what each input column was mapped to, the
    detected task family, and an overall confidence score.

    The mapping never raises on ambiguity — it returns the best guess and
    surfaces the uncertainty in ``confidence`` and ``warnings``. Callers
    should inspect the report and refuse to proceed if confidence is too low.
    """
    df, fmt = _load_dataframe(source)
    columns = list(df.columns)
    inferences: list[ColumnInference] = []

    # Pre-detect task family BEFORE scoring so we don't mix PPI fields with
    # PL fields. The hint, when supplied, overrides this.
    task_family = task_family_hint or _pretest_task_family(df, columns)

    # Score every (column, canonical_field) pair, restricted to the resolved
    # task family. Common fields (split, pdb_id, deposition_date, row_id)
    # belong to all task families.
    candidates: list[tuple[float, str, str, float, float, list[str]]] = []
    for column in columns:
        for field_name, spec in CANONICAL_FIELDS.items():
            if task_family not in spec.get("task_families", set()):
                continue
            n = _name_score(column, spec["name_patterns"])
            v_frac, sampled = _value_score(df[column], spec["value_validator"])
            v = v_frac if sampled else 0.0
            # Fields whose value shape is loose (gene names, generic symbols)
            # require an explicit name match to avoid claiming arbitrary
            # identifier columns. Without a name match, skip the candidate.
            if spec.get("name_required") and n == 0.0:
                continue
            # Combined score: 0.4 * name_match + 0.6 * value_match
            confidence = 0.4 * n + 0.6 * v
            notes = []
            if sampled == 0:
                notes.append("column was empty; relied on name match alone")
            if v_frac < minimum_value_fraction and v_frac > 0:
                notes.append(
                    f"value-format fit only {v_frac:.0%} on {sampled} sampled values"
                )
            candidates.append((confidence, column, field_name, n, v, notes))

    # Greedy assignment: highest-confidence (column, field) wins, then exclude
    # both column and field from further consideration (so we never assign two
    # columns to the same canonical field, or two canonical fields to the same
    # column).
    candidates.sort(key=lambda x: x[0], reverse=True)
    used_columns: set[str] = set()
    used_fields: set[str] = set()
    mapping: dict[str, str] = {}
    for conf, col, field_name, n, v, notes in candidates:
        if col in used_columns or field_name in used_fields:
            continue
        if conf < minimum_confidence:
            continue
        # Field must have at least *some* signal: either a strong name match
        # or a strong value match. Both being weak is a refusal.
        if n < 0.5 and v < minimum_value_fraction:
            continue
        mapping[field_name] = col
        used_columns.add(col)
        used_fields.add(field_name)
        inferences.append(
            ColumnInference(
                column=col,
                canonical_field=field_name,
                name_score=n,
                value_score=v,
                confidence=conf,
                notes=notes,
            )
        )

    # Validate task-family fit and compute overall confidence. Each required
    # field may be satisfied by EITHER a canonical identifier OR an alternate
    # identifier that the resolver can promote (sequence -> accession,
    # gene name -> accession, PDB ID -> accession). When only an alternate
    # identifier is present, a 'needs_resolution' warning is emitted so the
    # caller knows to dispatch to proteosphere.resolver before audit.
    warnings: list[str] = []
    field_alternatives = {
        "ppi": {
            "uniprot_a": ["gene_name_a", "protein_sequence", "pdb_id"],
            "uniprot_b": ["gene_name_b", "protein_sequence", "pdb_id"],
        },
        "pl": {
            "primary_accession": ["gene_name", "protein_sequence", "pdb_id"],
            "ligand_id": ["ligand_smiles", "pdb_id"],
        },
    }.get(task_family, {})
    needs_resolution: list[str] = []
    missing: list[str] = []
    for primary, alts in field_alternatives.items():
        if primary in mapping:
            continue
        for alt in alts:
            if alt in mapping:
                needs_resolution.append(f"{primary} via {alt} -> use proteosphere.resolver")
                break
        else:
            missing.append(primary)
    if missing:
        warnings.append(
            f"Required field(s) for task_family={task_family!r} not detected: {missing}"
        )
    if needs_resolution:
        for n in needs_resolution:
            warnings.append(f"resolver_needed: {n}")

    # Overall confidence = mean of individual confidences for the satisfied
    # primary or alternate identifier per required slot, penalized for any
    # missing slot (a slot with neither canonical nor alternate identifier).
    primary_slots = list(field_alternatives.keys()) or list(mapping.keys())
    slot_scores: list[float] = []
    for primary in primary_slots:
        if primary in mapping:
            slot_scores.append(next(c.confidence for c in inferences if c.canonical_field == primary))
            continue
        # try alternate
        best = 0.0
        for alt in field_alternatives.get(primary, []):
            if alt in mapping:
                cand = next(c.confidence for c in inferences if c.canonical_field == alt)
                # Alternate-identifier confidence gets a 0.85x discount because
                # it requires resolver dispatch to be fully usable.
                best = max(best, 0.85 * cand)
        slot_scores.append(best)
    overall_conf = (sum(slot_scores) / len(slot_scores)) if slot_scores else 0.0

    if "split" not in mapping:
        warnings.append(
            "No 'split' column detected. Audit will treat the entire input as a single partition."
        )

    canonical_cols = list(field_alternatives.keys())
    for opt in ("pdb_id", "split", "deposition_date", "affinity_value", "row_id"):
        if opt in mapping:
            canonical_cols.append(opt)

    return IngestReport(
        source_path=str(source),
        source_format=fmt,
        row_count=len(df),
        detected_columns=columns,
        task_family=task_family,
        column_mapping=mapping,
        column_inferences=inferences,
        confidence=overall_conf,
        warnings=warnings,
        canonical_columns=canonical_cols,
    )


# ---------------------------------------------------------------------------
# Conversion to canonical row schema
# ---------------------------------------------------------------------------


def _norm_acc(s: Any) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    text = str(s).strip().upper()
    if not text or text in {"NONE", "NAN", "NA"}:
        return ""
    if "-" in text:
        head, _, tail = text.partition("-")
        if not tail.isdigit():
            return head
    return text


def _norm_pdb(s: Any) -> str:
    if s is None or (isinstance(s, float) and pd.isna(s)):
        return ""
    return str(s).strip().upper()


def _norm_split(s: Any) -> str:
    text = _lower(s)
    if text in {"validation", "dev"}:
        return "val"
    if text == "holdout":
        return "test"
    return text or "train"


def to_canonical(
    df: pd.DataFrame, report: IngestReport
) -> pd.DataFrame:
    """Convert a raw DataFrame to canonical row schema using a mapping."""
    out = pd.DataFrame()
    m = report.column_mapping

    if report.task_family == "ppi":
        out["uniprot_a"] = df[m["uniprot_a"]].map(_norm_acc) if "uniprot_a" in m else ""
        out["uniprot_b"] = df[m["uniprot_b"]].map(_norm_acc) if "uniprot_b" in m else ""
    elif report.task_family == "pl":
        out["primary_accession"] = (
            df[m["primary_accession"]].map(_norm_acc) if "primary_accession" in m else ""
        )
        out["ligand_id"] = (
            df[m["ligand_id"]].astype(str).str.strip() if "ligand_id" in m else ""
        )

    if "pdb_id" in m:
        out["pdb_id"] = df[m["pdb_id"]].map(_norm_pdb)
    if "split" in m:
        out["split"] = df[m["split"]].map(_norm_split)
    else:
        out["split"] = "train"
    if "deposition_date" in m:
        out["deposition_date"] = df[m["deposition_date"]].astype(str).str.strip()
    if "affinity_value" in m:
        out["affinity_value"] = pd.to_numeric(df[m["affinity_value"]], errors="coerce")
    if "row_id" in m:
        out["row_id"] = df[m["row_id"]].astype(str).str.strip()
    else:
        out["row_id"] = [f"row_{i:08d}" for i in range(len(out))]

    # Stable column order
    leading = (
        ["row_id", "split", "uniprot_a", "uniprot_b", "primary_accession", "ligand_id", "pdb_id"]
        if report.task_family == "ppi"
        else ["row_id", "split", "primary_accession", "ligand_id", "pdb_id", "affinity_value"]
    )
    cols = [c for c in leading if c in out.columns] + [
        c for c in out.columns if c not in leading
    ]
    return out[cols]


def ingest_to_canonical(
    source: str | Path,
    output_path: str | Path | None = None,
    *,
    task_family_hint: str | None = None,
    minimum_confidence: float = 0.4,
    resolve: bool = False,
    config: "Config | None" = None,
) -> tuple[Path, IngestReport]:
    """Read an arbitrary tabular file and write canonical-schema Parquet.

    When ``resolve=True``, alternate identifiers (raw protein sequences,
    gene names, PDB IDs) are promoted to canonical UniProt accessions
    using ``proteosphere.resolver``. Requires a configured warehouse with
    the ``sequence_index`` and ``cross_references`` partitions present.
    """
    df, _ = _load_dataframe(source)
    report = infer_dataset(
        source, task_family_hint=task_family_hint, minimum_confidence=minimum_confidence
    )
    canonical = to_canonical(df, report)

    if resolve:
        canonical, resolution_summary = _apply_resolvers(canonical, df, report, config)
        report.warnings.append(
            f"resolver_applied: {resolution_summary}"
        )

    if output_path is None:
        output_path = Path(source).with_suffix(".canonical.parquet")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canonical.to_parquet(output_path, index=False)

    # Sidecar: write the inference report alongside the output
    report_path = output_path.with_suffix(".ingest_report.json")
    report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

    return output_path, report


def _apply_resolvers(
    canonical: pd.DataFrame,
    raw: pd.DataFrame,
    report: IngestReport,
    config: "Config | None",
) -> tuple[pd.DataFrame, str]:
    """Promote alternate identifiers in ``canonical`` to UniProt accessions."""
    from proteosphere import Config as _Config
    from proteosphere import resolver

    if config is None:
        config = _Config.discover()

    promoted: list[str] = []
    m = report.column_mapping

    # Sequence -> primary_accession
    if "protein_sequence" in m and "primary_accession" not in m:
        seqs = raw[m["protein_sequence"]].astype(str).fillna("").tolist()
        records = resolver.resolve_sequences_to_accessions(config, seqs)
        canonical["primary_accession"] = [
            (records.get(s) or [""])[0] for s in seqs
        ]
        promoted.append("protein_sequence -> primary_accession")

    # Gene name -> primary_accession (PL)
    if "gene_name" in m and "primary_accession" not in m:
        names = raw[m["gene_name"]].astype(str).fillna("").tolist()
        records = resolver.resolve_gene_names_to_accessions(config, names)
        canonical["primary_accession"] = [
            (records.get(n) or [""])[0] for n in names
        ]
        promoted.append("gene_name -> primary_accession")

    # Gene names -> uniprot_a / uniprot_b (PPI)
    if "gene_name_a" in m and "uniprot_a" not in m:
        names = raw[m["gene_name_a"]].astype(str).fillna("").tolist()
        records = resolver.resolve_gene_names_to_accessions(config, names)
        canonical["uniprot_a"] = [(records.get(n) or [""])[0] for n in names]
        promoted.append("gene_name_a -> uniprot_a")
    if "gene_name_b" in m and "uniprot_b" not in m:
        names = raw[m["gene_name_b"]].astype(str).fillna("").tolist()
        records = resolver.resolve_gene_names_to_accessions(config, names)
        canonical["uniprot_b"] = [(records.get(n) or [""])[0] for n in names]
        promoted.append("gene_name_b -> uniprot_b")

    # PDB ID -> accessions (one row in -> potentially many accessions out)
    if (
        "pdb_id" in m
        and "primary_accession" not in canonical.columns
        and "uniprot_a" not in canonical.columns
    ):
        pdbs = raw[m["pdb_id"]].astype(str).fillna("").tolist()
        records = resolver.resolve_pdbs_to_accessions(config, pdbs)
        canonical["primary_accession"] = [
            (records.get(str(p).upper()) or [""])[0] for p in pdbs
        ]
        promoted.append("pdb_id -> primary_accession")

    summary = "; ".join(promoted) or "no resolvers triggered"
    return canonical, summary
