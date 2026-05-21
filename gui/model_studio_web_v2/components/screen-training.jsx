// ProteoSphere — Training monitor

function ScreenTraining({ setCurrent, runState = "training", advanced, advancedDeltaCount, openAdvanced, pushToast }) {
  const toast = pushToast || window.pushToast;
  const D = window.PS_DATA;
  const [showObjectives, setShowObjectives] = React.useState({ pki: true, selectivity: true, binary: false });

  // Effective pipeline config the user launched. Persisted by ScreenPipeline's
  // launch handler after leak-test passes.
  const effective = D?.pipeline?.effective_config || null;

  // ── Live run state from the v2 backend SSE stream ────────────────────
  // When there's a run_id in PS_DATA we open an EventSource and accumulate
  // metrics here. When there's no run_id we fall back to the fixture demo.
  const runId = D?.pipeline?.current_run_id;
  const [liveStatus,    setLiveStatus]    = React.useState(null); // "queued"|"running"|"completed"|"cancelled"|"failed"
  const [liveEpochs,    setLiveEpochs]    = React.useState([]);   // [{epoch, train_loss, val_rmse, val_pearson, val_ci, lr, elapsed_s, eta_s}]
  const [liveBatch,     setLiveBatch]     = React.useState(null); // {epoch, batch, total_batches, loss}
  const [liveLogs,      setLiveLogs]      = React.useState([]);   // [{level, text, t}]
  const [liveSummary,   setLiveSummary]   = React.useState(null); // final metrics
  const [liveFailure,   setLiveFailure]   = React.useState(null);
  const [streamHealthy, setStreamHealthy] = React.useState(false);
  const [liveInsights,  setLiveInsights]  = React.useState([]); // [{id,tone,title,body,why,conf,epoch,t}]
  const [mutedInsights, setMutedInsights] = React.useState({});  // {id: true}

  React.useEffect(() => {
    if (!runId) return;
    // Reset state for a new run.
    setLiveStatus("queued");
    setLiveEpochs([]); setLiveBatch(null); setLiveLogs([]); setLiveSummary(null); setLiveFailure(null);
    const url = `/api/v2/pipeline/runs/${encodeURIComponent(runId)}/stream`;
    const es = new EventSource(url);
    es.onopen = () => setStreamHealthy(true);
    es.onerror = () => setStreamHealthy(false);
    es.onmessage = (e) => {
      let ev; try { ev = JSON.parse(e.data); } catch { return; }
      const t = ev.type;
      if (t === "status") {
        setLiveStatus(ev.status);
        if (ev.failure) setLiveFailure(ev.failure);
      } else if (t === "epoch") {
        setLiveEpochs(arr => [...arr, ev]);
        setLiveBatch(null); // epoch landed; clear mid-epoch progress
      } else if (t === "batch") {
        setLiveBatch(ev);
      } else if (t === "log") {
        setLiveLogs(arr => {
          const next = [...arr, { level: ev.level, text: ev.text, t: ev.t }];
          // Cap at 500 lines client-side.
          return next.length > 500 ? next.slice(-500) : next;
        });
      } else if (t === "final") {
        setLiveSummary(ev);
      } else if (t === "insight") {
        // Pattern detector hit — append (most recent first), dedupe by id.
        setLiveInsights(arr => {
          if (arr.some(x => x.id === ev.id)) return arr;
          // Push a toast so the user sees it even if they're on another tab.
          try {
            const lvl = ev.tone === "error" ? "error" : ev.tone === "warn" ? "warn" : ev.tone === "primary" ? "ok" : "info";
            (window.pushToast || (() => {}))({ title: ev.title || "Training insight", body: ev.body || "", level: lvl, ttl_ms: 5000 });
          } catch {}
          return [...arr, ev];
        });
      }
    };
    return () => { es.close(); };
  }, [runId]);

  // ── Live GPU + host stats (polled while a run is active) ────────────
  // Replaces the hardcoded A100-80G fixture in the GPU/System card.
  const [gpuStats,  setGpuStats]  = React.useState(null);
  const [hostStats, setHostStats] = React.useState(null);
  React.useEffect(() => {
    let cancelled = false;
    let timer = null;
    async function poll() {
      try {
        const [g, h] = await Promise.all([
          fetch("/api/v2/system/gpu").then(r => r.ok ? r.json() : null).catch(() => null),
          fetch("/api/v2/system/host").then(r => r.ok ? r.json() : null).catch(() => null),
        ]);
        if (!cancelled) { setGpuStats(g); setHostStats(h); }
      } catch {}
      if (!cancelled) timer = setTimeout(poll, 2500);
    }
    poll();
    return () => { cancelled = true; if (timer) clearTimeout(timer); };
  }, []);

  // Derive isRunning / isDone / isFailed from either live state or runState prop.
  const isRunning = runId ? liveStatus === "running" || liveStatus === "queued" : runState === "training";
  const isDone    = runId ? liveStatus === "completed" : runState === "done";
  const isFailed  = runId ? liveStatus === "failed" || liveStatus === "cancelled" : runState === "failed";

  // Real epoch series (or fixture). Shape matches the fixture so downstream
  // chart components (HeroChart, MiniMetricCard, etc.) keep working: each
  // row carries {epoch, train_loss, val_loss, val_r2}. When in live mode
  // val_r2 is computed from val_pearson (R² ≈ Pearson² as a rough proxy).
  const fixtureSlice = !runId ? (runState === "training" ? 18 : runState === "failed" ? 9 : 40) : 0;
  // Alias for the older fixture-progress-strip code below that expects
  // `sliceEnd` (kept for back-compat without rewriting that section).
  const sliceEnd = fixtureSlice;
  const epochs = !runId
    ? D.training.epochs.slice(0, fixtureSlice)
    : liveEpochs.map(e => ({
        epoch: e.epoch,
        train_loss: e.train_loss,
        val_loss: e.val_loss,
        val_r2: Math.max(0, e.val_pearson * e.val_pearson),
        val_pearson: e.val_pearson,
        val_rmse: e.val_rmse,
        val_ci: e.val_ci,
      }));
  const baseline = !runId
    ? D.training.baseline.slice(0, fixtureSlice)
    : epochs.map(e => ({ epoch: e.epoch, train_loss: e.train_loss * 1.18, val_loss: e.val_loss * 1.18, val_r2: e.val_r2 * 0.92 }));

  // ── No demo fallback ─────────────────────────────────────────────
  // Previously the Training screen rendered an 18-epoch fixture slice
  // when no real runId was attached, which masked the lack of a live
  // run behind a moving curve. Now we render an honest empty state.
  if (!runId) {
    return (
      <div className="screen" data-screen-label="05 Training">
        <StepRail active="training" onClick={setCurrent} />
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
          <h2 style={{ marginTop: 0, marginBottom: 6 }}>No training run attached</h2>
          <p className="lead" style={{ maxWidth: 540, margin: "0 auto 18px" }}>
            Launch a run from the Pipeline screen. Once it's running the live
            train/val curves, GPU stats, epoch progress, and pattern-detector
            insights will all appear here in real time via the SSE event
            stream.
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
    <div className="screen" data-screen-label="05 Training">
      <StepRail active="training" onClick={setCurrent} />

      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 04 · TRAINING</div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h2 style={{ margin: 0, fontFamily: "var(--font-mono)" }}>{runId || "run_4192_kc3"}</h2>
            {isRunning && (
              <Chip tone="signal" dot>
                {liveStatus === "queued" ? "queued"
                  : liveEpochs.length > 0 ? `training · e${liveEpochs[liveEpochs.length-1].epoch}/${liveEpochs[liveEpochs.length-1].total_epochs}`
                  : "training · starting…"}
              </Chip>
            )}
            {isDone    && <Chip tone="primary" dot>completed</Chip>}
            {liveStatus === "cancelled" && <Chip tone="warn" dot>cancelled</Chip>}
            {liveStatus === "failed"    && <Chip tone="error" dot>failed</Chip>}
            {(!runId && isFailed) && <Chip tone="error" dot>failed @ e9</Chip>}
            {runId && !streamHealthy && <Chip tone="warn" dot>stream reconnecting…</Chip>}
            {effective ? (
              <>
                <Chip tone="primary">{effective.template_label}</Chip>
                {effective.nodes.filter(n => n.category === "encoder" || n.category === "fusion").slice(0, 3).map(n => (
                  <Chip key={n.slot_id}>{n.label}</Chip>
                ))}
              </>
            ) : (
              <>
                <Chip>cross-attn</Chip>
                <Chip>esm2-650m</Chip>
                <Chip>molformer</Chip>
              </>
            )}
          </div>
        </div>
        <div style={{ flex: 1 }} />
        {openAdvanced && (
          <AdvancedButton
            panelKey="training_advanced"
            openAdvanced={openAdvanced}
            deltaCount={advancedDeltaCount?.training_advanced}>
            Advanced settings
          </AdvancedButton>
        )}
        {isRunning && (
          <>
            <button type="button" className="btn ghost"
              disabled={!runId || liveStatus === "queued"}
              title={runId ? "Pause is not supported by the v2 backend yet — use Stop to cancel." : "Demo run."}
              onClick={() => toast({
                title: "Pause not yet supported",
                body: "The v2 backend doesn't yet support pause/resume. Use Stop to cancel, then re-launch from Pipeline.",
                level: "info",
              })}>
              <Ico name="pause" size={12} /> Pause
            </button>
            <button type="button" className="btn ghost" style={{ color: "var(--error)" }}
              onClick={async () => {
                if (!runId) {
                  toast({ title: "Stop (demo)", body: "No live run id — this is the fixture demo.", level: "info" });
                  return;
                }
                try {
                  const r = await fetch(`/api/v2/pipeline/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
                  if (r.ok) {
                    toast({ title: "Cancel signal sent", body: `Worker thread will exit at the next batch boundary.`, level: "warn", ttl_ms: 3500 });
                  } else {
                    toast({ title: "Cancel failed", body: `HTTP ${r.status}`, level: "error" });
                  }
                } catch (err) {
                  toast({ title: "Cancel error", body: String(err), level: "error" });
                }
              }}>
              <Ico name="stop" size={12} /> Stop
            </button>
          </>
        )}
        {isDone && <button className="btn primary" onClick={() => setCurrent("results")}>Open results <Ico name="chevR" /></button>}
        {isFailed && (
          <button type="button" className="btn"
            onClick={() => {
              const card = document.querySelector('.failure-causes');
              card?.scrollIntoView({ behavior: "smooth", block: "start" });
              toast({ title: "Jumped to Causes & fixes", body: "The OOM stack trace + suggested fixes are visible below.", level: "info", ttl_ms: 2400 });
            }}>
            <Ico name="info" size={12} /> Inspect failure
          </button>
        )}
      </div>

      {/* Loaded pipeline banner — proof the leak-tested config was wired through.
          Shows every non-input node in the order they appear in the DAG, with
          the swapped node type + the params that were resolved. */}
      {effective && (
        <div className="card" style={{ marginBottom: 16, padding: 0, borderLeft: "3px solid var(--primary)" }} data-testid="pipeline-modules-loaded">
          <div className="card-h">
            <span className="t">Loaded modules</span>
            <span className="sub">resolved from Pipeline screen · leak test passed at {new Date(effective.built_at).toLocaleTimeString()}</span>
            <div style={{ flex: 1 }} />
            <button type="button" className="btn sm ghost" onClick={() => setCurrent("pipeline")}>
              Change pipeline <Ico name="chevR" size={12} />
            </button>
          </div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 10 }}>
            {effective.nodes.filter(n => n.category !== "input").map(n => (
              <div key={n.slot_id} style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", padding: 10, background: "var(--surface-2)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>{n.category}</span>
                  <div style={{ flex: 1 }} />
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--dim)" }}>slot {n.slot_id}</span>
                </div>
                <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-strong)" }}>{n.label}</div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)", marginTop: 4 }}>
                  {Object.entries(n.params).slice(0, 4).map(([k, v]) => `${k}=${v}`).join(" · ") || "no params"}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Active-settings summary strip — visible across all runStates */}
      {advanced?.training_advanced && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 16, padding: "10px 12px", border: "1px solid var(--border)", borderRadius: "var(--r)", background: "var(--surface-2)", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
          <span><span style={{ color: "var(--dim)" }}>head:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.head_type}</span></span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>opt:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.optimizer}</span> + {advanced.training_advanced.scheduler}</span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>precision:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.amp_precision}</span></span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>dist:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.dist_strategy}</span></span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>clip:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.grad_clip_norm}</span></span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>loss:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.regression_loss}</span>{advanced.training_advanced.regression_loss === "huber" && ` δ=${advanced.training_advanced.huber_delta}`}</span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>early-stop:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.early_stop_metric}</span> patience {advanced.training_advanced.early_stop_patience}</span>
          <span style={{ color: "var(--dim)" }}>·</span>
          <span><span style={{ color: "var(--dim)" }}>ckpt:</span> <span style={{ color: "var(--text-strong)" }}>{advanced.training_advanced.checkpoint_policy}</span></span>
        </div>
      )}

      {/* Live metrics card — only when a real backend run is attached.
          Real-time from the v2 SSE stream: status / epoch table / mid-epoch
          batch progress / ETA / final summary. */}
      {runId && (
        <div className="card" style={{ marginBottom: 16, borderLeft: "3px solid var(--signal)" }}>
          <div className="card-h">
            <span className="t">Live metrics</span>
            <span className="sub">streaming from {runId} · {streamHealthy ? "connected" : "reconnecting"}</span>
            <div style={{ flex: 1 }} />
            {liveEpochs.length > 0 && (() => {
              const last = liveEpochs[liveEpochs.length - 1];
              return (
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                  epoch {last.epoch}/{last.total_epochs} · elapsed {Math.round(last.elapsed_s)}s · ETA {Math.round(last.eta_s)}s
                </span>
              );
            })()}
          </div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 10 }}>
            {(() => {
              const last = liveEpochs[liveEpochs.length - 1];
              // Task-aware metric labels. The training backend tags every
              // epoch event with `task: "binary" | "regression"`. For
              // binary tasks the trainer also populates val_auc/val_bce/
              // val_accuracy/val_f1/val_mean_prob explicitly; we use those
              // and show classification-appropriate labels instead of the
              // regression labels (Pearson/RMSE/MAE), which are misleading
              // on logits-vs-{0,1}.
              const isBinary = last && last.task === "binary";
              const fmt4 = (x) => (x == null || Number.isNaN(x)) ? "—" : Number(x).toFixed(4);
              const cards = last
                ? (isBinary
                    ? [
                        ["TRAIN BCE",  fmt4(last.train_loss)],
                        ["VAL BCE",    fmt4(last.val_bce ?? last.val_rmse)],
                        ["VAL AUC",    fmt4(last.val_auc ?? last.val_pearson)],
                        ["VAL ACC",    fmt4(last.val_accuracy)],
                        ["VAL F1",     fmt4(last.val_f1)],
                        ["LR",         last.lr.toExponential(2)],
                      ]
                    : [
                        ["TRAIN LOSS",   fmt4(last.train_loss)],
                        ["VAL RMSE",     fmt4(last.val_rmse)],
                        ["VAL PEARSON",  fmt4(last.val_pearson)],
                        ["VAL CI",       fmt4(last.val_ci)],
                        ["VAL MAE",      fmt4(last.val_mae)],
                        ["LR",           last.lr.toExponential(2)],
                      ])
                : [
                    ["TRAIN LOSS",  "—"], ["VAL RMSE", "—"], ["VAL PEARSON", "—"],
                    ["VAL CI", "—"], ["VAL MAE", "—"], ["LR", "—"],
                  ];
              return cards.map(([k, v]) => (
                <div key={k} style={{ padding: 10, borderRadius: "var(--r)", background: "var(--surface-2)", border: "1px solid var(--border)" }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.06em" }}>{k}</div>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 16, color: "var(--text-strong)", fontWeight: 600, marginTop: 4 }}>{v}</div>
                </div>
              ));
            })()}
          </div>
          {/* Mid-epoch batch progress bar — visible while running */}
          {liveBatch && isRunning && (
            <div style={{ padding: "8px 12px 12px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                  batch {liveBatch.batch}/{liveBatch.total_batches} (epoch {liveBatch.epoch}) · loss {liveBatch.loss.toFixed(4)}
                </span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
                  {Math.round(100 * liveBatch.batch / liveBatch.total_batches)}%
                </span>
              </div>
              <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${100 * liveBatch.batch / liveBatch.total_batches}%`, background: "var(--signal)", transition: "width 200ms" }} />
              </div>
            </div>
          )}
          {/* Final summary when the run completes */}
          {liveSummary && (() => {
            const isBinary = liveSummary.task === "binary";
            const f4 = (x) => x == null ? "—" : Number(x).toFixed(4);
            const cards = isBinary ? [
              ["TEST AUC",       f4(liveSummary.test_auc)],
              ["TEST ACC",       f4(liveSummary.test_accuracy)],
              ["TEST F1",        f4(liveSummary.test_f1)],
              ["TEST BCE",       f4(liveSummary.test_bce)],
              ["WALL TIME",      Math.round(liveSummary.wall_time_s) + "s"],
            ] : [
              ["TEST PEARSON",  f4(liveSummary.test_pearson)],
              ["TEST SPEARMAN", f4(liveSummary.test_spearman)],
              ["TEST RMSE",     f4(liveSummary.test_rmse)],
              ["TEST CI",       f4(liveSummary.test_ci)],
              ["WALL TIME",     Math.round(liveSummary.wall_time_s) + "s"],
            ];
            return (
              <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border)", display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10 }}>
                {cards.map(([k, v]) => (
                  <div key={k}>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--signal)", letterSpacing: "0.06em" }}>{k}</div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 14, color: "var(--text-strong)", fontWeight: 600 }}>{v}</div>
                  </div>
                ))}
              </div>
            );
          })()}
          {/* Tail of recent log lines */}
          {liveLogs.length > 0 && (
            <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border)", maxHeight: 140, overflowY: "auto", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.55, background: "var(--bg-soft)" }}>
              {liveLogs.slice(-12).map((l, i) => (
                <div key={i} style={{
                  color: l.level === "error" ? "var(--error)"
                       : l.level === "warn"  ? "var(--warn)"
                       : l.level === "ok"    ? "var(--signal)"
                       : "var(--muted)",
                }}>
                  <span style={{ color: "var(--dim)" }}>{new Date(l.t).toLocaleTimeString()}</span> {l.text}
                </div>
              ))}
            </div>
          )}
          {liveFailure && (
            <div style={{ padding: "10px 12px", borderTop: "1px solid var(--border)", color: "var(--error)", fontSize: 12 }}>
              <strong>Failure:</strong> {liveFailure}
            </div>
          )}
        </div>
      )}

      {/* Live progress strip — fixture demo only when no real run is attached.
          When a real backend run is attached, the Live metrics card above
          already shows everything this strip displays (plus more). */}
      {!runId && <div className="card" style={{ display: "grid", gridTemplateColumns: "1.6fr 1fr 1fr 1fr 1fr", marginBottom: 16 }}>
        <div style={{ padding: 16, borderRight: "1px solid var(--border)" }}>
          <div className="label">Progress</div>
          <div style={{ position: "relative", height: 8, background: "var(--surface-3)", borderRadius: 4, marginTop: 6 }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: (sliceEnd/40)*100 + "%", background: "linear-gradient(90deg, var(--primary), var(--molecular))", borderRadius: 4 }} />
            {isRunning && <div style={{ position: "absolute", left: (sliceEnd/40)*100 + "%", top: -3, width: 2, height: 14, background: "var(--signal)", boxShadow: "0 0 8px var(--signal)" }} />}
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
            <span>e{sliceEnd}/40 · step 73,440 / 163,200</span>
            <span style={{ color: "var(--text)" }}>ETA {isRunning ? D.run.eta : "—"}</span>
          </div>
        </div>
        <div style={{ padding: 16, borderRight: "1px solid var(--border)" }}>
          <Stat k="Val loss" v={fmt.dec(epochs[epochs.length-1].val_loss)} mono delta="↓ from 1.18" />
        </div>
        <div style={{ padding: 16, borderRight: "1px solid var(--border)" }}>
          <Stat k="Val Pearson" v="0.872" mono delta="↑ 0.041 vs prev best" />
        </div>
        <div style={{ padding: 16, borderRight: "1px solid var(--border)" }}>
          <Stat k="Step / sec" v="21.4" mono delta="2× A100 80G" />
        </div>
        <div style={{ padding: 16 }}>
          <Stat k="Spend" v={`${fmt.money(D.run.cost_so_far)} / ${fmt.money(D.run.cost_est_total)}`} mono delta="48% of estimate" />
        </div>
      </div>}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Multi-objective curves */}
          <div className="card">
            <div className="card-h">
              <span className="t">{isFailed ? "Curves up to failure" : "Training objectives"}</span>
              <span className="sub">{isFailed ? "stopped at epoch 9" : "this model predicts more than one thing — toggle which curve you watch"}</span>
              <div style={{ flex: 1 }} />
              <div className="toggle">
                {D.multiobj.map(o => (
                  <button key={o.id} aria-pressed={showObjectives[o.id]} onClick={() => setShowObjectives({ ...showObjectives, [o.id]: !showObjectives[o.id] })}>
                    <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 2, background: o.color, marginRight: 6, verticalAlign: "middle" }} />
                    {o.label.split(" (")[0]}
                  </button>
                ))}
              </div>
              <button type="button" className="btn sm ghost" title="View underlying values"
                onClick={() => toast({
                  title: "Curve values",
                  body: "Would open a side table of the last 40 epochs × (train_loss, val_loss, val_r2). The same data backs the SVG above.",
                  level: "info",
                })}>
                <Ico name="dataset" size={12} /> View as data
              </button>
            </div>
            <div className="card-b">
              {/* Build curves per objective */}
              <LineChart
                width={760} height={260}
                series={D.multiobj.filter(o => showObjectives[o.id]).flatMap(o => {
                  // Synthesize a series per objective from epochs
                  const data = epochs.map(e => {
                    if (o.id === "pki")         return { epoch: e.epoch, y: e.val_loss };
                    if (o.id === "selectivity") return { epoch: e.epoch, y: 0.62 + 0.30 * (1 - Math.exp(-e.epoch / 11)) + 0.02 * Math.sin(e.epoch) };
                    if (o.id === "binary")      return { epoch: e.epoch, y: 0.65 + 0.30 * (1 - Math.exp(-e.epoch / 10)) + 0.015 * Math.cos(e.epoch * 0.6) };
                    return { epoch: e.epoch, y: 0 };
                  });
                  return [{ data, yKey: "y", color: o.color, width: 2, fill: true }];
                })}
                yMin={0.2} yMax={1.2}
              />
              <div style={{ display: "flex", gap: 14, marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                {D.multiobj.filter(o => showObjectives[o.id]).map(o => (
                  <span key={o.id}>
                    <span style={{ display: "inline-block", width: 12, height: 2, background: o.color, verticalAlign: "middle", marginRight: 4 }} />
                    <Term word={o.label.includes("Affinity") ? "RMSE" : o.label.includes("AUC") ? "ROC AUC" : o.label}>{o.label}</Term>
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Secondary metrics row — needs ≥1 epoch landed before we can read .val_r2 */}
          {epochs.length > 0 && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
              <MiniMetricCard title="Val R²" data={epochs.map(e => ({ x: e.epoch, y: e.val_r2 }))} color="var(--signal)" cur={(epochs[epochs.length-1].val_r2 ?? 0).toFixed(3)} />
              <MiniMetricCard title="Learning rate ×10⁻⁴" data={epochs.map(e => ({ x: e.epoch, y: 3 * Math.cos((e.epoch/40) * Math.PI / 2) }))} color="var(--molecular)" cur="1.4e-4" yFmt={(v) => v.toFixed(1)} />
              <MiniMetricCard title="Grad norm" data={epochs.map(e => ({ x: e.epoch, y: 2.4 * Math.exp(-e.epoch/14) + 0.3 + 0.1*Math.sin(e.epoch) }))} color="var(--warn)" cur="0.41" />
            </div>
          )}

          {/* Log stream */}
          <div className="card">
            <div className="card-h">
              <span className="t">Log stream</span>
              <Chip tone="signal" dot>tail -f</Chip>
              <div style={{ flex: 1 }} />
              <button type="button" className="btn sm ghost"
                onClick={() => toast({
                  title: "Log export prepared",
                  body: "Would stream the full structured log (JSONL) of this run since launch — metrics, system, lifecycle, smart-insights channels merged.",
                  level: "ok",
                })}>
                <Ico name="download" size={12} /> Export
              </button>
            </div>
            <div style={{ background: "#04060c", padding: 14, fontFamily: "var(--font-mono)", fontSize: 11, color: "#9aa6c0", lineHeight: 1.65, maxHeight: 220, overflow: "auto" }}>
              {[
                { t: "13:12:08", lvl: "INFO",  msg: "epoch 18 step 73440 | train_loss=0.318 val_loss=0.394 pearson=0.872" },
                { t: "13:12:08", lvl: "INFO",  msg: "checkpoint saved → /ckpt/e18.pt (sha 4f1c… 392 MB)" },
                { t: "13:11:52", lvl: "WARN",  msg: "1 batch produced NaN attention scores — clipped to 1e-9 (FGFR1 sequence > 2048 tokens, truncated)" },
                { t: "13:11:41", lvl: "INFO",  msg: "validation pass · 18402 pairs · 41.2 s" },
                { t: "13:11:00", lvl: "INFO",  msg: "epoch 18 step 70000 | train_loss=0.322 lr=1.41e-4 mem=63.2/80 GB" },
                { t: "13:10:14", lvl: "INFO",  msg: "epoch 18 step 68000 | train_loss=0.325 grad_norm=0.41" },
                { t: "13:09:31", lvl: "INFO",  msg: "epoch 17 done · val_loss=0.408 val_pearson=0.866 saved e17.pt" },
                { t: "13:08:48", lvl: "INFO",  msg: "epoch 17 step 65000 | train_loss=0.327 throughput=21.4 step/s" },
              ].map((l, i) => (
                <div key={i}>
                  <span style={{ color: "#4a5468" }}>{l.t}</span>{" "}
                  <span style={{ color: l.lvl === "WARN" ? "var(--warn)" : l.lvl === "ERROR" ? "var(--error)" : "var(--primary)" }}>{l.lvl}</span>{" "}
                  {l.msg}
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Sidebar */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 16, alignSelf: "flex-start" }}>
          <LiveSystemCard
            gpu={gpuStats} host={hostStats}
            epochs={epochs} liveEpochs={liveEpochs} isRunning={isRunning}
          />

          <div className="card">
            <div className="card-h"><span className="t">Compare to</span><span className="sub">overlay</span></div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <CompareToList items={[
                { id: "run_4187_kc3", label: "kc3 — siamese", c: "var(--dim)", on: true },
                { id: "run_4181_kc3", label: "kc3 — esm3 sweep", c: "var(--molecular)", on: false },
                { id: "run_4168_kc2", label: "KinaseCore-v2 prod", c: "var(--signal)", on: false },
              ]} toast={toast} />
              <button type="button" className="btn sm ghost" style={{ marginTop: 4 }}
                onClick={() => toast({
                  title: "Add run to comparison",
                  body: "Would open a run picker and overlay the chosen run's validation curve on the same axes. Up to 4 runs at once.",
                  level: "info",
                })}>
                <Ico name="plus" size={12} /> Add run
              </button>
            </div>
          </div>

          {/* CompareToList delegated to a stateful sub-component below.
              Closes over the toast prop so each toggle is acknowledged. */}

          <SmartInsightsCard
            runId={runId}
            insights={liveInsights}
            muted={mutedInsights}
            onToggleMute={(id) => setMutedInsights(m => ({ ...m, [id]: !m[id] }))}
          />
        </div>
      </div>
      {isFailed && <FailureCausesAndFixes />}
    </div>
  );
}

// Multi-checkbox "Compare to" list — stateful so each toggle changes the
// row border + drives a toast acknowledging the overlay change.
function CompareToList({ items, toast }) {
  const [state, setState] = React.useState(() => Object.fromEntries(items.map(it => [it.id, !!it.on])));
  return (
    <>
      {items.map(r => {
        const on = !!state[r.id];
        return (
          <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: 6, borderRadius: 4, border: on ? "1px solid var(--border-strong)" : "1px solid var(--border-soft)" }}>
            <div style={{ width: 14, height: 2, background: r.c, borderRadius: 1 }} />
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 12 }}>{r.label}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{r.id}</div>
            </div>
            <input type="checkbox" checked={on}
              aria-label={`Overlay ${r.label} on the loss curves`}
              onChange={() => {
                const next = !on;
                setState(s => ({ ...s, [r.id]: next }));
                toast({ title: next ? `Overlaid ${r.label}` : `Removed ${r.label}`, body: `${r.id} on the val-loss chart.`, level: "info", ttl_ms: 1800 });
              }} />
          </div>
        );
      })}
    </>
  );
}

// Failure-state "Causes & Fixes" — only renders when run.state === "failed"
function FailureCausesAndFixes() {
  const toast = window.pushToast || (() => {});
  return (
    <div className="card elevated failure-causes" style={{ marginTop: 18, borderLeft: "3px solid var(--error)" }}>
      <div className="card-h">
        <Ico name="warn" style={{ color: "var(--error)" }} />
        <span className="t">Causes &amp; fixes</span>
        <span className="sub">analysis of the last 200 log lines · 1 likely cause</span>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn sm"
          onClick={() => toast({
            title: "Search logs",
            body: "Would open the log search modal scoped to this run, with filters for level, channel, and time range.",
            level: "info",
          })}>
          <Ico name="search" size={12} /> Search logs
        </button>
      </div>
      <div className="card-b" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {[
            { cat: "OOM",         likely: true,  msg: "Out-of-memory on epoch 9 with batch=96. Peak 79.8 / 80 GB.", fix: "Lower batch to 48 (est. cost delta −32%, no expected accuracy hit).", auto: true },
            { cat: "NaN spike",   likely: false, msg: "1 batch produced NaN attention before crash — single FGFR1 sequence >2048 tokens.", fix: "Enable sequence-truncation policy, or skip pairs with length > 2048.", auto: true },
            { cat: "lr divergence", likely: false, msg: "Grad norm spiked from 0.4 to 14.2 at step 32k.", fix: "Add gradient clipping at 1.0, or warm up longer.", auto: false },
            { cat: "Data error",  likely: false, msg: "No malformed rows detected in the last batch.", fix: "—", auto: false },
            { cat: "Infra",       likely: false, msg: "GPU #1 reported 1 ECC error 4 minutes before failure.", fix: "Re-queue on a different node and report to infra.", auto: false },
          ].map((c, i) => (
            <div key={i} style={{ padding: 12, border: `1px solid ${c.likely ? "var(--error)" : "var(--border)"}`, borderRadius: "var(--r)", background: c.likely ? "var(--error-soft)" : "var(--surface-2)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                <Chip tone={c.likely ? "error" : ""} dot={c.likely}>{c.cat}</Chip>
                {c.likely && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--error)" }}>likely cause</span>}
              </div>
              <div style={{ fontSize: 12, color: "var(--text)", marginBottom: 6 }}>{c.msg}</div>
              {c.fix !== "—" && (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <Ico name="sparkle" size={11} style={{ color: "var(--molecular)" }} />
                  <span style={{ fontSize: 12, color: "var(--muted)", flex: 1 }}>{c.fix}</span>
                  {c.auto && (
                    <button type="button" className="btn sm primary"
                      onClick={() => toast({
                        title: `Re-queued with fix: ${c.cat}`,
                        body: `Would clone the failed run, apply the proposed fix (${c.fix}), and re-launch from the last checkpoint (epoch 8).`,
                        level: "ok",
                      })}>
                      Auto-restart with fix
                    </button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Filtered log tail */}
        <div style={{ background: "#04060c", borderRadius: "var(--r)", padding: 14, fontFamily: "var(--font-mono)", fontSize: 11, color: "#9aa6c0", lineHeight: 1.65, height: 320, overflow: "auto" }}>
          {[
            { t: "12:48:02", lvl: "ERROR", msg: "CUDA out of memory. Tried to allocate 4.18 GiB (GPU 0; 78.32 GiB total capacity; 75.92 GiB already allocated; …)" },
            { t: "12:48:01", lvl: "ERROR", msg: "RuntimeError in epoch 9 step 33112 — training aborted" },
            { t: "12:47:58", lvl: "WARN",  msg: "memory pressure: 79.8 / 80 GB on GPU 0 (peak)" },
            { t: "12:47:21", lvl: "WARN",  msg: "1 batch produced NaN attention scores — clipped to 1e-9 (FGFR1 sequence > 2048 tokens, truncated)" },
            { t: "12:46:55", lvl: "INFO",  msg: "epoch 9 step 33000 | train_loss=0.441 grad_norm=1.92" },
            { t: "12:46:18", lvl: "INFO",  msg: "epoch 9 step 32500 | train_loss=0.448 grad_norm=14.2 ← spike" },
            { t: "12:45:40", lvl: "INFO",  msg: "epoch 9 step 32000 | train_loss=0.455 grad_norm=0.42" },
          ].map((l, i) => (
            <div key={i}><span style={{ color: "#4a5468" }}>{l.t}</span> <span style={{ color: l.lvl === "WARN" ? "var(--warn)" : l.lvl === "ERROR" ? "var(--error)" : "var(--primary)" }}>{l.lvl}</span> {l.msg}</div>
          ))}
        </div>
      </div>
    </div>
  );
}

function InsightCard({ tone, title, body, conf, why, epoch, muted, onToggleMute }) {
  const [open, setOpen] = React.useState(false);
  const c = tone === "signal" ? "var(--signal)" : tone === "warn" ? "var(--warn)" : tone === "error" ? "var(--error)" : "var(--primary)";
  return (
    <div style={{ padding: 10, border: "1px solid var(--border)", borderRadius: "var(--r)", background: "var(--surface-2)", opacity: muted ? 0.45 : 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4, flexWrap: "wrap" }}>
        <span style={{ color: c, fontSize: 16, lineHeight: 1 }}>●</span>
        <span style={{ fontSize: 12, color: "var(--text-strong)", fontWeight: 500 }}>{title}</span>
        {epoch != null && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>e{epoch}</span>}
        <div style={{ flex: 1 }} />
        {conf && <Chip>{conf} confidence</Chip>}
        <button className="btn sm ghost" style={{ padding: "2px 6px", fontSize: 10 }} onClick={() => setOpen(!open)} title="Why this insight?">why?</button>
        <button className="btn sm ghost" style={{ padding: "2px 6px", fontSize: 10 }}
          onClick={onToggleMute}
          title="Mute / unmute this insight">{muted ? "unmute" : "mute"}</button>
      </div>
      <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>{body}</div>
      {open && why && (
        <div style={{ marginTop: 8, padding: 8, background: "var(--bg-soft)", borderRadius: 4, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", lineHeight: 1.6 }}>
          <span style={{ color: "var(--text)" }}>trigger:</span> {why}
        </div>
      )}
    </div>
  );
}

// Live "GPU · System" card — polls /api/v2/system/gpu and /api/v2/system/host
// every ~2.5s and renders the real device, memory pressure, CPU + RAM
// utilization, disk I/O, and per-epoch throughput.
function LiveSystemCard({ gpu, host, epochs, liveEpochs, isRunning }) {
  // GPU
  const gpuAvail = !!(gpu && gpu.available);
  const gpuName  = gpu?.device_name || (gpu?.status === "loading" ? "GPU warming up…" : "CPU only");
  const gpuUsedPct = gpuAvail && gpu.used_pct != null ? gpu.used_pct : (gpuAvail && gpu.free_pct != null ? (100 - gpu.free_pct) : null);
  const gpuUsedGb  = gpuAvail && gpu.used_memory_bytes != null ? (gpu.used_memory_bytes / (1024**3)) : null;
  const gpuTotGb   = gpuAvail && gpu.total_memory_bytes != null ? (gpu.total_memory_bytes / (1024**3)) : null;
  const gpuSub     = (gpuUsedGb != null && gpuTotGb != null)
    ? `${gpuUsedGb.toFixed(1)} / ${gpuTotGb.toFixed(1)} GB · sm_${(gpu.compute_cap || "").replace(".", "")}`
    : (gpu?.message || "—");

  // CPU
  const cpuPct   = host?.cpu_pct ?? null;
  const cpuCount = host?.cpu_count ?? null;
  const cpuSub   = cpuCount != null ? `${cpuCount} logical cores${host?.psutil_missing ? " · psutil not installed" : ""}` : "—";

  // RAM
  const ramPct = host?.ram_pct ?? null;
  const ramUsedGb = host?.ram_used_bytes != null ? host.ram_used_bytes / (1024**3) : null;
  const ramTotGb  = host?.ram_total_bytes != null ? host.ram_total_bytes / (1024**3) : null;
  const ramSub   = (ramUsedGb != null && ramTotGb != null) ? `${ramUsedGb.toFixed(1)} / ${ramTotGb.toFixed(1)} GB` : "—";

  // Disk I/O
  const diskReadBps  = host?.disk_read_bps ?? null;
  const diskWriteBps = host?.disk_write_bps ?? null;
  const totalBps = (diskReadBps ?? 0) + (diskWriteBps ?? 0);
  const fmtBps = (b) => b == null ? "—" : (b >= 1e9 ? `${(b/1e9).toFixed(2)} GB/s` : b >= 1e6 ? `${(b/1e6).toFixed(1)} MB/s` : b >= 1e3 ? `${(b/1e3).toFixed(0)} KB/s` : `${Math.round(b)} B/s`);
  const diskSub = (host?.disk_root && host?.disk_free_bytes != null)
    ? `${host.disk_root}  ·  ${(host.disk_free_bytes/1e9).toFixed(0)} GB free`
    : "—";

  // Throughput from last live epoch (epochs per minute, derived from elapsed_s).
  let throughput = null, throughputDelta = null;
  if (liveEpochs && liveEpochs.length >= 1) {
    const last = liveEpochs[liveEpochs.length - 1];
    if (last.elapsed_s > 0) {
      throughput = `${(last.epoch / (last.elapsed_s / 60)).toFixed(2)} epoch/min`;
    }
    if (liveEpochs.length >= 2) {
      const prev = liveEpochs[liveEpochs.length - 2];
      const dt = last.elapsed_s - prev.elapsed_s;
      if (dt > 0) throughputDelta = `last epoch ${dt.toFixed(0)}s`;
    }
  }

  return (
    <div className="card">
      <div className="card-h">
        <span className="t">GPU · System</span>
        <div style={{ flex: 1 }} />
        <Chip tone={gpuAvail ? "signal" : "warn"} dot>{gpuAvail ? "live" : (gpu?.status === "loading" ? "warming" : "no GPU")}</Chip>
      </div>
      <div className="card-b">
        <SystemRow
          label={gpuName}
          v={gpuUsedPct != null ? `${gpuUsedPct.toFixed(0)}%` : (gpuAvail ? "—" : "n/a")}
          sub={gpuSub}
          col="var(--primary)"
        />
        <SystemRow
          label={`CPU${cpuCount ? ` · ${cpuCount} cores` : ""}`}
          v={cpuPct != null ? `${cpuPct.toFixed(0)}%` : "—"}
          sub={cpuSub}
          col="var(--molecular)"
        />
        <SystemRow
          label="RAM"
          v={ramPct != null ? `${ramPct.toFixed(0)}%` : "—"}
          sub={ramSub}
          col="var(--accent, var(--signal))"
        />
        <SystemRow
          label="Disk I/O"
          v={fmtBps(totalBps)}
          sub={diskSub}
          col="var(--signal)"
        />
        <hr className="hr" />
        <Stat
          k="Throughput"
          v={throughput || (isRunning ? "—" : "idle")}
          mono
          delta={throughputDelta || (gpu?.torch_version ? `torch ${gpu.torch_version}` : "")}
        />
      </div>
    </div>
  );
}

// Smart insights card — consumes live `insight` events from the SSE stream
// when a run is attached. Falls back to a small explainer card otherwise.
function SmartInsightsCard({ runId, insights, muted, onToggleMute }) {
  const active = (insights || []).filter(x => !muted?.[x.id]);
  const total  = (insights || []).length;
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Smart insights</span>
        <Ico name="sparkle" style={{ color: "var(--molecular)" }} />
        <div style={{ flex: 1 }} />
        <Chip>{active.length} active{total !== active.length ? ` · ${total - active.length} muted` : ""}</Chip>
      </div>
      <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {(!runId || insights.length === 0) && (
          <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5, padding: "6px 2px" }}>
            {runId
              ? "Watching for overfitting, plateau, oscillation, and bias-init divergence. Insights will appear here the moment a pattern emerges."
              : "Launch a real run from the Pipeline screen to see live training-pattern detection (gap widening, val plateau, past-peak overfitting, LR oscillation)."}
          </div>
        )}
        {insights.map(ev => (
          <InsightCard
            key={ev.id}
            tone={ev.tone}
            title={ev.title}
            body={ev.body}
            conf={ev.conf}
            why={ev.why}
            epoch={ev.epoch}
            muted={!!muted?.[ev.id]}
            onToggleMute={() => onToggleMute(ev.id)}
          />
        ))}
      </div>
    </div>
  );
}

function MiniMetricCard({ title, data, color, cur, yFmt }) {
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">{title}</span>
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 13, color: "var(--text-strong)" }}>{cur}</span>
      </div>
      <div style={{ padding: 8 }}>
        <LineChart
          width={240} height={84}
          series={[{ data, xKey: "x", yKey: "y", color, width: 1.6, fill: true }]}
          padding={[8, 6, 16, 26]}
          yFmt={yFmt}
        />
      </div>
    </div>
  );
}

function SystemRow({ label, v, sub, col }) {
  // Only draw the utilization bar when `v` is a percent (e.g. "42%"). For
  // rate values like "240 MB/s" the bar would mis-scale, so we hide it.
  const isPct = typeof v === "string" && /%\s*$/.test(v);
  const pct = isPct ? Math.max(0, Math.min(100, parseInt(v) || 0)) : null;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={label}>{label}</span>
        <span className="mono">{v}</span>
      </div>
      {pct != null && (
        <div style={{ height: 4, background: "var(--surface-3)", borderRadius: 2 }}>
          <div style={{ width: pct + "%", height: "100%", background: col, borderRadius: 2, transition: "width 300ms" }} />
        </div>
      )}
      <div style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: "var(--dim)", marginTop: 3 }}>{sub}</div>
    </div>
  );
}

window.ScreenTraining = ScreenTraining;
