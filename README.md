# ProteoSphere Model Studio

**A local-first web app for exploring a benchmark warehouse, building
flow-based ML pipelines for drug-target affinity / protein-protein
interaction prediction, and re-training models with leakage-aware
splits.**

This is the operational tooling around the analyses described in the
companion repository
[**proteosphere-paper**](https://github.com/<your-handle>/proteosphere-paper).
The paper repo is the credible scientific entry point; this repo is
where you go after reading the paper if you want to *use* the same
splitter / analyzer / training pipeline interactively.

## What you get

- **Reference Library tab** — paginated browse over the local v2
  catalog: proteins (~57k UniProt accessions across HIPPIE / HuRI /
  GtoPdb / Davis / KIBA / 3did), ligands, binding pairs, structures,
  motifs, leakage groups, sources, releases. Live search punches
  through to the full ProteoSphere warehouse partition (262M UniProt)
  when mounted.
- **Splits tab** — interactive leakage-aware split designer: pick a
  policy, see the train/val/test cluster map, manually override
  cluster assignments, get an immediate verdict.
- **Pipeline tab** — flow-based ML model builder. Drag protein/ligand
  inputs → encoders → fusion → head into a DAG; the compiler emits a
  trainable `nn.Module` with auto-routing for common mistakes (ESM-2
  on the wrong input, bilinear fusion with N≠2 operands).
- **Training tab** — SSE event stream of live train/val curves, GPU
  stats, pattern-detector insights ("loss has plateaued for 3 epochs,
  consider early-stopping"), real torch optimizer running with the
  configured seed.
- **Results / Compare / Promote** — real metrics from the run
  registry; honest empty states when nothing's attached (no fixture
  fallbacks — see `LIMITATIONS.md` if you remember the "results always
  identical" issue).

## Quickstart (Windows)

```cmd
git clone https://github.com/<your-handle>/proteosphere-model-studio.git
cd proteosphere-model-studio
launch_model_studio.bat
```

The launcher will:

1. Check for prior listeners on port 8765 and kill them if found
   (the dual-process issue documented in `docs/ARCHITECTURE.md`)
2. Find a Python 3.10+ interpreter
3. Start the slim HTTP server in this terminal
4. Open `http://127.0.0.1:8765/v2/` in your default browser

First boot includes a one-time torch warmup (~10 seconds). Subsequent
launches are <2 seconds.

## Quickstart (macOS/Linux)

```bash
git clone https://github.com/<your-handle>/proteosphere-model-studio.git
cd proteosphere-model-studio
./launch_model_studio.sh
```

## What's in the demo

The `demo_warehouse/` directory contains the smallest viable warehouse:
Davis (442 kinases × 68 drugs = 30,056 pairs) + KIBA (229 proteins ×
2,111 drugs, sparse) + Struct2Graph public PPI pairs (~14k). Total
~80 MB. A full ProteoSphere warehouse with all the relationship axes
the Library tab can surface is several GB and isn't bundled — see
[`LIMITATIONS.md`](LIMITATIONS.md) for what you'd need to mount.

The demo lets you do real end-to-end runs:

1. Open the Library tab — see ~700 proteins, ~2,200 ligands, ~144k
   binding pairs across Davis + KIBA
2. Open Splits → pick `leakage-aware`, hit Apply
3. Open Pipeline → pick "DeepDTA template" → click "Randomize" on the
   seed banner → click Launch
4. Open Training → watch the SSE event stream paint real train/val
   curves from a real PyTorch run on real Davis data
5. After ~3 minutes (CPU) / ~30 seconds (GPU): Results tab shows the
   actual Pearson, RMSE, R², AUC at pKi=6 for the trained checkpoint

## What's not in the demo

- The full ProteoSphere warehouse (262M UniProt, 5.7M motif
  annotations, 5.8M ligand chemistry signatures) — too large to ship
  and parts are under licensed redistribution. Set
  `PROTEOSPHERE_WAREHOUSE=<path>` to point at a locally mounted full
  warehouse if you have one.
- ESM-2 protein embeddings beyond the bundled Davis + KIBA subset.
  Generating embeddings for new sequences requires `fair-esm` (~4 GB
  model download) — see `docs/ARCHITECTURE.md`.
- HIPPIE / HuRI / GtoPdb / 3did ingest. The full v2 catalog is in
  the source repo but not the demo warehouse.

## License

Apache-2.0 — see [LICENSE](LICENSE).

## Limitations

See [LIMITATIONS.md](LIMITATIONS.md) — the honest accounting of what's
real, what's "marked planned in the GUI but actually planned not
broken", and what's fundamentally local-demo-only.

## Cite the analysis this tool is built around

```bibtex
@article{proteosphere_2026,
  title  = {Hidden train/test leakage in published drug-target interaction benchmarks},
  year   = {2026},
  note   = {Preprint, in submission. See companion repo proteosphere-paper.}
}
```
