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

## Try the simulator (no install required)

Want to see what the Model Studio looks like without installing
anything? Download **`ProteoSphereDemo_Simulation.html`** from this
repo, double-click it. Full studio opens in your browser — all
data faked client-side, but every screen is interactive and the
training run plays back realistic curves seeded by the seed you
pick on the Pipeline tab.

## Quickstart (Windows) — 4 steps

> **You need Python 3.10+ installed** (check with `python --version`).
> If the launcher can't find Python on `PATH` or at
> `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`, it will:
>   1. Offer to `winget install Python.Python.3.12` for you (one-key
>      consent prompt — no silent install), or
>   2. If `winget` isn't available, open
>      https://python.org/downloads/windows/ in your browser — pick the
>      latest Python 3.x installer and tick **"Add Python to PATH"** at
>      the top of the installer dialog before clicking Install.
>
> The first `pip install -r requirements.txt` pulls down CPU-only
> torch (~700 MB), torch-geometric, RDKit, DuckDB, and pyarrow —
> ~3–4 minutes on a normal connection. Subsequent launches use the
> already-installed packages.

```cmd
REM 1. Clone (or unzip from "Code -> Download ZIP")
git clone https://github.com/jfvitas/ProteoSphereDemo.git
cd ProteoSphereDemo

REM 2. Install Python dependencies (~3 minutes)
pip install -r requirements.txt

REM 3. RIGHT-CLICK setup_windows_defender.bat -> "Run as administrator"
REM    This adds Defender exclusions for python.exe + torch + nvidia
REM    so the server boots in <5 seconds instead of being held up
REM    for minutes by real-time antivirus scanning. One-time only.

REM 4. Launch the GUI:
launch_model_studio.bat
```

The launcher will:

1. Kill any prior listener on port 8765
2. Filesystem-check that duckdb/pyarrow/torch are installed (no
   `python.exe` probe — that path used to hang on machines where
   Defender held python imports)
3. Start the slim HTTP server in this terminal
4. **Poll for the port to actually bind**, then open the browser to
   `http://127.0.0.1:8765/v2/` only once the server is ready (no
   more "site can't be reached" because the browser opened too early)

If the server takes longer than 60 seconds to bind, it's almost
certainly Defender holding torch — run `setup_windows_defender.bat`
as administrator and try again.

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

### Running on a low-end machine (laptop, no GPU)

On a 4-core CPU laptop with 8 GB RAM and no GPU, the defaults (5
epochs × ~470 batches × 64 batch-size) take ~10–15 minutes wall-clock.
For a quicker first-look demo, three knobs help (combine them):

- **`subsample_train_frac`** in Pipeline → Advanced. Set to `0.2` and
  the trainer randomly keeps 20% of the training records (val + test
  stay full so the reported metrics are still meaningful). Linear
  wall-clock speedup per epoch.
- Drop **epochs** from 5 to **2** and **batch_size** from 64 to
  **32** — combined with the 20% subsample this finishes in
  **under a minute on a 4-core CPU laptop**.
- The trainer now auto-pins `torch.set_num_threads(os.cpu_count())`
  on CPU-only runs (PyTorch defaults to 1 thread on Windows, leaving
  most cores idle). You'll see a `CPU-only training: pinned torch
  to N intra-op threads…` log line at the start of every run.
- Watch the Training tab logs: you should see
  `[embeddings] N/700 resolved (cache_hits=N, ...)` heartbeats every
  few seconds during the embedding-prefetch step (~5 s total when the
  bundled cache resolves; a few minutes if it has to call fair-esm on
  CPU). If you see `computed=N` climbing instead of `cache_hits`, your
  `PROTEOSPHERE_V2_EMBEDDINGS` env var didn't pick up the bundled
  cache — see the troubleshooting note below.
- Then the per-epoch `batch` events fire ~20 times per epoch so the
  loss curve paints smoothly instead of jumping.

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
