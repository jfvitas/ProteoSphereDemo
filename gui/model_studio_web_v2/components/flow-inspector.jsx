// ProteoSphere — Flow Builder inspector (right rail when a node is selected)
//
// Ported from proteosphere/project/flow-v4/inspector.jsx.
// Sections: Role · Implementation · I/O contract · Parameters · Cost · Why pick this?

function NodeInspector({ node, blockDef, implDef, onClose, onImplChange, onParamChange, onDelete, gpuLabel }) {
  const [why, setWhy] = React.useState(true);
  // Default GPU label reads from live PS_LIVE_GPU set by the side rail.
  const gpu = gpuLabel || (window.PS_LIVE_GPU?.device_name || "GPU");

  if (!node || !blockDef || !implDef) return <InspectorEmpty />;

  const inputs  = blockDef.inputs  || [];
  const outputs = blockDef.outputs || [];

  return (
    <div className="flow-inspector">
      <div className="ins-h">
        <span className="cat-badge lg" data-cat={blockDef.cat} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="title">{blockDef.role}</div>
          <div className="sub">{blockDef.cat.toUpperCase()} · {node.id}</div>
        </div>
        <button className="btn ghost sm x" onClick={onClose} aria-label="Close"><Ico name="x" /></button>
      </div>

      <div className="ins-b">
        <div className="section">
          <div className="section-h">Role <span style={{ opacity: 0.6 }}>· fixed when dropped</span></div>
          <div className="role-line">
            <span className="cat-badge" data-cat={blockDef.cat} style={{ width: 18, height: 18 }} />
            <span style={{ flex: 1 }}>{blockDef.role}</span>
            <span className="lock">locked</span>
          </div>
        </div>

        <div className="section">
          <div className="section-h">Implementation <span style={{ opacity: 0.6 }}>· swap freely · same I/O</span></div>
          {blockDef.impls.map(im => (
            <div key={im.id} className="impl-option" data-on={im.id === implDef.id}
              onClick={() => onImplChange && onImplChange(im.id)}
              title={im.planned ? "This implementation is surfaced in the GUI but not yet wired into the trainer — launching with it will return a FlowCompileError." : null}
              style={im.planned ? { opacity: 0.7 } : null}>
              <div className="title">
                <span style={{ width: 12, height: 12, borderRadius: 6, border: "1.4px solid var(--primary)",
                  display: "inline-grid", placeItems: "center", color: "var(--primary)", flexShrink: 0,
                  background: im.id === implDef.id ? "var(--primary)" : "transparent" }}>
                  {im.id === implDef.id && <span style={{ width: 5, height: 5, borderRadius: 3, background: "#021624" }} />}
                </span>
                <span>{im.label}</span>
                {im.planned       && <Chip tone="warn">planned</Chip>}
                {im.cost >= 0.7   && !im.planned && <Chip tone="warn">heavy</Chip>}
                {im.cost === 0    && !im.planned && <Chip tone="dim">identity</Chip>}
                <span style={{ flex: 1 }} />
                {im.paper && im.paper !== "—" && <span className="paper">{im.paper}</span>}
              </div>
              <div className="desc">{im.desc}</div>
              {im.planned && (
                <div style={{ marginTop: 4, fontSize: 10, color: "var(--warn)",
                  fontFamily: "var(--font-mono)", lineHeight: 1.4 }}>
                  Not yet wired to the trainer. Selecting it documents your design intent — backend builder ships in a later stage.
                </div>
              )}
            </div>
          ))}
        </div>

        <div className="section">
          <div className="section-h">I/O contract</div>
          {inputs.map(p => (
            <div key={"i" + p.port} className="contract-line">
              <span className="lbl">in.{p.port} ·</span> accepts {p.types.map(t => (
                <span key={t} style={{ color: window.PS_FLOW_PORT_TYPES[t]?.color, fontWeight: 600 }}>{t}</span>
              )).reduce((a, b) => [a, " | ", b])}
            </div>
          ))}
          {outputs.map(p => (
            <div key={"o" + p.port} className="contract-line">
              <span className="lbl">out.{p.port} ·</span> emits{" "}
              <span style={{ color: window.PS_FLOW_PORT_TYPES[p.type]?.color, fontWeight: 600 }}>{p.type}</span>
              <span style={{ color: "var(--dim)" }}> · {window.PS_FLOW_PORT_TYPES[p.type]?.desc}</span>
            </div>
          ))}
        </div>

        <div className="section">
          <div className="section-h">Parameters</div>
          {(implDef.params || []).length === 0 && (
            <div style={{ fontSize: 12, color: "var(--muted)", fontStyle: "italic" }}>None for this implementation.</div>
          )}
          {(implDef.params || []).map(p => {
            const value = node.params?.[p.key] ?? p.default;
            return (
              <ParamField key={p.key} param={p} value={value}
                explainer={window.flowParamExplainer && window.flowParamExplainer(blockDef, implDef, p.key)}
                onChange={(v) => onParamChange && onParamChange(p.key, v)} />
            );
          })}
        </div>

        <div className="section">
          <div className="section-h">Cost estimate</div>
          <div className="cost-row">
            <Stat k="Params"  v={flowCostEst(implDef).params}  mono />
            <Stat k="Forward" v={flowCostEst(implDef).forward} mono />
            <Stat k="GPU"     v={gpu}                          mono />
          </div>
        </div>

        <div className="section">
          <div className="section-h" style={{ display: "flex" }}>
            Why pick this?
            <span style={{ flex: 1 }} />
            <button className="btn ghost" style={{ padding: "2px 8px", fontSize: 10 }}
              onClick={() => setWhy(w => !w)}>{why ? "Hide" : "Show"}</button>
          </div>
          {why && (
            <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.55, padding: "6px 8px",
              background: "var(--bg-soft)", border: "1px solid var(--border-soft)", borderRadius: "var(--r)" }}>
              {flowWhyExplainer(blockDef, implDef)}
            </div>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, paddingTop: 4, borderTop: "1px solid var(--border)" }}>
          <button className="btn ghost" style={{ color: "var(--error)", fontSize: 12 }} onClick={onDelete}>
            <Ico name="trash" /> Delete node
          </button>
          <span style={{ flex: 1 }} />
          <button className="btn" style={{ fontSize: 12 }} onClick={onClose}>Done</button>
        </div>
      </div>
    </div>
  );
}

function ParamField({ param, value, onChange, explainer }) {
  return (
    <div style={{ marginBottom: 8 }}>
      <label className="label" style={{ display: "flex", alignItems: "center", fontFamily: "var(--font-mono)", fontSize: 10, color: "var(--dim)", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 4 }}>
        <span>{param.key}</span>
        {explainer && window.InfoTip && (
          <window.InfoTip word={param.key} text={explainer} size={10} />
        )}
        <span style={{ flex: 1 }} />
        <span style={{ textTransform: "none", letterSpacing: 0, fontSize: 9, color: "var(--dim)", opacity: 0.7 }}>
          default: {String(param.default)}
        </span>
      </label>
      {param.kind === "enum" ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}
          style={{ fontFamily: "var(--font-mono)", fontSize: 12, padding: "5px 8px",
            background: "var(--surface-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: "var(--r)", width: "100%" }}>
          {param.options.map(o => <option key={o}>{o}</option>)}
        </select>
      ) : param.kind === "bool" ? (
        <div className="toggle">
          <button aria-pressed={value === true}  onClick={() => onChange(true)}>on</button>
          <button aria-pressed={value === false} onClick={() => onChange(false)}>off</button>
        </div>
      ) : (
        <input
          type={param.kind === "int" || param.kind === "float" ? "number" : "text"}
          value={value}
          step={param.kind === "float" ? 0.01 : 1}
          onChange={(e) => {
            const raw = e.target.value;
            const v = param.kind === "int" ? parseInt(raw)
                    : param.kind === "float" ? parseFloat(raw)
                    : raw;
            onChange(Number.isNaN(v) ? raw : v);
          }}
          style={{ fontFamily: "var(--font-mono)", fontSize: 12, padding: "5px 8px",
            background: "var(--surface-2)", color: "var(--text)", border: "1px solid var(--border)", borderRadius: "var(--r)", width: "100%" }}
        />
      )}
    </div>
  );
}

function InspectorEmpty() {
  return (
    <div className="flow-inspector">
      <div className="ins-h">
        <span className="title">Inspector</span>
      </div>
      <div style={{ padding: 24, textAlign: "center", color: "var(--muted)", fontSize: 12, lineHeight: 1.6 }}>
        Click any block on the canvas to view and edit its role, implementation, parameters, and I/O contract.
      </div>
    </div>
  );
}

// Heuristic cost estimator (production reads from a real estimator).
function flowCostEst(impl) {
  const c = impl.cost || 0;
  const params  = c === 0 ? "~ 0"
                : c <= 0.3 ? "~ 0.4 MB"
                : c <= 0.5 ? "~ 2.1 MB"
                : c <= 0.7 ? "~ 18 MB"
                : "~ 110 MB";
  const forward = c === 0 ? "~ 0 ms"
                : c <= 0.3 ? "~ 0.2 ms"
                : c <= 0.5 ? "~ 0.6 ms"
                : c <= 0.7 ? "~ 1.4 ms"
                : "~ 5.1 ms";
  return { params, forward };
}

function flowWhyExplainer(blockDef, implDef) {
  const key = `${blockDef.id}/${implDef.id}`;
  return FLOW_WHY_EXPLAINERS[key] || (implDef.desc + " (No long-form explainer cached yet.)");
}

const FLOW_WHY_EXPLAINERS = {
  // ── Inputs ─────────────────────────────────────────────────────────
  "in.protein_seq/default":
    "Raw amino-acid tokens, one row per residue. The cheapest input — no precompute. Pair with any sequence encoder (CNN-1D, Transformer, ESM-2). The downstream encoder decides how much pretraining bias to inject.",
  "in.protein_graph/default":
    "Residue-level contact graph at ≤ 8 Å. Pulled from PDB or AlphaFold. Required for any GNN-on-protein encoder; carries structural inductive bias you can't get from sequence alone.",
  "in.protein_emb/default":
    "Pre-computed ESM-2 embedding, fetched from the on-disk cache. Zero recompute cost — pays off once you've evaluated more than one architecture per protein. Wire into an Identity protein encoder when you want to skip on-the-fly LM forward passes.",
  "in.ligand_graph/default":
    "RDKit 2-D mol graph: atoms as nodes, bonds as edges, with stereo + aromaticity flags. The right shape for GIN/GCN/GAT on molecules. Doesn't carry 3-D conformer info — wire in Uni-Mol if you need that.",
  "in.ligand_fp/default":
    "ECFP4 2048-bit Morgan fingerprint. Cheap, deterministic, and a strong baseline on classical QSAR tasks. The trade-off: no learned representation; treat as fixed features.",
  "in.contact_map/default":
    "L × L pairwise distance/contact map from a docked pose. Requires a docking pass first (slow, batchwise). Pair with a 2-D conv or TriangleUpdate encoder for AlphaFold-style pair refinement.",

  // ── Protein sequence encoders ─────────────────────────────────────
  "enc.protein_seq/esm2_frozen":
    "ESM-2 has seen ~250M proteins during pretraining. As a frozen feature extractor, it's the cheapest way to inherit that knowledge — no fine-tune cost, just 1280-d per token. Pick this when you have ≤ 100k labelled pairs.",
  "enc.protein_seq/cnn":
    "1-D convolution over AA tokens. Trained from scratch on your data. Cheap, fast, and a known-good baseline (DeepDTA). Picks up local motifs but won't transfer across protein families. Reach for it when you have a tight compute budget.",
  "enc.protein_seq/transformer":
    "Self-attention over AA tokens, trained from scratch. Stronger than CNN-1D on large datasets (≥ 200k pairs) and on tasks that depend on long-range residue context (allostery, distant active-site residues).",
  "enc.protein_seq/protbert":
    "ProtBert is BERT trained on UniRef100. Smaller VRAM than ESM-2 with comparable transfer on most kinase / GPCR tasks. Pick this if you can't fit ESM-2-650M in memory.",
  "enc.protein_seq/prott5":
    "ProtT5-XL: T5 encoder pretrained on BFD. Strong on remote-homology tasks — picks up signals that ESM-2 misses on small protein families. Slightly heavier than ProtBert.",
  "enc.protein_seq/lstm_bi":
    "Bidirectional LSTM. The pre-transformer baseline. Cheaper than Transformer; still captures long-range context (better than CNN). Use when you want a sequential model without attention's quadratic cost.",
  "enc.protein_seq/identity":
    "No transformation — the input is already in embedding space (typically the ESM-2 cached input). Use to keep the wiring uniform: every protein path ends at an embedding port even when no learning happens here.",
  "enc.protein_graph/egnn":
    "E(n)-equivariant GNN. Built so that rotating/translating the input structure rotates/translates the output equivariantly. Worth the extra cost when 3-D coordinates matter (binding-pose-conditional prediction).",
  "enc.protein_graph/schnet":
    "Continuous-filter convolutions parameterised by interatomic distances. The original molecular GNN with distance awareness. Works on residue contact graphs too when you carry the distance as an edge feature.",
  "in.protein_fakesetta/default":
    "19-d Rosetta ref2015-style energy vector computed from a Python surrogate. Stable across platforms; swaps to real PyRosetta automatically when locally licensed + ROSETTA_LICENSE_ACKNOWLEDGED=1.",
  "in.ligand_unimol/default":
    "Conformer-aware embedding from Uni-Mol v2. Needs an ETKDGv3 conformer (cached). Carries 3-D info that 2-D mol graphs lack — sometimes the difference between fitting and overfitting on small DTA sets.",
  "in.ligand_physchem/default":
    "78-d RDKit descriptor vector — logP/TPSA/HBA/HBD/QED/rotatable-bonds and friends. Cheap; correlates with druglikeness rather than binding. Useful as auxiliary input or for a QSAR baseline.",
  "in.iface_pairs/default":
    "Pairs of residues within 5 Å across the interface. Needs a resolved PDB or AF-multimer prediction. The signal that bilinear / cross-attention fusions exploit most directly.",
  "head.classifier/calibrated":
    "Sigmoid + Platt scaling on the validation fold. Calibrates the output so 'p=0.8' actually means '80% binder'. Use when downstream is a screening cut-off rather than a ranking.",
  "head.multiclass/softmax":
    "Softmax + cross-entropy over K classes. Pick when the label is categorical (binder type, MoA, off-target panel). Label smoothing helps on noisy labels.",

  // ── Protein graph encoders ────────────────────────────────────────
  "enc.protein_graph/gcn":
    "Vanilla graph convolutions. The cheap baseline GNN — pick when you want a low-parameter check-of-concept before reaching for GIN. Symmetric Laplacian normalization can over-smooth past ~4 layers.",
  "enc.protein_graph/gin":
    "Graph isomorphism network. Strictly more expressive than GCN, and the best off-the-shelf GNN on most structure-aware tasks. Use when the contact graph carries the signal — kinase ATP pockets, GPCR orthosteric sites.",
  "enc.protein_graph/gat":
    "Graph attention. Learns which neighbours to weight more. Helps when only a few residues drive binding (catalytic triads, hot spots) and the contact graph is dense.",
  "enc.protein_graph/identity":
    "Pass-through. Use only when the upstream block already produced a graph-shaped embedding (rare — usually you want a learned encoder here).",

  // ── Ligand sequence encoders ──────────────────────────────────────
  "enc.ligand_seq/smiles_cnn":
    "1-D conv over SMILES tokens — the ligand-side mirror of DeepDTA's protein encoder. Captures local substructure patterns. Won't notice global topology; pair with a graph encoder for that.",
  "enc.ligand_seq/chemberta":
    "Pretrained SMILES transformer (~10M molecules). Strong transfer when your dataset is small (≤ 50k). Frozen by default — flip the freeze param off to fine-tune at the cost of overfitting risk.",
  "enc.ligand_seq/molformer":
    "Larger SMILES LM with stronger transfer than ChemBERTa. Heavier (≈ 100M params); reach for it when you have a chunky compute budget and ≤ 100k labelled pairs.",
  "enc.ligand_seq/identity":
    "Pass-through. Use when the input is a fingerprint or precomputed embedding.",

  // ── Ligand graph encoders ─────────────────────────────────────────
  "enc.ligand_graph/gin":
    "The default GNN for molecular property tasks. Strong inductive bias on the chemical-graph distribution. Pair with a GIN protein-graph encoder when you want a fully graph-native pipeline.",
  "enc.ligand_graph/gcn":
    "Cheaper baseline. Use when the molecular dataset is small and you want a lightweight encoder before splurging on GIN/GAT.",
  "enc.ligand_graph/gat":
    "Per-edge attention. Helps when only a handful of substructures are predictive (e.g. an isoform-selectivity hinge). Slightly heavier than GIN; only worth it on > 50k samples.",
  "enc.ligand_graph/identity":
    "Pass-through. Use when you've wired in a pre-computed ligand embedding upstream.",

  // ── Tabular encoders ──────────────────────────────────────────────
  "enc.tabular/mlp":
    "2–3 dense layers, ReLU. The neural default for tabular features. Cheap, easy to tune, plays nicely with dropout. Pick this when you want the rest of the pipeline (fusion + head) to do the heavy lifting.",
  "enc.tabular/xgboost":
    "Gradient-boosted trees. Strong out-of-the-box on heterogeneous tabular inputs (fingerprints + descriptors). Doesn't fine-tune end-to-end with the rest of the graph — the booster is fit after neural features are produced.",
  "enc.tabular/catboost":
    "CatBoost handles categorical features without one-hot, which is rare in DTA datasets but matters once you mix in assay metadata (cell line, source lab). Otherwise comparable to XGBoost.",
  "enc.tabular/identity":
    "Pass-through. Use for raw fingerprints when you want the downstream fusion block to do the math.",

  // ── Interaction map encoders ──────────────────────────────────────
  "enc.interaction_map/cnn2d":
    "Plain 2-D CNN over the L × L map. Cheap and effective when the map carries clean pose info — distances, contacts. Doesn't enforce the triangle inequality the way Evoformer does.",
  "enc.interaction_map/triangle":
    "Evoformer-style triangle updates. Refines the pair representation by enforcing distance-geometry consistency. Heavy and slow; the right choice when pose accuracy matters more than throughput (PoseRank, binding-mode discrimination).",
  "enc.interaction_map/identity":
    "Pass-through. Skips refinement.",

  // ── Fusion ─────────────────────────────────────────────────────────
  "fuse/concat_mlp":
    "Concatenate the two embeddings and pass through an MLP. The cheapest fusion option. Loses the inductive bias of an explicit interaction — pick when both encoders are already strong and a small head suffices.",
  "fuse/bilinear":
    "Bilinear interaction with learned attention pooling — the DrugBAN recipe. Strong on protein-ligand tasks; gives you per-residue attention maps you can use for interpretability.",
  "fuse/cross_attn":
    "Symmetric cross-attention. Heavier than bilinear, but the most expressive of the dense fusion options. Pick when you have a structure-aware protein encoder + a graph ligand encoder.",
  "fuse/two_tower_dot":
    "Independent towers, dot product to score. The cheap-at-serve option — embeddings can be precomputed once per protein and reused for every query ligand. Loses dependent interactions but wins at scale.",
  "fuse/joint_mp":
    "Shared message-passing over the union graph (protein residues ∪ ligand atoms). The most expressive option; also the most expensive. Reach for it only when interpretability + per-edge attribution matter and compute budget is generous.",
  "fuse/tabular_xgb":
    "Concatenates the two embeddings and feeds them to XGBoost. Replaces the head — XGBoost is the regression. Use as a strong baseline; sometimes beats the neural head on small datasets.",
  "fuse/weighted_mean":
    "Project every input to a shared dim, softmax-weight, then average. Naturally handles any N ≥ 1 inputs. Static weights (one set per training run) — picks the best mixture but can't adapt per example. Cheap; a great default when you have 3+ heterogeneous featurizers.",
  "fuse/attention_pool":
    "Treats the N inputs as a length-N token sequence and runs self-attention over it. Generalises cross-attention to any number of inputs. Pick when you have ≥ 3 streams (e.g. protein-seq + protein-graph + ligand-graph) and want the model to learn pairwise interactions among all of them.",
  "fuse/gated_sum":
    "Per-input sigmoid gate (input-dependent), projected sum. Unlike weighted_mean's fixed mixture, the gate is computed from each input so the fusion can ignore an unreliable stream on a per-example basis. Reach for it when one of your features has spotty coverage (e.g. structural input for some proteins, sequence-only for others).",
  "fuse/joint_mp":
    "Shared message-passing over the union graph of N inputs. The most expressive option; also the most expensive. Planned — backend builder ships in a later stage.",

  // ── Heads ──────────────────────────────────────────────────────────
  "head.regression/pki":
    "Predicts pKi directly with a single scalar head. Pick this for medicinal-chem workflows where Ki is your endpoint. Huber loss is the default — it's robust to the long-tailed pKi distribution.",
  "head.regression/pkd":
    "Predicts pKd. Use when your benchmark (Davis) reports Kd. MSE is the default; switch to Huber if your dataset has assay outliers.",
  "head.regression/pic50":
    "Predicts pIC50. Note: IC50 depends on assay conditions (substrate concentration, enzyme), so pIC50 models transfer worse across labs than pKi/pKd models.",
  "head.regression/kd":
    "Predicts Kd directly (nM). Enable log_target to learn against log10(Kd) — usually wins because the affinity distribution is heavy-tailed.",
  "head.regression/dg":
    "Predicts the binding free energy ΔG. The thermodynamic readout. Sign convention: more negative = stronger binding.",
  "head.classifier/default":
    "Sigmoid + BCE. The standard binary-interaction head. Watch class balance; switch to focal loss if positives are ≤ 5 %.",
  "head.pose/default":
    "Coordinate MLP — predicts (x, y, z) atom offsets relative to a reference pocket frame. Used in EquiBind-style pipelines. Doesn't enforce SE(3)-equivariance natively; pair with a structure-aware encoder.",
  "head.ranking/infonce":
    "InfoNCE turns the pipeline into a retrieval system. Positives are the true binder; negatives are in-batch alternatives. Use this when downstream is virtual screening rather than absolute-affinity prediction.",

  // ── Diagnostics ────────────────────────────────────────────────────
  "diag.tap/default":
    "Inline diagnostic. Logs a histogram + small activation samples each step so you can sanity-check what's flowing through. No effect on the loss or gradients — pure observability.",
};

// ────────────────────────────────────────────────────────────────────
// PARAM_EXPLAINERS — one-line tooltips for the param keys most users
// hover over. Keyed by `${block_id}/${impl_id}/${param_key}` first, then
// `${impl_id}/${param_key}`, then `${param_key}` as a catch-all.
// ────────────────────────────────────────────────────────────────────
const FLOW_PARAM_EXPLAINERS = {
  // CNN-1D
  "cnn/filters":  "Number of conv filters per layer. 64–256 is the usable range; doubles param count linearly.",
  "cnn/kernel":   "Receptive-field width in tokens. 5–9 typical; bigger picks up longer motifs at the cost of compute.",
  "cnn/layers":   "Depth of the stack. 3 layers is a known-good DeepDTA baseline; > 5 layers rarely helps without residual connections.",
  // Transformer
  "transformer/hidden": "Hidden width per head, then total = hidden × heads. Round to a multiple of 32 for tensor-core perf.",
  "transformer/heads":  "Attention heads. 4–8 typical. More heads ≠ better past ~16 unless the dataset is very large.",
  "transformer/layers": "Encoder depth. 4 layers covers most DTA tasks; > 8 starts to overfit on < 100k pairs.",
  // ESM-2
  "esm2_frozen/checkpoint": "Which ESM-2 weights to load. 650M is the sweet spot for DTA; 35M for sanity checks, 3B only if you have spare compute.",
  "esm2_frozen/freeze":     "Frozen = inference only. Unfreezing fine-tunes ESM-2 to your data (huge VRAM cost; usually worth it only on ≥ 50k pairs).",
  "esm2_frozen/pool":       "How per-token embeddings collapse to one vector. Mean is robust; CLS works if you trained with a CLS objective; attention learns its own pooling weights.",
  "esm2_frozen/use_cache":  "Read from the on-disk ESM-2 cache when available. Turn off only if you've changed the tokenizer or checkpoint mid-experiment.",
  "transformer/dropout":    "Per-layer dropout. 0.1 is a safe default; bump to 0.2 if overfitting.",
  "transformer/max_len":    "Tokens hard-cap. 1024 covers most proteins; tails are clipped. Bump if you have many long sequences.",
  "lstm_bi/dropout":        "Between-layer dropout. 0.2 typical for bi-LSTMs.",
  "lstm_bi/layers":         "Stacked LSTM depth. 2 is enough; 3+ usually overfits on DTA data.",
  "protbert/pool":          "Mean pools every token; CLS uses BERT's classification token.",
  "egnn/hidden":            "Hidden width.",
  "egnn/layers":            "Number of equivariant message-passing rounds.",
  "schnet/hidden":          "Hidden width.",
  "schnet/n_filters":       "Continuous-filter network width.",
  "schnet/n_gaussians":     "Number of Gaussians used to expand interatomic distances.",
  "default/pos_weight":     "Positive-class weight (BCE). > 1 upweights the rare positives — set to (#neg / #pos) when classes are imbalanced.",
  "default/dropout":        "Pre-output dropout. 0.1 is fine for the head.",
  "softmax/num_classes":    "How many classes to predict. Must match the label set.",
  "softmax/label_smoothing": "0.0–0.1 typical. Helps when labels are noisy.",
  "calibrated/loss":        "Initial training loss before Platt scaling is fit.",
  "cnn/dropout":            "Per-layer dropout. 0.1 standard for DeepDTA-style stacks.",
  "cnn/pool":               "How per-position activations are reduced to one vector. Attention learns a position weighting.",
  "gcn/dropout":            "Between-layer dropout.",
  "gcn/pool":               "How node embeddings are reduced to one graph embedding.",
  "gin/pool":               "Sum-pooling is the GIN default; mean for small graphs.",
  "gat/dropout":            "Attention dropout. Useful when graphs are small.",
  // GNN-protein
  "gin/hidden": "Hidden width per GNN layer. 128–256 typical; doubling adds quadratic memory if layers share state.",
  "gin/layers": "Depth of message-passing. 3–5 layers reaches most useful neighbourhoods; deeper risks over-smoothing.",
  "gin/eps_trainable": "Whether the self-loop scaling ε is a learnable scalar. Usually helps marginally; turn off for reproducible baselines.",
  "gcn/hidden": "Hidden width. Cheaper than GIN — 64–128 is fine.",
  "gcn/layers": "Depth. GCN over-smooths past 3–4 layers without residual connections.",
  "gat/heads":  "Attention heads per layer. 4–8 typical. Use head=1 for an attention-augmented GCN.",
  "gat/layers": "Depth. Same caveat as GCN — past 4 layers, expressiveness gains taper.",
  // Tabular / boosters
  "mlp/dropout": "Per-layer dropout probability. 0.1–0.3 typical; only useful when you see val/train gap > 0.05 R².",
  "xgboost/n_estimators": "Number of boosting rounds. 200–1000 typical; cap with early stopping on the val set.",
  "xgboost/max_depth":    "Tree depth. 4–8 typical; deeper trees overfit small DTA datasets fast.",
  "xgboost/lr":           "Learning rate η. 0.05–0.1 balances speed vs accuracy. Smaller needs more rounds.",
  "catboost/iterations":  "Boosting rounds. CatBoost handles overfitting better than XGBoost, so 500–1500 is fine.",
  "catboost/depth":       "Tree depth. CatBoost uses oblivious trees; 6–8 is a sweet spot.",
  // Fusion
  "concat_mlp/hidden":  "Hidden width of the post-concat MLP.",
  "concat_mlp/layers":  "Depth of the post-concat MLP. 1–2 typical.",
  "bilinear/hidden":    "Projection width before bilinear pooling. Round to a power of 2 for fastest matmul.",
  "bilinear/heads":     "Attention heads over the residue × atom interaction grid.",
  "cross_attn/hidden":  "Hidden width.",
  "cross_attn/heads":   "Attention heads.",
  "cross_attn/layers":  "Cross-attention layers.",
  "two_tower_dot/proj_dim":    "Joint embedding dimension. Both towers project to this width before the dot product.",
  "two_tower_dot/temperature": "Softmax temperature for the contrastive loss (only used when paired with InfoNCE head). 0.05–0.1 typical.",
  "joint_mp/hidden": "Hidden width for the joint message-passing graph.",
  "joint_mp/layers": "Number of joint MP rounds. Heavy; 2–4 typical.",
  // Heads
  "pki/loss":    "Loss function. Huber is robust to outliers; MSE is the textbook default; smooth_l1 sits between the two.",
  "pkd/loss":    "Loss function. MSE if your Kd distribution looks Gaussian in log-space; Huber if it's heavy-tailed.",
  "pic50/loss":  "Loss function. Same trade-off as pKi.",
  "kd/log_target":   "Train against log10(Kd) instead of raw nM. Almost always worth keeping on.",
  "default/loss":    "BCE = standard binary log loss. Focal upweights hard positives; pick when positives < 5 %.",
  "infonce/temperature":  "Contrastive temperature. Lower → harder positives. 0.07 is the CLIP default.",
  "infonce/n_negatives":  "Negatives per anchor inside the batch. 16–64 typical; more negatives = sharper retrieval at cost of memory.",
  // Pose
  "default/max_atoms": "Max ligand atoms predicted. Pad/truncate; 64 covers most drug-like molecules.",
  // Interaction map
  "cnn2d/filters":  "Number of 2-D conv filters per layer.",
  "triangle/iters": "Number of triangle-update iterations. 3 matches AlphaFold's Evoformer block depth.",
  // Diagnostic
  "default/log_every_n_steps": "How often to log a histogram + samples. 50 keeps the SSE channel quiet.",
  "default/sample_size":       "How many activation values to log per step.",
};

function flowParamExplainer(blockDef, implDef, paramKey) {
  const k1 = `${blockDef.id}/${implDef.id}/${paramKey}`;
  if (FLOW_PARAM_EXPLAINERS[k1]) return FLOW_PARAM_EXPLAINERS[k1];
  const k2 = `${implDef.id}/${paramKey}`;
  if (FLOW_PARAM_EXPLAINERS[k2]) return FLOW_PARAM_EXPLAINERS[k2];
  return FLOW_PARAM_EXPLAINERS[paramKey] || null;
}

Object.assign(window, {
  NodeInspector, InspectorEmpty, ParamField, flowCostEst,
  FLOW_WHY_EXPLAINERS, FLOW_PARAM_EXPLAINERS, flowParamExplainer,
});
