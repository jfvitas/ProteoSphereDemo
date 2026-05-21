# Model Studio v2 design preview

This directory is the **Claude-Design redesign bundle, hardened for dev
use (May 2026)**, running as a read-only parallel preview alongside the
production Model Studio. Open it at:

```
http://127.0.0.1:8765/v2/
```

The current Model Studio remains at `/`; the v2 preview is opt-in via
the "Preview new design →" link in the v1 sidebar.

## What's in this round

This bundle picks up from the second Claude-Design hand-off and adds the
hardening + deep-options work the team called for:

- **~80 advanced settings exposed** via `PS_DEEP_SETTINGS` in `data.js`,
  driven by a generic `SettingsPanel`/`SettingRow` renderer in
  `shared-v2.jsx`. Five panels (Structure preparation, Featurizer
  advanced, Training advanced, Inference advanced, Eval & analytics)
  cover Rosetta / PyRosetta + alternatives (OpenMM, RDKit, OpenBabel,
  Schrödinger PrepWizard), ESM-2/SaProt/MolFormer encoder knobs,
  optimiser/scheduler/precision/distributed/repro/monitoring, ensembling
  + conformal calibration + attribution, and per-stratum metrics +
  selectivity panels + wet-lab follow-up. See `AGENT_HANDOFF.md § 10`
  for the contract.
- **Accessible modal & drawer primitives** (`Modal`, `Drawer` in
  `shared-v2.jsx`) with `role="dialog"`, `aria-modal="true"`,
  `aria-labelledby`/`aria-label`, focus trap, Esc-close (with
  `stopPropagation`), and focus restored to the launcher on close.
  `CostGuardModal` and `LineageDrawer` now wrap them.
- **Pipeline / Training / Inference / Splits / Results** each surface an
  `<AdvancedButton>` with an override-count badge and a live
  settings-summary strip. The Structure-preparation card on Pipeline
  shows engine / hydrogens / ligand 3D / docking prep at a glance plus
  a Rosetta line when PyRosetta is selected.
- **Results stratum toggle actually re-segments** (All / Held-out
  targets / Cold scaffolds) with deltas vs the global metric, paired-
  bootstrap CI(α) annotation, and a stratum-aware header strip.
- **Validator → field-jump unified** across all screens. All 5
  blocker cards on Results now jump and flash the correct
  `data-field` anchor (including the three that were broken in the
  previous round).
- **Coach inline hints** appear on Splits, Pipeline, Inference when the
  user has the relevant area checked in Tweaks → "Coach me on".
- **Term tooltip** is now a real `<button>` with `aria-describedby`
  pointing at a `role="tooltip"` popover. Keyboard reachable, click-
  toggles on touch.
- **Self-hosted third-party runtime** — React 18.3.1, ReactDOM 18.3.1,
  @babel/standalone 7.29.0, and Geist + Geist Mono fonts live under
  `vendor/`. No unpkg, no Google Fonts. No IP/UA leak.
- **Server hardening** — every static response sets
  `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors
  'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy:
  no-referrer`. `_build_v2_asset_map` skips dotfiles, caps per-file
  size at 5 MiB, and resolves symlinks against the v2 root.
- **Catalog test timeout** bumped from 10 s to 60 s in
  `tests/integration/test_model_studio_server.py::_get` (the warehouse
  walk on first call can take well over 10 s on an 80 GB library).

## What changed in this update (v2 vs v1)

The first bundle ("v1") shipped 8 screens. After dogfooding it, we sent
Claude Design a written brief (`CLAUDE_DESIGN_V2_BRIEF.md`) asking for
specific gaps to be closed. This second bundle delivers them:

- **9 screens, regrouped** — the rail is now `WORKSPACE` (Home, Reference
  library), `BUILD A RUN` (1 Dataset → 2 Splits → 3 Pipeline → 4 Training)
  and `ANALYZE & SHIP` (Results, Compare, Promote). Inference moved out of
  the primary rail and lives on a floating **Quick predict** FAB.
- **Reference library** (new) — DuckDB-warehouse browser. 8 tabs:
  Proteins · Ligands · Binding pairs · Structures · Motifs · Leakage
  groups · Sources · Releases. Per-row detail drawer.
- **Promote** (new) — candidate-vs-current cards, pass/fail/wait
  gates with "Resolve →" jumps, reviewer roster, threaded comments, audit
  log, wet-lab follow-up hook.
- **5-tier catalog visual language** — every option, picker and family card
  carries a `TierPill` (Production / Beta / Coming soon / Lab / Blocked).
  Active-lane bar atop every "Build a run" screen. Blocked options shown
  dashed + faded.
- **Validator → field-jump wiring** — Recommendations & blockers card on
  Results. Each blocker carries a `related_fields` anchor that jumps to
  the offending screen, scroll-locks to the right card via `data-field`,
  and pulses an error-colored highlight.
- **Stale-artifact banner** — surfaces newer warehouse releases on Home
  and Library with Pin / Stay options.
- **Cost guardrail modal** — typed-reason + named-reviewer override
  required to breach a compute cap; reused by Sweep launch.
- **Empty / loading / error states** — canonical examples on the Home
  Recent Runs card, cyclable from the dev-tools tab in the Tweaks panel.
- **Failure-state Training UI** — Causes & Fixes card (OOM, NaN, lr
  divergence, data, infra), auto-restart-with-fix buttons, filtered log
  tail.
- **Sweep mode** in Pipeline — Quick start / Standard / Sweep modes with
  sampler + pruner + n_trials + n_seeds + search-space table + live cost
  guardrail banner.
- **Multi-objective training** — n-curve toggle on the loss card.
- **Plain-language layer** — `<Term word="…">` glossary component with
  30+ definitions, used inline. Headlines rewritten plainly ("Pick what
  goes into your training set", "Send a candidate model to production").
- **Coach multi-select** — 8 per-area checkboxes in Tweaks → "Coach me on"
  instead of a binary Guided/Expert mode.

The `Inference` screen is still here but reached via the **Quick predict**
FAB. The polish items — provenance chip → Open lineage / Recreate run,
LineageDrawer, ⌘K palette, `g h / g l / g d …` jumps, `[` / `]` prev/next,
visible focus rings, anchor-based coach overlay — also landed.

## Why this directory exists

The user commissioned a UI/UX redesign via Claude Design. Rather than
forcing a choice between "wholesale replacement of a working v1" and
"weeks of incremental wiring before anyone can see it," this directory:

1. **Ships the prototype as-is** at `/v2/`, with fixture data, so the team
   can walk through every screen in the actual environment.
2. **Wires screens to live data one at a time**, in the order recommended
   in `AGENT_HANDOFF.md` § 8.
3. **Keeps v1 working** the whole time; never breaks the live tool.

The prototype runs React 18 via the CDN UMD build with Babel-Standalone
for in-browser JSX. That's fine for a preview but will eventually want a
real build step (Vite, esbuild) once the backend wiring lands.

## File layout

```
gui/model_studio_web_v2/
├── index.html                 # entry; loads scripts in dependency order
├── styles.css                 # tokens (Geist, sky/lime/violet, warm-cream
│                              # light, dark default) + v2 component styles
├── data.js                    # window.PS_DATA fixtures, PS_TIERS catalog,
│                              # PS_GLOSSARY — REPLACE with live API
├── app.jsx                    # App shell, router, theme, tweaks, coach
│                              # multi-select, ⌘K palette, FAB
├── tweaks-panel.jsx           # Tweaks UI (User Settings + Dev Tools)
├── components/
│   ├── icons.jsx              # Stroke-based icon set (incl. flask)
│   ├── chrome.jsx             # Rail (9 items), Topbar, StepRail, Chip,
│   │                          # Stat, PreCheck, BrandMark wrapper
│   ├── charts.jsx             # Line / Scatter / Histogram / Donut /
│   │                          # ROC / etc. Inline SVG, no chart deps.
│   ├── shared-v2.jsx          # NEW. TierPill, Term, LaneBar, StaleBanner,
│   │                          # CostGuardModal, Empty/Error/Loading,
│   │                          # StatefulCard, BlockerCard + jumpToField,
│   │                          # ProvenanceChip, LineageDrawer, BrandMark
│   ├── screen-home.jsx
│   ├── screen-library.jsx     # NEW. Reference library, 8 tabs
│   ├── screen-dataset.jsx
│   ├── screen-split.jsx       # the "signature" screen
│   ├── screen-pipeline.jsx
│   ├── screen-training.jsx
│   ├── screen-results.jsx
│   ├── screen-compare.jsx
│   ├── screen-promote.jsx     # NEW. Promote / review flow
│   └── screen-inference.jsx
├── AGENT_HANDOFF.md           # the v2 build spec (500 lines)
├── CLAUDE_DESIGN_V2_BRIEF.md  # the brief that produced this update
├── DESIGN_BUNDLE_README.md    # the original Claude-Design readme
└── README.md                  # this file
```

## How it's served

The Python server at `api/model_studio/server.py` walks this directory at
startup and builds a closed allow-list of URL paths under `/v2/*`
(see `_build_v2_asset_map`). The walker only emits files whose extension is
in `_V2_CONTENT_TYPES` (html, css, js, jsx, md, common images), so binary
artifacts or backup files dropped here are not served. `.jsx` files are
served with `Content-Type: text/babel` so the browser-side Babel
transpiler picks them up.

To pick up newly-added v2 files, restart the server.

## Migration plan

Per `AGENT_HANDOFF.md` § 8, the recommended build order for live data is:

1. **Provenance + IDs + content hashes everywhere** — every downstream
   feature depends on this.
2. **Validator service + `related_fields` contract** — unblocks
   blocker → field-jump, the most-used trust interaction.
3. **Dataset preview service** → unblocks the preview funnel and
   dropped-rows table.
4. **Cluster split service** → unblocks Splits (still the highest leverage).
5. **Tier registry** → read-only first, then editorial workflow.
6. **Warehouse release feed** → unblocks the stale banner.
7. **Run streaming WS + smart insights + multi-objective metrics** →
   unblocks Training.
8. **Eval bundle + attribution + conformal intervals** → unblocks Results.
9. **Promotion service + audit log** → unblocks Promote.
10. **Cost guard service** → wraps every job-launching action.

When wiring a screen to live data, replace the relevant `window.PS_DATA.*`
lookups in `data.js` with `fetch('/api/model-studio/...')` calls in the
screen file. Keep the fixture as the fallback so screens still render in
demos and tests.

## What's intentionally not yet implemented from the prototype

- A real 3D viewer (NGL.js or Mol\*); the Pose view is an SVG placeholder
  per the spec.
- The Monaco SQL editor (Dataset screen, expert mode).
- The wet-lab follow-up UI (data model is in `AGENT_HANDOFF.md § 5`; UI
  deferred to v3).
- `useTweaks` persistence to localStorage is wired but reset on hard reload.

## Do not commit

- Anything fetched from a private dataset.
- Anything in `node_modules/` (there is no build step yet).
- Compiled artifacts.

When the prototype eventually graduates to production, it should grow a
proper build pipeline (Vite is the obvious fit given the JSX-without-modules
shape) and the CDN React + Babel-Standalone tags get removed.
