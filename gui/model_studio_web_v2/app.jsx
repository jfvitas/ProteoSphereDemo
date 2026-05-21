// ProteoSphere v2 — App shell

const DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "dark",
  "density": "comfortable",
  "accent": "cyan",
  "runState": "training",
  "coachOn": false,
  "coachAreas": {
    "dataset": true,
    "split": true,
    "pipeline": false,
    "training": false,
    "results": true,
    "promote": true,
    "inference": false,
    "library": true
  },
  "devTools": false
}/*EDITMODE-END*/;

const ACCENTS = {
  cyan:    { primary: "#6fc7f5", soft: "#6fc7f520", strong: "#8ed7ff" },
  lime:    { primary: "#b3ef4d", soft: "#b3ef4d20", strong: "#caf76e" },
  violet:  { primary: "#c79bff", soft: "#c79bff20", strong: "#dab7ff" },
  amber:   { primary: "#f5c451", soft: "#f5c45120", strong: "#ffd676" },
};

function App() {
  const [tweaks, setTweak] = useTweaks(DEFAULTS);
  const [current, setCurrent] = React.useState("home");
  const [lineageOpen, setLineageOpen] = React.useState(false);
  const [quickPredictOpen, setQuickPredictOpen] = React.useState(false);
  const [cmdOpen, setCmdOpen] = React.useState(false);
  // Which advanced-settings modal is open (null | "structure_preparation" |
  // "featurizer_advanced" | "training_advanced" | "inference_advanced" |
  // "eval_analytics"). One modal at a time.
  const [advancedOpen, setAdvancedOpen] = React.useState(null);
  // Per-panel advanced settings, seeded from PS_DEEP_DEFAULTS. We keep
  // overrides in component state so the user's edits survive between
  // modal open/close until reload. A real wiring would persist to the
  // run config server-side.
  const [advanced, setAdvanced] = React.useState(() => {
    const defaults = window.PS_DEEP_DEFAULTS || {};
    // Shallow-clone each panel so React can detect changes.
    return Object.fromEntries(Object.entries(defaults).map(([k, v]) => [k, { ...v }]));
  });
  // How many overrides differ from default per panel — surfaced as a chip
  // count on the "Advanced" launcher button. Cheap O(n) over PS_DEEP_DEFAULTS.
  const advancedDeltaCount = React.useMemo(() => {
    const out = {};
    const defaults = window.PS_DEEP_DEFAULTS || {};
    for (const [panelKey, vals] of Object.entries(advanced)) {
      const dflt = defaults[panelKey] || {};
      let n = 0;
      for (const [k, v] of Object.entries(vals)) {
        const d = dflt[k];
        const same = (Array.isArray(d) && Array.isArray(v))
          ? d.length === v.length && d.every((x, i) => x === v[i])
          : d === v;
        if (!same) n++;
      }
      out[panelKey] = n;
    }
    return out;
  }, [advanced]);
  const setAdvancedField = React.useCallback((panelKey, key, value) => {
    setAdvanced(prev => ({ ...prev, [panelKey]: { ...prev[panelKey], [key]: value } }));
  }, []);
  const resetAdvancedPanel = React.useCallback((panelKey) => {
    const dflt = (window.PS_DEEP_DEFAULTS || {})[panelKey] || {};
    setAdvanced(prev => ({ ...prev, [panelKey]: { ...dflt } }));
  }, []);
  const mainRef = React.useRef(null);

  React.useEffect(() => {
    // The HTML markup hard-codes data-theme="dark" / data-density="comfortable"
    // on BOTH <html> and <body>. The CSS selector [data-theme="light"]
    // matches whichever element carries it, but if only body flips and
    // html keeps the old value, inheritance from <html> overrides the
    // body's variables. Mirror to both elements so toggles actually take.
    document.documentElement.dataset.theme = tweaks.theme;
    document.documentElement.dataset.density = tweaks.density;
    document.body.dataset.theme = tweaks.theme;
    document.body.dataset.density = tweaks.density;
    const a = ACCENTS[tweaks.accent] || ACCENTS.cyan;
    document.documentElement.style.setProperty("--primary", a.primary);
    document.documentElement.style.setProperty("--primary-soft", a.soft);
    document.documentElement.style.setProperty("--primary-strong", a.strong);
  }, [tweaks.theme, tweaks.density, tweaks.accent]);

  React.useEffect(() => {
    if (mainRef.current) mainRef.current.scrollTop = 0;
  }, [current]);

  React.useEffect(() => {
    if (window.PS_DATA) window.PS_DATA.run.state = tweaks.runState;
  }, [tweaks.runState]);

  // External openers (command palette, screen-level chips) dispatch a
  // CustomEvent("open-advanced", {detail: {panel}}) to ask for the modal.
  // This keeps screens decoupled from the modal's container.
  React.useEffect(() => {
    const handler = (e) => {
      const panel = e?.detail?.panel;
      if (panel) setAdvancedOpen(panel);
    };
    const palette = () => setCmdOpen(true);
    const lineage = () => setLineageOpen(true);
    const navTo = (e) => {
      const target = e?.detail?.screen;
      if (target) setCurrent(target);
    };
    window.addEventListener("open-advanced", handler);
    window.addEventListener("open-cmd-palette", palette);
    window.addEventListener("open-lineage", lineage);
    window.addEventListener("navigate-to", navTo);
    return () => {
      window.removeEventListener("open-advanced", handler);
      window.removeEventListener("open-cmd-palette", palette);
      window.removeEventListener("open-lineage", lineage);
      window.removeEventListener("navigate-to", navTo);
    };
  }, []);

  // Keyboard shortcuts
  React.useEffect(() => {
    let prefix = null;
    const jumps = { h: "home", l: "library", d: "dataset", s: "split", p: "pipeline", t: "training", r: "results", c: "compare", m: "promote", i: "inference" };
    const handler = (e) => {
      // Ctrl-K on Windows/Linux, Cmd-K on macOS.
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); setCmdOpen(o => !o); return; }
      // Ignore shortcuts while typing in form controls.
      const tag = e.target.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.target.isContentEditable) return;
      if (e.key === "?") { e.preventDefault(); setCmdOpen(o => !o); return; }
      if (e.key === "Escape") { setCmdOpen(false); /* fall through — Esc may also close modals/drawers */ }
      if (e.key === "[" || e.key === "]") {
        const order = ["home","library","dataset","split","pipeline","training","results","compare","promote"];
        const i = order.indexOf(current);
        const next = order[(i + (e.key === "]" ? 1 : -1) + order.length) % order.length];
        setCurrent(next);
        return;
      }
      if (prefix === "g" && jumps[e.key]) {
        setCurrent(jumps[e.key]);
        prefix = null;
        return;
      }
      if (e.key === "g") { prefix = "g"; setTimeout(() => prefix = null, 800); }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [current]);

  const screenProps = {
    setCurrent,
    runState: tweaks.runState,
    setLineageOpen,
    // Advanced-settings glue — every screen receives:
    //   advanced               full per-panel values
    //   advancedDeltaCount     {panelKey: int} of overrides vs default
    //   openAdvanced(panelKey) shows the modal for that panel
    advanced,
    advancedDeltaCount,
    openAdvanced: setAdvancedOpen,
    setAdvancedField,
    coachAreas: tweaks.coachAreas,
    coachOn: tweaks.coachOn,
    // Toast bus — stub actions push descriptive notifications so the
    // user gets visible feedback for every button instead of dead air.
    pushToast,
  };

  return (
    <div className="app">
      <a href="#main-content" className="skip-link">Skip to main content</a>
      <Rail current={current} setCurrent={setCurrent} runState={tweaks.runState} />
      <Topbar project={window.PS_DATA.project} run={{ ...window.PS_DATA.run, state: tweaks.runState }} current={current} />

      <main className="main" ref={mainRef} id="main-content" tabIndex={-1} role="main" aria-label={`Model Studio · ${current}`}>
        {current === "home"      && <ScreenHome {...screenProps} />}
        {current === "library"   && <ScreenLibrary {...screenProps} />}
        {current === "goal"      && <ScreenGoal {...screenProps} />}
        {current === "dataset"   && <ScreenDataset {...screenProps} />}
        {current === "split"     && <ScreenSplit {...screenProps} />}
        {current === "features"  && <ScreenFeatures {...screenProps} />}
        {current === "pipeline"  && <ScreenPipeline {...screenProps} />}
        {current === "flow"      && <ScreenFlow {...screenProps} />}
        {current === "training"  && <ScreenTraining {...screenProps} />}
        {current === "results"   && <ScreenResults {...screenProps} />}
        {current === "compare"   && <ScreenCompare {...screenProps} />}
        {current === "promote"   && <ScreenPromote {...screenProps} />}
        {current === "inference" && <ScreenInference {...screenProps} />}
      </main>

      {/* Quick Predict floating action button — Inference moved out of primary rail */}
      <button className="fab" onClick={() => setCurrent("inference")} title="Quick predict — single protein + ligand">
        <Ico name="target" size={14} /> Quick predict
      </button>

      {tweaks.coachOn && <CoachOverlay current={current} areas={tweaks.coachAreas} />}
      <LineageDrawer open={lineageOpen} onClose={() => setLineageOpen(false)} pushToast={pushToast} />
      <CommandPalette
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        setCurrent={setCurrent}
        setLineageOpen={setLineageOpen}
        pushToast={pushToast}
      />
      <ToastBus />

      {/* Advanced-settings modal — one component, switched by `advancedOpen`.
          The Modal primitive handles focus trap + Esc + return-focus a11y. */}
      <AdvancedSettingsModal
        panelKey={advancedOpen}
        values={advancedOpen ? advanced[advancedOpen] : {}}
        onChange={(k, v) => advancedOpen && setAdvancedField(advancedOpen, k, v)}
        onReset={() => advancedOpen && resetAdvancedPanel(advancedOpen)}
        onClose={() => setAdvancedOpen(null)}
        deltaCount={advancedOpen ? advancedDeltaCount[advancedOpen] : 0}
      />

      <TweaksPanel title="Tweaks">
        <TweakSection label="User settings">
          <TweakRadio label="Theme"   options={["dark", "light"]} value={tweaks.theme}   onChange={v => setTweak("theme", v)} />
          <TweakRadio label="Density" options={["comfortable", "compact"]} value={tweaks.density} onChange={v => setTweak("density", v)} />
          <TweakColor label="Accent" options={[ACCENTS.cyan.primary, ACCENTS.lime.primary, ACCENTS.violet.primary, ACCENTS.amber.primary]}
            value={ACCENTS[tweaks.accent]?.primary || ACCENTS.cyan.primary}
            onChange={hex => {
              const name = Object.entries(ACCENTS).find(([, v]) => v.primary === hex)?.[0] || "cyan";
              setTweak("accent", name);
            }} />
        </TweakSection>

        <TweakSection label="Coach me on">
          <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 8 }}>Show explanations + recommended-defaults badges on these screens. Uncheck what you already know.</div>
          <TweakToggle label="Whole coach overlay on/off" value={tweaks.coachOn} onChange={v => setTweak("coachOn", v)} />
          {[
            { id: "library",  label: "Reference library" },
            { id: "dataset",  label: "Dataset filters" },
            { id: "split",    label: "Splits & leakage" },
            { id: "pipeline", label: "Pipeline architecture" },
            { id: "training", label: "Training monitoring" },
            { id: "results",  label: "Results interpretation" },
            { id: "promote",  label: "Promotion workflow" },
            { id: "inference",label: "Inference" },
          ].map(a => (
            <TweakToggle
              key={a.id}
              label={a.label}
              value={!!tweaks.coachAreas?.[a.id]}
              onChange={v => setTweak("coachAreas", { ...tweaks.coachAreas, [a.id]: v })}
            />
          ))}
        </TweakSection>

        <TweakSection label="PyRosetta license">
          <RosettaLicensePanel />
        </TweakSection>

        <TweakSection label="Navigate">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6 }}>
            {SCREENS.flatMap(g => g.items).map(it => (
              <button key={it.id} className="btn sm" style={{ justifyContent: "flex-start" }} onClick={() => setCurrent(it.id)}>
                <Ico name={it.ico} size={12} /> {it.label}
              </button>
            ))}
            <button className="btn sm" style={{ justifyContent: "flex-start" }} onClick={() => setCurrent("inference")}>
              <Ico name="target" size={12} /> Inference
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--dim)", fontFamily: "var(--font-mono)", marginTop: 8, lineHeight: 1.6 }}>
            <span style={{ color: "var(--text)" }}>Keyboard</span><br />
            <kbd className="hint">⌘K</kbd> command palette &nbsp;
            <kbd className="hint">g</kbd> <kbd className="hint">h</kbd> jump to home &nbsp;
            <kbd className="hint">[</kbd> <kbd className="hint">]</kbd> prev / next
          </div>
        </TweakSection>

        <TweakSection label="Developer tools">
          <TweakToggle label="Show dev tools" value={tweaks.devTools} onChange={v => setTweak("devTools", v)} />
          {tweaks.devTools && (
            <>
              <TweakRadio label="Simulate run state" options={["idle", "training", "done", "failed"]} value={tweaks.runState} onChange={v => setTweak("runState", v)} />
              <TweakButton label="Open lineage drawer" onClick={() => setLineageOpen(true)} />
              <TweakButton label="Open command palette" onClick={() => setCmdOpen(true)} />
              <div style={{ fontSize: 10, color: "var(--dim)", fontFamily: "var(--font-mono)", marginTop: 4 }}>
                In production these tools are only available on localhost or behind `?dev=1`.
              </div>
            </>
          )}
        </TweakSection>
      </TweaksPanel>
    </div>
  );
}

// Coach overlay — anchor-based, not pixel-coordinate. Looks for an
// element with [data-coach-target="<id>"] and floats next to it.
//
// Per-screen dismissal: each tip stores its dismissed state in
// sessionStorage so closing it on the Results screen doesn't make it
// reappear when you navigate away and back in the same session. The
// dismissal does NOT persist across browser tab restarts — re-enabling
// `coachOn` in settings always brings every tip back, and a fresh tab
// session resets all of them. That matches the splitter coach answer
// (per-session, not per-localStorage).
function CoachOverlay({ current, areas }) {
  const tips = {
    home:      { anchor: null,        pos: { top: 110, left: 280 }, k: "Welcome", msg: "Active run is always here. Click 'Open' to jump straight into training." },
    library:   { anchor: null,        pos: { top: 180, left: 320 }, k: "Browse before you build", msg: "Look up what's actually in BindingDB or ChEMBL before picking sources — saves arguments later." },
    dataset:   { anchor: null,        pos: { top: 200, left: 320 }, k: "Sources", msg: "Each card shows how many rows survive your filters in real time." },
    split:     { anchor: null,        pos: { top: 280, left: 380 }, k: "Why splits matter", msg: "Random splits inflate metrics by 5–15 points. The leakage-aware default is honest, not pessimistic." },
    pipeline:  { anchor: null,        pos: { top: 220, left: 360 }, k: "Compute graph", msg: "Click any node to inspect. Embeddings are cached — only invalidated on featurizer changes." },
    training:  { anchor: null,        pos: { top: 240, left: 320 }, k: "Live curves", msg: "Compare to up to 4 historical runs. Smart insights flags overfit, plateau, drift." },
    results:   { anchor: null,        pos: { top: 200, left: 320 }, k: "Start at the top", msg: "Recommendations & blockers card surfaces the most useful next moves. Click one to jump to the offending field." },
    compare:   { anchor: null,        pos: { top: 180, left: 320 }, k: "A/B", msg: "Statistically significant Δ are highlighted. The Pareto chart finds your cost/perf sweet spot." },
    promote:   { anchor: null,        pos: { top: 240, left: 360 }, k: "Gates", msg: "Every gate must pass before the Promote button unlocks. Click 'Resolve' on a failing gate to jump to it." },
    inference: { anchor: null,        pos: { top: 220, left: 320 }, k: "Coverage check", msg: "Always check if the query is in-distribution before trusting the pKi number." },
  };
  // Per-screen dismiss state. We use sessionStorage (not localStorage)
  // so settings-toggle of coachOn always reveals fresh tips on the
  // next browser session and we don't accumulate state forever.
  const dismissKey = `ps.coach.dismissed.${current}`;
  const [dismissed, setDismissed] = React.useState(() => {
    try { return sessionStorage.getItem(dismissKey) === "1"; } catch { return false; }
  });
  // Reset dismissed state when navigating between screens so each
  // screen's coach tip has its own independent dismissal.
  React.useEffect(() => {
    try { setDismissed(sessionStorage.getItem(dismissKey) === "1"); } catch { setDismissed(false); }
  }, [dismissKey]);
  if (!areas?.[current]) return null;
  const t = tips[current];
  if (!t) return null;
  if (dismissed) return null;
  return (
    <div className="coach" style={{ ...t.pos, paddingRight: 36 }}>
      <span className="k">{t.k}</span>
      {t.msg}
      <button
        type="button"
        className="coach-x"
        aria-label="Dismiss coach tip"
        title="Dismiss this tip for the current browser session"
        onClick={() => {
          try { sessionStorage.setItem(dismissKey, "1"); } catch {}
          setDismissed(true);
        }}
        style={{
          position: "absolute", top: 6, right: 6,
          width: 22, height: 22, padding: 0, borderRadius: 6,
          border: "1px solid var(--border-strong)",
          background: "transparent", color: "var(--text)",
          cursor: "pointer", lineHeight: 1, fontSize: 14,
          display: "grid", placeItems: "center",
        }}
      >
        ×
      </button>
    </div>
  );
}

// Command palette (Ctrl-K / Cmd-K). Uses the accessible <Modal> primitive.
function CommandPalette({ open, onClose, setCurrent, setLineageOpen, pushToast }) {
  const [q, setQ] = React.useState("");
  React.useEffect(() => { if (open) setQ(""); }, [open]);
  const cmds = [
    ...SCREENS.flatMap(g => g.items).map(it => ({ kind: "Screen", label: `Go to ${it.label}`, id: it.id })),
    { kind: "Screen", label: "Go to Inference", id: "inference" },
    { kind: "Action", label: "Open lineage drawer", id: "lineage" },
    { kind: "Action", label: "Promote candidate model", id: "promote" },
    { kind: "Action", label: "Pin to warehouse v2026.05", id: "pin" },
    { kind: "Action", label: "Launch a hyperparameter sweep", id: "sweep" },
    { kind: "Settings", label: "Advanced — Structure preparation",  id: "advanced:structure_preparation" },
    { kind: "Settings", label: "Advanced — Featurizer (encoders)",  id: "advanced:featurizer_advanced" },
    { kind: "Settings", label: "Advanced — Training",               id: "advanced:training_advanced" },
    { kind: "Settings", label: "Advanced — Inference",              id: "advanced:inference_advanced" },
    { kind: "Settings", label: "Advanced — Eval & analytics",       id: "advanced:eval_analytics" },
  ];
  const filtered = q ? cmds.filter(c => c.label.toLowerCase().includes(q.toLowerCase())) : cmds;

  const runAction = (id) => {
    switch (id) {
      case "lineage":
        setLineageOpen && setLineageOpen(true);
        return;
      case "promote":
        setCurrent("promote");
        return;
      case "pin":
        // No backend in the prototype; surface what the action would do.
        pushToast && pushToast({
          title: "Pinned to warehouse v2026.05",
          body: "Would write the pin to project config (ds_kc3_v3 → v2026.05) and trigger an embedding-cache warm-up.",
          level: "ok",
        });
        return;
      case "sweep":
        setCurrent("pipeline");
        pushToast && pushToast({
          title: "Open the Pipeline screen and switch mode → Sweep",
          body: "Pick `Sweep` to expose the sampler / pruner / search-space panel, then `Launch sweep`.",
          level: "info",
        });
        return;
      default:
        pushToast && pushToast({ title: "Action triggered", body: id, level: "info" });
    }
  };

  return (
    <Modal open={open} onClose={onClose} ariaLabel="Command palette" size="md">
      <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 4px 12px" }}>
        <Ico name="search" />
        <input className="input" autoFocus placeholder="Search screens, runs, commands…" value={q} onChange={e => setQ(e.target.value)} style={{ border: "none", background: "transparent", flex: 1 }} />
        <kbd className="hint">esc</kbd>
      </div>
      <div style={{ maxHeight: 320, overflow: "auto", margin: "0 -18px -16px" }}>
        {filtered.length === 0 && <div style={{ padding: 18, color: "var(--dim)", fontSize: 12 }}>No match.</div>}
        {filtered.map((c, i) => (
          <button type="button" key={i}
            style={{ width: "100%", border: 0, background: "transparent", textAlign: "left", padding: "8px 18px", display: "flex", alignItems: "center", gap: 8, cursor: "pointer", borderTop: "1px solid var(--border-soft)", color: "var(--text)" }}
            onClick={() => {
              if (c.kind === "Screen") { setCurrent(c.id); onClose(); return; }
              if (c.kind === "Settings" && c.id.startsWith("advanced:")) {
                const panel = c.id.split(":")[1];
                onClose();
                requestAnimationFrame(() => {
                  window.dispatchEvent(new CustomEvent("open-advanced", { detail: { panel } }));
                });
                return;
              }
              if (c.kind === "Action") { onClose(); requestAnimationFrame(() => runAction(c.id)); return; }
              onClose();
            }}>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", textTransform: "uppercase", letterSpacing: "0.08em", width: 70 }}>{c.kind}</span>
            <span style={{ fontSize: 13, flex: 1 }}>{c.label}</span>
            <Ico name="arrowR" size={11} style={{ color: "var(--dim)" }} />
          </button>
        ))}
      </div>
    </Modal>
  );
}

// AdvancedButton — a chip that screens drop next to their primary CTA
// to open the matching PS_DEEP_SETTINGS panel. Shows a count badge when
// any field has been overridden from default.
function AdvancedButton({ panelKey, openAdvanced, deltaCount, children }) {
  const n = deltaCount || 0;
  const label = (window.PS_DEEP_SETTINGS || {})[panelKey]?.label || "Advanced";
  return (
    <button type="button" className="advanced-btn"
      onClick={() => openAdvanced(panelKey)}
      aria-label={`Open advanced settings: ${label}${n ? ` (${n} overridden)` : ""}`}>
      <Ico name="settings" size={12} /> {children || "Advanced"}
      {n > 0 && <span className="count" title={`${n} setting${n === 1 ? "" : "s"} overridden from default`}>{n}</span>}
    </button>
  );
}

// AdvancedSettingsModal — full SettingsPanel inside an accessible Modal,
// with Reset-to-defaults and Save (no-op in the prototype — values live
// in App state until backend wiring lands).
function AdvancedSettingsModal({ panelKey, values, onChange, onReset, onClose, deltaCount }) {
  const panel = panelKey ? (window.PS_DEEP_SETTINGS || {})[panelKey] : null;
  // Render nothing at all when there's no panel — avoids a no-op Modal with
  // a dangling aria-labelledby and lets the Modal's focus-trap effects stay
  // properly torn down between panel switches.
  if (!panelKey || !panel) return null;
  // The keyed inner component remounts whenever panelKey changes, so:
  //   * Modal's openerRef is recaptured for the new launcher.
  //   * SettingsPanel collapse state resets.
  //   * filterTier resets.
  return <AdvancedSettingsModalInner
    key={panelKey}
    panelKey={panelKey}
    panel={panel}
    values={values}
    onChange={onChange}
    onReset={onReset}
    onClose={onClose}
    deltaCount={deltaCount}
  />;
}

function AdvancedSettingsModalInner({ panelKey, panel, values, onChange, onReset, onClose, deltaCount }) {
  const [filterTier, setFilterTier] = React.useState("all");
  return (
    <Modal
      open={true}
      onClose={onClose}
      title={panel.label}
      titleIco="settings"
      size="xl"
      ariaLabel={`Advanced — ${panel.label}`}
      footer={
        <>
          <button className="btn ghost" onClick={onReset}>
            <Ico name="bolt" size={11} /> Reset to defaults
          </button>
          <div style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>
            {deltaCount} override{deltaCount === 1 ? "" : "s"}
          </span>
          <button className="btn" onClick={onClose}>Done</button>
        </>
      }
    >
      <div style={{ display: "flex", gap: 12, alignItems: "center", margin: "-4px 0 12px", padding: "8px 0 8px", borderBottom: "1px solid var(--border-soft)" }}>
        <div style={{ fontSize: 12, color: "var(--muted)", flex: 1 }}>{panel.sub}</div>
        {/* role="group" + aria-pressed (toggle-button semantics), NOT role="tablist" —
            we're not switching tabpanels, we're toggling a filter. */}
        <div className="toggle" role="group" aria-label="Filter by tier">
          {[
            { id: "all",     label: "All" },
            { id: "stable",  label: "Stable" },
            { id: "release", label: "Production only" },
          ].map(t => (
            <button key={t.id} type="button" aria-pressed={filterTier === t.id} onClick={() => setFilterTier(t.id)}>{t.label}</button>
          ))}
        </div>
      </div>
      <SettingsPanel
        panelKey={panelKey}
        values={values}
        onChange={onChange}
        filterTier={filterTier}
      />
    </Modal>
  );
}

// ── PyRosetta license panel ────────────────────────────────────────
// Shown inside the Tweaks panel. Reads /api/v2/system/rosetta on mount
// so the user sees the current state (platform support, whether a
// license is configured, whether PyRosetta is actually loaded), and
// posts to /api/v2/system/rosetta/install when the user fills in
// credentials and hits "Install".
function RosettaLicensePanel() {
  const [status, setStatus] = React.useState(null);
  const [user,   setUser]   = React.useState("");
  const [pw,     setPw]     = React.useState("");
  const [licensePath, setLicensePath] = React.useState("");
  const [busy,   setBusy]   = React.useState(false);
  const [msg,    setMsg]    = React.useState("");

  const refresh = React.useCallback(() => {
    fetch("/api/v2/system/rosetta")
      .then(r => r.json())
      .then(j => setStatus(j))
      .catch(() => setStatus({error: "fetch_failed"}));
  }, []);
  React.useEffect(() => { refresh(); }, [refresh]);

  const onInstall = async () => {
    setBusy(true); setMsg("");
    try {
      const r = await fetch("/api/v2/system/rosetta/install", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ user, password: pw, license_path: licensePath }),
      });
      const j = await r.json();
      setMsg(j.install?.hint || j.install?.status || j.error || "no response");
      setStatus(j.status);
    } catch (e) {
      setMsg(`error: ${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const supported  = status?.platform_supported;
  const loaded     = status?.loaded;
  const platform   = status?.platform || "?";

  return (
    <div style={{ fontSize: 11, color: "var(--muted)", display: "flex", flexDirection: "column", gap: 6 }}>
      <div>
        Platform: <span style={{ fontFamily: "var(--font-mono)", color: "var(--text)" }}>{platform}</span>
        {" · "}
        {loaded && status?.license_acknowledged
          ? <span style={{ color: "var(--signal)" }}>Rosetta loaded · license acknowledged</span>
          : loaded
            ? <span style={{ color: "var(--warn)" }}>PyRosetta loaded · awaiting license acknowledgement</span>
            : (supported
                ? <span style={{ color: "var(--warn)" }}>not installed</span>
                : <span style={{ color: "var(--dim)" }}>Windows wheels not published</span>)}
      </div>
      {/* Fake-setta vs real Rosetta — be precise about what the user is
          actually getting in their training inputs. */}
      <div style={{
        fontSize: 10, lineHeight: 1.5, color: "var(--text)",
        background: "var(--surface-3)",
        border: "1px solid var(--border)", borderRadius: 4, padding: "6px 8px",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
          <span style={{ width: 8, height: 8, borderRadius: 4,
            background: (loaded && status?.license_acknowledged) ? "var(--signal)" : "var(--warn)" }} />
          <strong style={{ color: "var(--text-strong)" }}>
            {(loaded && status?.license_acknowledged) ? "Real Rosetta active" : "Fake-setta active"}
          </strong>
        </div>
        {(loaded && status?.license_acknowledged) ? (
          <span style={{ color: "var(--muted)" }}>
            The <span className="mono">protein_rosetta_reu</span> featurizer
            is producing real ref2015 scores from your local Rosetta install.
            Fake-setta remains available if you want side-by-side comparison.
          </span>
        ) : (
          <span style={{ color: "var(--muted)" }}>
            Features picked on the Features screen are computed by{" "}
            <span className="mono">protein_fakesetta</span> — a Python
            approximation of ref2015 with the SAME 19 term names as real
            Rosetta. The numbers are NOT calibrated to REU units. To
            activate real Rosetta: (1) install PyRosetta locally per the
            Rosetta Commons Academic License, (2) set{" "}
            <span className="mono">ROSETTA_LICENSE_ACKNOWLEDGED=1</span> in
            the environment, (3) restart the v2 server.
          </span>
        )}
      </div>
      <input type="text" placeholder="License path (optional)"
        value={licensePath}
        onChange={e => setLicensePath(e.target.value)}
        style={{ background: "var(--surface-3)", color: "var(--text)", border: "1px solid var(--border)", padding: "4px 6px", fontSize: 11, fontFamily: "var(--font-mono)" }} />
      <input type="text" placeholder="Academic user"
        value={user}
        onChange={e => setUser(e.target.value)}
        style={{ background: "var(--surface-3)", color: "var(--text)", border: "1px solid var(--border)", padding: "4px 6px", fontSize: 11, fontFamily: "var(--font-mono)" }} />
      <input type="password" placeholder="Academic password"
        value={pw}
        onChange={e => setPw(e.target.value)}
        style={{ background: "var(--surface-3)", color: "var(--text)", border: "1px solid var(--border)", padding: "4px 6px", fontSize: 11, fontFamily: "var(--font-mono)" }} />
      <div style={{ display: "flex", gap: 4 }}>
        <button type="button" className="btn sm primary"
                disabled={busy || (!supported && !licensePath && !user)}
                onClick={onInstall}>
          {busy ? "Installing…" : "Install / verify"}
        </button>
        <button type="button" className="btn sm ghost" onClick={refresh}>Refresh</button>
      </div>
      {msg && <div style={{ fontSize: 10, color: "var(--muted)", fontStyle: "italic" }}>{msg}</div>}
      {!supported && (
        <div style={{ fontSize: 10, lineHeight: 1.4, color: "var(--dim)" }}>
          PyRosetta wheels are Linux/Mac only. The model studio still runs
          on Windows via a calibrated fallback (RDKit + sequence-based
          approximation of the dominant ref2015 score terms). For real
          Rosetta REU values, run the server under WSL with the Linux wheel.
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
