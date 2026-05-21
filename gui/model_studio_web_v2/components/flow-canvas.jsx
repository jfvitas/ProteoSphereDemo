// ProteoSphere — Flow Builder canvas (LabVIEW-style DAG editor)
//
// Ported from proteosphere/project/flow-v4/canvas.jsx with the
// FV_* → PS_FLOW_* namespace migration. All pointer-driven mechanics
// preserved:
//   - Drag a node body → move it on the grid (snap to 24-px)
//   - Drag from an output port → bezier ghost wire follows the cursor
//   - Release on an input port:
//       * types compatible → commit edge
//       * types incompatible → red flash + teach tooltip with bridge suggestion
//       * forms a cycle → snap back + toast
//   - Click a node → select for inspector
//   - Right-click a node → context menu (replace impl / disconnect / delete)

const FLOW_NODE_W = 200;
const FLOW_NODE_H_MIN = 88;
const flowPortHeight = (block) => {
  const n = Math.max((block.inputs || []).length, (block.outputs || []).length, 1);
  return Math.max(FLOW_NODE_H_MIN, 36 + n * 18);
};

function flowPortPosition(node, side, portName, blockDef) {
  const ports = side === "in" ? (blockDef.inputs || []) : (blockDef.outputs || []);
  const idx = ports.findIndex(p => p.port === portName);
  if (idx === -1) return null;
  const h = flowPortHeight(blockDef);
  const cx = side === "in" ? node.x : node.x + FLOW_NODE_W;
  const cy = node.y + ((idx + 1) / (ports.length + 1)) * h;
  return { x: cx, y: cy };
}

function flowBezierPath(x1, y1, x2, y2) {
  const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
  return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
}

// Suggest a 1-hop bridge node that resolves a type mismatch.
function suggestFlowBridge(sourceType, targetTypes) {
  const all = Object.values(window.PS_FLOW_BLOCKS).flat();
  for (const b of all) {
    const accepts = (b.inputs || []).some(p => (p.types || []).includes(sourceType));
    const emits   = (b.outputs || []).some(p => targetTypes.includes(p.type));
    if (accepts && emits) return b;
  }
  if (sourceType === "sequence" && targetTypes.includes("graph"))
    return { role: "GraphBuilder", id: "_bridge_graphbuilder",
             note: "Build a graph from the sequence first." };
  return null;
}

// Cycle check via DFS over the proposed edge set.
function flowWouldCycle(nodes, edges, proposed) {
  const next = [...edges, proposed];
  const adj = {};
  for (const e of next) {
    const [f] = e.from.split(":");
    const [t] = e.to.split(":");
    (adj[f] = adj[f] || []).push(t);
  }
  const WHITE = 0, GREY = 1, BLACK = 2;
  const color = {};
  for (const n of nodes) color[n.id] = WHITE;
  function dfs(u) {
    color[u] = GREY;
    for (const v of (adj[u] || [])) {
      if (color[v] === GREY) return true;
      if (color[v] === WHITE && dfs(v)) return true;
    }
    color[u] = BLACK;
    return false;
  }
  for (const n of nodes) if (color[n.id] === WHITE && dfs(n.id)) return true;
  return false;
}

// validateGraph(nodes, edges) → { state, issues }
function validateFlowGraph(nodes, edges) {
  const issues = [];
  const incoming = {};
  for (const e of edges) {
    const [t, tp] = e.to.split(":");
    (incoming[t] = incoming[t] || new Set()).add(tp);
  }
  for (const n of nodes) {
    const def = window.PS_FLOW_BLOCK_INDEX[n.block_id];
    if (!def || def.cat === "input") continue;
    // Per-impl arity check: fusion blocks declare arity ("any" or 2) on
    // each impl, so variadic impls don't get flagged for unwired
    // optional ports. Required ports are the non-optional ones.
    const impl = (def.impls || []).find(i => i.id === n.impl_id) || def.impls?.[0];
    const arity = impl?.arity;
    for (const p of (def.inputs || [])) {
      const wired = incoming[n.id]?.has(p.port);
      if (wired) continue;
      // For variadic ("any") fusion impls, optional ports are fine
      // unconnected — they're literally optional.
      if (p.optional && arity === "any") continue;
      // For arity=2 fusion impls, only ports a + b are required.
      if (def.cat === "fusion" && arity === 2 && !(p.port === "a" || p.port === "b")) continue;
      // Otherwise flag the missing required input.
      issues.push(`'${def.role}' input '${p.port}' is unconnected`);
    }
    // Variadic fusion also needs at least one inbound to be useful.
    if (def.cat === "fusion" && arity === "any" && (!incoming[n.id] || incoming[n.id].size === 0)) {
      issues.push(`'${def.role}' needs at least one inbound edge`);
    }
  }
  const outgoing = {};
  for (const e of edges) { const [f] = e.from.split(":"); outgoing[f] = (outgoing[f] || 0) + 1; }
  for (const n of nodes) {
    const def = window.PS_FLOW_BLOCK_INDEX[n.block_id];
    if (!def || def.cat !== "input") continue;
    if (!outgoing[n.id]) issues.push(`Input '${def.role}' is unused`);
  }
  const heads = nodes.filter(n => window.PS_FLOW_BLOCK_INDEX[n.block_id]?.cat === "head");
  if (heads.length === 0) issues.push("No head — pipeline has nothing to predict");
  if (nodes.length === 0) return { state: "error", issues: ["Canvas is empty. Drag a block from the palette to begin."] };
  if (issues.length === 0) return { state: "ok",    issues: ["all inputs covered · head connected · ready to train"] };
  return { state: issues.some(i => /head|empty/.test(i)) ? "error" : "warn", issues };
}

function FlowCanvas({
  nodes, edges, selectedId,
  onMoveNode, onSelect, onAddEdge, onDeleteEdge, onDeleteNode, onContextMenu, onCanvasReady,
  gridSize = 24,
}) {
  const canvasRef = React.useRef(null);
  const [wireDrag, setWireDrag] = React.useState(null);
  const [teach, setTeach] = React.useState(null);
  const [toast, setToast] = React.useState(null);
  const [nodeDrag, setNodeDrag] = React.useState(null);

  React.useEffect(() => { onCanvasReady && onCanvasReady(canvasRef); }, []);

  // ── Wire-drag handler ────────────────────────────────────────────
  const startWire = (e, node, port, type) => {
    e.stopPropagation(); e.preventDefault();
    const rect = canvasRef.current.getBoundingClientRect();
    const blockDef = window.PS_FLOW_BLOCK_INDEX[node.block_id];
    const pos = flowPortPosition(node, "out", port, blockDef);
    setWireDrag({
      fromNodeId: node.id, fromPort: port, fromType: type,
      x1: pos.x, y1: pos.y,
      curX: e.clientX - rect.left + canvasRef.current.scrollLeft,
      curY: e.clientY - rect.top  + canvasRef.current.scrollTop,
    });
    setTeach(null);
    const onMove = (ev) => {
      const r = canvasRef.current.getBoundingClientRect();
      setWireDrag(w => w && ({ ...w,
        curX: ev.clientX - r.left + canvasRef.current.scrollLeft,
        curY: ev.clientY - r.top  + canvasRef.current.scrollTop,
      }));
    };
    const onUp = (ev) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup",   onUp);
      const hitEl = document.elementFromPoint(ev.clientX, ev.clientY);
      const portEl = hitEl?.closest?.("[data-port-side='in']");
      if (portEl) {
        const targetNodeId = portEl.dataset.portNode;
        const targetPort   = portEl.dataset.portName;
        const targetTypes  = (portEl.dataset.portTypes || "").split(",");
        if (targetTypes.includes(type)) {
          if (flowWouldCycle(nodes, edges, { from: `${node.id}:${port}`, to: `${targetNodeId}:${targetPort}` })) {
            setToast("That edge would form a cycle. Pipelines must be acyclic.");
            setTimeout(() => setToast(null), 4200);
          } else {
            onAddEdge && onAddEdge({ from: `${node.id}:${port}`, to: `${targetNodeId}:${targetPort}` });
          }
        } else {
          const r = canvasRef.current.getBoundingClientRect();
          setTeach({
            x: ev.clientX - r.left + canvasRef.current.scrollLeft + 14,
            y: ev.clientY - r.top  + canvasRef.current.scrollTop  + 12,
            sourceNodeId: node.id, sourceType: type,
            targetNodeId, targetPort, targetTypes,
            bridge: suggestFlowBridge(type, targetTypes),
          });
          setTimeout(() => setTeach(null), 6000);
        }
      }
      setWireDrag(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup",   onUp);
  };

  // ── Node-drag handler ────────────────────────────────────────────
  const startNodeDrag = (e, node) => {
    if (e.button !== 0) return;
    if (e.target.closest("[data-port-side]")) return;
    e.stopPropagation();
    const startX = e.clientX, startY = e.clientY;
    const origX = node.x, origY = node.y;
    setNodeDrag({ id: node.id });
    onSelect && onSelect(node.id);
    const onMove = (ev) => {
      const dx = ev.clientX - startX, dy = ev.clientY - startY;
      const nx = Math.max(0, Math.round((origX + dx) / gridSize) * gridSize);
      const ny = Math.max(0, Math.round((origY + dy) / gridSize) * gridSize);
      onMoveNode && onMoveNode(node.id, nx, ny);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup",   onUp);
      setNodeDrag(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup",   onUp);
  };

  const onCanvasMouseDown = (e) => {
    if (e.target === canvasRef.current || e.target.tagName === "svg") {
      onSelect && onSelect(null);
      setTeach(null);
    }
  };

  const maxX = nodes.length ? Math.max(...nodes.map(n => n.x + FLOW_NODE_W + 60)) : 0;
  const maxY = nodes.length ? Math.max(...nodes.map(n => n.y + flowPortHeight(window.PS_FLOW_BLOCK_INDEX[n.block_id] || {}) + 60)) : 0;
  const canvasW = Math.max(1200, maxX);
  const canvasH = Math.max(540, maxY);

  const renderedEdges = edges.map((e, i) => {
    const [fn, fp] = e.from.split(":");
    const [tn, tp] = e.to.split(":");
    const fromNode = nodes.find(n => n.id === fn);
    const toNode   = nodes.find(n => n.id === tn);
    if (!fromNode || !toNode) return null;
    const fromDef = window.PS_FLOW_BLOCK_INDEX[fromNode.block_id];
    const toDef   = window.PS_FLOW_BLOCK_INDEX[toNode.block_id];
    const a = flowPortPosition(fromNode, "out", fp, fromDef);
    const b = flowPortPosition(toNode,   "in",  tp, toDef);
    if (!a || !b) return null;
    const portOut = (fromDef.outputs || []).find(p => p.port === fp);
    return { i, edge: e, a, b,
      color: window.PS_FLOW_PORT_TYPES[portOut?.type]?.color || "var(--border-strong)" };
  }).filter(Boolean);

  const wireValidPortSet = React.useMemo(() => {
    if (!wireDrag) return null;
    const set = new Set();
    for (const n of nodes) {
      if (n.id === wireDrag.fromNodeId) continue;
      const def = window.PS_FLOW_BLOCK_INDEX[n.block_id];
      for (const p of (def?.inputs || [])) {
        if (p.types.includes(wireDrag.fromType)) set.add(`${n.id}:${p.port}`);
      }
    }
    return set;
  }, [wireDrag, nodes]);

  return (
    <div className="flow-canvas grid-bg" ref={canvasRef} onMouseDown={onCanvasMouseDown}>
      <div style={{ width: canvasW, height: canvasH, position: "relative" }}>
        <svg className="wires" width={canvasW} height={canvasH} preserveAspectRatio="none" style={{ position: "absolute" }}>
          {renderedEdges.map(({ i, a, b, color, edge }) => (
            <path key={"e" + i} className="edge" d={flowBezierPath(a.x, a.y, b.x, b.y)} stroke={color}
              onClick={() => onDeleteEdge && onDeleteEdge(edge)} />
          ))}
          {wireDrag && (
            <path className="edge ghost"
              d={flowBezierPath(wireDrag.x1, wireDrag.y1, wireDrag.curX, wireDrag.curY)}
              stroke={window.PS_FLOW_PORT_TYPES[wireDrag.fromType]?.color || "var(--primary)"} />
          )}
        </svg>

        {nodes.map(node => {
          const def = window.PS_FLOW_BLOCK_INDEX[node.block_id];
          if (!def) return null;
          const impl = (def.impls || []).find(i => i.id === node.impl_id) || def.impls[0];
          const h = flowPortHeight(def);
          return (
            <div key={node.id} className="flow-node" data-cat={def.cat}
              data-selected={node.id === selectedId}
              data-dragging={nodeDrag?.id === node.id}
              style={{ left: node.x, top: node.y, height: h }}
              onPointerDown={(e) => startNodeDrag(e, node)}
              onContextMenu={(e) => { e.preventDefault(); onContextMenu && onContextMenu(node, { x: e.clientX, y: e.clientY }); }}
              onClick={(e) => { e.stopPropagation(); onSelect && onSelect(node.id); }}
            >
              <div className="stripe" />
              <div className="node-h">
                <span className="cat-badge" data-cat={def.cat} style={{ width: 20, height: 20 }} />
                <div className="role" title={def.role}>{def.role}</div>
              </div>
              <div className={"impl-pill" + (impl.id === "identity" ? " identity" : "")}>
                <span className="arrow">→</span>
                {impl.label}
              </div>
              <div className="node-stats">
                {(def.outputs || []).length > 0 && <span>out: {def.outputs[0].type}</span>}
                <span style={{ flex: 1 }} />
                {impl.cost > 0 && <span>cost {impl.cost.toFixed(1)}</span>}
              </div>
              {(def.inputs || []).map((p, i, arr) => {
                const portId = `${node.id}:${p.port}`;
                const validHere = wireValidPortSet?.has(portId);
                const dimHere   = wireDrag && !validHere && wireDrag.fromNodeId !== node.id;
                return (
                  <div key={"in" + p.port} className="port"
                    data-side="in" data-port-side="in"
                    data-port-node={node.id} data-port-name={p.port}
                    data-port-types={p.types.join(",")} data-type={p.types[0]}
                    data-valid={wireDrag ? (validHere ? "true" : undefined) : undefined}
                    data-dim={dimHere ? "true" : undefined}
                    style={{ top: ((i + 1) / (arr.length + 1)) * h - 7 }}
                  >
                    <span className="port-label">{p.port}</span>
                  </div>
                );
              })}
              {(def.outputs || []).map((p, i, arr) => (
                <div key={"out" + p.port} className="port"
                  data-side="out" data-port-side="out"
                  data-port-node={node.id} data-port-name={p.port}
                  data-type={p.type}
                  style={{ top: ((i + 1) / (arr.length + 1)) * h - 7 }}
                  onPointerDown={(e) => startWire(e, node, p.port, p.type)}
                >
                  <span className="port-label">{p.port}</span>
                </div>
              ))}
            </div>
          );
        })}

        {teach && (
          <div className="teach-tooltip" style={{ left: teach.x, top: teach.y }} onClick={(e) => e.stopPropagation()}>
            <span className="k">Won't connect</span>
            <div>
              Output emits {/^[aeiou]/i.test(teach.sourceType) ? "an" : "a"} <strong style={{ color: window.PS_FLOW_PORT_TYPES[teach.sourceType]?.color }}>{teach.sourceType}</strong>;
              this port accepts {teach.targetTypes.map((t, i) => (
                <React.Fragment key={t}>
                  <strong style={{ color: window.PS_FLOW_PORT_TYPES[t]?.color }}>{t}</strong>
                  {i < teach.targetTypes.length - 1 ? " or " : ""}
                </React.Fragment>
              ))}.
            </div>
            {teach.bridge && (
              <div className="suggest">
                <div className="lbl">Suggest · {teach.bridge.role}</div>
                <div className="body">{teach.bridge.note || `Insert a ${teach.bridge.role} between these two — it accepts ${teach.sourceType} and emits ${teach.targetTypes[0]}.`}</div>
              </div>
            )}
            <button className="btn ghost" style={{ marginTop: 6, padding: "3px 8px", fontSize: 11 }}
              onClick={() => setTeach(null)}>Dismiss</button>
          </div>
        )}

        {toast && (
          <div style={{ position: "absolute", left: "50%", top: 22, transform: "translateX(-50%)",
            background: "var(--surface-3)", border: "1px solid var(--warn)", color: "var(--warn)",
            padding: "8px 14px", borderRadius: "var(--r)", fontSize: 12, zIndex: 55,
            boxShadow: "0 8px 24px rgba(0,0,0,0.5)" }}>
            ⚠ {toast}
          </div>
        )}

        {nodes.length === 0 && (
          <div className="canvas-empty">
            <div className="box">
              <div className="glyph"><Ico name="flow" size={28} /></div>
              <div style={{ fontSize: 14, color: "var(--text)", marginBottom: 6, fontWeight: 600 }}>
                Drag a block from the palette
              </div>
              <div>
                Start with an <span style={{ color: "var(--cat-input)" }}>Input</span>, add an{" "}
                <span style={{ color: "var(--cat-encoder)" }}>Encoder</span>, wire them up.
                Or click <strong style={{ color: "var(--primary)" }}>Load preset</strong> for a starting point.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function FlowContextMenu({ ctx, onClose, onDelete, onDisconnect, onReplaceImpl }) {
  React.useEffect(() => {
    if (!ctx) return;
    const onDoc = () => onClose();
    setTimeout(() => document.addEventListener("click", onDoc, { once: true }), 0);
    return () => document.removeEventListener("click", onDoc);
  }, [ctx]);
  if (!ctx) return null;
  return (
    <div className="ctx-menu" style={{ left: ctx.x, top: ctx.y }} onClick={(e) => e.stopPropagation()}>
      <button onClick={() => { onReplaceImpl(); onClose(); }}>
        <Ico name="settings" /> Replace implementation…
      </button>
      <button onClick={() => { onDisconnect(); onClose(); }}>
        <Ico name="link" /> Disconnect all wires
      </button>
      <hr />
      <button className="danger" onClick={() => { onDelete(); onClose(); }}>
        <Ico name="trash" /> Delete node
      </button>
    </div>
  );
}

Object.assign(window, {
  FlowCanvas, FlowContextMenu, validateFlowGraph, suggestFlowBridge,
  FLOW_NODE_W, FLOW_NODE_H_MIN, flowPortHeight, flowPortPosition,
});
