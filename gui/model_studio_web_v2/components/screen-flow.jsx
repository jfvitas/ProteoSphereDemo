// ProteoSphere — Pipeline Flow Builder screen
//
// Composes the palette + canvas + inspector into the LabVIEW-style
// flow editor. Ported from proteosphere/project/flow-v4/screen-flow.jsx
// with the standard adaptations:
//   - Wrapped in the existing screen container + StepRail
//   - Uses setCurrent / pushToast props from the app shell
//   - Reads D.feature_selection from the Features screen
//   - Persists flow state to D.pipeline.flow

function ScreenFlow({ setCurrent, pushToast }) {
  const D = window.PS_DATA;
  const toast = pushToast || window.pushToast || (() => {});

  // ── Initial state: restore flow from PS_DATA or load the DrugBAN preset.
  const initialState = React.useMemo(() => {
    const saved = D.pipeline?.flow;
    if (saved && Array.isArray(saved.nodes) && saved.nodes.length > 0) {
      return { nodes: saved.nodes, edges: saved.edges || [] };
    }
    const preset = (window.PS_FLOW_PRESETS || []).find(p => p.id === "drugban");
    if (!preset) return { nodes: [], edges: [] };
    return {
      nodes: preset.layout.map(n => ({
        id: n.id, block_id: n.block_id, impl_id: n.impl_id,
        x: n.x, y: n.y, params: n.params || {},
      })),
      edges: preset.edges.map(e => ({ ...e })),
    };
  }, []);

  const [nodes, setNodes] = React.useState(initialState.nodes);
  const [edges, setEdges] = React.useState(initialState.edges);
  const [selectedId, setSelectedId] = React.useState(null);
  const [presetOpen, setPresetOpen] = React.useState(false);
  const [showInspector, setShowInspector] = React.useState(true);
  const [paletteDrag, setPaletteDrag] = React.useState(null);
  const [search, setSearch] = React.useState("");
  const [ctxMenu, setCtxMenu] = React.useState(null);
  const canvasRef = React.useRef(null);

  // Persist flow to PS_DATA so it survives screen navigation + reloads.
  React.useEffect(() => {
    if (!D.pipeline) D.pipeline = {};
    D.pipeline.flow_mode = "flow";
    D.pipeline.flow = { nodes, edges };
  }, [nodes, edges]);

  // Picks come from the Features screen. Empty array → palette won't filter
  // input cards (the Features tab is the source of truth; if user skipped
  // it we show every input block).
  const featuresPicked = React.useMemo(() => {
    const sel = D.feature_selection;
    if (!sel) return [];
    return [...(sel.protein || []), ...(sel.ligand || []), ...(sel.interaction || [])];
  }, [D.feature_selection]);

  // ── Palette drag system. Card onPointerDown bubbles up to here;
  // we follow the cursor with DragGhost and watch for release.
  const handlePaletteDragStart = (blockId, e) => {
    e.preventDefault();
    setPaletteDrag({ blockId, x: e.clientX, y: e.clientY });
    const onMove = (ev) => setPaletteDrag(p => p && ({ ...p, x: ev.clientX, y: ev.clientY }));
    const onUp = (ev) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup",   onUp);
      if (canvasRef.current) {
        const r = canvasRef.current.getBoundingClientRect();
        if (ev.clientX >= r.left && ev.clientX <= r.right &&
            ev.clientY >= r.top  && ev.clientY <= r.bottom) {
          const lx = ev.clientX - r.left + canvasRef.current.scrollLeft;
          const ly = ev.clientY - r.top  + canvasRef.current.scrollTop;
          dropBlock(blockId, lx, ly);
        }
      }
      setPaletteDrag(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup",   onUp);
  };

  function dropBlock(blockId, x, y) {
    const def = window.PS_FLOW_BLOCK_INDEX[blockId];
    if (!def) return;
    const nx = Math.max(0, Math.round((x - 100) / 24) * 24);
    const ny = Math.max(0, Math.round((y - 40)  / 24) * 24);
    const id = "n" + Date.now().toString().slice(-6);
    const node = {
      id, block_id: blockId, impl_id: def.impls[0].id,
      x: nx, y: ny, params: {},
    };
    setNodes(ns => [...ns, node]);
    setSelectedId(id);
  }

  const moveNode = (id, x, y) => setNodes(ns => ns.map(n => n.id === id ? { ...n, x, y } : n));
  const deleteNode = (id) => {
    setNodes(ns => ns.filter(n => n.id !== id));
    setEdges(es => es.filter(e => !e.from.startsWith(id + ":") && !e.to.startsWith(id + ":")));
    if (selectedId === id) setSelectedId(null);
  };
  const disconnectNode = (id) =>
    setEdges(es => es.filter(e => !e.from.startsWith(id + ":") && !e.to.startsWith(id + ":")));
  const updateImpl = (id, implId) =>
    setNodes(ns => ns.map(n => n.id === id ? { ...n, impl_id: implId, params: {} } : n));
  const updateParam = (id, key, value) =>
    setNodes(ns => ns.map(n => n.id === id ? { ...n, params: { ...n.params, [key]: value } } : n));
  const addEdge = (edge) => setEdges(es => {
    const filtered = es.filter(e => e.to !== edge.to);
    return [...filtered, edge];
  });
  const deleteEdge = (edge) =>
    setEdges(es => es.filter(e => !(e.from === edge.from && e.to === edge.to)));

  // ── Preset feature gating ──────────────────────────────────────────
  // For each preset, walk its input nodes and collect the feature_ids
  // they require. If any of those features aren't ticked on the Features
  // screen, the preset can't run — we gray it out + show a tooltip.
  // Returns { missing: [feature_id...], needed: [feature_id...] }.
  const presetGate = React.useCallback((preset) => {
    const idx = window.PS_FLOW_BLOCK_INDEX || {};
    const needed = new Set();
    for (const n of (preset.layout || [])) {
      const def = idx[n.block_id];
      if (def && def.cat === "input" && def.feature_id) needed.add(def.feature_id);
    }
    const picked = new Set(featuresPicked);
    // If the user hasn't visited the Features screen, treat as "all ok"
    // (the palette also short-circuits in that case).
    if (picked.size === 0) return { missing: [], needed: [...needed] };
    const missing = [...needed].filter(f => !picked.has(f));
    return { missing, needed: [...needed] };
  }, [featuresPicked]);

  // Lookup table for friendly feature labels in tooltips.
  const FEATURE_LABEL_INDEX = React.useMemo(() => {
    const out = {};
    const fs = window.PS_FEATURES || {};
    for (const axis of Object.values(fs)) {
      for (const f of axis) out[f.id] = f.label || f.id;
    }
    return out;
  }, []);

  const loadPreset = (preset) => {
    setNodes(preset.layout.map(n => ({
      id: n.id, block_id: n.block_id, impl_id: n.impl_id,
      x: n.x, y: n.y, params: n.params || {},
    })));
    setEdges(preset.edges.map(e => ({ ...e })));
    setSelectedId(null);
    setPresetOpen(false);
    toast({ title: `Loaded preset: ${preset.label}`, body: preset.blurb, level: "info", ttl_ms: 2400 });
  };

  const selectedNode = nodes.find(n => n.id === selectedId);
  const selectedDef  = selectedNode ? window.PS_FLOW_BLOCK_INDEX[selectedNode.block_id] : null;
  const selectedImpl = selectedDef ? (selectedDef.impls.find(i => i.id === selectedNode.impl_id) || selectedDef.impls[0]) : null;

  const validation = window.validateFlowGraph(nodes, edges);

  // Goal screen vs flow topology mismatch detector — surfaces a banner
  // when the user picked binding_type=pp_binary on Goal but built a P-L
  // flow on the canvas (or vice-versa). The trainer auto-corrects at
  // launch time (Stage 16), but a pre-launch hint saves a wasted click.
  const topologyWarning = React.useMemo(() => {
    const goalType = D.binding_type;
    if (!goalType || nodes.length === 0) return null;
    const inputBlocks = nodes
      .map(n => n.block_id)
      .filter(b => b && b.startsWith("in."));
    const proteinInputs = inputBlocks.filter(b =>
      b === "in.protein_seq" || b === "in.protein_graph" ||
      b === "in.protein_emb" || b === "in.protein_fakesetta").length;
    const ligandInputs = inputBlocks.filter(b => b.startsWith("in.ligand_")).length;
    const isPPI_flow = proteinInputs >= 2 && ligandInputs === 0;
    const isPL_flow  = proteinInputs >= 1 && ligandInputs >= 1;
    if (goalType === "pp_binary" && isPL_flow) {
      return {
        flow_topology: "protein-ligand",
        goal_type: "P-P (binary interaction)",
        suggested_benchmark: "kiba",
      };
    }
    if (goalType !== "pp_binary" && isPPI_flow) {
      return {
        flow_topology: "protein-protein",
        goal_type: goalType,
        suggested_benchmark: "hippie",
      };
    }
    return null;
  }, [nodes, D.binding_type]);

  return (
    <div className="screen" data-screen-label="04 Pipeline (flow)">
      <StepRail active="pipeline" onClick={setCurrent} />
      <PipelineModeToggle current="flow" setCurrent={setCurrent} />

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 14 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 04 · PIPELINE · FLOW BUILDER</div>
          <h2 style={{ margin: 0, marginTop: 4 }}>Compose the model graph</h2>
          <p className="lead" style={{ marginTop: 6 }}>
            Drag <Term word="block">blocks</Term> from the palette, wire ports by type. Role is fixed on
            drop; swap implementations in the inspector without re-wiring.
            Need a known-good baseline? Switch back to <strong>Prefab templates</strong> at the top of the page.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={() => setPresetOpen(true)}>
          <Ico name="archive" size={12} /> Load preset
        </button>
        <button className="btn ghost" onClick={() => setShowInspector(s => !s)}>
          {showInspector ? "Hide" : "Show"} inspector
        </button>
        <button className="btn primary" disabled={validation.state === "error"}
          onClick={async () => {
            // Quick stale-server probe before posting the launch. Hitting
            // /api/v2/pipeline/templates returns the live list of
            // supported template ids; if 'flow' isn't in it the server
            // is older than this build and the launch will 501. We toast
            // the user and bail BEFORE wasting their click + spinner
            // time on a guaranteed-failing POST.
            try {
              const probe = await fetch("/api/v2/pipeline/templates", { method: "GET" });
              if (probe.ok) {
                const info = await probe.json();
                if (Array.isArray(info.supported_templates)
                    && !info.supported_templates.includes("flow")) {
                  toast({
                    title: "Server restart needed",
                    body: ("Your on-disk build supports the flow builder, but the "
                        + "running server process loaded an older handlers.py and "
                        + "doesn't know about 'flow'. Python doesn't hot-reload "
                        + "modules — restart api/model_studio/server.py and try again."),
                    level: "warn", ttl_ms: 12000,
                  });
                  return;
                }
              }
              // If the probe 404s the server is even older (predates the
              // templates endpoint itself). Let the launch attempt run
              // so the user gets the existing 501 path.
            } catch (e) {
              // Network error — just proceed; the launch's own error
              // handling will catch it.
            }
            // Derive benchmark from the actual flow topology, not from
            // D.binding_type. Mismatches (e.g. binding_type='pp_binary'
            // but the flow has in.protein_emb + in.ligand_graph, a P-L
            // topology) caused the trainer to call load_warehouse_records
            // with 'hippie' (a PPI source), which raises ValueError.
            // Counting input blocks is a precise signal:
            //   2× in.protein_*  →  PPI       →  benchmark='hippie'
            //   1× in.protein_*  →  P-L (DTA) →  benchmark='kiba'
            const inputBlockIds = nodes
              .map(n => n.block_id)
              .filter(b => b && b.startsWith("in."));
            const proteinInputs = inputBlockIds.filter(b =>
              b === "in.protein_seq" || b === "in.protein_graph" ||
              b === "in.protein_emb" || b === "in.protein_fakesetta").length;
            const ligandInputs = inputBlockIds.filter(b => b.startsWith("in.ligand_")).length;
            const isPPI = proteinInputs >= 2 && ligandInputs === 0;
            const benchmark = isPPI ? "hippie" : "kiba";
            // Keep binding_type in sync with what the flow actually looks
            // like so downstream screens (Training, Results) don't show
            // the wrong task label.
            const derivedBindingType = isPPI ? "pp_binary" : (D.binding_type || "pl_simple");
            // POST the flow graph to /api/v2/pipeline/launch — the
            // backend compiles it to nn.Module via flow_compiler.
            const payload = {
              template_id: "flow",
              effective_config: {
                template_id: "flow",
                template_label: "Flow builder · user-built",
                flow: { nodes, edges },
                binding_type: derivedBindingType,
              },
              hparams: {
                benchmark: benchmark,
                split_policy: D.design_objective === "interpolation" ? "random" : "leakage-aware",
                epochs: 10,
                batch_size: 64,
                lr: 1e-3,
                seed: 4192,
                use_cuda: true,
              },
            };
            try {
              const r = await fetch("/api/v2/pipeline/launch", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
              });
              const j = await r.json();
              if (!r.ok) {
                // Stale-server detection: a 501 that calls out 'flow' as
                // unsupported almost always means Python loaded an old
                // copy of handlers.py into memory before the flow
                // exception was added. The on-disk source already accepts
                // flow; the running process just needs a restart. Tell
                // the user that directly rather than letting the raw 501
                // message imply the feature is missing entirely.
                if (r.status === 501) {
                  const supportsFlow = Array.isArray(j.supported_templates)
                    && j.supported_templates.includes("flow");
                  if (!supportsFlow) {
                    toast({
                      title: "Server restart needed",
                      body: ("This build supports the flow builder, but the running "
                          + "server process doesn't — Python doesn't hot-reload "
                          + "modules. Stop and restart the dev server "
                          + "(api/model_studio/server.py), then click Launch again."),
                      level: "warn", ttl_ms: 12000,
                    });
                    return;
                  }
                }
                toast({ title: `Launch failed (HTTP ${r.status})`,
                  body: j.message || j.error || "Unknown error",
                  level: "error", ttl_ms: 7000 });
                return;
              }
              // Persist the run id so the Training screen can stream it.
              if (!D.pipeline) D.pipeline = {};
              D.pipeline.current_run_id = j.run_id;
              toast({ title: `Run ${j.run_id} queued`,
                body: "Compiling the flow graph and starting the training loop.",
                level: "ok", ttl_ms: 3000 });
              setCurrent("training");
            } catch (err) {
              toast({ title: "Launch error",
                body: String(err.message || err),
                level: "error", ttl_ms: 6000 });
            }
          }}
          title={validation.state === "error" ? "Resolve the validation issues first" : "Launch a training run with this flow graph"}>
          <Ico name="play" size={12} /> Launch training
        </button>
      </div>

      <BindingBanner setCurrent={setCurrent} />

      {topologyWarning && (
        <div className="card" style={{ marginBottom: 14, borderLeft: "3px solid var(--warn)" }}>
          <div style={{ padding: "10px 14px", display: "flex", alignItems: "center", gap: 12 }}>
            <Ico name="warn" size={14} style={{ color: "var(--warn)" }} />
            <div style={{ flex: 1, fontSize: 12, lineHeight: 1.5 }}>
              <div style={{ color: "var(--text-strong)", marginBottom: 2 }}>
                Goal vs. flow mismatch — Goal screen says{" "}
                <span className="mono">{topologyWarning.goal_type}</span>{" "}
                but the flow you've built is a{" "}
                <strong>{topologyWarning.flow_topology}</strong> topology.
              </div>
              <div style={{ color: "var(--muted)" }}>
                Launch will auto-correct the benchmark to{" "}
                <span className="mono">{topologyWarning.suggested_benchmark}</span>
                {" "}— or update the Goal screen first to match.
              </div>
            </div>
            <button type="button" className="btn sm ghost"
              onClick={() => setCurrent("goal")}
              title="Jump back to the Goal screen to update the binding type">
              <Ico name="goal" size={11} /> Fix on Goal
            </button>
          </div>
        </div>
      )}

      <FlowHowItWorks />

      <div className={"flow-shell" + (showInspector ? "" : " no-inspector")}>
        <BlockPalette
          onPaletteDragStart={handlePaletteDragStart}
          search={search} setSearch={setSearch}
          draggingBlockId={paletteDrag?.blockId}
          featuresPicked={featuresPicked}
        />

        <div className="canvas-shell">
          <div className="canvas-toolbar">
            <Ico name="flow" size={14} style={{ color: "var(--primary)" }} />
            <span className="t">Compute graph</span>
            <span className="sub">{nodes.length} blocks · {edges.length} wires</span>
            <span style={{ flex: 1 }} />
            <button className="btn ghost" style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={() => { setNodes([]); setEdges([]); setSelectedId(null); }}>
              <Ico name="trash" size={11} /> Clear
            </button>
            <button className="btn ghost" style={{ padding: "4px 8px", fontSize: 11 }}
              onClick={() => setPresetOpen(true)}>
              <Ico name="archive" size={11} /> Preset
            </button>
          </div>
          <FlowCanvas
            nodes={nodes} edges={edges} selectedId={selectedId}
            onCanvasReady={(ref) => { canvasRef.current = ref.current; }}
            onMoveNode={moveNode}
            onSelect={setSelectedId}
            onAddEdge={addEdge}
            onDeleteEdge={deleteEdge}
            onDeleteNode={deleteNode}
            onContextMenu={(node, p) => setCtxMenu({ nodeId: node.id, x: p.x, y: p.y })}
          />
          <div className="validation-bar" data-state={validation.state}>
            <span className="dot" />
            <span style={{ fontWeight: 600 }}>
              {validation.state === "ok"    && "Pipeline valid"}
              {validation.state === "warn"  && `${validation.issues.length} issue${validation.issues.length === 1 ? "" : "s"}`}
              {validation.state === "error" && `${validation.issues.length} blocking issue${validation.issues.length === 1 ? "" : "s"}`}
            </span>
            <div className="issues">
              {validation.issues.slice(0, 3).map((i, k) => (
                <span key={k} style={{ opacity: 0.85 }}>· {i}</span>
              ))}
              {validation.issues.length > 3 && <span>+{validation.issues.length - 3} more</span>}
            </div>
          </div>
        </div>

        {showInspector && (
          <NodeInspector
            node={selectedNode} blockDef={selectedDef} implDef={selectedImpl}
            onClose={() => setSelectedId(null)}
            onImplChange={(implId) => updateImpl(selectedId, implId)}
            onParamChange={(k, v)  => updateParam(selectedId, k, v)}
            onDelete={() => deleteNode(selectedId)}
          />
        )}
      </div>

      {paletteDrag && <DragGhost blockId={paletteDrag.blockId} x={paletteDrag.x} y={paletteDrag.y} />}

      {ctxMenu && (
        <FlowContextMenu
          ctx={ctxMenu}
          onClose={() => setCtxMenu(null)}
          onDelete={() => deleteNode(ctxMenu.nodeId)}
          onDisconnect={() => disconnectNode(ctxMenu.nodeId)}
          onReplaceImpl={() => setSelectedId(ctxMenu.nodeId)}
        />
      )}

      <Modal open={presetOpen} onClose={() => setPresetOpen(false)}
        title="Load a preset"
        size="md">
        <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 14, lineHeight: 1.5 }}>
          Curated starting compositions. Each picks the right blocks and wires them up — mutate freely from there.
          Presets whose required inputs aren't in your <strong>Features</strong> selection appear grayed out;
          tick the missing features (or click to load anyway and the Features screen will surface the gap).
        </div>
        <div className="preset-modal-grid">
          {(window.PS_FLOW_PRESETS || []).map(p => {
            const gate = presetGate(p);
            const blocked = gate.missing.length > 0;
            const missingLabels = gate.missing.map(f => FEATURE_LABEL_INDEX[f] || f);
            const neededLabels  = gate.needed.map(f => FEATURE_LABEL_INDEX[f] || f);
            const tip = blocked
              ? `Missing features: ${missingLabels.join(", ")}.\nTick them on the Features tab to enable this preset (or load anyway — input blocks for missing features will dangle until you tick them).`
              : (gate.needed.length
                  ? `Uses: ${neededLabels.join(", ")}.`
                  : "No special feature requirements.");
            return (
              <div key={p.id}
                className={"preset-card" + (blocked ? " is-disabled" : "")}
                onClick={() => loadPreset(p)}
                title={tip}
                style={blocked ? {
                  opacity: 0.5, filter: "grayscale(0.6)",
                  borderLeft: "3px solid var(--warn)",
                } : null}>
                <div className="title" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span>{p.label}</span>
                  {blocked && (
                    <Chip tone="warn">missing {gate.missing.length}</Chip>
                  )}
                </div>
                <div className="blurb">{p.blurb}</div>
                {blocked && (
                  <div style={{ fontSize: 10, color: "var(--warn)", fontFamily: "var(--font-mono)",
                    marginTop: 4, lineHeight: 1.4 }}>
                    Needs: {missingLabels.join(" · ")}
                  </div>
                )}
                <div className="stats">
                  <span>{p.nodes} blocks</span>
                  <span>·</span>
                  <span>{p.paper}</span>
                  <span style={{ flex: 1 }} />
                  <Chip tone="dim">{p.binding}</Chip>
                </div>
              </div>
            );
          })}
        </div>
      </Modal>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// FlowHowItWorks — collapsible explainer banner that demystifies the
// flow editor. Covers: the five block categories, the port-colour
// contract, what's locked when you drop a node, and how the validator
// chooses error vs warn. Closed by default after first viewing.
// ────────────────────────────────────────────────────────────────────
function FlowHowItWorks() {
  const [open, setOpen] = React.useState(() => {
    try {
      return localStorage.getItem("ps_flow_howto_seen") !== "1";
    } catch { return true; }
  });
  const dismiss = () => {
    setOpen(false);
    try { localStorage.setItem("ps_flow_howto_seen", "1"); } catch {}
  };
  if (!open) {
    return (
      <div style={{ marginBottom: 12 }}>
        <button className="btn ghost" style={{ fontSize: 11, padding: "4px 10px" }}
          onClick={() => setOpen(true)}>
          <Ico name="info" size={11} /> How the flow editor works
        </button>
      </div>
    );
  }
  const cats = [
    { k: "input",      label: "Inputs",      desc: "What goes into the model — sequences, fingerprints, graphs, embeddings. Auto-populated from the Features screen." },
    { k: "encoder",    label: "Encoders",    desc: "Learn a representation from each input. Pick the role (e.g. ProteinSequenceEncoder); swap the implementation freely (CNN-1D / Transformer / ESM-2 / Identity)." },
    { k: "fusion",     label: "Fusion",      desc: "Combine the two representations into one. Cheap (concat) → mid (bilinear) → heavy (cross-attention, joint MP). Sometimes replaces the head (XGBoost-on-concat)." },
    { k: "head",       label: "Heads",       desc: "What the model is asked to output — pKi/pKd/pIC50/ΔG regression, binary classifier, ranking, or pose coordinates. Loss is part of the head spec." },
    { k: "diagnostic", label: "Diagnostics", desc: "Inserted inline. Pass-through; no effect on training. Logs histograms + small activation samples so you can see what's flowing through each wire." },
  ];
  const ports = window.PS_FLOW_PORT_TYPES || {};
  return (
    <div className="card" style={{ marginBottom: 14, borderLeft: "3px solid var(--primary)" }}>
      <div className="card-h">
        <Ico name="info" size={13} style={{ color: "var(--primary)" }} />
        <span className="t">How the flow editor works</span>
        <span className="sub">drag · drop · wire · swap</span>
        <span style={{ flex: 1 }} />
        <button className="btn ghost sm" onClick={dismiss}
          title="Hide. The button stays in the top toolbar so you can reopen anytime.">
          <Ico name="x" size={11} /> Got it
        </button>
      </div>
      <div style={{ padding: "10px 14px 14px", display: "grid",
        gridTemplateColumns: "1.2fr 1fr", gap: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10,
            color: "var(--dim)", letterSpacing: "0.06em",
            textTransform: "uppercase", marginBottom: 6 }}>
            Block categories
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {cats.map(c => (
              <div key={c.k} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                <span className="cat-badge" data-cat={c.k}
                  style={{ width: 14, height: 14, marginTop: 2, flexShrink: 0 }} />
                <div style={{ fontSize: 12, lineHeight: 1.5 }}>
                  <strong>{c.label}</strong> <span style={{ color: "var(--muted)" }}>— {c.desc}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10,
            color: "var(--dim)", letterSpacing: "0.06em",
            textTransform: "uppercase", marginBottom: 6 }}>
            Wire types · only matching colours can connect
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {Object.entries(ports).map(([k, p]) => (
              <div key={k} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                <span style={{ display: "inline-block", width: 24, height: 4,
                  background: p.color, borderRadius: 2 }} />
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10,
                  color: "var(--text-strong)", minWidth: 70 }}>{k}</span>
                <span style={{ color: "var(--muted)" }}>{p.desc}</span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 10, paddingTop: 10,
            borderTop: "1px dashed var(--border-soft)",
            fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
            <strong>Role is locked on drop.</strong> The block's <em>role</em> (e.g.
            ProteinGraphEncoder) is fixed once you drop it — that's the I/O
            contract the rest of your graph relies on. The <em>implementation</em>
            (GCN / GIN / GAT / Identity) is swappable in the inspector without
            re-wiring. The <strong>validation bar</strong> below the canvas turns
            red when the graph won't compile (cycle, missing input, wrong port
            type) and yellow when it's runnable but suspect (orphan output,
            unused diagnostic).
          </div>
        </div>
      </div>
    </div>
  );
}

window.FlowHowItWorks = FlowHowItWorks;
window.ScreenFlow = ScreenFlow;
