// ProteoSphere — Features screen
//
// The new STEP 03 (between Splits and Pipeline). User picks which
// featurizers describe each training example. Selection is persisted
// to PS_DATA.feature_selection + PS_DATA.feature_manifest and auto-
// populates the Inputs section of the flow-builder palette.
//
// Ported from the flow-v4 design comp at
// proteosphere/project/flow-v4/screen-features.jsx with the standard
// app-shell adaptations (screen container, StepRail, setCurrent prop,
// remove TweaksPanel / BrandMark / page-h).

function ScreenFeatures({ setCurrent, pushToast }) {
  const D = window.PS_DATA;
  const toast = pushToast || window.pushToast || (() => {});
  const FEATS = window.PS_FEATURES;

  // Initial selection: restore from PS_DATA, or fall back to `default: true`
  // rows the comp ships with.
  const initial = React.useMemo(() => {
    const prev = D.feature_selection;
    if (prev && typeof prev === "object") {
      // Flatten the per-axis arrays back into a Set of ids.
      const ids = new Set();
      for (const ax of Object.values(prev)) for (const id of (ax || [])) ids.add(id);
      if (ids.size > 0) return ids;
    }
    const out = new Set();
    for (const ax of Object.values(FEATS))
      for (const f of ax) if (f.default) out.add(f.id);
    return out;
  }, []);
  const [picked, setPicked] = React.useState(initial);
  const [bundle, setBundle] = React.useState(null);
  const [previewing, setPreviewing] = React.useState(null);

  // Persist the selection back to PS_DATA on every change. The Pipeline
  // screen's flow builder reads PS_DATA.feature_selection to populate
  // the Inputs section of the palette.
  React.useEffect(() => {
    const out = { protein: [], ligand: [], interaction: [] };
    for (const [axis, feats] of Object.entries(FEATS)) {
      for (const f of feats) if (picked.has(f.id)) out[axis].push(f.id);
    }
    D.feature_selection = out;
    // Build a flat manifest with full spec for downstream validation.
    const manifest = {};
    for (const [axis, feats] of Object.entries(FEATS)) {
      for (const f of feats) if (picked.has(f.id)) manifest[f.id] = { ...f, axis };
    }
    D.feature_manifest = manifest;
  }, [picked]);

  const toggle = (id, disabled) => {
    if (disabled) return;
    setPicked(p => {
      const next = new Set(p);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
    setBundle(null);    // any manual change deactivates the bundle chip
  };

  const applyBundle = (b) => {
    setPicked(new Set(b.features));
    setBundle(b.id);
    toast({ title: `Applied bundle: ${b.label}`, body: b.desc, level: "info", ttl_ms: 2400 });
  };

  // Aggregate stats over the selection.
  const all = Object.values(FEATS).flat();
  const pickedRows = all.filter(f => picked.has(f.id));
  const pickedByKind = pickedRows.reduce((a, f) => ({ ...a, [f.kind]: (a[f.kind] || 0) + 1 }), {});
  const cacheGB = pickedRows.reduce((a, f) => a + (
    f.cost === "trivial" ? 0.01 : f.cost === "fast" ? 0.1 : f.cost === "moderate" ? 0.6 : 2.0), 0);

  const warnings = pickedRows.filter(f =>
    f.status === "platform_limited" || f.status === "needs_cache" || f.status === "planned");

  const suggested = suggestPresets(pickedRows);

  return (
    <div className="screen" data-screen-label="03 Features">
      <StepRail active="features" onClick={setCurrent} />

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 03 · FEATURES</div>
          <h2 style={{ margin: 0, marginTop: 4 }}>What describes each training example?</h2>
          <p className="lead" style={{ marginTop: 6 }}>
            Pick what <em>describes</em> each training example before deciding <em>how to model it</em>.
            Choices here auto-populate the Inputs section on the Pipeline tab — every feature
            you tick becomes a draggable input block in the flow builder.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn ghost" onClick={() => setPicked(new Set())} disabled={picked.size === 0}>
          <Ico name="archive" /> Clear all
        </button>
        <button className="btn primary" disabled={picked.size === 0}
          onClick={() => setCurrent("pipeline")}>
          Continue to pipeline <Ico name="chevR" />
        </button>
      </div>

      <BindingBanner setCurrent={setCurrent} />

      <div className="bundle-strip">
        <Ico name="zap" size={12} style={{ color: "var(--warn)" }} />
        <span className="k">Bundles</span>
        {window.PS_FEATURE_BUNDLES.map(b => (
          <div key={b.id} className="bundle-chip" data-on={bundle === b.id}
            onClick={() => applyBundle(b)} title={b.desc}>
            {b.label}
            <span className="count">{b.features.length}</span>
          </div>
        ))}
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
          curated starting points · per-feature overrides below
        </span>
      </div>

      <div className="features-grid">
        <div>
          {Object.entries(FEATS).map(([axis, feats]) => (
            <div key={axis} className="axis-group">
              <div className="head">
                <AxisBadge axis={axis} />
                <span className="name">{axis === "protein" ? "Protein axis" : axis === "ligand" ? "Ligand axis" : "Interaction axis"}</span>
                <span className="line" />
                <span className="count">{feats.filter(f => picked.has(f.id)).length} / {feats.length} picked</span>
              </div>
              {feats.map(f => (
                <FeatureRow key={f.id} f={f}
                  picked={picked.has(f.id)}
                  onToggle={() => toggle(f.id, isDisabled(f))}
                  onPreview={() => setPreviewing(f)} />
              ))}
            </div>
          ))}
        </div>

        {/* Sticky right sidebar */}
        <div className="sticky-sidebar">
          <div className="card">
            <div className="card-h"><span className="t">Selection summary</span></div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 4 }}>
              <div className="summary-row"><span className="k">Features picked</span><span className="v">{picked.size}</span></div>
              {Object.entries(pickedByKind).map(([kind, n]) => (
                <div key={kind} className="summary-row">
                  <span className="k">· {kind}</span>
                  <span className="v">{n}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-h"><span className="t">Estimated cost</span><span className="sub">first epoch on this binding type</span></div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 4 }}>
              <div className="summary-row"><span className="k">Cache (disk)</span><span className="v">≈ {cacheGB.toFixed(2)} GB</span></div>
              <div className="summary-row"><span className="k">Batch RAM</span><span className="v">≈ {(cacheGB * 200).toFixed(0)} MB / batch 64</span></div>
              <div className="summary-row"><span className="k">Per-example shape</span><span className="v" style={{ fontSize: 10, textAlign: "right" }}>{shapeSummary(pickedRows)}</span></div>
              <div className="summary-row"><span className="k">Recommended batch</span><span className="v">{cacheGB > 1 ? "32" : cacheGB > 0.5 ? "48" : "64"}</span></div>
            </div>
          </div>

          {warnings.length > 0 && (
            <div className="card" style={{ borderLeft: "3px solid var(--warn)" }}>
              <div className="card-h"><span className="t" style={{ color: "var(--warn)" }}>{warnings.length} callout{warnings.length === 1 ? "" : "s"}</span></div>
              <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                {warnings.map(f => (
                  <div key={f.id} style={{ fontSize: 11.5, color: "var(--muted)", lineHeight: 1.55 }}>
                    <span style={{ color: "var(--warn)", fontWeight: 600 }}>{f.label}:</span> {warningCopy(f)}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-h"><span className="t">Suggested presets</span><span className="sub">based on what you picked</span></div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
              {suggested.map(p => (
                <div key={p.id} style={{ padding: 10, background: "var(--surface-2)", border: "1px solid var(--border)",
                  borderRadius: "var(--r)", cursor: "pointer" }}
                  onClick={() => {
                    toast({ title: `Will load preset: ${p.label}`,
                      body: `Heads up: the Pipeline flow-builder lands in the next integration stage. Continue lands you on the existing Pipeline editor for now.`,
                      level: "info", ttl_ms: 3000 });
                    setCurrent("pipeline");
                  }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
                    <Ico name="archive" size={11} style={{ color: "var(--primary)" }} />
                    <span style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-strong)" }}>{p.label}</span>
                    <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--dim)" }}>
                      {p.match}/{p.required} match
                    </span>
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.45 }}>{p.blurb}</div>
                </div>
              ))}
              {suggested.length === 0 && (
                <div style={{ fontSize: 11.5, color: "var(--dim)", fontStyle: "italic" }}>
                  No preset matches the current selection — you'll build from scratch on the next step. Fine.
                </div>
              )}
            </div>
          </div>

          <button className="btn primary"
            style={{ justifyContent: "center", padding: "12px 16px", fontSize: 14 }}
            disabled={picked.size === 0}
            onClick={() => setCurrent("pipeline")}>
            Continue to pipeline <Ico name="chevR" />
          </button>
        </div>
      </div>

      {/* Preview modal — small sample renderings per feature */}
      <Modal open={!!previewing} onClose={() => setPreviewing(null)}
        title={previewing?.preview?.title || (previewing ? `${previewing.label} · preview` : "")}
        size="md">
        {previewing && previewing.preview && <FeaturePreview p={previewing.preview} f={previewing} />}
        {previewing && !previewing.preview && (
          <div style={{ padding: "20px 0", color: "var(--muted)", fontSize: 13, lineHeight: 1.6 }}>
            <p>No cached preview for this feature yet. Production will render a small sample on a representative protein/ligand from the dataset.</p>
            <p style={{ color: "var(--dim)", marginTop: 12, fontFamily: "var(--font-mono)", fontSize: 11 }}>
              shape: {previewing.shape}<br />
              cost: {previewing.cost}<br />
              status: {previewing.status}
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ── Feature row ──────────────────────────────────────────────────────
function FeatureRow({ f, picked, onToggle, onPreview }) {
  const disabled = isDisabled(f);
  return (
    <div className="feat-row" data-on={picked && !disabled} data-disabled={disabled}>
      <div className="checkbox"
        data-on={picked && !disabled ? "true" : (disabled ? null : "false")}
        data-disabled={disabled}
        onClick={onToggle}
        role="checkbox" aria-checked={picked}
        tabIndex={0}
        onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); onToggle(); } }}
      />
      <div className="meta">
        <div className="title">
          {f.label}
          <span className="feat-badge" data-kind={f.kind}>{f.kind}</span>
        </div>
        <div className="desc">{f.desc}</div>
      </div>
      <div className="shape">{f.shape}</div>
      <div className="status">
        {f.status === "integrated"        && <Chip tone="signal">integrated</Chip>}
        {f.status === "planned"           && <Chip tone="warn">planned</Chip>}
        {f.status === "needs_cache"       && <Chip tone="warn">needs cache</Chip>}
        {f.status === "platform_limited"  && <Chip tone="error">{f.platform} only</Chip>}
      </div>
      <div className="cost">
        <Chip tone={costTone(f.cost)}>{f.cost}</Chip>
      </div>
      <div className="preview-link" onClick={onPreview} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onPreview(); } }}>
        <Ico name="preview" size={10} style={{ display: "inline-block", marginRight: 4 }} />
        preview
      </div>
    </div>
  );
}

// ── Per-axis badge ───────────────────────────────────────────────────
// Reuses the cat-badge styling (already in styles.css). Maps the
// feature axis name to a sensible cat slot.
function AxisBadge({ axis }) {
  // protein → preprocess (amber), ligand → encoder (violet), interaction → fusion (primary)
  const cat = axis === "interaction" ? "fusion" : axis === "ligand" ? "encoder" : "preprocess";
  return (
    <span className="cat-badge" data-cat={cat} style={{ width: 20, height: 20 }}>
      <Ico name={axis === "interaction" ? "link" : axis === "ligand" ? "molecule" : "helix"} size={11} />
    </span>
  );
}

function isDisabled(f) {
  return f.status === "platform_limited" || f.status === "planned";
}
function warningCopy(f) {
  if (f.status === "platform_limited") return `Disabled — ${f.platform} only on this machine. The model will run without it.`;
  if (f.status === "needs_cache")      return f.reason || "Needs a cache pass; will queue automatically.";
  if (f.status === "planned")          return f.reason || "Not yet integrated. Ignored at training time.";
  return "";
}
function costTone(c) {
  return c === "trivial" ? "dim" : c === "fast" ? "signal" : c === "moderate" ? "warn" : "error";
}

function shapeSummary(rows) {
  if (rows.length === 0) return "—";
  const vec = rows.filter(r => r.kind === "tabular" || r.kind === "embedding");
  const graphs = rows.filter(r => r.kind === "graph").length;
  const maps   = rows.filter(r => r.kind === "map").length;
  const parts = [];
  if (vec.length) parts.push(`vec[${vec.length}]`);
  if (graphs)     parts.push(`+ ${graphs} graph${graphs > 1 ? "s" : ""}`);
  if (maps)       parts.push(`+ ${maps} map${maps > 1 ? "s" : ""}`);
  return parts.join(" ");
}

// ── Pipeline auto-suggest ────────────────────────────────────────────
// Score each preset by how many of its expected features the user has
// ticked. Surfaces the top 3 matches. The presets here mirror the
// templates already registered in api/model_studio/v2/blocks.py's
// TEMPLATE_PRESETS so once the flow builder lands they map 1:1.
function suggestPresets(picked) {
  const ids = new Set(picked.map(p => p.id));
  const rules = [
    { id: "drugban",     label: "DrugBAN",     blurb: "ESM-2 protein + GIN ligand graph + bilinear fusion.",
      expects: ["esm2_650m", "mol_graph_2d"] },
    { id: "graphdta",    label: "GraphDTA",    blurb: "Protein-1D CNN + ligand GNN; concat fusion.",
      expects: ["mol_graph_2d"] },
    { id: "deepdta",     label: "DeepDTA",     blurb: "1-D CNN over both sides; concat + MLP head.",
      expects: ["ecfp4"] },
    { id: "struct_gnn_dta", label: "StructGNN-DTA", blurb: "Residue contact graph + ligand graph; bilinear fusion.",
      expects: ["res_contact", "mol_graph_2d"] },
    { id: "ppi_gnn_siamese", label: "PPI Siamese GNN", blurb: "Two protein graphs share an encoder; four-way fusion.",
      expects: ["res_contact"] },
    { id: "conplex",     label: "ConPLex",     blurb: "Two-tower; cheap-at-serve; InfoNCE ranking.",
      expects: ["esm2_650m", "ecfp4"] },
  ];
  return rules
    .map(r => ({ ...r, match: r.expects.filter(e => ids.has(e)).length, required: r.expects.length }))
    .filter(r => r.match >= 1)
    .sort((a, b) => (b.match / b.required) - (a.match / a.required))
    .slice(0, 3);
}

// ── Preview renderings ───────────────────────────────────────────────
// Tiny SVG samples for the three preview kinds the comp ships:
//   heatmap (ESM-2 PCA), graph (mol graph), bitmap (ECFP fingerprint).
function FeaturePreview({ p }) {
  if (p.kind === "heatmap") return <PreviewHeatmap />;
  if (p.kind === "graph")   return <PreviewGraph />;
  if (p.kind === "bitmap")  return <PreviewBitmap />;
  return null;
}

function PreviewHeatmap() {
  const rows = 8, cols = 90;
  const cells = [];
  for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) {
    const v = Math.sin(c * 0.18 + r * 0.6) * 0.5 + 0.5
            + (r === 2 && c > 60 && c < 75 ? 0.3 : 0);
    const hue = 220 - v * 200;
    cells.push(<rect key={r * cols + c} x={c * 8} y={r * 22} width="7" height="20"
      fill={`hsl(${hue}, 60%, ${30 + v * 35}%)`} />);
  }
  return (
    <div>
      <svg viewBox={`0 0 ${cols * 8} ${rows * 22}`} style={{ width: "100%", height: 200 }}>{cells}</svg>
      <div style={{ marginTop: 12, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", display: "flex", justifyContent: "space-between" }}>
        <span>residue 380</span>
        <span>residue 470</span>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 12, lineHeight: 1.55 }}>
        Top 8 principal components of ESM-2's per-residue activations, taken on BTK[380..470].
        The bright stripe in row 3 around residue 450 is the catalytic loop — ESM-2 encodes it
        distinctively even though it's never told about kinases.
      </div>
    </div>
  );
}

function PreviewGraph() {
  const atoms = [
    { x:  80, y:  80, l: "N" }, { x: 130, y:  60, l: "C" }, { x: 180, y:  80, l: "C" },
    { x: 180, y: 130, l: "C" }, { x: 130, y: 150, l: "C" }, { x:  80, y: 130, l: "C" },
    { x: 230, y:  60, l: "O" }, { x:  30, y:  60, l: "C" }, { x:  30, y: 130, l: "C" },
  ];
  const bonds = [[0,1],[1,2],[2,3],[3,4],[4,5],[5,0],[2,6],[0,7],[7,8],[8,5]];
  return (
    <div>
      <svg viewBox="0 0 280 200" style={{ width: "100%", height: 220, background: "var(--bg-soft)", borderRadius: "var(--r)" }}>
        {bonds.map(([i, j], k) => (
          <line key={k} x1={atoms[i].x} y1={atoms[i].y} x2={atoms[j].x} y2={atoms[j].y}
            stroke="var(--port-graph)" strokeWidth="2" />
        ))}
        {atoms.map((a, i) => (
          <g key={i}>
            <circle cx={a.x} cy={a.y} r={10} fill="var(--surface-3)" stroke="var(--port-graph)" strokeWidth="1.4" />
            <text x={a.x} y={a.y + 4} textAnchor="middle" fontSize="11" fontFamily="var(--font-mono)" fill="var(--text)">{a.l}</text>
          </g>
        ))}
      </svg>
      <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 12, lineHeight: 1.55 }}>
        Nodes are atoms (9-d feature vector each — element, formal charge, hybridization, …),
        edges are bonds (3-d — bond type, conjugation, ring).
      </div>
    </div>
  );
}

function PreviewBitmap() {
  const cells = [];
  for (let r = 0; r < 32; r++) for (let c = 0; c < 64; c++) {
    const seed = (r * 31 + c * 13 + r * c) % 23;
    const on = seed < 4;
    cells.push(<rect key={r * 64 + c} x={c * 7} y={r * 7} width="6" height="6"
      fill={on ? "var(--port-embedding)" : "var(--surface-2)"} />);
  }
  return (
    <div>
      <svg viewBox="0 0 448 224" style={{ width: "100%", height: 200 }}>{cells}</svg>
      <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
        2048 bits · 178 set ({(178 / 2048 * 100).toFixed(1)} %) · folded from 4096 raw circular substructure hashes
      </div>
    </div>
  );
}

window.ScreenFeatures = ScreenFeatures;
