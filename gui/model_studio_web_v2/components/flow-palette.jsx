// ProteoSphere — Flow Builder palette (left rail of the flow editor)
//
// Ported from proteosphere/project/flow-v4/palette.jsx. Renames:
//   FV_BLOCKS       → PS_FLOW_BLOCKS
//   FV_BLOCK_INDEX  → PS_FLOW_BLOCK_INDEX
//
// Drag mechanics:
//   The card's onPointerDown bubbles up via onPaletteDragStart(blockId, e).
//   The parent screen owns the global pointermove follower (DragGhost at
//   the cursor) and the release-on-canvas commit.

const FLOW_PALETTE_GROUPS = [
  { key: "input",      label: "Inputs",      blocks: () => window.PS_FLOW_BLOCKS.inputs,     auto: true,
    hint: "Auto-populated from your Features selection. Each row matches a feature you ticked." },
  { key: "encoder",    label: "Encoders",    blocks: () => window.PS_FLOW_BLOCKS.encoders,
    hint: "Pick the role here, swap the implementation in the inspector." },
  { key: "fusion",     label: "Fusion",      blocks: () => window.PS_FLOW_BLOCKS.fusion,
    hint: "How two representations get combined into one." },
  { key: "head",       label: "Heads",       blocks: () => window.PS_FLOW_BLOCKS.head,
    hint: "What the model is asked to output." },
  { key: "diagnostic", label: "Diagnostics", blocks: () => window.PS_FLOW_BLOCKS.diagnostic,
    hint: "Inserted inline. No effect on training — just visibility." },
];

function BlockPalette({ onPaletteDragStart, search, setSearch, draggingBlockId, featuresPicked, hideAuto = false }) {
  const [open, setOpen] = React.useState(() => Object.fromEntries(FLOW_PALETTE_GROUPS.map(g => [g.key, true])));
  const q = (search || "").toLowerCase();

  return (
    <div className="flow-palette">
      <div className="palette-h">
        <Ico name="archive" size={14} style={{ color: "var(--primary)" }} />
        <span className="t">Block palette</span>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>drag to canvas</span>
      </div>
      {setSearch && (
        <div className="palette-search">
          <input placeholder="Search blocks…" value={search || ""}
            onChange={(e) => setSearch(e.target.value)} />
        </div>
      )}
      <div style={{ overflow: "auto", flex: 1 }}>
        {FLOW_PALETTE_GROUPS.map(group => {
          if (hideAuto && group.auto) return null;
          let blocks = group.blocks();
          if (group.auto && featuresPicked && featuresPicked.length > 0) {
            blocks = blocks.filter(b => !b.feature_id || featuresPicked.includes(b.feature_id));
          }
          if (q) blocks = blocks.filter(b =>
            b.role.toLowerCase().includes(q) ||
            (b.impls || []).some(im => im.label.toLowerCase().includes(q))
          );
          return (
            <div key={group.key} className="palette-group" data-open={String(open[group.key])}>
              <div className="palette-group-h" onClick={() => setOpen(o => ({ ...o, [group.key]: !o[group.key] }))}>
                <span className="cat-badge" data-cat={group.key} style={{ width: 18, height: 18 }} />
                <span className="name">{group.label}</span>
                <span className="count">{blocks.length}</span>
                <Ico name="chev" className="chev" size={10} />
              </div>
              {open[group.key] && group.auto && featuresPicked && featuresPicked.length > 0 && (
                <div style={{ padding: "2px 14px 6px", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", lineHeight: 1.45 }}>
                  {group.hint}
                </div>
              )}
              <div className="palette-row">
                {blocks.map(b => {
                  // A block is "all planned" when every impl is planned —
                  // i.e. the whole role is GUI-only. Some blocks have
                  // mixed impls; in that case we show no badge here and
                  // let the inspector flag the unwired choices.
                  const allPlanned = b.planned === true
                    || ((b.impls || []).length > 0
                        && (b.impls || []).every(i => i.planned === true));
                  const tip = allPlanned
                    ? `${b.impls.map(i => i.label).join(" · ")}\n\nPlanned: not yet wired to the trainer. You can still drop it on the canvas — backend support ships in a later stage.`
                    : b.impls.map(i => i.label).join(" · ");
                  return (
                    <div key={b.id} className="palette-card" data-cat={b.cat}
                      data-dragging={draggingBlockId === b.id}
                      onPointerDown={(e) => onPaletteDragStart && onPaletteDragStart(b.id, e)}
                      title={tip}
                      style={allPlanned ? { opacity: 0.65 } : null}
                    >
                      <Ico name="drag" size={10} style={{ color: "var(--dim)" }} />
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="label" style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span>{b.role}</span>
                          {allPlanned && (
                            <span style={{
                              fontSize: 8, fontFamily: "var(--font-mono)",
                              color: "var(--warn)", border: "1px solid var(--warn)",
                              borderRadius: 3, padding: "0 4px", lineHeight: 1.5,
                              letterSpacing: "0.04em",
                            }}>planned</span>
                          )}
                        </div>
                        {b.impls.length > 1 && (
                          <div className="impls">
                            {b.impls.slice(0, 3).map(i => i.label).join(" · ")}
                            {b.impls.length > 3 && " · …"}
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
                {blocks.length === 0 && (
                  <div style={{ padding: "8px 12px", fontSize: 11, color: "var(--dim)", fontStyle: "italic" }}>
                    {group.auto && (!featuresPicked || featuresPicked.length === 0)
                      ? "Pick features on the Features tab to populate."
                      : "No matches."}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ padding: "8px 14px", borderTop: "1px solid var(--border)", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", lineHeight: 1.5 }}>
        Role is fixed on drop.<br />Implementation swappable in inspector.
      </div>
    </div>
  );
}

// Drag-ghost rendered at the cursor while a palette card is being dragged.
function DragGhost({ blockId, x, y }) {
  if (!blockId || x == null) return null;
  const b = window.PS_FLOW_BLOCK_INDEX[blockId];
  if (!b) return null;
  return (
    <div className="drag-ghost" style={{ left: x + 12, top: y + 12 }}>
      <span className="cat-badge" data-cat={b.cat} style={{ width: 20, height: 20 }} />
      <div>
        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-strong)" }}>{b.role}</div>
        <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{b.cat.toUpperCase()}</div>
      </div>
    </div>
  );
}

Object.assign(window, { BlockPalette, DragGhost });
