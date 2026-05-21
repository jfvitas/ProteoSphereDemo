// ProteoSphere — Goal & Binding Type screen
//
// The new STEP 01 (before Dataset). A single decision drives everything
// downstream:
//   - selected binding-type writes window.PS_DATA.binding_type
//   - downstream screens read it to filter sources, name the Splits
//     header, and seed the Pipeline preset gallery
//
// Ported from the flow-v4 design comp at
// proteosphere/project/flow-v4/screen-goal.jsx. The comp was a
// stand-alone page; this version drops the page chrome (already in
// the app shell) and follows the existing screen-* container pattern
// (`<div className="screen">` + `<StepRail>` + content).

function ScreenGoal({ setCurrent, pushToast }) {
  const D = window.PS_DATA;
  const toast = pushToast || window.pushToast || (() => {});

  // Persisted selection — defaults to the existing PS_DATA value, or
  // to the first available binding type when nothing's been picked yet.
  const initialBinding = D.binding_type
    || (window.PS_BINDING_TYPES.find(b => b.status === "available")?.id)
    || window.PS_BINDING_TYPES[0].id;
  const [selected, setSelected] = React.useState(initialBinding);
  const [objective, setObjective] = React.useState(D.design_objective || "generalization");
  const [explainOpen, setExplainOpen] = React.useState(null);

  // Persist + cascade to the rest of the app.
  React.useEffect(() => { D.binding_type = selected; }, [selected]);
  React.useEffect(() => { D.design_objective = objective; }, [objective]);
  // Backwards-compat: seed `D.binding_partners` so the existing Splits +
  // Pipeline screens (which read `binding_partners`) see the right
  // partners without needing edits today.
  React.useEffect(() => {
    const bt = window.PS_BINDING_TYPES.find(b => b.id === selected);
    if (!bt) return;
    const partners = new Set();
    if (selected.startsWith("pl") || selected === "complex_l") partners.add("pl");
    if (selected.startsWith("pp") || selected === "signaling" || selected === "ab_ag") partners.add("pp");
    D.binding_partners = partners;
  }, [selected]);

  const bt = window.PS_BINDING_TYPES.find(b => b.id === selected)
    || window.PS_BINDING_TYPES[0];

  return (
    <div className="screen" data-screen-label="00 Goal">
      <StepRail active="goal" onClick={setCurrent} />

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 00 · GOAL</div>
          <h2 style={{ margin: 0, marginTop: 4 }}>Goal &amp; <Term word="binding type">binding type</Term></h2>
          <p className="lead" style={{ marginTop: 6 }}>
            One decision drives everything downstream. Pick the kind of bound thing you're
            modelling — we'll filter the dataset, recommend a split policy, and seed the
            pipeline gallery accordingly.
          </p>
        </div>
        <div style={{ flex: 1 }} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 340px", gap: 22, alignItems: "flex-start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>

          {/* ── Binding type picker ──────────────────────────── */}
          <div className="card">
            <div className="card-h">
              <span className="t">Binding type</span>
              <span className="sub">{window.PS_BINDING_TYPES.length} types · feature coverage shown per-type</span>
              <div style={{ flex: 1 }} />
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
                <span style={{ display: "inline-block", width: 8, height: 8, background: "var(--signal)", borderRadius: 2, marginRight: 4 }} />full ·
                <span style={{ display: "inline-block", width: 8, height: 8, background: "var(--warn)", borderRadius: 2, marginRight: 4, marginLeft: 6 }} />partial ·
                <span style={{ display: "inline-block", width: 8, height: 8, background: "transparent", border: "1px dashed var(--error)", borderRadius: 2, marginRight: 4, marginLeft: 6 }} />none
              </span>
            </div>
            <div style={{ padding: 14 }}>
              <div className="binding-grid">
                {window.PS_BINDING_TYPES.map(b => (
                  <BindingCard key={b.id} b={b}
                    selected={selected === b.id}
                    onSelect={() => {
                      if (b.status === "needs_ingest") { setExplainOpen(b); return; }
                      setSelected(b.id);
                      toast({ title: `Binding type → ${b.label}`,
                        body: `Dataset sources will filter to ${b.sources.map(s => s.label).join(" / ")}.`,
                        level: "info", ttl_ms: 2400 });
                    }}
                    onExplain={() => setExplainOpen(b)}
                  />
                ))}
              </div>
            </div>
          </div>

          {/* ── Design objective ─────────────────────────────── */}
          <div className="card">
            <div className="card-h">
              <span className="t">Design objective</span>
              <span className="sub">Drives the recommended split policy on the next step</span>
            </div>
            <div style={{ padding: 14 }}>
              <div className="objective-card">
                {window.PS_DESIGN_OBJECTIVES.map(o => {
                  // Our PS_DESIGN_OBJECTIVES carries a richer shape than the
                  // flow-v4 comp's; pull the right fields out either way.
                  const desc = o.desc || o.sub || "";
                  const recommends = o.recommends
                    || `Recommended split: ${(o.recommendedPolicies || ["random"])[0]}`;
                  return (
                    <div key={o.id} className="objective-pick" data-on={objective === o.id} onClick={() => setObjective(o.id)}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <span className="checkbox" data-on={objective === o.id ? "true" : "false"} />
                        <span className="name">{o.label}</span>
                      </div>
                      <div className="desc">{desc}</div>
                      <div className="recommends">↳ recommends: {recommends}</div>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>

        </div>

        {/* ── Sticky sidebar: selection summary ──────────────── */}
        <div className="sticky-sidebar">
          <div className="card">
            <div className="card-h"><span className="t">Selection summary</span></div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
              <div className="binding-banner" style={{ margin: 0, padding: 10, background: "transparent", borderLeftWidth: 3 }}>
                <div className="badge"><Ico name={bt.icon || "molecule"} size={20} /></div>
                <div className="meta">
                  <div className="title" style={{ fontSize: 13 }}>{bt.label}</div>
                  <div className="sub">{bt.what}</div>
                </div>
              </div>
              <div>
                {bt.items != null ? (
                  <div className="summary-row"><span className="k">Bound items</span><span className="v">{fmt.short(bt.items)}</span></div>
                ) : (
                  <div className="summary-row"><span className="k">Bound items</span><span className="v" style={{ color: "var(--warn)" }}>needs ingest</span></div>
                )}
                {bt.unique?.proteins != null && <div className="summary-row"><span className="k">Unique proteins</span><span className="v">{fmt.short(bt.unique.proteins)}</span></div>}
                {bt.unique?.ligands != null && <div className="summary-row"><span className="k">Unique ligands</span><span className="v">{fmt.short(bt.unique.ligands)}</span></div>}
                {bt.unique?.complexes != null && <div className="summary-row"><span className="k">Complexes</span><span className="v">{fmt.short(bt.unique.complexes)}</span></div>}
                <div className="summary-row"><span className="k">Label type</span><span className="v">{bt.labels}</span></div>
                <div className="summary-row"><span className="k">Tier</span><span className="v"><TierPill tier={bt.tier} /></span></div>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-h"><span className="t">Downstream impact</span><span className="sub">what this picks for you</span></div>
            <div style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
              <DownstreamRow ico="dataset" lbl="Dataset"
                hint={`Filters to sources tagged ${bt.sources.map(s => s.label).join(" / ")}.`} />
              <DownstreamRow ico="split" lbl="Splits"
                hint={objective === "generalization"
                  ? "Leakage-aware cluster split · cold-target test ≥ 10%"
                  : "Scaffold split (ligand-side) · standard k-fold"} />
              <DownstreamRow ico="feature" lbl="Features"
                hint={`${countActive(bt)} of 4 feature axes have full coverage`} />
              <DownstreamRow ico="pipeline" lbl="Pipeline"
                hint={
                  selected.startsWith("pl") ? "DeepDTA · GraphDTA · DrugBAN · StructGNN" :
                  selected.startsWith("pp") ? "PPI-Siamese · two-tower"                  :
                  selected.startsWith("ab") ? "AbFlex (planned)"                         :
                  "Two-tower w/ pathway aux"
                } />
            </div>
          </div>

          <button className="btn primary" style={{ justifyContent: "center", padding: "12px 16px", fontSize: 14 }}
            disabled={bt.status === "needs_ingest"}
            onClick={() => setCurrent("dataset")}>
            Continue to dataset <Ico name="chevR" />
          </button>
          {bt.status === "needs_ingest" && (
            <div style={{ fontSize: 11, color: "var(--warn)", padding: "8px 12px", background: "var(--warn-soft)",
              border: "1px solid var(--warn)", borderRadius: "var(--r)", lineHeight: 1.5 }}>
              ⚠ This binding type is gated. {bt.needs}
            </div>
          )}
        </div>
      </div>

      {/* ── Explain modal (for blocked / planned cards) ───── */}
      <Modal open={!!explainOpen} onClose={() => setExplainOpen(null)}
        title={explainOpen ? `${explainOpen.label} · why gated?` : ""} size="md">
        {explainOpen && (
          <div style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text)" }}>
            <p>{explainOpen.desc}</p>
            <div style={{ padding: 12, background: "var(--warn-soft)", border: "1px solid var(--warn)", borderRadius: "var(--r)", margin: "12px 0" }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--warn)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>
                What's missing
              </div>
              {explainOpen.needs}
            </div>
            <div style={{ marginTop: 14 }}>
              <h3 style={{ marginBottom: 6, fontSize: 13 }}>Sources we expect to use</h3>
              <ul style={{ paddingLeft: 18, color: "var(--muted)", fontSize: 12 }}>
                {explainOpen.sources.map(s => <li key={s.id}>{s.label} · ~{fmt.short(s.n)} bound items</li>)}
              </ul>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
}

// ── Binding-type card ────────────────────────────────────────────────
function BindingCard({ b, selected, onSelect, onExplain }) {
  const blocked = b.status === "needs_ingest";
  return (
    <div className="bind-card"
      data-selected={selected}
      data-disabled={blocked}
      onClick={blocked ? onExplain : onSelect}
      role="button" tabIndex={0}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); (blocked ? onExplain : onSelect)(); } }}
    >
      <div className="head">
        <div className="icon"><Ico name={b.icon || "molecule"} size={22} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 className="title">{b.label}</h3>
          <div className="what">{b.what}</div>
          <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", gap: 4 }}>
            <TierPill tier={b.tier} />
            <Chip tone="dim">{b.labels}</Chip>
          </div>
        </div>
      </div>
      <div className="body">{b.desc}</div>
      <div className="counts">
        {b.items != null
          ? <Stat k="bound items" v={fmt.short(b.items)} mono />
          : <Stat k="bound items" v="—" mono />}
        {b.unique?.proteins != null
          ? <Stat k="proteins" v={fmt.short(b.unique.proteins)} mono />
          : <Stat k="proteins" v="—" mono />}
        {b.unique?.ligands != null
          ? <Stat k="ligands" v={fmt.short(b.unique.ligands)} mono />
          : b.unique?.complexes != null
            ? <Stat k="complexes" v={fmt.short(b.unique.complexes)} mono />
            : <Stat k="ligands" v="—" mono />}
      </div>
      <div className="sources">
        {b.sources.map(s => <Chip key={s.id}><span style={{ color: "var(--text)" }}>{s.label}</span></Chip>)}
      </div>
      <div className="coverage" title={b.coverage_note}>
        {[
          { name: "seq",  v: b.coverage.sequence },
          { name: "3D",   v: b.coverage.structure },
          { name: "REU",  v: b.coverage.rosetta },
          { name: "path", v: b.coverage.pathway },
        ].map(ax => (
          <div key={ax.name} className="ax">
            <div className="glyph" data-cov={covToLevel(ax.v)}>{covGlyph(ax.v)}</div>
            <div className="name">{ax.name}</div>
          </div>
        ))}
      </div>
      {blocked && (
        <div className="blocked-note">
          <Ico name="warn" /> needs ingest · click to read
        </div>
      )}
    </div>
  );
}

function DownstreamRow({ ico, lbl, hint }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      <div style={{ width: 24, height: 24, borderRadius: 5, background: "var(--surface-3)", color: "var(--muted)",
        display: "grid", placeItems: "center", flexShrink: 0 }}>
        <Ico name={ico} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12, color: "var(--text-strong)", fontWeight: 500 }}>{lbl}</div>
        <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", lineHeight: 1.5 }}>{hint}</div>
      </div>
    </div>
  );
}

function covToLevel(v) {
  return v === "full" ? "full" : v === "partial" ? "partial" : v === "thin" ? "thin" : "none";
}
function covGlyph(v) {
  return v === "full" ? "✓" : v === "partial" ? "◑" : v === "thin" ? "◔" : "✗";
}
function countActive(bt) {
  return Object.values(bt.coverage).filter(v => v === "full").length;
}

window.ScreenGoal = ScreenGoal;
