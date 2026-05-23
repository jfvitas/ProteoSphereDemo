#!/usr/bin/env python3
"""Extension-shard CLI for ProteoSphere warehouse.

The core warehouse at demo_warehouse/catalog/v2.duckdb is intentionally kept
small (<500 MB) so it bundles cleanly with the code via Git LFS. Tier-2-class
data (full TrEMBL, full UniRef, AFDB metadata at 200M scale, Foldseek index)
lives in separate "extension shards" under v2_extensions/ that are downloaded
on demand from a content-addressed mirror.

Usage:
  python proteosphere_extensions.py list
  python proteosphere_extensions.py status
  python proteosphere_extensions.py download <shard_name> [...]
  python proteosphere_extensions.py download all
  python proteosphere_extensions.py verify <shard_name>
  python proteosphere_extensions.py manifest > extensions_manifest.json

Once downloaded, ATTACH them in DuckDB for federated queries:

  ATTACH 'demo_warehouse/catalog/v2_extensions/trembl.duckdb' AS trembl (READ_ONLY);
  SELECT count(*) FROM trembl.trembl_motif_membership WHERE identifier='PF00069';

The mirror URL is read from $PROTEOSPHERE_MIRROR (defaults to a placeholder).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin
import urllib.request

REPO = Path(__file__).resolve().parent
EXT_DIR = REPO / "demo_warehouse" / "catalog" / "v2_extensions"
MANIFEST = REPO / "demo_warehouse" / "extensions_manifest.json"

DEFAULT_MIRROR = os.environ.get(
    "PROTEOSPHERE_MIRROR",
    "https://placeholder.example.com/proteosphere/v1/extensions/",
)

# Canonical shard registry. Updated by the materializer scripts as they
# produce new shards. Sizes are estimates until built.
SHARDS = {
    "trembl": {
        "purpose": "TrEMBL DR cross-refs (~250M unreviewed proteins): Pfam, "
                   "InterPro, OrthoDB, EC, PDB cross-references",
        "tables": ["trembl_protein_entry", "trembl_motif_membership",
                   "trembl_ortholog", "trembl_ec", "trembl_pdb"],
        "estimated_size_gb": 25,
        "license": "CC-BY 4.0 (UniProt)",
        "source": "https://ftp.uniprot.org/pub/databases/uniprot/current_release/"
                  "knowledgebase/complete/uniprot_trembl.xml.gz",
        "materializer": "materialize_trembl_dr.py",
    },
    "uniref_full": {
        "purpose": "Full UniRef50/90/100 cluster memberships across all UniProt",
        "tables": ["uniref_cluster_member", "uniref_cluster_meta"],
        "estimated_size_gb": 25,
        "license": "CC-BY 4.0 (UniProt)",
        "source": "https://ftp.uniprot.org/pub/databases/uniprot/uniref/uniref50/"
                  "uniref50.xml.gz",
        "materializer": "materialize_uniref_full.py",
    },
    "alphafold_full": {
        "purpose": "AFDB metadata + per-model pLDDT summary for all ~200M "
                   "predicted structures",
        "tables": ["afdb_model"],
        "estimated_size_gb": 3,
        "license": "CC-BY 4.0 (AlphaFold DB)",
        "source": "https://ftp.ebi.ac.uk/pub/databases/alphafold/v4/",
        "materializer": "materialize_afdb_full.py",
    },
    "foldseek_swissprot": {
        "purpose": "Foldseek 3Di-tokenized AFDB-Swiss-Prot model index for "
                   "structural similarity search",
        "tables": ["foldseek_3di"],
        "estimated_size_gb": 20,
        "license": "CC-BY 4.0 (Foldseek/AFDB)",
        "source": "https://foldseek.steineggerlab.workers.dev/afdb-swissprot.tar.zst",
        "materializer": "materialize_foldseek_3di.py",
    },
    "scop_cath": {
        "purpose": "SCOPe + CATH fold-level structural classifications",
        "tables": ["scop_membership", "cath_membership"],
        "estimated_size_gb": 2,
        "license": "CC-BY 4.0 (SCOPe / CATH)",
        "source": "multiple",
        "materializer": "materialize_scop_cath.py",
    },
}


def cmd_list(_):
    print("Available extension shards:")
    for name, meta in SHARDS.items():
        size = "?" if meta["estimated_size_gb"] is None else f"{meta['estimated_size_gb']} GB"
        print(f"  {name:<20} ({size:>5})   {meta['purpose']}")


def cmd_status(_):
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Extensions directory: {EXT_DIR}")
    for name in SHARDS:
        path = EXT_DIR / f"{name}.duckdb"
        if path.exists():
            mb = path.stat().st_size / (1024**2)
            print(f"  [OK]  {name:<20} {mb:.0f} MB at {path.name}")
        else:
            print(f"  [--]  {name:<20} not downloaded")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_download(args):
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    mirror = args.mirror or DEFAULT_MIRROR
    targets = args.shard
    if targets == ["all"]:
        targets = list(SHARDS)
    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}
    for name in targets:
        if name not in SHARDS:
            print(f"  unknown shard '{name}', skipping")
            continue
        dest = EXT_DIR / f"{name}.duckdb"
        if dest.exists() and not args.force:
            print(f"  {name}: already present, skipping (use --force to redownload)")
            continue
        url = urljoin(mirror, f"{name}.duckdb")
        print(f"  downloading {name} from {url}")
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as e:
            print(f"    FAILED ({e})")
            continue
        h = _sha256(dest)
        expected = manifest.get(name, {}).get("sha256")
        if expected and expected != h:
            print(f"    sha256 mismatch! expected {expected}, got {h}")
            print(f"    removing the corrupt download")
            dest.unlink()
            continue
        sz = dest.stat().st_size / (1024**2)
        print(f"    OK ({sz:.1f} MB, sha256 {h[:12]}…)")


def cmd_verify(args):
    if not MANIFEST.exists():
        print("  no manifest at extensions_manifest.json — nothing to verify against")
        return
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    targets = args.shard or list(SHARDS)
    if targets == ["all"]:
        targets = list(SHARDS)
    for name in targets:
        dest = EXT_DIR / f"{name}.duckdb"
        if not dest.exists():
            print(f"  {name}: missing")
            continue
        h = _sha256(dest)
        expected = manifest.get(name, {}).get("sha256")
        ok = (h == expected) if expected else None
        flag = "OK" if ok is True else ("MISSING_MANIFEST" if ok is None else "MISMATCH")
        print(f"  {name}: {flag}  sha256={h[:12]}…  expected={(expected or '-')[:12]}…")


def cmd_manifest(_):
    """Compute SHA256 for any locally-built shards and emit manifest JSON."""
    manifest = {}
    if MANIFEST.exists():
        try:
            manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            pass
    for name, meta in SHARDS.items():
        path = EXT_DIR / f"{name}.duckdb"
        if not path.exists():
            continue
        h = _sha256(path)
        manifest[name] = {
            "purpose": meta["purpose"],
            "tables": meta["tables"],
            "license": meta["license"],
            "source": meta["source"],
            "materializer": meta["materializer"],
            "size_bytes": path.stat().st_size,
            "sha256": h,
        }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {MANIFEST}")
    print(json.dumps(manifest, indent=2, sort_keys=True))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    sub.add_parser("status").set_defaults(func=cmd_status)
    dl = sub.add_parser("download")
    dl.add_argument("shard", nargs="+")
    dl.add_argument("--mirror")
    dl.add_argument("--force", action="store_true")
    dl.set_defaults(func=cmd_download)
    vf = sub.add_parser("verify")
    vf.add_argument("shard", nargs="*")
    vf.set_defaults(func=cmd_verify)
    sub.add_parser("manifest").set_defaults(func=cmd_manifest)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
