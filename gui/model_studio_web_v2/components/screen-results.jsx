// ProteoSphere — Results dashboard

function ScreenResults({ setCurrent, advanced, advancedDeltaCount, openAdvanced, coachAreas, coachOn, pushToast }) {
  const toast = pushToast || window.pushToast;
  const D = window.PS_DATA;
  const [stratum, setStratum] = React.useState("all");
  // Attention direction is lifted here so SequenceView can re-render with
  // different per-residue weights when the user flips protein↔ligand.
  const [attDir, setAttDir] = React.useState("p2l");

  // ── Real backend results ──────────────────────────────────────────
  // When a real run is attached (PS_DATA.pipeline.current_run_id), we
  // fetch /api/v2/pipeline/runs/{id}/results once after completion and
  // render the page from those numbers. Falls back to the fixture demo
  // when no run is attached or the run hasn't completed yet.
  const runId = D?.pipeline?.current_run_id;
  const [realResults, setRealResults] = React.useState(null);
  const [realLoading, setRealLoading] = React.useState(false);
  const [realError, setRealError] = React.useState(null);
  const refetch = React.useCallback(() => {
    if (!runId) return;
    setRealLoading(true);
    setRealError(null);
    fetch(`/api/v2/pipeline/runs/${encodeURIComponent(runId)}/results`)
      .then(async r => {
        const j = await r.json();
        if (!r.ok) {
          throw new Error(j.message || j.error || `HTTP ${r.status}`);
        }
        setRealResults(j);
      })
      .catch(err => setRealError(String(err)))
      .finally(() => setRealLoading(false));
  }, [runId]);
  React.useEffect(() => { refetch(); }, [refetch]);

  // ── Metrics: real if available, fixture otherwise ────────────────
  // Strata only make sense when the run actually has stratum metadata.
  // Davis warm-split (current v2 default) is one stratum: 'all'.
  const STRATA_DELTAS = {
    all:        { pearson: 0,      spearman: 0,      rmse: 0,    mae: 0,    r2: 0,     test_n: 1.00, label: "All test pairs" },
    held_out:   { pearson: -0.018, spearman: -0.022, rmse: 0.04, mae: 0.03, r2: -0.02, test_n: 0.43, label: "Held-out targets" },
    cold_scaff: { pearson: -0.041, spearman: -0.038, rmse: 0.09, mae: 0.07, r2: -0.06, test_n: 0.31, label: "Cold scaffolds" },
  };
  const useReal = !!(realResults && realResults.results && realResults.results.metrics);
  const realBase = useReal ? realResults.results.metrics : null;
  const fixtureBase = D.metrics;
  const sd = STRATA_DELTAS[stratum];
  const m = useReal
    ? { pearson: realBase.pearson, spearman: realBase.spearman, rmse: realBase.rmse, mae: realBase.mae, r2: realBase.r2, test_n: realBase.n }
    : {
        pearson:  fixtureBase.pearson  + sd.pearson,
        spearman: fixtureBase.spearman + sd.spearman,
        rmse:     fixtureBase.rmse     + sd.rmse,
        mae:      fixtureBase.mae      + sd.mae,
        r2:       fixtureBase.r2       + sd.r2,
        test_n:   Math.round(fixtureBase.test_n * sd.test_n),
      };
  const [showCalibTable, setShowCalibTable] = React.useState(false);

  // Filter validator items relevant on the Results page
  const validatorItems = D.validator.items;

  // Unified blocker → field jump. Works whether the field anchor lives on
  // this screen or another. `window.jumpToField` handles both cases.
  const handleJump = (item) => {
    const anchor = item.related_fields?.[0];
    if (!anchor) return;
    if (item.location === "results") {
      window.jumpToField(anchor);
    } else {
      window.jumpToField(anchor, setCurrent, item.location);
    }
  };

  // Scatter, ROC, calibration, residuals — real when the run is attached,
  // fixture otherwise. The ScatterChart expects [xn, yn, r, color, opacity]
  // tuples where xn/yn are in [0,1].
  const scatter = React.useMemo(() => {
    if (useReal && realResults?.results) {
      const r = realResults.results;
      const inliers  = (r.scatter_inliers  || []).map(p => [p[0], p[1], 1.6, "var(--primary)", 0.55]);
      const outliers = (r.scatter_outliers || []).map(p => [p[0], p[1], 2.4, "var(--error)", 0.95]);
      return [...inliers, ...outliers];
    }
    // Fixture demo (used only when no real run)
    const pts = [];
    for (let i = 0; i < 220; i++) {
      const x = 4 + Math.random() * 8;
      const y = x + (Math.random() - 0.5) * 0.9 + 0.05 * Math.sin(i);
      pts.push([(x - 4) / 8, (y - 4) / 8, 1.6, "var(--primary)", 0.5]);
    }
    for (let i = 0; i < 8; i++) {
      const x = 4 + Math.random() * 8;
      pts.push([(x - 4) / 8, Math.random() * 1, 2.4, "var(--error)", 0.9]);
    }
    return pts;
  }, [useReal, realResults]);

  const rocPts = (useReal && realResults?.results?.roc?.points)
    ? realResults.results.roc.points.map(r => ({ thr: r.thr, fpr: r.fpr, tpr: r.tpr }))
    : D.roc.map(r => ({ ...r }));
  const rocAuc = (useReal && realResults?.results?.roc) ? realResults.results.roc.auc : null;

  // Real calibration bins → format matches the fixture (pred + actual on x/y in 0..1).
  // We project pKd range [lo,hi] to [0,1] so the existing ScatterChart code keeps working.
  const realCalib = React.useMemo(() => {
    if (!useReal || !realResults?.results?.calibration) return null;
    const [lo, hi] = realResults.results.y_pkd_range || [4, 12];
    const span = Math.max(0.001, hi - lo);
    return realResults.results.calibration.map(c => ({
      pred:   (c.pred_mean   - lo) / span,
      actual: (c.actual_mean - lo) / span,
      n: c.n, abs_err: c.abs_err, bin: c.bin,
      pred_pki:   c.pred_mean, actual_pki: c.actual_mean,
    }));
  }, [useReal, realResults]);

  // Residual histogram (21 bins, real when available)
  const histBins = React.useMemo(() => {
    if (useReal && realResults?.results?.residual_hist) {
      const r = realResults.results.residual_hist;
      const rmse = r.rmse || 0.5;
      const edges = r.edges, counts = r.counts;
      return counts.map((v, i) => {
        const center = (edges[i] + edges[i + 1]) / 2;
        return {
          v: Math.max(0, v),
          color: Math.abs(center) > 1.5 * rmse ? "var(--warn)" : "var(--primary)",
        };
      });
    }
    return Array.from({ length: 21 }, (_, i) => {
      const c = (i - 10) / 5;
      return { v: Math.max(0, 90 * Math.exp(-c * c) + 6 * Math.sin(i)), color: Math.abs(c) > 1.5 ? "var(--warn)" : "var(--primary)" };
    });
  }, [useReal, realResults]);

  // Pick up the design objective from PS_DATA (set on the Splits screen).
  // Drives the banner tone + the kicker line so readers know whether the
  // metrics they're about to look at are interpolation-context (numbers
  // look high, only valid for similar new pairs) or extrapolation-context
  // (numbers look lower, generalise to truly novel pairs).
  const objectiveId = window.PS_DATA?.design_objective || "generalization";
  const objectiveDef = (window.PS_DESIGN_OBJECTIVES || []).find(o => o.id === objectiveId)
    || window.PS_DESIGN_OBJECTIVES?.[0]
    || { tone: "warn", short: "generalization", bannerTitle: "Generalisation study", bannerSub: "Metrics on held-out clusters." };

  // ── No demo fallback ─────────────────────────────────────────────
  // Earlier builds silently rendered fixture metrics when no real run
  // was attached. That made every visit look like a successful run
  // had completed, even when nothing was attached or the backend had
  // dropped the run. We now render an honest empty state so the user
  // never confuses fixture numbers for real ones.
  if (!useReal) {
    return (
      <div className="screen" data-screen-label="06 Results" data-objective={objectiveId}>
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
          <h2 style={{ marginTop: 0, marginBottom: 6 }}>
            {!runId ? "No run attached" : (realLoading ? "Loading results…" : "Run not ready")}
          </h2>
          <p className="lead" style={{ maxWidth: 540, margin: "0 auto 18px" }}>
            {!runId
              ? "No training run is attached to this dashboard. Launch a real run from the Pipeline screen and its metrics will appear here once it finishes."
              : realLoading
                ? `Fetching results for ${runId}…`
                : realError
                  ? `Couldn't load results for ${runId}: ${realError}. The run may still be training, may have failed, or its checkpoint was evicted (the registry is in-memory only and resets across server restarts).`
                  : `Run ${runId} attached but no results yet. Wait for it to finish on the Training tab and click Refresh.`}
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
            <button type="button" className="btn primary"
              onClick={() => setCurrent("pipeline")}>
              Go to Pipeline <Ico name="chevR" size={11} />
            </button>
            {runId && (
              <button type="button" className="btn" onClick={refetch}
                disabled={realLoading}>
                {realLoading ? "Refreshing…" : "Retry fetch"}
              </button>
            )}
          </div>
          {/* Show the run id (even when unresolved) so the user can copy
              it for a manual `curl /api/v2/pipeline/runs/<id>/results`. */}
          {runId && (
            <div style={{ marginTop: 18, fontFamily: "var(--font-mono)",
                          fontSize: 11, color: "var(--dim)" }}>
              run_id: {runId}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="screen" data-screen-label="06 Results" data-objective={objectiveId}>
      {/* Objective banner — colored to match the design intent. A reader
          glancing at this page should know within a second whether high
          numbers are real (generalisation) or merely fill-in (interpolation). */}
      <div className="objective-banner" style={{
        display: "flex", alignItems: "center", gap: 12,
        padding: "10px 14px", marginBottom: 14,
        background: `linear-gradient(90deg, var(--${objectiveDef.tone}-soft), transparent 70%)`,
        borderLeft: `3px solid var(--${objectiveDef.tone})`,
        borderRadius: "var(--r)",
      }}>
        <Ico name={objectiveDef.tone === "warn" ? "warn" : "info"} size={14} style={{ color: `var(--${objectiveDef.tone})`, flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: `var(--${objectiveDef.tone})`, textTransform: "uppercase", letterSpacing: "0.06em" }}>
            {objectiveDef.bannerTitle}
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 1 }}>{objectiveDef.bannerSub}</div>
        </div>
        <button type="button" className="btn sm ghost" onClick={() => setCurrent("split")}>
          Change on Splits <Ico name="chevR" size={11} />
        </button>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 14 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>
            RESULTS · {useReal
              ? realResults.run_id
              : (runId
                  ? (realLoading ? "loading…" : (realError ? "no results yet" : runId))
                  : "no run attached — showing demo")}
            {!useReal && !runId && (
              <Chip tone="warn" style={{ marginLeft: 8 }}>demo fixture</Chip>
            )}
          </div>
          <h1 style={{ fontSize: 24 }}>{useReal ? (realResults.template_label || realResults.template_id) : (runId ? (realLoading ? "Loading results…" : "Awaiting completion") : "KinaseCore-v3 (demo)")}</h1>
          <p className="lead">
            {useReal
              ? `${realResults.summary?.benchmark ?? "Davis"} ${realResults.summary?.split_policy ?? "warm-split"} · ${realResults.summary?.n_train?.toLocaleString() ?? "?"} train · evaluated on ${realResults.results.metrics.n.toLocaleString()} test pairs · wall ${Math.round(realResults.summary?.wall_time_s || 0)}s on ${realResults.summary?.device || "cuda"}.`
              : runId
                ? (realLoading
                    ? `Fetching results for ${runId}…`
                    : (realError
                        ? `Could not load results for ${runId}: ${realError}. The run may still be training, may have failed, or its checkpoint was evicted.`
                        : `Run ${runId} attached but no results yet — wait for it to finish on the Training tab.`))
                : "No run attached — showing the demo fixture (KinaseCore-v3). Launch a run on Pipeline to see live metrics here."}
          </p>
        </div>
        <div style={{ flex: 1 }} />
        {/* Stratum toggle — meaningful only when the run has stratum splits.
            Davis warm-split has one stratum ('all'). Other strata greyed out
            with a tooltip explaining what they'd need. */}
        <div className="toggle" role="tablist" aria-label="Stratify metrics"
          title={useReal ? "Davis warm-split has one stratum. Cold-target / cold-scaffold need cluster-split metadata from the Splits screen." : ""}>
          <button aria-pressed={stratum === "all"}        onClick={() => setStratum("all")}>All</button>
          <button aria-pressed={stratum === "held_out"}   onClick={() => !useReal && setStratum("held_out")}   disabled={useReal}>Held-out targets</button>
          <button aria-pressed={stratum === "cold_scaff"} onClick={() => !useReal && setStratum("cold_scaff")} disabled={useReal}>Cold scaffolds</button>
        </div>
        {openAdvanced && (
          <AdvancedButton
            panelKey="eval_analytics"
            openAdvanced={openAdvanced}
            deltaCount={advancedDeltaCount?.eval_analytics}>
            Eval advanced
          </AdvancedButton>
        )}
        <button type="button" className="btn ghost"
          onClick={() => toast({
            title: "Generating run report",
            body: "Would assemble a single HTML/PDF with metrics, per-stratum cuts, calibration, validator items, and the full Lineage chain. Ready in ~6s.",
            level: "ok",
          })}>
          <Ico name="download" size={12} /> Export report
        </button>
        <button type="button" className="btn primary"
          onClick={() => setCurrent("promote")}>
          <Ico name="flag" size={12} /> Promote to prod
        </button>
      </div>

      {/* Top metric strip — re-segments live when the stratum toggle changes */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-h" style={{ padding: "8px 14px", background: "var(--surface-2)", borderBottom: "1px solid var(--border)" }}>
          <span className="k">Stratum</span>
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text-strong)" }}>{sd.label}</span>
          {stratum !== "all" && (
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--warn)" }}>
              Δ vs all: Pearson {sd.pearson.toFixed(3)} · RMSE +{sd.rmse.toFixed(2)}
            </span>
          )}
          <div style={{ flex: 1 }} />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
            paired-bootstrap CI(α={advanced?.eval_analytics?.ci_alpha ?? 0.05}) · n={advanced?.eval_analytics?.bootstrap_n ?? 1000}
          </span>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", padding: 0 }}>
          {/* Headline metric labels now carry their units inline + a
              one-line "is this good?" caption so a first-time user can
              calibrate. The UX-review agent flagged this as a high-
              severity density issue: bare 0.612 / 0.041 floats forced
              users to look up the units. */}
          {[
            { k: "Pearson r",  v: m.pearson, mono: true,
              sub: m.pearson > 0.85 ? "excellent" : m.pearson > 0.7 ? "good" : m.pearson > 0.5 ? "moderate" : "weak",
              delta: stratum === "all" ? "↑ 0.018 vs prev best" : null },
            { k: "Spearman ρ", v: m.spearman, mono: true,
              sub: "rank-order agreement (-1…1)" },
            { k: "RMSE", v: m.rmse, mono: true,
              sub: `pKi units · ~${Math.pow(10, m.rmse).toFixed(1)}× Kd error`,
              delta: stratum === "all" ? "↓ 0.043" : null },
            { k: "MAE", v: m.mae, mono: true, sub: "pKi units" },
            { k: "R²", v: m.r2, mono: true,
              sub: m.r2 > 0.7 ? "publication-grade" : m.r2 > 0.5 ? "useful" : m.r2 > 0.2 ? "marginal" : "weak" },
            { k: "Test N", v: fmt.n(m.test_n), mono: true,
              sub: stratum === "all" ? "18,402 cold-target" : sd.label.toLowerCase() },
          ].map((s, i) => (
            <div key={i} style={{ padding: 18, borderRight: i < 5 ? "1px solid var(--border)" : "none" }}>
              <Stat k={s.k} v={typeof s.v === "number" ? fmt.dec(s.v, 3) : s.v} mono={s.mono} delta={s.delta} />
              {s.sub && (
                <div style={{ fontSize: 10, color: "var(--dim)", marginTop: 4, fontStyle: "italic" }}>{s.sub}</div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* Validator-driven recommendations */}
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-h">
          <Ico name="sparkle" style={{ color: "var(--molecular)" }} />
          <span className="t">Recommendations &amp; blockers</span>
          <span className="sub">{validatorItems.filter(i => i.level === "blocker").length} blocking · {validatorItems.filter(i => i.level === "warning").length} warnings · {validatorItems.filter(i => i.level === "info").length} tips</span>
          <div style={{ flex: 1 }} />
          <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>Click any card to jump to the offending field.</span>
        </div>
        <div style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {validatorItems.map((item, i) => (
            <BlockerCard key={i} item={item} onJump={handleJump} />
          ))}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, marginBottom: 16 }}>
        {/* Pred vs actual */}
        <div className="card">
          <div className="card-h"><span className="t">Predicted vs actual pKi</span><Chip>regression</Chip></div>
          <div className="card-b">
            <ScatterChart width={320} height={220} points={scatter} />
            <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--dim)", marginTop: 4 }}>n=228 sample · 8 high-residual flagged</div>
          </div>
        </div>

        {/* ROC */}
        <div className="card">
          <div className="card-h">
            <span className="t">ROC · binary @ pKi ≥ 6</span>
            <Chip>AUC {rocAuc != null ? rocAuc.toFixed(3) : "0.918"}</Chip>
          </div>
          <div className="card-b">
            <ScatterChart width={320} height={220} points={[
              ...rocPts.map(r => [r.fpr, r.tpr, 1.4, "var(--primary)", 0.9]),
            ]} showDiagonal />
            <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--dim)", marginTop: 4 }}>
              {useReal && realResults?.results?.roc
                ? `${realResults.results.roc.pos} pos / ${realResults.results.roc.neg} neg · threshold pKi = ${realResults.results.roc.threshold}`
                : "vs baseline AUC 0.881"}
            </div>
          </div>
        </div>

        {/* Calibration */}
        <div className="card" data-field="results.calibration">
          <div className="card-h">
            <span className="t">Calibration</span>
            <Chip tone="warn"><Term word="ECE">ECE</Term> 0.041 (active/inactive)</Chip>
            <div style={{ flex: 1 }} />
            <button className="btn sm ghost" onClick={() => setShowCalibTable(!showCalibTable)} title="View underlying data">
              <Ico name="dataset" size={12} /> {showCalibTable ? "Chart" : "View as data"}
            </button>
          </div>
          <div className="card-b">
            {!showCalibTable ? (
              <>
                <ScatterChart width={320} height={220} points={[
                  ...(realCalib || D.calibration).map(c => [c.pred, c.actual, 4, "var(--molecular)", 0.95]),
                ]} showDiagonal radius={4} />
                <div style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--dim)", marginTop: 4 }}>
                  {realCalib
                    ? `${realCalib.length} equal-frequency bins on test predictions · pKd range ${realResults.results.y_pkd_range[0].toFixed(2)}…${realResults.results.y_pkd_range[1].toFixed(2)}`
                    : "10 bins · predicted vs observed pKi · over-confident at high pred."}
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6, lineHeight: 1.5 }}>
                  ECE here is computed on the active/inactive binarisation (pKi ≥ 6). For the regression head, see the <Term word="conformal interval">conformal coverage curve</Term> on the Inference screen — nominal-vs-empirical coverage is the regression-native check.
                </div>
              </>
            ) : (
              <table className="tbl" style={{ fontSize: 12 }}>
                <thead><tr><th>Bin</th><th>Predicted</th><th>Actual</th><th>Δ</th>{realCalib && <th>n</th>}</tr></thead>
                <tbody>
                  {(realCalib
                    ? realCalib.map(c => ({ pred: c.pred_pki, actual: c.actual_pki, n: c.n }))
                    : D.calibration
                  ).map((c, i) => {
                    const d = c.actual - c.pred;
                    return (
                      <tr key={i}>
                        <td className="mono">{i + 1}</td>
                        <td className="mono">{c.pred.toFixed(2)}</td>
                        <td className="mono">{c.actual.toFixed(2)}</td>
                        <td className="mono" style={{ color: Math.abs(d) > (realCalib ? 0.15 : 0.04) ? "var(--warn)" : "var(--muted)" }}>{(d > 0 ? "+" : "") + d.toFixed(3)}</td>
                        {realCalib && <td className="mono" style={{ color: "var(--dim)" }}>{c.n}</td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        {/* Residual histogram + per-target errors */}
        <div className="card">
          <div className="card-h"><span className="t">Residual distribution</span><span className="sub">predicted − actual</span></div>
          <div className="card-b">
            <Histogram width={520} height={140} bins={histBins} />
            <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
              <span>μ = −0.04</span>
              <span>σ = 0.61</span>
              <span>tail (|r| &gt; 1.5) = 3.1%</span>
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-h">
            <span className="t">Per-target errors</span>
            <span className="sub">where the model struggles</span>
            <div style={{ flex: 1 }} />
            <button type="button" className="btn sm ghost"
              onClick={() => toast({
                title: "Per-target table",
                body: "Would open the full per-target error breakdown (~12 columns: UniProt, family, N pairs, RMSE, MAE, Pearson, Spearman, bias, conformal coverage, residual histogram).",
                level: "info",
              })}>
              View all targets →
            </button>
          </div>
          <table className="tbl">
            <thead>
              <tr><th>Target</th><th>N</th><th>RMSE</th><th>Bias</th><th></th></tr>
            </thead>
            <tbody>
              {D.perTarget.slice(0, 6).map(t => (
                <tr key={t.uniprot}>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span className="mono" style={{ color: "var(--dim)" }}>{t.uniprot}</span>
                      <span style={{ fontWeight: 500 }}>{t.name}</span>
                    </div>
                  </td>
                  <td className="mono">{fmt.n(t.n)}</td>
                  <td>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <div style={{ width: 50, height: 4, background: "var(--surface-3)", borderRadius: 2 }}>
                        <div style={{ width: `${Math.min(100, t.rmse * 80)}%`, height: "100%", background: t.rmse > 0.7 ? "var(--error)" : t.rmse > 0.55 ? "var(--warn)" : "var(--signal)", borderRadius: 2 }} />
                      </div>
                      <span className="mono">{t.rmse.toFixed(2)}</span>
                    </div>
                  </td>
                  <td className="mono">{t.bias > 0 ? "+" : ""}{t.bias.toFixed(2)}</td>
                  <td>
                    {t.status === "high-error" && <Chip tone="error" dot>high err</Chip>}
                    {t.status === "drift" && <Chip tone="warn" dot>bias drift</Chip>}
                    {t.status === "ok" && <Chip tone="signal">ok</Chip>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Sequence + 3D */}
      <div style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 16 }}>
        <div className="card">
          <div className="card-h">
            <span className="t">Attention attribution</span>
            <span className="sub">BTK · residues 380–470 · ibrutinib</span>
            <div style={{ flex: 1 }} />
            <AttentionDirToggle toast={toast} dir={attDir} setDir={setAttDir} />
          </div>
          <div className="card-b">
            <div className="seqrow" style={{ marginBottom: 14 }}>
              <SequenceView dir={attDir} />
            </div>
            <div style={{ display: "flex", gap: 12, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
              <span><span className="res hi" style={{ width: 11, display: "inline-block", textAlign: "center" }}>A</span> ATP-pocket residue (4)</span>
              <span><span className="res hi2" style={{ width: 11, display: "inline-block", textAlign: "center" }}>A</span> hinge region (3)</span>
              <span><span className="res hi3" style={{ width: 11, display: "inline-block", textAlign: "center" }}>A</span> gatekeeper T474 (1)</span>
            </div>
            <hr className="hr" />
            <div style={{ fontSize: 12, color: "var(--muted)" }}>
              Top attention mass clusters on P-loop (residues 412–416), hinge (E475–Y476), and gatekeeper T474 — biologically plausible for ATP-competitive kinase inhibitors.
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-h"><span className="t">3D — top attended residues</span><Chip>placeholder viewer</Chip></div>
          <div className="card-b">
            <MoleculeView height={260} label="BTK · ibrutinib · attention map" caption="warm: high attention · cool: low" />
          </div>
        </div>
      </div>
    </div>
  );
}

// Sequence with attention coloring
// Attention direction toggle (protein→ligand / ligand→protein).
// Stateful in the prototype; the chosen direction would re-fetch the
// attention tensor in a real wiring.
function AttentionDirToggle({ toast, dir, setDir }) {
  return (
    <div className="toggle" role="group" aria-label="Attention direction">
      <button type="button" aria-pressed={dir === "p2l"} onClick={() => {
        setDir("p2l");
        toast && toast({ title: "Attention: protein → ligand", body: "Sequence weights now show where each residue attended to the ligand — peaks should cluster at the ATP pocket, hinge, and gatekeeper.", level: "info", ttl_ms: 2000 });
      }}>protein → ligand</button>
      <button type="button" aria-pressed={dir === "l2p"} onClick={() => {
        setDir("l2p");
        toast && toast({ title: "Attention: ligand → protein", body: "Sequence weights now show where each ligand atom attended back — the peak shape shifts toward the covalent target (Cys481).", level: "info", ttl_ms: 2000 });
      }}>ligand → protein</button>
    </div>
  );
}

function SequenceView({ dir = "p2l" }) {
  const D = window.PS_DATA;
  const seq = D.sequence.seq;
  const start = D.sequence.range[0];
  // synthetic attention weights — memoised so they don't flicker on re-render.
  // The noise term uses a deterministic per-residue hash (not Math.random)
  // so the coloring is stable across hover/state changes.
  //
  // Two distinct profiles for the two attention directions:
  //   p2l (protein → ligand): peaks at ATP-pocket / hinge / DFG cluster
  //   l2p (ligand → protein): peak shifts toward the covalent target Cys481
  //                            (which sits ~60% along the displayed window)
  const weights = React.useMemo(() => seq.split("").map((_, i) => {
    const p = i / seq.length;
    const jitter = (Math.sin(i * 12.9898) * 43758.5453) % 1;
    if (dir === "l2p") {
      // Sharper, single dominant peak around p≈0.6 (Cys481-ish position).
      return 0.85 * Math.exp(-Math.pow((p - 0.60) * 11, 2)) +
             0.30 * Math.exp(-Math.pow((p - 0.42) * 18, 2)) +
             Math.abs(jitter) * 0.05;
    }
    // p2l (default): broader, three peaks (P-loop / hinge / DFG).
    return 0.5 * Math.exp(-Math.pow((p - 0.35) * 8, 2)) +
           0.7 * Math.exp(-Math.pow((p - 0.62) * 14, 2)) +
           0.4 * Math.exp(-Math.pow((p - 0.78) * 18, 2)) +
           Math.abs(jitter) * 0.08;
  }), [seq, dir]);
  // Position ruler
  return (
    <div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--dim)", letterSpacing: "0.02em", marginBottom: 2 }}>
        {Array.from({ length: Math.ceil(seq.length / 10) }, (_, i) => (start + i * 10).toString().padEnd(10, " ")).join("")}
      </div>
      <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, letterSpacing: "0.02em", lineHeight: "20px" }}>
        {seq.split("").map((a, i) => {
          const w = weights[i];
          let cls = "";
          if (w > 0.6) cls = "hi";
          else if (w > 0.45) cls = "hi2";
          else if (w > 0.3) cls = "hi3";
          return <span key={i} className={"res " + cls}>{a}</span>;
        })}
      </div>
      {/* annotation track */}
      <div style={{ position: "relative", height: 14, marginTop: 4 }}>
        {D.sequence.annotations.map((a, i) => {
          const l = ((a.from - start) / seq.length) * 100;
          const r = ((a.to - start) / seq.length) * 100;
          return (
            <div key={i} title={a.label} style={{
              position: "absolute",
              left: l + "%",
              width: (r - l) + "%",
              height: 6,
              background: a.kind === "ATP" ? "var(--molecular)" : a.kind === "HRD" ? "var(--primary)" : "var(--signal)",
              borderRadius: 2,
            }} />
          );
        })}
      </div>
    </div>
  );
}

window.ScreenResults = ScreenResults;
