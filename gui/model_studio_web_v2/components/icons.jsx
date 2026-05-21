// ProteoSphere — icon set. Stroke-based, 14×14 default.
// Usage: <Ico name="dataset" />

const PATHS = {
  home:     "M3 11 8 3l5 8v8H3z M6 19v-5h4v5",
  dataset:  "M2 5c0-1.1 2.7-2 6-2s6 .9 6 2-2.7 2-6 2-6-.9-6-2zM2 5v6c0 1.1 2.7 2 6 2s6-.9 6-2V5M2 11v3c0 1.1 2.7 2 6 2s6-.9 6-2v-3",
  split:    "M2 3v3a3 3 0 0 0 3 3h6a3 3 0 0 1 3 3v3 M2 9h12 M5 6.5 2 9l3 2.5 M11 1.5 14 4l-3 2.5",
  pipeline: "M3 4h4v3H3z M9 8h4v3H9z M3 12h4v3H3z M7 5.5h2v0 M9 9.5h0 M7 13.5h2",
  train:    "M2 14V4l5 3 5-3 2 2v8 M2 14h12 M5 14V8 M9 14V8",
  results:  "M2 13h12 M4 13V8 M7 13V4 M10 13V10 M13 13V6",
  compare:  "M5 2v12 M11 2v12 M5 5h-3v8h3 M11 9h3v4h-3",
  inference:"M8 1v3 M8 12v3 M1 8h3 M12 8h3 M3 3l2 2 M13 3l-2 2 M3 13l2-2 M13 13l-2-2 M8 5a3 3 0 1 1 0 6 3 3 0 0 1 0-6z",
  registry: "M3 2h10v12H3z M5 5h6 M5 8h6 M5 11h4",
  monitor:  "M2 12V4h12v8H2z M5 16h6 M8 12v4",
  search:   "M7 12a5 5 0 1 1 0-10 5 5 0 0 1 0 10z M11 11l4 4",
  cmd:      "M5 5v6h6V5H5z M2 5v0a2 2 0 1 1 2 2v0 M14 5v0a2 2 0 1 0-2 2v0 M2 11v0a2 2 0 1 0 2-2v0 M14 11v0a2 2 0 1 1-2-2v0",
  plus:     "M8 3v10 M3 8h10",
  check:    "M3 8l3 3 7-7",
  warn:     "M8 2 14 13H2L8 2z M8 7v3 M8 12v.5",
  info:     "M8 2a6 6 0 1 0 0 12 6 6 0 0 0 0-12z M8 7v4 M8 5v.5",
  chev:     "M5 6l3 3 3-3",
  "chev-down": "M5 6l3 3 3-3",
  chevR:    "M6 4l4 4-4 4",
  x:        "M4 4l8 8 M12 4l-8 8",
  drag:     "M5 4h0 M5 8h0 M5 12h0 M11 4h0 M11 8h0 M11 12h0",
  more:     "M3 8h0 M8 8h0 M13 8h0",
  download: "M8 3v9 M5 9l3 3 3-3 M3 14h10",
  upload:   "M8 12V3 M5 6l3-3 3 3 M3 14h10",
  filter:   "M2 3h12L9 9v5l-2 1V9L2 3z",
  beaker:   "M6 1v5L2 13a1 1 0 0 0 1 1.5h10a1 1 0 0 0 1-1.5L10 6V1 M5 1h6",
  molecule: "M4 5a2 2 0 1 1 0-1 M12 12a2 2 0 1 1 0-1 M12 5a2 2 0 1 1 0-1 M4 12a2 2 0 1 1 0-1 M5 5l3 3 M11 5L8 8 M5 11l3-3 M11 11L8 8",
  bolt:     "M9 1 3 9h4l-1 6 6-8H8l1-6z",
  play:     "M4 3v10l9-5z",
  pause:    "M5 3h2v10H5z M9 3h2v10H9z",
  stop:     "M4 4h8v8H4z",
  clock:    "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 5v3l2 2",
  star:     "M8 2l1.8 4 4.2.4-3.2 3 1 4.2L8 11.5 4.2 13.6l1-4.2-3.2-3L6.2 6z",
  sparkle:  "M8 2v3 M8 11v3 M2 8h3 M11 8h3 M4 4l2 2 M10 10l2 2 M10 4l2-2 M4 12l2-2",
  link:     "M9 4l1-1a3 3 0 0 1 4 4l-2 2 M7 12l-1 1a3 3 0 0 1-4-4l2-2 M6 10l4-4",
  user:     "M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6z M2 14a6 6 0 0 1 12 0",
  settings: "M8 5.5a2.5 2.5 0 1 1 0 5 2.5 2.5 0 0 1 0-5z M8 1v2 M8 13v2 M1 8h2 M13 8h2 M2.7 2.7l1.4 1.4 M11.9 11.9l1.4 1.4 M2.7 13.3l1.4-1.4 M11.9 4.1l1.4-1.4",
  layers:   "M8 1 1 5l7 4 7-4-7-4z M1 9l7 4 7-4 M1 13l7 4 7-4",
  zap:      "M9 1 3 9h4l-1 6 6-8H8l1-6z",
  flask:    "M6 1v5L2 13a1 1 0 0 0 1 1.5h10a1 1 0 0 0 1-1.5L10 6V1 M5 1h6 M4 10h8",
  helix:    "M3 2c0 4 10 4 10 8 M3 6c0 4 10 4 10 8 M3 4l10 0 M3 12l10 0",
  target:   "M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1z M8 4a4 4 0 1 0 0 8 4 4 0 0 0 0-8z M8 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  fork:     "M5 2v6a3 3 0 0 0 3 3v3 M11 2v3a3 3 0 0 1-3 3 M5 2a1.5 1.5 0 1 0 0-.1 M11 2a1.5 1.5 0 1 0 0-.1 M8 14a1.5 1.5 0 1 0 0-.1",
  arrowR:   "M3 8h10 M9 4l4 4-4 4",
  arrowL:   "M13 8H3 M7 4 3 8l4 4",
  archive:  "M2 4h12v3H2z M3 7v8h10V7 M6 10h4",
  flag:     "M3 14V2 M3 3h9l-2 3 2 3H3",
  branch:   "M5 2v12 M5 6a4 4 0 0 0 4 4v4 M5 4a1.5 1.5 0 1 0 0-.1 M5 14a1.5 1.5 0 1 0 0-.1 M9 12a1.5 1.5 0 1 0 0-.1",
  // v4 additions — Goal / Features / Flow builder
  atom:     "M8 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M3 8c0-4 2-6 5-6s5 2 5 6-2 6-5 6-5-2-5-6z M2 8c0-4 3-5 6-5s6 1 6 5-3 5-6 5-6-1-6-5z",
  ab:       "M2 14V2h3l3 8 3-8h3v12 M5 9h6",
  cluster:  "M5 5a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M11 5a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M8 15a2 2 0 1 0 0-4 2 2 0 0 0 0 4z M5 5l3 8 M11 5L8 13",
  graph:    "M3 4a2 2 0 1 0 0-1 M13 4a2 2 0 1 0 0-1 M3 12a2 2 0 1 0 0-1 M13 12a2 2 0 1 0 0-1 M8 8a2 2 0 1 0 0-1 M5 4l2 3 M11 4L9 7 M5 12l2-3 M11 12L9 9",
  feature:  "M2 4h12 M2 8h8 M2 12h10 M14 6l2 2-2 2",
  flow:     "M3 8a3 3 0 1 0 0-1 M13 8a3 3 0 1 0 0-1 M6 8h4 M11 5l2 2 M11 11l2-2",
  goal:     "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 5a3 3 0 1 0 0 6 3 3 0 0 0 0-6z M8 7v2 M7 8h2",
  trash:    "M3 4h10 M6 4V2h4v2 M5 4l1 10h4l1-10 M7 7v5 M9 7v5",
  chevR:    "M6 4l4 4-4 4",
  chevL:    "M10 4L6 8l4 4",
  preview:  "M2 8s2.5-5 6-5 6 5 6 5-2.5 5-6 5-6-5-6-5z M8 5.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z",
  load:     "M2 3h6l2 2h4v8H2z M5 8h6 M5 10h4",
  // Stand-in icons used by the v4 design comp; not all of these are
  // strictly needed by Stage 3 but we add them so future stages have them.
};

function Ico({ name, size = 14, className = "", style = {} }) {
  const d = PATHS[name];
  if (!d) return null;
  return (
    <svg
      className={`ico ${className}`}
      style={{ width: size, height: size, ...style }}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {d.split(" M").map((seg, i) => (
        <path key={i} d={(i === 0 ? "" : "M") + seg} />
      ))}
    </svg>
  );
}

window.Ico = Ico;
