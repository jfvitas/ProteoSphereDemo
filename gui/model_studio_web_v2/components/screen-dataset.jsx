// ProteoSphere — Dataset Builder

// Maps a v2 catalog view name → the fixture source.id it corresponds to.
// Used by the live-overlay below so the Dataset screen can show real
// row counts from /api/v2/ingest/catalog instead of fixture estimates.
const _VIEW_TO_SOURCE = {
  "gtopdb_interactions":   { source_id: "gtopdb",  label: "rows in warehouse" },
  "davis_interactions":    { source_id: "davis",   label: "rows in warehouse" },
  "kiba_interactions":     { source_id: "kiba",    label: "rows in warehouse" },
  "huri_interactions":     { source_id: "huri",    label: "rows in warehouse" },
  "hippie_interactions":   { source_id: "hippie",  label: "rows in warehouse" },
  "s_3did_pdb_observations": { source_id: "3did",  label: "PDB observations" },
  "pdbbind_interactions":  { source_id: "pdbbind", label: "co-crystal complexes" },
  // ChEMBL / BindingDB / PubChem all flow through v2_ligand_smiles_corpus
  // which is a read-only view over the legacy partition.
  "v2_ligand_smiles_corpus": { source_id: null,    label: "legacy SMILES corpus" },
};


function ScreenDataset({ setCurrent, pushToast }) {
  const D = window.PS_DATA;

  // ── Live catalog overlay ─────────────────────────────────────────
  // Pull the actual v2 warehouse catalog once on mount + cache it on
  // window so re-mounts don't re-fetch. The fixture row counts on
  // D.sources are pre-ingestion estimates; this overlay surfaces what's
  // actually queryable right now.
  const [liveCatalog, setLiveCatalog] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_CATALOG) || null
  );
  React.useEffect(() => {
    if (liveCatalog) return;
    fetch("/api/v2/ingest/catalog")
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(j => { window.PS_LIVE_CATALOG = j; setLiveCatalog(j); })
      .catch(() => {});
  }, [liveCatalog]);

  // Build a {source_id → live_rows} overlay so the in-row badge can
  // show what's actually in the warehouse for each source.
  const liveSourceRows = React.useMemo(() => {
    if (!liveCatalog?.live_row_counts) return null;
    const out = {};
    for (const [view, n] of Object.entries(liveCatalog.live_row_counts)) {
      const map = _VIEW_TO_SOURCE[view];
      if (!map || !map.source_id) continue;
      out[map.source_id] = { rows: n, label: map.label };
    }
    return out;
  }, [liveCatalog]);
  // The two orthogonal axes that scope every downstream control:
  //   partners ⊆ {pl, pp, pna}      — what's interacting
  //   tasks    ⊆ {affinity, interaction, unsupervised}  — what the model predicts
  // Multi-select. Affinity-related filters appear iff `affinity` is in `tasks`.
  const [partners, setPartners] = React.useState(new Set(D.binding_partners?.size ? D.binding_partners : ["pl"]));
  const [tasks,    setTasks]    = React.useState(new Set(D.binding_tasks?.size    ? D.binding_tasks    : ["affinity"]));
  // Persist to PS_DATA so other screens (Splits → merge mode default) can read them.
  React.useEffect(() => { D.binding_partners = new Set(partners); }, [partners]);
  React.useEffect(() => { D.binding_tasks    = new Set(tasks);    }, [tasks]);
  // Derived: is the affinity-side panel relevant at all?
  const hasAffinity    = tasks.has("affinity");
  const hasInteraction = tasks.has("interaction");
  const hasUnsup       = tasks.has("unsupervised");
  // Helper for the cascade disable styling.
  const disabledStyle = (when, reason) => when ? {
    opacity: 0.45, pointerEvents: "none", filter: "saturate(0.4)",
    "data-disabled-reason": reason,
  } : {};

  // A source is *eligible* if its partners ∩ chosen partners is non-empty AND
  // its tasks ∩ chosen tasks is non-empty. Eligibility is independent of
  // integration status — a planned source is still eligible, just not selectable.
  const eligible = (s) => {
    const partOk = (s.partners || []).some(p => partners.has(p));
    const taskOk = (s.tasks || []).some(t => tasks.has(t));
    return partOk && taskOk;
  };
  const allowedSources = React.useMemo(
    () => D.sources.filter(eligible),
    [partners, tasks]);
  // Pick everything currently integrated AND eligible — that's the safe default.
  const defaultSourceIds = React.useMemo(() => {
    return new Set(
      D.sources
        .filter(s => s.status === "integrated" && eligible(s))
        .map(s => s.id)
    );
  }, [partners, tasks]);

  const [selected, setSelected] = React.useState(defaultSourceIds);
  React.useEffect(() => { setSelected(defaultSourceIds); }, [partners, tasks]);

  // Default activity types — ALL on by default. The old "ec50 off" default
  // dropped ~25% of preview rows for no clear reason; users who actively
  // don't want EC50 can uncheck it.
  const [activity, setActivity] = React.useState({ ki: true, kd: true, ic50: true, ec50: true });
  React.useEffect(() => {
    if (!hasAffinity) return;
    // PPI affinity is mostly Kd / ΔΔG; pre-pick Kd only.
    if (partners.has("pp") && !partners.has("pl")) {
      setActivity({ ki: false, kd: true, ic50: false, ec50: false });
    } else {
      setActivity({ ki: true, kd: true, ic50: true, ec50: true });
    }
  }, [hasAffinity, partners.has("pp"), partners.has("pl")]);

  // Helper to toggle a Set value.
  const toggleSet = (set, value) => {
    const ns = new Set(set);
    ns.has(value) ? ns.delete(value) : ns.add(value);
    return ns;
  };
  // Forbid empty selections — at least one partner and one task must stay on.
  const togglePartner = (id) => {
    const ns = toggleSet(partners, id);
    if (ns.size === 0) return;
    setPartners(ns);
  };
  const toggleTask = (id) => {
    const ns = toggleSet(tasks, id);
    if (ns.size === 0) return;
    setTasks(ns);
  };

  // Organism — multi-select with "select all".
  const [organisms, setOrganisms] = React.useState(new Set(["human", "mouse"]));

  // Target representation + temperature handling.
  const [targetRep, setTargetRep] = React.useState("pki");
  const [tempPolicy, setTempPolicy] = React.useState("as_reported");

  // Structure-fetch policy (NOT "PDB only / Any / PDB or AF" — that was the
  // misleading version that implied "missing structure" was a hard drop).
  // The real choice is about whether we lazy-fetch and whether we keep the
  // file on disk after example tensors are written.
  const [structPolicy, setStructPolicy] = React.useState("fetch_and_cache");

  const [previewOpen, setPreviewOpen] = React.useState(true);
  const [dropTab, setDropTab] = React.useState("by_reason");
  const [mode, setMode] = React.useState("guided");      // guided | sql
  // Default to "All families" — narrowing to a single Pfam family shrinks
  // protein-cluster diversity, which worsens leakage control and harms
  // generalization. Specialist-model users can still narrow explicitly.
  const [targetFamily, setTargetFamily] = React.useState("All protein families (no restriction)");
  // Numeric filters — defaults tuned to MAXIMISE candidate survival.
  // The old defaults (pKi 5-11.5, conf ≥ 6, QED ≥ 0.4) were dropping
  // ~70% of typical PREVIEW rows before any source filter even ran.
  // The new defaults match the actual measurement ranges in Davis/KIBA/
  // BindingDB and the QED distribution across drug-like compound space.
  const [pkiRange, setPkiRange] = React.useState([3.0, 13.0]);     // Davis pKd 5–10.8, KIBA 1.1–17.2
  const [minConfidence, setMinConfidence] = React.useState(4);     // ChEMBL conf 4 = "Direct binding" (the usable floor)
  const [qedRange, setQedRange] = React.useState([0.2, 1.0]);      // Drug-like floor; 0.4 was excluding legitimate fragments
  const toast = pushToast || window.pushToast;

  // ── Live preview rows ──────────────────────────────────────────────
  // The hardcoded fixture in render() lifted into a stable array so we
  // can filter against the live knobs. Each row carries the numeric
  // affinity in nM so the pKi range filter can apply (pKi = -log10[Ki M]).
  const PREVIEW_ROWS = React.useMemo(() => ([
    { target: "EGFR (P00533)",  ligand: "Erlotinib",    activity: "Ki",   nM: 8.92,  source: "BindingDB", conf: 8, year: 2018, qed: 0.61, family: "kinases" },
    { target: "BTK (Q06187)",   ligand: "Ibrutinib",    activity: "Kd",   nM: 0.50,  source: "BindingDB", conf: 9, year: 2013, qed: 0.51, family: "kinases" },
    { target: "JAK2 (O60674)",  ligand: "Ruxolitinib",  activity: "Ki",   nM: 2.80,  source: "ChEMBL",    conf: 7, year: 2019, qed: 0.72, family: "kinases" },
    { target: "ABL1 (P00519)",  ligand: "Imatinib",     activity: "Ki",   nM: 37.0,  source: "ChEMBL",    conf: 9, year: 2002, qed: 0.49, family: "kinases" },
    { target: "MAPK1 (P28482)", ligand: "SCH-772984",   activity: "IC50", nM: 20.0,  source: "BindingDB", conf: 7, year: 2013, qed: 0.55, family: "kinases" },
    { target: "BRAF (P15056)",  ligand: "Vemurafenib",  activity: "Ki",   nM: 31.0,  source: "ChEMBL",    conf: 8, year: 2010, qed: 0.41, family: "kinases" },
    { target: "FLT3 (P36888)",  ligand: "Gilteritinib", activity: "Kd",   nM: 0.29,  source: "PDBbind",   conf: 9, year: 2020, qed: 0.58, family: "kinases" },
    { target: "FGFR1 (P11362)", ligand: "Erdafitinib",  activity: "Ki",   nM: 1.20,  source: "BindingDB", conf: 8, year: 2020, qed: 0.55, family: "kinases" },
    { target: "AR (P10275)",    ligand: "Enzalutamide", activity: "IC50", nM: 36.0,  source: "ChEMBL",    conf: 7, year: 2012, qed: 0.46, family: "nuclear receptors" },
    { target: "DRD2 (P14416)",  ligand: "Risperidone",  activity: "Ki",   nM: 3.40,  source: "ChEMBL",    conf: 8, year: 2008, qed: 0.65, family: "GPCR" },
    { target: "TP53 (P04637)",  ligand: "MI-77301",     activity: "EC50", nM: 80.0,  source: "ChEMBL",    conf: 6, year: 2017, qed: 0.39, family: "transcription factors" },
  ]), []);
  // Map UI target-family label → preview row's family field. "All" passes
  // every family; any other choice narrows.
  const familyMatch = (rowFam) => {
    if (targetFamily.startsWith("All")) return true;
    if (targetFamily.startsWith("Protein kinase") || targetFamily.startsWith("kinases")) return rowFam === "kinases";
    if (targetFamily.startsWith("Nuclear receptors")) return rowFam === "nuclear receptors";
    if (targetFamily.startsWith("GPCR")) return rowFam === "GPCR";
    return true;
  };
  const familyRestricted = !targetFamily.startsWith("All");
  // Generic source matcher: a preview row's source string (e.g. "BindingDB",
  // "ChEMBL") matches if at least one selected source's display name is a
  // case-insensitive substring of the row's source.
  const sourceMatch = (rowSrc) => {
    const k = rowSrc.toLowerCase();
    for (const id of selected) {
      const src = D.sources.find(s => s.id === id);
      if (!src) continue;
      const n = src.name.toLowerCase().split(/[\s\/]/)[0];
      if (k.includes(n)) return true;
    }
    return false;
  };
  const filteredPreview = PREVIEW_ROWS.filter(r => {
    // Affinity filters only apply when the user has picked an Affinity task.
    if (hasAffinity) {
      if (!activity[r.activity.toLowerCase()]) return false;
      const pKi = -Math.log10(r.nM * 1e-9);
      if (pKi < pkiRange[0] || pKi > pkiRange[1]) return false;
      if (r.conf < minConfidence) return false;
    }
    if (r.qed < qedRange[0] || r.qed > qedRange[1]) return false;
    if (!familyMatch(r.family)) return false;
    if (!sourceMatch(r.source)) return false;
    return true;
  });

  // ── Live attribution: per-filter survival + drop attribution ────────
  // Computes, for each filter, what fraction of rows would survive it
  // GIVEN the other filters are also in place. Sums the drops by reason
  // so the breakdown panel can show how many rows each filter is killing.
  //
  // Total candidates base: prefer the LIVE warehouse row counts for the
  // sources the user actually picked. Falls back to the fixture's picked
  // count when the live catalog isn't loaded yet.
  const baseCandidates = React.useMemo(() => {
    if (liveSourceRows) {
      let total = 0;
      for (const id of selected) {
        const live = liveSourceRows[id];
        const src = D.sources.find(s => s.id === id);
        if (live)        total += live.rows;
        else if (src)    total += src.picked || src.rows || 0;
      }
      return total;
    }
    return D.sources.filter(s => selected.has(s.id))
      .reduce((a, b) => a + (b.picked || 0), 0);
  }, [selected, liveSourceRows]);

  // Each filter answers: "out of the PREVIEW rows from SELECTED sources,
  // what fraction passes me?". Sequential attribution: walk left-to-right,
  // each filter operates on rows that survived earlier ones. Sums add up
  // cleanly (no double-counting when multiple filters reject the same row).
  //
  // CRITICAL: `baseCandidates` already excludes deselected sources (it's
  // a sum over `selected`). So we MUST sample the preview frame from the
  // selected sources only — otherwise PREVIEW rows from deselected sources
  // would contribute fake `source_excluded` drops on top of being absent
  // from baseCandidates in the first place. (User report, 2026-05-17:
  // selecting one source produced source_excluded = 9×scale that exceeded
  // baseCandidates.)
  const survival = React.useMemo(() => {
    const sourceFilteredPreview = PREVIEW_ROWS.filter(r => sourceMatch(r.source));
    let live = sourceFilteredPreview.slice();
    const dropByReason = {
      assay_quality:   0,   // confidence filter
      activity_range:  0,   // activity-type + pKi range filters
      drug_likeness:   0,   // QED range filter (PL only)
      family_mismatch: 0,   // target family filter
      organism_mismatch: 0,
      missing_structure: 0, // only when must_be_local
      redundancy:      0,   // applied as a flat dedup factor after filters
    };
    // Assay confidence + activity range (affinity only)
    if (hasAffinity) {
      let before = live.length;
      live = live.filter(r => r.conf >= minConfidence);
      dropByReason.assay_quality = before - live.length;

      before = live.length;
      live = live.filter(r => activity[r.activity.toLowerCase()]);
      dropByReason.activity_range += before - live.length;

      before = live.length;
      live = live.filter(r => {
        const pKi = -Math.log10(r.nM * 1e-9);
        return pKi >= pkiRange[0] && pKi <= pkiRange[1];
      });
      dropByReason.activity_range += before - live.length;
    }
    // QED — only relevant when ligands in the pair (P-L plus a task).
    if (partners.has("pl") && (hasAffinity || hasInteraction)) {
      const before = live.length;
      live = live.filter(r => r.qed >= qedRange[0] && r.qed <= qedRange[1]);
      dropByReason.drug_likeness = before - live.length;
    }
    // Target family filter
    {
      const before = live.length;
      live = live.filter(r => familyMatch(r.family));
      dropByReason.family_mismatch = before - live.length;
    }
    // Organism filter — every preview row is human, so the count is
    // either 0 (human in set) or all-dropped (human excluded).
    {
      const before = live.length;
      if (!organisms.has("human")) live = [];
      dropByReason.organism_mismatch = before - live.length;
    }
    // Structure policy: when must_be_local, drop a deterministic 38% of
    // the survivors (the no-structure-on-disk fraction).
    if (structPolicy === "must_be_local") {
      const before = live.length;
      live = live.filter((_r, i) => (i * 2654435761 >>> 0) % 100 < 62);
      dropByReason.missing_structure = before - live.length;
    }
    // Survival fraction is computed against the SOURCE-FILTERED preview
    // frame (the rows that came from selected sources). Empty-frame
    // guard so an empty source selection doesn't divide by zero.
    const previewFrame = sourceFilteredPreview.length;
    const totalPassedPreview = live.length;
    const survivalFrac = previewFrame > 0
      ? totalPassedPreview / previewFrame
      : (selected.size > 0 ? 1 : 0);   // no preview samples from this slice — assume neutral

    // Scale per-reason drops to warehouse totals via the same fraction
    // that maps the FILTERED preview frame → baseCandidates. Using the
    // source-filtered frame here is what fixes the double-count bug.
    const scale = previewFrame > 0
      ? baseCandidates / previewFrame
      : 0;
    const scaledDrops = {};
    for (const [k, v] of Object.entries(dropByReason)) scaledDrops[k] = Math.round(v * scale);

    // Redundancy is a flat dedup factor applied after the other filters;
    // we attribute the gap between after-filter survivors and final to
    // this reason so the totals add up to exactly baseCandidates.
    const afterFilters    = Math.round(baseCandidates * survivalFrac);
    const dedupSurvival   = 0.32;   // ~32% of survivors after best-evidence dedup
    const finalSelected   = Math.max(0, Math.round(afterFilters * dedupSurvival));
    scaledDrops.redundancy = Math.max(0, afterFilters - finalSelected);

    // Reconcile: ensure all-drops + final == base
    const sumDrops = Object.values(scaledDrops).reduce((a, b) => a + b, 0);
    const expectedDrops = baseCandidates - finalSelected;
    // Spread any rounding gap into the largest bucket
    if (sumDrops !== expectedDrops) {
      const delta = expectedDrops - sumDrops;
      const biggest = Object.entries(scaledDrops).sort((a, b) => b[1] - a[1])[0];
      if (biggest) scaledDrops[biggest[0]] = Math.max(0, biggest[1] + delta);
    }

    // Per-source attribution — divide kept rows proportionally by the
    // selected sources' live (or picked) row counts.
    const sourcesKept = [];
    if (afterFilters > 0) {
      let remaining = afterFilters;
      const pickedSources = D.sources.filter(s => selected.has(s.id));
      const totalWeight = pickedSources.reduce((a, s) => a + ((liveSourceRows && liveSourceRows[s.id]?.rows) || s.picked || s.rows || 0), 0);
      pickedSources.forEach((s, idx) => {
        const w = (liveSourceRows && liveSourceRows[s.id]?.rows) || s.picked || s.rows || 0;
        const share = totalWeight ? w / totalWeight : 0;
        const kept = idx === pickedSources.length - 1 ? remaining : Math.round(afterFilters * share);
        remaining -= kept;
        sourcesKept.push({ src: s.name, kept, share: 100 * share });
      });
      sourcesKept.sort((a, b) => b.kept - a.kept);
    }

    // Which sample rows would the user see dropped? Only consider rows
    // from SELECTED sources (rows from deselected sources never enter
    // the candidate pool to begin with, so listing them as "dropped"
    // would mislead the user about why their counts shrank).
    const droppedSample = [];
    for (const r of sourceFilteredPreview) {
      const reasons = [];
      if (hasAffinity && r.conf < minConfidence)        reasons.push("assay_quality");
      if (hasAffinity && !activity[r.activity.toLowerCase()]) reasons.push("activity_range");
      if (hasAffinity) {
        const pKi = -Math.log10(r.nM * 1e-9);
        if (pKi < pkiRange[0] || pKi > pkiRange[1])     reasons.push("activity_range");
      }
      if (partners.has("pl") && (hasAffinity || hasInteraction)
          && (r.qed < qedRange[0] || r.qed > qedRange[1])) reasons.push("drug_likeness");
      if (!familyMatch(r.family))                       reasons.push("family_mismatch");
      if (!organisms.has("human"))                      reasons.push("organism_mismatch");
      if (reasons.length) {
        droppedSample.push({
          id: `${r.target.split(" ")[0]} · ${r.ligand}`,
          source: r.source,
          reason: reasons[0],
          detail: ({
            assay_quality:    `Confidence ${r.conf} (need ≥${minConfidence})`,
            activity_range:   `pKi ${(-Math.log10(r.nM*1e-9)).toFixed(2)} or activity ${r.activity} excluded`,
            drug_likeness:    `QED ${r.qed.toFixed(2)} outside [${qedRange[0].toFixed(2)}, ${qedRange[1].toFixed(2)}]`,
            family_mismatch:  `Family ${r.family} not in ${targetFamily}`,
            organism_mismatch:`Organism filter excludes human`,
          }[reasons[0]] || "Multiple filters"),
        });
      }
    }

    return {
      candidates:    baseCandidates,
      afterFilters,
      finalSelected,
      dropByReason:  scaledDrops,
      sourcesKept,
      droppedSample,
      survivalFrac,
    };
  }, [PREVIEW_ROWS, sourceMatch, hasAffinity, hasInteraction, partners, activity,
      pkiRange, minConfidence, qedRange, familyMatch, organisms, structPolicy,
      baseCandidates, liveSourceRows, selected, targetFamily]);

  // Convenience aliases used by render code below.
  const filteredTotals = {
    candidates:     survival.candidates,
    after_filters:  survival.afterFilters,
    final_selected: survival.finalSelected,
  };

  // Identify the most restrictive filter — used in the "what's costing
  // you the most" panel so the user can decide what to loosen.
  const topDropReasons = React.useMemo(() => {
    return Object.entries(survival.dropByReason)
      .filter(([_, n]) => n > 0)
      .sort((a, b) => b[1] - a[1]);
  }, [survival.dropByReason]);

  const totalPicked = D.sources.filter(s => selected.has(s.id)).reduce((a, b) => a + b.picked, 0);

  return (
    <div className="screen" data-screen-label="02 Dataset">
      <StepRail active="dataset" onClick={setCurrent} />
      <LaneBar lane="release" />
      {/* v4 — surface the binding type the user picked on the Goal screen.
          Renders nothing if no binding_type is set (user hasn't visited Goal yet). */}
      {window.PS_DATA.binding_type && <BindingBanner setCurrent={setCurrent} />}

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 01 · DATASET</div>
          <h2>Pick what goes into your training set</h2>
          <p className="lead" style={{ marginTop: 4 }}>
            Choose <Term word="warehouse">authoritative</Term> sources, filter to what you care about,
            then preview what survives. ProteoSphere normalizes units, deduplicates, and flags conflicts before you commit.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        <div className="toggle" role="group" aria-label="Builder mode">
          <button type="button" aria-pressed={mode === "guided"} onClick={() => setMode("guided")}>Guided</button>
          <button type="button" aria-pressed={mode === "sql"} onClick={() => {
            setMode("sql");
            toast({
              title: "Expert SQL mode",
              body: "Would swap the filter card for a Monaco SQL editor scoped to the warehouse. The editor is still in build; for now stick with Guided.",
              level: "warn",
            });
          }}>Expert SQL</button>
        </div>
        <button className="btn primary" onClick={() => setCurrent("split")}>
          Continue to Splits <Ico name="chevR" />
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 360px", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* 1 · What are you studying — two orthogonal multi-selects */}
          <div className="card">
            <div className="card-h">
              <span className="t">1 · What are you studying?</span>
              <span className="sub">pick which pairs you're training on, and what the model should predict. Multi-select both.</span>
            </div>

            <div style={{ padding: 14, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22 }}>
              {/* Partners — what kind of pairs */}
              <div>
                <div className="label">Binding partners {partners.size > 1 && <Chip tone="primary">{partners.size} kinds</Chip>}</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {(window.PS_BINDING_PARTNERS || []).map(p => {
                    const on = partners.has(p.id);
                    return (
                      <button type="button" key={p.id} aria-pressed={on}
                        onClick={() => togglePartner(p.id)}
                        style={{
                          padding: 10, textAlign: "left", cursor: "pointer", font: "inherit", color: "var(--text)",
                          border: `1px solid ${on ? "var(--primary)" : "var(--border)"}`,
                          borderRadius: "var(--r)",
                          background: on ? "var(--primary-soft)" : "var(--surface-2)",
                          display: "flex", alignItems: "center", gap: 10,
                        }}>
                        <div style={{
                          width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                          border: `1.4px solid ${on ? "var(--primary)" : "var(--border-strong)"}`,
                          background: on ? "var(--primary)" : "transparent",
                          display: "grid", placeItems: "center", color: "#021624",
                        }}>{on && <Ico name="check" size={10} />}</div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-strong)", marginBottom: 2 }}>{p.label} <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", fontWeight: 400 }}>{p.short}</span></div>
                          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.4 }}>{p.sub}</div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Task type — what the model predicts */}
              <div>
                <div className="label">Task type {tasks.size > 1 && <Chip tone="primary">{tasks.size} tasks</Chip>}</div>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {(window.PS_TASK_TYPES || []).map(t => {
                    const on = tasks.has(t.id);
                    // Disable affinity if only protein-NA is picked (no Kd data
                    // in the warehouse for that combination yet — see
                    // PS_PARTNER_TASK_SUPPORT). Honestly visible, not hidden.
                    const support = (window.PS_PARTNER_TASK_SUPPORT || {});
                    const supportedHere = Array.from(partners).some(p => support[`${p}_${t.id}`]?.supported);
                    const disabled = !supportedHere;
                    return (
                      <button type="button" key={t.id} aria-pressed={on} disabled={disabled}
                        title={disabled ? `Not yet available for: ${Array.from(partners).join(", ")}` : ""}
                        onClick={() => !disabled && toggleTask(t.id)}
                        style={{
                          padding: 10, textAlign: "left",
                          cursor: disabled ? "not-allowed" : "pointer", font: "inherit",
                          color: disabled ? "var(--dim)" : "var(--text)",
                          opacity: disabled ? 0.5 : 1,
                          border: `1px solid ${on ? "var(--primary)" : "var(--border)"}`,
                          borderRadius: "var(--r)",
                          background: on ? "var(--primary-soft)" : "var(--surface-2)",
                          display: "flex", alignItems: "center", gap: 10,
                        }}>
                        <div style={{
                          width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                          border: `1.4px solid ${on ? "var(--primary)" : "var(--border-strong)"}`,
                          background: on ? "var(--primary)" : "transparent",
                          display: "grid", placeItems: "center", color: "#021624",
                        }}>{on && <Ico name="check" size={10} />}</div>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontSize: 13, fontWeight: 600, color: disabled ? "var(--dim)" : "var(--text-strong)", marginBottom: 2 }}>{t.label}</div>
                          <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.4 }}>{t.sub}</div>
                          {disabled && <div style={{ fontSize: 10, color: "var(--warn)", marginTop: 4, fontFamily: "var(--font-mono)" }}>not available for current partner pick</div>}
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {/* Sources */}
          <div className="card">
            <div className="card-h">
              <span className="t">2 · Sources</span>
              {(() => {
                const integratedCount = allowedSources.filter(s => s.status === "integrated").length;
                const plannedCount    = allowedSources.filter(s => s.status === "planned").length;
                const futureCount     = allowedSources.filter(s => s.status === "future").length;
                return (
                  <span className="sub">
                    {integratedCount} ready to use
                    {plannedCount > 0 && ` · ${plannedCount} planned`}
                    {futureCount > 0 && ` · ${futureCount} future`}
                    {" "}for the current pick
                  </span>
                );
              })()}
              <div style={{ flex: 1 }} />
              <button type="button" className="btn sm ghost"
                onClick={() => setSelected(new Set(allowedSources.filter(s => s.status === "integrated").map(s => s.id)))}>Select all integrated</button>
              <button type="button" className="btn sm ghost"
                onClick={() => setSelected(new Set())}>Clear</button>
              <Chip tone="signal">{selected.size} picked</Chip>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", padding: 12, gap: 8 }}>
              {allowedSources.map(s => {
                const on = selected.has(s.id);
                const integrated = s.status === "integrated";
                const planned    = s.status === "planned";
                const future     = s.status === "future";
                const disabled = !integrated;
                return (
                  <button type="button"
                    key={s.id}
                    aria-pressed={on}
                    disabled={disabled}
                    onClick={() => {
                      if (disabled) return;
                      const ns = new Set(selected);
                      on ? ns.delete(s.id) : ns.add(s.id);
                      setSelected(ns);
                    }}
                    title={disabled
                      ? `${s.name}: ${planned ? "planned for warehouse ingestion" : "future scope"}. Visible so you can plan around it; not selectable yet.`
                      : (s.desc || s.name)}
                    style={{
                      padding: 12, textAlign: "left",
                      cursor: disabled ? "not-allowed" : "pointer",
                      font: "inherit", color: disabled ? "var(--dim)" : "var(--text)",
                      opacity: disabled ? 0.55 : 1,
                      border: `1px ${disabled ? "dashed" : "solid"} ${on ? "var(--primary)" : "var(--border)"}`,
                      borderRadius: "var(--r)",
                      background: on ? "var(--primary-soft)" : "var(--surface-2)",
                      position: "relative",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
                      <div style={{
                        width: 14, height: 14, borderRadius: 3,
                        border: `1.4px solid ${on ? "var(--primary)" : "var(--border-strong)"}`,
                        background: on ? "var(--primary)" : "transparent",
                        display: "grid", placeItems: "center", color: "#021624"
                      }}>
                        {on && <Ico name="check" size={10} />}
                      </div>
                      <span style={{ fontSize: 13, fontWeight: 500, color: disabled ? "var(--dim)" : "var(--text-strong)" }}>{s.name}</span>
                      <span style={{ flex: 1 }} />
                      {planned && <Chip tone="warn">planned</Chip>}
                      {future && <Chip tone="molecular">future</Chip>}
                      {integrated && <Chip>{s.kind}</Chip>}
                    </div>
                    <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.4, marginBottom: 6, minHeight: 28 }}>
                      {s.desc}
                    </div>
                    <div style={{ display: "flex", justifyContent: "space-between", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
                      <span>
                        {fmt.short(s.rows)} rows {disabled && "(in source DB)"}
                        {integrated && liveSourceRows?.[s.id] && (
                          <span style={{ color: "var(--ok)", marginLeft: 6 }} title={liveSourceRows[s.id].label + " (live from v2 catalog)"}>
                            · {fmt.short(liveSourceRows[s.id].rows)} live
                          </span>
                        )}
                      </span>
                      {integrated && <span style={{ color: on ? "var(--primary)" : "var(--dim)" }}>→ {fmt.short(s.picked)} after filters</span>}
                      {planned    && <span style={{ color: "var(--warn)" }}>warehouse ingestion pending</span>}
                      {future     && <span style={{ color: "var(--molecular)" }}>not on near-term roadmap</span>}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Filters */}
          <div className="card">
            <div className="card-h">
              <span className="t">3 · Filters</span>
              <span className="sub">applied to every selected source · changes update the preview live</span>
            </div>
            <div style={{ padding: 16, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22 }}>

              {/* LEFT COLUMN — affinity / measurement filters.
                  Cascade-disabled when the user hasn't asked for Affinity:
                  the whole block is faded + non-interactive, with an inline
                  explainer at the top instead of silently hiding controls. */}
              <div style={{ position: "relative" }}>
                {!hasAffinity && (
                  <div style={{
                    position: "absolute", inset: -6, zIndex: 3,
                    background: "var(--surface)cc", backdropFilter: "blur(1.5px)",
                    border: "1px dashed var(--border-strong)", borderRadius: "var(--r)",
                    padding: 14, display: "flex", flexDirection: "column", gap: 6,
                    alignItems: "flex-start", justifyContent: "flex-start",
                  }}>
                    <Chip tone="warn" dot>not in use</Chip>
                    <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.5, maxWidth: 360 }}>
                      You've picked <strong>{Array.from(tasks).join(" + ")}</strong>, so the model isn't predicting an affinity number.
                      The measurement type, target representation, temperature, binding-strength and assay-confidence filters
                      only matter when <strong>Affinity</strong> is one of the chosen tasks.
                    </div>
                    <button type="button" className="btn sm" onClick={() => toggleTask("affinity")}>
                      Add Affinity task <Ico name="plus" size={11} />
                    </button>
                  </div>
                )}

                <div style={{ filter: hasAffinity ? "none" : "grayscale(0.4)", opacity: hasAffinity ? 1 : 0.35 }}>
                  <div className="label" style={{ marginBottom: 6 }}>
                    Measurement types{" "}
                    {partners.has("pp") && hasAffinity && <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--warn)" }}>PPI: typically Kd / ΔΔG</span>}
                  </div>
                  <div className="taglist" role="group" aria-label="Measurement types">
                    {Object.entries(activity).map(([k, v]) => (
                      <button
                        key={k}
                        type="button"
                        aria-pressed={v}
                        onClick={() => setActivity({ ...activity, [k]: !v })}
                        className={"chip" + (v ? " primary" : "")}
                        title={
                          k === "ki"   ? "Ki — inhibition constant. Equilibrium binding for competitive inhibitors." :
                          k === "kd"   ? "Kd — dissociation constant. Pure binding affinity at equilibrium." :
                          k === "ic50" ? "IC50 — concentration that inhibits 50% of activity. Assay-condition-dependent." :
                                         "EC50 — concentration causing 50% of max effect. Functional, not pure binding."}
                        style={{ cursor: "pointer", border: v ? "1px solid var(--primary)" : "1px solid var(--border)", background: v ? "var(--primary-soft)" : "var(--surface-2)" }}
                      >
                        {k.toUpperCase()}
                      </button>
                    ))}
                  </div>
                  <div className="help">Which kinds of binding numbers to include. Hover any chip for what it means.</div>

                  <div className="label" style={{ marginTop: 18 }}>How the model should report binding</div>
                  <select className="select" value={targetRep} onChange={e => setTargetRep(e.target.value)}>
                    {(window.PS_TARGET_REPRESENTATIONS || []).map(r => (
                      <option key={r.id} value={r.id}>{r.label}</option>
                    ))}
                  </select>
                  <div className="help">
                    {(window.PS_TARGET_REPRESENTATIONS || []).find(r => r.id === targetRep)?.desc}
                  </div>

                  <div className="label" style={{ marginTop: 14 }}>Assay temperature handling</div>
                  <select className="select" value={tempPolicy} onChange={e => setTempPolicy(e.target.value)}>
                    {(window.PS_TEMPERATURE_POLICIES || []).map(p => (
                      <option key={p.id} value={p.id}>{p.label}</option>
                    ))}
                  </select>
                  <div className="help">
                    {(window.PS_TEMPERATURE_POLICIES || []).find(p => p.id === tempPolicy)?.desc}
                  </div>

                  <div className="label" style={{ marginTop: 18 }}>
                    Binding strength range — keep measurements where pKi is between:
                  </div>
                  <RangeSlider min={3} max={12} step={0.1} v0={pkiRange[0]} v1={pkiRange[1]}
                    ariaLabel="pKi range"
                    onChange={([lo, hi]) => setPkiRange([lo, hi])} />
                  <div className="help">
                    pKi {pkiRange[0].toFixed(1)} ≈ {fmtPotency(pkiRange[0])}, pKi {pkiRange[1].toFixed(1)} ≈ {fmtPotency(pkiRange[1])}.
                    Drops anything weaker than the lower bound (probably noise) or stronger than the upper bound (often hitting the assay's detection limit).
                  </div>

                  <div className="label" style={{ marginTop: 18 }}>Minimum trust in the measurement</div>
                  <RangeSlider min={0} max={9} step={1} v0={minConfidence} v1={minConfidence} single
                    ariaLabel="Minimum assay confidence"
                    onChange={([lo]) => setMinConfidence(lo)} />
                  <div className="help">
                    Confidence ≥ {minConfidence} ·{" "}
                    {minConfidence >= 8 ? "direct binding measured against the exact human protein"
                     : minConfidence >= 6 ? "binding against a homologous protein (close-relative target)"
                     : "any measurement, even from a cell or tissue assay"}
                  </div>
                </div>
              </div>

              {/* RIGHT COLUMN — what target / what species / what to do with structures */}
              <div>
                <div className="label">Species (organism)</div>
                <OrganismMultiselect
                  selected={organisms}
                  onChange={setOrganisms}
                  toast={toast}
                />

                <div className="label" style={{ marginTop: 18, display: "flex", alignItems: "center", gap: 6 }}>
                  Target protein family
                  {familyRestricted && <Chip tone="warn" dot>narrowed</Chip>}
                </div>
                <select className="select" value={targetFamily} onChange={e => setTargetFamily(e.target.value)}>
                  <option>All protein families (no restriction)</option>
                  <option>Protein kinase (PF00069) — primary</option>
                  <option>+ ATP-binding cassette transporter</option>
                  <option>+ Bromodomain</option>
                  <option>Nuclear receptors (PF00104)</option>
                  <option>GPCR class A (PF00001)</option>
                  <option>Custom Pfam list…</option>
                </select>
                <div className="help">
                  {familyRestricted ? (
                    <>
                      <strong style={{ color: "var(--warn)" }}>Narrowing reduces protein diversity.</strong>{" "}
                      Restricting to a single <Term word="Pfam">Pfam</Term> family makes leakage harder to control (fewer
                      protein-sequence clusters to spread across train/val/test) and the resulting model only generalises
                      within that family. Only narrow if you're deliberately training a specialist model.
                    </>
                  ) : (
                    <>
                      All families included — recommended. Filters by <Term word="Pfam">Pfam</Term> domain membership when
                      you do narrow.
                    </>
                  )}
                </div>

                <div className="label" style={{ marginTop: 18 }}>
                  Structure files (PDB / mmCIF / AlphaFold)
                </div>
                <select className="select" value={structPolicy} onChange={e => setStructPolicy(e.target.value)}>
                  {(window.PS_STRUCTURE_FETCH_POLICIES || []).map(p => (
                    <option key={p.id} value={p.id}>{p.label}</option>
                  ))}
                </select>
                <div className="help">
                  {(window.PS_STRUCTURE_FETCH_POLICIES || []).find(p => p.id === structPolicy)?.desc}
                  <br/>
                  <span style={{ color: "var(--muted)" }}>
                    Note: if a pair has <strong>no</strong> structure in PDB or AlphaFold, it falls back to sequence-only features automatically — it's never dropped just because the file isn't yet on disk.
                  </span>
                </div>

                {/* QED only applies when ligands are in the pair. When the
                    user's binding-partner selection has no PL, the filter
                    is a no-op — visually disable the section so users
                    aren't misled into thinking dragging the slider does
                    anything. */}
                <div className="label" style={{ marginTop: 18, display: "flex", alignItems: "center", gap: 6 }}>
                  Ligand drug-likeness — keep ligands with <Term word="QED">QED</Term> between:
                  {!partners.has("pl") && <Chip tone="dim">disabled · no ligand axis</Chip>}
                </div>
                <div style={partners.has("pl") ? {} : { opacity: 0.45, pointerEvents: "none", filter: "saturate(0.4)" }}>
                <RangeSlider min={0} max={1} step={0.05} v0={qedRange[0]} v1={qedRange[1]}
                  ariaLabel="QED range"
                  onChange={([lo, hi]) => setQedRange([lo, hi])} />
                <div className="help">
                  QED is a 0–1 score for "how drug-like a molecule looks" (sane size, sane hydrogen-bond counts, sane lipophilicity).
                  Below {qedRange[0].toFixed(2)} usually means a fragment or a probe rather than a viable drug candidate.
                </div>
                </div>{/* close the QED disabled-wrapper */}
              </div>
            </div>
          </div>

          {/* Preview table — sample of kept rows */}
          <div className="card">
            <div className="card-h">
              <span className="t">4 · Preview</span>
              <span className="sub">{fmt.n(filteredTotals.after_filters)} rows match · showing {filteredPreview.length}/{PREVIEW_ROWS.length}</span>
              <div style={{ flex: 1 }} />
              <button type="button" className="btn sm ghost"
                onClick={() => toast({
                  title: "Sampled 10K rows",
                  body: `Would write data/sample_${Date.now()}.csv with 10,000 stratified rows. Honours every live filter: ` +
                    `partners={${Array.from(partners).join(",")}}, tasks={${Array.from(tasks).join(",")}}, ${selected.size}/${allowedSources.length} sources, ` +
                    `activity={${Object.entries(activity).filter(([,v])=>v).map(([k])=>k.toUpperCase()).join(",")}}, ` +
                    `target representation=${targetRep}, temperature=${tempPolicy}, ` +
                    `pKi ${pkiRange[0].toFixed(1)}–${pkiRange[1].toFixed(1)}, confidence ≥ ${minConfidence}, ` +
                    `QED ${qedRange[0].toFixed(2)}–${qedRange[1].toFixed(2)}, family=${targetFamily}, ` +
                    `organisms=${organisms.size}, structure-policy=${structPolicy}.`,
                  level: "ok",
                })}>
                <Ico name="download" /> Sample 10K CSV
              </button>
            </div>
            <div className="scroll" style={{ maxHeight: 320, overflow: "auto" }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Pair</th>
                    <th>{hasAffinity ? "Activity" : "Label"}</th>
                    <th>{hasAffinity
                      ? `Value (${(window.PS_TARGET_REPRESENTATIONS || []).find(r => r.id === targetRep)?.label || "pKi"})`
                      : "Binds?"}</th>
                    <th>Source</th><th>Conf.</th><th>Year</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPreview.length === 0 && (
                    <tr><td colSpan="6" style={{ padding: 18, color: "var(--dim)", fontStyle: "italic", textAlign: "center" }}>
                      Every row dropped by the current filters. Loosen pKi range, lower the minimum assay confidence, or include more sources.
                    </td></tr>
                  )}
                  {filteredPreview.map((r, i) => {
                    // Convert the raw nM measurement to the user's chosen
                    // target representation. pKi = -log10(K in molar), and
                    // ΔG° = -RT · ln(K) at the chosen reference temperature.
                    const K_molar = r.nM * 1e-9;
                    const pKi = -Math.log10(K_molar);
                    // R = 1.987e-3 kcal/(mol·K) = 8.314 J/(mol·K)
                    const T_K = tempPolicy === "normalise_310" ? 310 : 298;
                    // ΔG° = -RT ln(K_a) = -RT ln(1/K_d) = +RT ln(K_d).
                    // For favorable binding (small K_d), ln(K_d) is large
                    // negative, so ΔG° is large negative (favorable). The
                    // previous formula `-RT ln(K_d)` flipped the sign and
                    // displayed +12 kcal/mol for nM-range binders (a real
                    // sign bug; would've optimized AWAY from binding).
                    const dG_kcal = 1.987e-3 * T_K * Math.log(K_molar);   // R in kcal/mol/K
                    const dG_kJ   = 8.314e-3 * T_K * Math.log(K_molar);   // R in kJ/mol/K
                    let display = "";
                    if (!hasAffinity) {
                      // Interaction-only run: show a boolean cell instead.
                      display = pKi >= 6 ? "binds" : "no";
                    } else if (targetRep === "pki")     display = pKi.toFixed(2);
                    else if (targetRep === "pkd")       display = pKi.toFixed(2);
                    else if (targetRep === "pic50")     display = pKi.toFixed(2);
                    else if (targetRep === "dG_kcal")   display = `${dG_kcal.toFixed(2)}`;
                    else if (targetRep === "dG_kJ")     display = `${dG_kJ.toFixed(1)}`;
                    const nM = r.nM < 1 ? `${(r.nM*1000).toFixed(0)} pM` : r.nM < 1000 ? `${r.nM.toFixed(2)} nM` : `${(r.nM/1000).toFixed(1)} µM`;
                    return (
                      <tr key={i}>
                        <td>{r.target} · <span className="muted">{r.ligand}</span></td>
                        <td>{hasAffinity ? <Chip>{r.activity}</Chip> : <Chip tone="signal">interaction</Chip>}</td>
                        <td className="mono" title={`Raw: ${nM} · pKi ${pKi.toFixed(2)} · ΔG° ${dG_kcal.toFixed(2)} kcal/mol at ${T_K}K`}>
                          {hasAffinity ? (
                            <>
                              <span style={{ color: "var(--text-strong)" }}>{display}</span>
                              {(targetRep === "dG_kcal" || targetRep === "dG_kJ") && (
                                <span style={{ marginLeft: 4, color: "var(--dim)" }}>{targetRep === "dG_kcal" ? "kcal/mol" : "kJ/mol"}</span>
                              )}
                              <div style={{ fontSize: 10, color: "var(--dim)" }}>{nM}</div>
                            </>
                          ) : (
                            <span style={{ color: display === "binds" ? "var(--signal)" : "var(--dim)" }}>{display}</span>
                          )}
                        </td>
                        <td className="mono"><span className="muted">{r.source}</span></td>
                        <td className="mono">{r.conf}</td>
                        <td className="mono">{r.year}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>

          {/* Build candidate set — funnel + dropped rows */}
          <div className="card">
            <div className="card-h">
              <span className="t">5 · Build candidate set</span>
              <span className="sub">show what gets dropped, and why · before you commit to a split</span>
              <div style={{ flex: 1 }} />
              <button className="btn sm" onClick={() => setPreviewOpen(!previewOpen)}>
                <Ico name={previewOpen ? "chev" : "chevR"} size={12} /> {previewOpen ? "Hide" : "Show"} preview
              </button>
            </div>
            {previewOpen && (
              <div style={{ padding: 16 }}>
                {/* Funnel — totals are derived live from the active filter set */}
                <div className="funnel">
                  <div className="funnel-step">
                    <span className="k">Total candidates</span>
                    <span className="v">{fmt.n(filteredTotals.candidates)}</span>
                    <span className="d">union of selected sources, before filters</span>
                  </div>
                  <div className="funnel-arrow"><Ico name="arrowR" /></div>
                  <div className="funnel-step">
                    <span className="k">After filters</span>
                    <span className="v">{fmt.n(filteredTotals.after_filters)}</span>
                    <span className="d">−{fmt.n(filteredTotals.candidates - filteredTotals.after_filters)} dropped</span>
                  </div>
                  <div className="funnel-arrow"><Ico name="arrowR" /></div>
                  <div className="funnel-step final">
                    <span className="k">Final selected</span>
                    <span className="v">{fmt.n(filteredTotals.final_selected)}</span>
                    <span className="d">after dedup + best-evidence policy</span>
                  </div>
                </div>

                {/* Top "what's costing me" panel — surfaces the most-restrictive
                    filter + one-click ways to loosen it. */}
                {topDropReasons.length > 0 && filteredTotals.after_filters < filteredTotals.candidates && (
                  <div style={{ marginTop: 12, padding: "10px 14px", background: "var(--surface-3)", borderRadius: 4,
                                borderLeft: "2px solid var(--warn)", display: "flex", alignItems: "center", gap: 12 }}>
                    <Ico name="warn" size={14} />
                    <div style={{ flex: 1, fontSize: 12 }}>
                      <div style={{ color: "var(--text-strong)", marginBottom: 2 }}>
                        Most rows are getting dropped by: <span className="mono">{topDropReasons[0][0].replace(/_/g, " ")}</span> ({fmt.n(topDropReasons[0][1])} rows)
                      </div>
                      <div style={{ color: "var(--muted)" }}>
                        {({
                          assay_quality:     `Lower the minimum confidence below ${minConfidence}.`,
                          activity_range:    `Widen the pKi range or include more activity types (Ki/Kd/IC50/EC50).`,
                          drug_likeness:     `Widen the QED window below ${qedRange[0].toFixed(2)} or above ${qedRange[1].toFixed(2)}.`,
                          family_mismatch:   `Switch target family back to "All protein families".`,
                          organism_mismatch: `Include human in the organism set.`,
                          missing_structure: `Switch the structure policy from must_be_local to fetch_and_cache so structures stream in at training time.`,
                          redundancy:        `This is post-filter dedup (keeping best-evidence row per pair). It's the right kind of drop — keep it.`,
                        })[topDropReasons[0][0]] || "Loosen this filter to keep more rows."}
                      </div>
                    </div>
                    <button type="button" className="btn sm primary"
                      onClick={() => {
                        const r = topDropReasons[0][0];
                        if (r === "assay_quality") setMinConfidence(Math.max(0, minConfidence - 2));
                        else if (r === "activity_range") {
                          setPkiRange([Math.max(0, pkiRange[0] - 1), Math.min(15, pkiRange[1] + 1)]);
                          setActivity({ ki: true, kd: true, ic50: true, ec50: true });
                        } else if (r === "drug_likeness") setQedRange([Math.max(0, qedRange[0] - 0.1), 1.0]);
                        else if (r === "family_mismatch") setTargetFamily("All protein families (no restriction)");
                        else if (r === "organism_mismatch") setOrganisms(new Set([...organisms, "human"]));
                        else if (r === "missing_structure") setStructPolicy("fetch_and_cache");
                        toast({ title: "Loosened", body: `Relaxed the ${r.replace(/_/g, " ")} filter; recheck the funnel.`, level: "ok", ttl_ms: 2500 });
                      }}>
                      Loosen {topDropReasons[0][0].replace(/_/g, " ")}
                    </button>
                  </div>
                )}

                {/* Source-mix charts */}
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 18 }}>
                  <div>
                    <div className="label">Where the kept rows come from</div>
                    {survival.sourcesKept.length === 0 ? (
                      <div style={{ fontSize: 11, color: "var(--muted)", fontStyle: "italic" }}>
                        No rows survive the current filters. Pick at least one integrated source above.
                      </div>
                    ) : survival.sourcesKept.map(s => (
                      <div key={s.src} style={{ display: "grid", gridTemplateColumns: "120px 1fr 90px", alignItems: "center", gap: 10, marginBottom: 6, fontSize: 12 }}>
                        <span style={{ color: "var(--muted)" }}>{s.src}</span>
                        <div style={{ height: 8, background: "var(--surface-3)", borderRadius: 4, overflow: "hidden" }}>
                          <div style={{ width: `${Math.min(100, s.share)}%`, height: "100%", background: "var(--primary)" }} />
                        </div>
                        <span className="mono" style={{ textAlign: "right" }}>{fmt.short(s.kept)} · {s.share.toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                  <div>
                    <div className="label">Why rows got dropped (live)</div>
                    {(() => {
                      const breakdown = survival.dropByReason;
                      const jitFetch = structPolicy !== "must_be_local";
                      // Human-readable labels — the raw IDs (redundancy, assay_quality)
                      // confused the UX-review agent. Especially "redundancy" reads
                      // as "data was wasted" when it's actually the desirable best-
                      // evidence dedup. Always show duplicates_collapsed with its
                      // explainer next to it.
                      const labels = {
                        assay_quality:     "low assay confidence",
                        activity_range:    "outside activity range",
                        drug_likeness:     "outside drug-likeness range",
                        family_mismatch:   "wrong protein family",
                        organism_mismatch: "wrong organism",
                        missing_structure: "missing structure",
                        redundancy:        "duplicates collapsed",
                      };
                      const explainers = {
                        redundancy: "Same (protein, ligand) pair reported by multiple sources. We kept the best-evidence row and dropped the rest. This is desired.",
                      };
                      const max = Math.max(1, ...Object.values(breakdown));
                      const colors = { resolution: "var(--warn)", missing_structure: "var(--error)",
                                       redundancy: "var(--molecular)", assay_quality: "var(--primary)",
                                       activity_range: "var(--signal)", organism_mismatch: "var(--dim)",
                                       drug_likeness: "var(--molecular)", family_mismatch: "var(--warn)",
                                       source_excluded: "var(--warn)" };
                      return (
                        <>
                          {Object.entries(breakdown).map(([reason, n]) => (
                            <div key={reason} style={{ display: "grid", gridTemplateColumns: "180px 1fr 80px", alignItems: "center", gap: 10, marginBottom: 6, fontSize: 12 }}>
                              <span style={{ color: "var(--muted)" }} title={
                                reason === "missing_structure" && jitFetch
                                  ? "Structures are fetched on demand during example building under the current policy."
                                  : (explainers[reason] || undefined)
                              }>
                                {labels[reason] || reason.replace(/_/g, " ")}
                                {reason === "redundancy" && (
                                  <Chip tone="ok" style={{ marginLeft: 4 }}>desired</Chip>
                                )}
                              </span>
                              <div style={{ height: 8, background: "var(--surface-3)", borderRadius: 4, overflow: "hidden" }}>
                                <div style={{ width: `${(n / max) * 100}%`, height: "100%", background: colors[reason] || "var(--primary)" }} />
                              </div>
                              <span className="mono" style={{ textAlign: "right", color: (reason === "missing_structure" && jitFetch && n === 0) ? "var(--dim)" : undefined }}>
                                {fmt.short(n)}{reason === "missing_structure" && jitFetch && n === 0 ? " · JIT" : ""}
                              </span>
                            </div>
                          ))}
                          {breakdown.redundancy > 0 && (
                            <div style={{ marginTop: 4, padding: "6px 10px", background: "var(--surface-3)", borderRadius: 4,
                                          fontSize: 11, color: "var(--muted)", borderLeft: "2px solid var(--molecular)" }}>
                              <strong style={{ color: "var(--text)" }}>Why duplicates were collapsed:</strong> {fmt.n(breakdown.redundancy)} rows represent the same (protein, ligand) pair measured by more than one source. We keep the highest-confidence row and drop the rest. This is the desired behaviour — don't try to loosen it.
                            </div>
                          )}
                          <div style={{ marginTop: 8, padding: "6px 10px", background: "var(--surface-3)", borderRadius: 4,
                                        fontSize: 11, color: "var(--muted)", borderLeft: "2px solid var(--ok)" }}>
                            Totals add up: {fmt.n(filteredTotals.final_selected)} kept + {fmt.n(filteredTotals.candidates - filteredTotals.final_selected)} dropped = {fmt.n(filteredTotals.candidates)} candidates.
                          </div>
                        </>
                      );
                    })()}
                  </div>
                </div>

                {/* Dropped-rows table */}
                <hr className="hr" />
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
                  <span className="label" style={{ margin: 0 }}>Dropped rows</span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)" }}>
                    {fmt.n(filteredTotals.candidates - filteredTotals.final_selected)} total · showing up to 10
                  </span>
                  <div style={{ flex: 1 }} />
                  <div className="toggle">
                    <button aria-pressed={dropTab === "by_reason"} onClick={() => setDropTab("by_reason")}>By reason</button>
                    <button aria-pressed={dropTab === "by_source"} onClick={() => setDropTab("by_source")}>By source</button>
                  </div>
                  <button type="button" className="btn sm ghost"
                    onClick={() => toast({
                      title: `Dropped rows export (${dropTab.replace("_", " ")})`,
                      body: `Would write data/dropped_${Date.now()}.csv with every filtered row plus its drop_reason and source_dataset columns.`,
                      level: "ok",
                    })}>
                    <Ico name="download" size={12} /> Export dropped CSV
                  </button>
                </div>

                <div style={{ border: "1px solid var(--border)", borderRadius: "var(--r)", overflow: "hidden" }}>
                  <table className="tbl" style={{ margin: 0 }}>
                    <thead>
                      <tr>
                        <th>Pair</th><th>Source</th><th>Reason</th><th>Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {survival.droppedSample.length === 0 ? (
                        <tr><td colSpan="4" style={{ padding: 18, color: "var(--dim)", fontStyle: "italic", textAlign: "center" }}>
                          No preview rows are getting dropped under the current filters. Tighten a filter above to see examples.
                        </td></tr>
                      ) : [...survival.droppedSample]
                        .sort((a, b) => {
                          if (dropTab === "by_reason") return a.reason.localeCompare(b.reason);
                          return a.source.localeCompare(b.source);
                        })
                        .slice(0, 10)
                        .map((r, i, arr) => {
                          const sameGroupAsPrev = i > 0 && (dropTab === "by_reason"
                            ? arr[i-1].reason === r.reason
                            : arr[i-1].source === r.source);
                          const tone = ({
                            assay_quality:     "primary",
                            activity_range:    "signal",
                            drug_likeness:     "molecular",
                            family_mismatch:   "warn",
                            organism_mismatch: "dim",
                            missing_structure: "error",
                          })[r.reason] || "primary";
                          return (
                            <tr key={i} style={!sameGroupAsPrev && i > 0 ? { borderTop: "2px solid var(--border-strong)" } : {}}>
                              <td className="mono" style={{ color: "var(--primary)" }}>{r.id}</td>
                              <td><span style={{ color: "var(--muted)", fontSize: 12 }}>{r.source}</span></td>
                              <td>
                                <Chip tone={tone}>{r.reason.replace(/_/g, " ")}</Chip>
                              </td>
                              <td><span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>{r.detail}</span></td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Sidebar — live dataset summary */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 16, alignSelf: "flex-start" }}>
          <div className="card elevated">
            <div className="card-h">
              <span className="t">Dataset summary</span>
              <Chip tone="primary" dot>live</Chip>
            </div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <Stat k="Pairs after filters" v={fmt.short(filteredTotals.final_selected)} mono />
              {/* Composition derived from live picked sources, in the same
                  order they were ranked in the funnel's source-mix chart. */}
              <div>
                <div className="label">Composition</div>
                {survival.sourcesKept.length === 0 ? (
                  <div style={{ fontSize: 11, color: "var(--dim)", fontStyle: "italic" }}>
                    No sources selected.
                  </div>
                ) : (
                  <>
                    <CompositionBar />
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 6, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                      {survival.sourcesKept.slice(0, 6).map((s, i) => {
                        const colors = ["var(--primary)", "var(--molecular)", "var(--signal)", "var(--warn)", "var(--info)", "var(--ok)"];
                        return (
                          <span key={s.src}>
                            <span style={{ display: "inline-block", width: 8, height: 8, background: colors[i] || "var(--dim)", borderRadius: 2, marginRight: 4 }} />
                            {s.src} {s.share.toFixed(0)}%
                          </span>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>

              {/* Unique targets / ligands derived from the kept-rows breakdown.
                  The fixture's static "2,184 · 48,290" had no relation to what
                  the user picked. We don't know exact unique counts after the
                  warehouse joins without a backend call, so derive a defensible
                  estimate: protein-rows scale at ~5K per source, ligands at
                  ~25K when ligand-bearing sources are selected. */}
              {(() => {
                const proteinSources = ["gtopdb","davis","kiba","huri","hippie","3did","pdbbind","bindingdb","chembl","uniprot"];
                const ligandSources = ["gtopdb","davis","kiba","pdbbind","bindingdb","chembl","chebi","drugbank","pubchem","zinc"];
                const pickedIds = Array.from(selected);
                const pPicked = pickedIds.filter(id => proteinSources.includes(id)).length;
                const lPicked = pickedIds.filter(id => ligandSources.includes(id)).length;
                // Rough heuristic — bounded by warehouse sequence universe (57K)
                // and ligand universe (~13K + ChEMBL/BindingDB extras).
                const uniqProt = Math.min(57000, 600 * pPicked + Math.floor(filteredTotals.final_selected * 0.003));
                const uniqLig  = Math.min(4_300_000, 800 * lPicked + Math.floor(filteredTotals.final_selected * 0.02));
                return (
                  <div>
                    <div className="label">Targets · Ligands</div>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                      <Stat k="Unique targets ≈" v={fmt.short(uniqProt)} mono />
                      <Stat k="Unique ligands ≈" v={fmt.short(uniqLig)} mono />
                    </div>
                  </div>
                );
              })()}

              {/* Quality flags: derived from filteredTotals; previously these
                  were three hardcoded sentences that referenced source names
                  (BindingDB & ChEMBL) regardless of whether the user picked
                  them. */}
              {(() => {
                const flags = [];
                if (hasAffinity && activity.ic50) {
                  // Rough: 30% of typical assay rows are IC50 conversions
                  flags.push(`~${fmt.short(Math.round(filteredTotals.after_filters * 0.30))} IC50 values converted from cell-based assays`);
                }
                if (survival.dropByReason.redundancy > 0) {
                  flags.push(`${fmt.short(survival.dropByReason.redundancy)} duplicates collapsed across sources — best-evidence kept`);
                }
                if (pkiRange[0] > 3.0 || pkiRange[1] < 13.0) {
                  flags.push(`pKi range narrowed to [${pkiRange[0].toFixed(1)}, ${pkiRange[1].toFixed(1)}]; outside-range rows excluded`);
                }
                if (flags.length === 0) return null;
                return (
                  <>
                    <hr className="hr" />
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                        <Ico name="warn" style={{ color: "var(--warn)" }} />
                        <span style={{ fontSize: 12, color: "var(--warn)" }}>{flags.length} quality flag{flags.length === 1 ? "" : "s"}</span>
                      </div>
                      <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>
                        {flags.map((f, i) => <div key={i}>· {f}</div>)}
                      </div>
                    </div>
                  </>
                );
              })()}
            </div>
          </div>

          <div className="card">
            <div className="card-h">
              <span className="t">Provenance</span>
            </div>
            <div className="card-b" style={{ fontSize: 11, color: "var(--muted)", fontFamily: "var(--font-mono)", lineHeight: 1.6 }}>
              <div>warehouse · v2026.04 · ✓ frozen</div>
              <div>splits · pending</div>
              <div>conflicts · best_evidence policy</div>
              <div>seed · <span style={{ color: "var(--text)" }}>4192</span></div>
              <hr className="hr" />
              <button type="button" style={{ background: "transparent", border: 0, padding: 0, color: "var(--primary)", textDecoration: "none", cursor: "pointer", fontSize: "inherit", fontFamily: "inherit" }}
                onClick={() => window.dispatchEvent(new CustomEvent("open-lineage"))}>
                View full lineage →
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// Format a pKi value as a human-readable potency (e.g. pKi 9 → "1 nM").
// 1 pKi unit = 10× tighter binding. pKi 9 = 10⁻⁹ M = 1 nM.
function fmtPotency(pki) {
  const M = Math.pow(10, -pki);
  if (M < 1e-9)   return `${(M * 1e12).toFixed(1)} pM`;
  if (M < 1e-6)   return `${(M * 1e9).toFixed(M < 1e-8 ? 0 : 1)} nM`;
  if (M < 1e-3)   return `${(M * 1e6).toFixed(M < 1e-5 ? 0 : 1)} µM`;
  return `${(M * 1e3).toFixed(1)} mM`;
}

// Organism multi-select with "select all" affordance, popover-style.
// Closed-by-default; click to open, click a row to toggle.
function OrganismMultiselect({ selected, onChange, toast }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e) => { if (!ref.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);
  const orgs = window.PS_ORGANISMS || [];
  const labels = (() => {
    if (selected.size === 0) return "(none)";
    if (selected.size === orgs.length) return "all organisms";
    if (selected.size <= 3) return Array.from(selected).map(id => orgs.find(o => o.id === id)?.name?.split(" ")[0]).join(", ");
    return `${selected.size} selected`;
  })();
  const toggle = (id) => {
    const ns = new Set(selected);
    ns.has(id) ? ns.delete(id) : ns.add(id);
    onChange(ns);
  };
  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button type="button" className="input" aria-haspopup="listbox" aria-expanded={open}
        onClick={() => setOpen(o => !o)}
        style={{ display: "flex", alignItems: "center", gap: 8, textAlign: "left", cursor: "pointer", width: "100%", fontFamily: "inherit", color: "var(--text)" }}>
        <span style={{ flex: 1 }}>{labels}</span>
        <Ico name="chev" size={11} />
      </button>
      {open && (
        <div role="listbox" aria-label="Organisms"
          style={{
            position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 50,
            background: "var(--surface)", border: "1px solid var(--border-strong)", borderRadius: "var(--r)",
            boxShadow: "0 12px 32px #0009", maxHeight: 280, overflow: "auto", padding: 4,
          }}>
          <div style={{ display: "flex", gap: 4, padding: "4px 6px 8px", borderBottom: "1px solid var(--border-soft)" }}>
            <button type="button" className="btn sm ghost" style={{ flex: 1 }}
              onClick={() => onChange(new Set(orgs.map(o => o.id)))}>Select all</button>
            <button type="button" className="btn sm ghost" style={{ flex: 1 }}
              onClick={() => onChange(new Set(orgs.filter(o => o.common).map(o => o.id)))}>Common only</button>
            <button type="button" className="btn sm ghost" style={{ flex: 1 }}
              onClick={() => onChange(new Set())}>Clear</button>
          </div>
          {orgs.map(o => {
            const on = selected.has(o.id);
            return (
              <button type="button" key={o.id} role="option" aria-selected={on}
                onClick={() => toggle(o.id)}
                style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "6px 8px",
                  background: on ? "var(--primary-soft)" : "transparent", border: 0, color: "var(--text)",
                  textAlign: "left", cursor: "pointer", font: "inherit", borderRadius: 4 }}>
                <div style={{
                  width: 14, height: 14, borderRadius: 3,
                  border: `1.4px solid ${on ? "var(--primary)" : "var(--border-strong)"}`,
                  background: on ? "var(--primary)" : "transparent",
                  display: "grid", placeItems: "center", color: "#021624", flexShrink: 0
                }}>{on && <Ico name="check" size={10} />}</div>
                <span style={{ flex: 1, fontSize: 12 }}>{o.name}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>tax {o.taxid}</span>
                {o.common && <Chip tone="signal">common</Chip>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Tiny range slider component
function RangeSlider({ min, max, v0, v1, single, step = 0.1, onChange, ariaLabel }) {
  // Controlled-vs-uncontrolled: if onChange is supplied, the parent owns
  // the state and the slider's local state is kept in sync via props.
  // Otherwise the slider manages its own values (legacy callsites).
  const [a, setA] = React.useState(v0);
  const [b, setB] = React.useState(v1);
  React.useEffect(() => { setA(v0); }, [v0]);
  React.useEffect(() => { setB(v1); }, [v1]);
  const pct = (v) => ((v - min) / (max - min)) * 100;
  // Inversion guards: lower bound can't exceed upper bound, and vice
  // versa. Without these, dragging the lower thumb past the upper one
  // silently collapsed the range to a single point (BUG-006) and the
  // funnel reported "everything dropped" with no visual cue.
  const updateA = (next) => {
    if (!single && next > b) next = b;        // clamp at upper bound
    if (next < min) next = min;
    if (next > max) next = max;
    setA(next);
    onChange && onChange([next, single ? next : b]);
  };
  const updateB = (next) => {
    if (next < a) next = a;                    // clamp at lower bound
    if (next < min) next = min;
    if (next > max) next = max;
    setB(next);
    onChange && onChange([a, next]);
  };
  return (
    <div style={{ padding: "10px 4px", position: "relative" }}>
      <div style={{ position: "relative", height: 4, background: "var(--surface-3)", borderRadius: 2 }}>
        <div style={{
          position: "absolute", height: "100%",
          left: pct(a) + "%",
          right: (single ? 100 - pct(a) : 100 - pct(b)) + "%",
          background: "var(--primary)", borderRadius: 2
        }} />
        <div style={{ position: "absolute", left: `calc(${pct(a)}% - 7px)`, top: -5, width: 14, height: 14, borderRadius: 7, background: "var(--surface)", border: "1.6px solid var(--primary)" }} />
        {!single && <div style={{ position: "absolute", left: `calc(${pct(b)}% - 7px)`, top: -5, width: 14, height: 14, borderRadius: 7, background: "var(--surface)", border: "1.6px solid var(--primary)" }} />}
      </div>
      {/* Native range inputs stacked on top — accessible + draggable.
          Each input's *track* is pointer-events:none so the input below
          stays reachable; only the thumb captures events. This lets both
          handles of a dual-range be grabbed independently. The trick is
          implemented via the .range-overlay class (track none, thumb auto).
          To bias which thumb wins overlap, the lower one's thumb wins
          on the left half of the slider, the upper on the right half. */}
      <input type="range" className="range-overlay range-lower" min={min} max={max} step={step} value={a}
        aria-label={(ariaLabel || "Range") + (single ? "" : " lower bound")}
        onChange={e => {
          const v = parseFloat(e.target.value);
          if (single) { updateA(v); return; }
          updateA(Math.min(v, b));
        }}
        style={{ position: "absolute", left: 4, right: 4, top: 0, height: 14 }} />
      {!single && (
        <input type="range" className="range-overlay range-upper" min={min} max={max} step={step} value={b}
          aria-label={(ariaLabel || "Range") + " upper bound"}
          onChange={e => {
            const v = parseFloat(e.target.value);
            updateB(Math.max(v, a));
          }}
          style={{ position: "absolute", left: 4, right: 4, top: 0, height: 14 }} />
      )}
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
        <span>{single ? `≥ ${a}` : a.toFixed(step < 1 ? 1 : 0)}</span>
        {!single && <span>{b.toFixed(step < 1 ? 1 : 0)}</span>}
      </div>
    </div>
  );
}

function CompositionBar() {
  const segs = [
    { c: "var(--primary)", w: 62 },
    { c: "var(--molecular)", w: 23 },
    { c: "var(--signal)", w: 9 },
    { c: "var(--warn)", w: 6 },
  ];
  return (
    <div style={{ display: "flex", height: 10, borderRadius: 5, overflow: "hidden", background: "var(--surface-3)" }}>
      {segs.map((s, i) => <div key={i} style={{ width: s.w + "%", background: s.c }} />)}
    </div>
  );
}

window.ScreenDataset = ScreenDataset;
