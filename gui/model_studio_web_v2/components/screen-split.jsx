// ProteoSphere — Splits Designer (leakage-aware)
// This is the signature screen: shows similarity clusters, lets you choose a split policy,
// and surfaces overlap warnings between train/val/test live.

// Maps a top_groups entry from /api/v2/splits/leakage_report into the
// shape the existing leakage-row UI expects.
//
// Field derivations:
//   axis        — from the relationship kind (protein-axis vs ligand-axis vs joint)
//   id          — composite "kind:identifier" (used as React key + splitAssignment key)
//   short       — compact 6-char label shown inside the SVG bubble
//   kind        — human-readable description for the table row "Pocket / motif" column
//   similarity  — proxy from the relationship threshold (uniref50 → 0.5, etc.) so the
//                 SVG edge-threshold filter draws meaningful connections
//   risk        — band from cluster size (these are the TOP 25 hotspots, so most should
//                 read as "med" / "high")
//   residues    — cosmetic, set to label length so the column has some signal
function _mapTopGroupToFixture(tg) {
  const proteinKinds = new Set([
    "interpro", "pfam", "uniref50", "uniref90", "uniref100", "orthodb", "ec3",
  ]);
  const ligandKinds = new Set(["scaffold", "tanimoto_0.4"]);
  const axis = proteinKinds.has(tg.kind) ? "protein"
             : ligandKinds.has(tg.kind)  ? "ligand"
             : "joint";
  // Use cluster size to band risk. These are the top-25, so the cut-offs
  // are deliberately higher than the fixture's hand-tuned bands.
  const n = tg.n_uniprots || 0;
  const risk = n >= 200 ? "high" : n >= 80 ? "med" : "low";
  // Internal similarity proxy — drives the SVG edge filter. Cluster-name
  // semantics give the floor; bigger clusters tend to be tighter so we
  // add a small bonus for size.
  const baseSim = {
    "uniref50": 0.50, "uniref90": 0.90, "uniref100": 1.00,
    "orthodb": 0.65, "ec3": 0.55, "interpro": 0.60, "pfam": 0.60,
    "scaffold": 0.85, "tanimoto_0.4": 0.45,
  }[tg.kind] ?? 0.50;
  const similarity = Math.min(1.0, baseSim + Math.min(0.15, Math.log10(Math.max(2, n)) / 12));
  // Pretty kind labels for the table row
  const kindPretty = {
    "uniref50": "UniRef50 cluster", "uniref90": "UniRef90 cluster", "uniref100": "UniRef100 cluster",
    "orthodb": "OrthoDB ortholog group", "ec3": "EC sub-class",
    "interpro": "InterPro family", "pfam": "Pfam family",
    "scaffold": "Bemis-Murcko scaffold", "tanimoto_0.4": "Tanimoto ≥ 0.4 cluster",
  }[tg.kind] || tg.kind;
  const labelText = tg.label && tg.label.length ? tg.label : tg.id;
  // Compact bubble label: 6 chars max. Strip the namespace prefix common
  // across InterPro / Pfam ("IPR", "PF"); keep the leading digits otherwise.
  const compact = (tg.id || "").replace(/^IPR/, "").replace(/^PF/, "").slice(0, 6);
  return {
    id: `${tg.kind}:${tg.id}`,
    short: compact || (tg.kind || "?").slice(0, 4),
    n,
    kind: `${kindPretty} · ${labelText}`,
    axis,
    risk,
    residues: (labelText || "").length,
    similarity,
    _live: true,
    sources_touched: tg.sources_touched || [],
  };
}

function ScreenSplit({ setCurrent, advanced, advancedDeltaCount, openAdvanced, coachAreas, coachOn, pushToast }) {
  const toast = pushToast || window.pushToast;
  const [clusterView, setClusterView] = React.useState("protein"); // protein | ligand | joint
  const D = window.PS_DATA;

  // ── Real backend leakage report ──────────────────────────────────
  // Replaces fixture D.leakage_groups whenever the backend responds.
  // Cached on `window.PS_LIVE_LEAKAGE_REPORT` so navigating away and
  // back doesn't re-fetch + flash the fixture data; the JIT self-joins
  // on the 8.7 M-row catalog take ~2 s and showing fixtures during that
  // window is what made the splits screen feel "weird".
  const [liveReport, setLiveReport] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_LEAKAGE_REPORT) || null
  );
  const [liveError, setLiveError] = React.useState(null);
  // True while a manual "Recompute splits" POST is in flight; disables
  // the button + flips its label so the user gets feedback that the
  // backend was actually called this time.
  const [recomputing, setRecomputing] = React.useState(false);
  React.useEffect(() => {
    if (liveReport) return;  // already cached from a previous mount
    let cancelled = false;
    fetch("/api/v2/splits/leakage_report")
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(j => {
        if (cancelled) return;
        window.PS_LIVE_LEAKAGE_REPORT = j;
        setLiveReport(j);
      })
      .catch(err => { if (!cancelled) setLiveError(String(err.message || err)); });
    return () => { cancelled = true; };
  }, [liveReport]);

  const liveGroups = React.useMemo(() => {
    if (!liveReport || !Array.isArray(liveReport.top_groups)) return null;
    return liveReport.top_groups.map(_mapTopGroupToFixture);
  }, [liveReport]);
  const effectiveLeakageGroups = liveGroups && liveGroups.length ? liveGroups : D.leakage_groups;
  const isLive = !!(liveGroups && liveGroups.length);

  // ── Design objective — drives everything below ────────────────────
  // Persisted on PS_DATA so the Results / Compare / Promote screens can
  // surface it as a banner. Source of truth lives here.
  const [objective, setObjective] = React.useState(D.design_objective || "generalization");
  React.useEffect(() => { D.design_objective = objective; }, [objective]);
  const objectiveDef = (window.PS_DESIGN_OBJECTIVES || []).find(o => o.id === objective) || window.PS_DESIGN_OBJECTIVES[0];

  const [policy, setPolicy] = React.useState("cluster");
  const [protThresh, setProtThresh] = React.useState(0.30);
  const [ligThresh, setLigThresh] = React.useState(0.40);
  const [trainPct, setTrainPct] = React.useState(70);
  const [valPct, setValPct] = React.useState(15);
  const testPct = 100 - trainPct - valPct;
  const simThresh = Math.sqrt(protThresh * ligThresh);

  // Per-cluster cap — if a single group has more than this fraction of total
  // pairs, the splitter thins it down for balance. 1.0 = no cap.
  const [clusterCap, setClusterCap] = React.useState(0.20);

  // Which relationships count as "leakage" for the clusterer. Multi-select
  // from PS_CLUSTER_RELATIONSHIPS. Default = the two integrated, on-by-default
  // relationships (MMseqs2 sequence identity + ECFP Tanimoto).
  const [relationships, setRelationships] = React.useState(() => new Set(
    (window.PS_CLUSTER_RELATIONSHIPS || []).filter(r => r.defaultOn && r.status === "integrated").map(r => r.id)
  ));
  const toggleRelationship = (id) => setRelationships(prev => {
    const ns = new Set(prev);
    ns.has(id) ? ns.delete(id) : ns.add(id);
    return ns;
  });

  // How relationships compose when multiple fire. Transitive closure
  // ("union") on shared subunits creates mega-clusters in PPI; per-subunit
  // splitting is the standard remedy. Pick the right default based on the
  // binding partners chosen on the Dataset screen (persisted to PS_DATA).
  const initialMergeMode = React.useMemo(() => {
    const partners = D.binding_partners;
    if (partners && (partners.has?.("pp") || partners.has?.("pna"))) return "per_subunit";
    return "union";
  }, []);
  const [mergeMode, setMergeMode] = React.useState(initialMergeMode);
  const mergeModeDef = (window.PS_MERGE_MODES || []).find(m => m.id === mergeMode) || window.PS_MERGE_MODES[0];

  // Cluster construction explainer modal — walks the A-B-C-D-E mega-cluster scenario.
  const [explainerOpen, setExplainerOpen] = React.useState(false);

  // Session-scoped coach dismissal. Reads sessionStorage on first mount
  // so the tip stays hidden while the user navigates around the app in
  // the same tab session, but a fresh page load (or re-enabling coaching
  // in settings) brings it back. Distinct from the persistent
  // localStorage approach StaleBanner uses — coaching is a transient
  // helper, not a structural warning.
  const [coachDismissed, setCoachDismissed] = React.useState(() => {
    try { return sessionStorage.getItem("ps.coach_dismissed.split") === "1"; }
    catch { return false; }
  });

  // Detect the mega-cluster risk: if "union" mode + ≥2 relationships and
  // the largest cluster would balloon past a threshold, surface a warning
  // and an "Open explainer" CTA in the relationships card.
  const megaClusterRiskLevel = React.useMemo(() => {
    if (mergeMode !== "union") return "ok";
    if (relationships.size <= 1) return "ok";
    // Largest cluster / total pairs across the live (or fixture) groups.
    const total = effectiveLeakageGroups.reduce((s, g) => s + g.n, 0);
    if (!total) return "ok";
    const biggest = Math.max(...effectiveLeakageGroups.map(g => g.n));
    const ratio = biggest / total;
    if (ratio >= 0.40) return "high";
    if (ratio >= 0.25) return "med";
    return "low";
  }, [mergeMode, relationships, effectiveLeakageGroups]);

  // The recommended policy(ies) for the current objective, used to badge
  // the picker. Picking a non-recommended policy still works but flags a
  // warning in the readiness card.
  const recommended = new Set(objectiveDef.recommendedPolicies || []);

  // Full policy catalog. Each entry's `objective` tag is which design
  // objective it's appropriate for ("gen" | "interp" | "both").
  const policies = [
    { id: "random",      name: "Random",                  desc: "Pairs split i.i.d. — fastest, most optimistic metrics. Only valid for interpolation.",                   risk: "leakage likely",    objective: "interp" },
    { id: "scaffold",    name: "Scaffold (ligand)",       desc: "Bemis–Murcko scaffold split. Tests novel chemotypes; protein side still shared.",                          risk: "moderate",           objective: "both" },
    { id: "cluster",     name: "Leakage-aware cluster",   desc: "MMseqs2 + ECFP cluster on both axes. The honest default for generalisation studies.",                       risk: "honest",             objective: "gen" },
    { id: "cold-target", name: "Cold target",             desc: "Held-out proteins unseen in train. Hardest. Required for new-target campaigns.",                            risk: "stress test",        objective: "gen" },
    { id: "cold-drug",   name: "Cold drug / scaffold",    desc: "Held-out scaffolds unseen in train. Tests genuinely-novel chemotypes.",                                     risk: "stress test",        objective: "gen" },
    { id: "cold-pair",   name: "Cold pair (both novel)",  desc: "Both protein AND ligand absent from train. The hardest cut; numbers will look low.",                        risk: "hardest",            objective: "gen" },
    { id: "time-split",  name: "Time-based holdout",      desc: "Train on data published before a cutoff year, test on later. Models the real 'new compound' problem.",      risk: "realistic",          objective: "gen" },
    { id: "stratified",  name: "Stratified by family",    desc: "Equal protein-family representation in each split. Lower variance metrics; doesn't test family transfer.", risk: "balanced",           objective: "interp" },
  ];

  // Generated cluster bubble positions + a deterministic "share edge with" map.
  // Memoised so the dashed connection lines and bubble layout don't reshuffle on every render.
  const groups = React.useMemo(() => effectiveLeakageGroups.map((g, i) => ({
    ...g,
    cx: 60 + (i * 73) % 460 + Math.sin(i * 1.3) * 16,
    cy: 80 + ((i * 37) % 180) + Math.cos(i * 0.7) * 12,
    r: 10 + Math.sqrt(g.n) * 0.4,
    // Pre-computed boolean: should this group keep ~40% of its possible edges?
    _edgeKeep: (i * 2654435761 >>> 0) % 100 < 40,
  })), [effectiveLeakageGroups]);

  // ── Split assignment (LPT-greedy with diversity guarantees) ────────
  // Single source of truth for the SVG bubble colours, the per-row "Split"
  // column, and the summary stats. Computed as:
  //
  //   1. Apply the policy filter — which axes a given policy allows in
  //      each split (e.g. cold-target forbids protein-axis clusters in
  //      train; random allows anything everywhere).
  //   2. Treat user overrides as locked constraints — their pair count
  //      is subtracted from the target for the assigned split before the
  //      greedy fill runs.
  //   3. For the remaining (non-overridden) clusters, run LPT-greedy
  //      (Longest Processing Time first): sort by pair count descending
  //      and assign each cluster to whichever allowed split has the
  //      largest remaining deficit (target_pairs - current_pairs). Ties
  //      broken by preferring the split with fewer clusters of the same
  //      axis (diversity preserving).
  //   4. Enforce a minimum-cluster guarantee per split (≥ 2 clusters if
  //      ≥ 6 are available; ≥ 1 otherwise). If a split is starved, peel
  //      the smallest non-overridden cluster off the most-filled split.
  //
  // This produces splits that hit the requested pair-count ratios closely
  // AND preserve cluster diversity in val + test (not just a single huge
  // cluster), AND respect every user pin.
  const [overrides, setOverrides] = React.useState({}); // {gid: "train"|"val"|"test"}

  // What buckets is each policy willing to put a given cluster into?
  // Returns the set of allowed buckets, or null = "all three".
  const _policyAllowedBuckets = React.useCallback((g) => {
    const isProt = g.axis === "protein";
    const isLig  = g.axis === "ligand";
    const isJoint = g.axis === "joint";
    switch (policy) {
      case "random":
        return ["train", "val", "test"];
      case "scaffold":
        // Ligand-axis groups MUST go to val/test (we're holding out
        // scaffolds); protein-axis can go anywhere.
        if (isLig)  return ["val", "test"];
        if (isJoint) return ["val", "test"];
        return ["train", "val", "test"];
      case "cold-target":
        // Protein-axis groups must be HELD OUT — they cannot appear in
        // train. Ligand/joint can go anywhere.
        if (isProt) return ["val", "test"];
        return ["train", "val", "test"];
      case "cold-drug":
        if (isLig)  return ["val", "test"];
        if (isJoint) return ["val", "test"];
        return ["train", "val", "test"];
      case "cold-pair":
        // Joint clusters (related on BOTH axes) must be held out.
        // Single-axis clusters spread normally.
        if (isJoint) return ["val", "test"];
        return ["train", "val", "test"];
      case "time-split":
        // The real impl would split by year; here we fall through to all-allowed
        // and the LPT greedy + ratio sliders give a reasonable distribution.
        return ["train", "val", "test"];
      case "stratified":
        return ["train", "val", "test"];
      case "cluster":
      default:
        // Leakage-aware cluster split — every axis is allowed in every
        // bucket; LPT does the balancing.
        return ["train", "val", "test"];
    }
  }, [policy]);

  const splitAssignment = React.useMemo(() => {
    if (!groups.length) return {};
    const totalPairsLocal = groups.reduce((s, g) => s + g.n, 0);
    const targets = {
      train: totalPairsLocal * trainPct / 100,
      val:   totalPairsLocal * valPct   / 100,
      test:  totalPairsLocal * testPct  / 100,
    };
    const out = {};
    const currentPairs    = { train: 0, val: 0, test: 0 };
    const currentClusters = { train: 0, val: 0, test: 0 };
    const axisCounts = {
      train: { protein: 0, ligand: 0, joint: 0 },
      val:   { protein: 0, ligand: 0, joint: 0 },
      test:  { protein: 0, ligand: 0, joint: 0 },
    };

    // Step 1: lock in user overrides — they count against the target
    // budgets before greedy filling. An over-constrained set of pins
    // (e.g. user dumps 90% of pairs into test) will simply leave the
    // other splits short; we don't override the user's intent.
    for (const g of groups) {
      if (overrides[g.id]) {
        const b = overrides[g.id];
        out[g.id] = b;
        currentPairs[b]    += g.n;
        currentClusters[b] += 1;
        axisCounts[b][g.axis || "joint"] += 1;
      }
    }

    // Step 2: LPT-greedy on non-overridden clusters (descending by size).
    const free = groups
      .filter(g => !overrides[g.id])
      .slice()
      .sort((a, b) => b.n - a.n);
    for (const g of free) {
      const allowed = _policyAllowedBuckets(g);
      if (allowed.length === 0) {
        // Should never happen — degrade gracefully to "train".
        out[g.id] = "train";
        currentPairs.train    += g.n;
        currentClusters.train += 1;
        axisCounts.train[g.axis || "joint"] += 1;
        continue;
      }
      // Score each candidate bucket. Primary: largest deficit (pairs).
      // Tiebreak: fewer clusters of this axis (diversity) → fewer
      // clusters total (balance) → "train" first as a stable fallback.
      const ax = g.axis || "joint";
      const scored = allowed.map(s => ({
        s,
        deficit: targets[s] - currentPairs[s],
        axisCount: axisCounts[s][ax],
        clusterCount: currentClusters[s],
      }));
      scored.sort((a, b) =>
        (b.deficit - a.deficit) ||
        (a.axisCount - b.axisCount) ||
        (a.clusterCount - b.clusterCount)
      );
      const chosen = scored[0].s;
      out[g.id] = chosen;
      currentPairs[chosen]    += g.n;
      currentClusters[chosen] += 1;
      axisCounts[chosen][ax]  += 1;
    }

    // Step 3: minimum-cluster guarantee for val and test.
    //
    // Why this matters: a split with one cluster gives a single bin of
    // pseudo-replicates — Pearson/RMSE are then computed over a single
    // family which makes them statistically meaningless. We want ≥ 2
    // distinct clusters per split, scaling up as the cluster pool grows.
    const totalGroups = groups.length;
    // Scale: at 6 clusters total need ≥ 2 per side; at 25 need ≥ 3; at
    // 100 need ≥ 5. Capped so train never gets starved.
    const minPerSplit = Math.max(
      2,
      Math.min(Math.floor(totalGroups / 8), Math.floor(totalGroups * 0.10)),
    );
    const minPerSide = Math.min(minPerSplit, Math.max(1, Math.floor(totalGroups / 3)));
    for (const target of ["test", "val"]) {  // test first — it's the headline number
      while (currentClusters[target] < minPerSide && totalGroups > 2) {
        // Find the most-overfilled split (most clusters AND not the target),
        // then peel its smallest NON-OVERRIDDEN cluster.
        const candidates = [];
        for (const src of ["train", "val", "test"]) {
          if (src === target) continue;
          if (currentClusters[src] <= 1) continue;
          for (const g of groups) {
            if (out[g.id] !== src) continue;
            if (overrides[g.id]) continue;             // never touch pinned
            const allowed = _policyAllowedBuckets(g);
            if (!allowed.includes(target)) continue;   // respect policy
            candidates.push({ g, src });
          }
        }
        if (!candidates.length) break;
        candidates.sort((a, b) => a.g.n - b.g.n);       // smallest first
        const { g, src } = candidates[0];
        out[g.id] = target;
        currentPairs[src]      -= g.n;
        currentClusters[src]   -= 1;
        axisCounts[src][g.axis || "joint"] -= 1;
        currentPairs[target]    += g.n;
        currentClusters[target] += 1;
        axisCounts[target][g.axis || "joint"] += 1;
      }
    }

    return out;
  }, [groups, policy, trainPct, valPct, testPct, overrides, _policyAllowedBuckets]);

  // Counts per bucket for the legend (number of clusters, not pairs).
  const splitCounts = React.useMemo(() => {
    const c = { train: 0, val: 0, test: 0 };
    for (const b of Object.values(splitAssignment)) c[b] = (c[b] || 0) + 1;
    return c;
  }, [splitAssignment]);

  // ── Per-cluster cap (balance / thin over-represented groups) ──────
  // If a single cluster has more than `clusterCap` of total pairs, we
  // thin it down by random subsampling at example-build time. The split
  // counter shows pair counts both before and after the cap.
  const totalPairs = React.useMemo(() => groups.reduce((s, g) => s + g.n, 0), [groups]);
  const cappedPairs = (g) => {
    const cap = Math.floor(totalPairs * clusterCap);
    if (clusterCap >= 1) return g.n;
    return Math.min(g.n, cap);
  };
  const pairCounts = React.useMemo(() => {
    const c = { train: 0, val: 0, test: 0 };
    groups.forEach(g => {
      const split = splitAssignment[g.id];
      if (split) c[split] += cappedPairs(g);
    });
    return c;
  }, [groups, splitAssignment, clusterCap, totalPairs]);
  const totalAfterCap = pairCounts.train + pairCounts.val + pairCounts.test;

  const setRowSplit = React.useCallback((gid, bucket) => {
    setOverrides(o => ({ ...o, [gid]: bucket }));
  }, []);
  const resetOverrides = React.useCallback(() => setOverrides({}), []);

  // Bridge: IssuesAndRecommendations dispatches a CustomEvent because it's
  // a child component that doesn't have direct access to setRowSplit. The
  // listener below converts it to an override.
  React.useEffect(() => {
    const handler = (e) => {
      const { gid, to } = e.detail || {};
      if (gid && to) setRowSplit(gid, to);
    };
    window.addEventListener("ps-internal-set-override", handler);
    return () => window.removeEventListener("ps-internal-set-override", handler);
  }, [setRowSplit]);

  // ── Auto-laid-out bubble positions ────────────────────────────────
  // Each bubble's cx is pinned inside its assigned column (train/val/test),
  // cy is laid out in a tidy grid so users immediately see which group
  // landed in which split. Manual overrides cause the bubble to move to
  // the new column on the next render.
  //
  // CRITICAL: the SVG viewBox is tracked against the live container size,
  // not pinned to 560×360. The card sits in a flex/grid layout that
  // resizes with the viewport — pinning the viewBox to 560×360 + the
  // default ``xMidYMid meet`` letterboxes the SVG, so the SVG column
  // boundaries (viewBox-relative) stop matching the HTML grid column
  // dividers (container-relative). On a wide screen that gap can be
  // 30–60 px, and dragging bubbles snaps to the wrong column.
  //
  // The fix: measure the container with ResizeObserver, set viewBox to
  // ``0 0 width height``, and recompute the layout with that width.
  // Bubbles stay circular (uniform scale), and SVG x=COL_W aligns
  // exactly with the HTML grid divider at 33.3% / 66.7% of the
  // container width.
  const canvasRef = React.useRef(null);
  const [canvasSize, setCanvasSize] = React.useState({ w: 560, h: 360 });
  // useLayoutEffect rather than useEffect so the measurement → state
  // update → re-render cycle runs synchronously before the browser
  // paints. With useEffect, you'd see a 1-frame flash where bubbles
  // are positioned for a 560-wide canvas, then snap to the real width.
  React.useLayoutEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) {
        setCanvasSize(s => (Math.abs(s.w - r.width) > 0.5 || Math.abs(s.h - r.height) > 0.5)
          ? { w: r.width, h: r.height } : s);
      }
    };
    update();
    if (typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(update);
      ro.observe(el);
      return () => ro.disconnect();
    }
    window.addEventListener("resize", update);
    return () => window.removeEventListener("resize", update);
  }, []);
  const VIEWBOX_W = canvasSize.w;
  const VIEWBOX_H = canvasSize.h;
  const COL_W = VIEWBOX_W / 3;
  const ZONE_TOP = 38;                  // leave room for the column header
  const ZONE_BOTTOM = VIEWBOX_H - 32;   // leave room for legend
  const layoutByZone = React.useMemo(() => {
    // Bucket groups by their assigned zone (sorted by size desc for stable layout)
    const byZone = { train: [], val: [], test: [] };
    [...groups].sort((a, b) => b.n - a.n).forEach(g => {
      const z = splitAssignment[g.id] || "train";
      byZone[z].push(g);
    });
    // Within each zone, compute a per-bubble cx/cy on a grid.
    const result = {};
    ["train", "val", "test"].forEach((z, colIdx) => {
      const list = byZone[z];
      const cols = list.length <= 2 ? 1 : list.length <= 6 ? 2 : 3;
      const rows = Math.ceil(list.length / cols);
      const colCenter = COL_W * colIdx + COL_W / 2;
      const cellH = (ZONE_BOTTOM - ZONE_TOP) / Math.max(rows, 1);
      list.forEach((g, i) => {
        const r = Math.floor(i / cols), c = i % cols;
        const offsetX = (c - (cols - 1) / 2) * (COL_W / (cols + 1));
        result[g.id] = {
          cx: colCenter + offsetX,
          cy: ZONE_TOP + cellH * (r + 0.5),
        };
      });
    });
    return result;
  }, [groups, splitAssignment, VIEWBOX_W, VIEWBOX_H]);

  // ── Drag-to-reassign state ────────────────────────────────────────
  const [drag, setDrag] = React.useState(null); // { gid, x, y } | null
  const svgRef = React.useRef(null);
  const beginDrag = (e, gid) => {
    e.preventDefault();
    const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
    dragRef.current = { gid, x: pt.x, y: pt.y };
    setDrag({ gid, x: pt.x, y: pt.y });
    window.addEventListener("mousemove", onDrag);
    window.addEventListener("mouseup", endDrag);
  };
  const onDrag = (e) => {
    const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
    setDrag(d => d ? { ...d, x: pt.x, y: pt.y } : null);
  };
  // Use a ref to capture the in-flight drag synchronously so endDrag
  // doesn't have to call setState-inside-setState. The reducer passed to
  // setDrag must be pure (React may call it twice in dev / strict mode);
  // any cross-component setState or toast belongs OUTSIDE the reducer.
  const dragRef = React.useRef(null);
  const beginDragKeepRef = React.useCallback((gid) => { dragRef.current = { gid }; }, []);
  const endDrag = (e) => {
    window.removeEventListener("mousemove", onDrag);
    window.removeEventListener("mouseup", endDrag);
    const d = dragRef.current;
    dragRef.current = null;
    setDrag(null);
    if (!d) return;
    const pt = svgPoint(svgRef.current, e.clientX, e.clientY);
    const zoneIdx = Math.max(0, Math.min(2, Math.floor(pt.x / COL_W)));
    const newSplit = ["train", "val", "test"][zoneIdx];
    const currentSplit = splitAssignment[d.gid];
    if (newSplit !== currentSplit) {
      setRowSplit(d.gid, newSplit);
      toast({
        title: `Moved ${d.gid} → ${newSplit}`,
        body: "Manual override registered; bubble pinned. Reset overrides at the top of the leakage table to revert.",
        level: "info", ttl_ms: 2200,
      });
    }
  };

  return (
    <div className="screen" data-screen-label="03 Splits">
      <StepRail active="split" onClick={setCurrent} />
      {/* v4 — surface the binding type the user picked on the Goal screen.
          Tells the user which axis the splitter is clustering on. */}
      {window.PS_DATA.binding_type && <BindingBanner setCurrent={setCurrent} />}

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>STEP 02 · SPLITS</div>
          <h2>Design a leakage-aware split</h2>
          <p className="lead" style={{ marginTop: 4 }}>The split is the experiment — what the model never sees in training is the only thing the metric actually measures. ProteoSphere clusters proteins by <Term word="MMseqs2">sequence identity (MMseqs2)</Term> and ligands by <Term word="Tanimoto">ECFP4 Tanimoto</Term>, then places whole clusters in train/val/test so look-alike pairs can't leak across.</p>
        </div>
        <div style={{ flex: 1 }} />
        {isLive ? (
          <Chip tone="ok" title={`Cross-relationships sourced from /api/v2/splits/leakage_report — universe: ${liveReport.universe.n_uniprots.toLocaleString()} proteins, ${liveReport.universe.n_ligands.toLocaleString()} ligands`}>
            Live warehouse data
          </Chip>
        ) : liveError ? (
          <Chip tone="warn" title={`Backend error: ${liveError}. Showing fixture data.`}>Static preview</Chip>
        ) : (
          <Chip tone="muted">Loading…</Chip>
        )}
        {openAdvanced && (
          <AdvancedButton
            panelKey="eval_analytics"
            openAdvanced={openAdvanced}
            deltaCount={advancedDeltaCount?.eval_analytics}>
            Stratification & metrics
          </AdvancedButton>
        )}
        <button className="btn primary" onClick={() => setCurrent("pipeline")}>
          Continue to Pipeline <Ico name="chevR" />
        </button>
      </div>
      {coachOn && coachAreas?.split && !coachDismissed && (
        <div className="coach-inline" style={{ margin: "0 0 16px", position: "relative", paddingRight: 30 }}>
          <Ico name="sparkle" size={12} />
          <span>Bench biologist tip: <strong>leakage-aware cluster</strong> is the honest default — protein clusters at <Term word="MMseqs2">≥ 30% identity</Term> and ligand clusters at <Term word="Tanimoto">≥ 0.40 Tanimoto</Term> get held together. Use cold-target when you're testing a brand-new protein.</span>
          {/* Session-scoped dismiss. Persists in sessionStorage so the
              tip stays hidden as the user moves between screens within
              the same tab session, but reappears on a fresh page load
              or whenever they re-enable coaching from settings. */}
          <button type="button"
            aria-label="Dismiss coach tip for this session"
            onClick={() => {
              try { sessionStorage.setItem("ps.coach_dismissed.split", "1"); } catch {}
              setCoachDismissed(true);
            }}
            style={{
              position: "absolute", top: 6, right: 6,
              width: 18, height: 18, padding: 0,
              background: "transparent", border: "none",
              color: "var(--molecular)", cursor: "pointer",
              opacity: 0.7, borderRadius: 3,
            }}
            onMouseEnter={(e) => e.currentTarget.style.opacity = "1"}
            onMouseLeave={(e) => e.currentTarget.style.opacity = "0.7"}
          >
            <Ico name="x" size={11} />
          </button>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 20 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>

          {/* 0 · Design objective — drives the recommended policy below */}
          <div className="card" style={{ borderLeft: `3px solid var(--${objectiveDef.tone})` }}>
            <div className="card-h">
              <span className="t">Design objective</span>
              <span className="sub">what kind of question is the model answering? Carries through to Results / Compare as a banner.</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", padding: 12, gap: 8 }}>
              {(window.PS_DESIGN_OBJECTIVES || []).map(o => {
                const on = objective === o.id;
                return (
                  <button type="button" key={o.id} aria-pressed={on}
                    onClick={() => setObjective(o.id)}
                    style={{
                      padding: 14, textAlign: "left", cursor: "pointer", font: "inherit", color: "var(--text)",
                      border: `1px solid ${on ? `var(--${o.tone})` : "var(--border)"}`,
                      borderRadius: "var(--r)",
                      background: on ? `var(--${o.tone}-soft)` : "var(--surface-2)",
                    }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                      <div style={{
                        width: 14, height: 14, borderRadius: "50%", flexShrink: 0,
                        border: `1.4px solid ${on ? `var(--${o.tone})` : "var(--border-strong)"}`,
                        background: on ? `var(--${o.tone})` : "transparent",
                      }} />
                      <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-strong)" }}>{o.label}</span>
                    </div>
                    <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.5 }}>{o.sub}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Policy picker */}
          <div className="card">
            <div className="card-h">
              <span className="t">Split policy</span>
              <span className="sub">how to assign pairs to splits · <strong style={{ color: `var(--${objectiveDef.tone})` }}>recommended</strong> for {objectiveDef.short}</span>
              {!recommended.has(policy) && (
                <Chip tone="warn" dot>not recommended for {objectiveDef.short}</Chip>
              )}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", padding: 12, gap: 8 }}>
              {policies.map(p => {
                const on = policy === p.id;
                const isRecommended = recommended.has(p.id);
                const isMismatched = !isRecommended;
                return (
                  <button type="button" key={p.id} aria-pressed={on}
                    onClick={() => setPolicy(p.id)}
                    style={{
                      padding: 12, textAlign: "left", cursor: "pointer", font: "inherit", color: "var(--text)",
                      border: `1px solid ${on ? "var(--primary)" : "var(--border)"}`,
                      borderRadius: "var(--r)",
                      background: on ? "var(--primary-soft)" : "var(--surface-2)",
                      position: "relative",
                      opacity: isMismatched && !on ? 0.7 : 1,
                    }}
                  >
                    {isRecommended && (
                      <span style={{ position: "absolute", top: -8, right: 8 }}>
                        <Chip tone={objectiveDef.tone === "warn" ? "warn" : "signal"} dot>recommended</Chip>
                      </span>
                    )}
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-strong)", marginBottom: 4 }}>{p.name}</div>
                    <div style={{ fontSize: 11, color: "var(--muted)", minHeight: 36, lineHeight: 1.4 }}>{p.desc}</div>
                    <div style={{ marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 10, color: isRecommended ? `var(--${objectiveDef.tone})` : "var(--dim)" }}>{p.risk}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Cluster canvas — the killer viz */}
          <div className="card">
            <div className="card-h">
              <span className="t">Similarity clusters</span>
              <span className="sub">{groups.length} clusters · sized by # pairs · auto-placed by the policy · <strong>drag to override</strong></span>
              {Object.keys(overrides).length > 0 && (
                <Chip tone="warn">{Object.keys(overrides).length} manual override{Object.keys(overrides).length === 1 ? "" : "s"}</Chip>
              )}
              <div style={{ flex: 1 }} />
              <div className="toggle" role="group" aria-label="Cluster axis">
                <button type="button" aria-pressed={clusterView === "protein"} onClick={() => setClusterView("protein")} title="Show protein-sequence-identity clusters">Protein</button>
                <button type="button" aria-pressed={clusterView === "ligand"}  onClick={() => setClusterView("ligand")}  title="Show ligand-Tanimoto clusters">Ligand</button>
                <button type="button" aria-pressed={clusterView === "joint"}   onClick={() => setClusterView("joint")}   title="Cross-product: a pair leaks only if both axes overlap">Joint</button>
              </div>
              {Object.keys(overrides).length > 0 && (
                <button type="button" className="btn sm ghost"
                  title="Discard all manual cluster moves and revert to the pure-policy recommendation."
                  onClick={() => { resetOverrides(); toast({ title: "Cleared manual overrides", body: "Cluster assignments revert to the policy + ratio computation.", level: "info", ttl_ms: 2000 }); }}>
                  Reset overrides
                </button>
              )}
              <button type="button" className="btn sm primary"
                title="Recompute the recommended split using the current policy, ratios, and relationship choices, while keeping any clusters you've manually pinned in place."
                onClick={() => {
                  // Auto-balance: the live `splitAssignment` is ALREADY the
                  // LPT-greedy recommendation given the current policy /
                  // ratios / overrides — the button just confirms it.
                  // Useful when the user has been clicking around and wants
                  // a one-shot "what does the policy think now?" check.
                  //
                  // What it does NOT do: blindly dump high-risk clusters
                  // into test. That was the old behaviour and made
                  // train collapse to 1-2 clusters when the top groups
                  // were all large (which they are on KIBA / Davis).
                  const total   = groups.length;
                  const pinned  = Object.keys(overrides).length;
                  const counts  = { train: 0, val: 0, test: 0 };
                  const pairs   = { train: 0, val: 0, test: 0 };
                  groups.forEach(g => {
                    const s = splitAssignment[g.id];
                    if (!s) return;
                    counts[s] += 1;
                    pairs[s]  += g.n;
                  });
                  const totalP = pairs.train + pairs.val + pairs.test || 1;
                  const pct = (x) => Math.round(100 * x / totalP);
                  // The split assignment is recomputed automatically every
                  // time policy/ratios/overrides change (it lives in a
                  // useMemo upstream), so this button is a confirmation,
                  // not a re-trigger. Be honest about that in the toast
                  // — the previous wording made users think it was a
                  // separate compute pass.
                  toast({
                    title: pinned > 0
                      ? `Snapshot: current assignment (${pinned} pin${pinned === 1 ? "" : "s"} preserved)`
                      : `Snapshot: current assignment`,
                    body: (
                      `Clusters: train ${counts.train} · val ${counts.val} · test ${counts.test}. `
                      + `Pair shares: ${pct(pairs.train)}% / ${pct(pairs.val)}% / ${pct(pairs.test)}% `
                      + `(targets ${trainPct}/${valPct}/${testPct}). `
                      + `Changing policy / ratios / pins updates this live; `
                      + `the "Recompute splits" button is what re-queries the warehouse.`
                    ),
                    level: "ok",
                    ttl_ms: 5200,
                  });
                  if (total < 4) {
                    toast({
                      title: "Few clusters total",
                      body: `Only ${total} clusters returned by the leakage report. Broaden the relationship set (toggle more on in the Relationships card) to produce more, smaller groups.`,
                      level: "warn",
                      ttl_ms: 5600,
                    });
                  }
                }}>
                {/* Renamed from "Auto-balance" — that name overpromised.
                    The button doesn't trigger a new compute pass; it
                    confirms the live policy+ratio+override assignment
                    and prints the resulting train/val/test breakdown.
                    "Show current breakdown" is honest about that. */}
                Show current breakdown <Ico name="sparkle" size={12} />
              </button>
            </div>
            <div ref={canvasRef}
                 style={{ position: "relative", height: 360, background: "linear-gradient(180deg, var(--bg-soft), var(--surface) 80%)" }}
                 className="grid-bg">
              {/* Three drop zones with vivid backgrounds + percentages */}
              <div style={{ position: "absolute", inset: 0, display: "grid", gridTemplateColumns: "1fr 1fr 1fr", pointerEvents: "none" }}>
                {[
                  { z: "train", label: "TRAIN", pct: trainPct, n: splitCounts.train, pairs: pairCounts.train, color: "var(--primary)" },
                  { z: "val",   label: "VAL",   pct: valPct,   n: splitCounts.val,   pairs: pairCounts.val,   color: "var(--molecular)" },
                  { z: "test",  label: "TEST",  pct: testPct,  n: splitCounts.test,  pairs: pairCounts.test,  color: "var(--signal)" },
                ].map((z, i) => (
                  <div key={z.z} style={{
                    borderRight: i < 2 ? "1px dashed var(--border-strong)" : "none",
                    position: "relative",
                    background: drag ? `linear-gradient(180deg, ${z.color}11, transparent 90px)` : `linear-gradient(180deg, ${z.color}06, transparent 60px)`,
                  }}>
                    <div style={{ position: "absolute", top: 10, left: 12, fontFamily: "var(--font-mono)", fontSize: 10, letterSpacing: "0.1em", color: z.color, textTransform: "uppercase", fontWeight: 600 }}>
                      {z.label} · {z.n} cluster{z.n === 1 ? "" : "s"}
                    </div>
                    <div style={{ position: "absolute", top: 10, right: 12, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)" }}>
                      {z.pct}% target · {fmt.n(z.pairs)} pairs
                    </div>
                  </div>
                ))}
              </div>

              {/* SVG — leak-edges + draggable cluster bubbles laid out by zone */}
              <svg ref={svgRef} viewBox={`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`}
                style={{ position: "absolute", inset: 0, width: "100%", height: "100%", cursor: drag ? "grabbing" : "default" }}>
                {/* Leak-edges drawn between cluster *layout* positions */}
                {groups.map((g, i) => groups.slice(i + 1).map((h, j) => {
                  if (!(g._edgeKeep && h._edgeKeep)) return null;
                  const pairIsLigand = (g.axis === "ligand" || h.axis === "ligand") && !(g.axis === "protein" || h.axis === "protein");
                  const pairIsProtein = (g.axis === "protein" && h.axis === "protein");
                  const T = pairIsLigand ? ligThresh : pairIsProtein ? protThresh : Math.max(protThresh, ligThresh);
                  if (Math.min(g.similarity, h.similarity) < T) return null;
                  if (clusterView === "protein" && (g.axis === "ligand" || h.axis === "ligand")) return null;
                  if (clusterView === "ligand"  && (g.axis === "protein" || h.axis === "protein")) return null;
                  // Edges between clusters in DIFFERENT splits are the dangerous ones —
                  // those are the actual potential-leak edges.
                  const gSplit = splitAssignment[g.id], hSplit = splitAssignment[h.id];
                  const crossSplit = gSplit && hSplit && gSplit !== hSplit;
                  const gPos = layoutByZone[g.id] || { cx: g.cx, cy: g.cy };
                  const hPos = layoutByZone[h.id] || { cx: h.cx, cy: h.cy };
                  return (
                    <line key={`${i}-${j}`}
                      x1={gPos.cx} y1={gPos.cy} x2={hPos.cx} y2={hPos.cy}
                      stroke="var(--error)"
                      strokeOpacity={crossSplit ? 0.65 : 0.25}
                      strokeWidth={crossSplit ? 1.4 : 0.7}
                      strokeDasharray={crossSplit ? "4 2" : "2 3"} />
                  );
                }))}

                {/* Cluster bubbles laid out by zone, draggable */}
                {groups.map((g) => {
                  const dim = (clusterView === "protein" && g.axis === "ligand")
                           || (clusterView === "ligand"  && g.axis === "protein");
                  const split = splitAssignment[g.id] || "train";
                  const c = split === "train" ? "var(--primary)" : split === "val" ? "var(--molecular)" : "var(--signal)";
                  const pos = layoutByZone[g.id] || { cx: 0, cy: 0 };
                  const isDragging = drag && drag.gid === g.id;
                  const cx = isDragging ? drag.x : pos.cx;
                  const cy = isDragging ? drag.y : pos.cy;
                  return (
                    <g key={g.id}
                      transform={`translate(${cx} ${cy})`}
                      opacity={dim ? 0.3 : 1}
                      style={{ cursor: isDragging ? "grabbing" : "grab", transition: isDragging ? "none" : "transform 280ms cubic-bezier(0.4, 0, 0.2, 1)" }}
                      onMouseDown={(e) => beginDrag(e, g.id)}>
                      <title>{`${g.id} · ${g.kind} · axis=${g.axis} · split=${split}${overrides[g.id] ? " (manual)" : ""} · drag to move`}</title>
                      <circle r={g.r + 4} fill={c} opacity="0.10" />
                      <circle r={g.r} fill={c} opacity="0.28" stroke={c} strokeWidth={isDragging ? 2 : 1} />
                      <text textAnchor="middle" dy="3" fontSize="9" fontFamily="var(--font-mono)" fill="var(--text)">
                        {g.short || (g.id || "").slice(3)}
                      </text>
                      {g.risk === "high" && !dim && (
                        <circle r={g.r + 6} fill="none" stroke="var(--error)" strokeWidth="1" strokeDasharray="3 2">
                          <animate attributeName="opacity" values="0.3;0.9;0.3" dur="2.4s" repeatCount="indefinite" />
                        </circle>
                      )}
                      {overrides[g.id] && (
                        <circle r={g.r + 8} fill="none" stroke="var(--warn)" strokeWidth="1" strokeDasharray="1 2" />
                      )}
                    </g>
                  );
                })}
              </svg>

              {/* Overlay legend */}
              <div style={{ position: "absolute", bottom: 12, left: 12, display: "flex", gap: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: "var(--primary)", marginRight: 4 }} />train · {splitCounts.train}</span>
                <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: "var(--molecular)", marginRight: 4 }} />val · {splitCounts.val}</span>
                <span><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 4, background: "var(--signal)", marginRight: 4 }} />test · {splitCounts.test}</span>
                <span style={{ color: "var(--error)" }}><span style={{ display: "inline-block", width: 16, borderTop: "1.4px dashed var(--error)", marginRight: 4, verticalAlign: "middle" }} />cross-split leak (bad)</span>
                <span style={{ color: "var(--warn)" }}><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 50, border: "1px dashed var(--warn)", marginRight: 4, verticalAlign: "middle" }} />manual override</span>
              </div>
            </div>

            <div style={{ padding: 14, borderTop: "1px solid var(--border)", display: "grid", gridTemplateColumns: "1fr 1fr 1.4fr", gap: 18, alignItems: "center" }} data-field="split.thresholds">
              <div>
                <div className="label" title="MMseqs2 sequence identity (Steinegger & Söding 2017). Recommended default 0.30 for honest leakage control.">
                  Protein identity (MMseqs2) · <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-strong)" }}>{protThresh.toFixed(2)}</span>
                </div>
                <input type="range" min="0.20" max="0.95" step="0.05" value={protThresh}
                  onChange={e => setProtThresh(parseFloat(e.target.value))}
                  aria-label={`Protein sequence identity threshold ${protThresh.toFixed(2)}`}
                  style={{ width: "100%" }} />
              </div>
              <div>
                <div className="label" title="ECFP4/Morgan-r2 Tanimoto. Recommended default 0.40 for honest leakage control.">
                  Ligand Tanimoto (ECFP4) · <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-strong)" }}>{ligThresh.toFixed(2)}</span>
                </div>
                <input type="range" min="0.20" max="0.95" step="0.05" value={ligThresh}
                  onChange={e => setLigThresh(parseFloat(e.target.value))}
                  aria-label={`Ligand Tanimoto threshold ${ligThresh.toFixed(2)}`}
                  style={{ width: "100%" }} />
              </div>
              <div>
                <div className="label">Train / Val / Test ratio · <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-strong)" }}>{trainPct}/{valPct}/{testPct}</span></div>
                <SplitRatioSlider train={trainPct} val={valPct} test={testPct} onChange={(t, v) => { setTrainPct(t); setValPct(v); }} />
              </div>
            </div>
            <div data-field="split.cold_target_pct" style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", fontSize: 11, color: "var(--muted)" }}>
              Cold-target coverage in test: <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-strong)" }}>{(D.split_metrics?.cold_target_pct ?? 6.8).toFixed(1)}%</span>
              <span style={{ color: "var(--dim)" }}> · how much of the test set hits proteins absent from train</span>
            </div>
          </div>

          {/* Leakage relationships — multi-select for what counts as "too similar" */}
          <RelationshipsCard
            relationships={relationships}
            toggleRelationship={toggleRelationship}
            mergeMode={mergeMode}
            setMergeMode={setMergeMode}
            mergeModeDef={mergeModeDef}
            megaClusterRiskLevel={megaClusterRiskLevel}
            onOpenExplainer={() => setExplainerOpen(true)}
            protThresh={protThresh}
            setProtThresh={setProtThresh}
            ligThresh={ligThresh}
            setLigThresh={setLigThresh}
            toast={toast}
          />

          {/* Live relationship counts — only render when backend data is available */}
          {isLive && liveReport.relationships && liveReport.relationships.length > 0 && (
            <div className="card">
              <div className="card-h">
                <span className="t">Cross-source leakage edges · live counts</span>
                <Chip tone="muted">
                  {liveReport.universe.n_uniprots.toLocaleString()} proteins · {liveReport.universe.n_ligands.toLocaleString()} ligands
                </Chip>
              </div>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Relationship</th><th>Axis</th><th style={{ textAlign: "right" }}>Total edges</th><th style={{ textAlign: "right" }}>Cross-source</th><th style={{ textAlign: "right" }}>% cross</th>
                  </tr>
                </thead>
                <tbody>
                  {liveReport.relationships.map(rel => {
                    const pct = rel.edges_total > 0 ? (100 * rel.edges_cross_source / rel.edges_total) : 0;
                    const pctTone = pct >= 50 ? "warn" : pct >= 20 ? "info" : "ok";
                    return (
                      <tr key={rel.kind}>
                        <td style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}>{rel.kind}</td>
                        <td>{rel.axis}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                          {rel.edges_total.toLocaleString()}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                          {rel.edges_cross_source.toLocaleString()}
                        </td>
                        <td style={{ textAlign: "right" }}>
                          <Chip tone={pctTone}>{pct.toFixed(1)}%</Chip>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", fontSize: 11, color: "var(--muted)" }}>
                Computed JIT via self-joins on the v2 catalog's membership signatures. Snapshot: <span style={{ fontFamily: "var(--font-mono)" }}>{liveReport.snapshot_at}</span>
              </div>
            </div>
          )}

          {/* Leakage table */}
          <div className="card">
            <div className="card-h">
              <span className="t">Leakage groups · risk audit</span>
              <Chip tone="warn" dot>{groups.filter(g => g.risk === "high").length} hot</Chip>
              {Object.keys(overrides).length > 0 && (
                <Chip tone="warn">{Object.keys(overrides).length} manual override{Object.keys(overrides).length === 1 ? "" : "s"}</Chip>
              )}
              <div style={{ flex: 1 }} />
              <span style={{ fontSize: 11, color: "var(--dim)" }}>
                Reset / Auto-balance live in the plot card above.
              </span>
            </div>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Group</th><th>N pairs</th><th>Pocket / motif</th><th>Internal sim.</th><th>Split</th><th>Risk</th><th></th>
                </tr>
              </thead>
              <tbody>
                {effectiveLeakageGroups.slice(0, 6).map((g) => (
                  <LeakageRow
                    key={g.id}
                    g={g}
                    split={splitAssignment[g.id] || "train"}
                    overridden={!!overrides[g.id]}
                    onChange={(next) => {
                      setRowSplit(g.id, next);
                      toast({
                        title: `Moved ${g.id} → ${next}`,
                        body: `Cluster reassigned; the bubble flips color, the legend recount, and the overlap warnings recompute.`,
                        level: "info",
                        ttl_ms: 2400,
                      });
                    }}
                    toast={toast}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right column */}
        <div style={{ display: "flex", flexDirection: "column", gap: 16, position: "sticky", top: 16, alignSelf: "flex-start" }}>
          <div className="card elevated" style={{ borderLeft: `3px solid var(--${objectiveDef.tone})` }}>
            <div className="card-h">
              <span className="t">Split summary</span>
              <Chip tone={objectiveDef.tone}>{objectiveDef.short}</Chip>
            </div>
            <div className="card-b">
              <SplitBar train={trainPct} val={valPct} test={testPct} />
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10, marginTop: 14 }}>
                <Stat k="Train pairs" v={fmt.short(pairCounts.train)} mono delta={`${splitCounts.train} cluster${splitCounts.train === 1 ? "" : "s"}`} />
                <Stat k="Val pairs"   v={fmt.short(pairCounts.val)}   mono delta={`${splitCounts.val} cluster${splitCounts.val === 1 ? "" : "s"}`} />
                <Stat k="Test pairs"  v={fmt.short(pairCounts.test)}  mono delta={`${splitCounts.test} cluster${splitCounts.test === 1 ? "" : "s"}`} />
              </div>
              <hr className="hr" />
              <Stat k="Cold targets in test"
                v={`${Math.round((D.split_metrics?.cold_target_pct ?? 6.8) * 21.84)} / 2,184`}
                mono
                delta={`${(D.split_metrics?.cold_target_pct ?? 6.8).toFixed(1)}% unseen in train`} />
              <div style={{ marginTop: 10 }}>
                <Stat k="Cold scaffolds in test"
                  v={fmt.n(Math.round(2491 * (clusterCap < 1 ? clusterCap * 5 : 1)))}
                  mono delta="5.2% of scaffolds unseen" />
              </div>
            </div>
          </div>

          {/* Per-cluster cap — thin over-represented groups */}
          <div className="card">
            <div className="card-h">
              <span className="t">Balance · per-cluster cap</span>
              {clusterCap < 1 && <Chip tone="primary">capped</Chip>}
            </div>
            <div className="card-b">
              <div className="label">
                Largest cluster can contribute at most:{" "}
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--text-strong)" }}>
                  {clusterCap >= 1 ? "uncapped" : `${(clusterCap * 100).toFixed(0)}% of all pairs`}
                </span>
              </div>
              <input type="range" min="0.05" max="1.00" step="0.05" value={clusterCap}
                onChange={e => setClusterCap(parseFloat(e.target.value))}
                aria-label={`Per-cluster cap ${(clusterCap*100).toFixed(0)} percent`}
                style={{ width: "100%" }} />
              <div className="help">
                Some clusters are huge: e.g. <span className="mono" style={{ color: "var(--text-strong)" }}>lg-001</span> has{" "}
                {fmt.n(groups[0]?.n || 0)} pairs out of {fmt.n(totalPairs)} — that's{" "}
                <span style={{ color: groups[0] && groups[0].n / totalPairs > 0.2 ? "var(--warn)" : "var(--text)" }}>
                  {((groups[0]?.n || 0) / Math.max(totalPairs, 1) * 100).toFixed(0)}% of the data
                </span>.
                Capping subsamples them down so no single cluster dominates training.
                Lower = more balanced, less total data; uncapped = all rows, biased toward big groups.
              </div>
              {clusterCap < 1 && (
                <div style={{ marginTop: 10, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                  Before cap: {fmt.n(totalPairs)} pairs · After cap: <span style={{ color: "var(--text-strong)" }}>{fmt.n(totalAfterCap)}</span>
                  {" "}({((totalAfterCap / totalPairs) * 100).toFixed(0)}%)
                </div>
              )}
            </div>
          </div>

          {/* Issues & recommendations — surfaces empty/single-group splits,
              dominant-cluster bias, axis-empty splits, sufficiency fails.
              Each finding ships with a concrete actionable fix. */}
          <IssuesAndRecommendations
            groups={groups}
            splitAssignment={splitAssignment}
            splitCounts={splitCounts}
            pairCounts={pairCounts}
            policy={policy}
            recommended={recommended}
            objective={objective}
            objectiveDef={objectiveDef}
            trainPct={trainPct}
            valPct={valPct}
            testPct={testPct}
            setTrainPct={setTrainPct}
            setValPct={setValPct}
            setPolicy={setPolicy}
            clusterCap={clusterCap}
            setClusterCap={setClusterCap}
            overrides={overrides}
            resetOverrides={resetOverrides}
            toast={toast}
            mergeMode={mergeMode}
            setMergeMode={setMergeMode}
            onOpenExplainer={() => setExplainerOpen(true)}
          />

          {/* Sufficiency check — is the training set actually big enough? */}
          <SufficiencyCheck pairCounts={pairCounts} splitCounts={splitCounts} groups={groups} objective={objective} />

          {/* Diversity & bias — train + val + test, side-by-side */}
          <DiversityBiasCard groups={groups} splitAssignment={splitAssignment} />

          <div className="card">
            <div className="card-h"><span className="t">Overlap warnings</span><Chip tone="error" dot>2</Chip></div>
            <div className="card-b" style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.6 }}>
              <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
                <Ico name="warn" style={{ color: "var(--error)", flexShrink: 0, marginTop: 2 }} />
                <div>
                  <div style={{ color: "var(--text)" }}>lg-001 ↔ lg-005</div>
                  Train cluster lg-001 (kinase ATP) shares 9 ligand scaffolds with test cluster lg-005 (bromodomain). Tanimoto&nbsp;0.71.
                </div>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <Ico name="warn" style={{ color: "var(--warn)", flexShrink: 0, marginTop: 2 }} />
                <div>
                  <div style={{ color: "var(--text)" }}>BTK orthologs across train/val</div>
                  4 sequence-identical orthologs span train and val. Will be collapsed to one split.
                </div>
              </div>
              <hr className="hr" />
              <button type="button" className="btn sm" style={{ width: "100%" }}
                disabled={recomputing}
                onClick={() => {
                  // Actually refetch the leakage report with the user's
                  // current thresholds + merge mode. Previously this only
                  // emitted a toast, so changing the sliders had no
                  // observable effect — the report stayed at whatever the
                  // server's defaults were. We now POST the parameters
                  // and wait for the response; the screen state
                  // (effectiveLeakageGroups, splitAssignment) updates as
                  // soon as setLiveReport(j) fires below.
                  setRecomputing(true);
                  toast({
                    title: "Recomputing splits…",
                    body: `Re-running MMseqs2 (≥${(protThresh*100).toFixed(0)}% identity) + ECFP Tanimoto (≥${ligThresh.toFixed(2)}) with merge='${mergeMode}'.`,
                    level: "info", ttl_ms: 3500,
                  });
                  const params = new URLSearchParams({
                    prot_thresh: String(protThresh),
                    lig_thresh:  String(ligThresh),
                    merge_mode:  mergeMode,
                    relationships: Array.from(relationships).join(","),
                  });
                  fetch(`/api/v2/splits/leakage_report?${params.toString()}`)
                    .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
                    .then(j => {
                      window.PS_LIVE_LEAKAGE_REPORT = j;
                      setLiveReport(j);
                      toast({
                        title: "Splits recomputed",
                        body: `${Array.isArray(j.top_groups) ? j.top_groups.length : 0} clusters returned. Auto-balance reapplied.`,
                        level: "ok", ttl_ms: 4000,
                      });
                    })
                    .catch(err => toast({
                      title: "Recompute failed",
                      body: `Backend returned ${String(err.message || err)}. Original report kept.`,
                      level: "error", ttl_ms: 6000,
                    }))
                    .finally(() => setRecomputing(false));
                }}>
                {recomputing ? "Recomputing…" : <>Recompute splits <Ico name="bolt" size={12} /></>}
              </button>
            </div>
          </div>

          <div className="card">
            <div className="card-h"><span className="t">Estimate</span></div>
            <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 6, fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}><span>Build splits</span><span style={{ color: "var(--text)" }}>~ 42 s</span></div>
              <div style={{ display: "flex", justifyContent: "space-between" }}><span>Compute signatures</span><span style={{ color: "var(--text)" }}>cached ✓</span></div>
              <div style={{ display: "flex", justifyContent: "space-between" }}><span>Snapshot artifact</span><span style={{ color: "var(--text)" }}>~ 18 MB</span></div>
            </div>
          </div>
        </div>
      </div>

      {/* Cluster construction explainer — walks the user through the
          A-B-C-D-E transitive-closure scenario and what each merge mode
          does about it. Opens from the Relationships card. */}
      <ClusterConstructionExplainer
        open={explainerOpen}
        onClose={() => setExplainerOpen(false)}
        mergeMode={mergeMode}
        setMergeMode={setMergeMode}
      />
    </div>
  );
}

// Inline picker for the leakage-row "⋯" button. Previously this control
// emitted a placeholder toast describing a non-existent menu; it now
// opens a tiny popover with the three actions that actually map onto
// state we already track (`onChange`-driven split reassignment). The
// open/close state lives inside the picker so React's prop diffing
// doesn't force a row-wide re-render every time the popover toggles.
function LeakageRowActions({ g, split, onChange, toast }) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef(null);
  // Close on click-outside so the popover behaves like a regular menu.
  React.useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);
  const ACTIONS = [
    { id: "train", label: "Lock to train", help: "Keep this leakage group entirely in the train split." },
    { id: "val",   label: "Lock to val",   help: "Hold this group out for validation only." },
    { id: "test",  label: "Lock to test",  help: "Use this group as part of the test panel." },
  ];
  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button type="button" className="btn sm ghost"
        aria-label={`Actions for ${g.id}`}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(o => !o)}>
        <Ico name="more" />
      </button>
      {open && (
        <div role="menu"
          style={{
            position: "absolute", top: "100%", right: 0, marginTop: 4,
            background: "var(--surface)", border: "1px solid var(--border-strong)",
            borderRadius: "var(--r)", boxShadow: "0 10px 24px #000a",
            padding: 4, minWidth: 160, zIndex: 10,
          }}>
          {ACTIONS.map(a => (
            <button key={a.id} type="button" role="menuitem"
              disabled={a.id === split}
              title={a.help}
              onClick={() => {
                onChange(a.id);
                setOpen(false);
                toast({
                  title: `${g.id} → ${a.id}`,
                  body: a.help,
                  level: "ok", ttl_ms: 2200,
                });
              }}
              style={{
                display: "block", width: "100%", padding: "6px 10px",
                textAlign: "left", border: "none", background: "transparent",
                color: a.id === split ? "var(--dim)" : "var(--text)",
                fontSize: 12, cursor: a.id === split ? "not-allowed" : "pointer",
                borderRadius: 3,
              }}
              onMouseEnter={(e) => { if (a.id !== split) e.currentTarget.style.background = "var(--surface-2)"; }}
              onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>
              {a.label}{a.id === split ? "  ✓" : ""}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function LeakageRow({ g, split, overridden, onChange, toast }) {
  return (
    <tr>
      <td className="mono">
        {g.id}
        {overridden && <span title="Manual override" style={{ marginLeft: 4, fontSize: 9, color: "var(--warn)" }}>⬤</span>}
      </td>
      <td className="mono">{fmt.n(g.n)}</td>
      <td>{g.kind}</td>
      <td>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ width: 60, height: 4, background: "var(--surface-3)", borderRadius: 2 }}>
            <div style={{ width: `${g.similarity * 100}%`, height: "100%", background: g.similarity > 0.8 ? "var(--error)" : g.similarity > 0.6 ? "var(--warn)" : "var(--signal)", borderRadius: 2 }} />
          </div>
          <span className="mono">{g.similarity.toFixed(2)}</span>
        </div>
      </td>
      <td>
        <label className="visually-hidden" htmlFor={`split_${g.id}`}>Assign group {g.id} to split</label>
        <select id={`split_${g.id}`} className="select"
          style={{ width: 90, padding: "2px 6px", fontFamily: "var(--font-mono)", fontSize: 11 }}
          value={split}
          onChange={e => onChange(e.target.value)}>
          <option value="train">train</option><option value="val">val</option><option value="test">test</option>
        </select>
      </td>
      <td>
        {g.risk === "high" && <Chip tone="error" dot>high</Chip>}
        {g.risk === "med" && <Chip tone="warn" dot>medium</Chip>}
        {g.risk === "low" && <Chip tone="signal" dot>low</Chip>}
      </td>
      <td>
        {/* The "more" button used to emit a placeholder toast describing
            an imaginary menu. We now expose the three actions that ARE
            implemented today (lock-to-train, lock-to-val, drop-from-split)
            as a real inline picker: clicking the button toggles a tiny
            popover with one button per action. Each action mutates the
            row's split assignment through the same `onChange` callback
            the <select> uses, so the split-counts + crossing-detection
            logic upstream picks it up immediately. */}
        <LeakageRowActions g={g} split={split} onChange={onChange} toast={toast} />
      </td>
    </tr>
  );
}

// ── Sufficiency check ─────────────────────────────────────────────
// Heuristic guidance on whether the training set is large enough for the
// chosen objective. The rules of thumb baked in:
//   • DTA generalisation: ≥ 100K train pairs for a cross-attention head;
//     ≥ 1M pairs (or LoRA / linear probe) to fine-tune ESM-2 650M.
//   • Per-target coverage: ≥ 50 measurements per protein for cold-target
//     transfer to behave (Pahikkala 2014).
//   • Per-scaffold coverage: ≥ 10 distinct scaffolds in train per family
//     for cold-scaffold transfer.
//   • Validation: ≥ 5K pairs to make early-stopping signal stable.
//   • Test: ≥ 5K pairs for paired bootstrap to have <5% CI on Pearson.
function SufficiencyCheck({ pairCounts, splitCounts, groups, objective }) {
  // The pair counts in the prototype are scaled — multiply by a fixture
  // factor so the thresholds feel right. Real wiring would use raw counts.
  const SCALE = 1400;  // scales 11 lg groups × ~700 pairs ≈ 10K to a realistic ~14M
  const trainN = pairCounts.train * SCALE;
  const valN   = pairCounts.val   * SCALE;
  const testN  = pairCounts.test  * SCALE;
  const tightEval = objective === "generalization";
  const checks = [
    {
      label: "Train pairs",
      value: fmt.n(trainN),
      ok: trainN >= 100_000,
      warn: trainN >= 30_000,
      target: "≥ 100K for cross-attention; ≥ 1M to fine-tune ESM-2 650M",
    },
    {
      label: "Val pairs",
      value: fmt.n(valN),
      ok: valN >= 5_000,
      warn: valN >= 1_500,
      target: "≥ 5K so early-stopping signal isn't noisy",
    },
    {
      label: "Test pairs",
      value: fmt.n(testN),
      ok: testN >= 5_000,
      warn: testN >= 1_500,
      target: "≥ 5K so paired-bootstrap CI on Pearson is < 0.02",
    },
    {
      label: "Train clusters",
      value: splitCounts.train,
      ok: splitCounts.train >= 8,
      warn: splitCounts.train >= 4,
      target: tightEval ? "≥ 8 distinct clusters for cold-target transfer" : "≥ 4 clusters is fine for interpolation",
    },
  ];
  const allOk = checks.every(c => c.ok);
  const anyFail = checks.some(c => !c.warn);
  const overall = allOk ? "ok" : anyFail ? "fail" : "warn";
  const overallText = overall === "ok" ? "Sized appropriately for the objective"
    : overall === "warn" ? "Borderline — model may train but generalisation will be noisy"
    : "Underpowered — model will likely overfit or fail to generalise";
  const overallTone = overall === "ok" ? "signal" : overall === "warn" ? "warn" : "error";
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Sufficiency check</span>
        <Chip tone={overallTone} dot>{overall === "ok" ? "ok" : overall === "warn" ? "borderline" : "underpowered"}</Chip>
      </div>
      <div className="card-b">
        <div style={{ fontSize: 12, color: `var(--${overallTone})`, fontWeight: 500, marginBottom: 8 }}>{overallText}</div>
        {checks.map((c, i) => {
          const tone = c.ok ? "signal" : c.warn ? "warn" : "error";
          return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderTop: i > 0 ? "1px solid var(--border-soft)" : "0" }}>
              <Ico name={c.ok ? "check" : "warn"} size={11} style={{ color: `var(--${tone})`, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 12 }}>
                  <span style={{ color: "var(--muted)" }}>{c.label}: </span>
                  <span className="mono" style={{ color: `var(--${tone})` }}>{c.value}</span>
                </div>
                <div style={{ fontSize: 10, color: "var(--dim)", lineHeight: 1.4 }}>{c.target}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Diversity & bias card ────────────────────────────────────────
// Reports how broad the train split is across protein axis, ligand axis,
// and risk tiers. Surfaces any dominant cluster (>= 25% of pairs) and
// any axis that's heavily under-represented.
// ── Diversity & bias card ────────────────────────────────────────
// All three splits get the lens: imbalance in val/test is just as bad as
// in train (a single-cluster test set produces noise-dominated metrics;
// a val set without ligand diversity early-stops on the wrong thing).
function DiversityBiasCard({ groups, splitAssignment }) {
  const summary = (whichSplit) => {
    const gs = groups.filter(g => splitAssignment[g.id] === whichSplit);
    const total = gs.reduce((s, g) => s + g.n, 0) || 1;
    const byAxis = { protein: 0, ligand: 0, joint: 0 };
    gs.forEach(g => { byAxis[g.axis || "protein"] += g.n; });
    const sorted = [...gs].sort((a, b) => b.n - a.n);
    const dominant = sorted[0];
    return {
      whichSplit, gs, total, byAxis, dominant,
      dominantPct: dominant ? dominant.n / total : 0,
      nClusters: gs.length,
    };
  };
  const splits = [
    { id: "train", label: "Train", color: "var(--primary)", critical: 30 },
    { id: "val",   label: "Val",   color: "var(--molecular)", critical: 60 },  // val tolerates a bit more bias
    { id: "test",  label: "Test",  color: "var(--signal)", critical: 50 },     // test less so
  ].map(s => ({ ...s, ...summary(s.id) }));
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Diversity &amp; bias</span>
        <span className="sub">how broad each split is</span>
      </div>
      <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        {splits.map(s => {
          const tooFew = s.nClusters <= 1;
          const dom = s.dominantPct >= (s.critical / 100);
          const empty = s.nClusters === 0;
          const tone = empty ? "error" : tooFew ? "error" : dom ? "warn" : "signal";
          return (
            <div key={s.id}>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6, marginBottom: 4 }}>
                <div style={{ width: 10, height: 10, borderRadius: 3, background: s.color }} />
                <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-strong)" }}>{s.label}</span>
                <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>
                  · {s.nClusters} cluster{s.nClusters === 1 ? "" : "s"} · {fmt.short(s.total)} pairs
                </span>
                <div style={{ flex: 1 }} />
                {empty   && <Chip tone="error" dot>empty</Chip>}
                {tooFew  && !empty && <Chip tone="error" dot>single cluster</Chip>}
                {dom     && !tooFew && <Chip tone="warn" dot>dominant</Chip>}
                {!empty && !tooFew && !dom && <Chip tone="signal" dot>balanced</Chip>}
              </div>
              {!empty && (
                <>
                  <div style={{ display: "flex", height: 6, borderRadius: 3, overflow: "hidden", background: "var(--surface-3)" }}>
                    <div title={`protein-axis: ${fmt.n(s.byAxis.protein)} pairs`} style={{ width: `${(s.byAxis.protein / s.total) * 100}%`, background: "var(--molecular)" }} />
                    <div title={`ligand-axis: ${fmt.n(s.byAxis.ligand)} pairs`}  style={{ width: `${(s.byAxis.ligand  / s.total) * 100}%`, background: "var(--signal)" }} />
                    <div title={`joint-axis: ${fmt.n(s.byAxis.joint)} pairs`}    style={{ width: `${(s.byAxis.joint   / s.total) * 100}%`, background: "var(--primary)" }} />
                  </div>
                  <div style={{ display: "flex", gap: 8, fontFamily: "var(--font-mono)", fontSize: 9, color: "var(--muted)", marginTop: 2 }}>
                    <span style={{ color: "var(--molecular)" }}>prot {((s.byAxis.protein/s.total)*100).toFixed(0)}%</span>
                    <span style={{ color: "var(--signal)" }}>lig {((s.byAxis.ligand/s.total)*100).toFixed(0)}%</span>
                    <span style={{ color: "var(--primary)" }}>joint {((s.byAxis.joint/s.total)*100).toFixed(0)}%</span>
                    {s.dominantPct >= 0.30 && (
                      <span style={{ color: "var(--warn)", marginLeft: "auto" }}>
                        {s.dominant.id} = {(s.dominantPct*100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Issues & recommendations ─────────────────────────────────────
// Detects concrete problems in the current split design and offers a
// one-click fix when possible. Each item is actionable: click the button
// to apply the suggested remedy. The whole card is reactive — once the
// underlying problem is resolved, the item disappears.
function IssuesAndRecommendations({
  groups, splitAssignment, splitCounts, pairCounts,
  policy, recommended, objective, objectiveDef,
  trainPct, valPct, testPct,
  setTrainPct, setValPct, setPolicy,
  clusterCap, setClusterCap,
  overrides, resetOverrides,
  toast,
  // Merge-mode is plumbed in so we can detect mega-cluster risk and offer
  // a one-click switch to per-subunit splitting.
  mergeMode, setMergeMode, onOpenExplainer,
}) {
  const issues = [];

  // ── Mega-cluster detection ────────────────────────────────────────
  // The single most common pathology: union-mode + transitive closure
  // produces a cluster that swallows ≥ 25% of the data. Trigger this BEFORE
  // the per-split checks because mega-clusters break every downstream check.
  const totalPairs = groups.reduce((s, g) => s + g.n, 0) || 1;
  const sortedAll = [...groups].sort((a, b) => b.n - a.n);
  const biggest = sortedAll[0];
  const biggestPct = biggest ? biggest.n / totalPairs : 0;
  if (biggestPct >= 0.40 && mergeMode === "union") {
    issues.push({
      sev: "error", area: "mega",
      title: `Mega-cluster detected: ${biggest.id} is ${(biggestPct*100).toFixed(0)}% of all pairs`,
      body: `In ${objectiveDef.short} mode with "union" merging, transitive closure has produced one giant cluster. Splits will be unusable — most data will land in whichever split contains this cluster.`,
      fix: setMergeMode ? {
        label: "Switch merge mode → per-subunit",
        action: () => { setMergeMode("per_subunit"); toast({ title: "Merge mode → per-subunit", body: "Proteins (not pairs) get clustered. Pairs inherit their proteins' splits. Big clusters break up.", level: "info" }); },
      } : null,
    });
  } else if (biggestPct >= 0.25 && mergeMode === "union") {
    issues.push({
      sev: "warn", area: "mega",
      title: `Large cluster forming: ${biggest.id} = ${(biggestPct*100).toFixed(0)}% of all pairs`,
      body: `Union mode with multiple active relationships can grow this cluster fast. Consider switching merge mode or tightening per-cluster cap.`,
      fix: onOpenExplainer ? {
        label: "Open cluster explainer",
        action: onOpenExplainer,
      } : null,
    });
  }

  // Per-split structural problems
  ["train", "val", "test"].forEach(s => {
    const n = splitCounts[s] || 0;
    if (n === 0) {
      issues.push({
        sev: "error", area: s,
        title: `${s.toUpperCase()} is empty`,
        body: `No clusters are assigned to ${s}. Metrics on this split will be undefined.`,
        fix: { label: `Move 1 cluster to ${s}`, action: () => {
          // Move the smallest cluster from the largest split to the empty one
          const fromSplit = splitCounts.train >= splitCounts.val ? "train" : "val";
          const sortedFrom = groups.filter(g => splitAssignment[g.id] === fromSplit).sort((a, b) => a.n - b.n);
          if (sortedFrom[0]) {
            // Use override to force placement
            const evt = new CustomEvent("ps-split-override", { detail: { gid: sortedFrom[0].id, to: s } });
            window.dispatchEvent(evt);
            toast({ title: `Moved ${sortedFrom[0].id} → ${s}`, body: `Smallest cluster from ${fromSplit} reassigned to ${s}.`, level: "info" });
          }
        }},
      });
    } else if (n === 1) {
      const onlyGroup = groups.find(g => splitAssignment[g.id] === s);
      issues.push({
        sev: "error", area: s,
        title: `${s.toUpperCase()} has only one cluster (${onlyGroup?.id})`,
        body: s === "test"
          ? "A single-cluster test set means your metrics are measuring performance on one narrow region of pair-space — bootstrap CIs will be huge and conclusions can't generalise."
          : "A single-cluster val set will early-stop on whatever's peculiar about that one cluster.",
        fix: trainPct >= 75 ? {
          label: `Lower train % to give ${s} more clusters`,
          action: () => {
            setTrainPct(Math.max(50, trainPct - 10));
            toast({ title: "Train ratio dropped 10pp", body: `More clusters will spill into val/test on next recompute. New split: ${trainPct - 10}/${valPct}/${100 - (trainPct - 10) - valPct}.`, level: "info" });
          },
        } : Object.keys(overrides).length > 0 ? {
          label: `Reset manual overrides`,
          action: () => { resetOverrides(); toast({ title: "Overrides cleared", body: "Cluster assignments revert to the policy + ratio computation.", level: "info" }); },
        } : {
          label: `Switch to a more granular policy`,
          action: () => { setPolicy("cluster"); toast({ title: "Policy → leakage-aware cluster", body: "Spreads clusters more evenly across splits.", level: "info" }); },
        },
      });
    }
  });

  // Imbalance: one split has ≥ 5× the pairs of another.
  // Two distinct causes need distinct fixes:
  //   (a) cluster-count ratios are skewed → adjust train/val/test percentages
  //   (b) pair counts dominated by a few huge clusters → lower per-cluster cap
  // Heuristic: if cluster *counts* are roughly balanced (max/min ≤ 2) but
  // pair counts are skewed (≥ 5×), the cause is (b).
  const maxPairs = Math.max(pairCounts.train, pairCounts.val, pairCounts.test);
  const minPairs = Math.min(pairCounts.train, pairCounts.val, pairCounts.test);
  if (minPairs > 0 && maxPairs / Math.max(minPairs, 1) > 5 && (pairCounts.val < 1500 || pairCounts.test < 1500)) {
    const maxClusters = Math.max(splitCounts.train, splitCounts.val, splitCounts.test);
    const minClusters = Math.min(splitCounts.train, splitCounts.val, splitCounts.test);
    const clusterRatioOk = maxClusters / Math.max(minClusters, 1) <= 2;
    const pairOverflow = clusterRatioOk;  // pair imbalance with balanced cluster counts → cap is the lever
    if (pairOverflow && clusterCap >= 0.30) {
      issues.push({
        sev: "warn", area: "ratio",
        title: "Pair counts are imbalanced — a few clusters dominate",
        body: `Largest split has ${(maxPairs / minPairs).toFixed(1)}× the pairs of the smallest, but the *cluster* counts are roughly balanced. That means one or two huge clusters are inflating one split. Lowering the per-cluster cap thins them down so every split's pair count is comparable.`,
        fix: {
          label: `Tighten per-cluster cap to ${Math.max(0.10, clusterCap - 0.10).toFixed(2)}`,
          action: () => {
            const next = Math.max(0.10, clusterCap - 0.10);
            setClusterCap(next);
            toast({ title: `Per-cluster cap → ${(next*100).toFixed(0)}%`, body: "Big clusters subsampled; pair counts should now be more comparable across splits.", level: "info" });
          },
        },
      });
    } else if (!clusterRatioOk) {
      issues.push({
        sev: "warn", area: "ratio",
        title: "Split sizes are heavily imbalanced",
        body: `Largest split has ${(maxClusters / minClusters).toFixed(1)}× the clusters and ${(maxPairs / minPairs).toFixed(1)}× the pairs of the smallest. Adjust the train/val/test ratio to spread more clusters into val/test.`,
        fix: trainPct > 65 ? {
          label: `Lower train ratio to ${trainPct - 10}%`,
          action: () => { setTrainPct(trainPct - 10); toast({ title: `Train ratio → ${trainPct - 10}%`, body: `Val + test get more clusters on next recompute.`, level: "info" }); },
        } : {
          label: "Auto-balance ratios (70/15/15)",
          action: () => { setTrainPct(70); setValPct(15); toast({ title: "Ratios → 70/15/15", body: "Standard split applied.", level: "info" }); },
        },
      });
    } else {
      // Cluster cap already low, cluster counts already balanced —
      // imbalance is structural (one cluster is just way bigger than all others).
      issues.push({
        sev: "warn", area: "ratio",
        title: `Pair-count imbalance is structural (largest cluster is much bigger than the rest)`,
        body: `${(maxPairs / minPairs).toFixed(1)}× imbalance even with cap at ${(clusterCap*100).toFixed(0)}%. Consider broadening the leakage relationships (so the giant cluster splits into smaller ones), or accept the imbalance and weight the loss inversely.`,
        fix: null,
      });
    }
  }

  // Dominant cluster anywhere in val/test
  ["val", "test"].forEach(s => {
    const gs = groups.filter(g => splitAssignment[g.id] === s);
    const total = gs.reduce((sum, g) => sum + g.n, 0) || 1;
    const sorted = [...gs].sort((a, b) => b.n - a.n);
    const dom = sorted[0];
    if (dom && (dom.n / total) >= 0.7 && gs.length >= 2) {
      issues.push({
        sev: "warn", area: s,
        title: `${dom.id} dominates ${s.toUpperCase()} (${((dom.n/total)*100).toFixed(0)}%)`,
        body: `Metrics on this split will mostly reflect that one cluster's behaviour, not the model's overall ${s === "test" ? "generalisation" : "validation"} performance.`,
        fix: {
          label: clusterCap >= 1 ? `Cap per-cluster contribution at 30%` : `Tighten per-cluster cap`,
          action: () => { setClusterCap(Math.min(clusterCap, 0.30) - 0.05); toast({ title: "Per-cluster cap lowered", body: "Big clusters subsampled; balance should improve.", level: "info" }); },
        },
      });
    }
  });

  // Axis bias: val or test missing protein-axis or ligand-axis entirely
  ["val", "test"].forEach(s => {
    const gs = groups.filter(g => splitAssignment[g.id] === s);
    const ligands = gs.filter(g => g.axis === "ligand").length;
    const proteins = gs.filter(g => g.axis === "protein").length;
    if (gs.length >= 2 && ligands === 0 && objective === "generalization") {
      issues.push({
        sev: "warn", area: s,
        title: `${s.toUpperCase()} has no ligand-axis clusters`,
        body: `For a generalisation study, ${s} should expose the model to new chemotypes too — otherwise you only measure protein-side transfer.`,
        fix: {
          label: "Switch to cold-pair policy",
          action: () => { setPolicy("cold-pair"); toast({ title: "Policy → cold-pair", body: "Both protein AND ligand axes spread across splits.", level: "info" }); },
        },
      });
    }
    if (gs.length >= 2 && proteins === 0 && objective === "generalization") {
      issues.push({
        sev: "warn", area: s,
        title: `${s.toUpperCase()} has no protein-axis clusters`,
        body: `For a generalisation study, ${s} should expose the model to new proteins too — otherwise you only measure chemistry-side transfer.`,
        fix: { label: "Switch to cold-target policy", action: () => { setPolicy("cold-target"); toast({ title: "Policy → cold-target", body: "Protein-axis clusters spread across splits.", level: "info" }); } },
      });
    }
  });

  // Policy / objective mismatch
  if (!recommended.has(policy)) {
    const better = Array.from(recommended)[0];
    issues.push({
      sev: "warn", area: "policy",
      title: `Policy "${policy}" isn't ideal for a ${objectiveDef.short} study`,
      body: `For ${objectiveDef.short}, the recommended policies are ${Array.from(recommended).join(", ")}. Sticking with the current policy will produce metrics that aren't honest for the chosen objective.`,
      fix: { label: `Switch to "${better}"`, action: () => { setPolicy(better); toast({ title: `Policy → ${better}`, body: "Recommended for the current objective.", level: "info" }); } },
    });
  }

  // Pair-count sufficiency (cross-check with SufficiencyCheck heuristics)
  const SCALE = 1400;
  if (pairCounts.test * SCALE < 5000) {
    issues.push({
      sev: "warn", area: "test",
      title: "Test set is small — bootstrap CIs will be wide",
      body: `≈ ${fmt.n(pairCounts.test * SCALE)} test pairs (heuristic target: 5K). Pearson CI may exceed 0.05.`,
      fix: testPct < 20 ? {
        label: "Increase test ratio to 20%",
        action: () => { setTrainPct(Math.max(50, trainPct - (20 - testPct))); toast({ title: "Test ratio → 20%", body: "More pairs in test. Train ratio compensated.", level: "info" }); },
      } : null,
    });
  }

  // Listen for "ps-split-override" events from issue fixes (since
  // setRowSplit lives in the parent scope of this card, this hop lets
  // the empty-split fix function reach it without prop-drilling).
  React.useEffect(() => {
    const handler = (e) => {
      const { gid, to } = e.detail || {};
      if (gid && to) {
        window.dispatchEvent(new CustomEvent("ps-internal-set-override", { detail: { gid, to } }));
      }
    };
    window.addEventListener("ps-split-override", handler);
    return () => window.removeEventListener("ps-split-override", handler);
  }, []);

  if (issues.length === 0) {
    return (
      <div className="card">
        <div className="card-h">
          <span className="t">Issues &amp; recommendations</span>
          <Chip tone="signal" dot>none</Chip>
        </div>
        <div className="card-b" style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.5 }}>
          <Ico name="check" size={11} style={{ color: "var(--signal)", verticalAlign: "middle", marginRight: 4 }} />
          No blocking issues with the current split design.
        </div>
      </div>
    );
  }
  const errCount = issues.filter(i => i.sev === "error").length;
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Issues &amp; recommendations</span>
        {errCount > 0
          ? <Chip tone="error" dot>{errCount} blocking</Chip>
          : <Chip tone="warn" dot>{issues.length} to review</Chip>}
      </div>
      <div className="card-b" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {issues.map((it, i) => (
          <div key={i} style={{
            display: "flex", gap: 8, padding: 8, borderRadius: "var(--r)",
            background: it.sev === "error" ? "var(--error-soft)" : "var(--warn-soft)",
            borderLeft: `3px solid var(--${it.sev})`,
          }}>
            <Ico name="warn" size={12} style={{ color: `var(--${it.sev})`, flexShrink: 0, marginTop: 2 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-strong)", marginBottom: 2 }}>{it.title}</div>
              <div style={{ fontSize: 11, color: "var(--muted)", lineHeight: 1.45, marginBottom: it.fix ? 6 : 0 }}>{it.body}</div>
              {it.fix && (
                <button type="button" className="btn sm" onClick={it.fix.action}>
                  {it.fix.label} <Ico name="bolt" size={10} />
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Leakage relationships card ───────────────────────────────────
// Multi-select: which kinds of similarity count as "leakage" for the
// clusterer. The two integrated, on-by-default relationships drive the
// per-axis threshold sliders; selecting more relationships unions their
// signatures into the leakage clusters.
function RelationshipsCard({
  relationships, toggleRelationship,
  mergeMode, setMergeMode, mergeModeDef, megaClusterRiskLevel, onOpenExplainer,
  protThresh, setProtThresh, ligThresh, setLigThresh, toast,
}) {
  const cat = window.PS_CLUSTER_RELATIONSHIPS || [];
  const proteinSide = cat.filter(r => r.side === "protein");
  const ligandSide  = cat.filter(r => r.side === "ligand");
  const riskTone = megaClusterRiskLevel === "high" ? "error" : megaClusterRiskLevel === "med" ? "warn" : "signal";
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Leakage relationships</span>
        <span className="sub">what counts as "too similar" for the clusterer · multi-select</span>
        <div style={{ flex: 1 }} />
        <Chip tone="signal">{relationships.size} active</Chip>
      </div>

      {/* Merge mode + cluster-construction explainer link */}
      <div style={{ padding: "12px 14px", borderBottom: "1px solid var(--border-soft)", display: "grid", gridTemplateColumns: "1fr auto", gap: 12, alignItems: "center" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span className="label" style={{ margin: 0 }}>How relationships compose</span>
            <Chip tone={riskTone} dot>{mergeModeDef?.short}</Chip>
            {megaClusterRiskLevel === "high" && <Chip tone="error" dot>mega-cluster risk</Chip>}
            {megaClusterRiskLevel === "med"  && <Chip tone="warn"  dot>moderate risk</Chip>}
          </div>
          <select className="select" value={mergeMode} onChange={e => setMergeMode(e.target.value)}>
            {(window.PS_MERGE_MODES || []).map(m => (
              <option key={m.id} value={m.id}>{m.label}</option>
            ))}
          </select>
          <div className="help" style={{ marginTop: 6 }}>
            {mergeModeDef?.desc}
            <br/>
            <span style={{ color: "var(--dim)" }}>Trade-off: {mergeModeDef?.risk}</span>
          </div>
        </div>
        <button type="button" className="btn sm" onClick={onOpenExplainer}
          style={{ alignSelf: "start", whiteSpace: "nowrap" }}>
          <Ico name="info" size={11} /> How clusters form
        </button>
      </div>

      <div className="card-b" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <RelationshipColumn
          title="Protein side"
          items={proteinSide}
          relationships={relationships}
          toggleRelationship={toggleRelationship}
          toast={toast}
        />
        <RelationshipColumn
          title="Ligand side"
          items={ligandSide}
          relationships={relationships}
          toggleRelationship={toggleRelationship}
          toast={toast}
        />
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Cluster construction explainer — walks through the exact "A-B-C-D-E"
// scenario the user asked about and shows what each merge mode does.
// Reuses the accessible Modal primitive from shared-v2.
// ─────────────────────────────────────────────────────────────────────
// ──────────────────────────────────────────────────────────────────
// ClusterConstructionExplainer — Decision G redesign.
// Single-scroll, 4-step narrative with a consistent diagram language:
//   1. Each relationship → its own mini diagram
//   2. Stack them → the adjacency graph
//   3. Merge-mode picker → each mode shows its cluster outcome inline
//   4. Train/val/test buckets under the picked mode
// Same {open, onClose, mergeMode, setMergeMode} contract as before.
// ──────────────────────────────────────────────────────────────────

// Locked entity scheme — 5 colored circles reused across every step
// so the user learns "Pair A is blue, Pair B is violet…" once.
const _CLUSTER_ENTITY = {
  A: { x: 80,  y: 60,  color: "var(--primary)",    label: "Pair A", desc: "A1 + A2 bind; co-crystal resolved" },
  B: { x: 240, y: 60,  color: "var(--molecular)",  label: "Pair B", desc: "complex's 3D fold matches A" },
  C: { x: 50,  y: 170, color: "var(--signal)",     label: "Pair C", desc: "A1 + different partner" },
  D: { x: 270, y: 170, color: "var(--warn)",       label: "Pair D", desc: "A2 + different partner" },
  E: { x: 160, y: 240, color: "var(--error)",      label: "Prot E", desc: "shares Pfam family with A1" },
};
const _CLUSTER_RELATIONSHIPS = [
  { id: "fold",  from: "A", to: "B", label: "Foldseek ≥ 0.6", evidence: "structure similarity" },
  { id: "subA1", from: "A", to: "C", label: "shares A1",      evidence: "subunit-shared" },
  { id: "subA2", from: "A", to: "D", label: "shares A2",      evidence: "subunit-shared" },
  { id: "pfam",  from: "A", to: "E", label: "Pfam family",    evidence: "family-shared" },
];
const _CLUSTER_MERGE_MODES = [
  { id: "union", title: "Union (greedy)", tag: "default", tone: "error",
    cluster: ["A","B","C","D","E"],
    why:  "Transitive closure pulls all in. A↔B via structure, A↔C/D via subunits, A↔E via Pfam. One giant cluster.",
    risk: "When similar chains span the dataset, one cluster can engulf 30–60% of pairs. Splits become useless." },
  { id: "per_subunit", title: "Per-subunit", tag: "recommended for PPI", tone: "signal",
    cluster: ["A","B","C","D"],
    why:  "Each PROTEIN gets its own cluster. A pair lands in the split where ANY of its proteins is held out.",
    risk: "More bookkeeping (proteins, not pairs), but cold-target results become trustworthy." },
  { id: "strict_pair", title: "Strict pair-level", tag: "under-flags subunit leakage", tone: "warn",
    cluster: ["A","B"],
    why:  "Only pair-vs-pair similarity counts. C and D don't merge with A even though they share subunits.",
    risk: "Misses real leakage from held-out pairs containing proteins heavily in train. Pearson looks better than reality." },
  { id: "score_weighted", title: "Score-weighted", tag: "configurable", tone: "primary",
    cluster: ["A","B"],
    why:  "Per-relationship weights (Foldseek 1.0, subunit 0.7, Pfam 0.3) and a merge threshold. Tune sensitivity.",
    risk: "Hardest to debug when clusters look wrong — but the only mode you can actually tune." },
];

function ClusterConstructionExplainer({ open, onClose, mergeMode, setMergeMode }) {
  if (!open) return null;
  const activeMode = _CLUSTER_MERGE_MODES.find(m => m.id === mergeMode) || _CLUSTER_MERGE_MODES[0];
  return (
    <Modal open={open} onClose={onClose}
      title="How leakage clusters form"
      titleIco="layers"
      size="xl"
      ariaLabel="Cluster construction explainer"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>Close</button>
          <button className="btn primary"
            onClick={() => {
              // Apply the recommendation AND close the modal — previously
              // this set the merge mode but left the explainer open, so
              // the user had to manually click Close, which made the
              // primary CTA feel half-broken.
              setMergeMode("per_subunit");
              onClose();
            }}>
            Switch to per-subunit
          </button>
        </>
      }>
      <_ScenarioPreamble />

      {/* STEP 1 — Relationships */}
      <div className="explainer-step">
        <div className="step-n">1</div>
        <div className="step-body">
          <div className="step-title">Each relationship is one piece of evidence</div>
          <div className="step-prose">
            We don't have one "similarity" score — we have <strong>several</strong>, each backed by a different tool.
            Pick which ones you trust in the toolbar above the canvas. Here's how each one connects entities in our scenario.
          </div>
          <div className="step-diagram">
            <_RelationshipsDiagram />
          </div>
        </div>
      </div>

      {/* STEP 2 — Adjacency graph */}
      <div className="explainer-step">
        <div className="step-n">2</div>
        <div className="step-body">
          <div className="step-title">Stack them, and you get a graph</div>
          <div className="step-prose">
            Drop every active relationship's edges onto the same canvas. This is the adjacency graph —
            entities are nodes, evidence is the edges, and clusters are <em>about to happen</em>.
            The graph itself is the same for every merge mode; the modes differ in how aggressively they collapse it.
          </div>
          <div className="step-diagram">
            <_AdjacencyDiagram />
          </div>
        </div>
      </div>

      {/* STEP 3 — Merge mode picker (interactive) */}
      <div className="explainer-step">
        <div className="step-n">3</div>
        <div className="step-body">
          <div className="step-title">A merge mode decides what becomes a cluster</div>
          <div className="step-prose">
            Each option below collapses the graph differently. Click one — the splits in step 4 update to use the chosen mode.
          </div>
          <div className="explainer-modes">
            {_CLUSTER_MERGE_MODES.map(m => (
              <button
                key={m.id}
                type="button"
                className="explainer-mode"
                data-tone={m.tone}
                aria-pressed={mergeMode === m.id}
                onClick={() => setMergeMode(m.id)}>
                <div className="mode-h">
                  <span style={{
                    width: 12, height: 12, borderRadius: "50%",
                    border: `1.4px solid ${mergeMode === m.id ? `var(--${m.tone})` : "var(--border-strong)"}`,
                    background: mergeMode === m.id ? `var(--${m.tone})` : "transparent",
                    flexShrink: 0,
                  }} />
                  <span className="mode-title">{m.title}</span>
                  <Chip tone={m.tone}>{m.tag}</Chip>
                </div>
                <div className="mode-cluster-svg">
                  <_ClusterOutcomeDiagram mode={m} />
                </div>
                <div className="mode-why">{m.why}</div>
                <div className="mode-risk">{m.risk}</div>
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* STEP 4 — Result */}
      <div className="explainer-step">
        <div className="step-n">4</div>
        <div className="step-body">
          <div className="step-title">Now the splits make sense</div>
          <div className="step-prose">
            Pairs in the same cluster always land together — same train, val, or test. That's what makes a "cold-target" benchmark honest.
            For your current selection (<strong>{activeMode.title}</strong>), this is what falls into each split:
          </div>
          <div className="step-diagram">
            <_SplitOutcomeDiagram mode={activeMode} />
          </div>
        </div>
      </div>
    </Modal>
  );
}

// ── Preamble — 5 colored chips so the rest of the modal can refer to them ──
function _ScenarioPreamble() {
  return (
    <div style={{ padding: 12, background: "var(--surface-2)", borderRadius: "var(--r)", marginBottom: 14, display: "flex", flexWrap: "wrap", gap: 14, alignItems: "center" }}>
      <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.06em", textTransform: "uppercase" }}>Scenario · 5 entities</span>
      {Object.entries(_CLUSTER_ENTITY).map(([k, e]) => (
        <span key={k} style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}>
          <_EntityChip color={e.color} letter={k} size={20} />
          <span style={{ color: "var(--text)" }}>{e.label}</span>
          <span style={{ color: "var(--muted)" }}>· {e.desc}</span>
        </span>
      ))}
    </div>
  );
}

// HTML-rendered version of a labeled circle (used inside flex rows).
function _EntityChip({ color, letter, size = 24, dimmed }) {
  return (
    <span style={{
      width: size, height: size, borderRadius: size / 2,
      background: `color-mix(in oklab, ${color} 25%, transparent)`,
      border: `1.4px solid ${color}`,
      color, fontFamily: "var(--font-mono)", fontSize: size === 20 ? 11 : 12, fontWeight: 700,
      display: "inline-grid", placeItems: "center", flexShrink: 0,
      opacity: dimmed ? 0.35 : 1,
    }}>
      {letter}
    </span>
  );
}

// SVG version of the same labeled circle (used inside <svg>).
function _EntityNode({ color, letter, x, y, size = 24, dimmed }) {
  return (
    <g opacity={dimmed ? 0.35 : 1}>
      <circle cx={x} cy={y} r={size / 2}
        fill={`color-mix(in oklab, ${color} 22%, transparent)`}
        stroke={color} strokeWidth="1.6" />
      <text x={x} y={y + 4} textAnchor="middle" fontFamily="var(--font-mono)" fontSize="12" fontWeight="700" fill={color}>
        {letter}
      </text>
    </g>
  );
}

// STEP 1 diagram — 4 mini panels, one per relationship.
function _RelationshipsDiagram() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
      {_CLUSTER_RELATIONSHIPS.map(r => {
        const a = _CLUSTER_ENTITY[r.from], b = _CLUSTER_ENTITY[r.to];
        return (
          <div key={r.id} style={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: "var(--r)", padding: 10 }}>
            <svg viewBox="0 0 160 80" style={{ width: "100%", height: 80 }}>
              <line x1="40" y1="40" x2="120" y2="40" stroke="var(--primary)" strokeWidth="1.4" strokeDasharray="3 3" />
              <_EntityNode color={a.color} letter={r.from} x={40}  y={40} size={28} />
              <_EntityNode color={b.color} letter={r.to}   x={120} y={40} size={28} />
              <text x="80" y="32" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="9" fill="var(--muted)">{r.label}</text>
            </svg>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", textTransform: "uppercase", letterSpacing: "0.06em", marginTop: 4 }}>{r.evidence}</div>
          </div>
        );
      })}
    </div>
  );
}

// STEP 2 diagram — the full adjacency graph (every active relationship overlaid).
function _AdjacencyDiagram() {
  return (
    <svg viewBox="0 0 360 300" style={{ width: "100%", height: 300, display: "block" }}>
      {_CLUSTER_RELATIONSHIPS.map(r => {
        const a = _CLUSTER_ENTITY[r.from], b = _CLUSTER_ENTITY[r.to];
        return (
          <g key={r.id}>
            <line x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke="var(--border-strong)" strokeWidth="1.4" strokeDasharray="3 3" />
            <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} textAnchor="middle"
              fontFamily="var(--font-mono)" fontSize="9" fill="var(--muted)" dominantBaseline="middle">
              {r.label}
            </text>
          </g>
        );
      })}
      {Object.entries(_CLUSTER_ENTITY).map(([k, e]) => (
        <_EntityNode key={k} color={e.color} letter={k} x={e.x} y={e.y} size={36} />
      ))}
    </svg>
  );
}

// STEP 3 diagram — one cluster-outcome panel per mode (small, fits in the card).
function _ClusterOutcomeDiagram({ mode }) {
  const layout = {
    A: { x: 60,  y: 28 }, B: { x: 150, y: 28 },
    C: { x: 30,  y: 70 }, D: { x: 180, y: 70 }, E: { x: 240, y: 50 },
  };
  const bubble = (m) => {
    if (m.id === "union")          return [{ cx: 130, cy: 50, rx: 130, ry: 36, color: "var(--error)" }];
    if (m.id === "per_subunit")    return [
      { cx: 50,  cy: 50, rx: 38, ry: 30, color: "var(--signal)" },
      { cx: 195, cy: 50, rx: 60, ry: 30, color: "var(--signal)" },
      { cx: 105, cy: 28, rx: 60, ry: 16, color: "var(--signal)" },
    ];
    if (m.id === "strict_pair")    return [{ cx: 105, cy: 28, rx: 60, ry: 18, color: "var(--warn)" }];
    if (m.id === "score_weighted") return [{ cx: 105, cy: 28, rx: 60, ry: 18, color: "var(--primary)" }];
    return [];
  };
  const bubbles = bubble(mode);
  return (
    <svg viewBox="0 0 280 90" style={{ width: "100%", height: 90, display: "block" }}>
      {bubbles.map((b, i) => (
        <ellipse key={i} cx={b.cx} cy={b.cy} rx={b.rx} ry={b.ry}
          fill={`color-mix(in oklab, ${b.color} 18%, transparent)`}
          stroke={b.color} strokeWidth="1" strokeDasharray="3 2" />
      ))}
      {Object.entries(layout).map(([k, p]) => {
        const dimmed = !mode.cluster.includes(k);
        return <_EntityNode key={k} color={_CLUSTER_ENTITY[k].color} letter={k} x={p.x} y={p.y} size={22} dimmed={dimmed} />;
      })}
    </svg>
  );
}

// STEP 4 diagram — train/val/test bins under the picked mode.
function _SplitOutcomeDiagram({ mode }) {
  const assignment = (() => {
    if (mode.id === "union")          return { train: ["A","B","C","D","E"], val: [],          test: []      };
    if (mode.id === "per_subunit")    return { train: ["A","C","D"],          val: ["B"],      test: ["E"]   };
    if (mode.id === "strict_pair")    return { train: ["A","B"],              val: ["C","E"],  test: ["D"]   };
    if (mode.id === "score_weighted") return { train: ["A","B"],              val: ["C","D"],  test: ["E"]   };
    return { train: [], val: [], test: [] };
  })();
  const Bucket = ({ name, items, tone, warning }) => (
    <div style={{
      padding: 12, borderRadius: "var(--r)",
      border: `1px solid var(--${tone})`,
      background: `color-mix(in oklab, var(--${tone}) 8%, transparent)`,
      minHeight: 110,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: `var(--${tone})`, letterSpacing: "0.08em", textTransform: "uppercase" }}>{name}</span>
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{items.length} entit{items.length === 1 ? "y" : "ies"}</span>
      </div>
      {items.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--dim)", fontStyle: "italic" }}>nothing — all the data merged into another split</div>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {items.map(k => <_EntityChip key={k} color={_CLUSTER_ENTITY[k].color} letter={k} size={28} />)}
        </div>
      )}
      {warning && <div style={{ marginTop: 8, fontSize: 11, color: `var(--${tone})`, lineHeight: 1.45 }}>{warning}</div>}
    </div>
  );
  return (
    <>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        <Bucket name="Train" items={assignment.train} tone="primary"
          warning={mode.id === "union" && "Everything ended up here. Test will be empty — this is the mega-cluster failure mode."} />
        <Bucket name="Val"   items={assignment.val}   tone="molecular" />
        <Bucket name="Test"  items={assignment.test}  tone="signal"
          warning={mode.id === "strict_pair" && "C and D contain proteins already in train. The held-out 'test' is leakier than the metrics will show."} />
      </div>
      {mode.id === "per_subunit" && (
        <div style={{ marginTop: 10, padding: 10, background: "var(--signal-soft)", borderRadius: "var(--r)", border: "1px solid var(--signal)", fontSize: 11, color: "var(--signal)" }}>
          ✓ Honest split. A's proteins are in train; B (different complex) lands in val, E (Pfam-related to A1) lands in test. Cold-target metrics are now trustworthy.
        </div>
      )}
    </>
  );
}

function RelationshipColumn({ title, items, relationships, toggleRelationship, toast }) {
  // Hide non-integrated ("planned") options behind a toggle so the
  // grid stops looking half-broken. Integrated items always show.
  const [showComingSoon, setShowComingSoon] = React.useState(false);
  const integratedItems  = items.filter(r => r.status === "integrated");
  const plannedItems     = items.filter(r => r.status !== "integrated");
  const visibleItems     = showComingSoon ? items : integratedItems;
  return (
    <div>
      <div className="label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span>{title}</span>
        {plannedItems.length > 0 && (
          <button type="button" className="btn sm ghost"
            style={{ padding: "1px 6px", fontSize: 10, marginLeft: "auto" }}
            onClick={() => setShowComingSoon(!showComingSoon)}>
            {showComingSoon
              ? `Hide ${plannedItems.length} coming-soon`
              : `Show ${plannedItems.length} coming-soon`}
          </button>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {visibleItems.map(r => {
          const on = relationships.has(r.id);
          const integrated = r.status === "integrated";
          const disabled = !integrated;
          return (
            <button type="button" key={r.id}
              aria-pressed={on}
              disabled={disabled}
              title={disabled
                ? `${r.label}: planned — signature ingestion not done yet. Visible so you can plan around it; not selectable.`
                : r.desc}
              onClick={() => {
                if (disabled) {
                  toast({ title: `${r.label} — planned`, body: r.desc + " Will become available once the signature index lands.", level: "info" });
                  return;
                }
                toggleRelationship(r.id);
              }}
              style={{
                padding: 8, textAlign: "left",
                cursor: disabled ? "not-allowed" : "pointer", font: "inherit",
                color: disabled ? "var(--dim)" : "var(--text)",
                opacity: disabled ? 0.55 : 1,
                border: `1px ${disabled ? "dashed" : "solid"} ${on ? "var(--primary)" : "var(--border)"}`,
                borderRadius: "var(--r)",
                background: on ? "var(--primary-soft)" : "var(--surface-2)",
                display: "flex", alignItems: "flex-start", gap: 8,
              }}>
              <div style={{
                width: 14, height: 14, borderRadius: 3, flexShrink: 0, marginTop: 2,
                border: `1.4px solid ${on ? "var(--primary)" : "var(--border-strong)"}`,
                background: on ? "var(--primary)" : "transparent",
                display: "grid", placeItems: "center", color: "#021624",
              }}>{on && <Ico name="check" size={10} />}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ fontSize: 12, fontWeight: 600, color: disabled ? "var(--dim)" : "var(--text-strong)" }}>{r.label}</span>
                  {!integrated && <Chip tone="warn">planned</Chip>}
                </div>
                <div style={{ fontSize: 10, color: "var(--dim)", lineHeight: 1.45, marginTop: 2 }}>{r.desc}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

// Convert a clientX/Y mouse position to SVG viewBox coordinates so drag
// math is independent of how the SVG is sized on the page.
function svgPoint(svg, clientX, clientY) {
  if (!svg) return { x: 0, y: 0 };
  const pt = svg.createSVGPoint();
  pt.x = clientX; pt.y = clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return { x: 0, y: 0 };
  const inv = ctm.inverse();
  return pt.matrixTransform(inv);
}

// Train / Val / Test ratio slider — draggable two-handle bar.
//
// Handle A sits at the train↔val boundary (x = train%).
// Handle B sits at the val↔test boundary (x = (train+val)%).
// The two handles' positions imply (train, val, test) where test is
// just 100 − (train+val) — we never let the user manipulate test
// directly because three values constrained to sum to 100 is the
// classic 2-dof slider design.
//
// Constraints (per pixel-move, enforced in onMouseMove):
//   train ∈ [40, 90]    — never starve train below 40%
//   val   ∈ [5,  30]    — val needs enough rows for a meaningful score
//   test  ∈ [5,  30]    — same, plus it's the headline metric
// These reproduce the bounds you'd see in the literature; over-aggressive
// hold-outs (test > 30%) produce noisy metrics; under-sized hold-outs
// (< 5%) make Pearson / RMSE statistically unstable.
function SplitRatioSlider({ train, val, test, onChange }) {
  const barRef = React.useRef(null);
  // Which handle is currently being dragged. "a" = train↔val, "b" = val↔test.
  const dragRef = React.useRef(null);

  const MIN = { train: 40, val: 5, test: 5 };
  const MAX = { train: 90, val: 30, test: 30 };

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  const pctFromClientX = React.useCallback((clientX) => {
    const el = barRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    if (r.width <= 0) return null;
    return Math.round(((clientX - r.left) / r.width) * 100);
  }, []);

  const applyDrag = React.useCallback((clientX) => {
    const which = dragRef.current;
    if (!which) return;
    const raw = pctFromClientX(clientX);
    if (raw == null) return;
    // Mental model:
    //   Handle A directly sets train%. The other handle holds test% fixed,
    //   so val absorbs the change. If val would leave its allowed band,
    //   we PUSH the other handle so the drag keeps feeling continuous.
    //   Handle B directly sets test% (positioned at 100−test). Same
    //   compensation rule, mirrored.
    if (which === "a") {
      let nextTrain = clamp(raw, MIN.train, MAX.train);
      let nextTest  = test;
      let nextVal   = 100 - nextTrain - nextTest;
      // val below MIN: train ate into it, give test less so val can grow back
      if (nextVal < MIN.val) {
        nextTest = Math.max(MIN.test, 100 - nextTrain - MIN.val);
        nextVal  = 100 - nextTrain - nextTest;
        // Still impossible? clamp train at the tightest valid extreme.
        if (nextVal < MIN.val) {
          nextTrain = 100 - MIN.val - MIN.test;
          nextVal   = MIN.val;
          nextTest  = MIN.test;
        }
      }
      // val above MAX: train shrank too much. Push test UP to soak the excess.
      if (nextVal > MAX.val) {
        nextTest = Math.min(MAX.test, 100 - nextTrain - MAX.val);
        nextVal  = 100 - nextTrain - nextTest;
        if (nextVal > MAX.val) {
          nextTrain = 100 - MAX.val - MAX.test;
          nextVal   = MAX.val;
          nextTest  = MAX.test;
        }
      }
      // final hard clamp on test (shouldn't fire if maths are right)
      if (nextTest < MIN.test || nextTest > MAX.test) return;
      onChange(nextTrain, nextVal);
    } else {
      // Handle B sits at x = train + val = 100 − test. Larger raw → smaller test.
      let nextTest  = clamp(100 - raw, MIN.test, MAX.test);
      let nextTrain = train;
      let nextVal   = 100 - nextTrain - nextTest;
      if (nextVal < MIN.val) {
        nextTrain = Math.max(MIN.train, 100 - MIN.val - nextTest);
        nextVal   = 100 - nextTrain - nextTest;
        if (nextVal < MIN.val) {
          nextTest  = 100 - MIN.train - MIN.val;
          nextTrain = MIN.train;
          nextVal   = MIN.val;
        }
      }
      if (nextVal > MAX.val) {
        nextTrain = Math.min(MAX.train, 100 - MAX.val - nextTest);
        nextVal   = 100 - nextTrain - nextTest;
        if (nextVal > MAX.val) {
          nextTest  = 100 - MAX.train - MAX.val;
          nextTrain = MAX.train;
          nextVal   = MAX.val;
        }
      }
      if (nextTrain < MIN.train || nextTrain > MAX.train) return;
      onChange(nextTrain, nextVal);
    }
  }, [train, val, test, onChange, pctFromClientX]);

  const onMouseMove = React.useCallback((e) => {
    applyDrag(e.clientX);
  }, [applyDrag]);

  const onMouseUp = React.useCallback(() => {
    dragRef.current = null;
    window.removeEventListener("mousemove", onMouseMove);
    window.removeEventListener("mouseup", onMouseUp);
    document.body.style.userSelect = "";
  }, [onMouseMove]);

  const beginDrag = (handle) => (e) => {
    e.preventDefault();
    dragRef.current = handle;
    document.body.style.userSelect = "none";    // prevent text selection while dragging
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    // apply the click location immediately so a tap on the bar jumps the handle
    applyDrag(e.clientX);
  };

  // Click on the bar (not a handle) → jump the nearest handle.
  const onTrackMouseDown = (e) => {
    if (e.target.dataset && e.target.dataset.handle) return;   // handled below
    const raw = pctFromClientX(e.clientX);
    if (raw == null) return;
    const dA = Math.abs(raw - train);
    const dB = Math.abs(raw - (train + val));
    beginDrag(dA <= dB ? "a" : "b")(e);
  };

  // Keyboard: ←/→ nudge the focused handle by 1, Shift+←/→ by 5.
  const onHandleKey = (handle) => (e) => {
    const step = e.shiftKey ? 5 : 1;
    let delta = 0;
    if (e.key === "ArrowLeft")  delta = -step;
    else if (e.key === "ArrowRight") delta = step;
    else return;
    e.preventDefault();
    const cur = (handle === "a") ? train : (train + val);
    // Re-use applyDrag's math via a synthetic clientX: convert pct → pixel.
    const el = barRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    dragRef.current = handle;
    applyDrag(r.left + r.width * (cur + delta) / 100);
    dragRef.current = null;
  };

  return (
    <div>
      <div ref={barRef}
           onMouseDown={onTrackMouseDown}
           style={{ position: "relative", height: 16, background: "var(--surface-3)",
                    borderRadius: 4, overflow: "visible", cursor: "ew-resize",
                    touchAction: "none" }}>
        <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: train + "%", background: "var(--primary)", borderRadius: "4px 0 0 4px" }} />
        <div style={{ position: "absolute", left: train + "%", top: 0, bottom: 0, width: val + "%", background: "var(--molecular)" }} />
        <div style={{ position: "absolute", left: (train + val) + "%", top: 0, right: 0, background: "var(--signal)", borderRadius: "0 4px 4px 0" }} />
        {/* Handle A — train↔val boundary */}
        <div data-handle="a"
             role="slider"
             tabIndex={0}
             aria-label="Train / val boundary"
             aria-valuemin={MIN.train} aria-valuemax={MAX.train} aria-valuenow={train}
             onMouseDown={beginDrag("a")}
             onKeyDown={onHandleKey("a")}
             title={`Drag to set train ratio (currently ${train}%)`}
             style={{ position: "absolute", left: `calc(${train}% - 5px)`,
                      top: -3, bottom: -3, width: 10, background: "var(--bg)",
                      border: "1.4px solid var(--border-strong)", borderRadius: 3,
                      cursor: "ew-resize", boxShadow: "0 1px 2px rgba(0,0,0,0.4)",
                      transition: dragRef.current ? "none" : "left 120ms ease-out" }} />
        {/* Handle B — val↔test boundary */}
        <div data-handle="b"
             role="slider"
             tabIndex={0}
             aria-label="Val / test boundary"
             aria-valuemin={train + MIN.val} aria-valuemax={train + MAX.val} aria-valuenow={train + val}
             onMouseDown={beginDrag("b")}
             onKeyDown={onHandleKey("b")}
             title={`Drag to set val/test split (currently val=${val}%, test=${test}%)`}
             style={{ position: "absolute", left: `calc(${train + val}% - 5px)`,
                      top: -3, bottom: -3, width: 10, background: "var(--bg)",
                      border: "1.4px solid var(--border-strong)", borderRadius: 3,
                      cursor: "ew-resize", boxShadow: "0 1px 2px rgba(0,0,0,0.4)",
                      transition: dragRef.current ? "none" : "left 120ms ease-out" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
        <span><span style={{ display: "inline-block", width: 6, height: 6, background: "var(--primary)", borderRadius: 1, marginRight: 4 }} />train <span style={{ color: "var(--text-strong)" }}>{train}%</span></span>
        <span><span style={{ display: "inline-block", width: 6, height: 6, background: "var(--molecular)", borderRadius: 1, marginRight: 4 }} />val <span style={{ color: "var(--text-strong)" }}>{val}%</span></span>
        <span><span style={{ display: "inline-block", width: 6, height: 6, background: "var(--signal)", borderRadius: 1, marginRight: 4 }} />test <span style={{ color: "var(--text-strong)" }}>{test}%</span></span>
      </div>
    </div>
  );
}

function SplitBar({ train, val, test }) {
  return (
    <div style={{ height: 24, display: "flex", borderRadius: 4, overflow: "hidden", background: "var(--surface-3)" }}>
      <div style={{ width: train + "%", background: "var(--primary)", display: "grid", placeItems: "center", color: "#021624", fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600 }}>{train}</div>
      <div style={{ width: val + "%", background: "var(--molecular)", display: "grid", placeItems: "center", color: "#160028", fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600 }}>{val}</div>
      <div style={{ width: test + "%", background: "var(--signal)", display: "grid", placeItems: "center", color: "#0b1a02", fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600 }}>{test}</div>
    </div>
  );
}

window.ScreenSplit = ScreenSplit;
