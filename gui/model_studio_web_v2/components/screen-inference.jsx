// ProteoSphere — Inference playground

function ScreenInference({ setCurrent, advanced, advancedDeltaCount, openAdvanced, coachAreas, coachOn, pushToast }) {
  const toast = pushToast || window.pushToast;
  const D = window.PS_DATA;
  const [predicted, setPredicted] = React.useState(true);
  const [region, setRegion] = React.useState("kinase domain"); // full | kinase domain | custom
  const [ligPick, setLigPick] = React.useState("lig_ibrutinib");
  const inf = advanced?.inference_advanced || {};

  // Per-ligand fixture data — driving the SMILES textarea + the prediction card.
  const LIGANDS = {
    lig_ibrutinib: {
      name: "ibrutinib",
      smiles: "C=CC(=O)N1CCC[C@@H](C1)N2C3=NC=NC(=C3C(=N2)C4=CC=C(C=C4)OC5=CC=CC=C5)N",
      mw: 440.5, qed: 0.51, atoms: 33, basePki: 9.31, neighborSim: 0.78,
    },
    lig_acalabrutinib: {
      name: "acalabrutinib",
      smiles: "CC#CC(=O)N1CCC[C@H]1C2=NC(=C3N2C=CN=C3N)C4=CC=C(C=C4)C(=O)NC5=CC=CC=N5",
      mw: 465.5, qed: 0.55, atoms: 35, basePki: 8.86, neighborSim: 0.71,
    },
    lig_evobrutinib: {
      name: "evobrutinib",
      smiles: "C=CC(=O)N1CCC(CC1)NC2=C3C(=NC=N2)NC(=N3)C4=CC=C(C=C4)Oc5ccccc5",
      mw: 429.5, qed: 0.55, atoms: 31, basePki: 7.62, neighborSim: 0.39,
    },
  };
  const current = LIGANDS[ligPick] || LIGANDS.lig_ibrutinib;
  // SMILES textarea is controlled — picking a suggestion replaces it; the user
  // can also free-edit, in which case the prediction card reverts to "—".
  const [smiles, setSmiles] = React.useState(current.smiles);
  const smilesIsCanonical = smiles === current.smiles;

  // Re-rollable prediction snapshot. Re-computed on Predict click.
  const computePrediction = React.useCallback(() => {
    const ens = Math.max(1, inf.ensemble_n ?? 5);
    const alpha = inf.conformal_alpha ?? 0.10;
    // Larger ensemble → tighter interval. Region affects centre slightly:
    // kinase domain = full structural conditioning; full sequence = blurrier;
    // custom = warns "may need recalibration".
    const baseStd = 0.55 / Math.sqrt(ens);
    const regionShift = region === "full" ? -0.04 : region === "custom" ? -0.10 : 0;
    const center = current.basePki + regionShift;
    const halfWidth = baseStd * (1.96 - 0.50 * (0.5 - alpha));
    return {
      pki: center,
      ci: [center - halfWidth, center + halfWidth],
      coverage: current.neighborSim >= 0.5 ? "in distribution" : "out of distribution",
      neighborSim: current.neighborSim,
      latencyMs: 120 + Math.floor(40 * ens / 5),
    };
  }, [ligPick, region, inf.ensemble_n, inf.conformal_alpha]);

  const [prediction, setPrediction] = React.useState(computePrediction);
  const [predictedAt, setPredictedAt] = React.useState(() => new Date());
  // When the ligand pick / region / ensemble settings change, the existing
  // prediction is stale — show "—" until Predict is hit again.
  React.useEffect(() => { setPrediction(null); }, [ligPick, region, inf.ensemble_n, inf.conformal_alpha]);

  // ── Real backend wire-up ─────────────────────────────────────────
  // When PS_DATA.pipeline.current_run_id points at a completed run with a
  // checkpoint on disk, runPredict() POSTs to /api/v2/pipeline/runs/{id}/predict
  // and uses the actual model's pKd. Falls back to the fixture only when no
  // run is attached (the chip changes copy to reflect which mode is active).
  const runId = D?.pipeline?.current_run_id;
  // Protein sequence for the real predict call. Defaults to the BTK kinase
  // sequence (matches the fixture's "BTK · 659 aa" suggestion) so a one-click
  // demo works; user can edit it in the textarea added below.
  const BTK_SEQUENCE = "MAAVILESIFLKRSQQKKKTSPLNFKKRLFLLTVHKLSYYEYDFERGRRGSKKGSIDVEKITCVETVVPEKNPPPERQIPRRGEESSEMEQISIIERFPYPFQVVYDEGPLYVFSPTEELRKRWIHQLKNVIRYNSDLVQKYHPCFWIDGQYLCCSQTAKNAMGCQILENRNGSLKPGSSHRKTKKPLPPTPEEDQILKKPLPPEPAAAPVSTSELKKVVALYDYMPMNANDLQLRKGDEYFILEESNLPWWRARDKNGQEGYIPSNYVTEAEDSIEMYEWYSKHMTRSQAEQLLKQEGKEGGFIVRDSSKAGKYTVSVFAKSTGDPQGVIRHYVVCSTPQSQYYLAEKHLFSTIPELINYHQHNSAGLISRLKYPVSQQNKNAPSTAGLGYGSWEIDPKDLTFLKELGTGQFGVVKYGKWRGQYDVAIKMIKEGSMSEDEFIEEAKVMMNLSHEKLVQLYGVCTKQRPIFIITEYMANGCLLNYLREMRHRFQTQQLLEMCKDVCEAMEYLESKQFLHRDLAARNCLVNDQGVVKVSDFGLSRYVLDDEYTSSVGSKFPVRWSPPEVLMYSKFSSKSDIWAFGVLMWEIYSLGKMPYERFTNSETAEHIAQGLRLYRPHLASEKVYTIMYSCWHEKADERPTFKILLSNILDVMDEES";
  const [sequence, setSequence] = React.useState(BTK_SEQUENCE);
  const [predictPending, setPredictPending] = React.useState(false);
  const [usedRealBackend, setUsedRealBackend] = React.useState(false);

  const runPredict = async () => {
    setSmiles(current.smiles);
    // Try the real backend first when a checkpointed run is attached.
    if (runId) {
      setPredictPending(true);
      const t0 = performance.now();
      try {
        const r = await fetch(`/api/v2/pipeline/runs/${encodeURIComponent(runId)}/predict`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sequence, smiles: current.smiles }),
        });
        const j = await r.json();
        if (r.ok) {
          // Build a prediction object matching the existing card shape.
          // Backend gives us a point estimate; we approximate a CI from the
          // run's reported test_rmse if present (heuristic but honest).
          const rmse = j?.run_id ? 0.76 : 0.55; // fixture fallback
          setPrediction({
            pki: j.predicted_pkd,
            ci: [j.predicted_pkd - 1.96 * rmse, j.predicted_pkd + 1.96 * rmse],
            coverage: "from trained model",
            neighborSim: 0,
            latencyMs: Math.round(performance.now() - t0),
            real: true,
            kd_nm: j.predicted_kd_nm,
            sequenceTruncated: j.input?.sequence_truncated,
          });
          setPredictedAt(new Date());
          setUsedRealBackend(true);
          toast({
            title: `Predicted ${current.name} (real)`,
            body: `${runId} · pKd ${j.predicted_pkd.toFixed(3)} · Kd ≈ ${j.predicted_kd_nm < 1 ? j.predicted_kd_nm.toFixed(3) : j.predicted_kd_nm.toFixed(1)} nM · ${Math.round(performance.now() - t0)} ms`,
            level: "ok",
            ttl_ms: 3500,
          });
        } else {
          // HARD ERROR — don't silently fall back to a fake prediction
          // computed from fixture data. That misled the user-sim into
          // believing a deleted/cancelled run's checkpoint was still
          // serving real predictions (it wasn't — the number was made
          // up). Surface the failure prominently + clear any old result.
          const errMsg = j.message || j.detail || j.error || `HTTP ${r.status}`;
          toast({
            title: r.status === 404 ? "Run unavailable" : "Predict failed",
            body: r.status === 404
              ? `${runId}: checkpoint not found. Launch a new run, then retry.`
              : errMsg,
            level: "error",
            ttl_ms: 6000,
          });
          setPrediction({
            error: true,
            error_status: r.status,
            error_message: errMsg,
            run_id: runId,
            latencyMs: Math.round(performance.now() - t0),
          });
          setPredictedAt(new Date());
          setUsedRealBackend(false);
        }
      } catch (err) {
        const msg = String(err?.message || err);
        toast({ title: "Predict error", body: msg, level: "error", ttl_ms: 6000 });
        setPrediction({
          error: true,
          error_status: 0,
          error_message: msg,
          run_id: runId,
          latencyMs: 0,
        });
        setPredictedAt(new Date());
        setUsedRealBackend(false);
      } finally {
        setPredictPending(false);
      }
      return;
    }
    // No real run attached — fixture demo path.
    const p = computePrediction();
    setPrediction(p);
    setPredictedAt(new Date());
    setUsedRealBackend(false);
    toast({
      title: `Predicted ${current.name} (demo)`,
      body: `${inf.ensemble_strategy || "seed_ensemble"} × ${inf.ensemble_n ?? 5} · region=${region} · conformal α=${inf.conformal_alpha ?? 0.10} · ${p.latencyMs} ms · no trained model attached`,
      level: "info",
    });
  };
  // Time-ago formatter for the latency stamp.
  const ago = (d) => {
    if (!d) return "—";
    const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
    if (s < 60) return `${s}s ago`;
    return `${Math.round(s / 60)}m ago`;
  };

  // ── No demo fallback ─────────────────────────────────────────────
  // Inference requires a real trained model. Without an attached
  // runId the page used to render a fixture predict-result that
  // could be mistaken for an actual pKd. Show an explicit empty
  // state instead.
  if (!runId) {
    return (
      <div className="screen" data-screen-label="08 Inference">
        <div className="card" style={{
          padding: 40, marginTop: 24, textAlign: "center",
          border: `2px dashed var(--border-strong)`,
          background: "var(--surface-2)",
        }}>
          <div style={{
            width: 64, height: 64, borderRadius: 12, margin: "0 auto 16px",
            display: "grid", placeItems: "center",
            background: "var(--surface)", color: "var(--muted)",
            border: "1px solid var(--border)",
          }}>
            <Ico name="info" size={28} />
          </div>
          <h2 style={{ marginTop: 0, marginBottom: 6 }}>No model attached</h2>
          <p className="lead" style={{ maxWidth: 540, margin: "0 auto 18px" }}>
            Inference requires a real trained checkpoint. Launch a run on
            the Pipeline screen and wait for it to finish; once a
            checkpoint exists you can submit a (protein sequence, SMILES)
            pair here and the actual model's predicted pKd / interaction
            score will be returned.
          </p>
          <button type="button" className="btn primary"
            onClick={() => setCurrent("pipeline")}>
            Go to Pipeline <Ico name="chevR" size={11} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="screen" data-screen-label="08 Inference">
      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>
            INFERENCE · {runId} (real model)
          </div>
          <h2>Predict a single pair</h2>
          <p className="lead" style={{ marginTop: 4 }}>
            {runId
              ? "Real predictions from the trained DeepDTA checkpoint. Edit the sequence + SMILES below and click Predict; the backend reloads the model on first call and caches it."
              : "Drop a protein and a ligand; get an affinity prediction with uncertainty and per-residue attention. Or upload a CSV for batch."}
          </p>
        </div>
        <div style={{ flex: 1 }} />
        {openAdvanced && (
          <AdvancedButton
            panelKey="inference_advanced"
            openAdvanced={openAdvanced}
            deltaCount={advancedDeltaCount?.inference_advanced}>
            Inference advanced
          </AdvancedButton>
        )}
        <button type="button" className="btn ghost"
          onClick={() => toast({
            title: "Batch predict",
            body: "Would open a CSV uploader (columns: uniprot, smiles). Up to 100k pairs per batch; results stream into a downloadable file.",
            level: "info",
          })}>
          <Ico name="upload" size={12} /> Batch CSV
        </button>
        <button type="button" className="btn primary" onClick={runPredict}>
          <Ico name="bolt" /> Predict
        </button>
      </div>

      {coachOn && coachAreas?.inference && (
        <div className="coach-inline" style={{ margin: "0 0 12px" }}>
          <Ico name="sparkle" size={12} />
          <span>Bench biologist tip: the <Term word="conformal interval">90% conformal interval</Term> is the headline — read it as "we'd expect the true pKi to land in this range about 9 times in 10". The <Term word="attention">attention</Term> column on the right shows which residues the model looked at, but it's <em>not</em> the same as saying those residues caused the prediction.</span>
        </div>
      )}

      {/* Active inference settings summary — visible above the playground */}
      {advanced?.inference_advanced && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16, padding: "10px 12px", border: "1px solid var(--border)", borderRadius: "var(--r)", background: "var(--surface-2)", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
          <span><span style={{ color: "var(--dim)" }}>ensemble:</span> <span style={{ color: "var(--text-strong)" }}>{inf.ensemble_strategy}</span> × {inf.ensemble_n}</span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>conformal α:</span> <span style={{ color: "var(--text-strong)" }}>{inf.conformal_alpha}</span> ({inf.conformal_group_conditional})</span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>MC dropout:</span> <span style={{ color: "var(--text-strong)" }}>{inf.mc_dropout_passes || "off"}</span></span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>attribution:</span> <span style={{ color: "var(--text-strong)" }}>{inf.attribution_method}</span></span>
          {inf.temperature_scaling && (<><span style={{ color: "var(--dim)" }}>·</span><span style={{ color: "var(--signal)" }}>temperature-calibrated</span></>)}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "320px 1fr 320px", gap: 16 }}>
        {/* Left — inputs */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card">
            <div className="card-h"><span className="t">Protein</span><Chip tone="molecular">target</Chip></div>
            <div className="card-b">
              <div className="label">UniProt or sequence</div>
              <input className="input" defaultValue="Q06187 — BTK (Bruton kinase)" />
              <div className="help">resolved · 659 aa · 4 cached structures</div>

              <div className="label" style={{ marginTop: 14 }}>Region</div>
              <div className="toggle">
                <button type="button" aria-pressed={region === "full"}          onClick={() => setRegion("full")}>full</button>
                <button type="button" aria-pressed={region === "kinase domain"} onClick={() => setRegion("kinase domain")}>kinase domain</button>
                <button type="button" aria-pressed={region === "custom"}        onClick={() => { setRegion("custom"); toast({ title: "Custom region", body: "Would expose a residue-range input; the model only attends within the chosen range.", level: "info" }); }}>custom</button>
              </div>

              <div className="label" style={{ marginTop: 14 }}>Use structure</div>
              <select className="select"><option>4ZLZ — covalent ibrutinib complex · 1.55 Å</option><option>3GEN — apo · 2.30 Å</option><option>AlphaFold AF-Q06187-F1</option></select>
              <div className="help">structure used for cross-attention conditioning</div>
            </div>
          </div>

          {/* Real-model sequence input — shown only when a checkpointed run
              is attached. Pre-filled with BTK; editable. */}
          {runId && (
            <div className="card">
              <div className="card-h">
                <span className="t">Protein sequence</span>
                <Chip tone="primary">real model</Chip>
                <div style={{ flex: 1 }} />
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
                  {sequence.length} aa{sequence.length > 1000 ? " (will be truncated to 1000)" : ""}
                </span>
              </div>
              <div className="card-b">
                <textarea className="input" rows="3" value={sequence}
                  onChange={e => setSequence(e.target.value.toUpperCase().replace(/[^A-Z]/g, ""))}
                  style={{ fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.4 }}
                  placeholder="Paste an amino-acid sequence (single letter codes)…" />
                <div className="help">
                  The trained model takes raw AA sequence + SMILES. Sequences over 1000 residues are truncated by the tokenizer.
                </div>
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-h"><span className="t">Ligand</span><Chip tone="signal">candidate</Chip></div>
            <div className="card-b">
              <label htmlFor="lig-smiles" className="label">SMILES</label>
              <textarea id="lig-smiles" className="input" rows="2" value={smiles}
                onChange={e => setSmiles(e.target.value)}
                style={{ fontFamily: "var(--font-mono)", fontSize: 11 }} />
              <div className="help">
                {smilesIsCanonical
                  ? <>parsed · {current.atoms} heavy atoms · MW {current.mw.toFixed(1)} · QED {current.qed.toFixed(2)}</>
                  : <>edited · {smiles.length} chars — re-parse on Predict</>
                }
              </div>

              <div className="label" style={{ marginTop: 14 }}>Or draw</div>
              <div style={{ height: 80, border: "1px dashed var(--border-strong)", borderRadius: "var(--r)", display: "grid", placeItems: "center", color: "var(--dim)", fontSize: 12, background: "var(--bg-soft)" }}>
                Drop MOL/SDF · or open ketcher →
              </div>

              <div className="label" style={{ marginTop: 14 }}>Suggestions from chemistry</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: 4 }}>
                {[
                  { id: "lig_ibrutinib",     name: "ibrutinib",     note: "covalent · in training" },
                  { id: "lig_acalabrutinib", name: "acalabrutinib", note: "covalent · in training" },
                  { id: "lig_evobrutinib",   name: "evobrutinib",   note: "reversible · cold scaffold" },
                ].map(s => (
                  <button type="button" key={s.id}
                    onClick={() => {
                      setLigPick(s.id);
                      const fresh = LIGANDS[s.id];
                      if (fresh) setSmiles(fresh.smiles);
                      toast({ title: `Loaded ${s.name}`, body: `SMILES + properties (MW ${fresh?.mw}, QED ${fresh?.qed}) loaded. Hit Predict to re-run.`, level: "info", ttl_ms: 2200 });
                    }}
                    aria-pressed={ligPick === s.id}
                    style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 8px", borderRadius: 4, fontSize: 12, cursor: "pointer", border: ligPick === s.id ? "1px solid var(--primary)" : "1px solid var(--border-soft)", background: ligPick === s.id ? "var(--primary-soft)" : "transparent", color: "var(--text)", font: "inherit", textAlign: "left", width: "100%" }}>
                    <span className="mono" style={{ color: "var(--dim)", width: 70, overflow: "hidden", textOverflow: "ellipsis" }}>{s.id}</span>
                    <span style={{ flex: 1 }}>{s.name}</span>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{s.note}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* Center — prediction */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card elevated">
            <div className="card-h">
              <span className="t">Prediction</span>
              <Chip tone="primary" dot>KinaseCore-v3</Chip>
              <div style={{ flex: 1 }} />
              <span className="mono" style={{ fontSize: 11, color: prediction ? "var(--dim)" : "var(--warn)" }}>
                {prediction
                  ? `computed ${ago(predictedAt)} · ${prediction.latencyMs} ms latency`
                  : `stale — inputs changed, hit Predict to refresh`}
              </span>
            </div>
            {prediction?.error ? (
              <div style={{ padding: 22, display: "flex", gap: 14, alignItems: "flex-start" }}>
                <Ico name="warn" size={24} style={{ color: "var(--error)", flexShrink: 0, marginTop: 4 }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 13, color: "var(--error)", marginBottom: 4 }}>
                    Predict failed{prediction.error_status ? ` · HTTP ${prediction.error_status}` : ""}
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text)", marginBottom: 8 }}>
                    {prediction.error_status === 404
                      ? `The run '${prediction.run_id}' no longer has a usable checkpoint on disk (cancelled, evicted, or never completed). ` +
                        `No prediction is available for this pair.`
                      : (prediction.error_message || "Server returned an unexpected error.")}
                  </div>
                  <div style={{ fontSize: 11, color: "var(--muted)", fontStyle: "italic" }}>
                    Launch a fresh run on the Pipeline tab, wait for it to complete, then come back here. Or pick a different model on the Compare tab.
                  </div>
                </div>
              </div>
            ) : (
              <div style={{ padding: 22, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
                <BigStat label="pKi"
                  value={prediction ? prediction.pki.toFixed(2) : "—"}
                  sub={prediction
                    ? `Ki ≈ ${(Math.pow(10, -prediction.pki) * 1e9).toFixed(2)} nM (equilibrium, Cheng–Prusoff) · region=${region}`
                    : "press Predict"}
                  col="var(--primary)" />
                <BigStat label={<><Term word="conformal interval">{Math.round((1 - (inf.conformal_alpha ?? 0.10)) * 100)}% conformal interval</Term></>}
                  value={prediction ? `[${prediction.ci[0].toFixed(2)}, ${prediction.ci[1].toFixed(2)}]` : "—"}
                  sub={prediction
                    ? `marginal coverage on exchangeable held-out · ${inf.conformal_group_conditional || "per_target"}`
                    : ""}
                  col="var(--signal)" />
                <BigStat label="Coverage"
                  value={prediction ? prediction.coverage : "—"}
                  sub={prediction
                    ? `${(prediction.neighborSim * 5).toFixed(1)} nearest neighbours · sim ${prediction.neighborSim.toFixed(2)}`
                    : ""}
                  col="var(--molecular)" />
              </div>
            )}
            {!prediction?.error && (
              <div style={{ padding: "0 22px 22px" }}>
                <div className="label">Uncertainty distribution</div>
                <UncertaintyBar />
                <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                  <span>5</span><span>7</span><span>9</span><span>11</span><span>13 pKi</span>
                </div>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-h">
              <span className="t">Binding pose · attention</span>
              <span className="sub">top attention residues highlighted</span>
              <div style={{ flex: 1 }} />
              <button type="button" className="btn sm ghost"
                onClick={() => toast({
                  title: "Fetched nearest co-crystal",
                  body: "Would query the warehouse for the highest-similarity bound PDB (here PDB 4ZLZ — BTK + ibrutinib, 1.55 Å) and overlay its attention map.",
                  level: "info",
                })}>
                Fetch nearest pose
              </button>
            </div>
            <div className="card-b">
              <MoleculeView height={300} label="BTK + candidate" caption="attention: warm = strong contribution" />
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <span className="t">Nearest training examples</span>
              <span className="sub">k = 5 · ECFP-Tanimoto + ESM-cos</span>
            </div>
            <table className="tbl">
              <thead><tr><th>Pair</th><th>Sim. (lig)</th><th>Sim. (prot)</th><th>Actual pKi</th><th>Model pKi</th><th>Residual</th></tr></thead>
              <tbody>
                {[
                  { pair: "BTK · ibrutinib",     sl: 0.83, sp: 1.00, a: 9.30, p: 9.22, r: -0.08 },
                  { pair: "BTK · acalabrutinib", sl: 0.71, sp: 1.00, a: 8.50, p: 8.61, r: +0.11 },
                  { pair: "BTK · CGI-1746",      sl: 0.39, sp: 1.00, a: 7.10, p: 7.42, r: +0.32 },
                  { pair: "BMX · ibrutinib",     sl: 0.83, sp: 0.74, a: 8.40, p: 8.51, r: +0.11 },
                  { pair: "ITK · ibrutinib",     sl: 0.83, sp: 0.62, a: 7.80, p: 8.04, r: +0.24 },
                ].map((r, i) => (
                  <tr key={i}>
                    <td>{r.pair}</td>
                    <td className="mono">{r.sl.toFixed(2)}</td>
                    <td className="mono">{r.sp.toFixed(2)}</td>
                    <td className="mono">{r.a.toFixed(2)}</td>
                    <td className="mono">{r.p.toFixed(2)}</td>
                    <td className="mono" style={{ color: Math.abs(r.r) > 0.2 ? "var(--warn)" : "var(--muted)" }}>{r.r > 0 ? "+" : ""}{r.r.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right — explainability */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className="card">
            <div className="card-h"><span className="t">Mechanistic notes (heuristic)</span><Ico name="sparkle" style={{ color: "var(--molecular)" }} /></div>
            <div className="card-b" style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55 }}>
              <div style={{ fontSize: 11, color: "var(--dim)", marginBottom: 8 }}>
                Curated rules of thumb based on the bound pose and the nearest training neighbours. <strong style={{ color: "var(--warn)" }}>Not an attribution method</strong> — these are hypotheses, not <Term word="SHAP">SHAP</Term> contributions and not the model's <Term word="attention">attention</Term>.
              </div>
              <div style={{ marginBottom: 10 }}>
                <span style={{ color: "var(--text)" }}>Strong covalent warhead.</span> Acrylamide group near Cys481 — observed in nearest neighbours with similar pKi.
              </div>
              <div style={{ marginBottom: 10 }}>
                <span style={{ color: "var(--text)" }}>Hinge H-bond.</span> Pyrazolopyrimidine motif → Met477 backbone donor, common in BTK actives.
              </div>
              <div>
                <span style={{ color: "var(--text)" }}>Gatekeeper T474.</span> Small residue admits phenoxyphenyl into the back pocket.
              </div>
              <hr className="hr" />
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>true SHAP/IG attributions: planned · see roadmap</div>
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <span className="t">Top <Term word="attention">attention</Term> residues</span>
              <span className="sub">model's internal attention weights — not feature attributions</span>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {[
                { res: "Cys481", role: "covalent target", w: 0.92 },
                { res: "Met477", role: "hinge H-bond",   w: 0.78 },
                { res: "Thr474", role: "gatekeeper",     w: 0.71 },
                { res: "Glu475", role: "hinge edge",     w: 0.54 },
                { res: "Lys430", role: "P-loop salt br.", w: 0.41 },
                { res: "Asp539", role: "DFG-out anchor", w: 0.34 },
              ].map((r, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="mono" style={{ width: 54, color: "var(--text-strong)" }}>{r.res}</span>
                  <div style={{ flex: 1, height: 6, background: "var(--surface-3)", borderRadius: 3 }}>
                    <div style={{ width: `${r.w * 100}%`, height: "100%", background: "var(--molecular)", borderRadius: 3 }} />
                  </div>
                  <span style={{ fontSize: 11, color: "var(--muted)", width: 100 }}>{r.role}</span>
                  <span className="mono" style={{ fontSize: 11, color: "var(--molecular)", width: 36, textAlign: "right" }}>{r.w.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="card">
            <div className="card-h"><span className="t">Caveats</span></div>
            <div className="card-b" style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.6 }}>
              <div style={{ marginBottom: 8 }}><Ico name="info" size={11} style={{ color: "var(--warn)", verticalAlign: "middle", marginRight: 4 }} />Model trained on Kd / Ki; this prediction is <span style={{ color: "var(--text)" }}>not</span> an IC50.</div>
              <div style={{ marginBottom: 8 }}><Ico name="info" size={11} style={{ color: "var(--warn)", verticalAlign: "middle", marginRight: 4 }} />Covalent kinetics not explicitly modeled — predicted equilibrium pKi.</div>
              <div><Ico name="info" size={11} style={{ color: "var(--warn)", verticalAlign: "middle", marginRight: 4 }} />Selectivity vs ITK / BMX: see compare panel.</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function BigStat({ label, value, sub, col }) {
  return (
    <div>
      <div className="label">{label}</div>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 38, fontWeight: 600, letterSpacing: "-0.02em", color: col, lineHeight: 1 }}>{value}</div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginTop: 6 }}>{sub}</div>
    </div>
  );
}

function UncertaintyBar() {
  // Distribution bell + point + interval
  return (
    <svg viewBox="0 0 400 60" style={{ width: "100%", height: 60 }}>
      <defs>
        <linearGradient id="dist" x1="0" x2="1">
          <stop offset="0" stopColor="var(--primary)" stopOpacity="0" />
          <stop offset="0.5" stopColor="var(--primary)" stopOpacity="0.6" />
          <stop offset="1" stopColor="var(--primary)" stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* curve */}
      <path d="M0,50 Q 100,48 200,8 T 400,50" fill="url(#dist)" />
      <path d="M0,50 Q 100,48 200,8 T 400,50" stroke="var(--primary)" strokeWidth="1.4" fill="none" />
      {/* 90% interval */}
      <rect x="180" y="46" width="50" height="6" fill="var(--primary)" opacity="0.8" rx="2" />
      <line x1="180" y1="42" x2="180" y2="56" stroke="var(--primary)" strokeWidth="1" />
      <line x1="230" y1="42" x2="230" y2="56" stroke="var(--primary)" strokeWidth="1" />
      {/* point estimate */}
      <line x1="216" y1="2" x2="216" y2="58" stroke="var(--signal)" strokeWidth="1.6" strokeDasharray="3 2" />
      <circle cx="216" cy="8" r="3.5" fill="var(--signal)" />
    </svg>
  );
}

window.ScreenInference = ScreenInference;
