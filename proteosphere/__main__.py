"""``python -m proteosphere`` CLI: smoke test and config inspection.

For full audit workflows, the per-script entry points under ``scripts/``
remain canonical. This module exists as a quick "did my install work?"
shortcut.
"""
from __future__ import annotations

import argparse
import json
import sys

from proteosphere import Config
from proteosphere.warehouse import Warehouse
from proteosphere.ingest import infer_dataset, ingest_to_canonical
from proteosphere.overlap import cli as overlap_cli
from proteosphere.overlap import cluster_cli as overlap_cluster_cli
from proteosphere.overlap import audit_cli as overlap_audit_cli


def cmd_status(args: argparse.Namespace) -> int:
    config = Config.discover(
        warehouse_root=args.warehouse_root,
        config_file=args.config,
    )
    print(f"Warehouse: {config.warehouse_root}")
    print(f"Catalog:   {config.catalog_path()}  exists={config.catalog_path().is_file()}")
    print(f"Manifest:  {config.manifest_path()}  exists={config.manifest_path().is_file()}")
    portable = config.warehouse_root / "warehouse_manifest.portable.json"
    print(f"Portable manifest: {portable}  exists={portable.is_file()}")
    if config.source_mirror_root:
        print(f"Source mirror: {config.source_mirror_root}")
    if config.benchmark_data_root:
        print(f"Benchmark data: {config.benchmark_data_root}")
    if config.offline_mode:
        print("Offline mode: ON")
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    config = Config.discover(
        warehouse_root=args.warehouse_root,
        config_file=args.config,
    )
    warehouse = Warehouse(config)
    result = warehouse.smoke_test()
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    """Infer schema for a file and (optionally) convert to canonical Parquet."""
    if args.dry_run:
        report = infer_dataset(
            args.source,
            task_family_hint=args.task_family,
            minimum_confidence=args.min_confidence,
        )
        print(json.dumps(report.to_dict(), indent=2))
        if report.confidence < args.min_confidence:
            print(
                f"\nWARNING: overall confidence {report.confidence:.2f} below "
                f"threshold {args.min_confidence:.2f}.",
                file=sys.stderr,
            )
            return 3
        return 0

    config = None
    if args.resolve:
        config = Config.discover(
            warehouse_root=args.warehouse_root, config_file=args.config
        )
    output_path, report = ingest_to_canonical(
        args.source,
        output_path=args.output,
        task_family_hint=args.task_family,
        minimum_confidence=args.min_confidence,
        resolve=args.resolve,
        config=config,
    )
    print(f"Wrote canonical Parquet: {output_path}")
    print(f"Wrote inference report: {output_path.with_suffix('.ingest_report.json')}")
    print(f"Task family: {report.task_family}")
    print(f"Confidence: {report.confidence:.2f}")
    print(f"Columns mapped:")
    for canonical, source_col in report.column_mapping.items():
        infer = next(c for c in report.column_inferences if c.canonical_field == canonical)
        print(
            f"  {canonical:<22} <- {source_col!r:<30} "
            f"(name={infer.name_score:.2f}, value={infer.value_score:.2f})"
        )
    if report.warnings:
        print("Warnings:")
        for w in report.warnings:
            print(f"  - {w}")
    if report.confidence < args.min_confidence:
        print(
            f"\nWARNING: overall confidence {report.confidence:.2f} below threshold; "
            f"output may be incorrect.",
            file=sys.stderr,
        )
        return 3
    return 0


def cmd_uniref(args: argparse.Namespace) -> int:
    config = Config.discover(
        warehouse_root=args.warehouse_root,
        config_file=args.config,
    )
    warehouse = Warehouse(config)
    accs = args.accession or []
    if not accs:
        print("Pass at least one --accession.", file=sys.stderr)
        return 2
    records = warehouse.lookup_uniref(accs)
    out = {
        a: {
            "uniref100": records[a].uniref100 if a in records else None,
            "uniref90": records[a].uniref90 if a in records else None,
            "uniref50": records[a].uniref50 if a in records else None,
            "uniparc": records[a].uniparc if a in records else None,
            "taxon": records[a].taxon if a in records else None,
        }
        for a in accs
    }
    print(json.dumps(out, indent=2))
    return 0


def main() -> int:
    # Special-case ``overlap-scan`` BEFORE building the main argparse so the
    # subcommand's flags (e.g. --list-profiles, --input) don't collide with
    # the parent parser. We still honor the optional --warehouse-root /
    # --config global flags when they precede the subcommand name.
    argv = sys.argv[1:]
    # Special-case any subcommand whose flag set could collide with the
    # parent parser. Each gets its config resolved lazily so --help and
    # --list-* don't require a warehouse.
    #
    # ``overlap-scan`` is kept as a backward-compatible alias for
    # ``pairwise-checker`` so older scripts keep working.
    # `analyze-csv` is dispatched here too because its only inputs are
    # filesystem paths — it doesn't need a Config / Warehouse so we can
    # bypass the discover() call (which would otherwise fail when the
    # user is running it on a paper-rows CSV outside any warehouse).
    # The `_dispatch_analyze_csv` wrapper signature matches the other
    # special-case sub_mains: (sub_args, config=None) -> int.
    def _dispatch_analyze_csv(sub_args, config=None):  # noqa: ANN001
        from .csv_analyzer import analyze_csv_main
        return analyze_csv_main(sub_args)

    for sub_name, sub_main, help_only_flags in (
        ("pairwise-checker", overlap_cli.main,         {"--list-profiles"}),
        ("overlap-scan",     overlap_cli.main,         {"--list-profiles"}),  # alias
        ("overlap-cluster",  overlap_cluster_cli.main, {"--list-tiers"}),
        ("audit-split",      overlap_audit_cli.main,   set()),
        # CSV analyzer — no warehouse required, but use the same
        # special-case path so its flags don't collide with the parent
        # parser (e.g. --out is a common flag we want untouched).
        ("analyze-csv",      _dispatch_analyze_csv,    {"--help", "-h"}),
    ):
        pos = _find_subcommand(argv, sub_name)
        if pos is None:
            continue
        global_args, sub_args = argv[:pos], argv[pos + 1:]
        wh_root, cfg_path = _parse_global_flags(global_args)
        config = None
        if not (set(sub_args) & ({"--help", "-h"} | help_only_flags)):
            try:
                config = Config.discover(warehouse_root=wh_root, config_file=cfg_path)
            except FileNotFoundError:
                # Defer to sub_main, which will surface the same error
                # with more context (the user already provided --input).
                pass
        return sub_main(sub_args, config=config)
    return _main_with_subparsers()


def _find_subcommand(argv: list[str], target: str) -> int | None:
    """Find the index of a subcommand in argv, ignoring flag values."""
    skip_next = False
    for i, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if token in ("--warehouse-root", "--config"):
            skip_next = True
            continue
        if token.startswith("--warehouse-root=") or token.startswith("--config="):
            continue
        if token.startswith("-"):
            continue
        return i if token == target else None
    return None


def _parse_global_flags(argv: list[str]) -> tuple[str | None, str | None]:
    """Pull --warehouse-root / --config out of the prefix arguments."""
    wh_root: str | None = None
    cfg: str | None = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--warehouse-root" and i + 1 < len(argv):
            wh_root = argv[i + 1]; i += 2; continue
        if token.startswith("--warehouse-root="):
            wh_root = token.split("=", 1)[1]; i += 1; continue
        if token == "--config" and i + 1 < len(argv):
            cfg = argv[i + 1]; i += 2; continue
        if token.startswith("--config="):
            cfg = token.split("=", 1)[1]; i += 1; continue
        i += 1
    return wh_root, cfg


def _main_with_subparsers() -> int:
    parser = argparse.ArgumentParser(prog="python -m proteosphere", description=__doc__)
    parser.add_argument("--warehouse-root", default=None)
    parser.add_argument("--config", default=None)
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_status = subs.add_parser("status", help="Print resolved configuration paths.")
    p_status.set_defaults(func=cmd_status)

    p_smoke = subs.add_parser("smoke", help="Run end-to-end smoke test.")
    p_smoke.set_defaults(func=cmd_smoke)

    p_uniref = subs.add_parser("uniref", help="Look up UniRef clusters for one or more accessions.")
    p_uniref.add_argument("--accession", "-a", action="append", default=[], help="Accession (repeatable).")
    p_uniref.set_defaults(func=cmd_uniref)

    # ``overlap-scan`` is registered here so it appears in --help, but the
    # actual dispatch happens before this subparser via _find_subcommand
    # (so the overlap CLI sees its own flags untouched).
    subs.add_parser(
        "pairwise-checker",
        help="Score per-pair biological-similarity overlap from a CSV/JSON/XLSX.",
        add_help=False,
    )
    subs.add_parser(
        "overlap-scan",
        help="Alias for pairwise-checker (kept for backward compatibility).",
        add_help=False,
    )
    subs.add_parser(
        "overlap-cluster",
        help="Build a leakage-cluster manifest the splitter can constrain on.",
        add_help=False,
    )
    subs.add_parser(
        "audit-split",
        help="Audit an already-existing train/val/test (or k-fold) split for leakage.",
        add_help=False,
    )
    subs.add_parser(
        "analyze-csv",
        help=("Analyze a per-paper rows CSV from the manuscript procurement "
              "pipeline (overlap / leakage / split-classification findings)."),
        add_help=False,
    )

    p_ingest = subs.add_parser(
        "ingest",
        help="Convert a CSV/TSV/XLSX/JSON dataset to ProteoSphere canonical Parquet.",
    )
    p_ingest.add_argument("source", help="Path to input CSV/TSV/XLSX/JSON/Parquet file.")
    p_ingest.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output Parquet path. Defaults to <source>.canonical.parquet.",
    )
    p_ingest.add_argument(
        "--task-family",
        choices=("ppi", "pl"),
        default=None,
        help="Optional hint. Auto-detected if omitted.",
    )
    p_ingest.add_argument(
        "--min-confidence",
        type=float,
        default=0.4,
        help="Refuse to write if overall mapping confidence is below this (0..1).",
    )
    p_ingest.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the inferred mapping as JSON without writing the canonical file.",
    )
    p_ingest.add_argument(
        "--resolve",
        action="store_true",
        help=(
            "Promote alternate identifiers (raw sequences, gene names, PDB IDs) "
            "to canonical UniProt accessions using proteosphere.resolver. "
            "Requires the sequence_index and cross_references partitions."
        ),
    )
    p_ingest.set_defaults(func=cmd_ingest)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
