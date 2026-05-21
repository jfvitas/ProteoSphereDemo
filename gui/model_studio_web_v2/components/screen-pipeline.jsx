// ProteoSphere — Pipeline / Model designer
// Visual node-graph of featurizer → encoder → fusion → head, plus hyperparams + cost estimate.

function ScreenPipeline({ setCurrent, advanced, advancedDeltaCount, openAdvanced, coachAreas, coachOn, pushToast }) {
  const toast = pushToast || window.pushToast;
  const D = window.PS_DATA;
  const [mode, setMode]         = React.useState("standard"); // quick | standard | sweep

  // Template-driven DAG (Chunk 2 — read-only). The selected template id
  // persists to PS_DATA.pipeline so Train/Results can pick it up later.
  const TEMPLATES = window.PS_PIPELINE_TEMPLATES || [];
  // Partners come from the Dataset screen; default to ["pl"] if missing.
  const partners = React.useMemo(() => {
    const p = D.binding_partners;
    if (p && p.size) return Array.from(p);
    return ["pl"];
  }, [D.binding_partners]);
  const filteredTemplates = React.useMemo(
    () => TEMPLATES.filter(t => t.partners_tag.some(p => partners.includes(p))),
    [TEMPLATES, partners]
  );
  // Prefer the template the user previously picked. If nothing's persisted
  // and the partner filter has anything, use its first item; otherwise fall
  // back to the well-known deepdta default so we never auto-land on a
  // structure-heavy template like AlphaFold-Multimer.
  const initialTemplateId = (D.pipeline && D.pipeline.template_id)
    || (TEMPLATES.find(t => t.id === "deepdta") ? "deepdta" : null)
    || (filteredTemplates[0] && filteredTemplates[0].id)
    || (TEMPLATES[0] && TEMPLATES[0].id);
  const [templateId, setTemplateId] = React.useState(initialTemplateId);
  React.useEffect(() => {
    if (!D.pipeline) D.pipeline = {};
    D.pipeline.template_id = templateId;
  }, [templateId]);

  // Was: this useEffect auto-snapped the user's pick back to filteredTemplates[0]
  // whenever the chosen template was outside the partner filter. That
  // produced a "stuck on AlphaFold" trap — under partners=['pp'], the first
  // PP-compatible template is afm_tuned, so any other pick reverted instantly.
  //
  // New behaviour: surface a banner when there's a mismatch but DON'T
  // override the user's choice. They can fix it (change partners) or
  // proceed knowing the dataset filter isn't ideal for the architecture.
  const templateMismatch = React.useMemo(() => {
    if (!filteredTemplates.length) return null;
    const t = TEMPLATES.find(x => x.id === templateId);
    if (!t) return null;
    const compatible = t.partners_tag.some(p => partners.includes(p));
    if (compatible) return null;
    return {
      template_label: t.label,
      template_tags:  t.partners_tag,
      partners:       partners,
    };
  }, [filteredTemplates, templateId, TEMPLATES, partners]);

  const template = TEMPLATES.find(t => t.id === templateId) || TEMPLATES[0];

  // Per-slot overrides + params — cleared on every template change so the user
  // never carries an ESM-2 swap from MolTrans over to DeepDTA's CNN slot.
  // Keys are template-node-ids (e.g. "pe", "se"); values are the node-type
  // id swapped in (or absent, meaning "use the template's default").
  const [slotOverrides, setSlotOverrides] = React.useState({});
  const [slotParams,    setSlotParams]    = React.useState({});
  // Decision E (chunk 4) — user-added and user-removed edges on the DAG.
  // userEdges: [{ from: "nodeId:port", to: "nodeId:port" }]
  // removedEdges: ["from→to", ...]  (using → as separator, same as effectiveEdges)
  const [userEdges,    setUserEdges]    = React.useState([]);
  const [removedEdges, setRemovedEdges] = React.useState([]);
  React.useEffect(() => {
    setSlotOverrides({}); setSlotParams({});
    setUserEdges([]); setRemovedEdges([]);
  }, [templateId]);
  // Persist to PS_DATA so other screens (Training) can read what's loaded.
  React.useEffect(() => {
    if (!D.pipeline) D.pipeline = {};
    D.pipeline.overrides = slotOverrides;
    D.pipeline.params = slotParams;
    D.pipeline.userEdges = userEdges;
    D.pipeline.removedEdges = removedEdges;
  }, [slotOverrides, slotParams, userEdges, removedEdges]);
  // Edit-mode toggle — gates the drag-to-connect + edge-delete affordances.
  const [editMode, setEditMode] = React.useState(false);
  React.useEffect(() => { setEditMode(false); }, [templateId]);
  // Edge mutation handlers.
  const handleAddEdge = React.useCallback((from, to) => {
    setUserEdges(arr => {
      // Dedupe — never add the same edge twice.
      const key = `${from}→${to}`;
      if (arr.some(e => `${e.from}→${e.to}` === key)) return arr;
      return [...arr, { from, to }];
    });
    // If the user is re-adding an edge they previously deleted, drop it from removedEdges.
    setRemovedEdges(arr => arr.filter(k => k !== `${from}→${to}`));
  }, []);
  const handleRemoveEdge = React.useCallback((from, to) => {
    // If it's a user-added edge, just splice it out. Otherwise record in
    // removedEdges so the canvas keeps hiding it after re-render.
    const key = `${from}→${to}`;
    const wasUserAdded = (userEdges || []).some(e => `${e.from}→${e.to}` === key);
    const isTemplateEdge = !wasUserAdded && (template?.edges || []).some(e => `${e.from}→${e.to}` === key);
    if (wasUserAdded) {
      setUserEdges(arr => arr.filter(e => `${e.from}→${e.to}` !== key));
    } else if (isTemplateEdge) {
      setRemovedEdges(arr => arr.includes(key) ? arr : [...arr, key]);
    }
  }, [userEdges, template]);

  // Decision A — gallery modal launcher state.
  const [galleryOpen, setGalleryOpen] = React.useState(false);
  // Decision C — "show all 18" modal state.
  const [legendOpen,  setLegendOpen]  = React.useState(false);
  // Decision B — canvas density. Auto-pick by node count; user can override.
  const nodeCount = template?.nodes?.length || 0;
  const [canvasDensity, setCanvasDensity] = React.useState(nodeCount >= 8 ? "compact" : "comfortable");
  const [densityManual, setDensityManual] = React.useState(false);
  React.useEffect(() => {
    if (!densityManual) setCanvasDensity(nodeCount >= 8 ? "compact" : "comfortable");
  }, [nodeCount, densityManual]);
  // Decision D — selected node opens the inspector popover.
  const [selectedNodeId, setSelectedNodeId] = React.useState(null);
  React.useEffect(() => { setSelectedNodeId(null); }, [templateId]);
  // Param-change handler shared by inspector + stage stack (single source of truth).
  const handleParamChange = React.useCallback((slotId, key, value) => {
    setSlotParams(p => ({ ...p, [slotId]: { ...(p[slotId] || {}), [key]: value } }));
  }, []);

  // ── Backend launch controls ───────────────────────────────────────
  // Benchmark + split policy + featurizer multi-select. These get
  // packaged into the /api/v2/pipeline/launch hparams payload alongside
  // the template_id. Persisted on PS_DATA.pipeline so re-entries from
  // other screens preserve the selection.
  const [benchmark, setBenchmark] = React.useState(
    (D.pipeline && D.pipeline.benchmark) || "kiba"
  );
  const [splitPolicy, setSplitPolicy] = React.useState(
    (D.pipeline && D.pipeline.split_policy) || "cold-target"
  );
  const [pickedFeaturizers, setPickedFeaturizers] = React.useState(
    () => new Set((D.pipeline && D.pipeline.featurizers) || [])
  );
  React.useEffect(() => {
    if (!D.pipeline) D.pipeline = {};
    D.pipeline.benchmark    = benchmark;
    D.pipeline.split_policy = splitPolicy;
    D.pipeline.featurizers  = Array.from(pickedFeaturizers);
  }, [benchmark, splitPolicy, pickedFeaturizers]);

  // Live featurizer catalog — cached on window so re-mounts are instant.
  const [featurizerCatalog, setFeaturizerCatalog] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_FEATURIZERS) || null
  );
  React.useEffect(() => {
    if (featurizerCatalog) return;
    fetch("/api/v2/featurizers")
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(j => { window.PS_LIVE_FEATURIZERS = j; setFeaturizerCatalog(j); })
      .catch(() => {});
  }, [featurizerCatalog]);

  const toggleFeaturizer = (fid) => setPickedFeaturizers(prev => {
    const next = new Set(prev);
    next.has(fid) ? next.delete(fid) : next.add(fid);
    return next;
  });

  // ── HParams form state ────────────────────────────────────────────
  // Previously the HParam inputs were decorative (defaultValue, no
  // onChange) and the launch payload used hardcoded constants
  // (epochs:25, batch_size:256, ...). Users couldn't tune training time.
  // Now they're proper controlled inputs that flow into the launch body.
  const [hpEpochs,       setHpEpochs]       = React.useState(String((D.pipeline && D.pipeline.epochs) || "25"));
  const [hpBatchSize,    setHpBatchSize]    = React.useState(String((D.pipeline && D.pipeline.batch_size) || "256"));
  const [hpLearningRate, setHpLearningRate] = React.useState(String((D.pipeline && D.pipeline.lr) || "3e-4"));
  const [hpWeightDecay,  setHpWeightDecay]  = React.useState(String((D.pipeline && D.pipeline.weight_decay) || "0.0"));
  const [hpSeed,         setHpSeed]         = React.useState(String((D.pipeline && D.pipeline.seed) || "4192"));
  React.useEffect(() => {
    if (!D.pipeline) D.pipeline = {};
    D.pipeline.epochs       = hpEpochs;
    D.pipeline.batch_size   = hpBatchSize;
    D.pipeline.lr           = hpLearningRate;
    D.pipeline.weight_decay = hpWeightDecay;
    D.pipeline.seed         = hpSeed;
  }, [hpEpochs, hpBatchSize, hpLearningRate, hpWeightDecay, hpSeed]);

  return (
    <div className="screen" data-screen-label="04 Pipeline">
      <StepRail active="pipeline" onClick={setCurrent} />
      <PipelineModeToggle current="pipeline" setCurrent={setCurrent} />
      <LaneBar lane="release" />

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 04 · PIPELINE · PREFAB TEMPLATES</div>
          <h2>Choose what the model sees, and how it learns</h2>
          <p className="lead" style={{ marginTop: 4 }}>
            Featurizers turn proteins and ligands into numbers a network can read. The architecture combines them.
            The diagram below is the actual compute graph ProteoSphere will run — embeddings are cached against the <Term word="warehouse">warehouse</Term>, so re-runs are fast.
            Need full control? Switch to the <strong>Flow builder</strong> at the top of the page.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn ghost"
          onClick={() => setGalleryOpen(true)}
          title="Browse the full prefab template gallery.">
          <Ico name="archive" size={12} /> Browse templates
        </button>
        <button className="btn primary" onClick={async () => {
          // Build the effective config + leak-test it before hitting the backend.
          const effective = buildEffectiveConfig(template, slotOverrides, slotParams, userEdges, removedEdges);
          const leak = leakTestConfig(effective);
          if (!leak.ok) {
            toast({
              title: "Launch blocked — leak test failed",
              body: leak.errors.slice(0, 2).join(" · ") + (leak.errors.length > 2 ? ` (+${leak.errors.length - 2} more)` : ""),
              level: "error",
              ttl_ms: 5000,
            });
            return;
          }
          D.pipeline.effective_config = effective;
          // POST to the real v2 backend. Hparams pulled from the form state
          // (BUG-004 fix). Numeric strings parsed with Number(...) and
          // sanitised so non-positive epochs / NaN don't crash the trainer.
          const parsedEpochs    = Math.max(1, parseInt(hpEpochs, 10) || 25);
          const parsedBatchSize = Math.max(1, parseInt(hpBatchSize, 10) || 256);
          const parsedLR        = Math.max(1e-9, parseFloat(hpLearningRate) || 3e-4);
          const parsedWD        = Math.max(0, parseFloat(hpWeightDecay) || 0);
          const parsedSeed      = parseInt(hpSeed, 10) || 4192;
          const hparams = {
            epochs: parsedEpochs,
            batch_size: parsedBatchSize,
            lr: parsedLR,
            weight_decay: parsedWD,
            seed: parsedSeed,
            use_cuda: true,
            amp: true,
            benchmark:    benchmark,
            split_policy: splitPolicy,
          };
          if (pickedFeaturizers.size > 0) {
            hparams.featurizers = Array.from(pickedFeaturizers);
          }
          try {
            const resp = await fetch("/api/v2/pipeline/launch", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ effective_config: effective, hparams }),
            });
            const j = await resp.json();
            if (resp.status === 501) {
              // Template not implemented yet (only deepdta wired in v0).
              toast({
                title: "Template not yet in the v2 backend",
                body: j.message || `Only ${(j.supported_templates || ["deepdta"]).join(", ")} is wired right now. Switch to DeepDTA on the Architecture template card to launch a real run.`,
                level: "warn",
                ttl_ms: 8000,
              });
              return;
            }
            if (!resp.ok) {
              toast({
                title: "Launch failed",
                body: j.error || `HTTP ${resp.status}`,
                level: "error",
                ttl_ms: 5000,
              });
              return;
            }
            // Stash the run id so the Training screen can subscribe.
            D.pipeline.current_run_id = j.run_id;
            D.pipeline.stream_url = j.stream_url;
            window.dispatchEvent(new CustomEvent("pipeline-launched", { detail: { run_id: j.run_id, effective } }));
            toast({
              title: mode === "sweep" ? "Sweep launched" : "Training launched",
              body: `${leak.summary} · ${j.run_id}`,
              level: "ok",
              ttl_ms: 3500,
            });
            setCurrent("training");
          } catch (err) {
            toast({
              title: "Could not reach the training backend",
              body: String(err),
              level: "error",
              ttl_ms: 5000,
            });
          }
        }}>
          {mode === "sweep" ? "Launch sweep" : "Launch training"} <Ico name="play" />
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Banner: chosen template doesn't match the Dataset's partner filter.
              Previously this triggered a silent auto-snap-back; now we just
              warn + give the user one click to reset to a compatible default. */}
          {templateMismatch && (
            <div className="card" style={{ borderLeft: "3px solid var(--warn)" }}>
              <div style={{ padding: "10px 14px", display: "flex", alignItems: "center", gap: 12 }}>
                <Ico name="warn" size={14} />
                <div style={{ flex: 1, fontSize: 12, lineHeight: 1.4 }}>
                  <div style={{ color: "var(--text-strong)", marginBottom: 2 }}>
                    Template <span className="mono">{templateMismatch.template_label}</span> targets {templateMismatch.template_tags.join(" / ")}, but your dataset is scoped to {templateMismatch.partners.join(" / ")}.
                  </div>
                  <div style={{ color: "var(--muted)" }}>
                    Either switch templates (gallery →) or change the binding-partner filter on the Dataset step.
                  </div>
                </div>
                {filteredTemplates[0] && (
                  <button type="button" className="btn sm primary"
                    onClick={() => setTemplateId(filteredTemplates[0].id)}>
                    Reset to {filteredTemplates[0].label}
                  </button>
                )}
                <button type="button" className="btn sm ghost"
                  onClick={() => setGalleryOpen(true)}>Browse templates</button>
              </div>
            </div>
          )}

          {/* Decision A — collapsed template header (gallery in modal). */}
          <TemplateHeader
            template={template}
            objective={D.design_objective}
            partnerLabel={partners.length === 1
              ? (partners[0] === "pl" ? "P–L" : partners[0] === "pp" ? "P–P" : "P–NA")
              : null}
            onChangeClick={() => setGalleryOpen(true)}
          />

          {/* Backend launch controls — benchmark, split policy, featurizer mix.
              These map directly to the /api/v2/pipeline/launch hparams payload. */}
          <LaunchControls
            benchmark={benchmark}             setBenchmark={setBenchmark}
            splitPolicy={splitPolicy}         setSplitPolicy={setSplitPolicy}
            pickedFeaturizers={pickedFeaturizers}
            toggleFeaturizer={toggleFeaturizer}
            featurizerCatalog={featurizerCatalog}
            template={template}
          />

          {/* Pipeline canvas — typed DAG read from PS_PIPELINE_TEMPLATES.
              Decisions B (density modes), C (legend strip), D (inspector), F (category badges). */}
          <div className="card">
            <div className="card-h">
              <span className="t">Compute graph</span>
              <span className="sub">{template ? template.label : ""}{Object.keys(slotOverrides).length ? " · swapped" : " · template default"}</span>
              <div style={{ flex: 1 }} />
              {/* Density toggle — manual override of the auto-picked mode. */}
              <div className="toggle sm" title="Canvas density">
                <button aria-pressed={canvasDensity === "comfortable"}
                  onClick={() => { setCanvasDensity("comfortable"); setDensityManual(true); }}>
                  Comfortable
                </button>
                <button aria-pressed={canvasDensity === "compact"}
                  onClick={() => { setCanvasDensity("compact"); setDensityManual(true); }}>
                  Compact
                </button>
              </div>
              <button type="button"
                className={"btn sm " + (editMode ? "primary" : "ghost")}
                aria-pressed={editMode}
                onClick={() => setEditMode(m => !m)}
                title={editMode
                  ? "Editing on — drag an output port onto an input port to add an edge; click an edge to remove it."
                  : "Click to enable edge editing (drag-to-connect + click-edge-to-delete)."}>
                <Ico name="edit" size={12} /> {editMode ? "Editing" : "Edit"}
                {(userEdges.length + removedEdges.length) > 0 && (
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, marginLeft: 4, padding: "1px 5px", borderRadius: 3, background: editMode ? "rgba(0,0,0,0.2)" : "var(--surface-3)" }}>
                    +{userEdges.length}/-{removedEdges.length}
                  </span>
                )}
              </button>
            </div>
            <PipelineCanvas
              template={template}
              slotOverrides={slotOverrides}
              density={canvasDensity}
              selectedNodeId={selectedNodeId}
              onSelectNode={(id) => setSelectedNodeId(prev => prev === id ? null : id)}
              onCloseInspector={() => setSelectedNodeId(null)}
              slotParams={slotParams}
              onParamChange={handleParamChange}
              userEdges={userEdges}
              removedEdges={removedEdges}
              editMode={editMode}
              onAddEdge={handleAddEdge}
              onRemoveEdge={handleRemoveEdge}
              pushToast={toast}
            />
            <LegendStrip
              template={template}
              slotOverrides={slotOverrides}
              onShowAll={() => setLegendOpen(true)}
            />
          </div>

          {/* Stage panels — per-role pickers + params, driven by the loaded template. */}
          <StageStack
            template={template}
            slotOverrides={slotOverrides}
            slotParams={slotParams}
            onPickSlot={(slotId, nodeTypeId) => setSlotOverrides(o => ({ ...o, [slotId]: nodeTypeId }))}
            onParamChange={(slotId, key, value) => setSlotParams(p => ({ ...p, [slotId]: { ...(p[slotId] || {}), [key]: value } }))}
          />
          {/* Structure preparation — anchor for validator → field jump from Results.
              Shows a 4-card summary of the current values; opens the full
              Advanced — Structure preparation modal for the deep options. */}
          <div className="card" data-field="pipeline.preprocessing">
            <div className="card-h">
              <span className="t">Structure preparation</span>
              <span className="sub">how raw sequences, structures and ligands are prepared before featurisation</span>
              <div style={{ flex: 1 }} />
              {openAdvanced && (
                <AdvancedButton
                  panelKey="structure_preparation"
                  openAdvanced={openAdvanced}
                  deltaCount={advancedDeltaCount?.structure_preparation}>
                  Structure prep advanced
                </AdvancedButton>
              )}
            </div>
            <div style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12 }}>
              <PrepSummary
                label="Engine"
                value={(advanced?.structure_preparation?.prep_engine || "pyrosetta").replace(/_/g, " ")}
                hint={advanced?.structure_preparation?.prep_engine === "pyrosetta" ? "Rosetta FastRelax + ref2015" : "OpenMM / RDKit / OpenBabel path"}
              />
              <PrepSummary
                label="Hydrogens"
                value={advanced?.structure_preparation?.hydrogen_engine || "reduce"}
                hint="protein H placement"
              />
              <PrepSummary
                label="Ligand 3D"
                value={`${advanced?.structure_preparation?.conformer_generator || "rdkit_etkdgv3"} · ${advanced?.structure_preparation?.n_conformers ?? 20} conf`}
                hint={`${advanced?.structure_preparation?.partial_charges || "am1_bcc"} charges · pH ${(advanced?.structure_preparation?.ligand_ph ?? 7.4).toFixed(1)}`}
              />
              <PrepSummary
                label="Docking pre-pose"
                value={advanced?.structure_preparation?.docking_pre_pose || "none"}
                hint={advanced?.structure_preparation?.docking_pre_pose === "none" ? "start from input pose" : "starting pose docked"}
              />
            </div>
            {advanced?.structure_preparation?.prep_engine === "pyrosetta" && (
              <div style={{ padding: "0 12px 12px", fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
                Rosetta: <span className="mono" style={{ color: "var(--text-strong)" }}>{advanced.structure_preparation.scorefxn}</span> · {advanced.structure_preparation.relax_protocol} × {advanced.structure_preparation.relax_cycles} cycles · {advanced.structure_preparation.cartesian_relax ? "cartesian" : "torsion"} space · {advanced.structure_preparation.constrain_to_start_coords ? `coord-constrained σ=${advanced.structure_preparation.coord_constraint_stdev}Å` : "unconstrained"}
              </div>
            )}
            {coachOn && coachAreas?.pipeline && (
              <div className="coach-inline">
                <Ico name="sparkle" size={12} />
                <span>Bench biologist tip: keep the engine at <strong>PyRosetta</strong> unless your target is membrane-embedded (try OpenMM + Amber14) or you have a Schrödinger license. Open advanced if you need ligand protonation at a non-physiological pH.</span>
              </div>
            )}
          </div>

          {/* Hyperparams + Sweep — three modes */}
          <div className="card">
            <div className="card-h">
              <span className="t">{mode === "sweep" ? "Hyperparameter sweep" : "Training settings"}</span>
              <div style={{ flex: 1 }} />
              <div className="toggle">
                <button aria-pressed={mode === "quick"}    onClick={() => setMode("quick")}>Quick start</button>
                <button aria-pressed={mode === "standard"} onClick={() => setMode("standard")}>Standard</button>
                <button aria-pressed={mode === "sweep"}    onClick={() => setMode("sweep")}>Sweep</button>
              </div>
            </div>

            {mode === "quick" && (
              <div style={{ padding: 16 }}>
                {/* Seed banner — pulled out of the grid so it cannot be
                    set by accident. When still at the default 4192 it
                    renders with a warning border + chip; once changed
                    the warning clears. This is the single hyperparam
                    most likely to be silently identical across runs,
                    so we surface it deliberately. */}
                <SeedBanner value={hpSeed} onChange={setHpSeed} />
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginTop: 16 }}>
                  <HParam label="Optimizer" value="AdamW" type="select" opts={["AdamW","Adam","Lion","SGD"]} />
                  <HParam label="Learning rate" value={hpLearningRate} onChange={setHpLearningRate} type="input" mono />
                  <HParam label="Batch size"   value={hpBatchSize}    onChange={setHpBatchSize}    type="input" mono />
                  <HParam label="Epochs"       value={hpEpochs}       onChange={setHpEpochs}       type="input" mono />
                  <HParam label="Loss" value="Huber" type="select" opts={["MSE","Huber","Smooth-L1"]} />
                  <div style={{ gridColumn: "1 / -1", fontSize: 12, color: "var(--muted)", borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                    5 fields + seed · opinionated defaults for the rest. Switch to Standard or Sweep to expose more.
                  </div>
                </div>
              </div>
            )}

            {mode === "standard" && (
              <div style={{ padding: 16 }}>
                <SeedBanner value={hpSeed} onChange={setHpSeed} />
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginTop: 16 }}>
                <HParam label="Optimizer" value="AdamW" type="select" opts={["AdamW","Adam","Lion","SGD"]} />
                <HParam label="Learning rate" value={hpLearningRate} onChange={setHpLearningRate} type="input" mono help="Cosine decay, 1000 warm-up steps" />
                <HParam label="Batch size"   value={hpBatchSize}    onChange={setHpBatchSize}    type="input" mono />
                <HParam label="Epochs"       value={hpEpochs}       onChange={setHpEpochs}       type="input" mono />
                <HParam label="Weight decay" value={hpWeightDecay}  onChange={setHpWeightDecay}  type="input" mono />
                <HParam label="Loss" value="Huber (δ=0.3)" type="select" opts={["MSE","Huber (δ=0.3)","Smooth-L1"]} />
                <HParam label="Augmentations" value="reverse-pairs · seq-crop" type="input" />
                <HParam label="Early stop" value="val Pearson · patience 6" type="input" />
                <HParam label="Mixed precision" value="bf16" type="select" opts={["fp32","bf16","fp16 + master"]} />
                <HParam label="Multi-GPU strategy" value="DDP" type="select" opts={["single","DDP","FSDP","ZeRO-3"]} />
                <HParam label="Checkpoint cadence" value="every epoch" type="select" opts={["every epoch","every 5 epochs","best only"]} />
                </div>
              </div>
            )}

            {mode === "sweep" && <SweepPanel D={D} />}
          </div>
        </div>

        {/* Right column — cost estimate */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 16, alignSelf: "flex-start" }}>
          <div className="card elevated">
            <div className="card-h">
              <span className="t">Cost &amp; compute estimate</span>
              <Chip tone="primary">baseline</Chip>
              <span className="sub" style={{ marginLeft: 6 }} title="Reflects the picked featurizers + architecture. HParam edits (LR/batch/epochs) reprice at launch.">at launch</span>
            </div>
            <div className="card-b">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <Stat k="Total time"     v="2h 14m" mono delta="2 × A100" />
                <Stat k="Estimated cost" v="$9.82"  mono delta="↓ 41% with embedding cache" />
                <Stat k="Peak memory"    v="63 GB"  mono />
                <Stat k="Carbon"         v="0.84 kg" mono />
              </div>
              <hr className="hr" />
              <div className="label">Breakdown</div>
              <CostBar />
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 8, lineHeight: 1.7 }}>
                <div><span style={{ display: "inline-block", width: 8, height: 8, background: "var(--molecular)", borderRadius: 2, marginRight: 5 }} />Embedding compute · 38m · $2.91</div>
                <div><span style={{ display: "inline-block", width: 8, height: 8, background: "var(--primary)", borderRadius: 2, marginRight: 5 }} />Training loop · 1h 24m · $5.42</div>
                <div><span style={{ display: "inline-block", width: 8, height: 8, background: "var(--signal)", borderRadius: 2, marginRight: 5 }} />Eval &amp; checkpoints · 12m · $1.49</div>
              </div>
              <hr className="hr" />
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "var(--muted)" }}>
                <Ico name="sparkle" style={{ color: "var(--signal)" }} />
                <span>Switching to ESM-2 35M and ECFP would save ~$7.10 but lose ~2.4 pts Pearson.</span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-h"><span className="t">Pre-flight checks</span></div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 12 }}>
              <PreCheck label="Splits frozen" state="ok" detail="seed 4192 · sha 9f3a…" />
              <PreCheck label="Dataset cached" state="ok" detail="1.84M rows on /mnt/scratch" />
              <PreCheck label="ESM-2 weights resolved" state="ok" detail="from /mnt/models" />
              <PreCheck label="GPU availability" state="warn" detail="2 of 2 A100s — slot for 14:00" />
              <PreCheck label="Budget remaining" state="ok" detail="$285.70 this month" />
              <PreCheck label="Reproducibility" state="ok" detail="lockfile, env hash recorded" />
            </div>
          </div>
        </div>
      </div>

      {/* Decision A — gallery launcher modal. */}
      <GalleryModal
        open={galleryOpen}
        onClose={() => setGalleryOpen(false)}
        templateId={templateId}
        onPick={setTemplateId}
      />
      {/* Decision C — all 18 port types modal. */}
      <LegendAllModal open={legendOpen} onClose={() => setLegendOpen(false)} />
    </div>
  );
}

// Prominent seed input. We keep the seed OUT of the regular HParam grid
// because PyTorch's determinism contract means two runs with the same
// seed + template + data produce nearly identical metrics — the most
// common source of "why are my results identical?" confusion. The
// banner makes the seed value impossible to miss and surfaces a
// warning chip when it's still the default 4192. A Randomize button
// generates a fresh 9-digit seed on demand.
function SeedBanner({ value, onChange }) {
  const isDefault = String(value).trim() === "4192";
  const handleRandomize = () => {
    // 9-digit seed so the value is recognisably non-default at a glance.
    const next = Math.floor(100_000_000 + Math.random() * 900_000_000);
    onChange(String(next));
  };
  return (
    <div
      style={{
        display: "flex", alignItems: "center", gap: 14,
        padding: "14px 16px", borderRadius: "var(--r)",
        border: `2px solid ${isDefault ? "var(--warn)" : "var(--signal)"}`,
        background: isDefault ? "var(--warn-soft)" : "var(--signal-soft)",
      }}
    >
      <div style={{
        width: 36, height: 36, borderRadius: 8, flexShrink: 0,
        display: "grid", placeItems: "center",
        background: isDefault ? "var(--warn)" : "var(--signal)",
        color: "var(--bg)",
        fontFamily: "var(--font-mono)", fontWeight: 700,
      }}>#</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: "0 0 auto" }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11,
                       letterSpacing: "0.08em", color: "var(--text-strong)" }}>
          RNG SEED
        </span>
        <span style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.4 }}>
          Same seed + same setup → same numbers. Change before relaunching.
        </span>
      </div>
      <input
        type="text"
        className="input mono"
        value={value}
        onChange={e => onChange(e.target.value)}
        aria-label="Training random seed"
        style={{
          fontSize: 18, fontWeight: 600,
          padding: "6px 12px", letterSpacing: "0.04em",
          minWidth: 180, marginLeft: 6,
        }}
      />
      <button
        type="button"
        className="btn sm"
        title="Generate a fresh 9-digit seed (so the next run isn't byte-identical to the last one)."
        onClick={handleRandomize}
      >
        Randomize
      </button>
      <div style={{ flex: 1 }} />
      {isDefault ? (
        <span className="chip warn" style={{ fontWeight: 600 }}>
          ⚠ DEFAULT SEED (4192) — every run will produce identical results
        </span>
      ) : (
        <span className="chip ok">Custom seed — runs will vary</span>
      )}
    </div>
  );
}

function SweepPanel({ D }) {
  const toast = window.pushToast || (() => {});
  const sw = D.sweep;
  // Controlled state for every Sweep input — the cost banner below
  // re-computes whenever any of these changes.
  const [sampler, setSampler] = React.useState(sw.sampler);
  const [pruner, setPruner] = React.useState(sw.pruner);
  const [nTrials, setNTrials] = React.useState(sw.n_trials);
  const [nSeeds, setNSeeds] = React.useState(sw.n_seeds);
  const total_cost = nTrials * sw.per_trial_cost_usd * Math.max(1, nSeeds);
  const cap = 100;
  const breachesCap = total_cost > cap;
  return (
    <div style={{ padding: 16 }}>
      {/* Top strip — sampler + counts + cost */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 12, marginBottom: 14 }}>
        <div>
          <label htmlFor="sweep-sampler" className="label">Sampler</label>
          <select id="sweep-sampler" className="select" value={sampler} onChange={e => setSampler(e.target.value)}>
            <option>Bayesian (TPE)</option><option>Random</option><option>Grid</option><option>CMA-ES</option>
          </select>
          <div className="help">how the search picks the next trial</div>
        </div>
        <div>
          <label htmlFor="sweep-pruner" className="label">Pruner</label>
          <select id="sweep-pruner" className="select" value={pruner} onChange={e => setPruner(e.target.value)}>
            <option>Median (warmup 5 epochs)</option><option>Hyperband</option><option>None</option>
          </select>
          <div className="help">kill bad trials early</div>
        </div>
        <div>
          <label htmlFor="sweep-trials" className="label">Trials</label>
          <input id="sweep-trials" type="number" min={1} max={1000} className="input" value={nTrials}
            onChange={e => { const n = parseInt(e.target.value, 10); if (Number.isFinite(n)) setNTrials(n); }}
            style={{ fontFamily: "var(--font-mono)" }} />
          <div className="help">how many runs to launch</div>
        </div>
        <div>
          <label htmlFor="sweep-seeds" className="label">Seeds per trial</label>
          <input id="sweep-seeds" type="number" min={1} max={10} className="input" value={nSeeds}
            onChange={e => { const n = parseInt(e.target.value, 10); if (Number.isFinite(n)) setNSeeds(n); }}
            style={{ fontFamily: "var(--font-mono)" }} />
          <div className="help">replication for stability</div>
        </div>
      </div>

      {/* Search space table */}
      <div className="label">Search space</div>
      <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", overflow: "hidden" }}>
        <table className="tbl" style={{ margin: 0 }}>
          <thead><tr><th>Parameter</th><th>Distribution</th><th>Range / values</th><th>Current value</th><th></th></tr></thead>
          <tbody>
            {sw.space.map(s => (
              <tr key={s.param}>
                <td className="mono">{s.param}</td>
                <td><Chip>{s.kind}</Chip></td>
                <td className="mono" style={{ color: "var(--muted)" }}>
                  {s.values ? s.values.join(" · ") : `[${s.lo}, ${s.hi}]`}
                </td>
                <td className="mono">{s.current || "—"}</td>
                <td>
                  <button type="button" className="btn sm ghost"
                    aria-label={`Actions for ${s.param}`}
                    onClick={() => toast({
                      title: `Actions for ${s.param}`,
                      body: "Would offer: Edit range · Pin to current value · Remove from sweep.",
                      level: "info",
                    })}>
                    <Ico name="more" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 8 }}>
        <button type="button" className="btn sm ghost"
          onClick={() => toast({
            title: "Add search-space parameter",
            body: "Would open a picker showing every hyperparameter in PS_DEEP_SETTINGS plus the distribution choice (loguniform / int / categorical / discrete).",
            level: "info",
          })}>
          <Ico name="plus" size={12} /> Add parameter
        </button>
      </div>

      {/* Cost guardrail */}
      <hr className="hr" />
      <div className={"banner " + (breachesCap ? "error" : "warn")}>
        <div className="ico-wrap"><Ico name={breachesCap ? "warn" : "info"} /></div>
        <div className="banner-body">
          <div className="t">
            Will train <span className="mono" style={{ color: "var(--text-strong)" }}>{nTrials} × {nSeeds} = {nTrials * nSeeds} runs</span>
            {" "}for an estimated <span className="mono" style={{ color: "var(--text-strong)" }}>{fmt.money(total_cost)}</span>.
          </div>
          <div className="d">
            Per-sweep cap is {fmt.money(cap)}.
            {breachesCap
              ? ` Exceeds the cap by ${fmt.money(total_cost - cap)} — override requires a typed reason + reviewer.`
              : " Within budget; safe to launch."}
          </div>
        </div>
        <div className="banner-actions">
          <button type="button" className="btn sm"
            onClick={() => {
              // Halve trial count (and never go below 1). This is the cheapest
              // way to get under the cap without dropping seeds.
              const next = Math.max(1, Math.floor(nTrials / 2));
              setNTrials(next);
              toast({
                title: `Trials lowered: ${nTrials} → ${next}`,
                body: `New estimated cost ${fmt.money(next * sw.per_trial_cost_usd * Math.max(1, nSeeds))} (cap ${fmt.money(cap)}).`,
                level: "info",
                ttl_ms: 2400,
              });
            }}>Lower trials</button>
          <button type="button" className={"btn sm " + (breachesCap ? "" : "primary")}
            onClick={() => toast({
              title: breachesCap ? "Override + launch sweep" : "Sweep launched",
              body: breachesCap
                ? `Would open the Cost guardrail modal (typed reason + reviewer) before submitting ${nTrials} × ${nSeeds} trials at ${fmt.money(total_cost)}.`
                : `Would enqueue ${nTrials} × ${nSeeds} = ${nTrials * nSeeds} runs at ${fmt.money(total_cost)}.`,
              level: breachesCap ? "warn" : "ok",
            })}>
            {breachesCap ? "Override and launch" : "Launch sweep"}
          </button>
        </div>
      </div>
    </div>
  );
}

// Small read-only summary card used by the Structure-preparation panel.
function PrepSummary({ label, value, hint }) {
  return (
    <div style={{ padding: 10, borderRadius: "var(--r)", background: "var(--surface-2)", border: "1px solid var(--border)" }}>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: 13, color: "var(--text-strong)", fontWeight: 500, marginTop: 4, wordBreak: "break-word" }}>{value}</div>
      {hint && <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{hint}</div>}
    </div>
  );
}

// Controlled HParam component.
// When `onChange` is provided, the input is fully controlled (value + onChange)
// and the user's edit propagates to the parent's state. Older callsites that
// only pass `value` keep the old uncontrolled (defaultValue) behaviour so
// decorative chips don't break.
function HParam({ label, value, type, opts, mono, help, onChange }) {
  const controlled = typeof onChange === "function";
  return (
    <div>
      <div className="label">{label}</div>
      {type === "select" ? (
        controlled ? (
          <select className="select" value={value}
            onChange={e => onChange(e.target.value)}
            style={mono ? { fontFamily: "var(--font-mono)" } : {}}>
            {(opts || []).map(o => <option key={o}>{o}</option>)}
          </select>
        ) : (
          <select className="select" defaultValue={value} style={mono ? { fontFamily: "var(--font-mono)" } : {}}>
            {(opts || []).map(o => <option key={o}>{o}</option>)}
          </select>
        )
      ) : (
        controlled ? (
          <input className="input" value={value}
            onChange={e => onChange(e.target.value)}
            style={mono ? { fontFamily: "var(--font-mono)" } : {}} />
        ) : (
          <input className="input" defaultValue={value} style={mono ? { fontFamily: "var(--font-mono)" } : {}} />
        )
      )}
      {help && <div className="help">{help}</div>}
    </div>
  );
}

// =============================================================================
// CHUNK 2 — template picker + read-only DAG canvas
// =============================================================================

// Horizontal grid of template cards. Each card is a clickable mini-summary.
// Decision A — collapsed template header + gallery modal.
// The header surfaces the loaded template's identity (name, blurb, cost, objective,
// partners, recommended ★) and a "Change template ↓" button that opens the gallery.
// Total vertical footprint ≈ 80 px vs ~200 px for the old inline 3-column grid.
// LaunchControls — picks the benchmark + split policy + featurizer
// combination that go into the /api/v2/pipeline/launch payload. Lets
// the user reproduce in the GUI what previously required curl.
function LaunchControls({ benchmark, setBenchmark, splitPolicy, setSplitPolicy,
                          pickedFeaturizers, toggleFeaturizer,
                          featurizerCatalog, template }) {
  const [openAxis, setOpenAxis] = React.useState(null);
  // Templates that expect featurizers (vs token / graph templates).
  const wantsFeaturizers = template && ["tabular_mlp", "thermo_mlp", "conplex"].includes(template.id);
  const benchmarks = [
    { id: "kiba",   label: "KIBA",   sub: "118 K assays · 229 prot × 2.1 K lig" },
    { id: "davis",  label: "Davis",  sub: "30 K assays · 442 kinases × 68 lig" },
    { id: "gtopdb", label: "GtoPdb", sub: "13.8 K curated · sequence-resolved" },
  ];
  const splits = [
    { id: "random",       label: "Random",       hint: "Warm split — easiest" },
    { id: "cold-target",  label: "Cold target",  hint: "Held-out proteins" },
    { id: "cold-drug",    label: "Cold drug",    hint: "Held-out ligands" },
    { id: "cold-pair",    label: "Cold pair",    hint: "Both axes held out" },
    { id: "scaffold",     label: "Scaffold",     hint: "Bemis-Murcko" },
    { id: "cluster",      label: "Cluster",      hint: "Leakage-aware" },
    { id: "stratified",   label: "Stratified",   hint: "Per-protein random" },
  ];

  const costTone = (c) => c === "trivial" ? "ok" : c === "fast" ? "signal" : c === "moderate" ? "info" : "warn";
  const groupedFeats = React.useMemo(() => {
    if (!featurizerCatalog) return null;
    return featurizerCatalog.by_axis || {};
  }, [featurizerCatalog]);

  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Backend launch</span>
        <span className="sub">benchmark · split · featurizer mix</span>
        <div style={{ flex: 1 }} />
        {pickedFeaturizers.size > 0 && (
          <Chip tone="signal">{pickedFeaturizers.size} featurizer{pickedFeaturizers.size === 1 ? "" : "s"}</Chip>
        )}
        {wantsFeaturizers && pickedFeaturizers.size === 0 && (
          <Chip tone="warn">pick at least 1 featurizer</Chip>
        )}
      </div>

      {/* Benchmark + split rows */}
      <div style={{ padding: "12px 14px", display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, borderBottom: "1px solid var(--border-soft)" }}>
        <div>
          <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 6, letterSpacing: "0.06em" }}>BENCHMARK</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {benchmarks.map(b => (
              <button key={b.id} type="button"
                className={`btn sm ${benchmark === b.id ? "primary" : "ghost"}`}
                style={{ justifyContent: "flex-start", textAlign: "left", padding: "6px 10px" }}
                onClick={() => setBenchmark(b.id)}>
                <span style={{ fontFamily: "var(--font-mono)", marginRight: 6 }}>{b.label}</span>
                <span style={{ fontSize: 10, color: "var(--dim)" }}>{b.sub}</span>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 6, letterSpacing: "0.06em" }}>SPLIT POLICY</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {splits.map(s => (
              <button key={s.id} type="button"
                className={`btn sm ${splitPolicy === s.id ? "primary" : "ghost"}`}
                style={{ padding: "4px 8px", fontFamily: "var(--font-mono)", fontSize: 11 }}
                title={s.hint}
                onClick={() => setSplitPolicy(s.id)}>{s.label}</button>
            ))}
          </div>
        </div>
      </div>

      {/* Featurizer multi-select */}
      <div style={{ padding: "12px 14px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 11, color: "var(--dim)", letterSpacing: "0.06em" }}>
            FEATURIZER MIX {pickedFeaturizers.size > 0 ? `(${pickedFeaturizers.size} picked)` : ""}
          </span>
          {pickedFeaturizers.size > 0 && (
            <button type="button" className="btn sm ghost"
              style={{ padding: "2px 6px", fontSize: 10 }}
              onClick={() => Array.from(pickedFeaturizers).forEach(toggleFeaturizer)}>
              Clear all
            </button>
          )}
        </div>
        {!featurizerCatalog && (
          <div style={{ fontSize: 11, color: "var(--muted)" }}>Loading featurizer catalog…</div>
        )}
        {groupedFeats && Object.entries(groupedFeats).map(([axis, items]) => {
          const isOpen = openAxis === axis;
          const nPicked = items.filter(it => pickedFeaturizers.has(it.id)).length;
          return (
            <div key={axis} style={{ borderTop: "1px solid var(--border-soft)", paddingTop: 8, marginTop: 8 }}>
              <button type="button" className="btn sm ghost"
                style={{ width: "100%", justifyContent: "space-between", padding: "4px 4px", fontSize: 11 }}
                onClick={() => setOpenAxis(isOpen ? null : axis)}>
                <span><Ico name="chevR" size={10} style={{ transform: isOpen ? "rotate(90deg)" : "none", marginRight: 4 }} /> {axis} ({items.length})</span>
                {nPicked > 0 && <Chip tone="signal">{nPicked} picked</Chip>}
              </button>
              {isOpen && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 4, marginTop: 6 }}>
                  {items.map(it => {
                    const picked = pickedFeaturizers.has(it.id);
                    return (
                      <button key={it.id} type="button"
                        className={`btn sm ${picked ? "primary" : "ghost"}`}
                        style={{ justifyContent: "flex-start", textAlign: "left", padding: "4px 8px", fontFamily: "var(--font-mono)", fontSize: 10 }}
                        title={`${it.short_desc} · dim=${it.dim} · ${it.cost}`}
                        disabled={!it.integrated}
                        onClick={() => toggleFeaturizer(it.id)}>
                        <span style={{ display: "inline-block", width: 10, height: 10, marginRight: 6,
                                       background: picked ? "var(--primary)" : "transparent",
                                       border: "1px solid var(--border)", borderRadius: 2 }} />
                        <span style={{ flex: 1 }}>{it.id.replace(`${axis}_`, "")}</span>
                        <span style={{ marginLeft: 6, color: "var(--dim)" }}>{it.dim}</span>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}


function TemplateHeader({ template, onChangeClick, objective, partnerLabel }) {
  if (!template) return null;
  const isRec = objective && template.objective_tag === objective;
  return (
    <div className="template-header">
      <div className="icon"><CatBadge category="encoder" size={24} /></div>
      <div className="meta">
        <div className="title-row">
          <h2 style={{ display: "inline-flex", alignItems: "center" }}>
            {template.label}
            <InfoTip word={template.label} text={`${template.blurb} Reference: ${(template.refs || []).join(", ")}.`} size={11} />
          </h2>
          <CostDot cost={template.cost} />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>
            {template.objective_tag}
          </span>
          <Chip tone="molecular">{partnerLabel || "any"} binding</Chip>
          {isRec && (
            <span className="rec-badge">★ recommended for your splits</span>
          )}
        </div>
        <div className="blurb">{template.blurb}</div>
        <div className="stats">
          <span>{template.nodes.length} NODES</span>
          <span>{template.edges.length} EDGES</span>
          <span>REFS · {(template.refs || []).join(", ")}</span>
        </div>
      </div>
      <div className="change">
        <button type="button" className="btn" onClick={onChangeClick}>
          Change template ↓
        </button>
      </div>
    </div>
  );
}

// Decision A — full gallery modal. Three filters (search, partners, cost),
// recommended-for-objective pinned at the top, then the remaining templates.
function GalleryModal({ open, onClose, templateId, onPick }) {
  const TEMPLATES = window.PS_PIPELINE_TEMPLATES || [];
  const objective = window.PS_DATA?.design_objective;
  const [partnerFilter, setPartnerFilter] = React.useState("all");
  const [costFilter, setCostFilter]       = React.useState("all");
  const [q, setQ] = React.useState("");

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const filtered = TEMPLATES.filter(t => {
    if (q && !t.label.toLowerCase().includes(q.toLowerCase()) && !t.blurb.toLowerCase().includes(q.toLowerCase())) return false;
    if (partnerFilter !== "all" && !(t.partners_tag || []).includes(partnerFilter)) return false;
    if (costFilter !== "all" && t.cost !== costFilter) return false;
    return true;
  });
  const recommended = filtered.filter(t => objective && t.objective_tag === objective);
  const rest        = filtered.filter(t => !objective || t.objective_tag !== objective);

  return (
    <div className="scrim" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 1080 }}>
        <div className="modal-h">
          <div style={{ flex: 1 }}>
            <div className="t">Architecture templates</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              {TEMPLATES.length} templates{objective ? ` · recommended for your design objective: "${objective}"` : ""}
            </div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="gallery-filters">
          <div className="group">
            <input className="input" placeholder="Search…" value={q}
              onChange={e => setQ(e.target.value)} style={{ width: 180 }} />
          </div>
          <div className="group">
            <span className="label">Partners</span>
            <div className="toggle sm">
              <button aria-pressed={partnerFilter === "all"} onClick={() => setPartnerFilter("all")}>All</button>
              <button aria-pressed={partnerFilter === "pl"}  onClick={() => setPartnerFilter("pl")}>P–L</button>
              <button aria-pressed={partnerFilter === "pp"}  onClick={() => setPartnerFilter("pp")}>P–P</button>
              <button aria-pressed={partnerFilter === "pna"} onClick={() => setPartnerFilter("pna")}>P–NA</button>
            </div>
          </div>
          <div className="group">
            <span className="label">Cost</span>
            <div className="toggle sm">
              <button aria-pressed={costFilter === "all"}  onClick={() => setCostFilter("all")}>All</button>
              <button aria-pressed={costFilter === "low"}  onClick={() => setCostFilter("low")}>Low</button>
              <button aria-pressed={costFilter === "mid"}  onClick={() => setCostFilter("mid")}>Mid</button>
              <button aria-pressed={costFilter === "high"} onClick={() => setCostFilter("high")}>High</button>
              <button aria-pressed={costFilter === "huge"} onClick={() => setCostFilter("huge")}>Huge</button>
            </div>
          </div>
        </div>
        <div className="modal-b" style={{ padding: "14px 16px" }}>
          {recommended.length > 0 && (
            <>
              <div className="gallery-section">
                <span>Recommended for "{objective}"</span>
                <span className="line" />
                <Chip tone="signal">{recommended.length}</Chip>
              </div>
              <div className="gallery-grid">
                {recommended.map(t => (
                  <TemplateCard key={t.id} t={t}
                    active={t.id === templateId}
                    onPick={() => { onPick(t.id); onClose && onClose(); }}
                    recommended />
                ))}
              </div>
            </>
          )}
          {rest.length > 0 && (
            <>
              <div className="gallery-section">
                <span>{recommended.length ? "All other templates" : "All templates"}</span>
                <span className="line" />
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{rest.length}</span>
              </div>
              <div className="gallery-grid">
                {rest.map(t => (
                  <TemplateCard key={t.id} t={t}
                    active={t.id === templateId}
                    onPick={() => { onPick(t.id); onClose && onClose(); }} />
                ))}
              </div>
            </>
          )}
          {filtered.length === 0 && (
            <div style={{ padding: 30, textAlign: "center", color: "var(--muted)", fontSize: 13 }}>
              No templates match those filters.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function TemplateCard({ t, active, onPick, recommended }) {
  return (
    <button type="button" className="tmpl-card" aria-pressed={active} onClick={onPick}>
      <div className="title-row">
        <span className="title">{t.label}</span>
        <CostDot cost={t.cost} />
        {recommended && <span className="rec-badge">★ rec</span>}
      </div>
      <div className="blurb">{t.blurb}</div>
      <div className="stats">
        <span>{t.nodes.length} nodes · {t.edges.length} edges</span>
        <span style={{ flex: 1 }} />
        <span className="objective">{t.objective_tag}</span>
      </div>
    </button>
  );
}

// CostDot moved to shared-v2.jsx — registered on window.CostDot.

// Layout dimensions per density mode (Decision B). The canvas auto-picks
// "comfortable" for templates ≤ 7 nodes and "compact" for ≥ 8; user can
// override with the toggle in the canvas header.
const PIPELINE_LAYOUT = {
  comfortable: { NODE_W: 196, NODE_H: 80, COL_GAP: 56, ROW_GAP: 36, PAD_X: 32, PAD_Y: 24, PORT_R: 6, PORT_LABELS: true,  LABEL_X: 36, LABEL_Y: 22, META_Y: 38, FOOT_Y: 60, LABEL_SIZE: 13 },
  compact:     { NODE_W: 156, NODE_H: 56, COL_GAP: 48, ROW_GAP: 24, PAD_X: 24, PAD_Y: 18, PORT_R: 5, PORT_LABELS: false, LABEL_X: 32, LABEL_Y: 20, META_Y: 34, FOOT_Y: 50, LABEL_SIZE: 12 },
};

// Read-only DAG canvas (Decisions B + F). Lays out nodes by (col, row),
// draws bezier edges with opacity-by-length, renders typed port rings on
// the node perimeter, and adds the category icon badge + left-stripe
// identity from Decision F. Honours slotOverrides so swapping a stage on
// the StageStack also updates the canvas labels + edge colors in real
// time. Decision D's floating inspector is rendered here when a node is
// selected.
function PipelineCanvas({ template, slotOverrides, density, selectedNodeId, onSelectNode, onCloseInspector, slotParams, onParamChange, userEdges, removedEdges, editMode, onAddEdge, onRemoveEdge, pushToast }) {
  if (!template) {
    return <div style={{ padding: 24, fontSize: 12, color: "var(--muted)" }}>No template selected.</div>;
  }
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  const PORT     = window.PS_PIPELINE_PORT_TYPES;
  const CATS     = window.PS_PIPELINE_CATEGORIES;
  const overrides = slotOverrides || {};
  const D = PIPELINE_LAYOUT[density] || PIPELINE_LAYOUT.comfortable;
  const showLabels = D.PORT_LABELS;

  // Resolve every template node into a positioned canvas node + port positions.
  // Effective type respects overrides so the DAG visualisation stays in sync
  // with the StageStack's per-slot picker.
  const positioned = React.useMemo(() => {
    return template.nodes.map(tn => {
      const effectiveType = overrides[tn.id] || tn.type;
      const def = NODE_IDX[effectiveType];
      const x = D.PAD_X + tn.col * (D.NODE_W + D.COL_GAP);
      const y = D.PAD_Y + tn.row * (D.NODE_H + D.ROW_GAP);
      const inPorts = (def?.inputs || []).map((p, i, arr) => ({
        port: p.port, types: p.types, x, y: y + ((i + 1) / (arr.length + 1)) * D.NODE_H,
      }));
      const outPorts = (def?.outputs || []).map((p, i, arr) => ({
        port: p.port, type: p.type, x: x + D.NODE_W, y: y + ((i + 1) / (arr.length + 1)) * D.NODE_H,
      }));
      return { tn, def, x, y, inPorts, outPorts };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [template, JSON.stringify(overrides), density]);
  const byId = React.useMemo(() => {
    const m = {}; for (const n of positioned) m[n.tn.id] = n; return m;
  }, [positioned]);
  // Edges = template default edges minus removedEdges plus userEdges.
  // Each edge carries its column span (for opacity) and an origin flag
  // (template vs user) so the canvas can visually distinguish them.
  const allEdgeRows = React.useMemo(
    () => effectiveEdges(template, userEdges, removedEdges),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [template, JSON.stringify(userEdges), JSON.stringify(removedEdges)]
  );
  const edges = React.useMemo(() => {
    const removedSet = new Set(removedEdges || []);
    const userKeys = new Set((userEdges || []).map(e => `${e.from}→${e.to}`));
    return allEdgeRows.map(e => {
      const [fromN, fromP] = e.from.split(":");
      const [toN,   toP]   = e.to.split(":");
      const fn = byId[fromN], tn = byId[toN];
      if (!fn || !tn) return null;
      const fout = fn.outPorts.find(p => p.port === fromP);
      const tin  = tn.inPorts.find (p => p.port === toP);
      if (!fout || !tin) return null;
      const colSpan = Math.abs(tn.tn.col - fn.tn.col);
      const userAdded = userKeys.has(`${e.from}→${e.to}`);
      return {
        from: e.from, to: e.to,
        x1: fout.x, y1: fout.y, x2: tin.x, y2: tin.y,
        type: fout.type, long: colSpan > 1, userAdded,
      };
    }).filter(Boolean);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allEdgeRows, byId]);

  const maxCol = Math.max(...template.nodes.map(n => n.col));
  const maxRow = Math.max(...template.nodes.map(n => n.row));
  const W = D.PAD_X * 2 + (maxCol + 1) * D.NODE_W + maxCol * D.COL_GAP;
  const H = D.PAD_Y * 2 + (maxRow + 1) * D.NODE_H + maxRow * D.ROW_GAP;

  // Selected node for the inspector (Decision D)
  const selected = selectedNodeId ? byId[selectedNodeId] : null;
  const popoverWidth = 280;
  // Anchor right of the node; flip to left if it would overflow.
  const inspectorAnchor = selected
    ? (() => {
        const right = selected.x + D.NODE_W + 14;
        const wouldOverflow = right + popoverWidth + 16 > W;
        const left = wouldOverflow
          ? Math.max(8, selected.x - popoverWidth - 14)
          : right;
        return { left, top: selected.y };
      })()
    : null;

  // ── Decision E (chunk 4) — pointer-driven drag-to-connect ──
  // drag = null | { sourceNodeId, sourcePort, sourceType, x, y, hover }
  //   hover = null | { nodeId, port, kind: "valid"|"invalid"|"cycle", reason?, suggest? }
  // svgRef is used to translate clientXY → viewBox space during drag.
  const svgRef = React.useRef(null);
  const [drag, setDrag] = React.useState(null);
  const dragRef = React.useRef(null);
  const [cycleToast, setCycleToast] = React.useState(null);

  // Convert client coords → SVG viewBox coords.
  const clientToSvg = React.useCallback((clientX, clientY) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = clientX; pt.y = clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    return pt.matrixTransform(ctm.inverse());
  }, []);

  // Hit-test: which input port (if any) is within INPUT_HIT_R of the cursor?
  // Threshold is generous so users don't need pixel-precise drops.
  const INPUT_HIT_R = Math.max(28, D.PORT_R * 5);
  const findHoverInput = React.useCallback((svgPt) => {
    if (!svgPt) return null;
    let best = null, bestD2 = INPUT_HIT_R * INPUT_HIT_R;
    for (const n of positioned) {
      for (const p of n.inPorts) {
        const dx = p.x - svgPt.x, dy = p.y - svgPt.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < bestD2) { bestD2 = d2; best = { n, p }; }
      }
    }
    return best;
  }, [positioned, INPUT_HIT_R]);

  // Drag-end side effects can't go in setDrag's reducer (it'd warn about
  // setState during render of ToastBus). Stash the in-flight drag in a ref.
  React.useEffect(() => { dragRef.current = drag; }, [drag]);

  const onPortPointerDown = (e, sourceNodeId, sourcePort, sourceType) => {
    if (!editMode) return;
    e.stopPropagation();
    e.preventDefault();
    const pt = clientToSvg(e.clientX, e.clientY);
    if (!pt) return;
    setDrag({ sourceNodeId, sourcePort, sourceType, x: pt.x, y: pt.y, hover: null });
  };

  React.useEffect(() => {
    if (!drag) return;
    const onMove = (e) => {
      const pt = clientToSvg(e.clientX, e.clientY);
      if (!pt) return;
      const found = findHoverInput(pt);
      if (!found) {
        setDrag(d => d ? { ...d, x: pt.x, y: pt.y, hover: null } : d);
        return;
      }
      const { n, p } = found;
      // Same-node self-loops are forbidden (also: source's own outputs).
      if (n.tn.id === drag.sourceNodeId) {
        setDrag(d => d ? { ...d, x: pt.x, y: pt.y, hover: null } : d);
        return;
      }
      // Type compatibility.
      const accepted = p.types || [];
      const typeOk = accepted.includes(drag.sourceType);
      // Cycle check uses CURRENT effective edges (so previously-added user edges count).
      const fromKey = `${drag.sourceNodeId}:${drag.sourcePort}`;
      const toKey   = `${n.tn.id}:${p.port}`;
      const wouldCycle = wouldCreateCycle(allEdgeRows, drag.sourceNodeId, n.tn.id);
      // Duplicate-edge guard.
      const alreadyExists = allEdgeRows.some(ex => ex.from === fromKey && ex.to === toKey);
      let kind = "valid";
      let reason = null, suggest = null;
      if (alreadyExists) {
        kind = "invalid";
        reason = "An edge already connects these ports.";
      } else if (!typeOk) {
        kind = "invalid";
        const PORT = window.PS_PIPELINE_PORT_TYPES;
        reason = `${PORT[drag.sourceType]?.label || drag.sourceType} → port accepts ${accepted.map(t => PORT[t]?.label || t).join(" or ")}`;
        const bridge = suggestBridgeNode(drag.sourceType, accepted);
        if (bridge) suggest = `Try ${bridge.label} as a bridge — it accepts ${PORT[drag.sourceType]?.short || drag.sourceType} and emits a compatible type.`;
      } else if (wouldCycle) {
        kind = "cycle";
        reason = "That edge would form a cycle. Pipelines must be acyclic.";
      }
      setDrag(d => d ? { ...d, x: pt.x, y: pt.y, hover: { nodeId: n.tn.id, port: p.port, kind, reason, suggest } } : d);
    };
    const onUp = (e) => {
      const d = dragRef.current;
      setDrag(null);
      if (!d) return;
      const h = d.hover;
      if (!h) return;
      if (h.kind === "valid") {
        onAddEdge && onAddEdge(`${d.sourceNodeId}:${d.sourcePort}`, `${h.nodeId}:${h.port}`);
      } else if (h.kind === "cycle") {
        setCycleToast("That edge would form a cycle. Pipelines must be acyclic.");
        if (pushToast) pushToast({ title: "Edge rejected", body: "That edge would form a cycle.", level: "warn", ttl_ms: 3000 });
      } else if (h.kind === "invalid" && pushToast) {
        pushToast({ title: "Edge rejected", body: h.reason || "Incompatible port types.", level: "warn", ttl_ms: 3500 });
      }
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup",   onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup",   onUp);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag?.sourceNodeId, drag?.sourcePort, drag?.sourceType, allEdgeRows, positioned, onAddEdge, pushToast]);

  // Auto-fade the cycle toast.
  React.useEffect(() => {
    if (!cycleToast) return;
    const t = setTimeout(() => setCycleToast(null), 3000);
    return () => clearTimeout(t);
  }, [cycleToast]);

  // ── Edge-delete hover state — show × on the edge midpoint when hovered ──
  const [hoverEdgeIdx, setHoverEdgeIdx] = React.useState(null);
  React.useEffect(() => { if (!editMode) setHoverEdgeIdx(null); }, [editMode]);

  // While a drag is in flight, every input port gets a 'valid' or 'invalid'
  // class so the canvas filters by glow / dim. Compute once per render.
  const portDecorations = React.useMemo(() => {
    if (!drag) return { dim: new Set(), valid: new Set() };
    const dim = new Set(), valid = new Set();
    const accepted = drag.sourceType;
    const PORT = window.PS_PIPELINE_PORT_TYPES;
    for (const n of positioned) {
      if (n.tn.id === drag.sourceNodeId) continue;
      for (const p of n.inPorts) {
        const ok = (p.types || []).includes(accepted);
        const cyc = wouldCreateCycle(allEdgeRows, drag.sourceNodeId, n.tn.id);
        const id = `${n.tn.id}:${p.port}`;
        if (ok && !cyc) valid.add(id);
        else dim.add(id);
      }
    }
    return { dim, valid };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drag?.sourceNodeId, drag?.sourceType, positioned, allEdgeRows]);

  return (
    <div className="canvas-wrap grid-bg" style={{ position: "relative", minHeight: H + 20 }}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="xMidYMid meet"
        style={{ width: "100%", height: Math.max(220, H), display: "block" }}>
        {/* edges (drawn first so nodes sit on top). In edit mode each edge
            gets a hover-X delete affordance at its midpoint. User-added
            edges are drawn dashed so they read distinct from template edges. */}
        {edges.map((e, i) => {
          const mx = (e.x1 + e.x2) / 2;
          const my = (e.y1 + e.y2) / 2;
          const color = PORT[e.type]?.color || "var(--border-strong)";
          const isHover = editMode && hoverEdgeIdx === i;
          return (
            <g key={"e" + i}
              onMouseEnter={() => editMode && setHoverEdgeIdx(i)}
              onMouseLeave={() => editMode && setHoverEdgeIdx(prev => prev === i ? null : prev)}>
              {/* Wider invisible hit target — makes it easy to land on the edge */}
              {editMode && (
                <path
                  d={`M ${e.x1} ${e.y1} C ${mx} ${e.y1}, ${mx} ${e.y2}, ${e.x2} ${e.y2}`}
                  stroke="transparent" strokeWidth="14" fill="none"
                  style={{ cursor: "pointer" }}
                  onClick={(ev) => {
                    ev.stopPropagation();
                    onRemoveEdge && onRemoveEdge(e.from, e.to);
                  }} />
              )}
              <path className="edge"
                d={`M ${e.x1} ${e.y1} C ${mx} ${e.y1}, ${mx} ${e.y2}, ${e.x2} ${e.y2}`}
                stroke={color}
                data-long={String(e.long)}
                strokeDasharray={e.userAdded ? "5 4" : null}
                style={{ pointerEvents: "none" }} />
              <title>{(e.userAdded ? "(user-added) " : "") + (PORT[e.type]?.label || e.type)}</title>
              {/* Edit-mode delete × badge at midpoint, shown on hover */}
              {editMode && isHover && (
                <g style={{ pointerEvents: "none" }}>
                  <circle cx={mx} cy={my} r="9" fill="var(--error)" />
                  <text x={mx} y={my + 4} textAnchor="middle"
                    fontFamily="var(--font-mono)" fontSize="12" fontWeight="700" fill="#fff">×</text>
                </g>
              )}
            </g>
          );
        })}

        {/* Ghost edge while dragging */}
        {drag && (() => {
          const PORTM = window.PS_PIPELINE_PORT_TYPES;
          const sourceNode = byId[drag.sourceNodeId];
          if (!sourceNode) return null;
          const fout = sourceNode.outPorts.find(p => p.port === drag.sourcePort);
          if (!fout) return null;
          const targetX = drag.hover
            ? byId[drag.hover.nodeId]?.inPorts.find(p => p.port === drag.hover.port)?.x ?? drag.x
            : drag.x;
          const targetY = drag.hover
            ? byId[drag.hover.nodeId]?.inPorts.find(p => p.port === drag.hover.port)?.y ?? drag.y
            : drag.y;
          const stroke = drag.hover?.kind === "invalid" || drag.hover?.kind === "cycle"
            ? "var(--error)"
            : (PORTM[drag.sourceType]?.color || "var(--primary)");
          const mx = (fout.x + targetX) / 2;
          return (
            <path
              className="ghost-edge"
              d={`M ${fout.x} ${fout.y} C ${mx} ${fout.y}, ${mx} ${targetY}, ${targetX} ${targetY}`}
              stroke={stroke} strokeWidth="2" strokeDasharray="4 3" fill="none"
              style={{ pointerEvents: "none" }} />
          );
        })()}
        {/* nodes */}
        {positioned.map(n => {
          if (!n.def) return null;
          const CAT = CATS[n.def.category];
          const isSelected = selectedNodeId === n.tn.id;
          return (
            <g
              key={n.tn.id}
              transform={`translate(${n.x} ${n.y})`}
              onClick={() => onSelectNode && onSelectNode(n.tn.id)}
              style={{ cursor: "pointer" }}>
              {/* Left-edge category stripe */}
              <rect className="cat-stripe" data-cat={n.def.category} x="0" y="0" width="4" height={D.NODE_H} />
              {/* Node body */}
              <rect className="node-rect"
                data-selected={isSelected ? "true" : "false"}
                x="0" y="0" width={D.NODE_W} height={D.NODE_H} rx="6" />
              {/* Category icon badge (upper-left of body) */}
              <g transform="translate(12, 10)">
                <rect className="cat-badge" data-cat={n.def.category} x="0" y="0" width="16" height="16" rx="4" strokeWidth="1" />
                <g transform="translate(2, 2)">
                  {CAT && (
                    <path className="cat-icon-glyph" data-cat={n.def.category}
                      d={CAT.glyph}
                      fill="none" strokeWidth="1.4"
                      strokeLinecap="round" strokeLinejoin="round" />
                  )}
                </g>
              </g>
              {/* Label */}
              <text x={D.LABEL_X} y={D.LABEL_Y} fontSize={D.LABEL_SIZE}
                fontFamily="var(--font-sans)" fontWeight="600" fill="var(--text-strong)">
                {n.def.label}
              </text>
              {/* Category caps tag + cost */}
              <text x={D.LABEL_X} y={D.META_Y} fontSize="9"
                fontFamily="var(--font-mono)" fill="var(--dim)" letterSpacing="0.06em">
                {n.def.category.toUpperCase()} · cost: {n.def.cost}
              </text>
              {/* Param count line — comfortable only */}
              {density === "comfortable" && (
                <text x={D.LABEL_X} y={D.FOOT_Y} fontSize="10"
                  fontFamily="var(--font-mono)" fill="var(--muted)">
                  {(n.def.params || []).length} param{(n.def.params || []).length === 1 ? "" : "s"}
                  {overrides[n.tn.id] ? " · swapped" : ""}
                </text>
              )}
              <title>{n.def.label} ({n.def.id}) · ref: {(n.def.refs || []).join(", ")}</title>
              {/* Input port rings (outer-color stroke, inner-surface fill).
                  During drag, valid targets get a wider stroke / brighter ring;
                  invalid targets dim. */}
              {n.inPorts.map((p, i) => {
                const localY = p.y - n.y;
                const color = (p.types && p.types[0] && PORT[p.types[0]]?.color) || "var(--border-strong)";
                const portKey = `${n.tn.id}:${p.port}`;
                const isHoverTarget = drag?.hover && drag.hover.nodeId === n.tn.id && drag.hover.port === p.port;
                const isValidDuringDrag   = drag && portDecorations.valid.has(portKey);
                const isInvalidDuringDrag = drag && portDecorations.dim.has(portKey);
                const flashAttr = isHoverTarget
                  ? (drag.hover.kind === "valid" ? "valid" : "invalid")
                  : null;
                return (
                  <g key={"in" + i}>
                    <circle className="port-outer" cx={0} cy={localY}
                      r={isHoverTarget ? D.PORT_R + 2 : D.PORT_R}
                      stroke={color}
                      strokeWidth={isValidDuringDrag ? 3 : 2}
                      data-flash={flashAttr}
                      data-dim={isInvalidDuringDrag ? "true" : "false"}>
                      <title>{p.port} · accepts: {(p.types || []).map(t => PORT[t]?.label || t).join(" | ")}</title>
                    </circle>
                    {showLabels && (
                      <text x={-10} y={localY + 3} textAnchor="end"
                        fontSize="9" fontFamily="var(--font-mono)" fill={color} opacity={isInvalidDuringDrag ? 0.3 : 0.85}>
                        {(p.types || []).map(t => PORT[t]?.short || t).join("|")}
                      </text>
                    )}
                  </g>
                );
              })}
              {/* Output port rings — pointerdown starts a drag in edit mode */}
              {n.outPorts.map((p, i) => {
                const localY = p.y - n.y;
                const color = PORT[p.type]?.color || "var(--border-strong)";
                const isSource = drag?.sourceNodeId === n.tn.id && drag.sourcePort === p.port;
                return (
                  <g key={"out" + i}>
                    <circle className="port-outer" cx={D.NODE_W} cy={localY}
                      r={isSource ? D.PORT_R + 2 : D.PORT_R}
                      stroke={color}
                      strokeWidth={isSource || editMode ? 2.5 : 2}
                      onPointerDown={(ev) => onPortPointerDown(ev, n.tn.id, p.port, p.type)}
                      style={{
                        cursor: editMode ? "grab" : "default",
                        // Pointer-events: only attach when in edit mode so non-edit click flows past to the node.
                        pointerEvents: editMode ? "auto" : "none",
                      }}>
                      <title>{p.port} · emits: {PORT[p.type]?.label || p.type}{editMode ? "  ·  drag to add edge" : ""}</title>
                    </circle>
                    {showLabels && (
                      <text x={D.NODE_W + 10} y={localY + 3}
                        fontSize="9" fontFamily="var(--font-mono)" fill={color} opacity="0.85">
                        {PORT[p.type]?.short || p.type}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      {/* Decision E — invalid-drop teach tooltip while dragging */}
      {drag && drag.hover && (drag.hover.kind === "invalid" || drag.hover.kind === "cycle") && (() => {
        // Anchor near the hovered input port, but project into pixel coords
        // by scaling the SVG viewBox to the rendered width.
        const target = byId[drag.hover.nodeId];
        const tgtPort = target?.inPorts.find(p => p.port === drag.hover.port);
        if (!tgtPort) return null;
        // Convert SVG → pixel by reading the SVG bounding box.
        const svg = svgRef.current;
        const rect = svg?.getBoundingClientRect();
        const wrap = svg?.parentElement?.getBoundingClientRect();
        if (!rect || !wrap) return null;
        const scaleX = rect.width / W;
        const scaleY = rect.height / H;
        const px = (tgtPort.x * scaleX) + (rect.left - wrap.left) + 16;
        const py = (tgtPort.y * scaleY) + (rect.top  - wrap.top)  + 10;
        return (
          <div className="port-tooltip" style={{ left: px, top: py }}>
            <span className="k">{drag.hover.kind === "cycle" ? "Cycle" : "Won't connect"}</span>
            {drag.hover.reason}
            {drag.hover.suggest && (
              <div style={{ marginTop: 6, fontSize: 11, color: "var(--text-strong)" }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--primary)", letterSpacing: "0.06em" }}>SUGGEST · </span>
                {drag.hover.suggest}
              </div>
            )}
          </div>
        );
      })()}
      {/* Decision E — cycle-rejected banner toast (3s auto-dismiss) */}
      {cycleToast && (
        <div className="canvas-toast">
          <span style={{ marginRight: 6 }}>⚠</span>{cycleToast}
        </div>
      )}
      {/* Inspector popover (Decision D) — opens on node click */}
      {selected && inspectorAnchor && (
        <NodeInspector
          slot={selected}
          anchor={inspectorAnchor}
          canvasWidth={W}
          density={density}
          slotOverrides={slotOverrides}
          slotParams={slotParams}
          onParamChange={onParamChange}
          onClose={onCloseInspector}
        />
      )}
    </div>
  );
}

// Inline port-type legend strip (Decision C). Shows only the port types
// actually used in the current effective template — keeps the legend short
// and honest. Has a "+ Show all 18" button that opens LegendAllModal.
function LegendStrip({ template, slotOverrides, onShowAll }) {
  if (!template) return null;
  const PORT = window.PS_PIPELINE_PORT_TYPES;
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  const overrides = slotOverrides || {};
  const usedTypes = new Set();
  for (const tn of template.nodes) {
    const def = NODE_IDX[overrides[tn.id] || tn.type];
    if (!def) continue;
    for (const o of def.outputs) usedTypes.add(o.type);
    for (const i of def.inputs)  for (const t of i.types) usedTypes.add(t);
  }
  const items = Array.from(usedTypes).map(t => ({ id: t, ...PORT[t] })).filter(x => x.label);
  return (
    <div className="legend-strip">
      <span className="lbl">Port types in use</span>
      {items.map(it => (
        <span key={it.id} className="legend-chip" title={it.label}>
          <span className="dot" style={{ background: it.color }} />
          {it.short}
        </span>
      ))}
      <div style={{ flex: 1 }} />
      <button type="button" className="btn sm ghost" onClick={onShowAll}>
        Show all 18 →
      </button>
    </div>
  );
}

// Modal listing all 18 port types grouped by usage tier (Decision C).
function LegendAllModal({ open, onClose }) {
  // Hooks must run on every render in the same order — guard inside the
  // effect, not around it.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  const PORT = window.PS_PIPELINE_PORT_TYPES;
  const groups = [
    { name: "Raw inputs",    ids: ["aa_seq","msa","smiles_tokens","mol_graph_2d","atom_cloud_3d","backbone_3d","complex_3d","voxel","surface_mesh","structure_tokens","descriptors"] },
    { name: "Learned reps",  ids: ["embedding_1d","embedding_2d_pair","embedding_3d","contact_map"] },
    { name: "Predictions",   ids: ["pose","scalar","prob"] },
  ];
  return (
    <div className="scrim" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal" onClick={(e) => e.stopPropagation()} style={{ width: 720 }}>
        <div className="modal-h">
          <div style={{ flex: 1 }}>
            <div className="t">All 18 port types</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              Color is identity — edges only connect compatible types.
            </div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-b">
          {groups.map(g => (
            <div key={g.name} style={{ marginBottom: 18 }}>
              <div className="gallery-section">
                <span>{g.name}</span>
                <span className="line" />
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{g.ids.length}</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 8 }}>
                {g.ids.map(id => {
                  const p = PORT[id];
                  if (!p) return null;
                  return (
                    <div key={id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", border: "1px solid var(--border)", borderRadius: "var(--r)", background: "var(--surface-2)" }}>
                      <span style={{ width: 12, height: 12, borderRadius: 6, background: p.color, flexShrink: 0 }} />
                      <div style={{ flex: 1 }}>
                        <div style={{ fontSize: 12, color: "var(--text-strong)" }}>{p.label}</div>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
                          {p.short} · {id}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// NodeInspector — floating popover anchored to the right of the selected
// node (flips left if it would overflow the canvas). Decision D.
// Critically: this is the SAME ParamField the StageStack's SlotEditor uses
// and writes to the SAME slotParams state — so canvas-edit and stage-edit
// are two views of one source of truth. Swap-pickers are NOT shown here
// (that's the stage panel's job); the inspector is for tuning the current
// node's params. The bottom "Open in Stage panel" jumps to the StageStack
// for users who want the full slot view.
function NodeInspector({ slot, anchor, canvasWidth, density, slotOverrides, slotParams, onParamChange, onClose }) {
  const def = slot.def;
  if (!def) return null;
  const params = (slotParams || {})[slot.tn.id] || {};
  const isOverridden = (slotOverrides || {})[slot.tn.id] != null;

  React.useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose && onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="inspector" style={{ left: anchor.left, top: anchor.top }}>
      <div className="ins-h">
        <CatBadge category={def.category} size={22} />
        <div className="title">
          {def.label}
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", fontWeight: 400 }}>
            {def.category.toUpperCase()} · slot {slot.tn.id}{isOverridden ? " · swapped" : ""}
          </div>
        </div>
        {def.blurb && <InfoTip word={def.glossary || def.label} text={def.blurb} />}
        <button type="button" className="btn ghost sm" onClick={onClose} aria-label="Close inspector"
          style={{ padding: "2px 6px" }}>×</button>
      </div>
      <div className="ins-b">
        {(def.params || []).length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--muted)", textAlign: "center", padding: "8px 0" }}>
            No parameters to configure.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {def.params.map(p => (
              <ParamField
                key={p.key}
                param={p}
                value={params[p.key] !== undefined ? params[p.key] : p.default}
                onChange={(v) => onParamChange && onParamChange(slot.tn.id, p.key, v)}
              />
            ))}
          </div>
        )}
      </div>
      <div className="ins-f">
        <button type="button" className="reset"
          disabled={Object.keys(params).length === 0}
          onClick={() => {
            // Reset every param on this slot to template defaults (no params set).
            for (const p of def.params || []) {
              if (params[p.key] !== undefined && onParamChange) {
                onParamChange(slot.tn.id, p.key, p.default);
              }
            }
          }}>
          {Object.keys(params).length ? "Reset to template defaults" : "Using template defaults"}
        </button>
        <button type="button" className="btn sm primary" onClick={onClose}>Done</button>
      </div>
    </div>
  );
}

// =============================================================================
// CHUNK 3 — stage panels, swap-compatible picker, leak-tested launch path
// =============================================================================

// What types feed into / out of a slot in the template's wiring? Used by
// swapCandidatesFor and the leak test.
function slotIO(template, slotNodeId) {
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  const incomingTypes = [];   // types the slot must accept on some input
  const outgoingTypeSets = []; // each entry is the SET of types the downstream port accepts
  for (const e of template.edges) {
    const [fromN, fromP] = e.from.split(":");
    const [toN,   toP]   = e.to.split(":");
    if (toN === slotNodeId) {
      const upDef = NODE_IDX[template.nodes.find(n => n.id === fromN)?.type];
      const upOut = upDef?.outputs?.find(o => o.port === fromP);
      if (upOut) incomingTypes.push(upOut.type);
    }
    if (fromN === slotNodeId) {
      const downDef = NODE_IDX[template.nodes.find(n => n.id === toN)?.type];
      const downIn  = downDef?.inputs?.find(i => i.port === toP);
      if (downIn) outgoingTypeSets.push(new Set(downIn.types));
    }
  }
  return { incomingTypes, outgoingTypeSets };
}

// Resolve the user's current binding-partner selection ("pl" | "pp" | "pna")
// from the Dataset screen state. Used by node-catalog filters so users
// running a PPI task don't see ligand-only encoders/preprocessors.
function currentPartners() {
  const p = window.PS_DATA?.binding_partners;
  if (p && p.size) return Array.from(p);
  if (Array.isArray(p) && p.length) return p;
  return ["pl"];
}

// Is this node-type compatible with the current binding-partner selection?
// Nodes WITHOUT a partners_whitelist are visible everywhere; nodes WITH one
// only show up if their whitelist intersects the active partners.
function nodeAllowedForPartners(def, partners) {
  if (!def || !def.partners_whitelist) return true;
  const active = partners || currentPartners();
  return def.partners_whitelist.some(p => active.includes(p));
}

// Given a slot in a template, return the list of node-type ids that are
// type-compatible substitutes (same category, accepts every upstream type,
// emits a type each downstream port accepts).
function swapCandidatesFor(template, slotNodeId) {
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  const slot = template.nodes.find(n => n.id === slotNodeId);
  if (!slot) return [];
  const slotDef = NODE_IDX[slot.type];
  if (!slotDef) return [];
  const { incomingTypes, outgoingTypeSets } = slotIO(template, slotNodeId);
  const partners = currentPartners();
  const out = [];
  for (const def of window.PS_PIPELINE_NODE_TYPES) {
    if (def.category !== slotDef.category) continue;
    // Hide ligand-only nodes when the user's task isn't protein-ligand
    // (and vice versa for any future protein-only-tagged nodes). The
    // currently-installed slot is always retained — see the unshift at
    // the bottom — so the user can revert.
    if (!nodeAllowedForPartners(def, partners) && def.id !== slotDef.id) continue;
    // Strict input check: every candidate input port must be covered by an
    // upstream type AND every upstream type must be accepted by some input.
    // This prevents dangling input ports (e.g. SaProt's `struct` port would
    // sit unwired if we let it substitute a 1-input slot).
    if (def.inputs.length !== incomingTypes.length) continue;
    const allInputsCovered = def.inputs.every(i => incomingTypes.some(t => i.types.includes(t)));
    const allIncomingFit   = incomingTypes.every(t => def.inputs.some(i => i.types.includes(t)));
    // Permissive output check: each downstream consumer must be served by
    // some output of the candidate. Extra outputs are fine (just unused).
    const outsOk = outgoingTypeSets.every(accepted =>
      def.outputs.some(o => accepted.has(o.type)));
    if (allInputsCovered && allIncomingFit && outsOk) out.push(def.id);
  }
  // Always include the original at the top so the user can revert.
  if (!out.includes(slotDef.id)) out.unshift(slotDef.id);
  else out.sort((a, b) => (a === slotDef.id ? -1 : b === slotDef.id ? 1 : 0));
  return out;
}

// Effective edges for the loaded template = template default edges minus
// any the user deleted, plus any the user added. Pure helper consumed by
// the canvas, the leak test, and buildEffectiveConfig — single source of
// truth for "which edges does this pipeline actually have right now".
function effectiveEdges(template, userEdges, removedEdges) {
  const removed = new Set(removedEdges || []);
  const base = (template?.edges || []).filter(e => !removed.has(`${e.from}→${e.to}`));
  return [...base, ...(userEdges || [])];
}

// Does adding fromNodeId → toNodeId close a cycle, given the current
// edge set? BFS from toNodeId following outgoing edges; if we ever
// reach fromNodeId, the new edge would be a back-link.
function wouldCreateCycle(edges, fromNodeId, toNodeId) {
  if (fromNodeId === toNodeId) return true;
  const adj = new Map();
  for (const e of edges) {
    const [fn] = e.from.split(":");
    const [tn] = e.to.split(":");
    if (!adj.has(fn)) adj.set(fn, new Set());
    adj.get(fn).add(tn);
  }
  const seen = new Set([toNodeId]);
  const queue = [toNodeId];
  while (queue.length) {
    const cur = queue.shift();
    if (cur === fromNodeId) return true;
    const nexts = adj.get(cur);
    if (!nexts) continue;
    for (const n of nexts) {
      if (!seen.has(n)) { seen.add(n); queue.push(n); }
    }
  }
  return false;
}

// Suggest a 1-hop bridge node that resolves a type mismatch: any node
// whose inputs accept sourceType AND whose outputs emit a type the
// target's input port accepts. Returns the cheapest match or null.
function suggestBridgeNode(sourceType, targetTypes) {
  const accepted = new Set(targetTypes || []);
  const order = { low: 1, mid: 2, high: 3, huge: 4 };
  const partners = currentPartners();
  const candidates = (window.PS_PIPELINE_NODE_TYPES || []).filter(def => {
    if (def.category === "input" || def.category === "head") return false;
    if (!nodeAllowedForPartners(def, partners)) return false;
    const acceptsSource = (def.inputs || []).some(inp => (inp.types || []).includes(sourceType));
    const emitsTarget   = (def.outputs || []).some(out => accepted.has(out.type));
    return acceptsSource && emitsTarget;
  });
  candidates.sort((a, b) => (order[a.cost] || 99) - (order[b.cost] || 99));
  return candidates[0] || null;
}

// Build the full effective config that "Launch" hands off downstream.
// Resolves overrides, fills in default params, returns a plain JSON shape.
// Honours userEdges + removedEdges so chunk-4 drag-edits make it through
// to the launch payload and the leak test.
function buildEffectiveConfig(template, slotOverrides, slotParams, userEdges, removedEdges) {
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  const nodes = template.nodes.map(tn => {
    const effectiveType = (slotOverrides && slotOverrides[tn.id]) || tn.type;
    const def = NODE_IDX[effectiveType] || NODE_IDX[tn.type];
    const userParams = (slotParams && slotParams[tn.id]) || {};
    const params = {};
    for (const p of def.params || []) {
      params[p.key] = (userParams[p.key] !== undefined) ? userParams[p.key] : p.default;
    }
    return {
      slot_id: tn.id,
      node_type: def.id,
      category: def.category,
      label: def.label,
      params,
      // Surface I/O shape so downstream serializers don't need to re-look-up.
      inputs:  (def.inputs  || []).map(i => ({ port: i.port, types: i.types })),
      outputs: (def.outputs || []).map(o => ({ port: o.port, type: o.type })),
    };
  });
  return {
    template_id: template.id,
    template_label: template.label,
    objective_tag: template.objective_tag,
    partners_tag: template.partners_tag,
    nodes,
    edges: effectiveEdges(template, userEdges, removedEdges),
    edge_mutations: { added: (userEdges || []).length, removed: (removedEdges || []).length },
    built_at: Date.now(),
  };
}

// Leak test — walk every edge in the effective config and assert the upstream
// emitted type is in the downstream accepted-types set. Returns { ok, errors,
// summary } where summary is a one-liner for the launch toast.
function leakTestConfig(effective) {
  const errors = [];
  const byId = {};
  for (const n of effective.nodes) byId[n.slot_id] = n;
  for (const e of effective.edges) {
    const [fromN, fromP] = e.from.split(":");
    const [toN,   toP]   = e.to.split(":");
    const fn = byId[fromN], tn = byId[toN];
    if (!fn || !tn) { errors.push(`edge references missing slot: ${e.from} → ${e.to}`); continue; }
    const fout = fn.outputs.find(o => o.port === fromP);
    const tin  = tn.inputs.find(i => i.port === toP);
    if (!fout) { errors.push(`missing output port: ${fromN}:${fromP}`); continue; }
    if (!tin)  { errors.push(`missing input port: ${toN}:${toP}`);     continue; }
    if (!tin.types.includes(fout.type)) {
      errors.push(`type mismatch: ${fn.label}.${fromP} (${fout.type}) → ${tn.label}.${toP} (accepts ${tin.types.join(", ")})`);
    }
  }
  // Param sanity — make sure no override silently mangled an enum.
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  for (const n of effective.nodes) {
    const def = NODE_IDX[n.node_type];
    if (!def) { errors.push(`unknown node_type: ${n.node_type}`); continue; }
    for (const p of def.params || []) {
      const v = n.params[p.key];
      if (p.kind === "enum" && p.options && !p.options.includes(v)) {
        errors.push(`param ${n.label}.${p.key} = ${JSON.stringify(v)} not in ${p.options.join("|")}`);
      }
      if ((p.kind === "int" || p.kind === "float") && typeof v !== "number") {
        errors.push(`param ${n.label}.${p.key} should be number, got ${typeof v}`);
      }
      if (p.kind === "bool" && typeof v !== "boolean") {
        errors.push(`param ${n.label}.${p.key} should be bool, got ${typeof v}`);
      }
    }
  }
  const ok = errors.length === 0;
  const stages = effective.nodes.filter(n => n.category !== "input").map(n => n.label).join(" → ");
  const summary = ok
    ? `${effective.template_label}: ${stages} · ${effective.nodes.length} modules wired`
    : `${errors.length} wiring error${errors.length === 1 ? "" : "s"}`;
  return { ok, errors, summary };
}

// Console-runnable hook so the user can leak-test any state from devtools.
// Returns the same object the launch button uses, plus prints a digest.
window.PS_LEAK_TEST = function () {
  const D = window.PS_DATA;
  const TEMPLATES = window.PS_PIPELINE_TEMPLATES;
  if (!D?.pipeline?.template_id) { console.warn("PS_LEAK_TEST: no pipeline template loaded"); return null; }
  const tpl = TEMPLATES.find(t => t.id === D.pipeline.template_id);
  if (!tpl) { console.warn("PS_LEAK_TEST: template not found:", D.pipeline.template_id); return null; }
  const effective = buildEffectiveConfig(
    tpl,
    D.pipeline.overrides || {},
    D.pipeline.params || {},
    D.pipeline.userEdges || [],
    D.pipeline.removedEdges || []
  );
  const result = leakTestConfig(effective);
  console.log("%c PS_LEAK_TEST ", "background: " + (result.ok ? "#22c55e" : "#ef4444") + "; color: white; padding: 2px 6px; border-radius: 3px",
    result.ok ? "PASS" : "FAIL");
  console.log("summary:", result.summary);
  if (!result.ok) console.log("errors:", result.errors);
  console.log("effective:", effective);
  return { effective, result };
};

// ── StageStack — one card per role, one slot per role-node, picker + params ──

function StageStack({ template, slotOverrides, slotParams, onPickSlot, onParamChange }) {
  if (!template || !template.roles) {
    return (
      <div className="card">
        <div className="card-h"><span className="t">Stages</span></div>
        <div style={{ padding: 16, fontSize: 12, color: "var(--muted)" }}>
          This template doesn't declare role slots yet.
        </div>
      </div>
    );
  }
  const ROLES = window.PS_PIPELINE_ROLE_LABELS;
  // Render the roles in a stable canonical order so the column flow doesn't
  // jump when switching templates that expose different sets.
  const order = ["preprocess", "protein_encoder", "ligand_encoder", "trunk", "retrieval", "fusion", "scorer", "head"];
  const visible = order.filter(r => template.roles[r]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {visible.map(role => (
        <RoleCard
          key={role}
          template={template}
          role={role}
          label={ROLES[role]?.label || role}
          sub={ROLES[role]?.sub || ""}
          slotIds={template.roles[role]}
          slotOverrides={slotOverrides}
          slotParams={slotParams}
          onPickSlot={onPickSlot}
          onParamChange={onParamChange}
        />
      ))}
    </div>
  );
}

function RoleCard({ template, role, label, sub, slotIds, slotOverrides, slotParams, onPickSlot, onParamChange }) {
  // Role-level explanations — used as one-shot text on the InfoTip next to
  // the card title for users who aren't sure what "fusion" or "head" means.
  const ROLE_BLURBS = {
    protein_encoder: "Reads protein input (sequence or structure) and produces a learned vector for each residue. The bulk of the compute on the protein side.",
    ligand_encoder:  "Reads the small-molecule input (SMILES, graph, or 3D) and produces a learned vector. Choose by what your data format is.",
    trunk:           "AlphaFold-style trunk that runs over an MSA + pair representation and outputs the per-residue and per-pair embeddings every downstream module reads.",
    retrieval:       "Looks up the top-k most similar candidates from a pre-built index. Used in two-stage cascades where a cheap retriever shortlists and an expensive reranker scores.",
    preprocess:      "Stuff that happens before encoding — pocket cropping, protonation, conformer generation, docking. Cheap relative to training but affects every downstream module.",
    scorer:          "A post-fusion module that turns the joint signal into features the prediction head can consume (e.g. an EGNN over a generated pose).",
    fusion:          "Combines the protein and ligand representations into a joint signal. The fusion choice is usually what gives one DTA model an edge over another.",
    head:            "The output layer — what the model is asked to predict. Regression for affinity, classification for binary DTI, ranking for retrieval, pose for docking.",
  };
  return (
    <div className="card" data-field={`pipeline.role.${role}`}>
      <div className="card-h">
        <span className="t" style={{ display: "inline-flex", alignItems: "center" }}>
          {label}
          <InfoTip word={label} text={ROLE_BLURBS[role]} />
        </span>
        <span className="sub">{sub}</span>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
          {slotIds.length} slot{slotIds.length === 1 ? "" : "s"}
        </span>
      </div>
      <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 12 }}>
        {slotIds.map(slotId => (
          <SlotEditor
            key={slotId}
            template={template}
            slotId={slotId}
            slotOverrides={slotOverrides}
            slotParams={slotParams}
            onPickSlot={onPickSlot}
            onParamChange={onParamChange}
          />
        ))}
      </div>
    </div>
  );
}

function SlotEditor({ template, slotId, slotOverrides, slotParams, onPickSlot, onParamChange }) {
  const NODE_IDX = window.PS_PIPELINE_NODE_INDEX;
  const tn = template.nodes.find(n => n.id === slotId);
  if (!tn) return null;
  const effectiveType = (slotOverrides[slotId]) || tn.type;
  const def = NODE_IDX[effectiveType];
  if (!def) return null;
  const candidateIds = React.useMemo(() => swapCandidatesFor(template, slotId), [template, slotId]);
  const candidates = candidateIds.map(id => NODE_IDX[id]).filter(Boolean);
  const params = (slotParams[slotId]) || {};
  const onlyOne = candidates.length === 1;
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", padding: 10, background: "var(--surface-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.06em" }}>
          SLOT {slotId.toUpperCase()}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 10, color: "var(--muted)" }}>
          {candidates.length} compatible option{candidates.length === 1 ? "" : "s"}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <select
          className="select"
          style={{ flex: 1, fontFamily: "var(--font-mono)" }}
          value={def.id}
          disabled={onlyOne}
          onChange={(e) => onPickSlot(slotId, e.target.value)}>
          {candidates.map(c => (
            <option key={c.id} value={c.id}>
              {c.label}{c.id === tn.type ? "  · template default" : ""}  ({c.cost})
            </option>
          ))}
        </select>
        <InfoTip word={def.glossary || def.label} text={def.blurb} />
        {(def.id !== tn.type) && (
          <button type="button" className="btn sm ghost"
            onClick={() => onPickSlot(slotId, tn.type)}
            title="Revert this slot to the template's default node.">
            Revert
          </button>
        )}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6, lineHeight: 1.5 }}>
        {def.refs && def.refs.length ? <span style={{ color: "var(--dim)" }}>ref: </span> : null}
        {(def.refs || []).join(", ")}
      </div>
      {def.params && def.params.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 10 }}>
          {def.params.map(p => (
            <ParamField
              key={p.key}
              param={p}
              value={params[p.key] !== undefined ? params[p.key] : p.default}
              onChange={(v) => onParamChange(slotId, p.key, v)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// Per-param info bubbles — one short explanation per opaque key. Keyed by the
// raw param key (matches whatever's declared in PS_PIPELINE_NODE_TYPES).
// Anything not in this map renders without an InfoTip.
const PARAM_INFO = {
  checkpoint:      { word: "checkpoint", text: "Which pretrained weight file to load. Bigger checkpoints (e.g. esm2_t36_3B) are slower and more accurate. The 650M variant is the practical default." },
  freeze:          { word: "freeze",     text: "When on, the pretrained encoder's weights stay fixed during training and only the new layers learn. Cheaper; rarely hurts quality if your fine-tune set is small." },
  lora:            { word: "LoRA",       text: "Low-Rank Adaptation. Injects small trainable matrices into a frozen model so you can fine-tune billion-parameter encoders on one GPU." },
  rank:            { word: "LoRA",       text: "Capacity of the LoRA adapters. 8–32 is typical. Higher = more capacity but more memory." },
  alpha:           { word: "LoRA",       text: "LoRA scaling factor (effective learning rate multiplier for the adapter)." },
  blocks:          { text: "Number of transformer / triangle-update blocks. AlphaFold defaults to 48; cheaper baselines use 4–12." },
  heads:           { text: "Number of attention heads. 4–16 typical. More heads = finer-grained attention patterns; not always better." },
  layers:          { text: "Depth of the encoder. More layers see longer-range patterns but cost linearly more time and memory." },
  hidden:          { text: "Hidden dimension of the encoder. Most modern defaults sit at 128–512." },
  filters:         { text: "Number of CNN filters per layer. Bigger = more capacity, more compute." },
  kernel:          { text: "CNN kernel size. Larger kernels see longer-range sequence motifs at the cost of compute." },
  degrees:         { word: "SE(3)",      text: "Maximum spherical-harmonic degree the equivariant network uses. Degree 0 = scalar features; ≥1 = vector features that rotate with the input." },
  correlation:     { word: "MACE",       text: "Body order MACE uses per node update. 1 = pairs, 2 = triples, 3 = quads. Higher = more accurate but compute scales steeply." },
  scalar_dim:      { word: "GVP-GNN",    text: "Dimension of the scalar feature channel (energies, distances). Pairs with `vector_dim`." },
  vector_dim:      { word: "GVP-GNN",    text: "Dimension of the vector feature channel (forces, directions). Vector features rotate with the input — the source of GVP-GNN's equivariance." },
  depth:           { text: "Search depth for the MSA / retrieval / docking subroutine. More = stricter / slower." },
  tool:            { text: "Which alignment tool to call for MSA construction. hhblits = standard / slow; mmseqs2 = much faster; jackhmmer = sensitive but slow." },
  tokenizer:       { text: "How the SMILES string is split into tokens before being fed to the chem-LM. Tokenizer must match the checkpoint that produced the pretrained weights." },
  n_conformers:    { word: "conformer",  text: "How many 3D conformations to generate per molecule. 1 is the lowest-energy ETKDG; 10–20 is common for richer pretraining." },
  engine:          { text: "Which backend program to invoke. Each has its own dependencies and speed/accuracy trade-offs." },
  exhaustiveness:  { text: "How hard the docking engine searches. AutoDock Vina default is 8; higher = slower, more reproducible top pose." },
  radius_a:        { text: "Crop radius in Ångströms — keep atoms within this distance of the ligand and drop everything else. 8–12 Å is common." },
  include_water:   { text: "Whether to keep crystallographic water molecules in the cropped pocket. Off by default; flip on for known structural waters." },
  steps:           { text: "Number of denoising / diffusion steps. More = better quality, more compute." },
  sigma:           { text: "Diffusion noise scale. Affects exploration vs. exploitation in the sampler." },
  ph:              { text: "Solution pH used for protonation. 7.4 is physiological; some screens use 6.0 or 8.0." },
  index:           { text: "Vector-search index type. faiss_flat = exact, slow; faiss_ivf = approximate, fast; hnsw = approximate, fastest." },
  top_k:           { text: "How many candidates the retrieval step shortlists per query for the reranker to score." },
  strategy:        { text: "Active-learning acquisition strategy. Uncertainty = pick the least confident; diversity = pick the most novel; greedy = pick the highest-scoring." },
  method:          { text: "Algorithm used for the preprocessing step (charges, alignment, etc.)." },
  source:          { text: "Where the protein 3D structure comes from. pdb_or_af = PDB if present, AlphaFold otherwise. af2_only forces predicted structures even when PDB exists (useful when you need consistent confidence scores)." },
  max_len:         { text: "Maximum residues per sequence. Longer proteins get truncated." },
  max_atoms:       { text: "Maximum atoms per ligand. Larger ligands get dropped." },
  resolution:      { text: "Surface mesh sampling resolution in Ångströms. Lower = finer mesh." },
  kind:            { text: "Which sub-variant of the prediction to emit. For confidence heads: plDDT (per-residue 0–100), pTM (global 0–1), or ipTM (interface-only)." },
  loss:            { text: "Loss function the head is trained with." },
  target:          { text: "Target representation: pKi, pKd, pIC50, ΔG (kcal·mol⁻¹), or ΔG (kJ·mol⁻¹). Must match what the Dataset screen is producing." },
  temperature:     { text: "Softmax temperature for the contrastive loss. Lower = harder positives, sharper distinction." },
  n_keypoints:     { word: "keypoint matching", text: "How many anchor (protein-atom, ligand-atom) correspondences EquiBind solves for before rigid alignment. 4–16 typical." },
  n_targets:       { text: "How many targets in the selectivity panel. The head outputs one score per target." },
  weight:          { text: "Auxiliary loss weight relative to the main objective." },
  balancing:       { text: "How multi-task losses are balanced. Uncertainty weighting learns per-task variances (Kendall 2018); GradNorm balances gradient magnitudes." },
  tasks:           { text: "Comma-separated list of tasks the multi-task head aggregates." },
  dropout:         { text: "Dropout rate applied between MLP layers. Higher = stronger regularisation." },
  normalize:       { text: "L2-normalise the embedding before the dot product. Standard for contrastive / two-tower setups." },
};

function ParamField({ param, value, onChange }) {
  const label = param.key.replace(/_/g, " ");
  const info = PARAM_INFO[param.key];
  const tip = info ? <InfoTip word={info.word || param.key} text={info.text} /> : null;
  if (param.kind === "enum") {
    return (
      <div>
        <div className="label" style={{ display: "inline-flex", alignItems: "center" }}>
          {label}{tip}
        </div>
        <select className="select" value={value}
          style={{ fontFamily: "var(--font-mono)" }}
          onChange={(e) => onChange(e.target.value)}>
          {(param.options || []).map(o => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  if (param.kind === "bool") {
    return (
      <label style={{ display: "flex", alignItems: "center", gap: 8, paddingTop: 18 }}>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} />
        <span style={{ fontSize: 12, color: "var(--text-strong)" }}>{label}</span>
        {tip}
      </label>
    );
  }
  if (param.kind === "int" || param.kind === "float") {
    return (
      <div>
        <div className="label" style={{ display: "inline-flex", alignItems: "center" }}>
          {label}{tip}
        </div>
        <input
          type="number"
          className="input"
          style={{ fontFamily: "var(--font-mono)" }}
          value={value}
          step={param.kind === "int" ? 1 : 0.01}
          onChange={(e) => {
            const n = param.kind === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value);
            if (Number.isFinite(n)) onChange(n);
          }}
        />
      </div>
    );
  }
  // text fallback
  return (
    <div>
      <div className="label" style={{ display: "inline-flex", alignItems: "center" }}>
        {label}{tip}
      </div>
      <input
        type="text"
        className="input"
        value={value || ""}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

function CostBar() {
  const segs = [
    { c: "var(--molecular)", w: 30 },
    { c: "var(--primary)",   w: 55 },
    { c: "var(--signal)",    w: 15 },
  ];
  return (
    <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", background: "var(--surface-3)" }}>
      {segs.map((s, i) => <div key={i} style={{ width: s.w + "%", background: s.c }} />)}
    </div>
  );
}

window.ScreenPipeline = ScreenPipeline;
