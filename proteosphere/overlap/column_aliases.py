"""Centralized column-name alias registry for every CLI loader.

Different users hand us CSV / XLSX / JSON files with wildly varying
column names: ``Test ID``, ``test_ids``, ``test-id``, ``TestIDs``,
``test pdb``, ``test_accession``, ... — all meaning the same thing.
Rather than scatter that knowledge across the three CLIs (cluster,
pairwise-checker, audit-split), this module collects every reasonable
alias for each *role* a column might play, plus a fuzzy ``normalize()``
helper so user spelling variants match transparently.

Roles
-----

* :data:`ACCESSION_COLUMNS`   -- a UniProt accession column
* :data:`PDB_COLUMNS`         -- a PDB structure ID column
* :data:`PROTEIN_COLUMNS`     -- a generic "protein" column (any ID kind)
* :data:`TEST_COLUMNS`        -- the test side of a pair
* :data:`COMPARISON_COLUMNS`  -- the train / reference / comparison side
* :data:`PAIR_A_COLUMNS`      -- first protein in a pair (entity-kind protein_pair)
* :data:`PAIR_B_COLUMNS`      -- second protein in a pair
* :data:`LABEL_COLUMNS`       -- free-text label / name for the row
* :data:`SPLIT_COLUMNS`       -- train / val / test partition column
* :data:`FOLD_COLUMNS`        -- k-fold-style partition column
* :data:`SUBGROUP_COLUMNS`    -- category / family / subgroup tag for stratification
* :data:`MUTATION_COLUMNS`    -- mutation string column
* :data:`ROW_ID_COLUMNS`      -- unique per-row identifier (Complex ID etc.)
* :data:`KD_COLUMNS`          -- binding affinity column (KD / Ki / IC50)

Lookup
------

Use :func:`find_column_index` for the most common case: "which column
in this header row plays role X?". It returns the first matching index
or None.

Use :func:`find_all_column_indices` when a role can be played by
multiple columns (e.g. PPB-Affinity has both ``test_id`` and ``comp_id``
that both hold PDB IDs).

Both helpers run their inputs through :func:`normalize` so case,
whitespace, underscores, and dashes don't matter.
"""
from __future__ import annotations

from typing import Iterable, Sequence


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


_SEPARATORS = ("_", "-", ".", " ", "/", "\\", "\t")


def normalize(name: str | None) -> str:
    """Canonicalise a column name for alias matching.

    The matcher should treat all of the following as equivalent:

    - ``Test ID``
    - ``test_id``
    - ``test-id``
    - ``test.id``
    - ``TestID``
    - ``  test_id  `` (with whitespace)
    - ``test ids`` / ``Test IDs``

    We lowercase, strip whitespace, and remove every separator character
    (``_``, ``-``, ``.``, space, ``/``, ``\\``, tab). This collapses
    camelCase, snake_case, kebab-case, and "space separated" all to the
    same compact form. So ``UniProt``, ``uni_prot``, ``Uni Prot`` all
    normalize to ``uniprot``.

    Empty / None inputs return an empty string.
    """
    if not name:
        return ""
    text = str(name).strip().lower()
    for sep in _SEPARATORS:
        text = text.replace(sep, "")
    return text


def _alias_set(*parts: Iterable[str]) -> frozenset[str]:
    """Build a frozen set of normalized aliases from multiple iterables."""
    out: set[str] = set()
    for piece in parts:
        for item in piece:
            n = normalize(item)
            if n:
                out.add(n)
    return frozenset(out)


# ---------------------------------------------------------------------------
# Reusable building blocks. Bare nouns; plurals; common qualifiers.
# Each builder takes a base noun and emits singular + plural + common
# id-suffix variants.
# ---------------------------------------------------------------------------


def _id_variants(base: str) -> list[str]:
    """Variants of a 'base' noun used as an identifier column.

    e.g. ``_id_variants('test')`` yields (after normalization):
        test, tests, testid, testids, testpdb, testpdbid, testpdbids,
        testaccession, testaccessions, testuniprot, testuniprotid,
        testuniprotids, testprotein, testproteins, teststructure, ...

    The point is to be exhaustive about the noun's role -- if you ever
    see "Test PDB IDs", "test_uniprots", or "test_structure_ids" in a
    CSV header, it should match.
    """
    bases = [base, base + "s"]
    suffixes = (
        "", "_id", "_ids", "id", "ids",
        # PDB variants
        "_pdb", "_pdbs", "_pdb_id", "_pdb_ids", "_pdbid", "_pdbids",
        "_pdb_code", "_pdb_codes",
        # UniProt accession variants
        "_accession", "_accessions",
        "_acc", "_accs",
        "_uniprot", "_uniprots",
        "_uniprot_id", "_uniprot_ids",
        "_uniprot_accession", "_uniprot_accessions",
        # Generic protein/gene
        "_protein", "_proteins", "_protein_id", "_protein_ids",
        "_gene", "_genes", "_gene_id", "_gene_ids",
        # Structure / complex / entry
        "_structure", "_structures",
        "_structure_id", "_structure_ids",
        "_entry", "_entries", "_entry_id", "_entry_ids",
        "_complex", "_complexes", "_complex_id", "_complex_ids",
    )
    out: list[str] = []
    for b in bases:
        for s in suffixes:
            out.append(b + s)
    return out


# ---------------------------------------------------------------------------
# Role -> alias set
# ---------------------------------------------------------------------------

# UniProt-accession columns. Most explicit.
ACCESSION_COLUMNS = _alias_set([
    "accession", "accessions",
    "uniprot", "uniprots",
    "uniprot_id", "uniprot_ids",
    "uniprot_accession", "uniprot_accessions",
    "uniprotkb", "uniprotkb_id", "uniprotkb_accession",
    "uniprotid", "uniprotids",
    "acc", "accs", "acc_id", "acc_ids",
    "swissprot", "swiss_prot", "swiss_prot_id",
    "trembl", "trembl_id",
])

# PDB structure IDs.
PDB_COLUMNS = _alias_set([
    "pdb", "pdbs", "pdb_id", "pdb_ids", "pdbid", "pdbids",
    "pdb_code", "pdb_codes",
    "structure", "structures", "structure_id", "structure_ids",
    "structureid", "structureids",
    "struct", "struct_id",
    "rcsb", "rcsb_id",
    "entry", "entries", "entry_id", "entry_ids", "entryid",
    "complex", "complexes", "complex_id", "complex_ids", "complexid",
])

# Generic "this is a protein / gene" column.
PROTEIN_COLUMNS = _alias_set([
    "protein", "proteins", "protein_id", "protein_ids", "proteinid",
    "gene", "genes", "gene_id", "gene_ids", "gene_name", "genename",
    "chain", "chains", "chain_id", "chain_ids",
    "id", "ids", "identifier", "identifiers",
])

# "Test" side of a pair (the held-out / evaluation side). Carries
# benchmark-partition semantics (this side is what we evaluate against).
# For symmetric "side a / side b" pair files without that semantics,
# use PAIR_A_COLUMNS / PAIR_B_COLUMNS instead.
TEST_COLUMNS = _alias_set(
    _id_variants("test"),
    _id_variants("eval"),
    _id_variants("evaluation"),
    _id_variants("holdout"),
    _id_variants("held_out"),
    _id_variants("query"),
    _id_variants("probe"),
    [
        "target", "targets", "target_id", "target_ids",
    ],
)

# "Comparison" / "training" / "reference" side of a pair.
COMPARISON_COLUMNS = _alias_set(
    _id_variants("comp"),
    _id_variants("comparison"),
    _id_variants("train"),
    _id_variants("training"),
    _id_variants("reference"),
    _id_variants("ref"),
    _id_variants("baseline"),
    _id_variants("control"),
    _id_variants("ctrl"),
    [
        "anchor", "anchor_id",
    ],
)

# Symmetric pair columns (no "test"/"comparison" semantics). Used when
# a file has ``protein_a`` / ``protein_b`` or ``side_a`` / ``side_b``.
PAIR_A_COLUMNS = _alias_set([
    "protein_a", "protein_1", "protein1",
    "side_a", "a", "left",
    "first", "first_id", "first_protein",
    "left_id", "left_protein",
    "antigen", "antigen_id", "antigen_chain",
    "ligand", "ligand_id", "ligand_chains",
    "p1", "p_a",
])
PAIR_B_COLUMNS = _alias_set([
    "protein_b", "protein_2", "protein2",
    "side_b", "b", "right",
    "second", "second_id", "second_protein",
    "right_id", "right_protein",
    "antibody", "antibody_id", "antibody_chain",
    "receptor", "receptor_id", "receptor_chains",
    "p2", "p_b",
])

# Free-text label / name for the row.
LABEL_COLUMNS = _alias_set([
    "label", "labels", "name", "names",
    "pair_id", "pair_ids", "pair_name", "pair_names",
    "row_label", "row_name",
    "entry_name", "entry_label",
    "description", "desc",
    "title", "alias",
])

# train / val / test partition column. Includes "fold" because some
# legacy formats use "split" and "fold" interchangeably.
SPLIT_COLUMNS = _alias_set([
    "split", "splits",
    "fold", "folds",
    "partition", "partitions",
    "set", "sets",
    "subset", "subsets",
    "split_label", "fold_label",
    "assignment", "assignments",
    "data_split", "ml_split", "data_partition",
    "train_test", "train_val_test",
    # Common "<phase> set" phrasings
    "training_set", "test_set", "val_set", "validation_set",
    "training_data", "test_data", "validation_data",
    "fold_id", "fold_index",
    # Cross-validation phrasings
    "cv", "cv_fold", "cv_split", "cross_validation", "cross_validation_fold",
])

# Specifically a k-fold-style column (kept separate so callers that
# want only k-fold semantics can distinguish from the looser
# SPLIT_COLUMNS set).
FOLD_COLUMNS = _alias_set([
    "fold", "folds",
    "fold_id", "fold_idx", "fold_index",
    "cv", "cv_fold", "cross_validation_fold",
])

# Category / family / subgroup for stratified splits.
SUBGROUP_COLUMNS = _alias_set([
    "subgroup", "subgroups",
    "category", "categories",
    "class", "classes",
    "family", "families",
    "type", "types",
    "domain", "domains",
    "benchmark", "benchmark_class",
    "tag", "tags",
    "stratum", "strata",
    "source", "source_dataset", "source_set", "source_db",
    "kind",
])

# Mutation strings (SKEMPI / PPB-Affinity style).
MUTATION_COLUMNS = _alias_set([
    "mutation", "mutations",
    "mut", "muts",
    "mutant", "mutants",
    "variant", "variants",
    "substitution", "substitutions",
    "point_mutation", "point_mutations",
])

# Unique per-row identifier (Complex ID for SKEMPI / PPB-Affinity, etc.)
ROW_ID_COLUMNS = _alias_set([
    "row_id", "row_ids", "rowid",
    "record_id", "record_ids", "recordid",
    "complex_id", "complex_ids", "complexid",
    "entry_id", "entry_ids", "entryid",
    "case_id", "case_ids", "caseid",
    "unique_id", "uniqueid", "uid",
    "row_index", "rowindex", "row_number",
    "pair_id", "pair_ids",
    "id", "ids",  # ambiguous fallback; LAST priority
])

# Binding affinity / Kd / Ki / IC50.
KD_COLUMNS = _alias_set([
    "kd", "kd_m", "kd_value",
    "ki", "ki_m", "ki_value",
    "ic50", "ic50_m", "ic50_value",
    "affinity", "binding_affinity",
    "delta_g", "ddg", "delta_delta_g",
    "log_kd", "pkd",
])


# ---------------------------------------------------------------------------
# Header-detection heuristics
# ---------------------------------------------------------------------------


# Any role we recognise -- used by header-vs-data detection.
ALL_KNOWN_COLUMNS: frozenset[str] = (
    ACCESSION_COLUMNS | PDB_COLUMNS | PROTEIN_COLUMNS
    | TEST_COLUMNS | COMPARISON_COLUMNS
    | PAIR_A_COLUMNS | PAIR_B_COLUMNS
    | LABEL_COLUMNS
    | SPLIT_COLUMNS | FOLD_COLUMNS
    | SUBGROUP_COLUMNS
    | MUTATION_COLUMNS
    | ROW_ID_COLUMNS
    | KD_COLUMNS
)


def looks_like_header_row(row: Sequence[str | None]) -> bool:
    """Return True if ``row`` looks like a header row.

    Heuristic: a header row contains short alphabetic-ish names like
    ``pdb_id`` or ``accession``. We say it's a header if any cell

    1. matches a known alias from any role, OR
    2. contains ``_`` plus letters (and at least one letter overall),
       OR
    3. is purely alphabetic and >=3 characters (e.g. ``Subgroup``,
       ``Mutations``).

    Numeric-only / single-letter cells don't count.
    """
    for cell in row:
        if cell is None:
            continue
        s = str(cell).strip()
        if not s:
            continue
        n = normalize(s)
        if n in ALL_KNOWN_COLUMNS:
            return True
        if "_" in n and any(ch.isalpha() for ch in n):
            return True
        # Purely-alphabetic words >= 3 chars (avoids treating "1A22"
        # as a header).
        if n.isalpha() and len(n) >= 3:
            return True
    return False


# ---------------------------------------------------------------------------
# Column matchers
# ---------------------------------------------------------------------------


def matches_role(column_name: str | None, role_aliases: Iterable[str]) -> bool:
    """Return True if ``column_name`` (after normalization) is in the role."""
    if column_name is None:
        return False
    return normalize(column_name) in set(role_aliases)


def find_column_index(
    header: Sequence[str | None],
    role_aliases: Iterable[str],
) -> int | None:
    """Return the first column index in ``header`` matching ``role_aliases``.

    Use this when a role should be represented by exactly one column.
    Returns None if no column matches.
    """
    target = set(role_aliases)
    for i, name in enumerate(header):
        if normalize(name) in target:
            return i
    return None


def find_all_column_indices(
    header: Sequence[str | None],
    role_aliases: Iterable[str],
) -> list[int]:
    """Return every column index in ``header`` that matches a role alias.

    Use this for roles where multiple columns can play the same role
    (e.g. PPB-Affinity has both ``test_id`` and ``comp_id`` holding
    PDB IDs).
    """
    target = set(role_aliases)
    return [i for i, name in enumerate(header) if normalize(name) in target]


def column_role(column_name: str | None) -> str | None:
    """Return the role name a column belongs to, or None if unrecognised.

    When a column matches multiple roles, the most-specific one wins
    in this priority order: ROW_ID > ACCESSION > PDB > MUTATION >
    SPLIT > FOLD > SUBGROUP > TEST > COMPARISON > PAIR_A > PAIR_B >
    LABEL > KD > PROTEIN.
    """
    if not column_name:
        return None
    n = normalize(column_name)
    for role_name, role_set in (
        ("row_id",     ROW_ID_COLUMNS),
        ("accession",  ACCESSION_COLUMNS),
        ("pdb",        PDB_COLUMNS),
        ("mutation",   MUTATION_COLUMNS),
        ("split",      SPLIT_COLUMNS),
        ("fold",       FOLD_COLUMNS),
        ("subgroup",   SUBGROUP_COLUMNS),
        ("test",       TEST_COLUMNS),
        ("comparison", COMPARISON_COLUMNS),
        ("pair_a",     PAIR_A_COLUMNS),
        ("pair_b",     PAIR_B_COLUMNS),
        ("label",      LABEL_COLUMNS),
        ("kd",         KD_COLUMNS),
        ("protein",    PROTEIN_COLUMNS),
    ):
        if n in role_set:
            return role_name
    return None
