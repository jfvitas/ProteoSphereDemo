"""Declarative source list — every source the LEAN ingest will pull.

Each entry is a SourceDescriptor with:
    source_id          stable identifier (matches the GUI's PS_DATA source ids)
    family             warehouse family the rows land in
    name               human-readable label
    license            free-text; see notes
    urls               one or more URLs to fetch; concatenated in order
    expected_bytes     pre-flight size hint for the cap; None if unknown
    sha256             optional expected hash (where the source publishes one)
    format             "csv" | "tsv" | "xml" | "json" | "sdf" | "tarball" | "zip"
    parser_status      "todo" | "stub" | "implemented" — where the per-source
                       parser stands. Most are "todo" in this first pass.
    parser_target      what normalized partition the rows ultimately go to
                       (so reviewers can see the target schema in one place)
    notes              free-text caveats / license / cookies / auth

The downloader consumes `urls` + `expected_bytes` + `sha256`. The parser
field is informational — actual parsers live next to this file as separate
modules once written.

See WAREHOUSE_GROWTH_PROJECTION.md for the size math behind each entry.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceDescriptor:
    source_id: str
    name: str
    family: str                  # warehouse family this lands in
    urls: list[str]              # one or more (concatenated on download)
    license: str
    expected_bytes: int | None   # for the cap; None means "unknown, fetch a HEAD"
    sha256: str | None           # if the publisher provides a checksum
    format: str
    parser_status: str           # "todo" | "stub" | "implemented"
    parser_target: str           # normalized partition path
    notes: str = ""
    insecure_ssl: bool = False   # True for known-broken public mirrors (e.g. HuRI)


# ── Manifest ─────────────────────────────────────────────────────────
# Sizes are LEAN-variant estimates (see WAREHOUSE_GROWTH_PROJECTION.md §2).
# URLs are publisher-canonical at the time of writing; refresh-cadence
# vetting is the downloader's job (HEAD before reserve).

MANIFEST: list[SourceDescriptor] = [
    # ── Assays + Affinities ────────────────────────────────────────
    SourceDescriptor(
        source_id="gtopdb",
        name="GtoPdb (IUPHAR/BPS Guide to Pharmacology)",
        family="ligand_assay",
        urls=[
            "https://www.guidetopharmacology.org/DATA/interactions.tsv",
            "https://www.guidetopharmacology.org/DATA/ligands.tsv",
            "https://www.guidetopharmacology.org/DATA/targets_and_families.tsv",
        ],
        license="Open (CC-BY-4.0)",
        expected_bytes=60_000_000,        # ~60 MB total across the three TSVs
        sha256=None,
        format="tsv",
        parser_status="todo",
        parser_target="normalized/ligand_assay/gtopdb",
        notes="High-confidence expert-curated. The smallest credible end-to-end smoke target.",
    ),
    SourceDescriptor(
        source_id="drugbank",
        name="DrugBank (open download)",
        family="ligand_assay",
        urls=["https://go.drugbank.com/releases/latest/downloads/all-full-database"],
        license="Academic-free; commercial restricted. Requires registered account + per-release access link.",
        expected_bytes=200_000_000,
        sha256=None,
        format="xml",
        parser_status="todo",
        parser_target="normalized/ligand_assay/drugbank",
        notes="The CC0 'open structures' subset is auth-free; the full XML requires academic registration. Manual download may be required before parser runs.",
    ),
    SourceDescriptor(
        source_id="pdsp",
        name="PDSP Ki Database (NIMH)",
        family="ligand_assay",
        urls=["https://kidbdev.med.unc.edu/databases/kidb.php"],  # site itself; CSV behind form
        license="Public (NIH-hosted)",
        expected_bytes=15_000_000,
        sha256=None,
        format="csv",
        parser_status="todo",
        parser_target="normalized/ligand_assay/pdsp",
        notes="Snapshot dumps available as periodic CSV pulls. Site doesn't expose a stable URL; downloader will need a session cookie.",
    ),
    SourceDescriptor(
        source_id="davis",
        name="Davis DTA benchmark (Davis 2011)",
        family="ligand_assay",
        urls=[
            "https://raw.githubusercontent.com/hkmztrk/DeepDTA/master/data/davis/proteins.txt",
            "https://raw.githubusercontent.com/hkmztrk/DeepDTA/master/data/davis/ligands_iso.txt",
            "https://raw.githubusercontent.com/hkmztrk/DeepDTA/master/data/davis/Y",
        ],
        license="Open (re-published from primary)",
        expected_bytes=1_000_000,
        sha256=None,
        format="json",
        parser_status="implemented",
        parser_target="normalized/ligand_assay/davis",
        notes="442 kinases × 68 inhibitors, Kd in nM. Parser converts to pKd = 9 - log10(Kd_nM).",
    ),
    SourceDescriptor(
        source_id="kiba",
        name="KIBA DTA benchmark (Tang 2014)",
        family="ligand_assay",
        urls=[
            "https://raw.githubusercontent.com/hkmztrk/DeepDTA/master/data/kiba/proteins.txt",
            "https://raw.githubusercontent.com/hkmztrk/DeepDTA/master/data/kiba/ligands_iso.txt",
            "https://raw.githubusercontent.com/hkmztrk/DeepDTA/master/data/kiba/Y",
        ],
        license="Open (re-published from primary)",
        expected_bytes=30_000_000,
        sha256=None,
        format="json",
        parser_status="implemented",
        parser_target="normalized/ligand_assay/kiba",
        notes="229 proteins × 2,111 ligands. Raw 'KIBA score' kept as-is (integrated KI/Kd/IC50 metric).",
    ),

    # ── PPI ──────────────────────────────────────────────────────────
    SourceDescriptor(
        source_id="huri",
        name="HuRI (Human Reference Interactome)",
        family="interaction_network",
        urls=["http://www.interactome-atlas.org/data/HuRI.tsv"],
        license="Open",
        expected_bytes=55_000_000,
        sha256=None,
        format="tsv",
        parser_status="implemented",
        parser_target="normalized/interaction_network/huri",
        notes="Systematic Y2H screen, ~64K binary PPIs. SSL cert on interactome-atlas.org is misconfigured for the HTTPS redirect; insecure_ssl=True bypasses verification.",
        insecure_ssl=True,
    ),
    SourceDescriptor(
        source_id="hippie",
        name="HIPPIE (Human Integrated PPI rEference)",
        family="interaction_network",
        urls=["http://cbdm-01.zdv.uni-mainz.de/~mschaefer/hippie/HIPPIE-current.mitab.txt"],
        license="Open",
        expected_bytes=95_000_000,
        sha256=None,
        format="tsv",
        parser_status="todo",
        parser_target="normalized/interaction_network/hippie",
        notes="Scored, integrated PPIs — ~412K rows with confidence.",
    ),
    SourceDescriptor(
        source_id="corum",
        name="CORUM (mammalian protein complexes)",
        family="interaction_network",
        urls=["https://mips.helmholtz-muenchen.de/corum/download/allComplexes.json.zip"],
        license="Open",
        expected_bytes=25_000_000,
        sha256=None,
        format="zip",
        parser_status="implemented",     # parser exists; download URL needs fix
        parser_target="normalized/interaction_network/corum",
        notes=(
            "URL_BROKEN: CORUM migrated to a JS SPA without preserving the static "
            "/corum/download/allComplexes.json.zip endpoint — all old paths return "
            "the SPA index (423 bytes of HTML). Two workarounds available: "
            "(1) reverse-engineer the SPA's network calls to find the new API "
            "URL; (2) drop the json.zip manually into the snapshot dir and run "
            "`v2_ingest parse corum`. Parser already handles both raw JSON and "
            "JSON-in-ZIP formats."
        ),
    ),
    SourceDescriptor(
        source_id="3did",
        name="3did (3D interacting domains)",
        family="interaction_network",
        urls=["https://3did.irbbarcelona.org/download/current/3did_flat.gz"],
        license="Open",
        expected_bytes=160_000_000,
        sha256=None,
        format="tsv",
        parser_status="todo",
        parser_target="normalized/interaction_network/3did",
        notes="Domain-domain interactions observed in PDB. Pfam joins required at parse time.",
    ),

    # ── Structures (LEAN variants) ──────────────────────────────────
    SourceDescriptor(
        source_id="pdb_redo_meta",
        name="PDB-REDO (metadata + summary)",
        family="structure",
        urls=["https://pdb-redo.eu/downloads/latest_metadata.tsv"],
        license="Open",
        expected_bytes=5_000_000_000,    # ~5 GB metadata only
        sha256=None,
        format="tsv",
        parser_status="todo",
        parser_target="normalized/structure/pdb_redo",
        notes="LEAN: metadata + summary tables only. Refined coord files (1.0 TB) deferred to a follow-up campaign.",
    ),
    SourceDescriptor(
        source_id="alphafill_index",
        name="AlphaFill (transplant index, no coords)",
        family="structure",
        urls=["https://alphafill.eu/v2/download/index.json"],
        license="Open",
        expected_bytes=10_000_000_000,   # ~10 GB index
        sha256=None,
        format="json",
        parser_status="todo",
        parser_target="normalized/structure/alphafill",
        notes="LEAN: index of which AF models got which ligands transplanted from PDB homologs. Coord files (3.5 TB) deferred.",
    ),
    SourceDescriptor(
        source_id="scpdb",
        name="sc-PDB (druggable binding sites)",
        family="structure",
        urls=["http://bioinfo-pharma.u-strasbg.fr/scPDB/download/scPDB_release.tar.gz"],
        license="Academic free",
        expected_bytes=20_000_000_000,   # ~20 GB
        sha256=None,
        format="tarball",
        parser_status="todo",
        parser_target="normalized/structure/scpdb",
        notes="Druggable binding-site annotations on PDB structures.",
    ),

    # ── Ligand chemistry (PubChem LEAN subset) ──────────────────────
    SourceDescriptor(
        source_id="pubchem_bioactivity",
        name="PubChem (bioactivity-linked compounds, LEAN subset)",
        family="ligand_assay",
        urls=[
            "https://ftp.ncbi.nlm.nih.gov/pubchem/Bioassay/Concise/CSV/Data/0000001_0000500.zip",
            # In production we'd iterate the full directory listing; this is a representative subset.
        ],
        license="Open",
        expected_bytes=20_000_000_000,   # ~20 GB LEAN subset
        sha256=None,
        format="csv",
        parser_status="todo",
        parser_target="normalized/ligand_assay/pubchem",
        notes="LEAN: only the bioactivity-linked compound subset. Full PubChem (500 GB SDF) deferred.",
    ),

    # ── Annotations ─────────────────────────────────────────────────
    SourceDescriptor(
        source_id="open_targets",
        name="Open Targets Platform",
        family="scrape_enrichment",
        urls=["https://platform.opentargets.org/downloads"],
        license="Open (mixed CC0/CC-BY)",
        expected_bytes=30_000_000_000,
        sha256=None,
        format="json",
        parser_status="todo",
        parser_target="normalized/scrape_enrichment/open_targets",
        notes="Target-disease associations + evidence. Large multi-file dump; downloader needs to walk an index.",
    ),
    SourceDescriptor(
        source_id="pharos",
        name="Pharos / IDG (target development levels)",
        family="scrape_enrichment",
        urls=["https://pharos.nih.gov/api/dump/latest.tar.gz"],
        license="Open",
        expected_bytes=5_000_000_000,
        sha256=None,
        format="tarball",
        parser_status="todo",
        parser_target="normalized/scrape_enrichment/pharos",
        notes="NIH Illuminating the Druggable Genome.",
    ),
    SourceDescriptor(
        source_id="ttd",
        name="TTD (Therapeutic Target Database)",
        family="scrape_enrichment",
        urls=["https://db.idrblab.net/ttd/sites/default/files/ttd_database/P1-01-TTD_target_download.txt"],
        license="Academic free",
        expected_bytes=200_000_000,
        sha256=None,
        format="tsv",
        parser_status="todo",
        parser_target="normalized/scrape_enrichment/ttd",
        notes=(
            "URL_BROKEN: db.idrblab.net 302-redirects to ttd.idrblab.cn which now serves a "
            "JS SPA (HTML index, ~600 bytes). Static file paths no longer accessible. "
            "Workarounds: (1) manual download via the SPA's UI, then drop the TSV into the "
            "snapshot dir; (2) reverse-engineer the new API once TTD publishes one."
        ),
    ),
]


# Convenience lookups ─────────────────────────────────────────────────
def by_id(source_id: str) -> SourceDescriptor | None:
    return next((s for s in MANIFEST if s.source_id == source_id), None)


def total_expected_bytes() -> int:
    return sum(s.expected_bytes or 0 for s in MANIFEST)
