// ProteoSphere v2 — Promote / review flow
//
// The most-used surface after Training in a lab setting. Reviewer-gated.
// Gates are pass/fail/wait; promotion is disabled until all gates clear.

function ScreenPromote({ setCurrent, pushToast }) {
  const toast = pushToast || window.pushToast;
  const D = window.PS_DATA;
  const p = D.promote;
  const [comment, setComment] = React.useState("");
  const [showCostGuard, setShowCostGuard] = React.useState(false);
  const [comments, setComments] = React.useState(p.comments);
  const [approved, setApproved] = React.useState(false);

  // ── Real backend lifecycle ────────────────────────────────────────
  // The most recent run becomes the default candidate (matches the
  // workflow: train → look at results → click Promote on Results screen).
  const [models, setModels] = React.useState([]);
  const [currentProd, setCurrentProd] = React.useState(null);
  const [candidate, setCandidate] = React.useState(null);
  const [activePromotion, setActivePromotion] = React.useState(null);
  const [decideBusy, setDecideBusy] = React.useState(false);

  const refresh = React.useCallback(() => {
    fetch("/api/v2/registry/models").then(r => r.json()).then(j => {
      const items = j.items || [];
      setModels(items);
      setCurrentProd(j.current_prod || null);
      const candidateId = D?.pipeline?.current_run_id;
      const matching = candidateId
        ? items.find(m => m.run_id === candidateId)
        : items.find(m => m.status !== "promoted");
      setCandidate(matching || null);
    }).catch(() => {});
  }, []);
  React.useEffect(() => { refresh(); }, [refresh]);

  // Fetch / refresh the latest open promotion for the candidate model.
  React.useEffect(() => {
    if (!candidate) { setActivePromotion(null); return; }
    fetch(`/api/v2/registry/models/${encodeURIComponent(candidate.id)}`).then(r => r.json()).then(j => {
      const promotions = j.promotions || [];
      const open = promotions.find(p => p.status === "open");
      setActivePromotion(open || promotions[0] || null);
    }).catch(() => {});
  }, [candidate]);

  const openPromotionRequest = async () => {
    if (!candidate) return;
    try {
      const r = await fetch("/api/v2/registry/promotions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model_id: candidate.id, comment: comment || null }),
      });
      const j = await r.json();
      if (r.ok) {
        setActivePromotion(j.promotion);
        const fails = j.promotion.gates.filter(g => !g.passed && g.severity === "blocker").length;
        toast({
          title: "Promotion request opened",
          body: fails === 0 ? "All blocker gates passing. Ready to approve." : `${fails} blocker gate(s) failing — see the gate list.`,
          level: fails === 0 ? "ok" : "warn",
        });
      } else {
        toast({ title: "Open promotion failed", body: j.detail || j.error || `HTTP ${r.status}`, level: "error" });
      }
    } catch (err) {
      toast({ title: "Network error", body: String(err), level: "error" });
    }
  };

  const decide = async (approveFlag) => {
    if (!activePromotion) return;
    setDecideBusy(true);
    try {
      const r = await fetch(`/api/v2/registry/promotions/${encodeURIComponent(activePromotion.id)}/decide`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approve: approveFlag, actor: "user", note: comment || null }),
      });
      const j = await r.json();
      if (r.ok) {
        setActivePromotion(j.promotion);
        if (j.model) {
          setCandidate(j.model);
        }
        // Refresh the prod pointer if a promotion just succeeded.
        refresh();
        const newStatus = j.promotion.status;
        toast({
          title: newStatus === "promoted" ? "Model promoted to production" : "Promotion rejected",
          body: newStatus === "promoted"
            ? `${j.model.run_id} is now the current prod. Previous prod (if any) was demoted automatically.`
            : "All audit entries written. Open another promotion request to retry.",
          level: newStatus === "promoted" ? "ok" : "warn",
          ttl_ms: 4500,
        });
        setApproved(approveFlag && newStatus === "promoted");
      } else {
        toast({ title: "Decide failed", body: j.detail || j.error || `HTTP ${r.status}`, level: "error" });
      }
    } catch (err) {
      toast({ title: "Network error", body: String(err), level: "error" });
    } finally {
      setDecideBusy(false);
    }
  };

  // Adapt the active promotion's gates into the shape the fixture UI expects.
  const useRealGates = activePromotion && Array.isArray(activePromotion.gates);
  const realGates = useRealGates ? activePromotion.gates.map(g => ({
    id: g.id, label: g.label,
    status: g.passed ? "pass" : (g.severity === "blocker" ? "fail" : "wait"),
    detail: g.detail,
  })) : null;

  // No demo fallback — gate the entire Promote screen on an
  // active promotion request (which itself requires a candidate
  // runId). Earlier the fixture p.gates kept the page populated
  // even when there was nothing to promote.
  if (!useRealGates) {
    return (
      <div className="screen" data-screen-label="08 Promote">
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
          <h2 style={{ marginTop: 0, marginBottom: 6 }}>No promotion request open</h2>
          <p className="lead" style={{ maxWidth: 560, margin: "0 auto 18px" }}>
            A candidate model + open promotion request is required before
            gates can be evaluated. Train a run on the Pipeline screen,
            then open a promotion request from the Compare screen to start
            the review.
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
            <button type="button" className="btn"
              onClick={() => setCurrent("compare")}>
              Go to Compare <Ico name="chevR" size={11} />
            </button>
            <button type="button" className="btn primary"
              onClick={() => setCurrent("pipeline")}>
              Go to Pipeline <Ico name="chevR" size={11} />
            </button>
          </div>
        </div>
      </div>
    );
  }
  const gatesForUI = realGates;
  const passed = gatesForUI.filter(g => g.status === "pass").length;
  const failed = gatesForUI.filter(g => g.status === "fail").length;
  const waiting = gatesForUI.filter(g => g.status === "wait").length;
  const canPromote = activePromotion?.status === "open" && failed === 0;

  return (
    <div className="screen" data-screen-label="08 Promote">
      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>PROMOTE · KINASECORE-V3</div>
          <h2>Send a candidate model to production</h2>
          <p className="lead" style={{ marginTop: 4 }}>
            A new prod model needs <Term word="leakage">leakage</Term>-clear splits, reviewer sign-off, and every gate to pass.
            Nothing here happens silently — the audit log records every promotion and demotion.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn ghost"
          onClick={() => {
            const link = `${window.location.origin}/v2/?promotion=${p.id}`;
            navigator.clipboard?.writeText(link);
            toast({ title: "Review link copied", body: `Anyone with the link can open this review (read-only). ${link}`, level: "ok" });
          }}>
          <Ico name="link" size={12} /> Share review link
        </button>
        {/* Real mode: button reflects promotion lifecycle.
            No active promotion → "Open promotion request"
            Open + can promote      → "Approve & promote"
            Open + can't promote    → disabled with "Blocked by gates"
            Promoted                → "View prod" toast
            Rejected                → "Re-open promotion request" */}
        {!useRealGates ? (
          <button
            className={canPromote ? "btn primary" : "btn"}
            aria-disabled={!canPromote}
            style={{ opacity: canPromote ? 1 : 0.5, cursor: canPromote ? "pointer" : "not-allowed" }}
            onClick={() => canPromote && setShowCostGuard(true)}
          >
            <Ico name="flag" /> Promote to prod
          </button>
        ) : !activePromotion ? (
          <button className="btn primary" disabled={!candidate} onClick={openPromotionRequest}>
            <Ico name="flag" /> Open promotion request
          </button>
        ) : activePromotion.status === "open" ? (
          <>
            <button className="btn ghost" disabled={decideBusy}
              onClick={() => decide(false)}>
              Reject
            </button>
            <button
              className={canPromote ? "btn primary" : "btn"}
              aria-disabled={!canPromote || decideBusy}
              style={{ opacity: canPromote ? 1 : 0.5, cursor: canPromote ? "pointer" : "not-allowed" }}
              onClick={() => canPromote && decide(true)}
              title={canPromote ? "Approve and promote to production" : `${failed} blocker gate(s) failing`}>
              <Ico name="flag" /> {canPromote ? "Approve & promote" : "Blocked by gates"}
            </button>
          </>
        ) : activePromotion.status === "promoted" ? (
          <button className="btn primary" onClick={() => toast({ title: "Already promoted", body: `${candidate?.run_id} is current prod.`, level: "info" })}>
            <Ico name="check" size={12} /> Promoted to prod
          </button>
        ) : (
          <button className="btn primary" onClick={openPromotionRequest}>
            <Ico name="flag" /> Re-open promotion request
          </button>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 20 }}>
        {/* Left column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Candidate vs current — real metrics when a model is registered. */}
          <div className="card">
            <div className="card-h">
              <span className="t">Candidate vs current production</span>
              {!candidate && useRealGates !== null && (
                <span className="sub">No candidate yet — train a run to register one.</span>
              )}
            </div>
            <div style={{ padding: 16, display: "grid", gridTemplateColumns: "1fr 24px 1fr", alignItems: "stretch", gap: 0 }}>
              {/* Candidate */}
              <div style={{ padding: 14, background: "var(--primary-soft)", borderRadius: "var(--r)", border: "1px solid var(--primary)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                  <Chip tone="primary" dot>candidate</Chip>
                  <span className="mono" style={{ fontSize: 12, color: "var(--text-strong)" }}>
                    {candidate ? candidate.run_id : p.candidate_run}
                  </span>
                </div>
                <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-strong)" }}>
                  {candidate ? (candidate.template_label || candidate.template_id) : "KinaseCore-v3"}
                </div>
                <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                  {candidate
                    ? `${(candidate.metrics?.n_params || 0) / 1e6 | 0}M params · Davis warm-split · ${candidate.metrics?.n_test || "?"} test`
                    : "cross-attn · esm2-650m · molformer"}
                </div>
                <hr className="hr" />
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                  {candidate ? (
                    <>
                      <Stat k="Pearson" v={(candidate.metrics?.test_pearson ?? 0).toFixed(3)} mono />
                      <Stat k="RMSE"    v={(candidate.metrics?.test_rmse    ?? 0).toFixed(3)} mono />
                      <Stat k="CI"      v={(candidate.metrics?.test_ci      ?? 0).toFixed(3)} mono />
                      <Stat k="AUC@6"   v={(candidate.metrics?.test_auc_pki6 ?? 0).toFixed(3)} mono />
                    </>
                  ) : (
                    <>
                      <Stat k="Pearson" v="0.872" mono delta="↑ 0.018" />
                      <Stat k="RMSE"    v="0.612" mono delta="↓ 0.029" />
                      <Stat k="AUC@6"   v="0.918" mono delta="↑ 0.014" />
                      <Stat k="ECE"     v="0.041" mono delta="↑ 0.005" deltaNeg />
                    </>
                  )}
                </div>
              </div>
              <div style={{ display: "grid", placeItems: "center", color: "var(--dim)" }}>
                <Ico name="arrowR" />
              </div>
              {/* Current prod (or "no prod yet") */}
              <div style={{ padding: 14, background: "var(--surface-2)", borderRadius: "var(--r)", border: "1px solid var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                  <Chip>{currentProd ? "current prod" : "no current prod"}</Chip>
                  <span className="mono" style={{ fontSize: 12, color: "var(--text-strong)" }}>
                    {currentProd ? currentProd.run_id : (candidate ? "—" : p.current_prod)}
                  </span>
                </div>
                {currentProd ? (
                  <>
                    <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-strong)" }}>
                      {currentProd.template_label || currentProd.template_id}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                      promoted{currentProd.created_at ? ` · ${new Date(currentProd.created_at * 1000).toLocaleDateString()}` : ""}
                    </div>
                    <hr className="hr" />
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <Stat k="Pearson" v={(currentProd.metrics?.test_pearson ?? 0).toFixed(3)} mono />
                      <Stat k="RMSE"    v={(currentProd.metrics?.test_rmse    ?? 0).toFixed(3)} mono />
                      <Stat k="CI"      v={(currentProd.metrics?.test_ci      ?? 0).toFixed(3)} mono />
                      <Stat k="AUC@6"   v={(currentProd.metrics?.test_auc_pki6 ?? 0).toFixed(3)} mono />
                    </div>
                  </>
                ) : candidate ? (
                  <>
                    <div style={{ fontSize: 13, color: "var(--muted)", lineHeight: 1.55, marginTop: 8 }}>
                      No model has been promoted yet. The "beats current prod" blocker gate is bypassed for the first promotion.
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: 14, fontWeight: 500, color: "var(--text-strong)" }}>KinaseCore-v2</div>
                    <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>cross-attn · esm2-650m · ecfp4</div>
                    <hr className="hr" />
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <Stat k="Pearson" v="0.854" mono />
                      <Stat k="RMSE"    v="0.641" mono />
                      <Stat k="AUC@6"   v="0.904" mono />
                      <Stat k="ECE"     v="0.036" mono />
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Gates */}
          <div className="card" data-field="promote.gates">
            <div className="card-h">
              <span className="t">Promotion gates</span>
              <span className="sub">all must pass before "Promote to prod" unlocks</span>
              <div style={{ flex: 1 }} />
              <Chip tone="signal" dot>{passed} pass</Chip>
              {waiting > 0 && <Chip tone="warn" dot>{waiting} waiting</Chip>}
              {failed > 0  && <Chip tone="error" dot>{failed} failing</Chip>}
            </div>
            <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 4 }}>
              {gatesForUI.map(g => (
                <div key={g.id} className="gate" data-status={g.status}>
                  <div className="icon"><Ico name={g.status === "pass" ? "check" : g.status === "fail" ? "warn" : "clock"} size={10} /></div>
                  <div className="lbl">{g.label}</div>
                  <div className="det">{g.detail}</div>
                  {g.status === "fail" && !useRealGates && (
                    <button className="btn sm ghost" onClick={() => { setCurrent && setCurrent(g.id === "g6" ? "pipeline" : "results"); }}>
                      Resolve <Ico name="chevR" size={10} />
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Reviewers */}
          <div className="card">
            <div className="card-h">
              <span className="t">Reviewers</span>
              <span className="sub">3 of 4 actions received</span>
              <div style={{ flex: 1 }} />
              <button type="button" className="btn sm ghost"
                onClick={() => toast({
                  title: "Request reviewers",
                  body: "Would open a picker of project members + reviewers from the policy. Adds them to the approver roster and emails the review link.",
                  level: "info",
                })}>
                <Ico name="plus" size={12} /> Request more reviewers
              </button>
            </div>
            <div style={{ padding: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
              {p.reviewers.map(r => {
                // If the current user (assumed to be the first "reviewing"
                // reviewer for this prototype) has clicked Approve below,
                // promote their status here too — the roster mirrors it.
                const effectiveStatus = (approved && r.status === "reviewing") ? "approved" : r.status;
                return (
                <div key={r.id} className="rev">
                  <div className="avatar" style={{ background: "linear-gradient(135deg, var(--molecular), var(--primary))" }}>{r.avatar}</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-strong)" }}>{r.name}</span>
                    </div>
                    <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{r.role} · {approved && r.status === "reviewing" ? "just now" : r.when}</div>
                  </div>
                  {effectiveStatus === "approved"          && <Chip tone="signal" dot>approved</Chip>}
                  {effectiveStatus === "reviewing"         && <Chip tone="primary" dot>reviewing</Chip>}
                  {effectiveStatus === "requested"         && <Chip tone="warn">requested</Chip>}
                  {effectiveStatus === "changes-requested" && <Chip tone="error" dot>changes</Chip>}
                </div>
                );
              })}
            </div>
          </div>

          {/* Comments */}
          <div className="card">
            <div className="card-h"><span className="t">Comments</span><span className="sub">threaded by artifact · {p.comments.length} entries</span></div>
            <div style={{ padding: 14 }}>
              {comments.map((c, i) => (
                <div key={i} className="comment" style={c.flag === "changes-requested" ? { borderLeft: "3px solid var(--error)" } : c.flag === "request" ? { borderLeft: "3px solid var(--primary)" } : c.flag === "approved" ? { borderLeft: "3px solid var(--signal)" } : {}}>
                  <div className="h">
                    <span className="who">{c.who}</span>
                    <span className="when">{c.when}</span>
                    {c.flag === "changes-requested" && <Chip tone="error" dot>changes</Chip>}
                    {c.flag === "request"           && <Chip tone="primary" dot>request</Chip>}
                    {c.flag === "approved"          && <Chip tone="signal" dot>approved</Chip>}
                  </div>
                  <div className="text">{c.text}</div>
                </div>
              ))}
              <form style={{ display: "flex", gap: 8, marginTop: 10 }}
                onSubmit={(e) => { e.preventDefault(); }}>
                <label htmlFor="promote-comment" className="visually-hidden">Comment</label>
                <input id="promote-comment" className="input" placeholder="Reply…" value={comment} onChange={e => setComment(e.target.value)} style={{ flex: 1 }} />
                <button type="button" className="btn" disabled={!comment}
                  onClick={() => {
                    setComments(prev => [...prev, { who: "rosa.kw", when: "just now", text: comment, flag: null }]);
                    toast({ title: "Comment posted", body: "Visible to all reviewers; pings the roster.", level: "ok", ttl_ms: 2400 });
                    setComment("");
                  }}>Comment</button>
                <button type="button" className={"btn primary" + (approved ? " disabled" : "")} disabled={!comment || approved}
                  onClick={() => {
                    setComments(prev => [...prev, { who: "rosa.kw", when: "just now", text: comment, flag: "approved" }]);
                    setApproved(true);
                    toast({ title: "Approved", body: "Approval recorded. The audit log shows reviewer + timestamp + comment hash.", level: "ok" });
                    setComment("");
                  }}>{approved ? "Approved" : "Approve"}</button>
              </form>
            </div>
          </div>
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 16, alignSelf: "flex-start" }}>
          <div className="card elevated">
            <div className="card-h"><span className="t">Promotion readiness</span></div>
            <div className="card-b">
              <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
                <Donut value={passed} total={p.gates.length} color={canPromote ? "var(--signal)" : "var(--warn)"} label={`${passed}/${p.gates.length}`} />
                <div>
                  <div style={{ fontSize: 14, color: "var(--text-strong)", fontWeight: 500 }}>{canPromote ? "Ready to ship" : "Not ready yet"}</div>
                  <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", marginTop: 2 }}>
                    {failed + waiting === 0
                      ? "All gates pass · all reviewers signed off"
                      : `${failed} failing · ${waiting} waiting`}
                  </div>
                </div>
              </div>
              <hr className="hr" />
              <div style={{ display: "flex", flexDirection: "column", gap: 8, fontSize: 12 }}>
                <PreCheck label="Splits frozen" state="ok" detail="cluster · seed 4192" />
                <PreCheck label="No data-error tags" state="ok" detail="cleared 11d ago" />
                <PreCheck label="Per-target RMSE" state="fail" detail="2 targets over 0.85" />
                <PreCheck label="Bench-biology sign-off" state="warn" detail="Owen R. requested changes" />
              </div>
            </div>
          </div>

          {/* Audit log — real entries from the active promotion when available */}
          <div className="card">
            <div className="card-h">
              <span className="t">Audit log</span>
              <span className="sub">{useRealGates && activePromotion ? `${activePromotion.audit.length} entr${activePromotion.audit.length === 1 ? "y" : "ies"}` : "last 4 events"}</span>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 10, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
              {(useRealGates && activePromotion
                ? activePromotion.audit.map((a, i) => ({
                    ts: new Date(a.at * 1000).toLocaleString(),
                    who: a.actor,
                    action: `${a.event}${a.detail ? " · " + a.detail : ""}`,
                  }))
                : p.audit
              ).map((a, i) => (
                <div key={i} style={{ borderLeft: "2px solid var(--border-strong)", paddingLeft: 10 }}>
                  <div style={{ color: "var(--dim)" }}>{a.ts}</div>
                  <div style={{ color: "var(--text)", marginTop: 2, fontFamily: "var(--font-sans)", fontSize: 12 }}>
                    <span style={{ color: "var(--primary)" }}>{a.who}</span> {a.action}
                  </div>
                </div>
              ))}
              <hr className="hr" />
              <button type="button"
                style={{ background: "transparent", border: 0, padding: 0, color: "var(--primary)", textDecoration: "none", fontFamily: "var(--font-sans)", fontSize: 12, cursor: "pointer" }}
                onClick={() => toast({
                  title: "Full audit log",
                  body: `Would open the immutable, hash-chained audit of this promotion (currently ${p.audit?.length || 12} entries) — every reviewer action, gate evaluation, override.`,
                  level: "info",
                })}>
                View full audit →
              </button>
            </div>
          </div>

          {/* Wet-lab follow-up hook */}
          <div className="card">
            <div className="card-h"><span className="t">Wet-lab follow-up</span><Chip tone="molecular">data hook ready</Chip></div>
            <div className="card-b" style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55 }}>
              Every prediction this model makes can carry an <span className="mono" style={{ color: "var(--text)" }}>experimental_followup</span> record.
              When measured affinities come back, they show up here and feed the next training cycle.
              <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
                0 predictions · 0 measured · UI ships in v3
              </div>
            </div>
          </div>
        </div>
      </div>

      <CostGuardModal
        open={showCostGuard}
        onClose={() => setShowCostGuard(false)}
        onOverride={() => setShowCostGuard(false)}
        breach={{ kind: "promotion-policy", cost: 0, cap: 0 }}
      />
    </div>
  );
}

window.ScreenPromote = ScreenPromote;
