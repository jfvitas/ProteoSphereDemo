# Install + smoke-test ProteoSphere Model Studio

## Prerequisites

- **Python 3.10+** (3.12 recommended; the project is tested on 3.12)
- **~3 GB free disk** (for the CPU-only torch + GNN dependencies +
  bundled demo warehouse)
- **No NVIDIA driver or CUDA required** — `requirements.txt` pins the
  CPU-only torch build. If you have a GPU and want to use it, see the
  comment in `requirements.txt`.

## Install (any OS)

```bash
git clone https://github.com/<your-handle>/proteosphere-model-studio.git
cd proteosphere-model-studio
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

Expected time: ~3–4 minutes on a normal connection (most of it is
torch + torch-geometric).

## Launch

### Windows
```cmd
launch_model_studio.bat
```

### macOS/Linux
```bash
./launch_model_studio.sh
```

The launcher will:

1. Detect and kill any prior server on port 8765 (so a second double-
   click never leaves you with two listeners — see
   `docs/ARCHITECTURE.md`)
2. Start the slim HTTP server (~2 sec boot, no torch loaded yet)
3. Open `http://127.0.0.1:8765/v2/` in your default browser

## Smoke-test that training actually works

Once the GUI is up:

1. **Library tab** — should populate within ~5s with ~700 proteins
   from the bundled demo warehouse (Davis + KIBA kinase panels).
   The "live · warehouse" green chip confirms it's reading real
   DuckDB views, not fixtures.

2. **Pipeline tab** — select template "DeepDTA" from the dropdown.
   Confirm:
   - Seed banner shows `⚠ DEFAULT SEED (4192)` in red — click
     **Randomize** to get a fresh 9-digit seed
   - Hyperparameters (LR=3e-4, batch=64, epochs=**5** for quick demo)
   - Source picker shows `davis` selected

3. **Click Launch.** The first training run takes ~30s extra to
   warm torch (Defender scans the .pyd files); subsequent launches
   are <2s to start.

4. **Training tab** — within ~30s you should see:
   - Status chip "running" with a live counter
   - Train + val loss curves painting in real time via SSE
   - GPU panel showing "CPU only" + actual CPU% usage
   - Each epoch prints ~1–2 min on a typical laptop CPU (Davis is
     30k pairs × 64 batch ≈ 470 batches/epoch with a small DeepDTA-
     style CNN tower)

5. **After ~6–10 minutes** (5 epochs on CPU): the run completes and
   the Results tab activates with real Pearson, Spearman, RMSE, R².
   Compare to the manuscript's published number for DeepDTA-on-Davis:
   you should land within ±0.05 Pearson of the reported value.

### Command-line smoke test (no GUI required)

If you'd rather verify training works without opening the browser
— the server boots the same API the GUI uses, so curl gets you the
exact same training pipeline:

```bash
# 1. Start the server in one terminal:
./launch_model_studio.sh         # macOS/Linux
# or
launch_model_studio.bat          # Windows

# 2. In another terminal, fire a 3-epoch Davis run:
curl -X POST http://127.0.0.1:8765/api/v2/pipeline/launch \
    -H "Content-Type: application/json" \
    -d '{"template_id":"deepdta","effective_config":{"benchmark":"davis","split_policy":"random"},"hparams":{"epochs":3,"batch_size":64,"lr":0.0003,"seed":0}}'

# Response: {"run_id":"run_v2_abc123...", "status":"queued"}

# 3. Stream the live events:
curl -N http://127.0.0.1:8765/api/v2/pipeline/runs/run_v2_abc123/stream

# 4. After training completes (~4 min CPU / ~30 sec GPU), fetch results:
curl http://127.0.0.1:8765/api/v2/pipeline/runs/run_v2_abc123/results | python -m json.tool
```

The JSON response contains real test-set Pearson, Spearman, RMSE,
R², AUC at pKi=6 from a PyTorch model trained on the actual Davis
data — same numbers the GUI's Results tab would show.

## Troubleshooting

### "import torch" hangs forever on Windows

Almost certainly Windows Defender real-time scanning torch's ~700
.pyd files. Fix once:

```powershell
# Run as Administrator:
Add-MpPreference -ExclusionPath "$(python -c 'import torch, os; print(os.path.dirname(torch.__file__))')"
Add-MpPreference -ExclusionProcess "python.exe"
```

After that, fresh python processes import torch in ~2s.

### "RuntimeError: torch not compiled with CUDA enabled"

You installed the CPU-only torch (the default) but a template tried
to use a GPU. Either:

- Set `use_cuda: false` in the Pipeline hyperparameters (Advanced
  panel), or
- Install the CUDA build of torch (see `requirements.txt` comment)

### "No run attached" on Results tab

This is the honest empty state, not a bug. The Results tab refuses
to show fixture data when no real run is attached. Go back to
Pipeline → click Launch → wait for Training to complete, then come
back.

### Library tab shows "preview · fixtures" instead of "live · warehouse"

The DuckDB catalog at `demo_warehouse/catalog/v2.duckdb` couldn't
open. Most likely the demo_warehouse wasn't unpacked — check
that file exists. If you have your own warehouse:

```bash
export PROTEOSPHERE_WAREHOUSE=/path/to/your/reference_library
./launch_model_studio.sh   # or .bat
```

## What the demo warehouse contains

| Source | Rows | Notes |
|---|---|---|
| `davis_proteins` | 442 | Davis kinase panel, full sequences |
| `davis_ligands` | 68 | Davis drugs with SMILES |
| `davis_interactions` | 30,056 | Full warm-start matrix |
| `kiba_proteins` | 229 | KIBA panel |
| `kiba_ligands` | 2,111 | KIBA drugs |
| `kiba_interactions` | 118,253 | KIBA pairs (sparse) |
| `struct2graph_pairs` | 14,036 | Public PPI pair list |
| ESM-2 embeddings cache | 700 rows × 1280 dims | Pre-computed; no fair-esm download needed |

Total: ~80 MB. Lets you do real end-to-end training on actual
benchmark data without any external downloads after the initial
`pip install`.
