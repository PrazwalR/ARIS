"""Generates docs/assets/metrics/<dataset>_{roc,pr,confusion}.png from a
fresh training run -- not from any cached/committed file -- and prints the
same numbers run.py's JSON report would, so the curves and docs/METRICS.md's
tables stay provably consistent with each other.

Needs the `viz` extra (matplotlib), which src/aris itself never depends on:
    pip install -e ".[dev,ml,viz]"

Usage:
    ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \\
        python scripts/generate_metrics_report.py --dataset synthetic

    ARIS_SALT=$(python -c "import secrets;print(secrets.token_hex(32))") \\
        python scripts/generate_metrics_report.py --dataset ulb \\
            --max-rows 30000 --rounds 5 --epochs 2
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy.typing as npt
from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve

from aris.fl.evaluation import train_and_score
from aris.fl.metrics import classification_metrics
from aris.fl.config import TrainConfig

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets" / "metrics"


def plot_curves(
    dataset_label: str,
    banks_scores: list[tuple[str, npt.NDArray[Any]]],
    global_scores: npt.NDArray[Any],
    y_te: npt.NDArray[Any],
    out_prefix: str,
) -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(6, 5))
    for bank_id, scores in banks_scores:
        fpr, tpr, _ = roc_curve(y_te, scores)
        ax.plot(fpr, tpr, alpha=0.6, linewidth=1, label=f"{bank_id} (local)")
    fpr, tpr, _ = roc_curve(y_te, global_scores)
    ax.plot(fpr, tpr, color="black", linewidth=2.5, label="Global FedAvg")
    ax.plot([0, 1], [0, 1], "--", color="gray", linewidth=1, label="chance")
    ax.set_xlabel("False Positive Rate")
    ax.set_ylabel("True Positive Rate")
    ax.set_title(f"{dataset_label} — ROC, held-out set (never trained on)")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(ASSETS / f"{out_prefix}_roc.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 5))
    for bank_id, scores in banks_scores:
        precision, recall, _ = precision_recall_curve(y_te, scores)
        ax.plot(recall, precision, alpha=0.6, linewidth=1, label=f"{bank_id} (local)")
    precision, recall, _ = precision_recall_curve(y_te, global_scores)
    ax.plot(recall, precision, color="black", linewidth=2.5, label="Global FedAvg")
    base_rate = float(y_te.mean())
    ax.axhline(
        base_rate, linestyle="--", color="gray", linewidth=1, label=f"base rate ({base_rate:.3f})"
    )
    ax.set_xlabel("Recall")
    ax.set_ylabel("Precision")
    ax.set_title(f"{dataset_label} — Precision/Recall, held-out set")
    ax.legend(fontsize=8, loc="upper right")
    ax.set_ylim(0, 1.02)
    fig.tight_layout()
    fig.savefig(ASSETS / f"{out_prefix}_pr.png", dpi=150)
    plt.close(fig)


def plot_confusion_matrices(
    dataset_label: str,
    banks_scores: list[tuple[str, npt.NDArray[Any]]],
    global_scores: npt.NDArray[Any],
    y_te: npt.NDArray[Any],
    out_prefix: str,
) -> None:
    """One grid, all 6 models (5 local + global), confusion matrix at the
    same 0.5 threshold classification_metrics() uses for accuracy/precision/
    recall -- so this picture and those numbers always agree."""
    ASSETS.mkdir(parents=True, exist_ok=True)
    panels = [*banks_scores, ("GLOBAL FedAvg", global_scores)]

    fig, axes = plt.subplots(2, 3, figsize=(12, 8))
    for ax, (name, scores) in zip(axes.flat, panels, strict=True):
        predicted = (scores >= 0.5).astype(int)
        cm = confusion_matrix(y_te, predicted, labels=[0, 1])
        ax.imshow(cm, cmap="Blues")
        for i in range(2):
            for j in range(2):
                color = "white" if cm[i, j] > cm.max() / 2 else "black"
                ax.text(j, i, str(cm[i, j]), ha="center", va="center", color=color, fontsize=13)
        ax.set_xticks([0, 1])
        ax.set_yticks([0, 1])
        ax.set_xticklabels(["pred: not fraud", "pred: fraud"])
        ax.set_yticklabels(["actual: not fraud", "actual: fraud"])
        ax.set_title(name, fontsize=10)
    fig.suptitle(f"{dataset_label} — confusion matrices at score ≥ 0.5, held-out set")
    fig.tight_layout()
    fig.savefig(ASSETS / f"{out_prefix}_confusion.png", dpi=150)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render ROC/PR curves for docs/METRICS.md")
    parser.add_argument("--dataset", choices=("synthetic", "ulb"), required=True)
    parser.add_argument("--max-rows", type=int, default=None)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=4)
    args = parser.parse_args(argv)

    cfg = TrainConfig(
        num_banks=5, rounds=args.rounds, local_epochs=args.epochs, max_train_rows=args.max_rows
    )
    run = train_and_score(args.dataset, cfg)
    banks_scores = list(zip(run.bank_ids, run.local_scores, strict=True))
    global_scores, y_te = run.global_scores, run.y_holdout

    label = "Synthetic" if run.dataset == "synthetic" else "ULB credit-card fraud"
    plot_curves(label, banks_scores, global_scores, y_te, run.dataset)
    plot_confusion_matrices(label, banks_scores, global_scores, y_te, run.dataset)

    print(f"wrote {ASSETS / (run.dataset + '_roc.png')}")
    print(f"wrote {ASSETS / (run.dataset + '_pr.png')}")
    print(f"wrote {ASSETS / (run.dataset + '_confusion.png')}")

    g = classification_metrics(y_te, global_scores)
    print(
        f"cross-check -- global auc: {g['auc']:.4f}  pr_auc: {g['pr_auc']:.4f}  "
        f"precision@0.5: {g['precision_at_0_5']:.4f}  recall@0.5: {g['recall_at_0_5']:.4f}"
    )
    print("(compare against data/processed/m1_metrics_<dataset>.json's 'global' block)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
