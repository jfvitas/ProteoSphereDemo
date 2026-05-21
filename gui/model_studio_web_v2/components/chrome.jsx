// ProteoSphere — chrome (Rail + Topbar) and re-usable mini-components

const SCREENS = [
  { group: "Workspace", items: [
    { id: "home",      label: "Home",              ico: "home" },
    { id: "library",   label: "Reference library", ico: "beaker" },
  ]},
  { group: "Build a run", items: [
    // STEP 00 — pick the binding type before anything else. Drives downstream filtering.
    { id: "goal",      label: "Goal",            ico: "goal",     step: 0 },
    { id: "dataset",   label: "Dataset",         ico: "dataset",  step: 1 },
    { id: "split",     label: "Splits",          ico: "split",    step: 2 },
    // STEP 03 — feature-selection screen. Picks the featurizers that
    // describe each training example before the user opens the pipeline.
    { id: "features",  label: "Features",        ico: "feature",  step: 3 },
    // STEP 04 — Pipeline editor. The screen itself carries a segmented
    // toggle (PipelineModeToggle) that flips between two editing
    // surfaces: prefab templates and the LabVIEW-style flow builder.
    // Both surfaces share PS_DATA.pipeline state, so the user can hop
    // back and forth without losing work. We pin the rail item to
    // `pipeline` but route to `flow` if that's the mode the user was
    // last using (sticky preference via PS_DATA.pipeline.mode).
    { id: "pipeline",  label: "Pipeline",        ico: "pipeline", step: 4 },
    { id: "training",  label: "Training",        ico: "train",    step: 5, badge: "live" },
  ]},
  { group: "Analyze & ship", items: [
    { id: "results",   label: "Results",         ico: "results" },
    { id: "compare",   label: "Compare",         ico: "compare" },
    { id: "promote",   label: "Promote",         ico: "flag" },
  ]},
];

function Rail({ current, setCurrent, runState }) {
  return (
    <aside className="rail" role="navigation" aria-label="Primary navigation">
      <div className="rail-brand">
        <BrandMark size={28} />
        <div className="name">ProteoSphere</div>
        <span className="ver">v3.1</span>
      </div>

      {SCREENS.map((group, gi) => (
        <React.Fragment key={gi}>
          <div className="rail-section-label">{group.group}</div>
          {group.items.map((it) => {
            const isLive = it.badge === "live" && runState === "training";
            // Treat `flow` as part of the Pipeline rail item — they're
            // two faces of the same step, switched by PipelineModeToggle.
            const isActive = current === it.id
              || (it.id === "pipeline" && current === "flow");
            const planned = !!it.planned;
            return (
              <button
                key={it.id}
                type="button"
                className="rail-item"
                aria-current={isActive ? "page" : undefined}
                onClick={() => {
                  if (planned) {
                    const toast = (typeof window !== "undefined" && window.pushToast) || (() => {});
                    toast({ title: `${it.label} — coming soon`,
                      body: "This screen ships in the next stage of the v4 design integration.",
                      level: "info", ttl_ms: 2400 });
                    return;
                  }
                  // "Pipeline" rail item respects the user's sticky mode
                  // choice — if they previously switched to prefab, send
                  // them back there. Flow builder is the default
                  // (post-v4-stage8 redesign) so a fresh session lands
                  // on the LabVIEW canvas, not the template editor.
                  if (it.id === "pipeline") {
                    const D = (typeof window !== "undefined" && window.PS_DATA) || {};
                    const m = D?.pipeline?.mode;
                    setCurrent(m === "prefab" ? "pipeline" : "flow");
                    return;
                  }
                  setCurrent(it.id);
                }}
                data-screen-label={it.label}
                style={planned ? { opacity: 0.55 } : null}
              >
                <span className="num" aria-hidden="true">{it.step ?? ""}</span>
                <Ico name={it.ico} />
                <span>{it.label}</span>
                {planned && <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--warn)" }}>soon</span>}
                {isLive && <span className="badge live" aria-label="currently training, epoch 18">e18</span>}
              </button>
            );
          })}
        </React.Fragment>
      ))}

      <div className="rail-footer">
        <RailFooterIdentity />
      </div>
    </aside>
  );
}

// Live user + GPU summary for the sidebar footer. Pulls from
// /api/v2/system/user + /api/v2/system/gpu and caches on window so we
// don't re-fetch every screen change.
function RailFooterIdentity() {
  const [user, setUser] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_USER) || null
  );
  const [gpu, setGpu] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_GPU) || null
  );
  React.useEffect(() => {
    if (!user) {
      fetch("/api/v2/system/user")
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(j => { window.PS_LIVE_USER = j; setUser(j); })
        .catch(() => {});
    }
    if (!gpu) {
      fetch("/api/v2/system/gpu")
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(j => { window.PS_LIVE_GPU = j; setGpu(j); })
        .catch(() => {});
    }
  }, []);
  const initials = user?.initials || "?";
  const name     = user?.handle || user?.name || "user";
  // GPU summary — "RTX 5080 · 91% free" when available, "no GPU" otherwise.
  const gpuLine = (() => {
    if (!gpu) return "Loading hardware…";
    if (gpu.available === false) return "CPU only (no GPU)";
    if (gpu.available === null)  return "Hardware loading…";
    const name = (gpu.device_name || "GPU").replace(/NVIDIA |GeForce | Laptop GPU/g, "");
    const free = gpu.free_pct != null ? ` · ${Math.round(gpu.free_pct)}% free` : "";
    return `${name}${free}`;
  })();
  return (
    <>
      <div className="avatar" title={user?.email || ""}>{initials}</div>
      <div style={{ minWidth: 0 }}>
        <div style={{ color: "var(--text)", fontSize: 12 }}>{name}</div>
        <div style={{ color: "var(--dim)", fontSize: 10, fontFamily: "var(--font-mono)" }}
             title={user?.lab || ""}>
          {(user?.lab || "ProteoSphere") + " · " + gpuLine}
        </div>
      </div>
    </>
  );
}

function Topbar({ project, run, current }) {
  // Project name from PS_DATA.project (configurable per session); the
  // hardcoded "KinaseCore-v3" placeholder is gone.
  const projectName = (project && project.name) || (window.PS_DATA?.project?.name) || "ProteoSphere";
  const crumbs = [projectName, currentLabel(current)];
  return (
    <div className="topbar">
      <div className="crumbs">
        <Ico name="layers" size={12} style={{ color: "var(--dim)" }} />
        <span>{crumbs[0]}</span>
        <span className="sep">/</span>
        <span className="current">{crumbs[1]}</span>
      </div>

      <div className="topbar-spacer" />

      <button type="button" className="topbar-tool"
        aria-label="Open command palette (Ctrl-K)"
        title="Command palette (Ctrl-K)"
        onClick={() => window.dispatchEvent(new CustomEvent("open-cmd-palette"))}>
        <Ico name="search" size={12} /> Search <kbd>⌘K</kbd>
      </button>
      <div className="run-pill" data-state={run.state} role="status" aria-live="polite" aria-atomic="true"
           aria-label={run.state === "training"
             ? `Run ${run.id}, training, epoch ${run.epoch ?? 18} of 40, ETA ${run.eta}`
             : `Run ${run.id}, ${run.state}`}>
        <span className="dot" aria-hidden="true" />
        <span>{run.state === "training" ? `${run.id} · e${run.epoch ?? 18}/40` : run.id}</span>
        <span style={{ color: "var(--dim)" }}>· {run.eta} ETA</span>
      </div>
      <button type="button" className="topbar-tool"
        aria-label="Open settings (Tweaks panel)"
        title="Settings"
        onClick={() => window.dispatchEvent(new CustomEvent("open-tweaks"))}>
        <Ico name="settings" size={12} />
      </button>
    </div>
  );
}

function currentLabel(id) {
  for (const g of SCREENS) for (const it of g.items) if (it.id === id) return it.label;
  return id;
}

// Step rail for the build flow. The 7-step v4 rail inserts a Goal step
// before Dataset and a Features step between Splits and Pipeline. The
// Features step is marked `planned` for the current stage of v4
// integration (the screen ships in a later stage); clicking it shows
// a toast instead of routing to a blank page.
function StepRail({ active, onClick }) {
  const D = (typeof window !== "undefined" && window.PS_DATA) || {};
  const bt = D.binding_type
    ? (window.PS_BINDING_TYPES || []).find(b => b.id === D.binding_type)
    : null;
  const steps = [
    { id: "goal",     label: "Goal",         meta: bt ? bt.label : "pick binding type", state: bt ? "done" : "" },
    { id: "dataset",  label: "Dataset",      meta: "1.84M rows",     state: "done" },
    { id: "split",    label: "Splits",       meta: "leakage-aware",  state: "done" },
    { id: "features", label: "Features",     meta: "feature picks",  state: "" },
    { id: "pipeline", label: "Pipeline",     meta: "cross-attn",     state: "done" },
    { id: "training", label: "Training",     meta: "e18/40 · 12m",   state: "active" },
    { id: "results",  label: "Results",      meta: "—",              state: "" },
  ];
  return (
    <div className="steprail">
      {steps.map((s, i) => {
        const isActive = active === s.id;
        const state = isActive ? "active" : s.state;
        const planned = state === "planned";
        return (
          <div key={s.id} className="step" data-state={state}
            onClick={() => {
              if (planned) {
                const toast = (typeof window !== "undefined" && window.pushToast) || (() => {});
                toast({ title: `${s.label} — coming soon`,
                  body: "This step is reserved in the rail; the screen ships in the next integration chunk.",
                  level: "info", ttl_ms: 2400 });
                return;
              }
              onClick && onClick(s.id);
            }}
            style={planned ? { opacity: 0.55, cursor: "default" } : null}>
            <span className="n">{state === "done" ? "✓" : i}</span>
            <span className="label">{s.label}</span>
            <span className="meta">{s.meta}</span>
          </div>
        );
      })}
    </div>
  );
}

function Stat({ k, v, mono, delta, deltaNeg }) {
  return (
    <div className="stat">
      <div className="k">{k}</div>
      <div className={"v" + (mono ? " mono" : "")}>{v}</div>
      {delta != null && <div className={"delta" + (deltaNeg ? " neg" : "")}>{delta}</div>}
    </div>
  );
}

function Chip({ tone, children, dot }) {
  return (
    <span className={"chip" + (tone ? " " + tone : "")}>
      {dot && <span className="dot" />}
      {children}
    </span>
  );
}

// Format helpers
const fmt = {
  n: (x) => new Intl.NumberFormat("en-US").format(x),
  short: (x) => {
    if (x >= 1e9) return (x / 1e9).toFixed(1) + "B";
    if (x >= 1e6) return (x / 1e6).toFixed(1) + "M";
    if (x >= 1e3) return (x / 1e3).toFixed(1) + "K";
    return String(x);
  },
  pct: (x, d = 1) => (x * 100).toFixed(d) + "%",
  dec: (x, d = 3) => Number(x).toFixed(d),
  money: (x) => "$" + Number(x).toFixed(2),
};

// ────────────────────────────────────────────────────────────────────
// PipelineModeToggle — segmented control that flips between the two
// editing surfaces on the Pipeline step. Both share `PS_DATA.pipeline`
// state, so the user can hop back and forth without losing work.
//
//   • "prefab"  → ScreenPipeline (template-first DAG editor, the
//                  pre-built architectures GraphDTA/DeepDTA/AFM/…)
//   • "flow"    → ScreenFlow     (LabVIEW-style canvas; drop any
//                  block, wire ports, full freedom)
//
// The current mode is mirrored to `D.pipeline.mode` so /api/v2/pipeline
// launches can record which surface produced the run.
// ────────────────────────────────────────────────────────────────────
function PipelineModeToggle({ current, setCurrent }) {
  const D = (typeof window !== "undefined" && window.PS_DATA) || {};
  // `current` is the explicit route the parent is rendering. The flow
  // builder is the default surface — if no current is passed (e.g. the
  // toggle is rendered standalone), fall through to flow.
  const mode = current === "pipeline" ? "prefab" : "flow";
  const setMode = (m) => {
    if (!D.pipeline) D.pipeline = {};
    D.pipeline.mode = m;
    setCurrent && setCurrent(m === "flow" ? "flow" : "pipeline");
  };
  return (
    <div className="pipeline-mode-toggle"
      style={{
        display: "inline-flex", alignItems: "center", gap: 0,
        background: "var(--surface-2)", border: "1px solid var(--border)",
        borderRadius: "var(--r)", padding: 3, marginBottom: 14,
      }}
      role="tablist" aria-label="Pipeline editor mode">
      <button type="button" role="tab" aria-selected={mode === "prefab"}
        onClick={() => setMode("prefab")}
        title="Pre-built architectures (DeepDTA, GraphDTA, DrugBAN, AlphaFold-Multimer, …). The fastest way to launch a known-good baseline."
        style={{
          padding: "6px 14px", fontSize: 12, fontFamily: "var(--font-sans)",
          background: mode === "prefab" ? "var(--primary)" : "transparent",
          color:      mode === "prefab" ? "#021624"        : "var(--text)",
          border: "none", borderRadius: "calc(var(--r) - 2px)",
          cursor: "pointer", fontWeight: mode === "prefab" ? 600 : 500,
          display: "inline-flex", alignItems: "center", gap: 6,
        }}>
        <Ico name="layers" size={11} /> Prefab templates
      </button>
      <button type="button" role="tab" aria-selected={mode === "flow"}
        onClick={() => setMode("flow")}
        title="Free-form flow builder — drag blocks onto a canvas and wire them by port type. Pick this when no prefab matches your design."
        style={{
          padding: "6px 14px", fontSize: 12, fontFamily: "var(--font-sans)",
          background: mode === "flow" ? "var(--primary)" : "transparent",
          color:      mode === "flow" ? "#021624"        : "var(--text)",
          border: "none", borderRadius: "calc(var(--r) - 2px)",
          cursor: "pointer", fontWeight: mode === "flow" ? 600 : 500,
          display: "inline-flex", alignItems: "center", gap: 6,
        }}>
        <Ico name="flow" size={11} /> Flow builder
      </button>
      <span style={{
        marginLeft: 10, fontFamily: "var(--font-mono)", fontSize: 10,
        color: "var(--dim)", letterSpacing: "0.05em",
      }}>
        {mode === "prefab"
          ? "pick a known baseline → swap pieces in the inspector"
          : "drag blocks, wire by port type, total freedom"}
      </span>
    </div>
  );
}

Object.assign(window, { Rail, Topbar, StepRail, PipelineModeToggle, Stat, Chip, SCREENS, fmt, PreCheck });

function PreCheck({ label, state, detail }) {
  const c = state === "ok" ? "var(--signal)" : state === "warn" ? "var(--warn)" : "var(--error)";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ width: 16, height: 16, borderRadius: 8, border: `1.6px solid ${c}`, display: "grid", placeItems: "center", color: c, flexShrink: 0 }}>
        <Ico name={state === "ok" ? "check" : "warn"} size={9} />
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: "var(--text)" }}>{label}</div>
        <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--dim)" }}>{detail}</div>
      </div>
    </div>
  );
}
