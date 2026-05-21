// ProteoSphere — Workspace home

function ScreenHome({ setCurrent, pushToast }) {
  const D = window.PS_DATA;
  const [recentState, setRecentState] = React.useState("ok"); // demo: cycle through ok | loading | empty | error
  const [staleDismissed, setStaleDismissed] = React.useState(false);
  const [stalePinned, setStalePinned] = React.useState(false);
  const cycle = () => setRecentState(s => ({ ok: "loading", loading: "empty", empty: "error", error: "ok" }[s]));
  const toast = pushToast || window.pushToast;

  // Live user identity for the greeting. Falls back to "Welcome back" if
  // the /api/v2/system/user fetch hasn't finished yet — we never show a
  // hardcoded fictional name.
  const [liveUser, setLiveUser] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_USER) || null
  );
  React.useEffect(() => {
    if (liveUser) return;
    fetch("/api/v2/system/user").then(r => r.ok ? r.json() : null).then(j => {
      if (j) { window.PS_LIVE_USER = j; setLiveUser(j); }
    }).catch(() => {});
  }, [liveUser]);
  const greetName = liveUser ? (liveUser.name?.split(" ")[0] || liveUser.handle) : null;
  const labLabel  = (liveUser?.lab || "PROTEOSPHERE WORKSPACE").toUpperCase();

  return (
    <div className="screen" data-screen-label="01 Home">
      {!staleDismissed && !stalePinned && (
        <StaleBanner
          data={D.staleBanner}
          onPin={() => {
            setStalePinned(true);
            toast({ title: "Pinned to v2026.05", body: "Project ds_kc3_v3 now tracks the latest warehouse release. New embeddings will be cached on first use.", level: "ok" });
          }}
          onDismiss={() => {
            setStaleDismissed(true);
            toast({ title: "Staying on v2026.04", body: "Dataset stays pinned. We'll remind you when v2026.06 lands.", level: "info" });
          }}
        />
      )}

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 24 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>{labLabel}</div>
          <h1>{greetName ? `Welcome back, ${greetName}.` : "Welcome back."}</h1>
          <p className="lead">One run training, four queued. Reference library refreshed 2 days ago — 12 sources current.</p>
        </div>
        <div style={{ flex: 1 }} />
        <button className="btn primary" onClick={() => setCurrent("dataset")}>
          <Ico name="plus" /> New run
        </button>
        <button className="btn" onClick={() => {
          toast({ title: "Import dataset", body: "Would open a CSV / Parquet uploader. Drop the file in the Dataset screen's expert mode → SQL editor to point at your own warehouse table.", level: "info" });
          setCurrent("dataset");
        }}>
          <Ico name="upload" /> Import dataset
        </button>
      </div>

      {/* Top stat strip */}
      <div className="card" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", padding: 0, marginBottom: 20 }}>
        {[
          { k: "Reference library", v: fmt.short(D.warehouse.proteins) + " proteins", sub: "12 sources · refreshed 2d ago" },
          { k: "Active assays",    v: fmt.short(D.warehouse.protein_ligand_edges) + " edges", sub: "BindingDB + ChEMBL + PDBbind" },
          { k: "Leakage groups",   v: D.warehouse.leakage_groups, sub: "identified across pockets" },
          { k: "Models in registry", v: 14, sub: "1 promoted to prod" },
          { k: "Compute budget",   v: "$214 / $500", sub: "this month" },
        ].map((s, i) => (
          <div key={i} style={{ padding: 18, borderRight: i < 4 ? "1px solid var(--border)" : "none" }}>
            <div className="stat">
              <div className="k">{s.k}</div>
              <div className="v">{s.v}</div>
              <div style={{ fontSize: 11, color: "var(--dim)", marginTop: 4, fontFamily: "var(--font-mono)" }}>{s.sub}</div>
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 20 }}>
        {/* Left column — live run + recent runs */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Active run */}
          <div className="card elevated">
            <div className="card-h">
              <Chip tone="signal" dot>training</Chip>
              <span className="t">{D.run.id}</span>
              <span className="sub">· KinaseCore-v3 — cross-attention DTA · ESM-2 + MolFormer</span>
              <div style={{ flex: 1 }} />
              <button className="btn sm ghost" onClick={() => toast({ title: "Run paused (mock)", body: "Would issue a SIGUSR1 to the trainer; checkpoints flush, optimiser state snapshot, resume picks up at this epoch.", level: "info" })}>
                <Ico name="pause" size={12} /> Pause
              </button>
              <button className="btn sm" onClick={() => setCurrent("training")}>Open <Ico name="chevR" size={12} /></button>
            </div>
            <div className="card-b" style={{ display: "grid", gridTemplateColumns: "1fr 200px", gap: 18, alignItems: "center" }}>
              <LineChart
                width={520} height={140}
                series={[
                  { data: D.training.epochs.slice(0, 18), yKey: "val_loss",   color: "var(--primary)",   width: 2, fill: true },
                  { data: D.training.epochs.slice(0, 18), yKey: "train_loss", color: "var(--molecular)", width: 1.6, dash: "3 3" },
                  { data: D.training.baseline.slice(0, 18), yKey: "val_loss", color: "var(--dim)", width: 1.2 },
                ]}
                yMin={0.2} yMax={1.2}
              />
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <Stat k="Epoch" v="18 / 40" mono />
                <Stat k="ETA"   v={D.run.eta} mono />
                <Stat k="Val loss" v={fmt.dec(D.training.epochs[17].val_loss)} mono delta="↓ 12% vs baseline" />
                <Stat k="Val R²"   v={fmt.dec(D.training.epochs[17].val_r2, 3)} mono delta="↑ 0.04" />
                <Stat k="GPU"   v={(() => {
                  const g = window.PS_LIVE_GPU;
                  if (!g) return "—";
                  if (g.available === false) return "CPU only";
                  if (g.available === null)  return "loading…";
                  return (g.device_name || "GPU").replace(/NVIDIA |GeForce | Laptop GPU/g, "");
                })()} mono />
                <Stat k="Spend" v={`${fmt.money(D.run.cost_so_far)} / ${fmt.money(D.run.cost_est_total)}`} mono />
              </div>
            </div>
          </div>

          {/* Recent runs table */}
          <div className="card">
            <div className="card-h">
              <span className="t">Recent runs</span>
              <span className="sub">last 14 days</span>
              <div style={{ flex: 1 }} />
              <button className="btn sm ghost" onClick={cycle} title="Demo: cycle empty / loading / error" style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>
                state: {recentState} <Ico name="chev" size={10} />
              </button>
              <button className="btn sm ghost" onClick={() => setCurrent("compare")}>Compare <Ico name="chevR" size={12} /></button>
            </div>
            {recentState === "ok" && (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Run</th><th>Arch</th><th>Pearson</th><th>RMSE</th><th>Cost</th><th>Started</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {D.runs.slice(0, 6).map(r => (
                    <tr key={r.id}
                      style={{ cursor: "pointer" }}
                      onClick={() => setCurrent(r.state === "training" ? "training" : r.state === "failed" ? "training" : "results")}>
                      <td>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span className={`run-pill`} data-state={r.state} style={{ padding: "2px 8px", fontSize: 10 }}>
                            <span className="dot" /><span>{r.state}</span>
                          </span>
                          <span>{r.name}</span>
                          {r.tag && <Chip tone="primary">{r.tag}</Chip>}
                        </div>
                        <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", marginTop: 2 }}>{r.id}</div>
                      </td>
                      <td className="mono">{r.arch}</td>
                      <td className="mono">{fmt.dec(r.pearson, 3)}</td>
                      <td className="mono">{fmt.dec(r.rmse, 3)}</td>
                      <td className="mono">{fmt.money(r.cost)}</td>
                      <td><span className="muted">{r.started}</span></td>
                      <td>
                        <button type="button" className="btn sm ghost"
                          aria-label={`Open actions for ${r.name}`}
                          onClick={e => {
                            e.stopPropagation();
                            toast({
                              title: `Actions for ${r.name}`,
                              body: "Would open a menu: Open · Compare · Recreate · Archive · Delete. Right-click any row for the same menu.",
                              level: "info",
                            });
                          }}>
                          <Ico name="more" />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {recentState === "loading" && <LoadingSkeleton rows={5} />}
            {recentState === "empty" && (
              <EmptyState
                ico="train"
                title="No runs yet"
                body="Once you launch a training run, it'll show up here with metrics and links."
                cta="Launch a demo run"
                onCta={() => setCurrent("dataset")}
              />
            )}
            {recentState === "error" && (
              <ErrorState
                message="Could not reach the runs service (502). Your local view may be stale."
                errorId="req_3a1f9d"
                onRetry={cycle}
              />
            )}
          </div>
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Resume work */}
          <div className="card">
            <div className="card-h"><span className="t">Resume</span></div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {[
                { ico: "split",   label: "Splits — KinaseCore-v3",  desc: "3 leakage groups still hot · review threshold", target: "split",   state: "warn" },
                { ico: "dataset", label: "Dataset — GPCR-pan-v2",   desc: "Draft · 4 sources selected",                    target: "dataset", state: "" },
                { ico: "results", label: "Results — kc3 ablation",  desc: "Failed run · investigate FGFR1 errors",         target: "results", state: "error" },
              ].map((r, i) => (
                <button key={i} type="button"
                  onClick={() => setCurrent(r.target)}
                  style={{ display: "flex", alignItems: "center", gap: 10, padding: 10, border: "1px solid var(--border)", borderRadius: "var(--r)", cursor: "pointer", textAlign: "left", background: "transparent", color: "var(--text)", font: "inherit", width: "100%" }}>
                  <div style={{ width: 28, height: 28, borderRadius: 6, display: "grid", placeItems: "center", background: "var(--surface-2)", color: "var(--muted)" }}>
                    <Ico name={r.ico} />
                  </div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{ fontSize: 13, color: "var(--text-strong)" }}>{r.label}</div>
                    <div style={{ fontSize: 11, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>{r.desc}</div>
                  </div>
                  {r.state === "warn" && <Chip tone="warn" dot>review</Chip>}
                  {r.state === "error" && <Chip tone="error" dot>failed</Chip>}
                  <Ico name="chevR" style={{ color: "var(--dim)" }} />
                </button>
              ))}
            </div>
          </div>

          {/* Compute budget */}
          <div className="card">
            <div className="card-h"><span className="t">Compute &amp; budget</span><span className="sub">this month</span></div>
            <div className="card-b" style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <Donut value={214} total={500} color="var(--primary)" label="43%" />
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>Spend</span>
                  <span className="mono" style={{ fontSize: 12 }}>$214.30 / $500</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>GPU-hours</span>
                  <span className="mono" style={{ fontSize: 12 }}>118.4 h</span>
                </div>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>Carbon</span>
                  <span className="mono" style={{ fontSize: 12 }}>22.1 kg CO₂e</span>
                </div>
                <hr className="hr" />
                <div style={{ fontSize: 11, color: "var(--dim)", fontFamily: "var(--font-mono)" }}>
                  ↘ 18% vs last month · 4 H100s available
                </div>
              </div>
            </div>
          </div>

          {/* Reference library health */}
          <div className="card">
            <div className="card-h"><span className="t">Reference library</span><span className="sub">12 authoritative sources</span></div>
            <div className="card-b" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {D.sources.slice(0, 8).map(s => (
                <div key={s.id} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12 }}>
                  <span style={{ width: 6, height: 6, borderRadius: 3, background: "var(--signal)" }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{s.name}</span>
                  <span className="mono" style={{ fontSize: 10, color: "var(--dim)" }}>{fmt.short(s.rows)}</span>
                </div>
              ))}
              <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--dim)", fontFamily: "var(--font-mono)", marginTop: 4, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
                + 4 more · all current · last consolidation 04-12 07:39 UTC
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

window.ScreenHome = ScreenHome;
