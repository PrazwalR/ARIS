from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)


def classification_metrics(y_true: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    """AUC, PR-AUC, recall at 5% FPR, FPR at 50% recall."""
    y_true = np.asarray(y_true).astype(int)
    scores = np.asarray(scores, dtype=float)
    if len(np.unique(y_true)) < 2:
        return {
            "auc": float("nan"),
            "pr_auc": float("nan"),
            "recall_at_fpr_0_05": float("nan"),
            "fpr_at_recall_0_50": float("nan"),
            "positives": int(y_true.sum()),
            "n": int(len(y_true)),
        }

    auc = float(roc_auc_score(y_true, scores))
    pr_auc = float(average_precision_score(y_true, scores))

    fpr, tpr, _ = roc_curve(y_true, scores)
    recall_at_fpr = _best_tpr_at_fpr(fpr, tpr, max_fpr=0.05)

    prec, rec, _ = precision_recall_curve(y_true, scores)
    fpr_at_recall = _fpr_at_recall(y_true, scores, target_recall=0.5)

    return {
        "auc": auc,
        "pr_auc": pr_auc,
        "recall_at_fpr_0_05": float(recall_at_fpr),
        "fpr_at_recall_0_50": float(fpr_at_recall),
        "positives": int(y_true.sum()),
        "n": int(len(y_true)),
    }


def _best_tpr_at_fpr(fpr: np.ndarray, tpr: np.ndarray, max_fpr: float) -> float:
    ok = fpr <= max_fpr
    if not np.any(ok):
        return 0.0
    return float(tpr[ok].max())


def _fpr_at_recall(y_true: np.ndarray, scores: np.ndarray, target_recall: float) -> float:
    order = np.argsort(-scores)
    y = y_true[order]
    tp = np.cumsum(y)
    fp = np.cumsum(1 - y)
    pos = max(int(y_true.sum()), 1)
    neg = max(int((1 - y_true).sum()), 1)
    rec = tp / pos
    hits = np.where(rec >= target_recall)[0]
    if len(hits) == 0:
        return 1.0
    i = int(hits[0])
    return float(fp[i] / neg)


def mean_metric(rows: list[dict[str, float]], key: str) -> float:
    vals = [r[key] for r in rows if r.get(key) == r.get(key)]  # drop NaN
    if not vals:
        return float("nan")
    return float(np.mean(vals))
