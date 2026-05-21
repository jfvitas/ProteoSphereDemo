# ProteoSphere Dataset Reviewer

This is the freestanding release-facing ProteoSphere app. It reviews user-supplied
train/validation/test manifests against a local ProteoSphere reference library and
can generate leakage-resistant split manifests.

The app is intentionally small: standard Python plus DuckDB. It does not import
the larger Model Studio runtime.

## Main Commands

```powershell
proteosphere-validate-library --warehouse .\reference_library --report validation.json
proteosphere-review --manifest .\examples\protein_pair_overlap.json --warehouse .\reference_library --out report.json --markdown report.md
proteosphere-split --manifest .\examples\clean_protein_pair.json --policy accession_grouped --fractions 0.8,0.1,0.1 --seed 1337 --out split_manifest.json
proteosphere-paper-review --papers .\examples\paper_corpus_minimal.json --warehouse .\reference_library --out papers.json --markdown papers.md
```

## Dataset Manifest

Manifests are JSON objects with `manifest_id`, `entity_kind`, and `records`.
Supported `entity_kind` values are:

- `protein_pair`
- `protein_ligand`
- `structure_pair`

Records may already contain `split` values (`train`, `val`, `test`) for review.
For splitting, omit `split` or use `proteosphere-split --resplit`.

See `schemas/` and `examples/` in the bundle for exact formats.
