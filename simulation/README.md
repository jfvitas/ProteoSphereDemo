# ProteoSphere Model Studio — single-file simulation

## What is this?

A fully interactive, self-contained demo of the Model Studio. The user
double-clicks **`ProteoSphereDemo_Simulation.html`** and the entire
studio opens in their browser. No Python, no install, no server, no
network requests — every backend API call is intercepted client-side
and answered with realistic canned data.

Built for situations where the audience (e.g. a hiring committee, an
admissions panel, a grant reviewer) needs to see what the tool does
without going through the install + launch process.

## Open the demo

Just double-click `ProteoSphereDemo_Simulation.html`. Works in Chrome,
Firefox, Edge, and Safari from `file://`.

A small fixed banner at the top of the page reads
**"SIMULATION MODE · All data is faked client-side."** — so viewers
know it's a demo, not a live session.

## Walk-through (~6 minutes end-to-end)

1. **Home** — landing tab, greeting + recent runs.
2. **Library** — flip through *Proteins / Ligands / Binding pairs /
   Structures / Motifs / Sources / Featurizers / Releases*. Pagination
   and search both work; rows use real UniProt accessions
   (P00519 ABL1, P15056 BRAF, P00533 EGFR…) and real ligand SMILES
   (Imatinib, Dasatinib, Ibrutinib, …). Click "View source" to open
   the corresponding UniProt / ChEMBL / RCSB page.
3. **Goal / Dataset / Split / Features** — the leakage-aware splitter
   reports realistic train/val/test counts and a verdict
   (e.g. *"random: WARN — 47% of test pairs share a UniRef90 cluster
   with a train pair"* vs. *"leakage-aware: OK — 0% overlap"*).
4. **Pipeline** — pick a template (all 11 are wired:
   `deepdta / drugban / graphdta / moltrans / conplex / baseline_mlp /
   ppi_gnn_siamese / struct_gnn_dta / tabular_mlp / thermo_mlp / flow`),
   optionally edit hyperparameters (epochs, batch_size, lr, **seed**),
   click **Launch**.
5. **Training** — the simulator plays back ~60 s of realistic SSE
   events: boot logs → batch progress → per-epoch train/val curves →
   final summary. The curve and final metrics are **seeded by the
   `seed` hparam** — different seeds produce visibly different runs.
6. **Results** — scatter plot + ROC + calibration + residuals,
   all driven by the run's seed. Default Pearson ≈ 0.881 (the
   actual published DeepDTA-on-Davis number); other seeds ±0.03.
7. **Compare** — the just-trained run appears alongside a pre-seeded
   production model.
8. **Promote** — open a promotion request; six gates evaluate against
   the model's metrics; click Approve.
9. **Inference** — pick a ligand from the dropdown, click Predict;
   pKd is a deterministic function of (sequence, SMILES, seed).

## Files

| File | Purpose |
|---|---|
| `ProteoSphereDemo_Simulation.html` | The 5.9 MB single-file deliverable — open this. |
| `mock-api.js` | The client-side API interceptor. Monkey-patches `window.fetch` and `window.EventSource`, returns canned data for every `/api/v2/*` endpoint the GUI calls. |
| `build_simulation.py` | The bundler. Reads the GUI source tree from `../gui/model_studio_web_v2/`, inlines CSS / fonts (as base64) / vendor JS / JSX components / mock layer, writes the single-file output. |

## Rebuilding

To rebuild after editing the mock layer or pulling new GUI source:

```bash
cd simulation
python build_simulation.py
```

Output goes to `ProteoSphereDemo_Simulation.html` in the same
directory.

## What's mocked

Every endpoint the React app actually calls:

```
GET    /api/v2/system/user
GET    /api/v2/system/host        (drifts on each poll)
GET    /api/v2/system/gpu         (drifts on each poll)
GET    /api/v2/system/rosetta
POST   /api/v2/system/rosetta/install
GET    /api/v2/ingest/catalog
GET    /api/v2/library/{family}   (paginated + searchable)
GET    /api/v2/library/_source_url
GET    /api/v2/featurizers
GET    /api/v2/pipeline/templates
POST   /api/v2/pipeline/launch
GET    /api/v2/pipeline/runs/{id}
EventSource /api/v2/pipeline/runs/{id}/stream
POST   /api/v2/pipeline/runs/{id}/cancel
GET    /api/v2/pipeline/runs/{id}/results
POST   /api/v2/pipeline/runs/{id}/predict
GET    /api/v2/registry/models
POST   /api/v2/registry/promotions
POST   /api/v2/registry/promotions/{id}/decide
GET    /api/v2/splits/leakage_report
```

All randomness is **mulberry32-seeded by `hparams.seed`**, so the same
seed always replays the same training curve and produces the same
final metrics. Different seeds → visibly different curves and final
metrics within a realistic range.

## Caveats

- **Schema-download link**: one `<a href="/api/v2/library/_schema.sql">`
  element on the Library tab is a browser navigation rather than a
  fetch, so clicking it from `file://` won't download (it'll show a
  "page not found" in the browser). Cosmetic only; an adcom is very
  unlikely to click it.
- **Run history is in-memory**: each new run is appended to a JS
  array. Closing the browser tab resets the simulation state. By
  design — there's no persistence layer.
- **Inference predictions** use a deterministic hash function rather
  than a real model forward pass. Numbers look reasonable (pKd
  6.5–9.5 for the kinase inhibitors in the demo dropdown) but are
  not the actual model's predictions.
