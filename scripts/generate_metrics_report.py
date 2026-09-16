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
import numpy as np
import numpy.typing as npt
from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve

from aris.fl.client import BankFlowerClient
from aris.fl.config import TrainConfig
from aris.fl.datasets import TabularFraud, load_dataset
from aris.fl.metrics import classification_metrics
from aris.fl.model import new_model, predict_scores, set_weights, train_local
from aris.fl.partition import (
    dirichlet_shards,
    equal_contiguous_shards,
    holdout_split,
    named_banks,
    pooled_holdout_from_shards,
    temporal_shards,
)
from aris.fl.run import _federated_train

ASSETS = Path(__file__).resolve().parent.parent / "docs" / "assets" / "metrics"


def build_split(
    dataset: str, cfg: TrainConfig
) -> tuple[TabularFraud, list[BankFlowerClient], npt.NDArray[Any], npt.NDArray[Any]]:
    """Mirrors aris.fl.run.run_experiment's dataset/partition/holdout setup
    exactly, so the split here matches what a real training run would use."""
    data = load_dataset(
        dataset, max_rows=cfg.max_train_rows, seed=cfg.seed, num_banks=cfg.num_banks
    )
    banks = named_banks(cfg.num_banks)

    if data.name == "synthetic":
        shards = equal_contiguous_shards(data.x, data.y, cfg.num_banks)
        shards, x_te, y_te = pooled_holdout_from_shards(shards, cfg.holdout_frac, cfg.seed)
    else:
        x_tr, y_tr, x_te, y_te = holdout_split(
            data.x, data.y, frac=cfg.holdout_frac, seed=cfg.seed, time_col=data.time
        )
        if data.time is not None:
            t_tr = np.arange(len(y_tr), dtype=np.float32)
            shards = temporal_shards(x_tr, y_tr, t_tr, cfg.num_banks)
        else:
            shards = dirichlet_shards(x_tr, y_tr, cfg.num_banks, cfg.dirichlet_alpha, cfg.seed)

    clients = [
        BankFlowerClient(banks[i], shards[i][0], shards[i][1], hidden=cfg.hidden, seed=cfg.seed + i)
        for i in range(cfg.num_banks)
    ]
    return data, clients, x_te, y_te


def train_local_models(clients: list[BankFlowerClient], cfg: TrainConfig) -> list[Any]:
    """Same local steps as run.py's _train_local_baselines -- separate model
    instances, never touching clients' own Flower-side state."""
    total_epochs = cfg.local_epochs * cfg.rounds
    models = []
    for i, client in enumerate(clients):
        model = new_model(client.x.shape[1], hidden=cfg.hidden, seed=cfg.seed + 100 + i)
        train_local(
            model,
            client.x,
            client.y,
            epochs=total_epochs,
            batch_size=cfg.batch_size,
            lr=cfg.learning_rate,
            seed=cfg.seed + i,
        )
        models.append(model)
    return models


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
    data, clients, x_te, y_te = build_split(args.dataset, cfg)

    local_models = train_local_models(clients, cfg)
    banks_scores = [
        (clients[i].bank_id, predict_scores(local_models[i], x_te)) for i in range(len(clients))
    ]

    global_weights, _, _ = _federated_train(clients, cfg)
    global_model = new_model(clients[0].x.shape[1], hidden=cfg.hidden, seed=cfg.seed)
    set_weights(global_model, global_weights)
    global_scores = predict_scores(global_model, x_te)

    label = "Synthetic" if data.name == "synthetic" else "ULB credit-card fraud"
    plot_curves(label, banks_scores, global_scores, y_te, data.name)
    plot_confusion_matrices(label, banks_scores, global_scores, y_te, data.name)

    print(f"wrote {ASSETS / (data.name + '_roc.png')}")
    print(f"wrote {ASSETS / (data.name + '_pr.png')}")
    print(f"wrote {ASSETS / (data.name + '_confusion.png')}")

    g = classification_metrics(y_te, global_scores)
    print(
        f"cross-check -- global auc: {g['auc']:.4f}  pr_auc: {g['pr_auc']:.4f}  "
        f"precision@0.5: {g['precision_at_0_5']:.4f}  recall@0.5: {g['recall_at_0_5']:.4f}"
    )
    print("(compare against data/processed/m1_metrics_<dataset>.json's 'global' block)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
