// ProteoSphere — chart & viz primitives (inline SVG, no chart libs).

function useId() {
  return React.useMemo(() => "id-" + Math.random().toString(36).slice(2, 9), []);
}

// Sparkline / line chart with multiple series
function LineChart({ width = 480, height = 180, series, xKey = "epoch", yKey, yMin, yMax, grid = true, padding = [12, 8, 22, 36], showAxis = true, smooth = true, yFmt }) {
  const [pt, pr, pb, pl] = padding;
  const W = width, H = height;
  const allY = series.flatMap(s => s.data.map(d => d[yKey ?? s.yKey]));
  const allX = series.flatMap(s => s.data.map(d => d[xKey]));
  const xMin = Math.min(...allX), xMax = Math.max(...allX);
  const yLo = yMin ?? Math.min(...allY);
  const yHi = yMax ?? Math.max(...allY);
  const sx = (x) => pl + (x - xMin) / (xMax - xMin || 1) * (W - pl - pr);
  const sy = (y) => H - pb - (y - yLo) / (yHi - yLo || 1) * (H - pt - pb);

  const path = (data, ykey) => {
    if (!data.length) return "";
    const pts = data.map(d => [sx(d[xKey]), sy(d[ykey])]);
    if (!smooth) return "M" + pts.map(p => p.join(" ")).join(" L ");
    let d = `M ${pts[0][0]} ${pts[0][1]}`;
    for (let i = 1; i < pts.length; i++) {
      const [x1, y1] = pts[i - 1], [x2, y2] = pts[i];
      const cx = (x1 + x2) / 2;
      d += ` C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
    }
    return d;
  };

  const yTicks = 4;
  const ticks = Array.from({ length: yTicks + 1 }, (_, i) => yLo + (yHi - yLo) * i / yTicks);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {grid && ticks.map((t, i) => (
        <line key={i} x1={pl} x2={W - pr} y1={sy(t)} y2={sy(t)} stroke="var(--border)" strokeDasharray="2 3" />
      ))}
      {showAxis && ticks.map((t, i) => (
        <text key={i} x={pl - 6} y={sy(t) + 3} fill="var(--dim)" fontSize="9" fontFamily="var(--font-mono)" textAnchor="end">
          {yFmt ? yFmt(t) : Number(t).toFixed(2)}
        </text>
      ))}
      {series.map((s, i) => (
        <g key={i}>
          {s.fill && (
            <path d={`${path(s.data, yKey ?? s.yKey)} L ${sx(xMax)} ${sy(yLo)} L ${sx(xMin)} ${sy(yLo)} Z`}
                  fill={s.color} opacity="0.12" />
          )}
          <path d={path(s.data, yKey ?? s.yKey)} stroke={s.color} strokeWidth={s.width || 1.6} fill="none"
                strokeDasharray={s.dash || undefined} />
        </g>
      ))}
    </svg>
  );
}

// Scatter / calibration
function ScatterChart({ width = 320, height = 200, points, xRange = [0, 1], yRange = [0, 1], showDiagonal = true, padding = [10, 8, 22, 30], color = "var(--primary)", radius = 2 }) {
  const [pt, pr, pb, pl] = padding;
  const W = width, H = height;
  const sx = (x) => pl + (x - xRange[0]) / (xRange[1] - xRange[0]) * (W - pl - pr);
  const sy = (y) => H - pb - (y - yRange[0]) / (yRange[1] - yRange[0]) * (H - pt - pb);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {[0.25, 0.5, 0.75, 1].map(t => (
        <line key={t} x1={pl} x2={W - pr} y1={sy(t)} y2={sy(t)} stroke="var(--border)" strokeDasharray="2 3" />
      ))}
      {showDiagonal && (
        <line x1={sx(xRange[0])} y1={sy(xRange[0])} x2={sx(xRange[1])} y2={sy(xRange[1])} stroke="var(--dim)" strokeDasharray="3 3" />
      )}
      {points.map((p, i) => (
        <circle key={i} cx={sx(p[0])} cy={sy(p[1])} r={p[2] || radius} fill={p[3] || color} opacity={p[4] || 0.85} />
      ))}
      <line x1={pl} x2={pl} y1={pt} y2={H - pb} stroke="var(--border)" />
      <line x1={pl} x2={W - pr} y1={H - pb} y2={H - pb} stroke="var(--border)" />
    </svg>
  );
}

// Histogram
function Histogram({ width = 320, height = 120, bins, color = "var(--primary)", padding = [8, 8, 22, 30] }) {
  const [pt, pr, pb, pl] = padding;
  const W = width, H = height;
  const max = Math.max(...bins.map(b => b.v));
  const bw = (W - pl - pr) / bins.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto", display: "block" }}>
      {bins.map((b, i) => {
        const h = (b.v / max) * (H - pt - pb);
        return <rect key={i} x={pl + i * bw + 1} y={H - pb - h} width={bw - 2} height={h} fill={b.color || color} opacity={b.opacity || 0.85} rx="1" />;
      })}
      <line x1={pl} x2={W - pr} y1={H - pb} y2={H - pb} stroke="var(--border)" />
    </svg>
  );
}

// Donut for stat
function Donut({ size = 64, value, total, color = "var(--primary)", track = "var(--surface-3)", label }) {
  const r = (size - 8) / 2;
  const c = 2 * Math.PI * r;
  const p = Math.max(0, Math.min(1, value / total));
  return (
    <div style={{ position: "relative", width: size, height: size }}>
      <svg viewBox={`0 0 ${size} ${size}`} style={{ width: "100%", height: "100%" }}>
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={track} strokeWidth="6" />
        <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth="6"
                strokeDasharray={`${c * p} ${c}`} strokeLinecap="round"
                transform={`rotate(-90 ${size/2} ${size/2})`} />
      </svg>
      {label && <div style={{ position: "absolute", inset: 0, display: "grid", placeItems: "center", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--text)" }}>{label}</div>}
    </div>
  );
}

// Tiny sparkline
function Spark({ data, color = "var(--primary)", width = 80, height = 22, fill = false }) {
  if (!data.length) return null;
  const lo = Math.min(...data), hi = Math.max(...data);
  const pts = data.map((v, i) => [
    (i / (data.length - 1)) * width,
    height - ((v - lo) / (hi - lo || 1)) * (height - 4) - 2
  ]);
  const d = "M" + pts.map(p => p.map(n => n.toFixed(1)).join(",")).join(" L ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} style={{ width, height }}>
      {fill && <path d={`${d} L ${width},${height} L 0,${height} Z`} fill={color} opacity="0.18" />}
      <path d={d} stroke={color} strokeWidth="1.2" fill="none" />
    </svg>
  );
}

// 3D-ish protein/ligand viewer placeholder (SVG)
function MoleculeView({ height = 260, showLigand = true, label = "BTK · 4ZLZ", caption = "view: cartoon · pocket · ligand 1IJ" }) {
  // Generate a faux backbone using parametric noise
  const pts = React.useMemo(() => {
    const arr = [];
    for (let i = 0; i < 140; i++) {
      const t = i / 140;
      const a = t * Math.PI * 5.5;
      const r = 60 + 18 * Math.sin(a * 0.6 + 1);
      const x = 180 + r * Math.cos(a) + 18 * Math.sin(t * 7);
      const y = 130 + (t * 60 - 30) + 12 * Math.sin(a * 0.9);
      arr.push([x, y, t]);
    }
    return arr;
  }, []);
  const path = "M" + pts.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join(" L ");
  const id = useId();
  return (
    <div style={{ position: "relative", height, background: "radial-gradient(ellipse at 60% 40%, #1a2645 0%, var(--bg-soft) 70%)", borderRadius: "var(--r-md)", overflow: "hidden", border: "1px solid var(--border)" }}>
      <svg viewBox="0 0 360 260" preserveAspectRatio="xMidYMid meet" style={{ width: "100%", height: "100%" }}>
        <defs>
          <linearGradient id={id + "-bb"} x1="0" x2="1">
            <stop offset="0" stopColor="var(--primary)" />
            <stop offset="0.5" stopColor="var(--molecular)" />
            <stop offset="1" stopColor="var(--signal)" />
          </linearGradient>
          <radialGradient id={id + "-pocket"} cx="0.5" cy="0.5" r="0.5">
            <stop offset="0" stopColor="var(--molecular)" stopOpacity="0.45" />
            <stop offset="1" stopColor="var(--molecular)" stopOpacity="0" />
          </radialGradient>
        </defs>
        {/* pocket halo */}
        <circle cx="195" cy="125" r="42" fill={`url(#${id}-pocket)`} />
        {/* backbone */}
        <path d={path} stroke={`url(#${id}-bb)`} strokeWidth="3" fill="none" opacity="0.9" strokeLinecap="round" />
        <path d={path} stroke="white" strokeWidth="0.6" fill="none" opacity="0.25" strokeLinecap="round" />
        {/* residues */}
        {pts.filter((_, i) => i % 8 === 0).map(([x, y], i) => (
          <circle key={i} cx={x} cy={y} r="2" fill="var(--text)" opacity="0.5" />
        ))}
        {/* ligand */}
        {showLigand && (
          <g transform="translate(195 125)">
            <line x1="-6" y1="-4" x2="6" y2="-4" stroke="var(--signal)" strokeWidth="1.4" />
            <line x1="6" y1="-4" x2="10" y2="3" stroke="var(--signal)" strokeWidth="1.4" />
            <line x1="-6" y1="-4" x2="-10" y2="3" stroke="var(--signal)" strokeWidth="1.4" />
            <line x1="-10" y1="3" x2="-4" y2="8" stroke="var(--signal)" strokeWidth="1.4" />
            <line x1="10" y1="3" x2="4" y2="8" stroke="var(--signal)" strokeWidth="1.4" />
            <line x1="-4" y1="8" x2="4" y2="8" stroke="var(--signal)" strokeWidth="1.4" />
            <line x1="0" y1="8" x2="0" y2="14" stroke="var(--signal)" strokeWidth="1.4" />
            {[[-6,-4],[6,-4],[10,3],[-10,3],[-4,8],[4,8],[0,14]].map(([x,y],i) =>
              <circle key={i} cx={x} cy={y} r="2.2" fill="var(--signal)" stroke="var(--bg)" strokeWidth="0.5" />
            )}
          </g>
        )}
      </svg>
      <div style={{ position: "absolute", left: 10, top: 10, display: "flex", gap: 6 }}>
        <Chip tone="molecular" dot>{label}</Chip>
      </div>
      <div style={{ position: "absolute", right: 10, top: 10, display: "flex", gap: 4 }}>
        {["cartoon", "surface", "sticks"].map((m, i) => (
          <button key={m} className="btn sm" style={{ background: i === 0 ? "var(--surface-3)" : "transparent", borderColor: "var(--border)", fontFamily: "var(--font-mono)" }}>{m}</button>
        ))}
      </div>
      <div style={{ position: "absolute", left: 10, bottom: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>{caption}</div>
      <div style={{ position: "absolute", right: 10, bottom: 10, fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)" }}>↻ drag · scroll: zoom</div>
    </div>
  );
}

Object.assign(window, { LineChart, ScatterChart, Histogram, Donut, Spark, MoleculeView });
