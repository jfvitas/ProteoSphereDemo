from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .evaluator import evaluate_dataset
from .io import read_json, write_json, write_text
from .model import DatasetManifest
from .paper import evaluate_paper_corpus
from .reports import render_paper_markdown, render_review_markdown
from .splitter import parse_fractions, split_dataset
from .validate import validate_library
from .warehouse import Warehouse


def _default_warehouse() -> Path:
    candidates = [
        Path.cwd() / "reference_library",
        Path(__file__).resolve().parents[3] / "reference_library",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return Path("reference_library")


def review_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review a dataset split manifest.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--warehouse", type=Path, default=_default_warehouse())
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    manifest = DatasetManifest.from_dict(read_json(args.manifest))
    warehouse = Warehouse.open(args.warehouse)
    report = evaluate_dataset(manifest, warehouse)
    write_json(args.out, report)
    if args.markdown:
        write_text(args.markdown, render_review_markdown(report))
    print(f"verdict={report['verdict']} reason_codes={','.join(report['reason_codes'])}")
    return 0 if report["verdict"] != "blocked_pending_mapping" else 2


def split_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a ProteoSphere grouped split.")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--warehouse", type=Path, default=_default_warehouse())
    parser.add_argument("--policy", required=True)
    parser.add_argument("--fractions", default="0.8,0.1,0.1")
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--resplit", action="store_true")
    parser.add_argument(
        "--leakage-manifest",
        type=Path,
        default=None,
        help=(
            "Optional path to a leakage-cluster JSON manifest produced by "
            "`proteosphere overlap-cluster`. When provided, the splitter "
            "treats clustered accessions as a single split-time unit on "
            "top of whatever the policy already enforces."
        ),
    )
    parser.add_argument(
        "--kfold",
        type=int,
        default=None,
        help=(
            "When set (>=2), produce a k-fold cross-validation split instead "
            "of a single train/val/test split. Records get split labels "
            "`fold_0`, `fold_1`, ... `fold_{k-1}`. --fractions is ignored "
            "in k-fold mode. Leakage clusters (and the policy's group keys) "
            "are still respected: whole groups always land in a single fold."
        ),
    )
    parser.add_argument(
        "--row-id-column",
        default=None,
        help=(
            "Use a per-row identifier (e.g. SKEMPI's 'Complex ID') as the "
            "group key instead of the policy's accession-based keys. Each "
            "row becomes its own group unless a leakage manifest bridges it "
            "to others. Useful for mutation-prediction benchmarks where the "
            "same PDB legitimately appears across train and test with "
            "different mutations. The column is looked up first as a "
            "DatasetRecord attribute, then in extra_metadata (case-insensitive)."
        ),
    )
    parser.add_argument(
        "--subgroup-column",
        default=None,
        help=(
            "Honour --fractions independently within each value of this "
            "column. Useful for stratified splits across subgroups like "
            "PPB-Affinity's 'Antibody-Antigen' / 'TCR-pMHC' / general PPI. "
            "Mixed-subgroup leakage clusters are assigned to their majority "
            "subgroup with a warning."
        ),
    )
    args = parser.parse_args(argv)
    manifest = DatasetManifest.from_dict(read_json(args.manifest))
    warehouse = Warehouse.open(args.warehouse)
    result = split_dataset(
        manifest,
        warehouse,
        policy=args.policy,
        fractions=parse_fractions(args.fractions),
        seed=args.seed,
        resplit=args.resplit,
        leakage_manifest_path=args.leakage_manifest,
        kfold=args.kfold,
        row_id_column=args.row_id_column,
        subgroup_column=args.subgroup_column,
    )
    if result.get("status") != "ready":
        write_json(args.out, result)
        print(f"status={result.get('status')} blockers={len(result.get('blockers') or [])}")
        return 2
    write_json(args.out, result["manifest"])
    if args.diagnostics:
        write_json(args.diagnostics, result["diagnostics"])
    counts = result["diagnostics"]["split_counts"]
    summary = " ".join(f"{key}={value}" for key, value in counts.items())
    mode = "kfold" if result["diagnostics"].get("kfold") else "split"
    print(f"status=ready mode={mode} {summary}")
    return 0


def paper_review_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review paper-described split claims.")
    parser.add_argument("--papers", required=True, type=Path)
    parser.add_argument("--warehouse", type=Path, default=_default_warehouse())
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    warehouse = Warehouse.open(args.warehouse)
    report = evaluate_paper_corpus(read_json(args.papers), warehouse)
    write_json(args.out, report)
    if args.markdown:
        write_text(args.markdown, render_paper_markdown(report))
    print(f"paper_count={report['paper_count']}")
    return 0


def validate_library_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a relocated ProteoSphere reference library.")
    parser.add_argument("--warehouse", type=Path, default=_default_warehouse())
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    report = validate_library(args.warehouse)
    write_json(args.report, report)
    print(f"status={report['status']} file_count={report['file_count']} byte_count={report['byte_count']}")
    return 0 if report["status"] in {"passed", "degraded", "metadata_only"} else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProteoSphere release app")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("review")
    sub.add_parser("split")
    sub.add_parser("paper-review")
    sub.add_parser("validate-library")
    args, remaining = parser.parse_known_args(argv)
    if args.command == "review":
        return review_main(remaining)
    if args.command == "split":
        return split_main(remaining)
    if args.command == "paper-review":
        return paper_review_main(remaining)
    if args.command == "validate-library":
        return validate_library_main(remaining)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
