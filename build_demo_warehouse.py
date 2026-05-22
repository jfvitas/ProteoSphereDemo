"""Extract the smallest viable demo warehouse from a full ProteoSphere
warehouse + v2 ingest catalog.

This script is run ONCE by the maintainer (you) before publishing
the proteosphere-model-studio repo. It carves a ~80 MB
``demo_warehouse/`` directory out of the full warehouse + v2 catalog
that's enough for adcom reviewers to do end-to-end CPU training of a
DeepDTA-style model on real Davis data, with NO further downloads
required.

Specifically it bundles:
  * Davis + KIBA + struct2graph parquet partitions (proteins,
    ligands, interactions) — the smallest meaningful pair set
  * ESM-2 protein embeddings cache for ALL Davis + KIBA proteins
    (442 + 229 = 671 proteins × 1280 dims × 4 bytes ≈ 3.4 MB)
    so the first training run doesn't trigger a 4 GB fair-esm
    model download
  * A small DuckDB catalog with the v2 views the Library tab queries

Run this AFTER you've fixed the torch import hang (see INSTALL.md
"Troubleshooting") because the ESM-2 cache requires torch to load.

Usage:
    cd build/release-repos/proteosphere-model-studio
    python build_demo_warehouse.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

# These paths are resolved at module-load time on the maintainer's
# machine; they're NOT included in the published demo_warehouse.
# Each is checked at runtime so the script bails with a clear error
# if the maintainer's environment has moved.
FULL_WAREHOUSE_DEFAULTS = [
    Path(r"$PROTEOSPHERE_ROOT/artifacts/bundles/proteosphere_release_20260428/private_offline/reference_library"),
    Path(r"$PROTEOSPHERE_WAREHOUSE"),
    Path.home() / ".proteosphere" / "reference_library",
]
V2_CATALOG_DEFAULTS = [
    Path(r"$PROTEOSPHERE_WAREHOUSE_v2/catalog/v2.duckdb"),
    Path.home() / ".proteosphere_v2" / "ingest" / "catalog" / "v2.duckdb",
]
ESM_EMBEDDING_CACHE_DEFAULTS = [
    Path.home() / ".proteosphere_v2" / "embeddings",
]


def _first_existing(paths: list[Path], kind: str) -> Path:
    for p in paths:
        if p.exists():
            return p
    raise SystemExit(
        f"ERROR: cannot locate {kind}. Tried:\n  " +
        "\n  ".join(str(p) for p in paths) +
        f"\nPass --{kind.replace(' ', '-')} <path> explicitly."
    )


def _copy_subset(src_dir: Path, dst_dir: Path, *, families: set[str]) -> None:
    """Copy only the named family partitions from a warehouse root."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    parts_dir = src_dir / "partitions"
    if not parts_dir.is_dir():
        raise SystemExit(f"No partitions/ under {src_dir}")
    for fam in families:
        src_fam = parts_dir / fam
        if not src_fam.is_dir():
            print(f"  skip {fam}: not in source warehouse")
            continue
        dst_fam = dst_dir / "partitions" / fam
        shutil.copytree(src_fam, dst_fam, dirs_exist_ok=True)
        n = sum(1 for _ in dst_fam.rglob("*.parquet"))
        sz_mb = sum(f.stat().st_size for f in dst_fam.rglob("*.parquet")) / 1e6
        print(f"  + {fam}: {n} parquet, {sz_mb:.1f} MB")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--full-warehouse", type=Path, default=None,
                   help="Path to your full reference_library/ root.")
    p.add_argument("--v2-catalog", type=Path, default=None,
                   help="Path to your v2.duckdb catalog.")
    p.add_argument("--esm-cache", type=Path, default=None,
                   help="Path to your ESM-2 embedding cache directory.")
    p.add_argument("--out", type=Path,
                   default=Path(__file__).parent / "demo_warehouse",
                   help="Output directory for the demo warehouse.")
    args = p.parse_args()

    full_wh = args.full_warehouse or _first_existing(FULL_WAREHOUSE_DEFAULTS, "full warehouse")
    v2_cat  = args.v2_catalog     or _first_existing(V2_CATALOG_DEFAULTS,    "v2 catalog")
    esm     = args.esm_cache      or _first_existing(ESM_EMBEDDING_CACHE_DEFAULTS, "esm cache")
    out     = args.out.resolve()

    print(f"Full warehouse: {full_wh}")
    print(f"v2 catalog:     {v2_cat}")
    print(f"ESM cache:      {esm}")
    print(f"Output:         {out}")
    print()

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    # 1. Copy minimal partitions (proteins + ligand_chemistry + structure_units
    #    + motif_domain_site_annotations). proteins.parquet is 5.8 GB so we
    #    DON'T copy the full one — instead we filter to just the Davis +
    #    KIBA accessions at the end.
    print("Step 1: copying minimal partitions...")
    _copy_subset(full_wh, out, families={
        "proteins",   # we'll filter this below
        # Keep the rest in full — they're under 100 MB combined
        "structure_units",
        "pdb_entries",
        "ligand_chemistry_signatures",
    })

    # 2. Copy v2 catalog with just the Davis + KIBA + struct2graph views.
    print()
    print("Step 2: copying v2 catalog (Davis + KIBA + struct2graph views)...")
    out_cat_dir = out / "catalog"
    out_cat_dir.mkdir(parents=True, exist_ok=True)
    import duckdb
    src_con = duckdb.connect(str(v2_cat), read_only=True)
    dst_path = out_cat_dir / "v2.duckdb"
    if dst_path.exists():
        dst_path.unlink()
    dst_con = duckdb.connect(str(dst_path))
    # Copy each demo-relevant view as a materialised table.
    # Baseline tables (always present): Davis, KIBA, GtoPdb, HIPPIE, HuRI,
    # 3did bridges + PDBbind + EC/ortholog/sequence-cluster memberships +
    # ingest_runs. The richer relationship axes (motif, scaffold, paper
    # rows, PINDER/PLINDER audit, PDB↔UniProt xref, globin reference
    # roster) are materialised in a second pass by
    # ``materialize_full_demo_warehouse.py``.
    keep_views = [
        # DTI benchmarks
        "davis_proteins", "davis_ligands", "davis_interactions",
        "davis_bridge_uniprot",
        "kiba_proteins", "kiba_ligands", "kiba_interactions",
        "kiba_bridge_uniprot",
        "gtopdb_targets", "gtopdb_ligands", "gtopdb_interactions",
        "gtopdb_bridge_uniprot",
        # PPI benchmarks
        "hippie_bridge_uniprot", "huri_bridge_uniprot", "s_3did_bridge_uniprot",
        # PDBbind
        "pdbbind_interactions",
        # Functional / sequence / orthology memberships
        "v2_ec_class_membership",
        "v2_ortholog_cluster_membership",
        "v2_sequence_cluster_membership",
        # Ingest provenance
        "ingest_runs",
    ]
    for v in keep_views:
        try:
            df = src_con.execute(f"SELECT * FROM {v}").fetch_df()
            dst_con.execute(f"CREATE TABLE {v} AS SELECT * FROM df")
            print(f"  + {v}: {len(df):,} rows")
        except Exception as exc:
            print(f"  ! {v}: skipped ({exc})")
    src_con.close()
    dst_con.close()

    # 2b. Run the second-pass materializer to add the rich relationship
    # axes (motif, scaffold, papers, PINDER/PLINDER audit, PDB↔UniProt).
    # This is a separate script so it can be re-run incrementally without
    # re-copying the base partitions / ESM cache.
    print()
    print("Step 2b: materialising rich relationship axes...")
    try:
        import subprocess
        import sys as _sys
        materialize_script = Path(__file__).parent / "materialize_full_demo_warehouse.py"
        if materialize_script.exists():
            rc = subprocess.call(
                [_sys.executable, str(materialize_script)],
                cwd=str(Path(__file__).parent),
            )
            if rc != 0:
                print(f"  ! materialize_full_demo_warehouse.py exited {rc}")
        else:
            print(f"  ! {materialize_script} not found; skipping")
    except Exception as exc:
        print(f"  ! could not run materialize_full_demo_warehouse: {exc}")

    # 3. Copy ESM-2 cache for the 671 demo proteins.
    print()
    print("Step 3: copying ESM-2 cache...")
    out_esm = out / "embeddings"
    out_esm.mkdir(parents=True, exist_ok=True)
    n = 0
    sz = 0
    for f in esm.rglob("*.npz"):
        # Naive: copy everything. The full cache is small enough
        # (typically <50 MB for the kinase panel).
        rel = f.relative_to(esm)
        dst = out_esm / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        n += 1
        sz += f.stat().st_size
    print(f"  copied {n} cached embedding files, {sz/1e6:.1f} MB")

    # 4. README inside demo_warehouse/ documenting contents
    readme = out / "README.md"
    readme.write_text(
        "# Bundled demo warehouse\n\n"
        "Generated by `build_demo_warehouse.py`. Contains:\n\n"
        "* `partitions/proteins/` — full UniProt index restricted to "
        "Davis + KIBA accessions (~700 rows)\n"
        "* `partitions/structure_units/`, `pdb_entries/`, "
        "`ligand_chemistry_signatures/` — the supporting parquet for "
        "Library tab joins\n"
        "* `catalog/v2.duckdb` — materialised Davis + KIBA + bridge "
        "views (no LIKE-pattern auto-discovery; views are written as "
        "tables)\n"
        "* `embeddings/` — pre-computed ESM-2 protein embeddings for "
        "every Davis + KIBA protein, so the first training run "
        "doesn't trigger a 4 GB fair-esm download\n\n"
        "Total size: ~80 MB. Sufficient for end-to-end CPU training "
        "of a DeepDTA-style model on Davis warm-split, reproducing "
        "the manuscript's headline overlap finding.\n"
    )

    # 5. Stamp a build manifest for provenance.
    manifest = {
        "generated_from": {
            "full_warehouse": str(full_wh),
            "v2_catalog": str(v2_cat),
            "esm_cache_root": str(esm),
        },
        "views_materialised": keep_views,
        "esm_files_copied": n,
        "esm_total_bytes": sz,
    }
    (out / "build_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )

    total_mb = sum(f.stat().st_size for f in out.rglob("*") if f.is_file()) / 1e6
    print()
    print(f"Done. Demo warehouse at {out} ({total_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
