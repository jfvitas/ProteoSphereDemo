// ProteoSphere v2 — Reference library / warehouse browser
//
// The warehouse (a DuckDB catalog + partitioned parquet) is where 90% of
// "what's actually in my data?" questions land. Each tab is a paginated,
// filterable table. Per-row click → side drawer with provenance + lineage.
//
// Data path: each tab calls /api/v2/library/<family> with q/page/per_page/tier
// query params and renders the envelope's `rows`. When the v2 catalog has
// the family materialised the backend serves live rows; otherwise it
// returns bundled fixtures under the same shape (see
// api/model_studio/v2/library.py). The response's `live` flag drives the
// "live · vNNNN" badge above each tab body so the user always knows
// whether they're looking at warehouse data or the preview cohort.

// useLibraryFamily — shared fetch hook. Returns the full envelope and a
// refetch function. Memoises by family/q/page/perPage/tier so flipping
// between tabs doesn't re-hit the backend if nothing changed.
function useLibraryFamily(family, q, page, perPage, tier) {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(null);
  const refetch = React.useCallback(() => {
    if (!family) return;
    const params = new URLSearchParams({
      page: String(page || 1),
      per_page: String(perPage || 50),
    });
    if (q)    params.set("q", q);
    if (tier) params.set("tier", tier);
    setLoading(true);
    setError(null);
    fetch(`/api/v2/library/${family}?${params.toString()}`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(j => { setData(j); setLoading(false); })
      .catch(e => { setError(String(e.message || e)); setLoading(false); });
  }, [family, q, page, perPage, tier]);
  React.useEffect(() => { refetch(); }, [refetch]);
  return { data, loading, error, refetch };
}

function ScreenLibrary({ setCurrent, setLineageOpen, pushToast }) {
  const toast = pushToast || window.pushToast;
  const [perPage, setPerPage] = React.useState(50);
  const [tierFilter, setTierFilter] = React.useState("release"); // any | release
  const D = window.PS_DATA;
  const [tab, setTab] = React.useState("proteins");
  const [q, setQ] = React.useState("");
  const [selected, setSelected] = React.useState(null);
  // Per-tab page number, lifted from <Pager> so the fetch hook + the
  // pager UI stay in lockstep. Resets to 1 whenever the user changes
  // tab / search / per-page / tier — those changes invalidate the
  // current page index.
  const [page, setPage] = React.useState(1);
  React.useEffect(() => { setPage(1); }, [tab, q, perPage, tierFilter]);

  // Live featurizer catalog — pulled from /api/v2/featurizers.
  const [featurizers, setFeaturizers] = React.useState(
    () => (typeof window !== "undefined" && window.PS_LIVE_FEATURIZERS) || null
  );
  React.useEffect(() => {
    if (featurizers) return;
    fetch("/api/v2/featurizers")
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(j => { window.PS_LIVE_FEATURIZERS = j; setFeaturizers(j); })
      .catch(() => {});
  }, [featurizers]);

  const TABS = [
    { id: "proteins",      label: "Proteins",       count: D.warehouse.proteins, ico: "helix" },
    { id: "ligands",       label: "Ligands",        count: D.warehouse.ligands, ico: "molecule" },
    { id: "edges",         label: "Binding pairs",  count: D.warehouse.protein_ligand_edges, ico: "link" },
    { id: "structures",    label: "Structures",     count: D.warehouse.structures, ico: "layers" },
    { id: "motifs",        label: "Motifs",         count: D.warehouse.motif_site_annotations, ico: "target" },
    { id: "clans",         label: "Pfam clans",     count: 812, ico: "target" },
    { id: "pathways",      label: "Pathways",       count: 2730, ico: "link" },
    { id: "interactions",  label: "Interactions",   count: 1400000, ico: "link" },
    { id: "leakage",       label: "Leakage groups", count: D.warehouse.leakage_groups, ico: "split" },
    { id: "sources",       label: "Sources",        count: D.sources.length, ico: "dataset" },
    { id: "featurizers",   label: "Featurizers",    count: featurizers?.n_integrated || 0, ico: "sparkle" },
    { id: "releases",      label: "Releases",       count: D.releases.length, ico: "archive" },
  ];

  return (
    <div className="screen" data-screen-label="01 Library">
      <StaleBanner data={D.staleBanner} onPin={() => {}} onDismiss={() => {}} />

      <div style={{ display: "flex", alignItems: "flex-end", gap: 16, marginBottom: 18 }}>
        <div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", letterSpacing: "0.08em" }}>REFERENCE LIBRARY · WAREHOUSE v2026.04</div>
          <h2>What's actually in your data</h2>
          <p className="lead" style={{ marginTop: 4 }}>
            Every source ProteoSphere knows about, every protein and ligand we've curated, every release in the history.
            Browse before you build — saves arguments later.
          </p>
        </div>
        <div style={{ flex: 1 }} />
        {/* Schema export — real GET to /api/v2/library/_schema.sql,
            which the backend serves as a text/plain attachment so the
            browser triggers a download dialog. Anchor tag (not button)
            so middle-click + right-click "Save link as" work too. */}
        <a className="btn ghost" href="/api/v2/library/_schema.sql"
           download="warehouse_schema.sql"
           onClick={() => toast({
             title: "Downloading warehouse_schema.sql",
             body: "DuckDB CREATE statements for every table + source view.",
             level: "info", ttl_ms: 2400,
           })}>
          <Ico name="download" size={12} /> Export schema
        </a>
        <button className="btn" disabled
          title="SQL console will land once the read-only query endpoint ships. The Export schema button gives you the schema you'd query against in the meantime."
          style={{ opacity: 0.55, cursor: "not-allowed" }}>
          <Ico name="search" size={12} /> SQL console <span style={{ fontSize: 9, color: "var(--dim)", marginLeft: 6 }}>(planned)</span>
        </button>
      </div>

      {/* Top stat strip */}
      <div className="card" style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", padding: 0, marginBottom: 16 }}>
        {[
          { k: "Proteins",       v: fmt.short(D.warehouse.proteins),     sub: "across 262M UniProt entries" },
          { k: "PDB entries",    v: fmt.short(D.warehouse.pdb_entries),  sub: "experimental + AF predicted" },
          { k: "Binding pairs",  v: fmt.short(D.warehouse.protein_ligand_edges), sub: "from 12 authoritative sources" },
          { k: "Leakage groups", v: D.warehouse.leakage_groups,           sub: "pre-clustered for safe splits" },
          { k: "Last refresh",   v: "2 days ago",                         sub: "v2026.04 · " + D.warehouse.last_consolidation },
        ].map((s, i) => (
          <div key={i} style={{ padding: 16, borderRight: i < 4 ? "1px solid var(--border)" : "none" }}>
            <Stat k={s.k} v={s.v} mono />
            <div style={{ fontSize: 11, color: "var(--dim)", marginTop: 4, fontFamily: "var(--font-mono)" }}>{s.sub}</div>
          </div>
        ))}
      </div>

      {/* Tab strip */}
      <div className="tabs" style={{ marginBottom: 14 }}>
        {TABS.map(t => (
          <div key={t.id} className="tab" aria-current={tab === t.id ? "true" : "false"} onClick={() => setTab(t.id)} tabIndex={0}>
            <Ico name={t.ico} size={12} />
            <span>{t.label}</span>
            <span className="count">{fmt.short(t.count)}</span>
          </div>
        ))}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: selected ? "1fr 360px" : "1fr", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* Search / filter bar */}
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ position: "relative", flex: 1 }}>
              <Ico name="search" size={12} style={{ position: "absolute", left: 10, top: 9, color: "var(--dim)" }} />
              <input className="input" placeholder={`Search ${TABS.find(t => t.id === tab)?.label.toLowerCase()}…`} style={{ paddingLeft: 30 }} value={q} onChange={e => setQ(e.target.value)} />
            </div>
            <label htmlFor="lib-perpage" className="visually-hidden">Rows per page</label>
            <select id="lib-perpage" className="select" style={{ width: 140 }} value={perPage} onChange={e => setPerPage(parseInt(e.target.value, 10))}>
              <option value={50}>50 per page</option>
              <option value={100}>100 per page</option>
              <option value={500}>500 per page</option>
            </select>
            <div className="toggle" role="group" aria-label="Tier filter">
              <button type="button" aria-pressed={tierFilter === "any"}     onClick={() => setTierFilter("any")}>Any tier</button>
              <button type="button" aria-pressed={tierFilter === "release"} onClick={() => setTierFilter("release")}>Production only</button>
            </div>
          </div>

          {/* Tab body — every backend-driven tab now receives the
              same (q, page, perPage, tier, onPageChange) bundle so the
              parent's <Pager> controls AND the search box drive the
              fetch hook. Leakage + Featurizers keep their own data
              paths (one's already live via /splits/leakage_report and
              the other via /api/v2/featurizers). */}
          {tab === "proteins"   && <ProteinsTab   q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} onPick={setSelected} />}
          {tab === "ligands"    && <LigandsTab    q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} onPick={setSelected} />}
          {tab === "edges"      && <EdgesTab      q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} onPick={setSelected} />}
          {tab === "structures" && <StructuresTab q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} onPick={setSelected} />}
          {tab === "motifs"     && <MotifsTab     q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} />}
          {tab === "clans"      && <ClansTab      q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} />}
          {tab === "pathways"   && <PathwaysTab   q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} />}
          {tab === "interactions" && <InteractionsTab q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} />}
          {tab === "leakage"    && <LeakageTab />}
          {tab === "sources"    && <SourcesTab    q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} onPick={setSelected} />}
          {tab === "featurizers" && <FeaturizersTab data={featurizers} />}
          {tab === "releases"   && <ReleasesTab   q={q} page={page} perPage={perPage} tier={tierFilter} onPageChange={setPage} />}
        </div>

        {selected && (
          <DetailDrawer item={selected} kind={tab} onClose={() => setSelected(null)} onOpenLineage={() => setLineageOpen(true)} />
        )}
      </div>
    </div>
  );
}

// ───── Tabs ─────

// ─── Shared empty-state helpers ───────────────────────────────────
// All live tabs use the same loading / empty / error skeletons so the
// user gets consistent feedback while the backend works.
function _TabStatus({ loading, error, empty, retry }) {
  if (loading) return (
    <div style={{ padding: 18, color: "var(--muted)", fontSize: 12 }}>
      Loading from the warehouse…
    </div>
  );
  if (error) return (
    <div style={{ padding: 18, color: "var(--error)", fontSize: 12 }}>
      Backend error: {error}
      {retry && <button type="button" className="btn sm" style={{ marginLeft: 8 }} onClick={retry}>Retry</button>}
    </div>
  );
  if (empty) return (
    <div style={{ padding: 18, color: "var(--muted)", fontSize: 12 }}>
      No rows match the current filter. Clear the search box or pick "Any tier" to widen the scope.
    </div>
  );
  return null;
}

function _LiveBadge({ live }) {
  return (
    <Chip tone={live ? "ok" : "info"} dot>
      {live ? "live · warehouse" : "preview · fixtures"}
    </Chip>
  );
}

function ProteinsTab({ onPick, q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("proteins", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Proteins</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead>
            <tr>
              <th>UniProt</th><th>Name</th><th>Organism</th><th>Length</th><th>PDB</th><th>Family</th><th>Tier</th><th></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(p => (
              <tr key={p.uniprot} onClick={() => onPick({ ...p, _kind: "protein" })} style={{ cursor: "pointer" }}>
                <td className="mono"><span style={{ color: "var(--primary)" }}>{p.uniprot}</span></td>
                <td><span style={{ fontWeight: 500 }}>{p.name}</span></td>
                <td><span style={{ color: "var(--muted)", fontStyle: "italic", fontSize: 12 }}>{p.organism}</span></td>
                <td className="mono">{p.len}</td>
                <td className="mono">{p.pdbs}</td>
                <td><span style={{ fontSize: 12 }}>{p.family}</span></td>
                <td><TierPill tier={p.tier} /></td>
                <td><Ico name="chevR" style={{ color: "var(--dim)" }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function LigandsTab({ onPick, q, page, perPage, tier, onPageChange }) {
  // `id` is the internal ProteoSphere ligand ID, not the PDB chemical-component
  // het code. Real het codes are stored separately and surfaced via the
  // structure-row detail drawer (e.g. PDB 4ZLZ → ibrutinib het 1E8).
  const { data, loading, error, refetch } = useLibraryFamily("ligands", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Ligands</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Ligand ID</th><th>Name</th><th>MW</th><th>QED</th><th>Binding pairs</th><th>Source</th><th>Tier</th><th></th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.id} onClick={() => onPick({ ...r, _kind: "ligand" })} style={{ cursor: "pointer" }}>
                <td className="mono" style={{ color: "var(--signal)" }}>{r.id}</td>
                <td style={{ fontWeight: 500 }}>{r.name}</td>
                <td className="mono">{r.mw}</td>
                <td className="mono">{(r.qed ?? 0).toFixed(2)}</td>
                <td className="mono">{r.n_pairs}</td>
                <td><span style={{ fontSize: 12, color: "var(--muted)" }}>{r.source}</span></td>
                <td><TierPill tier={r.tier} /></td>
                <td><Ico name="chevR" style={{ color: "var(--dim)" }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function EdgesTab({ onPick, q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("edges", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Binding pairs</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Protein</th><th>Ligand</th><th>Activity</th><th>Value</th><th>Source</th><th>Year</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} onClick={() => onPick({ ...r, _kind: "edge" })} style={{ cursor: "pointer" }}>
                <td>{r.protein}</td>
                <td><span style={{ color: "var(--signal)" }}>{r.ligand}</span></td>
                <td><Chip>{r.act}</Chip></td>
                <td className="mono">{r.value}</td>
                <td><span style={{ color: "var(--muted)", fontSize: 12 }}>{r.src}</span></td>
                <td className="mono">{r.year}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function StructuresTab({ onPick, q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("structures", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Structures</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>PDB id</th><th>Title</th><th>Resolution</th><th>Method</th><th>Ligand</th><th>Year</th></tr></thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.pdb} onClick={() => onPick && onPick({ ...r, _kind: "structure" })}
                  style={{ cursor: onPick ? "pointer" : "default" }}>
                <td className="mono" style={{ color: "var(--primary)" }}>{r.pdb}</td>
                <td>{r.title}</td>
                <td className="mono">{r.resolution}</td>
                <td><Chip tone={r.method === "Predicted" ? "molecular" : ""}>{r.method}</Chip></td>
                <td className="mono">{r.ligand}</td>
                <td className="mono">{r.year}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function MotifsTab({ q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("motifs", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Motif &amp; domain annotations</span>
        <span className="sub">5.7M annotations</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Motif</th><th>Source</th><th>Annotations</th><th>Example protein</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><Term word={String(r.name || "").includes("Pkinase") ? "ATP pocket" : r.name}>{r.name}</Term></td>
                <td className="mono"><span style={{ color: "var(--muted)" }}>{r.src}</span></td>
                <td className="mono">{typeof r.n === "number" ? r.n.toLocaleString() : r.n}</td>
                <td className="mono">{r.ex}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function ClansTab({ q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("clans", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Pfam clans &mdash; fold-level superfamily groupings</span>
        <span className="sub">812 clans &middot; 12,486 Pfam families with clan assignment</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Clan ID</th><th>Name</th><th>Pfam families</th><th>Example members</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="mono">{r.clan_id}</td>
                <td>{r.name}</td>
                <td className="mono">{r.n_pfam}</td>
                <td className="mono" style={{ fontSize: 11 }}>{r.ex}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function PathwaysTab({ q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("pathways", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Reactome pathway membership</span>
        <span className="sub">2,730 pathways across model organisms &middot; from UniProt2Reactome</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Pathway ID</th><th>Name</th><th>UniProts</th><th>Organism</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="mono">{r.id}</td>
                <td>{r.name}</td>
                <td className="mono">{r.n_uniprots}</td>
                <td className="mono" style={{ color: "var(--muted)" }}>{r.organism}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function InteractionsTab({ q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("interactions", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Protein-protein interactions</span>
        <span className="sub">IntAct (670k) &middot; BioGRID &middot; STRING (combined&ge;700) &middot; Reactome co-membership</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Source</th><th>A</th><th>B</th><th>Type</th><th>Detection</th><th>Score</th><th>PMID</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td className="mono">{r.source}</td>
                <td className="mono">{r.uniprot_a}</td>
                <td className="mono">{r.uniprot_b}</td>
                <td style={{ fontSize: 11 }}>{r.type}</td>
                <td style={{ fontSize: 11, color: "var(--muted)" }}>{r.detection}</td>
                <td className="mono">{r.score !== null && r.score !== undefined ? r.score : "&mdash;"}</td>
                <td className="mono" style={{ fontSize: 11 }}>{r.pmid || "&mdash;"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function LeakageTab() {
  const D = window.PS_DATA;
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Pre-clustered leakage groups</span>
        <span className="sub">computed by <Term word="MMseqs2">MMseqs2</Term> + <Term word="ECFP">ECFP-Tanimoto</Term> · use these on the Splits screen</span>
      </div>
      <table className="tbl">
        <thead><tr><th>Group</th><th>N pairs</th><th>Description</th><th>Residues</th><th>Internal similarity</th><th>Risk</th></tr></thead>
        <tbody>
          {D.leakage_groups.map(g => (
            <tr key={g.id}>
              <td className="mono">{g.id}</td>
              <td className="mono">{fmt.n(g.n)}</td>
              <td>{g.kind}</td>
              <td className="mono">{g.residues}</td>
              <td>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <div style={{ width: 60, height: 4, background: "var(--surface-3)", borderRadius: 2 }}>
                    <div style={{ width: `${g.similarity * 100}%`, height: "100%", background: g.similarity > 0.8 ? "var(--error)" : g.similarity > 0.6 ? "var(--warn)" : "var(--signal)", borderRadius: 2 }} />
                  </div>
                  <span className="mono">{g.similarity.toFixed(2)}</span>
                </div>
              </td>
              <td>
                {g.risk === "high" && <Chip tone="error" dot>high</Chip>}
                {g.risk === "med"  && <Chip tone="warn" dot>medium</Chip>}
                {g.risk === "low"  && <Chip tone="signal">low</Chip>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SourcesTab({ onPick, q, page, perPage, tier, onPageChange }) {
  const { data, loading, error, refetch } = useLibraryFamily("sources", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h" style={{ borderBottom: "1px solid var(--border-soft)" }}>
        <span className="t" style={{ fontSize: 12 }}>Sources</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <table className="tbl">
          <thead><tr><th>Source</th><th>Kind</th><th>Rows</th><th>Scope</th><th>Updated</th><th></th></tr></thead>
          <tbody>
            {rows.map(s => (
              <tr key={s.id} onClick={() => onPick({ ...s, _kind: "source" })} style={{ cursor: "pointer" }}>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: "var(--signal)" }} />
                    <span style={{ fontWeight: 500 }}>{s.name}</span>
                  </div>
                </td>
                <td><Chip>{s.kind}</Chip></td>
                <td className="mono">{typeof s.rows === "number" ? fmt.short(s.rows) : s.rows}</td>
                <td><span style={{ fontSize: 11, color: "var(--muted)" }}>{s.scope || "—"}</span></td>
                <td className="mono">{s.updated}</td>
                <td><Ico name="chevR" style={{ color: "var(--dim)" }} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <Pager total={data?.total || 0} page={page} perPage={perPage} onPageChange={onPageChange} />
    </div>
  );
}

function FeaturizersTab({ data }) {
  if (!data) {
    return (
      <div className="card">
        <div className="card-h"><span className="t">Featurizer catalog</span></div>
        <div style={{ padding: 18, color: "var(--muted)" }}>Loading featurizer catalog from /api/v2/featurizers…</div>
      </div>
    );
  }
  const [axisFilter, setAxisFilter] = React.useState("all");
  const [costFilter, setCostFilter] = React.useState("all");
  const all = data.items || [];
  const filtered = all.filter(it =>
    (axisFilter === "all" || it.axis === axisFilter) &&
    (costFilter === "all" || it.cost === costFilter)
  );
  const costTone = (c) => c === "trivial" ? "ok" : c === "fast" ? "signal" : c === "moderate" ? "info" : "warn";
  return (
    <>
      <div className="card">
        <div className="card-h">
          <span className="t">Featurizer catalog</span>
          <span className="sub">{data.n_integrated} integrated · {data.n_featurizers} total</span>
          <div style={{ flex: 1 }} />
          <div style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 11 }}>
            <span style={{ color: "var(--dim)" }}>axis</span>
            {["all", "ligand", "protein", "interaction"].map(a => (
              <button key={a} type="button" className={`btn sm ${axisFilter === a ? "primary" : "ghost"}`}
                style={{ padding: "2px 8px", fontFamily: "var(--font-mono)" }}
                onClick={() => setAxisFilter(a)}>{a}</button>
            ))}
            <span style={{ color: "var(--dim)", marginLeft: 8 }}>cost</span>
            {["all", "trivial", "fast", "moderate", "heavy"].map(c => (
              <button key={c} type="button" className={`btn sm ${costFilter === c ? "primary" : "ghost"}`}
                style={{ padding: "2px 8px", fontFamily: "var(--font-mono)" }}
                onClick={() => setCostFilter(c)}>{c}</button>
            ))}
          </div>
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th>Featurizer</th><th>Axis</th><th style={{ textAlign: "right" }}>Dim</th><th>Cost</th><th>Requires</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(it => (
              <tr key={it.id} title={it.long_desc}>
                <td style={{ maxWidth: 360 }}>
                  <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--text-strong)" }}>{it.id}</div>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{it.short_desc}</div>
                </td>
                <td>{it.axis}</td>
                <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12 }}>{it.dim.toLocaleString()}</td>
                <td><Chip tone={costTone(it.cost)}>{it.cost}</Chip></td>
                <td style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--muted)" }}>
                  {(it.requires || []).join(", ") || "—"}
                </td>
                <td>
                  {it.integrated
                    ? <Chip tone="ok">integrated</Chip>
                    : <Chip tone="warn">missing deps</Chip>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}


function ReleasesTab({ q, page, perPage, tier, onPageChange }) {
  const toast = window.pushToast || (() => {});
  const { data, loading, error, refetch } = useLibraryFamily("releases", q, page, perPage, tier);
  const rows = data?.rows || [];
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">Warehouse releases</span>
        <span className="sub">all consolidations · pick to compare</span>
        <div style={{ flex: 1 }} />
        <_LiveBadge live={!!data?.live} />
      </div>
      <_TabStatus loading={loading} error={error}
                  empty={!loading && !error && rows.length === 0}
                  retry={refetch} />
      {rows.length > 0 && (
        <div style={{ padding: 14 }}>
          {rows.map((r, i) => (
            <div key={r.id} style={{ display: "grid", gridTemplateColumns: "100px 140px 1fr auto", gap: 14, padding: "12px 4px", borderBottom: i < rows.length - 1 ? "1px solid var(--border-soft)" : "none", alignItems: "center" }}>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span className="mono" style={{ fontSize: 13, color: "var(--text-strong)" }}>{r.version || r.id}</span>
                  {r.current && <Chip tone="signal" dot>current</Chip>}
                </div>
                <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{r.status}</div>
              </div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>{r.published}</div>
              <div style={{ display: "flex", gap: 16, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
                <span>{r.n_sources} sources</span>
                <span>{fmt.short(r.n_rows || 0)} rows</span>
                <span>{r.n_leakage_groups} leakage groups</span>
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <button type="button" className="btn sm ghost"
                  onClick={() => toast({
                    title: `Diff ${r.version} vs your pin`,
                    body: `Row-level delta between warehouse ${r.version} and your current pin. (Diff backend ships next — for now the release manifest is mirrored at artifacts/bundles/preview/proteosphere-lite.release_manifest.json.)`,
                    level: "info", ttl_ms: 4000,
                  })}>Diff</button>
                {!r.current && (
                  <button type="button" className="btn sm"
                    onClick={() => toast({
                      title: `Pinned to ${r.version}`,
                      body: `Dataset will resolve against ${r.version} on next training run. Embeddings warm on first use; cached results stay valid until invalidated by a schema change.`,
                      level: "ok", ttl_ms: 4000,
                    })}>Pin to this</button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DetailDrawer({ item, kind, onClose, onOpenLineage }) {
  const title = item.name || item.id || item.uniprot;
  const toast = window.pushToast || (() => {});
  return (
    <div className="card" style={{ position: "sticky", top: 16, alignSelf: "flex-start" }}>
      <div className="card-h">
        <span className="t">{title}</span>
        <Chip>{item._kind || kind}</Chip>
        <div style={{ flex: 1 }} />
        <button type="button" className="btn sm ghost" onClick={onClose} aria-label="Close detail panel">×</button>
      </div>
      <div className="card-b" style={{ fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.7 }}>
        {item.uniprot && <Row k="UniProt" v={item.uniprot} />}
        {item.organism && <Row k="Organism" v={item.organism} />}
        {item.len && <Row k="Length" v={`${item.len} aa`} />}
        {item.pdbs != null && <Row k="PDB entries" v={item.pdbs} />}
        {item.family && <Row k="Family" v={item.family} />}
        {item.mw && <Row k="MW" v={item.mw} />}
        {item.qed != null && <Row k="QED" v={item.qed.toFixed(2)} />}
        {item.n_pairs != null && <Row k="Binding pairs" v={item.n_pairs} />}
        {item.tier && <Row k="Tier" v={<TierPill tier={item.tier} />} />}
        <hr className="hr" />
        <Row k="Content hash" v="sha256:9f3a4e2…" />
        <Row k="Last seen"    v={item.updated || "2026-04-12"} />
        <Row k="Pinned to"    v="v2026.04" />
        <hr className="hr" />
        <div style={{ display: "flex", gap: 6 }}>
          <button type="button" className="btn sm" onClick={onOpenLineage}>Open lineage</button>
          {/* "View source" — resolve to a real upstream URL via the
              /api/v2/library/_source_url endpoint, then open it in a
              new tab. Falls back to a toast when no public anchor is
              known (e.g. internal-only sources like STRING or PDBbind
              under the access-controlled tier). */}
          <button type="button" className="btn sm ghost"
            onClick={async () => {
              try {
                const family = (item._kind || kind || "").toLowerCase() + "s";
                const params = new URLSearchParams({
                  family: family.replace(/s$/, "s"),  // normalise
                  payload: JSON.stringify(item),
                });
                const r = await fetch(`/api/v2/library/_source_url?${params.toString()}`);
                const j = r.ok ? await r.json() : { url: null };
                if (j.url) {
                  window.open(j.url, "_blank", "noopener,noreferrer");
                  toast({
                    title: `Opened upstream record`,
                    body: `Source for ${title} → ${j.url}`,
                    level: "ok", ttl_ms: 2400,
                  });
                } else {
                  toast({
                    title: `No public source for ${title}`,
                    body: `This record family doesn't have a canonical public anchor yet (e.g. internal-only or access-controlled tier). The record lives in the warehouse only.`,
                    level: "info", ttl_ms: 4000,
                  });
                }
              } catch (e) {
                toast({
                  title: "Source lookup failed",
                  body: String(e.message || e),
                  level: "error", ttl_ms: 4000,
                });
              }
            }}>
            View source
          </button>
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
      <span style={{ color: "var(--dim)" }}>{k}</span>
      <span style={{ color: "var(--text)" }}>{v}</span>
    </div>
  );
}

// Pager — caller-controlled. The Library screen lifts `page` to its
// own state so the value drives the fetch hook AND the buttons stay
// in sync with what's actually rendered. When `onPageChange` is
// omitted (legacy callers) the pager falls back to local state so the
// component still works as a self-contained widget.
function Pager({ total, page, perPage, onPageChange }) {
  const isControlled = typeof onPageChange === "function";
  const [internalP, setInternalP] = React.useState(page || 1);
  const p = isControlled ? (page || 1) : internalP;
  const pages = Math.max(1, Math.ceil((total || 0) / (perPage || 50)));
  const setBounded = (next) => {
    const clamped = Math.min(pages, Math.max(1, next));
    if (isControlled) onPageChange(clamped);
    else              setInternalP(clamped);
  };
  // Guard against total=0 so the "1–0 of 0" footer doesn't read weird.
  const lo = total === 0 ? 0 : (p - 1) * perPage + 1;
  const hi = total === 0 ? 0 : Math.min(p * perPage, total);
  return (
    <div style={{ padding: "10px 14px", borderTop: "1px solid var(--border)", display: "flex", alignItems: "center", gap: 10, fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)" }}>
      <span>{lo}–{hi} of {fmt.n(total || 0)}</span>
      <div style={{ flex: 1 }} />
      <button type="button" className="btn sm ghost"
        disabled={p === 1 || total === 0} aria-disabled={p === 1 || total === 0}
        onClick={() => setBounded(p - 1)} aria-label="Previous page">
        <Ico name="arrowL" size={10} /> Prev
      </button>
      <span>Page {p} of {pages.toLocaleString()}</span>
      <button type="button" className="btn sm ghost"
        disabled={p >= pages || total === 0} aria-disabled={p >= pages || total === 0}
        onClick={() => setBounded(p + 1)} aria-label="Next page">
        Next <Ico name="arrowR" size={10} />
      </button>
    </div>
  );
}

window.ScreenLibrary = ScreenLibrary;
