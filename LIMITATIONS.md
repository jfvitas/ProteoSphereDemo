# Limitations — Model Studio demo

## What works end-to-end on the bundled demo warehouse

- **Library tab** browse + search across all 8 tabs (Proteins, Ligands,
  Binding pairs, Structures, Motifs, Leakage groups, Sources,
  Releases). Counts reflect the demo warehouse (Davis + KIBA +
  struct2graph subset).
- **Splits tab** — all 12 policies, k-fold CV, leakage-cluster
  constraints, manual cluster overrides, design-objective banner,
  topology warnings.
- **Pipeline tab** — flow builder with the auto-routing safety net
  (ESM-2 on wrong input → auto-promoted to cached embedding;
  bilinear/cross_attn/two_tower_dot with N≠2 inputs → auto-promoted
  to concat_mlp). Sweep mode with Optuna Hyperband pruning.
- **Training tab** — real torch optimizer, real Davis/KIBA loaders,
  real per-epoch metrics streamed over SSE.
- **Results tab** — real Pearson / Spearman / RMSE / R² / AUC from
  the actual checkpoint. CSV export of every prediction.
- **Compare tab** — side-by-side metrics matrix across multiple
  registered runs.
- **Promote** — gate-checked transition to production tier in the
  local model registry.

## What's marked "planned" in the GUI (not broken — explicitly future)

- SQL console button — disabled with `(planned)` tag and an
  explanation tooltip
- ProtBert / ProtT5 / EGNN / SchNet encoders — visible in the
  catalog with a `planned` indicator
- Multiclass classification head with calibrated sigmoid — visible
  with `planned` indicator
- Multi-user / authentication — by design single-user local-only
- Distributed training — single-GPU only

## What's intentionally local-demo

- **In-memory run registry** — active-run metadata + streaming logs
  live in process memory. Restarting the server clears them.
  Promoted checkpoints persist on disk in
  `~/.proteosphere_v2/checkpoints/`; their metrics survive restarts,
  the live event streams don't.
- **No authentication** — the server binds to 127.0.0.1 by default
  and has no users/passwords. Don't expose it to a network.
- **In-memory sweep registry** — same constraint as runs.

## Specific things you'll see if you poke

| You see | Meaning |
|---|---|
| "live · warehouse" green chip on a tab | The data is coming from a real DuckDB / parquet read |
| "preview · fixtures" blue chip on a tab | The bundled demo warehouse doesn't have that family yet — showing a small fixture pool. Not faked numbers, just smaller scope. |
| "auto-promoted fusion" message in the training log | Your flow had a structurally incompatible fusion (e.g. `fuse/bilinear` with 4 inputs); compiler swapped it for `fuse/concat_mlp` and continued |
| "Flow auto-route: ..." messages in the run log | Your flow had `enc.protein_seq/esm2_frozen` wired to `in.protein_seq`; compiler rewired to cached ESM-2 `in.protein_emb` for ~10x speedup |
| Seed banner shows red ⚠ DEFAULT SEED (4192) | You didn't change the seed; every run with the same template + data will produce identical results. Click Randomize. |
| "No run attached" empty state on Results / Training / Inference / Promote / Compare | Honest empty state when there's nothing to show. Previous builds rendered fixture data here, which led to confusion. |

## Reproducibility caveats

- **Demo warehouse counts differ from the manuscript.** The full
  warehouse has 262M UniProt entries and produces the headline
  PINDER/PLINDER cross-audit findings. The demo warehouse has the
  Davis + KIBA subset which is sufficient for reproducing DeepDTA
  setting1 warm-start verdict but NOT for reproducing PLINDER's
  100% Pfam overlap. Run the
  [`proteosphere-paper`](https://github.com/<your-handle>/proteosphere-paper)
  CLI against a full warehouse mount for those.
- **Torch import speed on Windows is sensitive to Defender.** If
  startup takes >60 seconds, see `docs/TROUBLESHOOTING.md` for the
  Defender exclusion command.

## What I am not claiming

- That this is production-ready software. It's an academic-research-grade
  demonstrator that reproduces the analyses in our paper. Treat it
  as such.
- That the metrics you compute with this match what the original
  benchmark authors reported. Different optimizer hyperparameters,
  different random seeds, different torch versions, different CUDA
  versions all cause small variation. The HEADLINE finding —
  warm-start leakage — is the property of the SPLIT, which is
  invariant to these details, and that's what the analyzer measures.
