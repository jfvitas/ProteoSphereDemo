// ProteoSphere v2 — shared components (tier pills, term tooltips, banners, modals, states, blockers, drawers)

// ────────────────────────────────────────────────────────────────────
// TierPill — applies to every option, chip, picker, model-family card.
// Plain-language labels (Production / Beta / Coming soon / Lab / Blocked)
// because half the team won't recognise the internal lane names.
// ────────────────────────────────────────────────────────────────────
function TierPill({ tier, full, dot = true, title }) {
  const t = window.PS_TIERS?.[tier] || { label: tier, tone: "dim", desc: "" };
  const label = full ? t.label : (t.short || t.label);
  return (
    <span className={`tier ${tier}`} title={title || t.desc}>
      {dot && <span className="dot" />}
      {label}
    </span>
  );
}

// ────────────────────────────────────────────────────────────────────
// Term — inline jargon explainer. Wrap any technical word.
// <Term word="MMseqs2" /> renders "MMseqs2" with a dotted underline and
// a hover/focus popover with the plain-language definition.
//
// Accessibility: the trigger is a real <button>, the popover is
// <span role="tooltip"> referenced via aria-describedby, Esc closes,
// click-toggle works on touch devices, and the trigger is reachable
// via Tab. Keyboard `Esc` closes if the trigger is focused.
// ────────────────────────────────────────────────────────────────────
let __termIdCounter = 0;
function Term({ word, children, define }) {
  const [open, setOpen] = React.useState(false);
  const idRef = React.useRef(null);
  if (!idRef.current) idRef.current = `tt_${++__termIdCounter}`;
  const explain = define || window.PS_GLOSSARY?.[word] || "";
  if (!explain) return <span>{children || word}</span>;
  return (
    <button
      type="button"
      className="term"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onClick={(e) => { e.preventDefault(); setOpen(o => !o); }}
      onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
      aria-describedby={open ? idRef.current : undefined}
    >
      {children || word}
      {open && (
        <span id={idRef.current} className="term-pop" role="tooltip">
          <span className="k">{word}</span>
          {explain}
        </span>
      )}
    </button>
  );
}

// ────────────────────────────────────────────────────────────────────
// InfoTip — a standalone (i) icon next to anything jargon-y. Click or
// hover surfaces a popover with the explanation. Unlike Term, InfoTip
// does not wrap content — it sits beside the term so the layout doesn't
// gain an underline / dotted indicator on every word.
//
// Source of truth (in priority order):
//   1. the `text` prop (one-shot inline definition)
//   2. PS_GLOSSARY[word] (the central glossary)
//   3. nothing → renders nothing (so callers can pass an unknown word
//      without breaking the layout)
// ────────────────────────────────────────────────────────────────────
let __infoTipIdCounter = 0;
function InfoTip({ word, text, size = 12, color = "var(--muted)" }) {
  const [open, setOpen] = React.useState(false);
  const idRef = React.useRef(null);
  if (!idRef.current) idRef.current = `it_${++__infoTipIdCounter}`;
  const body = text || (word && window.PS_GLOSSARY?.[word]) || "";
  if (!body) return null;
  return (
    <button
      type="button"
      className="info-tip"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); setOpen(o => !o); }}
      onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
      aria-label={word ? `What is ${word}?` : "More info"}
      aria-describedby={open ? idRef.current : undefined}
      style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        width: size + 4, height: size + 4,
        borderRadius: "50%", border: "1px solid var(--border)",
        background: "transparent", color, cursor: "help",
        fontFamily: "var(--font-mono)", fontSize: size - 2, lineHeight: 1,
        padding: 0, verticalAlign: "middle", marginLeft: 4,
        position: "relative", flexShrink: 0,
      }}
    >
      i
      {open && (
        <span
          id={idRef.current}
          role="tooltip"
          className="term-pop"
          style={{
            position: "absolute", top: "calc(100% + 6px)", left: "50%", transform: "translateX(-50%)",
            zIndex: 50, width: "max-content", maxWidth: 320,
            background: "var(--surface)", border: "1px solid var(--border)",
            borderRadius: "var(--r)", padding: "8px 10px",
            fontSize: 11, color: "var(--text)", lineHeight: 1.45,
            fontFamily: "var(--font-sans)", textAlign: "left", whiteSpace: "normal",
            boxShadow: "var(--shadow-md, 0 4px 16px rgba(0,0,0,0.18))",
            fontWeight: 400,
          }}
        >
          {word && <span style={{ display: "block", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", marginBottom: 4, letterSpacing: "0.05em" }}>{word}</span>}
          {body}
        </span>
      )}
    </button>
  );
}
window.InfoTip = InfoTip;

// ────────────────────────────────────────────────────────────────────
// CatBadge — 12×12 SVG glyph in a token-colored rounded rectangle.
// One of the five identity primitives for the pipeline-v3 redesign
// (Decision F). Used on:
//   1. canvas nodes (upper-left badge inside the body)
//   2. template-header icon block
//   3. node-inspector popover header
//   4. (chunk 4) the node palette group header
// Pulls glyph + token from PS_PIPELINE_CATEGORIES; renders nothing if
// the category is unknown.
// ────────────────────────────────────────────────────────────────────
function CatBadge({ category, size = 22 }) {
  const CAT = window.PS_PIPELINE_CATEGORIES?.[category];
  if (!CAT) return null;
  const radius = size >= 20 ? 4 : 3;
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" style={{ display: "block", flexShrink: 0 }}>
      <rect
        className="cat-badge"
        data-cat={category}
        x="0" y="0" width="12" height="12" rx={radius}
        strokeWidth="1"
      />
      <g transform="translate(2, 2)">
        <path
          className="cat-icon-glyph"
          data-cat={category}
          d={CAT.glyph}
          fill="none" strokeWidth="1.4"
          strokeLinecap="round" strokeLinejoin="round"
        />
      </g>
    </svg>
  );
}
window.CatBadge = CatBadge;

// CostDot — single source of truth for the cost semaphore used on
// template cards, the template header, and the node body cost line.
// Color is set by `.cost-dot[data-c]` rules in styles.css.
function CostDot({ cost, title }) {
  return (
    <span
      className="cost-dot"
      data-c={cost}
      title={title || ("Compute cost: " + cost)}
      role="img"
      aria-label={"cost " + cost}
      style={{ width: 8, height: 8, borderRadius: 4, display: "inline-block" }}
    />
  );
}
window.CostDot = CostDot;

// ────────────────────────────────────────────────────────────────────
// Active-lane indicator strip — sits above every Build-run screen.
// ────────────────────────────────────────────────────────────────────
// BindingBanner — read-only banner used at the top of downstream
// screens (Dataset, Splits, Pipeline, Features) to surface the
// binding type the user picked on the Goal screen. Clicking "Change"
// jumps back to Goal.
// ────────────────────────────────────────────────────────────────────
function BindingBanner({ bindingId, setCurrent, onChange }) {
  const D = (typeof window !== "undefined" && window.PS_DATA) || {};
  const id = bindingId || D.binding_type;
  const bt = (window.PS_BINDING_TYPES || []).find(b => b.id === id);
  if (!bt) return null;
  const handleChange = () => {
    if (onChange) return onChange();
    if (setCurrent) return setCurrent("goal");
  };
  return (
    <div className="binding-banner">
      <div className="badge"><Ico name={bt.icon || "molecule"} size={20} /></div>
      <div className="meta">
        <div className="title">Binding type · {bt.label}</div>
        <div className="sub">{bt.what}{bt.use_case ? ` · use case: ${bt.use_case}` : ""}</div>
      </div>
      <div className="stats">
        {bt.items != null && <Stat k="Bound items" v={fmt.short(bt.items)} mono />}
        {bt.unique?.proteins != null && <Stat k="Proteins" v={fmt.short(bt.unique.proteins)} mono />}
        {bt.unique?.ligands != null && <Stat k="Ligands" v={fmt.short(bt.unique.ligands)} mono />}
        {bt.unique?.complexes != null && <Stat k="Complexes" v={fmt.short(bt.unique.complexes)} mono />}
      </div>
      <button className="btn sm" onClick={handleChange} title="Pick a different binding type on the Goal screen">
        Change <Ico name="chevR" size={11} />
      </button>
    </div>
  );
}
window.BindingBanner = BindingBanner;


// Tells the user "your current selections are Production / Beta / Lab"
// and offers a one-click filter to clamp every picker to one tier.
// ────────────────────────────────────────────────────────────────────
function LaneBar({ lane = "release", onChange, hint }) {
  // Compact inline badge — previously this was a full-width toolbar
  // at the top of every step page, dominating the visual hierarchy.
  // Users complained they didn't know what "lane" meant and the bar
  // out-competed the screen's actual heading for attention.
  const tiers = ["release", "beta", "lab"];
  const [open, setOpen] = React.useState(false);
  const tierLabel = window.PS_TIERS?.[lane]?.label || lane;
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 6, marginBottom: 8, fontSize: 11 }}>
      <span style={{ color: "var(--dim)", fontFamily: "var(--font-mono)" }}>lane:</span>
      <button type="button" className="btn sm ghost"
        onClick={() => setOpen(!open)}
        style={{ padding: "2px 8px", fontFamily: "var(--font-mono)" }}
        title="Filters the option lists on each step to a maturity tier. Changing the lane does not delete work; it just narrows what's shown.">
        {tierLabel} <Ico name={open ? "chev" : "chevR"} size={9} />
      </button>
      {open && (
        <div style={{ display: "inline-flex", gap: 4 }}>
          {tiers.map(t => (
            <button key={t} type="button"
              className={`btn sm ${lane === t ? "primary" : "ghost"}`}
              style={{ padding: "2px 8px", fontFamily: "var(--font-mono)" }}
              onClick={() => { onChange && onChange(t); setOpen(false); }}>
              {window.PS_TIERS[t].label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Stale-artifact banner — surfaces a newer warehouse version.
// Non-modal, dismissible. Lives on Home, Dataset, Results.
// ────────────────────────────────────────────────────────────────────
function StaleBanner({ data, onPin, onDismiss }) {
  // Persist the dismissal across navigations + refreshes. Previously
  // the banner re-rendered on every screen change because each screen
  // kept its own dismissed state, which the UX agent flagged as nag-y.
  // Key by data.available so a NEW release reshows it.
  const storageKey = data ? `ps.stale_banner.dismissed.${data.available}` : null;
  const [dismissed, setDismissed] = React.useState(() => {
    if (!storageKey) return false;
    try { return localStorage.getItem(storageKey) === "1"; } catch (e) { return false; }
  });
  if (!data || dismissed) return null;
  const persistDismiss = () => {
    try { if (storageKey) localStorage.setItem(storageKey, "1"); } catch (e) {}
    setDismissed(true);
    onDismiss && onDismiss();
  };
  const persistPin = () => {
    try { if (storageKey) localStorage.setItem(storageKey, "1"); } catch (e) {}
    setDismissed(true);
    onPin && onPin();
  };
  return (
    <div className="banner">
      <div className="ico-wrap"><Ico name="bolt" /></div>
      <div className="banner-body">
        <div className="t">
          New data available ({data.available})
        </div>
        <div className="d">
          Your dataset <span className="mono" style={{ color: "var(--text)" }}>{data.dataset_id}</span> is locked to {data.pinnedTo}. Switching gives you {data.sources_added} more sources and {fmt.short(data.rows_added)} more rows; locking your current dataset keeps results reproducible.
        </div>
      </div>
      <div className="banner-actions">
        <button className="btn sm" onClick={persistPin}>Use new data</button>
        <button className="btn sm ghost" onClick={persistDismiss}>Keep current</button>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Modal — a11y wrapper for dialogs.
// * role="dialog" aria-modal="true"
// * focus trap: Tab cycles only inside the modal
// * Esc closes
// * Focus returns to the element that opened the modal on close
// * Click outside the modal body closes
//
// Usage:
//   <Modal open={open} onClose={...} title="Title" footer={<>buttons</>}>
//     <body content/>
//   </Modal>
// ────────────────────────────────────────────────────────────────────
function Modal({ open, onClose, title, titleIco, ariaLabel, children, footer, size = "md" }) {
  const dialogRef = React.useRef(null);
  const titleId = React.useRef(`m_${Math.random().toString(36).slice(2, 8)}`).current;
  const openerRef = React.useRef(null);

  // Capture opener focus on open; restore on close.
  React.useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement;
    // Focus the first focusable element inside the dialog (or the dialog itself).
    requestAnimationFrame(() => {
      if (!dialogRef.current) return;
      const focusable = dialogRef.current.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      (focusable[0] || dialogRef.current).focus();
    });
    return () => {
      if (openerRef.current && typeof openerRef.current.focus === "function") {
        try { openerRef.current.focus(); } catch (e) { /* element may have unmounted */ }
      }
    };
  }, [open]);

  // Esc + focus trap.
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose && onClose();
        return;
      }
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusable = Array.from(dialogRef.current.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter(el => el.offsetParent !== null);
      if (focusable.length === 0) { e.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="scrim" onClick={onClose}>
      <div
        className={"modal modal-" + size}
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={!title ? (ariaLabel || "Dialog") : undefined}
        tabIndex={-1}
        onClick={e => e.stopPropagation()}
      >
        {title && (
          <div className="modal-h">
            {titleIco && (
              <div className="ico-wrap" style={{ width: 28, height: 28, borderRadius: 6, background: "var(--surface-2)", color: "var(--text)", display: "grid", placeItems: "center" }}>
                <Ico name={titleIco} />
              </div>
            )}
            <div className="t" id={titleId}>{title}</div>
            <div style={{ flex: 1 }} />
            <button type="button" className="btn sm ghost" onClick={onClose} aria-label="Close dialog"><Ico name="x" size={12} /></button>
          </div>
        )}
        <div className="modal-b">{children}</div>
        {footer && <div className="modal-f">{footer}</div>}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Drawer — a11y wrapper for right-side detail/lineage drawers.
// Same focus-trap + return-focus + Esc semantics as Modal, plus a
// dedicated complementary role.
// ────────────────────────────────────────────────────────────────────
function Drawer({ open, onClose, title, titleIco, ariaLabel, children, footer, width = 420 }) {
  const drawerRef = React.useRef(null);
  const titleId = React.useRef(`d_${Math.random().toString(36).slice(2, 8)}`).current;
  const openerRef = React.useRef(null);

  React.useEffect(() => {
    if (!open) return;
    openerRef.current = document.activeElement;
    requestAnimationFrame(() => {
      if (!drawerRef.current) return;
      const focusable = drawerRef.current.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])'
      );
      (focusable[0] || drawerRef.current).focus();
    });
    return () => {
      if (openerRef.current && typeof openerRef.current.focus === "function") {
        try { openerRef.current.focus(); } catch (e) { /* gone */ }
      }
    };
  }, [open]);

  React.useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        e.stopPropagation();
        onClose && onClose();
        return;
      }
      if (e.key !== "Tab" || !drawerRef.current) return;
      const focusable = Array.from(drawerRef.current.querySelectorAll(
        'a[href], button:not([disabled]), textarea, input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'
      )).filter(el => el.offsetParent !== null);
      if (focusable.length === 0) { e.preventDefault(); return; }
      const first = focusable[0], last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="scrim" onClick={onClose}>
      <aside
        className="drawer"
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={title ? titleId : undefined}
        aria-label={!title ? (ariaLabel || "Detail drawer") : undefined}
        tabIndex={-1}
        style={{ width }}
        onClick={e => e.stopPropagation()}
      >
        {title && (
          <div className="drawer-h">
            {titleIco && <Ico name={titleIco} />}
            <div className="t" id={titleId}>{title}</div>
            <div style={{ flex: 1 }} />
            <button type="button" className="btn sm ghost" onClick={onClose} aria-label="Close drawer"><Ico name="x" size={12} /></button>
          </div>
        )}
        <div className="drawer-b">{children}</div>
        {footer && <div className="drawer-f">{footer}</div>}
      </aside>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// SettingRow — generic renderer for one entry in PS_DEEP_SETTINGS.
// Driven entirely by the item spec; the parent panel owns the value
// and supplies an onChange callback.
//
// Supported types: select, multi-select, chips, bool, int, float, range.
// ────────────────────────────────────────────────────────────────────
function SettingRow({ item, value, onChange, tier }) {
  const v = value === undefined ? item.default : value;
  const id = React.useRef(`s_${Math.random().toString(36).slice(2, 8)}`).current;
  const isBlocked = item.tier === "planned_inactive";
  const labelEl = (
    <label htmlFor={id} className="label" style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span>{item.label}</span>
      {item.tier && item.tier !== "release" && <TierPill tier={item.tier} />}
    </label>
  );

  let control;
  if (item.type === "select") {
    control = (
      <select id={id} className="select" value={String(v)} disabled={isBlocked}
        onChange={e => {
          const raw = e.target.value;
          const native = item.options && typeof item.options[0] === "number" ? Number(raw) : raw;
          onChange(item.key, native);
        }}>
        {(item.options || []).map(o => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
      </select>
    );
  } else if (item.type === "multi-select") {
    const sel = new Set(Array.isArray(v) ? v : []);
    control = (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {(item.options || []).map(o => {
          const on = sel.has(o);
          return (
            <button key={o} type="button" className={"chip-toggle" + (on ? " on" : "")}
              aria-pressed={on} disabled={isBlocked}
              onClick={() => {
                const next = on ? Array.from(sel).filter(x => x !== o) : [...sel, o];
                onChange(item.key, next);
              }}>
              {o}
            </button>
          );
        })}
      </div>
    );
  } else if (item.type === "chips") {
    // Buffer the raw string locally so the user can type spaces / commas
    // mid-token without the value being collapsed on every keystroke.
    // We only commit on blur.
    const list = Array.isArray(v) ? v : [];
    const [buf, setBuf] = React.useState(list.join(", "));
    React.useEffect(() => { setBuf(list.join(", ")); }, [list.join(",")]);
    control = (
      <input id={id} className="input mono" value={buf}
        disabled={isBlocked}
        onChange={e => setBuf(e.target.value)}
        onBlur={() => onChange(item.key, buf.split(/[,\s]+/).map(s => s.trim()).filter(Boolean))}
        placeholder="comma-separated" />
    );
  } else if (item.type === "bool") {
    control = (
      <button type="button" role="switch" aria-checked={!!v} disabled={isBlocked}
        className={"chip-toggle" + (v ? " on" : "")}
        onClick={() => onChange(item.key, !v)}>
        {v ? "on" : "off"}
      </button>
    );
  } else if (item.type === "int" || item.type === "float") {
    const step = item.step ?? (item.type === "int" ? 1 : 0.01);
    // Guard NaN: parseFloat("") yields NaN, which both warns React on the
    // controlled value and breaks the override-vs-default comparison (NaN !== NaN).
    // We display "" while the input is blank but keep the previous numeric value
    // committed; on a real number the parser commits it.
    const display = (typeof v === "number" && Number.isFinite(v)) ? v : "";
    control = (
      <input id={id} type="number" className="input mono" value={display}
        min={item.min} max={item.max} step={step} disabled={isBlocked}
        onChange={e => {
          const raw = e.target.value;
          if (raw === "") return; // do not commit a blank as NaN
          const n = item.type === "int" ? parseInt(raw, 10) : parseFloat(raw);
          if (Number.isFinite(n)) onChange(item.key, n);
        }}
        onBlur={e => {
          // On blur, if still blank, restore the default so we don't leave a bad value.
          if (e.target.value === "") onChange(item.key, item.default);
        }} />
    );
  } else {
    control = <input id={id} className="input mono" value={String(v)} disabled={isBlocked} onChange={e => onChange(item.key, e.target.value)} />;
  }

  return (
    <div className="setting-row" data-tier={item.tier} data-blocked={isBlocked ? "true" : "false"}>
      <div className="setting-row-h">
        {labelEl}
        {item.tooltip && (
          <span className="setting-help" title={item.tooltip}><Ico name="info" size={11} /></span>
        )}
      </div>
      {control}
      {item.tooltip && <div className="help">{item.tooltip}</div>}
    </div>
  );
}

// SettingsPanel — render an entire PS_DEEP_SETTINGS panel (with groups).
// Visible-when conditions in `show_if` apply against the current value
// object. The panel manages its own collapse state per-group.
//
// The collapse state resets whenever `panelKey` changes, so switching
// the modal between "training_advanced" and "inference_advanced" without
// unmount doesn't carry stale group keys across.
function SettingsPanel({ panelKey, values, onChange, defaultExpanded = "all", filterTier = "all" }) {
  const panel = (window.PS_DEEP_SETTINGS || {})[panelKey];
  const buildInitial = (p) => {
    const out = {};
    if (p) Object.keys(p.groups).forEach(g => { out[g] = (defaultExpanded === "all"); });
    return out;
  };
  const [open, setOpen] = React.useState(() => buildInitial(panel));
  React.useEffect(() => { setOpen(buildInitial(panel)); }, [panelKey]);
  if (!panel) return null;

  const matchesIf = (item) => {
    if (!item.show_if) return true;
    return Object.entries(item.show_if).every(([k, expected]) => values[k] === expected);
  };
  const matchesTier = (item) => {
    if (filterTier === "all") return true;
    if (filterTier === "release") return item.tier === "release";
    if (filterTier === "stable") return item.tier === "release" || item.tier === "beta";
    return true;
  };

  return (
    <div className="settings-panel" data-panel={panelKey}>
      {Object.entries(panel.groups).map(([gKey, group]) => {
        const items = group.items.filter(matchesIf).filter(matchesTier);
        if (items.length === 0) return null;
        const isOpen = !!open[gKey];
        const bodyId = `${panelKey}_${gKey}_b`;
        return (
          <div className="settings-group" key={gKey}>
            <button type="button" className="settings-group-h"
              aria-expanded={isOpen}
              aria-controls={bodyId}
              onClick={() => setOpen(o => ({ ...o, [gKey]: !o[gKey] }))}>
              <Ico name={isOpen ? "chev-down" : "chev"} size={12} />
              <span>{group.label}</span>
              <span className="settings-count">{items.length}</span>
            </button>
            {isOpen && (
              <div id={bodyId} className="settings-group-b">
                {items.map(item => (
                  <SettingRow
                    key={item.key}
                    item={item}
                    value={values[item.key]}
                    onChange={onChange}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Cost guardrail modal — when a launch would exceed a hard cap.
// ────────────────────────────────────────────────────────────────────
function CostGuardModal({ open, onClose, onOverride, breach }) {
  const [reason, setReason] = React.useState("");
  const [reviewer, setReviewer] = React.useState("");
  React.useEffect(() => { if (open) { setReason(""); setReviewer(""); } }, [open]);
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={`Cost guardrail — ${breach?.kind || "limit"} exceeded`}
      titleIco="warn"
      ariaLabel="Cost guardrail override"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
          <button className="btn" disabled={!reason || !reviewer} onClick={() => onOverride({ reason, reviewer })} style={{ opacity: (!reason || !reviewer) ? 0.5 : 1 }}>
            Override and launch
          </button>
        </>
      }
    >
      <p style={{ marginTop: 0, color: "var(--muted)", fontSize: 13 }}>
        Launching this would spend <span className="mono" style={{ color: "var(--text-strong)" }}>{fmt.money(breach?.cost || 0)}</span>,
        which is {fmt.money((breach?.cost || 0) - (breach?.cap || 0))} over the {breach?.kind || "per-run"} cap of {fmt.money(breach?.cap || 0)}.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, margin: "12px 0", padding: 12, background: "var(--bg-soft)", borderRadius: "var(--r)" }}>
        <Stat k="This run" v={fmt.money(breach?.cost || 0)} mono />
        <Stat k="Cap" v={fmt.money(breach?.cap || 0)} mono />
        <Stat k="Override delta" v={fmt.money((breach?.cost || 0) - (breach?.cap || 0))} mono />
      </div>
      <div className="label">Reason for override (will be logged)</div>
      <textarea className="input" rows="2" value={reason} onChange={e => setReason(e.target.value)} placeholder="e.g. final pre-paper sweep, approved by Mira" />
      <div className="label" style={{ marginTop: 10 }}>Reviewer who approved</div>
      <input className="input" value={reviewer} onChange={e => setReviewer(e.target.value)} placeholder="@username" />
    </Modal>
  );
}

// ────────────────────────────────────────────────────────────────────
// Card states — Empty, Loading (skeleton), Error.
// Each card on every screen should render one of these when its data
// isn't in the happy path.
// ────────────────────────────────────────────────────────────────────
function EmptyState({ ico = "dataset", title, body, cta, onCta }) {
  return (
    <div className="state empty">
      <div className="ico-wrap"><Ico name={ico} size={18} /></div>
      <div className="t">{title}</div>
      <div className="d">{body}</div>
      {cta && <button className="btn primary sm" onClick={onCta}>{cta}</button>}
    </div>
  );
}

function ErrorState({ message, errorId, onRetry }) {
  return (
    <div className="state error">
      <div className="ico-wrap"><Ico name="warn" size={18} /></div>
      <div className="t">Couldn't load this card</div>
      <div className="d">{message}</div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button className="btn sm" onClick={onRetry}><Ico name="bolt" size={12} /> Retry</button>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--dim)", cursor: "pointer" }} title="Copy error id">
          {errorId} <Ico name="link" size={10} />
        </span>
      </div>
    </div>
  );
}

function LoadingSkeleton({ rows = 4, height = 14 }) {
  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="sk sk-lg" style={{ width: "40%" }} />
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="sk sk-row" style={{ width: `${100 - i * 12}%`, height }} />
      ))}
    </div>
  );
}

// Wrap any card body and pick the right state. Cycle button on the right
// of the card header lets the user/dev preview each state for that card.
function StatefulCard({ title, subtitle, state = "ok", children, onCycle, errorMsg, errorId, emptyTitle, emptyBody, emptyCta, onEmptyCta, emptyIco, headerRight }) {
  return (
    <div className="card">
      <div className="card-h">
        <span className="t">{title}</span>
        {subtitle && <span className="sub">{subtitle}</span>}
        <div style={{ flex: 1 }} />
        {headerRight}
        {onCycle && (
          <button className="btn sm ghost" title="Cycle state" onClick={onCycle} style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}>
            state: {state} <Ico name="chev" size={10} />
          </button>
        )}
      </div>
      {state === "ok" && children}
      {state === "loading" && <LoadingSkeleton />}
      {state === "empty" && <EmptyState title={emptyTitle} body={emptyBody} cta={emptyCta} onCta={onEmptyCta} ico={emptyIco} />}
      {state === "error" && <ErrorState message={errorMsg || "Server returned 502 from /v1/runs"} errorId={errorId || "req_3a1f9d"} onRetry={onCycle} />}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// BlockerCard — validator-driven. Click an "Open field" link and the
// app scrolls + flashes the offending field on the relevant screen.
// ────────────────────────────────────────────────────────────────────
function BlockerCard({ item, onJump }) {
  const ico = item.level === "blocker" ? "warn" : item.level === "warning" ? "info" : "sparkle";
  return (
    <div className="rec" data-level={item.level} onClick={() => onJump && onJump(item)}>
      <div className="lvl"><Ico name={ico} size={11} /></div>
      <div className="body">
        <div className="msg">{item.message}</div>
        <div className="action">{item.action}</div>
        {item.related_fields?.[0] && (
          <span className="open-link">
            <Ico name="arrowR" size={11} /> Open {item.related_fields[0]}
          </span>
        )}
      </div>
    </div>
  );
}

// Imperative helper: scroll to a field anchor and flash it.
function jumpToField(anchorId, setScreen, screen) {
  if (screen && setScreen) setScreen(screen);
  // Defer so the screen render happens first
  setTimeout(() => {
    const el = document.querySelector(`[data-field="${anchorId}"]`);
    if (el) {
      el.dataset.flash = "true";
      const top = el.getBoundingClientRect().top + (document.querySelector(".main")?.scrollTop || 0) - 120;
      const main = document.querySelector(".main");
      if (main) main.scrollTo({ top, behavior: "smooth" });
      setTimeout(() => { delete el.dataset.flash; }, 4000);
    }
  }, 80);
}

// ────────────────────────────────────────────────────────────────────
// Provenance chip + LineageDrawer
// Hover: full hash + "Copy" + "Open in lineage drawer" + "Recreate run"
// Lineage drawer: dependency graph (model → run → pipe → split → ds → wh).
// ────────────────────────────────────────────────────────────────────
function ProvenanceChip({ hash = "9f3a4e2", label = "ds_kc3_v3", onOpen }) {
  const [pop, setPop] = React.useState(false);
  return (
    <span style={{ position: "relative", display: "inline-flex" }} onMouseEnter={() => setPop(true)} onMouseLeave={() => setPop(false)}>
      <Chip>
        <span style={{ color: "var(--dim)", marginRight: 4 }}>sha</span>{hash.slice(0, 4)}…
      </Chip>
      {pop && (
        <div style={{
          position: "absolute", top: "calc(100% + 6px)", left: 0, zIndex: 80,
          background: "var(--surface-3)", border: "1px solid var(--border-strong)",
          borderRadius: "var(--r)", padding: 10, minWidth: 280,
          boxShadow: "0 8px 24px #000a", fontFamily: "var(--font-mono)", fontSize: 11
        }}>
          <div style={{ color: "var(--muted)", marginBottom: 6 }}>{label}</div>
          <div style={{ color: "var(--text)", marginBottom: 8, wordBreak: "break-all" }}>sha256:{hash}…</div>
          <div style={{ display: "flex", gap: 6 }}>
            <button type="button" className="btn sm" style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}
              onClick={() => {
                navigator.clipboard?.writeText(`sha256:${hash}`);
                pushToast({ title: "Hash copied", body: `sha256:${hash}`, level: "ok", ttl_ms: 2200 });
              }}>Copy</button>
            <button type="button" className="btn sm" style={{ fontFamily: "var(--font-mono)", fontSize: 10 }} onClick={onOpen}>Open lineage</button>
            <button type="button" className="btn sm" style={{ fontFamily: "var(--font-mono)", fontSize: 10 }}
              onClick={() => pushToast({
                title: "Run re-staged",
                body: `Would clone the pipeline / split / dataset / advanced settings keyed on sha256:${hash.slice(0,8)}… into a new draft.`,
                level: "info",
              })}>Recreate run</button>
          </div>
        </div>
      )}
    </span>
  );
}

function LineageDrawer({ open, onClose, pushToast: extPushToast }) {
  const toast = extPushToast || pushToast;
  // Each lineage node carries `nav` — the screen to jump to when the user
  // clicks it. The Model node goes to Promote (registry-style), the Run
  // node to Training, etc. Warehouse jumps to Library.
  const chain = [
    { kind: "Model",     id: "model_v3.0.1",  hash: "a91f",  meta: "promoted candidate",                          nav: "promote" },
    { kind: "Run",       id: "run_4192_kc3",  hash: "9f3a",  meta: "trained 2026-05-13",                          nav: "training" },
    { kind: "Pipeline",  id: "pipe_kc3_v3",   hash: "c4b1",  meta: "cross-attn + esm2-650m + molformer",         nav: "pipeline" },
    { kind: "Split",     id: "split_kc3_v3",  hash: "7d20",  meta: "leakage-aware cluster, prot=0.30/lig=0.40",  nav: "split" },
    { kind: "Dataset",   id: "ds_kc3_v3",     hash: "9f3a",  meta: "1.84M rows · 12 sources",                    nav: "dataset" },
    { kind: "Warehouse", id: "v2026.04",      hash: "b0e2",  meta: "consolidated 2026-04-12",                    nav: "library" },
  ];
  const handleNodeClick = (c) => {
    toast && toast({
      title: `Opening ${c.kind} · ${c.id}`,
      body: `Jumping to the ${c.kind} screen at sha ${c.hash}…`,
      level: "info",
      ttl_ms: 2200,
    });
    // Dispatch the navigate event synchronously so the screen change happens
    // before the drawer's close-effect re-focuses the opener (which would
    // otherwise live on the previous screen and cause focus to vanish).
    window.dispatchEvent(new CustomEvent("navigate-to", { detail: { screen: c.nav } }));
    onClose && onClose();
  };
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Lineage · run_4192_kc3"
      titleIco="layers"
      ariaLabel="Run lineage drawer"
      footer={
        <>
          <button className="btn sm" onClick={() => {
            const citation = 'ProteoSphere run_4192_kc3 · model_v3.0.1 · dataset ds_kc3_v3@sha:9f3a… · split policy=cluster prot=0.30 lig=0.40 seed=4192 · KinaseCore-v3 (Anvil Lab, 2026-05)';
            navigator.clipboard?.writeText(citation);
            toast && toast({ title: "Citation copied", body: citation, level: "ok" });
          }}>Copy citation</button>
          <button className="btn sm primary" onClick={() => {
            toast && toast({
              title: "Run config re-staged",
              body: "Would clone run_4192_kc3's pipeline + split + dataset + advanced settings into a new draft, ready to launch from the Pipeline screen.",
              level: "info",
            });
            onClose && onClose();
          }}>Recreate this run</button>
        </>
      }
    >
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>
        Every artifact downstream depends on every artifact above it. Click any node to inspect.
      </div>
      {chain.map((c, i) => (
        <button key={c.id} type="button"
          onClick={() => handleNodeClick(c)}
          aria-label={`Open ${c.kind} ${c.id} on its screen`}
          style={{
            display: "flex", gap: 12, marginBottom: i < chain.length - 1 ? 0 : 4,
            padding: "4px 6px", border: 0, borderRadius: "var(--r)",
            background: "transparent", color: "var(--text)", cursor: "pointer",
            textAlign: "left", font: "inherit", width: "100%",
          }}
          onMouseEnter={e => e.currentTarget.style.background = "var(--surface-2)"}
          onMouseLeave={e => e.currentTarget.style.background = "transparent"}>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
            <div style={{ width: 24, height: 24, borderRadius: 6, background: i === 0 ? "var(--primary)" : "var(--surface-3)", display: "grid", placeItems: "center", color: i === 0 ? "#021624" : "var(--muted)" }}>
              <Ico name={["target","train","pipeline","split","dataset","layers"][i]} size={12} />
            </div>
            {i < chain.length - 1 && <div style={{ width: 2, flex: 1, background: "var(--border-strong)", minHeight: 22 }} />}
          </div>
          <div style={{ flex: 1, padding: "2px 0 16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.08em", textTransform: "uppercase" }}>{c.kind}</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>·</span>
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>sha {c.hash}…</span>
              <div style={{ flex: 1 }} />
              <Ico name="chevR" size={11} style={{ color: "var(--dim)" }} />
            </div>
            <div style={{ fontSize: 13, color: "var(--text-strong)", marginTop: 2 }}>{c.id}</div>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{c.meta}</div>
          </div>
        </button>
      ))}
    </Drawer>
  );
}

// ────────────────────────────────────────────────────────────────────
// New brand mark — helix + small molecule docked in a binding cleft.
// Replaces the v1 radial-gradient blob (literal but memorable).
// ────────────────────────────────────────────────────────────────────
function BrandMark({ size = 28 }) {
  return (
    <svg viewBox="0 0 28 28" width={size} height={size} style={{ display: "block" }}>
      <defs>
        <linearGradient id="bm-helix" x1="0" x2="1">
          <stop offset="0" stopColor="var(--primary)" />
          <stop offset="1" stopColor="var(--molecular)" />
        </linearGradient>
      </defs>
      {/* Helix backbone — two intertwining curves forming a cleft on the right */}
      <path d="M 4 4 Q 14 8, 12 14 Q 10 20, 16 24" stroke="url(#bm-helix)" strokeWidth="1.8" fill="none" strokeLinecap="round" />
      <path d="M 4 24 Q 14 20, 12 14 Q 10 8, 16 4"  stroke="url(#bm-helix)" strokeWidth="1.8" fill="none" strokeLinecap="round" opacity="0.7" />
      {/* Cross-bars */}
      {[7, 11, 17, 21].map(y => (
        <line key={y} x1="6" y1={y} x2="14" y2={y} stroke="var(--molecular)" strokeWidth="0.6" opacity="0.5" />
      ))}
      {/* Small molecule docked into the cleft (top-right region) */}
      <g transform="translate(20 12)">
        <line x1="-2" y1="-2" x2="2" y2="-2" stroke="var(--signal)" strokeWidth="1" />
        <line x1="2"  y1="-2" x2="2" y2="2"  stroke="var(--signal)" strokeWidth="1" />
        <line x1="-2" y1="-2" x2="-2" y2="2" stroke="var(--signal)" strokeWidth="1" />
        <line x1="-2" y1="2"  x2="2" y2="2"  stroke="var(--signal)" strokeWidth="1" />
        <circle cx="-2" cy="-2" r="1.2" fill="var(--signal)" />
        <circle cx="2"  cy="-2" r="1.2" fill="var(--signal)" />
        <circle cx="-2" cy="2"  r="1.2" fill="var(--signal)" />
        <circle cx="2"  cy="2"  r="1.2" fill="var(--signal)" />
      </g>
    </svg>
  );
}

// ────────────────────────────────────────────────────────────────────
// Toast system — small transient notifications anchored top-right.
// Every "would call the backend" stub in the prototype pushes a toast
// with a clear description of what it would do (this is a fixture-only
// build with no live backend, so producing visible feedback for every
// button is the alternative to a blank click).
//
// Usage:
//   const pushToast = useToastBus();
//   pushToast({ title: "Promoted to prod (mock)", body: "Would write …", level: "ok" });
//
// In App, render <ToastBus/> exactly once near the root.
//
// Levels: ok | info | warn | error.
// ────────────────────────────────────────────────────────────────────
window.__ps_toast_listeners = window.__ps_toast_listeners || new Set();
let __ps_toast_seq = 0;
function pushToast(t) {
  const payload = {
    id: ++__ps_toast_seq,
    title: t.title || "",
    body: t.body || "",
    level: t.level || "info",
    ttl_ms: t.ttl_ms ?? 4200,
  };
  for (const fn of window.__ps_toast_listeners) {
    try { fn(payload); } catch (e) { /* ignore listener error */ }
  }
  return payload.id;
}
function useToastBus() { return pushToast; }

function ToastBus() {
  const [toasts, setToasts] = React.useState([]);
  React.useEffect(() => {
    const onPush = (t) => {
      setToasts(prev => [...prev, t]);
      if (t.ttl_ms > 0) {
        setTimeout(() => setToasts(prev => prev.filter(x => x.id !== t.id)), t.ttl_ms);
      }
    };
    window.__ps_toast_listeners.add(onPush);
    return () => window.__ps_toast_listeners.delete(onPush);
  }, []);
  const dismiss = (id) => setToasts(prev => prev.filter(t => t.id !== id));
  return (
    <div className="toast-bus" role="region" aria-label="Notifications" aria-live="polite">
      {toasts.map(t => (
        <div key={t.id} className={"toast toast-" + t.level} role={t.level === "error" ? "alert" : "status"}>
          <Ico name={t.level === "error" ? "warn" : t.level === "warn" ? "warn" : t.level === "ok" ? "check" : "info"} size={14} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="toast-title">{t.title}</div>
            {t.body && <div className="toast-body">{t.body}</div>}
          </div>
          <button type="button" className="toast-x" onClick={() => dismiss(t.id)} aria-label="Dismiss notification">
            <Ico name="x" size={11} />
          </button>
        </div>
      ))}
    </div>
  );
}

Object.assign(window, {
  TierPill, Term, LaneBar, StaleBanner,
  Modal, Drawer,
  SettingRow, SettingsPanel,
  CostGuardModal,
  EmptyState, ErrorState, LoadingSkeleton, StatefulCard,
  BlockerCard, jumpToField, ProvenanceChip, LineageDrawer, BrandMark,
  pushToast, useToastBus, ToastBus,
});
