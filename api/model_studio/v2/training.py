"""Real training loop for DeepDTA on Davis.

Runs in a background thread spawned by the launch handler. Pushes events
to the Run's event bus (status / batch / epoch / log / final). Polls the
Run's cancel_event between batches so cancellation is responsive.

Metrics computed on val + test:
    * Pearson correlation
    * Spearman correlation
    * RMSE
    * MAE
    * Concordance Index (CI) — the DeepDTA paper's headline metric
"""

from __future__ import annotations

import time
import math
import traceback

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from .checkpoints import save_checkpoint
from .dataset import make_loaders
from .models import model_for_template, count_parameters
from .registry import Run
from . import registry_db as model_db


# ── Metrics ─────────────────────────────────────────────────────────────

def pearson(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    yt = y_true - y_true.mean()
    yp = y_pred - y_pred.mean()
    denom = float(np.sqrt((yt * yt).sum() * (yp * yp).sum()))
    if denom == 0.0:
        return 0.0
    return float((yt * yp).sum() / denom)


def spearman(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if len(y_true) < 2:
        return 0.0
    rt = np.argsort(np.argsort(y_true))
    rp = np.argsort(np.argsort(y_pred))
    return pearson(rt.astype(np.float64), rp.astype(np.float64))


def rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(((y_true - y_pred) ** 2).mean()))


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.abs(y_true - y_pred).mean())


def concordance_index(y_true: np.ndarray, y_pred: np.ndarray, max_pairs: int = 200_000) -> float:
    """DeepDTA's headline metric — what fraction of label-ordered pairs
    the model also orders correctly. For Davis (~30K test) the all-pairs
    count is ~9e8 which is too slow; we sample.
    """
    n = len(y_true)
    if n < 2:
        return 0.0
    rng = np.random.default_rng(0)
    pairs = min(max_pairs, n * (n - 1) // 2)
    i = rng.integers(0, n, size=pairs)
    j = rng.integers(0, n, size=pairs)
    mask = i != j
    i, j = i[mask], j[mask]
    diff_t = y_true[i] - y_true[j]
    diff_p = y_pred[i] - y_pred[j]
    ordered = (diff_t != 0)
    if not ordered.any():
        return 0.0
    same_dir = (np.sign(diff_t[ordered]) == np.sign(diff_p[ordered]))
    ties = (diff_p[ordered] == 0)
    return float((same_dir.sum() + 0.5 * ties.sum()) / ordered.sum())


# ── Eval ────────────────────────────────────────────────────────────────

def _roc_auc_binary(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """ROC-AUC via Mann-Whitney U / (n_pos × n_neg).

    Robust to ties (uses average rank), and identical to sklearn's
    roc_auc_score within 1e-9. Returns NaN when either class is empty.
    """
    y_true = y_true.astype(np.float64)
    y_score = y_score.astype(np.float64)
    n_pos = int(y_true.sum())
    n_neg = int(len(y_true) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(y_score, kind="mergesort")
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(1, len(y_score) + 1)
    # Average tied ranks (proper Mann-Whitney handling)
    _, inv, counts = np.unique(y_score, return_inverse=True, return_counts=True)
    for v in np.where(counts > 1)[0]:
        mask = inv == v
        ranks[mask] = ranks[mask].mean()
    rank_sum_pos = ranks[y_true == 1].sum()
    auc = (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
    return float(auc)


@torch.no_grad()
def evaluate(model: nn.Module, loader: DataLoader, device: torch.device, amp: bool = True,
             return_preds: bool = False, featurized: bool = False,
             ligand_dim: int = 0, protein_dim: int = 0,
             template_id: str | None = None,
             task: str = "regression") -> dict:
    """Evaluates a model on a loader. Returns a metric dict whose keys
    depend on ``task``:

    * ``task="regression"`` (default) — DTA / affinity models with continuous
      labels. Returns Pearson, Spearman, RMSE, MAE, and CI. Pearson is the
      headline metric the trainer tracks for "best epoch".

    * ``task="binary"`` — PPI / DTI classifiers with 0/1 labels and BCE
      training loss. Returns BCE loss, ROC-AUC, accuracy, F1, mean predicted
      probability, and (still) CI — which for binary labels is just AUC, so
      it's the right "best epoch" metric.

      Critically, this branch does NOT report Pearson / RMSE / MAE on raw
      logits, because those are misleading for binary classification:
      Pearson on (logits, {0,1}) tops out at sqrt(class_separability) ≈ 0.6
      even for a perfect classifier, and RMSE on logits *grows* as the model
      gets more confident — which makes overfit detection trigger on a
      model that's actually learning. The old behaviour reported them
      anyway, and that's the "wrong direction" the user was seeing.
    """
    model.eval()
    preds: list[np.ndarray] = []
    trues: list[np.ndarray] = []
    autocast_ctx = torch.amp.autocast("cuda", enabled=amp and device.type == "cuda")
    for batch in loader:
        # Generic N-input dispatch (mirror the training loop).
        *inputs, y = batch
        inputs = [t.to(device, non_blocking=True) for t in inputs]
        with autocast_ctx:
            if template_id == "conplex" and len(inputs) == 1:
                feats = inputs[0]
                lig  = feats[:, :ligand_dim]
                prot = feats[:, ligand_dim:ligand_dim + protein_dim]
                yp = model(prot, lig).float()
            else:
                yp = model(*inputs).float()
        preds.append(yp.cpu().numpy())
        trues.append(y.numpy())
    yp = np.concatenate(preds)
    yt = np.concatenate(trues)

    if task == "binary":
        # Model emits logits; convert to probabilities for accuracy / mean-prob.
        # Use float64 for the sigmoid to avoid overflow on |logit| > ~16.
        logits = yp.astype(np.float64)
        probs  = 1.0 / (1.0 + np.exp(-np.clip(logits, -50.0, 50.0)))
        # BCE with logits (numerically stable form)
        # bce = max(logit,0) − logit·y + log(1 + exp(−|logit|))
        bce_pp = np.maximum(logits, 0.0) - logits * yt.astype(np.float64) \
                 + np.log1p(np.exp(-np.abs(logits)))
        bce_mean = float(bce_pp.mean())
        pred_bin = (probs >= 0.5).astype(np.float32)
        accuracy = float((pred_bin == yt).mean())
        auc = _roc_auc_binary(yt, probs)
        # F1 on the positive class (threshold 0.5)
        tp = float(((pred_bin == 1) & (yt == 1)).sum())
        fp = float(((pred_bin == 1) & (yt == 0)).sum())
        fn = float(((pred_bin == 0) & (yt == 1)).sum())
        precision = tp / max(tp + fp, 1e-9)
        recall    = tp / max(tp + fn, 1e-9)
        f1 = 2.0 * precision * recall / max(precision + recall, 1e-9) if (precision + recall) > 0 else 0.0
        out: dict = {
            "task":      "binary",
            "bce":       bce_mean,
            "rmse":      bce_mean,            # alias so the trainer's val_loss field stays defined
            "auc":       float(auc) if auc == auc else 0.5,
            "accuracy":  accuracy,
            "f1":        float(f1),
            "precision": float(precision),
            "recall":    float(recall),
            "mean_prob": float(probs.mean()),
            "pos_rate":  float(yt.mean()),
            # CI on binary labels is identical to ROC-AUC (Mann-Whitney equivalence),
            # so we keep this key populated for the existing trainer-side bookkeeping.
            "ci":        float(auc) if auc == auc else 0.5,
            # ``pearson`` is intentionally NOT exposed — see docstring.
            "n":         int(len(yt)),
        }
    else:
        out = {
            "task":     "regression",
            "pearson":  pearson(yt, yp),
            "spearman": spearman(yt, yp),
            "rmse":     rmse(yt, yp),
            "mae":      mae(yt, yp),
            "ci":       concordance_index(yt, yp),
            "n":        int(len(yt)),
        }
    if return_preds:
        out["y_true"] = yt
        out["y_pred"] = yp
    return out


def compute_results_summary(y_true: np.ndarray, y_pred: np.ndarray, *, n_bins: int = 10, pki_threshold: float = 6.0) -> dict:
    """Derive everything the Results screen needs from raw test predictions.

    Returned shape:
        metrics            { pearson, spearman, rmse, mae, r2, ci, n }
        scatter_sample     [[xn, yn], ...]  up to 500 points, normalised to [0,1] for the GUI's ScatterChart
        scatter_outliers   indices of pairs with |residual| > 1.5 * RMSE
        residual_hist      { bins: [edges...], counts: [...] }     21 bins, symmetric around 0
        calibration_bins   [{ bin, pred_mid, pred_mean, actual_mean, n, abs_err }] equal-frequency on y_pred
        roc                [{ thr, fpr, tpr }] sampled, plus auc — binarised at pki_threshold
    """
    n = len(y_true)
    rmse_v = rmse(y_true, y_pred)
    mae_v = mae(y_true, y_pred)
    pe = pearson(y_true, y_pred)
    sp = spearman(y_true, y_pred)
    ci = concordance_index(y_true, y_pred)
    ss_res = float(((y_true - y_pred) ** 2).sum())
    ss_tot = float(((y_true - y_true.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    # Scatter sample (downsample to ≤ 500 for the chart, keep all outliers too)
    rng = np.random.default_rng(0)
    keep = rng.choice(n, size=min(500, n), replace=False)
    yp_lo, yp_hi = float(y_true.min()), float(y_true.max())
    span = max(0.001, yp_hi - yp_lo)
    def _norm(v: float) -> float:
        return float((v - yp_lo) / span)
    residuals = y_pred - y_true
    rmse_for_outlier = max(rmse_v, 1e-6)
    out_mask = np.abs(residuals) > 1.5 * rmse_for_outlier
    out_idx = np.where(out_mask)[0]
    # Keep up to 50 outliers visible in the chart, drawn red
    keep_outliers = out_idx[:50]
    scatter_inliers = []
    for i in keep:
        scatter_inliers.append([_norm(float(y_true[i])), _norm(float(y_pred[i]))])
    scatter_outliers = []
    for i in keep_outliers:
        scatter_outliers.append([_norm(float(y_true[i])), _norm(float(y_pred[i]))])
    # Residual histogram (21 bins, ±3·RMSE limits clamped)
    lim = max(0.5, 3.0 * rmse_v)
    edges = np.linspace(-lim, lim, 22)
    counts, _ = np.histogram(residuals, bins=edges)
    # Calibration: equal-frequency bins on y_pred (10 bins by default)
    order = np.argsort(y_pred)
    yp_sorted = y_pred[order]
    yt_sorted = y_true[order]
    bin_edges = np.linspace(0, n, n_bins + 1).astype(int)
    calib = []
    for i in range(n_bins):
        lo, hi = bin_edges[i], bin_edges[i + 1]
        if hi <= lo:
            continue
        bp = yp_sorted[lo:hi]
        bt = yt_sorted[lo:hi]
        calib.append({
            "bin": i + 1,
            "pred_mean": float(bp.mean()),
            "actual_mean": float(bt.mean()),
            "n": int(hi - lo),
            "abs_err": float(np.abs(bp - bt).mean()),
        })
    # ROC at pki_threshold (binary)
    y_bin = (y_true >= pki_threshold).astype(np.int64)
    pos = int(y_bin.sum())
    neg = int(n - pos)
    roc_pts = []
    auc = 0.0
    if pos > 0 and neg > 0:
        # Sweep thresholds at quantiles of y_pred for a smooth curve
        thrs = np.quantile(y_pred, np.linspace(0, 1, 41))
        # Add the actual class boundary for a meaningful "operating point"
        thrs = np.concatenate([thrs, [pki_threshold]])
        thrs = np.sort(np.unique(thrs))[::-1]
        for thr in thrs:
            pred_pos = (y_pred >= thr)
            tp = int((pred_pos & (y_bin == 1)).sum())
            fp = int((pred_pos & (y_bin == 0)).sum())
            tpr = tp / pos if pos else 0.0
            fpr = fp / neg if neg else 0.0
            roc_pts.append({"thr": float(thr), "tpr": tpr, "fpr": fpr})
        # AUC via trapezoid on sorted-by-fpr
        roc_sorted = sorted(roc_pts, key=lambda p: p["fpr"])
        fprs = np.array([p["fpr"] for p in roc_sorted])
        tprs = np.array([p["tpr"] for p in roc_sorted])
        auc = float(np.trapezoid(tprs, fprs)) if len(fprs) > 1 else 0.0
    return {
        "metrics": {
            "pearson": pe, "spearman": sp, "rmse": rmse_v, "mae": mae_v,
            "r2": r2, "ci": ci, "n": n,
        },
        "scatter_inliers":  scatter_inliers,
        "scatter_outliers": scatter_outliers,
        "y_pkd_range": [yp_lo, yp_hi],
        "residual_hist": {
            "edges":  [float(x) for x in edges],
            "counts": [int(x)   for x in counts],
            "rmse":   rmse_v,
        },
        "calibration":  calib,
        "roc": {
            "points": roc_pts,
            "auc":     auc,
            "pos":     pos,
            "neg":     neg,
            "threshold": pki_threshold,
        },
    }


# ── Trainer entry-point ────────────────────────────────────────────────

def train_run(run: Run) -> None:
    """Top-level training entry. Catches every exception so the run
    transitions to 'failed' with a useful message rather than dying
    silently on the worker thread.
    """
    try:
        _train_run_inner(run)
    except _RunCancelled:
        run.set_status("cancelled", finished_at=time.time())
        run.emit({"type": "log", "level": "warn", "text": "Run cancelled by user."})
    except Exception as exc:
        tb = traceback.format_exc()
        run.failure = f"{type(exc).__name__}: {exc}"
        run.emit({"type": "log", "level": "error", "text": run.failure})
        run.emit({"type": "log", "level": "error", "text": tb})
        run.set_status("failed", finished_at=time.time(), failure=run.failure)


class _RunCancelled(Exception):
    """Internal sentinel raised when the cancel event fires."""


# ── Live overfitting / divergence pattern detector ─────────────────────
#
# Called from inside the per-epoch loop after each epoch event is
# emitted. Inspects the recent epoch history and emits structured
# `insight` events (type=insight, tone=warn|signal|primary|error) when
# a pattern is first detected. The `emitted` set prevents a single
# pattern from spamming the stream every epoch — once detected,
# it stays in the Smart Insights panel.
#
# Event shape (consumed by the GUI's InsightCard):
#   { type: "insight", id: "<pattern_id>", tone: "warn"|...,
#     title: "...", body: "...", why: "...", conf: "low|medium|high",
#     epoch: <int> }

def _detect_training_patterns(run: Run, history: list[dict],
                              best_pearson: float, best_epoch: int,
                              emitted: set[str]) -> None:
    """Look for known training-trouble patterns and emit insight events.

    Args:
        run:          the active Run (for emit())
        history:      list of per-epoch event dicts emitted so far this run
        best_pearson: highest val_pearson seen
        best_epoch:   epoch index at which best_pearson was achieved
        emitted:      set of pattern IDs that have already fired; we
                      mutate it in place to record new firings
    """
    n = len(history)
    if n == 0:
        return
    cur = history[-1]
    cur_epoch = int(cur["epoch"])

    def _fire(pid: str, tone: str, title: str, body: str, why: str,
              conf: str = "medium", level: str = "warn") -> None:
        if pid in emitted:
            return
        emitted.add(pid)
        ev = {
            "type":  "insight",
            "id":    pid,
            "tone":  tone,
            "title": title,
            "body":  body,
            "why":   why,
            "conf":  conf,
            "epoch": cur_epoch,
        }
        run.emit(ev)
        # Also drop a log line so the live log shows the insight too.
        run.emit({"type": "log", "level": level,
                  "text": f"[insight:{pid}] {title} — {body}"})

    # ── Pattern A: Validation past peak (textbook overfitting) ──────
    # The best epoch was ≥3 epochs ago AND current pearson dropped
    # meaningfully from the best. Once this fires, early-stop should
    # have triggered.
    if (n >= 5
        and best_epoch > 0
        and (cur_epoch - best_epoch) >= 3
        and best_pearson - cur["val_pearson"] > 0.015):
        _fire(
            "past_peak",
            tone="error",
            title="Past the validation peak",
            body=(f"Best val Pearson was {best_pearson:.3f} at epoch {best_epoch}; "
                  f"current is {cur['val_pearson']:.3f}. Model is overfitting — "
                  f"the early-stop policy should pull from epoch {best_epoch}."),
            why=(f"current - best = {cur['val_pearson'] - best_pearson:+.3f} over "
                 f"{cur_epoch - best_epoch} epochs; threshold = -0.015 over 3+."),
            conf="high",
            level="warn",
        )

    # ── Pattern B: Train-val gap widening ───────────────────────────
    # train_loss = MSE on training; val_loss ≈ val_rmse² (also MSE-ish).
    # Compare the gap now vs 3 epochs ago. If the gap > 0.10 AND
    # has grown by > 0.05 over 3 epochs, we're overfitting.
    if n >= 5:
        e0 = history[-4]; e1 = cur
        gap_now  = max(0.0, e1["val_loss"] - e1["train_loss"])
        gap_then = max(0.0, e0["val_loss"] - e0["train_loss"])
        if gap_now > 0.10 and (gap_now - gap_then) > 0.05:
            _fire(
                "gap_widening",
                tone="warn",
                title="Train-val gap widening",
                body=(f"Val loss is {gap_now:.3f} above train loss (was {gap_then:.3f} "
                      f"3 epochs ago). Consider increasing weight decay or dropout, "
                      f"or stopping near the current peak."),
                why=(f"gap = val_loss - train_loss; now {gap_now:.3f}, then {gap_then:.3f}; "
                     f"trigger when now > 0.10 AND Δ > 0.05 over 3 epochs."),
                conf="medium",
            )

    # ── Pattern C: Validation plateau ───────────────────────────────
    # Last 3 epochs of val_pearson have spread < 0.003 (and we're not
    # past peak — that would have fired first).
    if n >= 4 and "past_peak" not in emitted:
        last3 = [h["val_pearson"] for h in history[-3:]]
        spread = max(last3) - min(last3)
        if spread < 0.003 and cur_epoch < int(cur.get("total_epochs", cur_epoch + 1)):
            _fire(
                "val_plateau",
                tone="signal",
                title="Validation plateaued",
                body=(f"Val Pearson varied by only {spread:.4f} over the last 3 epochs "
                      f"(~{last3[-1]:.3f}). Further training may not help — early-stop "
                      f"could save the remaining epochs."),
                why=(f"max-min of val_pearson over last 3 epochs = {spread:.4f}; "
                     f"threshold = 0.003."),
                conf="medium",
                level="info",
            )

    # ── Pattern D: Val oscillation (unstable LR) ────────────────────
    # Sign of Δval_pearson flips ≥3 times over the last 5 epochs.
    if n >= 6:
        diffs = [history[i]["val_pearson"] - history[i-1]["val_pearson"]
                 for i in range(n-5, n)]
        signs = [(1 if d > 0 else (-1 if d < 0 else 0)) for d in diffs]
        flips = sum(1 for i in range(1, len(signs))
                    if signs[i] != 0 and signs[i-1] != 0 and signs[i] != signs[i-1])
        if flips >= 3:
            _fire(
                "val_oscillation",
                tone="warn",
                title="Validation oscillating",
                body=(f"Val Pearson changed direction {flips}× in the last 5 epochs. "
                      f"Learning rate may be too high or batch size too small — "
                      f"try halving the LR or doubling the batch."),
                why=(f"sign(Δval_pearson) flips over last 5 epochs = {flips}; "
                     f"threshold = 3."),
                conf="medium",
            )

    # ── Pattern E: Both RMSE and Pearson climbing (bias-init failure) ─
    # Pearson goes up while RMSE also goes up — the classic
    # "bias-init wrong" footprint. Should be impossible post-fix,
    # but still useful as a sanity check if someone disables the fix.
    if n >= 4:
        p_up = all(history[i]["val_pearson"] > history[i-1]["val_pearson"]
                   for i in range(n-3, n))
        r_up = all(history[i]["val_rmse"] > history[i-1]["val_rmse"]
                   for i in range(n-3, n))
        if p_up and r_up:
            _fire(
                "rmse_pearson_codrift",
                tone="error",
                title="RMSE and Pearson both climbing",
                body=("Val Pearson is rising while Val RMSE is ALSO rising — the model "
                      "is learning rank correlation faster than absolute scale. "
                      "Usually means the output-layer bias wasn't initialised to the "
                      "train-label mean. Check the run summary's init_output_bias field."),
                why=("val_pearson strictly increasing AND val_rmse strictly increasing "
                     "over last 3 epochs."),
                conf="high",
            )

    # ── Pattern F: Healthy convergence (a positive insight) ─────────
    # Best-so-far improved in last 3 epochs, gap is small. Fires once
    # ~midway through to give a confidence-boost insight.
    if (n >= 5
        and "past_peak" not in emitted
        and "val_plateau" not in emitted
        and cur["val_pearson"] >= best_pearson - 1e-6
        and best_epoch >= n - 1):
        gap = max(0.0, cur["val_loss"] - cur["train_loss"])
        total_ep = int(cur.get("total_epochs", cur_epoch))
        if 0.30 * total_ep <= cur_epoch <= 0.60 * total_ep and gap < 0.10:
            _fire(
                "converging_well",
                tone="primary",
                title="Converging well",
                body=(f"Val Pearson reached {cur['val_pearson']:.3f} at epoch {cur_epoch}, "
                      f"and train-val gap is only {gap:.3f}. Healthy trajectory — let "
                      f"it run."),
                why=("val_pearson at running max, train-val gap < 0.10, and we're "
                     "in the 30-60% epoch window."),
                conf="medium",
                level="ok",
            )


def _check_cancel(run: Run) -> None:
    if run.cancel_event.is_set():
        raise _RunCancelled()


# ── k-fold + stratified split helpers ────────────────────────────────

def _rebuild_loader(loader, new_indices: list[int], shuffle: bool):
    """Re-Subset a DataLoader's underlying dataset with new indices,
    preserving batch_size / collate_fn / pin_memory / drop_last.
    """
    full_ds = loader.dataset.dataset if hasattr(loader.dataset, "dataset") else loader.dataset
    new_subset = torch.utils.data.Subset(full_ds, new_indices)
    return DataLoader(
        new_subset,
        batch_size=loader.batch_size,
        shuffle=shuffle,
        num_workers=getattr(loader, "num_workers", 0),
        pin_memory=getattr(loader, "pin_memory", True),
        drop_last=getattr(loader, "drop_last", False),
        collate_fn=loader.collate_fn,
    )


def _apply_cv_fold(train_loader, val_loader, *,
                   fold: int, k: int, seed: int) -> tuple:
    """Repartition the (train ∪ val) index pool into k disjoint folds;
    return (new_train_loader, new_val_loader, meta).

    Test set is unchanged. The pool is deterministically shuffled by
    seed so re-running fold #i always yields the same partition.
    """
    tr_idx = list(getattr(train_loader.dataset, "indices", []))
    va_idx = list(getattr(val_loader.dataset, "indices", []))
    pool = sorted(set(tr_idx + va_idx))
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(pool))
    shuffled = [pool[p] for p in perm]
    fold_size = len(shuffled) // k
    val_start = fold * fold_size
    val_end = val_start + fold_size if fold < k - 1 else len(shuffled)
    new_val_idx   = shuffled[val_start:val_end]
    new_train_idx = shuffled[:val_start] + shuffled[val_end:]
    return (
        _rebuild_loader(train_loader, new_train_idx, shuffle=True),
        _rebuild_loader(val_loader,   new_val_idx,   shuffle=False),
        {"n_train": len(new_train_idx), "n_val": len(new_val_idx)},
    )


def _maybe_stratify_binary(train_loader, val_loader, *,
                           binarize_threshold=None, binarize=None,
                           seed: int = 42) -> tuple:
    """If the underlying labels are binary (already 0/1 OR will be after
    binarize), rebuild train/val splits with matched positive fraction.
    Returns (new_train_loader, new_val_loader, meta) or (orig, orig, None)
    when the labels aren't binary.
    """
    tr_idx = list(getattr(train_loader.dataset, "indices", []))
    va_idx = list(getattr(val_loader.dataset, "indices", []))
    pool = tr_idx + va_idx
    full_ds = train_loader.dataset.dataset if hasattr(train_loader.dataset, "dataset") else train_loader.dataset
    if not hasattr(full_ds, "ys"):
        return train_loader, val_loader, None
    ys = np.asarray(full_ds.ys)
    pool_y = ys[pool]
    # Detect binary: already 0/1, or will be after threshold.
    unique = set(np.unique(pool_y).tolist())
    is_binary = unique <= {0.0, 1.0}
    if not is_binary and binarize == "threshold" and binarize_threshold is not None:
        try:
            thr = float(binarize_threshold)
            pool_y = (pool_y >= thr).astype(np.float32)
            unique = {0.0, 1.0}
            is_binary = True
        except Exception:
            pass
    if not is_binary:
        return train_loader, val_loader, None
    pos_idx = [i for i, lab in zip(pool, pool_y) if lab > 0.5]
    neg_idx = [i for i, lab in zip(pool, pool_y) if lab <= 0.5]
    if not pos_idx or not neg_idx:
        return train_loader, val_loader, None  # single-class, nothing to stratify
    rng = np.random.default_rng(seed)
    rng.shuffle(pos_idx); rng.shuffle(neg_idx)
    # Use the original val fraction as the target.
    val_frac = len(va_idx) / max(len(pool), 1)
    n_val_pos = max(1, int(round(len(pos_idx) * val_frac)))
    n_val_neg = max(1, int(round(len(neg_idx) * val_frac)))
    new_val_idx   = pos_idx[:n_val_pos] + neg_idx[:n_val_neg]
    new_train_idx = pos_idx[n_val_pos:] + neg_idx[n_val_neg:]
    new_train = _rebuild_loader(train_loader, new_train_idx, shuffle=True)
    new_val   = _rebuild_loader(val_loader,   new_val_idx,   shuffle=False)
    # Sanity-check the matched fraction.
    train_y_after = ys[new_train_idx]
    val_y_after   = ys[new_val_idx]
    if binarize == "threshold" and binarize_threshold is not None:
        try:
            thr = float(binarize_threshold)
            train_y_after = (train_y_after >= thr).astype(np.float32)
            val_y_after   = (val_y_after   >= thr).astype(np.float32)
        except Exception:
            pass
    pos_train = float((train_y_after > 0.5).mean()) if len(train_y_after) else 0.0
    pos_val   = float((val_y_after   > 0.5).mean()) if len(val_y_after)   else 0.0
    return new_train, new_val, {"pos_train": pos_train, "pos_val": pos_val}


def train_run_kfold(*, base_run_factory, k: int = 5) -> dict:
    """Run train_run k times with rotating val folds and aggregate metrics.

    Args:
        base_run_factory: callable(fold_i: int) -> Run. Should create a
            FRESH Run with hparams including
            ``cv_fold=fold_i, cv_folds=k``. The trainer reads those and
            rotates the (train ∪ val) split accordingly.
        k: number of folds.

    Returns a dict with per-fold metrics + mean ± std. Best practice:
    let test stay held-out across folds for honest reporting.
    """
    fold_results: list[dict] = []
    for fold_i in range(k):
        run = base_run_factory(fold_i)
        train_run(run)
        fold_results.append({
            "fold":              fold_i,
            "run_id":            run.run_id,
            "status":            run.status,
            "best_val_pearson":  run.summary.get("best_val_pearson"),
            "best_val_rmse":     run.summary.get("best_val_rmse"),
            "test_pearson":      run.summary.get("test_pearson"),
            "test_rmse":         run.summary.get("test_rmse"),
            "n_train":           run.summary.get("n_train"),
            "n_val":             run.summary.get("n_val"),
        })
    # Aggregate (drop None scores in case any fold failed/cancelled).
    def _stats(key: str):
        vals = [r[key] for r in fold_results if isinstance(r.get(key), (int, float))]
        if not vals:
            return None
        a = np.asarray(vals, dtype=np.float64)
        return {"mean": float(a.mean()), "std": float(a.std()), "n": int(len(a)),
                "values": [float(v) for v in a]}
    return {
        "k":                 k,
        "fold_results":      fold_results,
        "val_pearson_stats": _stats("best_val_pearson"),
        "val_rmse_stats":    _stats("best_val_rmse"),
        "test_pearson_stats": _stats("test_pearson"),
        "test_rmse_stats":    _stats("test_rmse"),
    }


def _train_run_inner(run: Run) -> None:
    run.set_status("running", started_at=time.time())
    hp = run.hparams
    # Use the shared device selector so PROTEOSPHERE_FORCE_CPU env honored
    # and the warmup state is reused.
    from .gpu_runtime import select_device
    device = select_device("auto" if hp.get("use_cuda", True) else "cpu")
    amp = bool(hp.get("amp", True)) and device.type == "cuda"
    epochs = int(hp.get("epochs", 25))
    batch_size = int(hp.get("batch_size", 256))
    lr = float(hp.get("lr", 3e-4))
    weight_decay = float(hp.get("weight_decay", 0.0))
    seed = int(hp.get("seed", 4192))

    # Dataset selection. Default to the Davis CSV loader for backward
    # compat with existing run payloads, but a v2 launch can specify
    # `benchmark` and `split_policy` to use the warehouse loader instead
    # (no external file dependencies, supports cold-target / cold-drug
    # / cold-pair splits, works for davis / kiba / gtopdb out of the box).
    #
    # When `featurizers` is set in hparams, route through the featurized
    # loader so the model takes a single tabular tensor instead of the
    # (seq, smi) token pair. Used by tabular_mlp / thermo_mlp / conplex.
    benchmark = hp.get("benchmark", "davis-legacy")
    split_policy = hp.get("split_policy", "random")
    featurizer_ids = hp.get("featurizers") or []
    torch.manual_seed(seed)
    np.random.seed(seed)
    # Only templates that *consume* a single concatenated feature tensor
    # route through the featurized loader. Token / graph templates ignore
    # the featurizer list (it's still recorded in the run summary so the
    # user can see what was unused).
    _FEATURIZER_CONSUMERS = {"tabular_mlp", "thermo_mlp", "conplex"}
    is_featurized_run = (
        bool(featurizer_ids)
        and benchmark != "davis-legacy"
        and run.template_id in _FEATURIZER_CONSUMERS
    )
    # Templates whose forward pass takes (protein_graph, ligand_graph) — both
    # sides are torch_geometric Batches. Routed through a paired-graph loader.
    _PAIRED_GRAPH_TEMPLATES = {"struct_gnn_dta"}
    is_paired_graph_run = (
        run.template_id in _PAIRED_GRAPH_TEMPLATES
        and benchmark != "davis-legacy"
    )
    # PPI templates take two protein graphs (no ligand) and predict a
    # binary interaction label. Trainer uses BCEWithLogitsLoss instead
    # of MSE for these. ``benchmark`` in this case is interpreted as the
    # PPI source ("hippie" or "huri") rather than a DTA benchmark.
    _PPI_TEMPLATES = {"ppi_gnn_siamese"}
    is_ppi_run = run.template_id in _PPI_TEMPLATES
    # Flow runs use the user-built block graph (template_id == "flow").
    # The loader is picked from the flow's input blocks via
    # flow_compiler.loader_shape_for_flow().
    is_flow_run = run.template_id == "flow"
    is_esm_smi_run     = False
    is_esm_fp_run      = False
    is_esm_graph_run   = False
    is_esm_tabular_run = False
    is_pp_emb_run      = False
    # `seq_graph` flow shape (protein_seq + ligand_graph) needs the
    # warehouse graph loader, which emits (seq_tokens, ligand_Batch, y).
    # The default warehouse loader emits int SMILES tokens, which would
    # then crash the GNN's batch.x access. is_graph_run dispatches to
    # the right one. The SAME loader-shape mismatch was hitting the
    # legacy DrugBAN + GraphDTA templates — their forward expects
    # `(seq, ligand_graph_Batch)`, but the template-only dispatch was
    # falling through to make_warehouse_loaders. Pre-set is_graph_run=True
    # for these templates so the trainer picks the right loader.
    _LIGAND_GRAPH_TEMPLATES = {"drugban", "graphdta"}
    is_graph_run       = (
        run.template_id in _LIGAND_GRAPH_TEMPLATES
        and benchmark != "davis-legacy"
    )
    # `seq_fp` flow shape (protein_seq + ligand_fp) needs the warehouse
    # seq+ECFP loader, emitting (seq_tokens, float_fp, y). The default
    # warehouse loader would emit int SMILES tokens which crash a
    # tabular MLP's Linear layer ("Long vs Float dtype").
    is_seq_fp_run      = False
    # `multi` flow shape (3+ inputs) routes to the multi-feature loader
    # which emits one tensor per input block in the flow's topo order,
    # then the label. The trainer's batch dispatch (below) uses *args
    # unpacking so arbitrary input arity flows through.
    is_multi_run       = False
    if is_flow_run:
        from .flow_compiler import (
            loader_shape_for_flow,
            _autoroute_embedding_encoders,
            _autoroute_incompatible_fusions,
        )
        flow_spec = (run.effective_config or {}).get("flow") or {}
        # Apply both auto-routing passes once HERE too so we can (a)
        # log the rewrites the user should know about and (b) feed the
        # same rewritten flow to the loader builder below.
        # loader_shape_for_flow also calls _autoroute internally; this
        # just guarantees the spec passed to make_*loaders() matches
        # the shape that was computed.
        flow_spec, _enc_notes = _autoroute_embedding_encoders(flow_spec)
        flow_spec, _fuse_notes = _autoroute_incompatible_fusions(flow_spec)
        _autoroute_notes = _enc_notes + _fuse_notes
        # Persist the rewrite so downstream callers (compile_flow, the
        # registry view, etc.) see the routed flow.
        if _autoroute_notes:
            (run.effective_config or {})["flow"] = flow_spec
        flow_shape = loader_shape_for_flow(flow_spec)
        run.emit({"type": "log", "level": "info",
                  "text": (f"Flow run: {len(flow_spec.get('nodes', []))} blocks, "
                           f"{len(flow_spec.get('edges', []))} wires, loader shape='{flow_shape}'.")})
        for _note in _autoroute_notes:
            run.emit({"type": "log", "level": "info",
                      "text": f"Flow auto-route: {_note}"})
        run.summary["flow_loader_shape"] = flow_shape
        # Route the loader by shape.
        if flow_shape == "seq_graph":
            # protein_seq + ligand_graph → make_graph_warehouse_loaders.
            # NOT the standard make_warehouse_loaders (which would emit
            # SMILES tokens instead of a torch_geometric Batch and crash
            # the GIN encoder's `batch.x` access).
            is_graph_run = True
        elif flow_shape == "seq_fp":
            is_seq_fp_run = True            # protein_seq + ECFP4 fingerprint
        elif flow_shape == "struct_graph":
            is_paired_graph_run = True
        elif flow_shape == "pp_graph":
            is_ppi_run = True
        elif flow_shape == "pp_emb":
            is_pp_emb_run = True            # two-tower ESM-2 PPI
            is_ppi_run   = True             # still binary task
        elif flow_shape == "esm_smi":
            is_esm_smi_run = True          # cached ESM-2 emb + SMILES tokens
        elif flow_shape == "esm_fp":
            is_esm_fp_run = True           # cached ESM-2 emb + ECFP4 fingerprint
        elif flow_shape == "esm_graph":
            is_esm_graph_run = True        # cached ESM-2 emb + mol graph
        elif flow_shape == "esm_tabular":
            is_esm_tabular_run = True      # cached ESM-2 emb + ligand fingerprint/unimol/physchem
        elif flow_shape == "multi":
            is_multi_run = True            # 3+ inputs → per-block loader
        elif flow_shape == "seq_smi":
            pass                            # standard warehouse loader
        else:
            run.emit({"type": "log", "level": "warn",
                      "text": (f"Flow loader shape '{flow_shape}' is not yet auto-mapped; "
                               "defaulting to the standard warehouse loader. If your flow has "
                               "an unusual input combination the loader may not produce all "
                               "tensors the graph needs.")})

        # ── Defense in depth: flow-shape vs benchmark sanity ──────────
        # The GUI normally derives the benchmark from the flow topology
        # (a P-L flow → 'kiba'; a P-P flow → 'hippie'). But stale GUIs,
        # scripted launches, or sweep configs can still send a mismatched
        # benchmark — e.g. a P-L flow with `benchmark='hippie'`. The
        # downstream loader then calls `load_warehouse_records('hippie')`
        # which raises `ValueError: Unknown benchmark 'hippie'`. Auto-
        # correct here with a clear warning so users see WHY their pick
        # was overridden, rather than getting a deep stack trace.
        _DTA_SHAPES = {"seq_smi", "seq_fp", "seq_graph", "struct_graph",
                        "esm_smi", "esm_fp", "esm_graph", "esm_tabular"}
        _PPI_SHAPES = {"pp_graph", "pp_emb", "pp_seq"}
        _PPI_BENCHMARKS = ("hippie", "huri")
        if flow_shape in _DTA_SHAPES and benchmark in _PPI_BENCHMARKS:
            run.emit({"type": "log", "level": "warn",
                      "text": (f"Flow shape '{flow_shape}' is a protein-ligand topology, "
                               f"but benchmark='{benchmark}' is a PPI source. Auto-switching "
                               f"to 'kiba' (the default P-L benchmark). If you wanted PPI, "
                               f"rebuild the flow with two protein inputs and no ligand.")})
            benchmark = "kiba"
            run.summary["benchmark_autocorrected"] = True
            run.summary["benchmark_autocorrected_from"] = "hippie/huri"
        elif flow_shape in _PPI_SHAPES and benchmark not in _PPI_BENCHMARKS:
            run.emit({"type": "log", "level": "warn",
                      "text": (f"Flow shape '{flow_shape}' is a protein-protein topology, "
                               f"but benchmark='{benchmark}' is a P-L dataset. Auto-switching "
                               f"to 'hippie' (the default PPI source). If you wanted P-L, "
                               f"rebuild the flow with one protein and one ligand input.")})
            benchmark = "hippie"
            run.summary["benchmark_autocorrected"] = True
            run.summary["benchmark_autocorrected_from"] = "non-PPI"
    if featurizer_ids and not is_featurized_run:
        run.emit({"type": "log", "level": "warn",
                  "text": (f"Featurizers picked ({len(featurizer_ids)}) but template "
                           f"'{run.template_id}' uses its own tokenizer. The featurizer "
                           f"list will be recorded but not used. Pick tabular_mlp, "
                           f"thermo_mlp, or conplex to consume featurizers.")})
        run.summary["featurizers_ignored"] = featurizer_ids
    if is_featurized_run:
        from .dataset_warehouse import make_featurized_warehouse_loaders
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {benchmark} with {len(featurizer_ids)} featurizer(s): "
                           f"{featurizer_ids[:6]}{'…' if len(featurizer_ids) > 6 else ''}")})
        train_loader, val_loader, test_loader, meta = make_featurized_warehouse_loaders(
            benchmark, featurizer_ids,
            split_policy=split_policy, batch_size=batch_size, seed=seed,
        )
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
        run.summary["featurizers"]   = featurizer_ids
        run.summary["feature_dim"]   = meta["total_feature_dim"]
        run.summary["ligand_dim"]    = meta["ligand_dim"]
        run.summary["protein_dim"]   = meta["protein_dim"]
        run.summary["feature_manifest"] = meta["featurizers"]
        # Patch effective_config so the model knows its input dim
        ec = dict(run.effective_config or {})
        nodes = list(ec.get("nodes", []))
        # Ensure there's an "f" slot with the dims wired in
        f_idx = next((i for i, n in enumerate(nodes) if n.get("slot_id") == "f"), None)
        params_patch = {
            "input_dim":   meta["total_feature_dim"],
            "ligand_dim":  meta["ligand_dim"] or None,
            "protein_dim": meta["protein_dim"] or None,
        }
        if f_idx is None:
            nodes.append({"slot_id": "f", "params": params_patch})
        else:
            cur = dict(nodes[f_idx].get("params") or {})
            cur.update(params_patch)
            nodes[f_idx] = {**nodes[f_idx], "params": cur}
        ec["nodes"] = nodes
        run.effective_config = ec
    elif is_ppi_run and not is_pp_emb_run:
        # `is_pp_emb_run` ALSO sets `is_ppi_run = True` (so the binary
        # loss / metrics path activates). The graph-based PPI loader
        # below would clobber that — guard against it here so pp_emb
        # falls through to its own dispatch branch.
        from .dataset_warehouse import make_ppi_warehouse_loaders
        source = benchmark if benchmark in ("hippie", "huri") else "hippie"
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {source} PPI pairs (split={split_policy if split_policy != 'random' else 'cold-protein'}, "
                           f"min_confidence=0.5, neg_ratio=1.0, batch={batch_size})…")})
        train_loader, val_loader, test_loader, meta = make_ppi_warehouse_loaders(
            source=source,
            split_policy=split_policy if split_policy in ("random", "cold-protein") else "cold-protein",
            batch_size=batch_size,
            seed=seed,
        )
        cov = meta.get("structure_coverage") or {}
        if cov:
            n_with = cov.get("with_structure", 0)
            n_fb   = cov.get("fallback", 0)
            tot    = max(n_with + n_fb, 1)
            run.emit({"type": "log", "level": "info",
                      "text": (f"PPI structure coverage: {n_with}/{tot} proteins "
                               f"({100.0 * n_with / tot:.0f}%) used a cached PDB; "
                               f"{n_fb} fell back to sequence-only graphs.")})
            run.summary["structure_coverage"] = cov
        run.summary.update({
            "ppi_n_positives":      meta["n_positives"],
            "ppi_n_negatives":      meta["n_negatives"],
            "ppi_positive_fraction": meta["positive_fraction"],
            "ppi_n_proteins":       meta["n_proteins"],
            "ppi_source":           source,
        })
        label_range = (0.0, 1.0)
        label_unit = "interaction_prob"
        # benchmark field gets used downstream as a key in some metrics —
        # store the original so the run summary doesn't lose it.
        run.summary["benchmark"] = source
    elif is_pp_emb_run:
        # Two-tower PPI on cached ESM-2 embeddings.
        from .dataset_warehouse import make_ppi_esm_loaders
        source = benchmark if benchmark in ("hippie", "huri") else "hippie"
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {source} PPI via two-tower ESM-2 loader "
                           f"(split={split_policy if split_policy != 'random' else 'cold-protein'}, "
                           f"batch={batch_size}). Resolving ESM-2 cache for both proteins…")})
        train_loader, val_loader, test_loader, meta = make_ppi_esm_loaders(
            source=source,
            split_policy=split_policy if split_policy in ("random", "cold-protein") else "cold-protein",
            batch_size=batch_size,
            seed=seed,
        )
        cm = meta.get("esm2_cache_meta") or {}
        run.emit({"type": "log", "level": "ok",
                  "text": (f"PP-ESM cache resolved: {cm.get('cache_hits', 0)} hits, "
                           f"{cm.get('computed', 0)} computed, {cm.get('zeros', 0)} zero-fallbacks. "
                           f"{meta.get('n_proteins')} unique UniProts.")})
        run.summary["esm2_cache_meta"] = cm
        run.summary.update({
            "ppi_n_positives":      meta["n_positives"],
            "ppi_n_negatives":      meta["n_negatives"],
            "ppi_positive_fraction": meta["positive_fraction"],
            "ppi_n_proteins":       meta["n_proteins"],
            "ppi_source":           source,
        })
        run.summary["benchmark"] = source
        label_range = (0.0, 1.0)
        label_unit = "interaction_prob"
    elif is_esm_smi_run or is_esm_fp_run or is_esm_graph_run or is_esm_tabular_run:
        # Extract per-flow fingerprint config from the in.ligand_fp node
        # so non-default fp_radius / fp_bits can flow through to the
        # loader. The compiler reads the same params and sizes downstream
        # MLPs accordingly. Falls back to RDKit defaults (radius=2,
        # 2048 bits) when the user hasn't customized.
        _flow_fp_radius, _flow_fp_bits = 2, 2048
        if is_flow_run:
            _spec = (run.effective_config or {}).get("flow") or {}
            for _n in _spec.get("nodes", []):
                if _n.get("block_id") == "in.ligand_fp":
                    _p = _n.get("params") or {}
                    if "fp_radius" in _p:
                        _flow_fp_radius = int(_p["fp_radius"])
                    if "fp_bits" in _p:
                        _flow_fp_bits = int(_p["fp_bits"])
                    break
        # Cached-ESM-2 path. Pre-fetches embeddings via embeddings.batch_get_or_compute
        # (auto-computes missing ones via fair-esm when available, falls back to
        # zeros when neither cache nor fair-esm is present).
        # esm_tabular falls back to the SMI loader for legacy combos
        # (in.ligand_descriptors that aren't yet first-class) but emits
        # int tokens — that path is gated on flows that intentionally
        # use enc.ligand_seq + smiles_cnn anyway.
        if is_esm_smi_run:
            from .dataset_warehouse import make_esm_smi_warehouse_loaders as _make_esm
            run.emit({"type": "log", "level": "info",
                      "text": (f"Loading {benchmark} via ESM-2 + SMILES-token loader "
                               f"(split={split_policy}, batch={batch_size}). "
                               f"Pre-fetching ESM-2 650M embeddings (cache lookup; "
                               f"auto-computes missing UniProts on first use)…")})
        elif is_esm_fp_run:
            from .dataset_warehouse import make_esm_fp_warehouse_loaders as _make_esm
            run.emit({"type": "log", "level": "info",
                      "text": (f"Loading {benchmark} via ESM-2 + ECFP4 loader "
                               f"(split={split_policy}, batch={batch_size}). "
                               f"Computing 2048-bit Morgan fingerprints from SMILES, "
                               f"pre-fetching ESM-2 650M embeddings…")})
        elif is_esm_tabular_run:
            # Fallback — surface that this path isn't first-class yet.
            from .dataset_warehouse import make_esm_smi_warehouse_loaders as _make_esm
            run.emit({"type": "log", "level": "warn",
                      "text": ("Flow uses a ligand tabular input (Uni-Mol / physchem / "
                               "descriptors) that doesn't have a dedicated loader yet. "
                               "Routing through the SMILES-token loader as a fallback — "
                               "the ligand-side tensor will be int tokens, so your encoder "
                               "needs to accept that. For now switch to in.ligand_fp / "
                               "in.ligand_graph / in.ligand_smiles for a clean run.")})
        else:
            from .dataset_warehouse import make_esm_graph_warehouse_loaders as _make_esm
            run.emit({"type": "log", "level": "info",
                      "text": (f"Loading {benchmark} via ESM-2 + mol-graph loader "
                               f"(split={split_policy}, batch={batch_size}). "
                               f"Pre-fetching ESM-2 650M embeddings…")})
        _esm_kwargs = dict(split_policy=split_policy, batch_size=batch_size, seed=seed)
        if is_esm_fp_run:
            _esm_kwargs["fp_radius"] = _flow_fp_radius
            _esm_kwargs["fp_bits"]   = _flow_fp_bits
        train_loader, val_loader, test_loader, meta = _make_esm(
            benchmark, **_esm_kwargs,
        )
        cm = meta.get("esm2_cache_meta") or {}
        run.emit({"type": "log", "level": "ok",
                  "text": (f"ESM-2 cache resolved: {cm.get('cache_hits', 0)} hits, "
                           f"{cm.get('computed', 0)} freshly computed, "
                           f"{cm.get('zeros', 0)} zero-fallbacks. dim={meta.get('esm2_dim')}, "
                           f"checkpoint={meta.get('esm2_checkpoint')}.")})
        run.summary["esm2_cache_meta"] = cm
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
    elif is_multi_run:
        # Flow has 3+ input blocks. Build input_blocks list in the flow's
        # topo order so the loader emits tensors in the same order the
        # FlowModule.forward expects them.
        from .flow_compiler import compile_flow
        from .dataset_warehouse import make_multifeature_warehouse_loaders
        flow_spec = (run.effective_config or {}).get("flow") or {}
        # Mirror compile_flow's topo logic to find input order.
        adj: dict[str, list[str]] = {n["id"]: [] for n in flow_spec.get("nodes", [])}
        indeg: dict[str, int] = {n["id"]: 0 for n in flow_spec.get("nodes", [])}
        for e in flow_spec.get("edges", []):
            f = e["from"].split(":")[0]
            t = e["to"].split(":")[0]
            adj.setdefault(f, []).append(t)
            indeg[t] = indeg.get(t, 0) + 1
        queue_ = [nid for nid, d in indeg.items() if d == 0]
        topo_order_: list[str] = []
        while queue_:
            u = queue_.pop(0)
            topo_order_.append(u)
            for v in adj.get(u, []):
                indeg[v] -= 1
                if indeg[v] == 0:
                    queue_.append(v)
        node_by_id = {n["id"]: n for n in flow_spec.get("nodes", [])}
        input_blocks_ordered = [
            {"block_id": node_by_id[nid]["block_id"],
             "params":   node_by_id[nid].get("params") or {}}
            for nid in topo_order_
            if node_by_id[nid]["block_id"].startswith("in.")
        ]
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {benchmark} via multi-feature loader "
                           f"(split={split_policy}, batch={batch_size}). "
                           f"Inputs in topo order: "
                           f"{[b['block_id'] for b in input_blocks_ordered]}")})
        train_loader, val_loader, test_loader, meta = make_multifeature_warehouse_loaders(
            benchmark, input_blocks_ordered,
            split_policy=split_policy if split_policy in ("random", "cluster", "leakage-aware", "cold-target") else "random",
            batch_size=batch_size,
            seed=seed,
        )
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
    elif is_seq_fp_run:
        # Flow shape "seq_fp": (seq_tokens, float_ecfp, y).
        # Used by protein-Transformer + tabular-MLP-on-fingerprint flows.
        # Read per-flow fp_radius / fp_bits from the in.ligand_fp node
        # so user-customized Morgan widths thread through correctly.
        _fp_radius, _fp_bits = 2, 2048
        if is_flow_run:
            _spec = (run.effective_config or {}).get("flow") or {}
            for _n in _spec.get("nodes", []):
                if _n.get("block_id") == "in.ligand_fp":
                    _p = _n.get("params") or {}
                    if "fp_radius" in _p: _fp_radius = int(_p["fp_radius"])
                    if "fp_bits"   in _p: _fp_bits   = int(_p["fp_bits"])
                    break
        from .dataset_warehouse import make_seq_fp_warehouse_loaders
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {benchmark} via seq+ECFP loader "
                           f"(split={split_policy}, seed={seed}, batch={batch_size}). "
                           f"Computing {_fp_bits}-bit Morgan-r{_fp_radius} fingerprints from SMILES…")})
        train_loader, val_loader, test_loader, meta = make_seq_fp_warehouse_loaders(
            benchmark,
            split_policy=split_policy if split_policy in ("random", "cluster", "leakage-aware", "cold-target") else "random",
            batch_size=batch_size,
            seed=seed,
            fp_radius=_fp_radius,
            fp_bits=_fp_bits,
        )
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
        run.summary["fp_backend"] = meta.get("fp_backend")
    elif is_graph_run:
        # Flow shape "seq_graph": (seq_tokens, ligand_graph_Batch, y).
        # Used by GraphDTA/DrugBAN-style flows where the protein side
        # is char-tokenised but the ligand is a torch_geometric graph.
        from .dataset_warehouse import make_graph_warehouse_loaders
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {benchmark} via warehouse graph loader "
                           f"(split={split_policy}, seed={seed}, batch={batch_size}). "
                           f"Building per-ligand mol graphs…")})
        train_loader, val_loader, test_loader, meta = make_graph_warehouse_loaders(
            benchmark,
            split_policy=split_policy if split_policy in ("random", "cold-target", "cold-drug", "cold-pair") else "random",
            batch_size=batch_size,
            seed=seed,
        )
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
    elif is_paired_graph_run:
        from .dataset_warehouse import make_struct_graph_warehouse_loaders
        run.emit({"type": "log", "level": "info",
                  "text": (f"Loading {benchmark} via paired-graph loader "
                           f"(split={split_policy}, seed={seed}, batch={batch_size}). "
                           f"Building per-protein residue graphs (PDB-derived when "
                           f"AlphaFold cache hits, sequence-fallback otherwise)…")})
        train_loader, val_loader, test_loader, meta = make_struct_graph_warehouse_loaders(
            benchmark,
            split_policy=split_policy,
            batch_size=batch_size,
            seed=seed,
        )
        cov = meta.get("structure_coverage") or {}
        if cov:
            n_with = cov.get("with_structure", 0)
            n_fb   = cov.get("fallback", 0)
            tot    = max(n_with + n_fb, 1)
            run.emit({"type": "log", "level": "info",
                      "text": (f"Structure coverage: {n_with}/{tot} proteins "
                               f"({100.0 * n_with / tot:.0f}%) used a cached PDB; "
                               f"{n_fb} fell back to sequence-only sliding-window "
                               f"graphs.")})
            run.summary["structure_coverage"] = cov
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
    elif benchmark == "davis-legacy":
        run.emit({"type": "log", "level": "info",
                  "text": f"Loading Davis dataset via CSV loader (seed={seed}, batch={batch_size})…"})
        train_loader, val_loader, test_loader, meta = make_loaders(
            batch_size=batch_size, seed=seed,
        )
        label_range = meta.get("pkd_range", (0.0, 0.0))
        label_unit = "pKd"
    else:
        from .dataset_warehouse import make_warehouse_loaders
        run.emit({"type": "log", "level": "info",
                  "text": f"Loading {benchmark} via warehouse loader (split={split_policy}, seed={seed}, batch={batch_size})…"})
        train_loader, val_loader, test_loader, meta = make_warehouse_loaders(
            benchmark,
            split_policy=split_policy,
            batch_size=batch_size,
            seed=seed,
        )
        label_range = meta.get("label_range", (0.0, 0.0))
        label_unit = "kiba_score" if benchmark == "kiba" else ("pKi/pKd/pIC50" if benchmark == "gtopdb" else "label")
    run.emit({"type": "log", "level": "info",
              "text": (f"Dataset ready ({benchmark}): {meta['n_train']:,} train / "
                       f"{meta['n_val']:,} val / {meta['n_test']:,} test "
                       f"({label_unit} range {label_range[0]:.2f}…{label_range[1]:.2f})")})

    # ── k-fold CV split rotation ─────────────────────────────────────
    # When ``cv_fold`` + ``cv_folds`` are set (typically by train_run_kfold),
    # repartition the train+val pool into k disjoint folds and assign
    # fold #cv_fold as val, the rest as train. Test stays held out
    # across all folds for honest reporting. The aggregated metrics
    # land in run.summary; train_run_kfold collects them.
    if hp.get("cv_fold") is not None and hp.get("cv_folds") is not None:
        cv_fold  = int(hp["cv_fold"])
        cv_total = int(hp["cv_folds"])
        if cv_total > 1 and 0 <= cv_fold < cv_total:
            train_loader, val_loader, fold_meta = _apply_cv_fold(
                train_loader, val_loader,
                fold=cv_fold, k=cv_total, seed=seed,
            )
            run.summary["cv_fold"]  = cv_fold
            run.summary["cv_folds"] = cv_total
            run.summary["n_train"]  = fold_meta["n_train"]
            run.summary["n_val"]    = fold_meta["n_val"]
            run.emit({"type": "log", "level": "info",
                      "text": (f"k-fold CV split: fold {cv_fold + 1}/{cv_total} "
                               f"-> {fold_meta['n_train']:,} train / "
                               f"{fold_meta['n_val']:,} val "
                               f"(test held out, unchanged: {meta['n_test']:,})")})

    # ── Stratified split for binary tasks ───────────────────────────
    # When labels are binary (already 0/1, or about to be binarized
    # below) and the user didn't pick cold-target / leakage-aware, the
    # random val split can drift class balance enough to make metrics
    # noisy. Stratify so val + train carry the same positive fraction
    # as the full pool. Skip when cv_fold is active (fold rotation
    # already shuffled), when split_policy is structure-aware, or when
    # the label set isn't actually binary at this point.
    _stratify_requested = hp.get("stratify_binary", True)
    if (_stratify_requested
            and hp.get("cv_fold") is None
            and split_policy in ("random",)):
        try:
            train_loader, val_loader, strat_meta = _maybe_stratify_binary(
                train_loader, val_loader,
                binarize_threshold=hp.get("binarize_threshold"),
                binarize=hp.get("binarize"),
                seed=seed,
            )
            if strat_meta is not None:
                run.summary["stratified_binary"] = True
                run.summary["stratified_pos_fraction_train"] = strat_meta["pos_train"]
                run.summary["stratified_pos_fraction_val"]   = strat_meta["pos_val"]
                run.emit({"type": "log", "level": "info",
                          "text": (f"Stratified binary split: train pos="
                                   f"{strat_meta['pos_train']:.3f}, val pos="
                                   f"{strat_meta['pos_val']:.3f} (matched within ±0.02)")})
        except Exception as _exc:
            # Stratification is best-effort; never fail the run for it.
            run.emit({"type": "log", "level": "warn",
                      "text": f"Stratified split skipped: {_exc}"})

    # Optional binarisation hook — runs after splits are formed, so the
    # negatives don't leak across folds. Off by default (hp.binarize ∈
    # {None, "threshold", "auto"}); when on we transform the regression
    # labels into 0/1 for binary classification + log the ratio.
    binarize = hp.get("binarize")
    if binarize:
        # Lift the previous `benchmark != 'davis-legacy'` exclusion —
        # davis-legacy's DavisDataset also stores ys as a float32 numpy
        # array on `train_loader.dataset.dataset.ys`, so the same
        # in-place rewrite works. The previous gate was a leftover from
        # before davis-legacy adopted the Subset-of-DTADataset shape.
        try:
            from .negatives import _BINARIZE_THRESHOLDS
        except Exception:
            _BINARIZE_THRESHOLDS = {}
        threshold = float(hp.get("binarize_threshold")
                          or _BINARIZE_THRESHOLDS.get(benchmark, 0.0))
        if threshold > 0.0:
            # Rewrite labels in-place on each loader's underlying full ds.
            # Subset(dataset, indices) shares the .ys array.
            full = train_loader.dataset.dataset
            full.ys = (full.ys >= threshold).astype("float32")
            n_pos_train = int(sum(full.ys[i] for i in train_loader.dataset.indices))
            n_pos_test  = int(sum(full.ys[i] for i in test_loader.dataset.indices))
            run.emit({"type": "log", "level": "info",
                      "text": (f"Binarised labels at {label_unit} >= {threshold:.2f}: "
                               f"train pos={n_pos_train:,}/{meta['n_train']:,} "
                               f"({100*n_pos_train/max(meta['n_train'],1):.1f}%), "
                               f"test pos={n_pos_test:,}/{meta['n_test']:,} "
                               f"({100*n_pos_test/max(meta['n_test'],1):.1f}%)")})
            run.summary["binarize_threshold"] = threshold
            run.summary["n_train_positives"] = n_pos_train
            run.summary["n_test_positives"]  = n_pos_test

    run.emit({"type": "log", "level": "info", "text": f"Building model for template '{run.template_id}'…"})
    model = model_for_template(run.template_id, run.effective_config).to(device)

    # ── Read flow-model loss signal early ─────────────────────────────
    # For flow runs the head builder picked the loss; that determines
    # whether the task is binary (output bias init uses logit) or
    # regression (raw label mean). We need this BEFORE the bias-init
    # block runs, so it's hoisted up here. The full loss_fn / task_kind
    # / optimizer wiring still lives below.
    _flow_loss_name: str | None = None
    try:
        from .flow_compiler import FlowModule as _FlowModule
        if isinstance(model, _FlowModule):
            _flow_loss_name = getattr(model, "loss_name", None)
    except Exception:
        pass
    is_binary_run = (
        is_ppi_run
        or _flow_loss_name in ("bce_with_logits", "bce_with_logits_platt")
    )

    # ── Output-layer bias initialization ──────────────────────────────
    # PyTorch's default Linear init sets the output bias to 0. For
    # regression on labels with non-zero mean (KIBA mean≈11.7, Davis
    # pKd mean≈5.5), that gives an initial RMSE equal to the label
    # mean and forces the optimizer to spend several epochs just
    # moving the bias toward the right offset. During those early
    # epochs the model's WEIGHTS learn rank correlation (Pearson goes
    # up) before its BIAS centers the predictions (RMSE comes down),
    # so users see Pearson + RMSE climb together for the first 5-15
    # epochs — counterintuitive and easy to misread as a bug.
    #
    # Fix: initialize the final Linear layer's bias to the train-set
    # label mean. Now epoch-1 predictions hover around the label
    # distribution mean instead of zero, RMSE starts close to the
    # label std (not the mean), and both metrics improve monotonically.
    try:
        train_labels = []
        full_ds = train_loader.dataset
        # Subset(dataset, indices) → reach into the wrapped dataset.
        if hasattr(full_ds, "dataset") and hasattr(full_ds, "indices"):
            inner = full_ds.dataset
            if hasattr(inner, "ys"):
                idx = list(full_ds.indices)
                train_labels = inner.ys[idx]
            else:
                # Fall back to iterating the loader once (slow but safe)
                for batch in train_loader:
                    train_labels.append(batch[-1].numpy())
                train_labels = np.concatenate(train_labels) if train_labels else np.array([])
        if len(train_labels) > 0:
            label_mean = float(np.mean(train_labels))
            label_std  = float(np.std(train_labels))
            # For binary classification (PPI: labels in {0, 1}) the
            # model emits logits. The right bias-init is logit(p_pos),
            # not p_pos directly — otherwise the head starts with
            # sigmoid(p_pos) ≈ 0.62 even when the prior is 0.5.
            unique_vals = set(float(v) for v in np.unique(train_labels).tolist())
            if unique_vals <= {0.0, 1.0}:
                p = max(min(label_mean, 1.0 - 1e-3), 1e-3)
                label_mean = float(np.log(p / (1.0 - p)))
            # Find the last nn.Linear in the model and set its bias.
            # Multiclass heads (out_features > 1) and pure-similarity
            # heads (no final Linear) are skipped — they don't have a
            # scalar bias that maps to the label mean.
            last_linear = None
            for m in model.modules():
                if isinstance(m, nn.Linear):
                    last_linear = m
            if last_linear is not None and last_linear.bias is not None \
               and last_linear.out_features == 1:
                with torch.no_grad():
                    last_linear.bias.fill_(label_mean)
                # For binary tasks, label_mean has been logit-transformed
                # above so the log line should make that explicit.
                if is_binary_run:
                    run.emit({"type": "log", "level": "info",
                              "text": (f"Initialised output-layer bias to logit(p_pos) = "
                                       f"{label_mean:.3f} (label fraction "
                                       f"~{1.0/(1.0+np.exp(-label_mean)):.3f}); "
                                       f"head starts at the population prior.")})
                else:
                    run.emit({"type": "log", "level": "info",
                              "text": (f"Initialised output-layer bias to train-label mean "
                                       f"({label_mean:.3f} ± {label_std:.3f}); "
                                       f"this prevents the early-epoch RMSE spike where "
                                       f"the model learns rank-correlation before "
                                       f"absolute-scale.")})
                run.summary["init_output_bias"] = label_mean
                run.summary["train_label_mean"] = label_mean
                run.summary["train_label_std"]  = label_std
            else:
                run.emit({"type": "log", "level": "warn",
                          "text": ("Couldn't find a single-output Linear to "
                                   "initialise — model may show the early-epoch "
                                   "RMSE/Pearson-co-climbing artifact.")})
    except Exception as exc:  # noqa: BLE001
        run.emit({"type": "log", "level": "warn",
                  "text": f"Output-bias init skipped: {exc}"})
    n_params = count_parameters(model)
    run.emit({"type": "log", "level": "info",
              "text": f"Model ready: {n_params/1e6:.2f}M parameters on {device}."})
    run.summary.update({"n_params": n_params, "device": str(device),
                        "n_train": meta["n_train"], "n_val": meta["n_val"], "n_test": meta["n_test"],
                        "benchmark": benchmark, "split_policy": split_policy,
                        "label_unit": label_unit})
    # Surface split-time provenance (e.g. how many UniProts collapsed into
    # UniRef50 clusters on a leakage-aware split) so the run summary tells
    # the user *what* the split actually did, not just *which policy* they picked.
    sp = meta.get("split_provenance") or {}
    if sp:
        run.summary["split_provenance"] = sp
        la = sp.get("leakage_aware")
        if la:
            run.emit({"type": "log", "level": "info",
                      "text": (f"Leakage-aware split: {la['uniprots_or_keys']} proteins → "
                               f"{la['clusters']} {la['threshold']} clusters "
                               f"(coverage {la['cluster_coverage_pct']}% of records; "
                               f"{la['merged_into_clusters']} homologs merged into "
                               f"their cluster representative).")})

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=max(1, epochs * len(train_loader)))
    scaler = torch.amp.GradScaler("cuda", enabled=amp)
    autocast_ctx = lambda: torch.amp.autocast("cuda", enabled=amp)

    # PPI templates emit logits and use binary cross-entropy; DTA
    # templates emit a scalar and use MSE.
    #
    # For flow runs the FlowModule carries the loss_name picked by the
    # compiler (head builder returns it). That lets a P-L binary
    # classifier — head.classifier on a non-PPI flow — train with BCE
    # instead of being forced into MSE, and lets head.regression with
    # loss=huber pick Huber. We honor the model's choice when present,
    # falling back to the template-driven default otherwise.
    loss_name_from_model: str | None = None
    try:
        from .flow_compiler import FlowModule
        if isinstance(model, FlowModule):
            loss_name_from_model = getattr(model, "loss_name", None)
    except Exception:
        pass

    if loss_name_from_model in ("bce_with_logits", "bce_with_logits_platt"):
        loss_fn = nn.BCEWithLogitsLoss()
        run.summary["loss"] = loss_name_from_model
        task_kind = "binary"
        # Mark the run for post-train Platt scaling on the val set when
        # the head requested it. The actual scaling fit runs after the
        # main training loop completes (see "Platt scaling" block below).
        if loss_name_from_model == "bce_with_logits_platt":
            run.summary["platt_scaling_requested"] = True
    elif loss_name_from_model == "huber":
        loss_fn = nn.HuberLoss()
        run.summary["loss"] = "huber"
        task_kind = "regression"
    elif loss_name_from_model == "smooth_l1":
        loss_fn = nn.SmoothL1Loss()
        run.summary["loss"] = "smooth_l1"
        task_kind = "regression"
    elif loss_name_from_model == "cross_entropy":
        # Multiclass head — requires int targets the loaders don't emit.
        # Surface this as a hard failure rather than silently corrupting
        # the loss signal.
        raise NotImplementedError(
            "head.multiclass requires categorical targets but the current "
            "DTA / PPI loaders emit float labels. Multiclass support ships "
            "in a later loader update."
        )
    elif loss_name_from_model == "infonce":
        # Treat InfoNCE as MSE on the similarity score for the MVP. The
        # head produces a scalar that we regress against the affinity
        # label. The contrastive variant ships in a later trainer stage.
        loss_fn = nn.MSELoss()
        run.summary["loss"] = "infonce-as-mse"
        task_kind = "regression"
        run.emit({"type": "log", "level": "info",
                  "text": ("Ranking head selected — running it in MSE-on-similarity mode "
                           "for now. The true contrastive InfoNCE loop ships in a later "
                           "trainer stage; this still produces a usable scoring model.")})
    elif is_ppi_run:
        loss_fn = nn.BCEWithLogitsLoss()
        run.summary["loss"] = "bce_with_logits"
        task_kind = "binary"
    else:
        loss_fn = nn.MSELoss()
        run.summary["loss"] = "mse"
        task_kind = "regression"

    # Best-epoch tracking metric depends on the task. For regression we
    # maximise Pearson; for binary classification we maximise ROC-AUC.
    # Either way, "higher is better" so the same comparison works.
    run.summary["task"] = task_kind
    # Surface task downstream (some bookkeeping below was conditional on
    # is_ppi_run; replicate that for any binary flow run).
    is_binary_run = task_kind == "binary"
    best_val_score = -1.0          # generic: Pearson (regression) or AUC (binary)
    best_val_rmse = float("inf")   # only meaningful for regression
    best_val_epoch = 0
    best_state: dict | None = None
    epoch_start_times: list[float] = []
    epoch_history: list[dict] = []        # for live overfitting detector
    insights_emitted: set[str] = set()    # pattern IDs already announced
    t0 = time.time()

    # ── Resume from a prior run's checkpoint ──────────────────────────
    # When ``hparams.resume_from_run_id`` is set, load that run's
    # state.pt into the live model, jump start_epoch past whatever
    # the snapshot said had completed. Trainer continues from there.
    # The optimizer/scheduler state isn't restored (this is a "warm
    # start" not a strict mid-run resume), so the user gets a small
    # transient bump in train_loss but the weights are intact.
    start_epoch = 1
    resume_id = hp.get("resume_from_run_id")
    if resume_id:
        try:
            from .checkpoints import load_meta as _load_meta, _run_dir as _resume_dir
            prior_meta = _load_meta(resume_id)
            if prior_meta is None:
                run.emit({"type": "log", "level": "warn",
                          "text": f"resume_from_run_id={resume_id!r} not found; "
                                  f"starting from scratch."})
            else:
                # Load weights via the same callable-map-location bypass
                # as load_for_inference (CPU-safe).
                state_path = _resume_dir(resume_id) / "state.pt"
                _state = torch.load(
                    state_path,
                    map_location=lambda storage, _loc: storage,
                    weights_only=False,
                )
                model.load_state_dict(_state)
                model.to(device)
                # Skip past the resumed epochs.
                prior_summary = prior_meta.get("summary", {}) or {}
                ep_done = int(prior_summary.get("epochs_completed", 0))
                start_epoch = max(1, ep_done + 1)
                if start_epoch > epochs:
                    run.emit({"type": "log", "level": "warn",
                              "text": (f"Prior run {resume_id} already completed "
                                       f"{ep_done} epochs ≥ requested {epochs}. "
                                       f"Bump hparams.epochs to continue training.")})
                run.summary["resumed_from"] = resume_id
                run.summary["resumed_at_epoch"] = start_epoch
                run.emit({"type": "log", "level": "info",
                          "text": (f"Resumed from {resume_id} at epoch {start_epoch} "
                                   f"(prior best val "
                                   f"{prior_summary.get('best_val_pearson')}, "
                                   f"prior epochs_completed={ep_done}).")})
        except Exception as _resume_exc:
            run.emit({"type": "log", "level": "warn",
                      "text": f"Resume failed ({_resume_exc}); starting from scratch."})

    for epoch in range(start_epoch, epochs + 1):
        _check_cancel(run)
        epoch_start = time.time()
        epoch_start_times.append(epoch_start)
        model.train()
        running_loss = 0.0
        running_n = 0
        total_batches = len(train_loader)
        for batch_i, batch in enumerate(train_loader, start=1):
            _check_cancel(run)
            # Generic N-input batch dispatch:
            #   (feats, y)               featurized run / single tensor input
            #   (seq, smi, y)            token / 2-input runs (DeepDTA, ESM-*)
            #   (t0, t1, ..., t_{n-1}, y) multi-feature runs (3+ inputs)
            # The trailing element is always the label. Everything else
            # is moved to the device and passed positionally to model().
            #
            # The ConPLex template needs a manual split — it doesn't use
            # the new flow path, so the special case is preserved.
            *inputs, y = batch
            inputs = [t.to(device, non_blocking=True) for t in inputs]
            y     = y.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with autocast_ctx():
                if is_featurized_run and run.template_id == "conplex" and len(inputs) == 1:
                    feats = inputs[0]
                    lig_dim = meta.get("ligand_dim", 0)
                    lig = feats[:, :lig_dim]
                    prot = feats[:, lig_dim:lig_dim + meta.get("protein_dim", 0)]
                    yp = model(prot, lig)
                else:
                    yp = model(*inputs)
                loss = loss_fn(yp, y)
            if amp:
                scaler.scale(loss).backward()
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                optimizer.step()
            scheduler.step()
            running_loss += float(loss.item()) * y.size(0)
            running_n += y.size(0)
            # Emit a batch event every N batches so the GUI shows mid-epoch progress
            if (batch_i % max(1, total_batches // 20) == 0) or (batch_i == total_batches):
                run.emit({"type": "batch", "epoch": epoch, "batch": batch_i,
                          "total_batches": total_batches,
                          "loss": running_loss / max(running_n, 1)})
        train_loss = running_loss / max(running_n, 1)
        _check_cancel(run)
        val = evaluate(model, val_loader, device, amp=amp,
                       featurized=is_featurized_run,
                       ligand_dim=meta.get("ligand_dim", 0),
                       protein_dim=meta.get("protein_dim", 0),
                       template_id=run.template_id,
                       task=task_kind)
        elapsed = time.time() - t0
        # ETA = mean epoch duration × epochs remaining
        if len(epoch_start_times) >= 2:
            mean_dur = (time.time() - epoch_start_times[0]) / len(epoch_start_times)
            eta = mean_dur * (epochs - epoch)
        else:
            eta = (time.time() - epoch_start) * (epochs - epoch)
        lr_now = optimizer.param_groups[0]["lr"]
        # Task-specific epoch event. For BOTH tasks we always populate
        # train_loss, val_loss, val_pearson, val_rmse, val_ci so the
        # existing GUI / overfitting-detector code keeps working — but
        # for binary tasks those slots carry classification-appropriate
        # values (BCE for losses, AUC for pearson/ci) instead of
        # meaningless logit-vs-{0,1} arithmetic.
        if task_kind == "binary":
            epoch_event = {
                "type": "epoch",
                "epoch": epoch, "total_epochs": epochs,
                "train_loss": train_loss,        # train BCE
                "val_loss":   val["bce"],        # val BCE  (was rmse² — wrong for binary)
                "val_bce":    val["bce"],
                "val_auc":    val["auc"],
                "val_accuracy": val["accuracy"],
                "val_f1":     val["f1"],
                "val_precision": val["precision"],
                "val_recall": val["recall"],
                "val_mean_prob": val["mean_prob"],
                # Aliases so the existing live-metrics card + pattern detector
                # see a meaningful number. Pearson ≡ AUC (both higher-is-better,
                # both in [0,1]). RMSE ≡ BCE. CI ≡ AUC (Mann-Whitney equivalence).
                "val_pearson":  val["auc"],
                "val_spearman": val["auc"],
                "val_rmse":     val["bce"],
                "val_mae":      val["bce"],
                "val_ci":       val["auc"],
                "lr": lr_now, "elapsed_s": elapsed, "eta_s": max(0.0, eta),
                "task": "binary",
            }
        else:
            epoch_event = {
                "type": "epoch",
                "epoch": epoch, "total_epochs": epochs,
                "train_loss": train_loss,
                "val_loss":   val["rmse"] ** 2,
                "val_pearson": val["pearson"],
                "val_spearman": val["spearman"],
                "val_rmse":   val["rmse"],
                "val_mae":    val["mae"],
                "val_ci":     val["ci"],
                "lr": lr_now, "elapsed_s": elapsed, "eta_s": max(0.0, eta),
                "task": "regression",
            }
        run.emit(epoch_event)
        epoch_history.append(epoch_event)
        # ── Flow diagnostic taps ─────────────────────────────────────
        # If the user dropped diag.tap blocks into the flow, each one
        # accumulated running stats during training-mode forward passes.
        # Surface those after the epoch so the GUI can render per-tap
        # health (mean/std/min/max + NaN fraction).
        try:
            from .flow_compiler import find_diag_taps
            taps = find_diag_taps(model)
        except Exception:
            taps = []
        for tap in taps:
            if tap.n_observations == 0:
                continue
            run.emit({
                "type":    "diag",
                "epoch":   epoch,
                "node_id": tap.node_id,
                "mean":    tap.last_mean,
                "std":     tap.last_std,
                "min":     tap.last_min,
                "max":     tap.last_max,
                "nan_frac": tap.last_nan_frac,
                "n_obs":   tap.n_observations,
            })
        # Track best using the task-appropriate score.
        cur_score = val["auc"] if task_kind == "binary" else val["pearson"]
        # ── Optuna pruning report ─────────────────────────────────────
        # When this run is part of a sweep, the sweep loop stashes the
        # active Optuna trial in a thread-local before invoking
        # train_run. We report cur_score at every epoch and check
        # should_prune so the Hyperband pruner can early-terminate
        # trials whose intermediate values are already hopeless.
        _trial = None
        try:
            from .sweeps import _OPTUNA_TRIAL_LOCAL
            _trial = getattr(_OPTUNA_TRIAL_LOCAL, "trial", None)
        except Exception:
            _trial = None
        if _trial is not None:
            try:
                _trial.report(float(cur_score), step=int(epoch))
                if _trial.should_prune():
                    import optuna
                    run.emit({"type": "log", "level": "warn",
                              "text": (f"Optuna pruner stopping trial at epoch "
                                       f"{epoch} (cur_score={cur_score:.4f}).")})
                    raise optuna.TrialPruned()
            except ImportError:
                pass
        if cur_score > best_val_score:
            best_val_score = cur_score
            best_val_rmse  = val.get("rmse", val.get("bce", 0.0))
            best_val_epoch = epoch
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            metric_name = "val AUC" if task_kind == "binary" else "val Pearson"
            run.emit({"type": "log", "level": "ok",
                      "text": (f"Best {metric_name} so far: {best_val_score:.4f} "
                               f"({'BCE' if task_kind=='binary' else 'RMSE'} {best_val_rmse:.4f}) "
                               f"at epoch {epoch}")})
        # ── Auto-checkpoint after every epoch ──────────────────────────
        # Long runs (25-100 epochs) used to lose ALL progress on crash
        # because the only save was at end-of-train. Now we persist the
        # *best-so-far* state after every epoch — cheap (one extra disk
        # write per epoch, dwarfed by the train+val time) and lets the
        # user resume from the latest checkpoint by setting
        # hparams.resume_from_run_id on the next launch. We save only
        # when we updated best_state THIS epoch (otherwise the on-disk
        # state.pt is already current).
        # Mirror epochs_completed into run.summary so the end-of-train
        # save_checkpoint preserves it across the final overwrite.
        run.summary["epochs_completed"] = epoch
        run.summary["total_epochs"] = epochs
        run.summary["snapshot_at"]  = "epoch_end"
        if (best_state is not None
                and best_val_epoch == epoch
                and hp.get("checkpoint_every_epoch", True)):
            try:
                from .checkpoints import save_checkpoint as _save_ckpt
                # Build a snapshot summary so the partial checkpoint is
                # self-describing even mid-run.
                snapshot_summary = dict(run.summary)
                snapshot_summary.update({
                    "best_val_pearson":  best_val_score if task_kind != "binary" else None,
                    "best_val_auc":      best_val_score if task_kind == "binary" else None,
                    "best_val_rmse":     best_val_rmse,
                    "best_val_epoch":    best_val_epoch,
                    "epochs_completed":  epoch,
                    "total_epochs":      epochs,
                    "snapshot_at":       "epoch_end",
                })
                # Restore best_state into a temp module purely for save,
                # then put the live state back (so training continues
                # from the live weights, not the checkpointed ones).
                live_state = {k: v.detach().cpu().clone()
                              for k, v in model.state_dict().items()}
                model.load_state_dict(best_state)
                _save_ckpt(
                    run_id=run.run_id, model=model,
                    template_id=run.template_id,
                    effective_config=run.effective_config or {},
                    hparams=hp, summary=snapshot_summary,
                    y_pkd_range=label_range,
                )
                model.load_state_dict(live_state)
            except Exception as _ckpt_exc:
                run.emit({"type": "log", "level": "warn",
                          "text": f"Per-epoch checkpoint failed: {_ckpt_exc}"})
        # ── Live overfitting / divergence pattern check ───────────────
        # Runs after every epoch event so the GUI's Smart Insights card
        # can light up the moment a pattern emerges, not just at the end.
        try:
            _detect_training_patterns(
                run, epoch_history,
                best_pearson=best_val_score,
                best_epoch=best_val_epoch,
                emitted=insights_emitted,
            )
        except Exception as exc:  # noqa: BLE001
            # Detector bugs must never crash a training run.
            run.emit({"type": "log", "level": "warn",
                      "text": f"Pattern detector skipped: {exc}"})

    # Final test eval on best checkpoint — capture raw predictions so the
    # Results screen can render real scatter / calibration / ROC / residuals.
    if best_state is not None:
        model.load_state_dict(best_state)
    _check_cancel(run)

    # ── Platt scaling for head.classifier/calibrated ──────────────────
    # When the flow's classifier head was the "calibrated" impl, the
    # FlowModule's loss_name carries "bce_with_logits_platt". After the
    # main torch training, fit a 1-D logistic regression on the model's
    # val-set logits + binary labels. Stash the scalars (a, b) in the
    # run summary so inference can apply σ(a · logit + b) to produce
    # well-calibrated probabilities.
    if loss_name_from_model == "bce_with_logits_platt":
        try:
            model.eval()
            val_logits, val_labels = [], []
            with torch.no_grad():
                for batch in val_loader:
                    *inputs, y = batch
                    inputs = [t.to(device, non_blocking=True) for t in inputs]
                    yp = model(*inputs).float()
                    val_logits.append(yp.cpu().numpy().reshape(-1))
                    val_labels.append(y.numpy().reshape(-1))
            logits = np.concatenate(val_logits) if val_logits else np.array([])
            labels = np.concatenate(val_labels).astype(np.float64) if val_labels else np.array([])
            if len(logits) >= 10 and len(np.unique(labels)) >= 2:
                # 1-D logistic regression via sklearn — small + reliable.
                from sklearn.linear_model import LogisticRegression
                X = logits.reshape(-1, 1).astype(np.float64)
                lr = LogisticRegression(C=1e9, solver="lbfgs", max_iter=200)
                lr.fit(X, labels.astype(int))
                a = float(lr.coef_[0, 0])
                b = float(lr.intercept_[0])
                run.summary["platt_a"] = a
                run.summary["platt_b"] = b
                # Quick calibration quality on the val set.
                cal_probs = 1.0 / (1.0 + np.exp(-(a * logits + b)))
                run.summary["platt_val_brier"] = float(
                    np.mean((cal_probs - labels) ** 2)
                )
                run.emit({"type": "log", "level": "ok",
                          "text": (f"Platt scaling fitted on val (n={len(logits)}): "
                                   f"σ({a:+.3f}·logit + {b:+.3f}); "
                                   f"val Brier = {run.summary['platt_val_brier']:.4f}")})
            else:
                run.emit({"type": "log", "level": "warn",
                          "text": ("Platt scaling skipped: val set too small "
                                   f"(n={len(logits)}) or single-class.")})
        except Exception as _exc:
            run.emit({"type": "log", "level": "warn",
                      "text": f"Platt scaling failed: {_exc}"})

    # ── Hybrid head fit-after-feature-extract (XGBoost / CatBoost) ───
    # If the flow's head is a HybridBoosterHead (the user picked
    # head.regression.xgboost or .catboost in the flow editor), the
    # torch portion is now trained. Walk the model, extract the fused
    # embedding for every train+val example, then fit the booster on
    # (embedding, label). At test time we score with the booster.
    try:
        from .flow_compiler import find_hybrid_heads, _HybridBoosterHead
    except Exception:
        find_hybrid_heads = lambda _m: []
    hybrid_heads = find_hybrid_heads(model)
    if hybrid_heads:
        head_node = hybrid_heads[0]   # at most one head in a flow today
        run.emit({"type": "log", "level": "info",
                  "text": (f"Hybrid head detected ({head_node.backend}). Extracting "
                           f"fused embeddings from the trained torch portion to fit "
                           f"the booster…")})
        # The fused embedding is whatever the head's input was. We
        # capture it via a forward hook on head_node, then run a
        # gradient-free pass over the train + val loaders.
        captured: list = []
        def _hook(_mod, inp, _out):
            x = inp[0] if isinstance(inp, tuple) else inp
            captured.append(x.detach().cpu().numpy())
        h = head_node.register_forward_hook(_hook)
        try:
            model.eval()
            train_embeddings, train_labels = [], []
            for loader, label in [(train_loader, "train"), (val_loader, "val")]:
                buf_emb, buf_y = [], []
                with torch.no_grad():
                    for batch in loader:
                        captured.clear()
                        # Generic N-input unpacking — mirror the train + eval loops.
                        *inputs, y = batch
                        inputs = [t.to(device, non_blocking=True) for t in inputs]
                        if is_featurized_run and run.template_id == "conplex" and len(inputs) == 1:
                            feats = inputs[0]
                            lig_dim = meta.get("ligand_dim", 0)
                            _ = model(
                                feats[:, lig_dim:lig_dim + meta.get("protein_dim", 0)],
                                feats[:, :lig_dim],
                            )
                        else:
                            _ = model(*inputs)
                        # ``captured`` now contains the input tensor that
                        # was fed to the booster head — exactly the fused
                        # embedding we want.
                        if captured:
                            buf_emb.append(captured[0])
                            buf_y.append(y.numpy())
                if buf_emb:
                    train_embeddings.append(np.concatenate(buf_emb, axis=0))
                    train_labels.append(np.concatenate(buf_y, axis=0))
            X_fit = np.concatenate(train_embeddings, axis=0) if train_embeddings else None
            y_fit = np.concatenate(train_labels, axis=0)     if train_labels     else None
        finally:
            h.remove()
        if X_fit is not None and len(X_fit) > 0:
            fit_meta = head_node.fit_booster(X_fit, y_fit)
            run.emit({"type": "log", "level": "ok",
                      "text": (f"Booster fitted: {fit_meta['backend']} on "
                               f"{fit_meta['n_train']:,} embeddings "
                               f"(dim {X_fit.shape[1]}). "
                               f"Test eval below uses the booster predictions.")})
            run.summary["hybrid_booster"] = fit_meta

    test = evaluate(model, test_loader, device, amp=amp, return_preds=True,
                    featurized=is_featurized_run,
                    ligand_dim=meta.get("ligand_dim", 0),
                    protein_dim=meta.get("protein_dim", 0),
                    template_id=run.template_id,
                    task=task_kind)
    if task_kind == "binary":
        run.summary.update({
            "best_val_auc":      best_val_score,
            "best_val_bce":      best_val_rmse,
            "best_val_pearson":  best_val_score,    # keep the legacy key populated for old GUIs
            "best_val_rmse":     best_val_rmse,
            "test_auc":          test["auc"],
            "test_accuracy":     test["accuracy"],
            "test_f1":           test["f1"],
            "test_precision":    test["precision"],
            "test_recall":       test["recall"],
            "test_bce":          test["bce"],
            "test_mean_prob":    test["mean_prob"],
            "test_pos_rate":     test["pos_rate"],
            "test_pearson":      test["auc"],       # legacy alias = AUC
            "test_rmse":         test["bce"],       # legacy alias = BCE
            "test_ci":           test["auc"],       # legacy alias = AUC
            "test_mae":          test["bce"],
            "wall_time_s":       time.time() - t0,
        })
    else:
        run.summary.update({
            "best_val_pearson": best_val_score,
            "best_val_rmse": best_val_rmse,
            "test_pearson": test["pearson"],
            "test_spearman": test["spearman"],
            "test_rmse": test["rmse"],
            "test_mae": test["mae"],
            "test_ci": test["ci"],
            "wall_time_s": time.time() - t0,
        })
    # Derive everything the Results screen needs from the raw test
    # predictions. compute_results_summary is regression-oriented (it
    # binarises at a configurable pki_threshold for ROC); for binary
    # tasks we skip it and emit a minimal classification summary.
    if task_kind == "binary":
        # No scatter/calibration/residuals — those don't fit binary outputs.
        # Render a confusion-matrix-friendly summary instead.
        results = {
            "task": "binary",
            "metrics": {
                "auc":       test["auc"],
                "accuracy":  test["accuracy"],
                "f1":        test["f1"],
                "precision": test["precision"],
                "recall":    test["recall"],
                "bce":       test["bce"],
                "n":         test["n"],
            },
            "y_pkd_range": [0.0, 1.0],            # so the checkpoint range field stays valid
        }
        run.results = results
    else:
        results = compute_results_summary(test["y_true"], test["y_pred"])
        # Keep raw test-set predictions in run.results so /results.csv
        # can stream them. compute_results_summary returns
        # downsampled scatter pairs only; the CSV exporter wants every
        # row. Store them as plain Python lists so JSON serialisation
        # works downstream (np arrays don't json-encode cleanly).
        results["y_true"] = [float(v) for v in test["y_true"]]
        results["y_pred"] = [float(v) for v in test["y_pred"]]
        run.results = results
        run.summary["r2"] = results["metrics"]["r2"]
        run.summary["test_auc_pki6"] = results["roc"]["auc"]
    # Persist the best checkpoint to disk so Inference / Promote can reload
    # it without keeping the model in RAM.
    try:
        cp_dir = save_checkpoint(
            run_id=run.run_id,
            model=model,
            template_id=run.template_id,
            effective_config=run.effective_config,
            hparams=run.hparams,
            summary=run.summary,
            y_pkd_range=tuple(results["y_pkd_range"]),
        )
        run.summary["checkpoint_dir"] = str(cp_dir)
        run.emit({"type": "log", "level": "ok", "text": f"Checkpoint saved → {cp_dir}"})
        # Auto-register the model in the SQLite registry as a 'candidate'.
        # Promote endpoint can then open a promotion request against it.
        try:
            registered = model_db.register_model(
                run_id=run.run_id,
                template_id=run.template_id,
                template_label=run.effective_config.get("template_label"),
                metrics={k: v for k, v in run.summary.items() if isinstance(v, (int, float, str))},
                hparams=run.hparams,
                checkpoint_dir=str(cp_dir),
            )
            run.summary["model_id"] = registered["id"]
            run.emit({"type": "log", "level": "ok",
                      "text": f"Registered as candidate model {registered['id']}"})
        except Exception as exc:
            run.emit({"type": "log", "level": "warn", "text": f"Model registry insert failed: {exc}"})
    except Exception as exc:
        run.emit({"type": "log", "level": "warn", "text": f"Checkpoint save failed: {exc}"})
    run.emit({"type": "final", **{k: v for k, v in run.summary.items() if k != "y_true" and k != "y_pred"}})
    run.set_status("completed", finished_at=time.time())
