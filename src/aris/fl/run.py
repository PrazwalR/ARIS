"""M1 experiment: local-only baselines vs Flower FedAvg global model.

Raw transaction rows stay inside each BankFlowerClient. The server only
sees weight arrays + example counts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from aris.fl.client import BankFlowerClient
from aris.fl.config import DATA_PROCESSED, TrainConfig
from aris.fl.datasets import load_dataset
from aris.fl.fedavg import as_plain_floats, fedavg
from aris.fl.metrics import classification_metrics, mean_metric
from aris.fl.model import new_model, predict_scores, set_weights, train_local
from aris.fl.partition import (
    dirichlet_shards,
    equal_contiguous_shards,
    holdout_split,
    named_banks,
    pooled_holdout_from_shards,
    temporal_shards,
)
from aris.fl.scorer import save_weights


def run_experiment(
    dataset: str = "synthetic",
    cfg: TrainConfig | None = None,
    partition: str = "auto",
) -> dict[str, Any]:
    cfg = cfg or TrainConfig()
    data = load_dataset(
        dataset,
        max_rows=cfg.max_train_rows,
        seed=cfg.seed,
        num_banks=cfg.num_banks,
    )
    banks = named_banks(cfg.num_banks)
    part = partition

    if data.name == "synthetic":
        part = "bank-identity"
        shards = equal_contiguous_shards(data.x, data.y, cfg.num_banks)
        shards, x_te, y_te = pooled_holdout_from_shards(shards, cfg.holdout_frac, cfg.seed)
    else:
        if part == "auto":
            part = "temporal" if data.time is not None else "dirichlet"
        x_tr, y_tr, x_te, y_te = holdout_split(
            data.x,
            data.y,
            frac=cfg.holdout_frac,
            seed=cfg.seed,
            time_col=data.time,
        )
        if part == "temporal" and data.time is not None:
            t_tr = np.arange(len(y_tr), dtype=np.float32)
            shards = temporal_shards(x_tr, y_tr, t_tr, cfg.num_banks)
        else:
            shards = dirichlet_shards(x_tr, y_tr, cfg.num_banks, cfg.dirichlet_alpha, cfg.seed)

    if any(len(s[1]) == 0 for s in shards):
        raise RuntimeError("A bank shard is empty. Use fewer banks or a larger dataset.")

    clients = [
        BankFlowerClient(banks[i], shards[i][0], shards[i][1], hidden=cfg.hidden, seed=cfg.seed + i)
        for i in range(cfg.num_banks)
    ]

    local_metrics = _train_local_baselines(clients, x_te, y_te, cfg)
    global_weights, round_log = _federated_train(clients, cfg)
    global_model = new_model(clients[0].x.shape[1], hidden=cfg.hidden, seed=cfg.seed)
    set_weights(global_model, global_weights)
    global_scores = predict_scores(global_model, x_te)
    global_metrics = classification_metrics(y_te, global_scores)

    mean_local_auc = mean_metric(local_metrics, "auc")
    ckpt = DATA_PROCESSED / f"m1_global_{data.name}.npz"
    DATA_PROCESSED.mkdir(parents=True, exist_ok=True)
    save_weights(
        ckpt,
        global_weights,
        {
            "n_features": int(clients[0].x.shape[1]),
            "hidden": cfg.hidden,
            "dataset": data.name,
            "model_version": "v0.4-fl",
            "feature_names": data.feature_names,
        },
    )

    return {
        "dataset": data.name,
        "partition": part,
        "num_banks": cfg.num_banks,
        "rounds": cfg.rounds,
        "n_features": int(clients[0].x.shape[1]),
        "n_train": int(sum(len(c.y) for c in clients)),
        "n_holdout": len(y_te),
        "holdout_positives": int(y_te.sum()),
        "checkpoint": str(ckpt),
        "banks": [
            {
                "bank_id": clients[i].bank_id,
                "n": len(clients[i].y),
                "positives": int(clients[i].y.sum()),
                "local": local_metrics[i],
            }
            for i in range(cfg.num_banks)
        ],
        "mean_local": {
            "auc": mean_local_auc,
            "pr_auc": mean_metric(local_metrics, "pr_auc"),
            "recall_at_fpr_0_05": mean_metric(local_metrics, "recall_at_fpr_0_05"),
            "fpr_at_recall_0_50": mean_metric(local_metrics, "fpr_at_recall_0_50"),
        },
        "global": global_metrics,
        "global_beats_mean_local_auc": bool(global_metrics["auc"] > mean_local_auc),
        "privacy": {
            "raw_rows_shared": False,
            "server_sees": "model weight arrays + example counts only",
        },
        "rounds_log": round_log,
        "model_version": "v0.4-fl",
    }


def _train_local_baselines(
    clients: list[BankFlowerClient],
    x_te: np.ndarray,
    y_te: np.ndarray,
    cfg: TrainConfig,
) -> list[dict[str, float]]:
    """Same local steps as FL (epochs x rounds) so the comparison is fair."""
    rows = []
    total_epochs = cfg.local_epochs * cfg.rounds
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
        scores = predict_scores(model, x_te)
        rows.append(classification_metrics(y_te, scores))
    return rows


def _federated_train(
    clients: list[BankFlowerClient],
    cfg: TrainConfig,
) -> tuple[list[np.ndarray], list[dict[str, Any]]]:
    weights = clients[0].get_parameters({})
    logs = []
    for rnd in range(1, cfg.rounds + 1):
        config = {
            "local_epochs": cfg.local_epochs,
            "batch_size": cfg.batch_size,
            "learning_rate": cfg.learning_rate,
            "server_round": rnd,
        }
        updates: list[tuple[list[np.ndarray], int]] = []
        for client in clients:
            new_w, n, _fit_metrics = client.fit(weights, config)
            updates.append((new_w, n))
        weights = fedavg(updates)
        logs.append({"round": rnd, "clients": len(updates)})
    return weights, logs


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(as_plain_floats(report), indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARIS M1 federated vs local fraud models")
    parser.add_argument(
        "--dataset",
        default="synthetic",
        choices=("synthetic", "ulb", "paysim", "ieee-cis"),
    )
    parser.add_argument("--banks", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--partition", default="auto", choices=("auto", "temporal", "dirichlet"))
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Cap rows (ULB laptop runs: 40000)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="JSON report path (default m1_metrics_<dataset>.json in data/processed)",
    )
    args = parser.parse_args(argv)

    cfg = TrainConfig(
        num_banks=args.banks,
        rounds=args.rounds,
        local_epochs=args.epochs,
        max_train_rows=args.max_rows,
    )
    report = run_experiment(dataset=args.dataset, cfg=cfg, partition=args.partition)
    out = Path(args.out) if args.out else DATA_PROCESSED / f"m1_metrics_{args.dataset}.json"
    write_report(report, out)

    print(
        json.dumps(
            as_plain_floats(
                {
                    "dataset": report["dataset"],
                    "partition": report["partition"],
                    "mean_local_auc": report["mean_local"]["auc"],
                    "global_auc": report["global"]["auc"],
                    "global_pr_auc": report["global"]["pr_auc"],
                    "global_beats_mean_local_auc": report["global_beats_mean_local_auc"],
                    "metrics_path": str(out),
                    "checkpoint": report["checkpoint"],
                }
            ),
            indent=2,
        )
    )

    if report["dataset"] == "synthetic" and not report["global_beats_mean_local_auc"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
