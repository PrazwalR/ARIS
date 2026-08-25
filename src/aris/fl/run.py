"""M1 experiment: local-only baselines vs Flower FedAvg global model.

Raw transaction rows stay inside each BankFlowerClient. The server only
sees weight arrays + example counts.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

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
from aris.fl.privacy import epsilon_for_dpsgd
from aris.fl.scorer import save_weights
from aris.fl.secure_agg import secure_fedavg


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
    global_weights, round_log, dp_steps_per_client = _federated_train(clients, cfg)
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
            "secure_aggregation": cfg.secure_agg,
            "differential_privacy": _dp_report(cfg, dp_steps_per_client),
        },
        "rounds_log": round_log,
        "model_version": "v0.4-fl",
    }


def _dp_report(cfg: TrainConfig, dp_steps_per_client: dict[str, int]) -> dict[str, Any]:
    if not cfg.dp_enabled:
        return {"enabled": False}
    # Report the worst case: privacy protection is only as strong as the most
    # exposed client, so use the largest per-client step count, not the mean.
    max_steps = max(dp_steps_per_client.values()) if dp_steps_per_client else 0
    epsilon = epsilon_for_dpsgd(
        noise_multiplier=cfg.noise_multiplier,
        num_steps=max_steps,
        delta=cfg.dp_delta,
    )
    return {
        "enabled": True,
        "mechanism": "DP-SGD (per-example gradient clipping + Gaussian noise)",
        "noise_multiplier": cfg.noise_multiplier,
        "max_grad_norm": cfg.max_grad_norm,
        "delta": cfg.dp_delta,
        "max_client_steps": max_steps,
        "epsilon": epsilon,
        "epsilon_is_conservative_upper_bound": True,
        "steps_per_client": dp_steps_per_client,
    }


def _train_local_baselines(
    clients: list[BankFlowerClient],
    x_te: npt.NDArray[Any],
    y_te: npt.NDArray[Any],
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
) -> tuple[list[npt.NDArray[Any]], list[dict[str, Any]], dict[str, int]]:
    weights = clients[0].get_parameters({})
    logs = []
    dp_steps_per_client: dict[str, int] = {c.bank_id: 0 for c in clients}
    for rnd in range(1, cfg.rounds + 1):
        config = {
            "local_epochs": cfg.local_epochs,
            "batch_size": cfg.batch_size,
            "learning_rate": cfg.learning_rate,
            "server_round": rnd,
            "dp_enabled": cfg.dp_enabled,
            "max_grad_norm": cfg.max_grad_norm,
            "noise_multiplier": cfg.noise_multiplier,
        }
        updates: list[tuple[list[npt.NDArray[Any]], int]] = []
        for client in clients:
            new_w, n, fit_metrics = client.fit(weights, config)
            updates.append((new_w, n))
            if cfg.dp_enabled:
                if "dp_steps" not in fit_metrics:
                    # Never default this to 0: it silently understates
                    # dp_steps_per_client, which silently understates the reported
                    # epsilon -- a wrong "this run is more private than it is"
                    # result is worse than a crash here.
                    raise KeyError(
                        f"dp_enabled=True but fit() for {client.bank_id!r} returned no "
                        "'dp_steps' metric -- train_local_dp must always report it. "
                        "Refusing to silently default to 0 and understate epsilon."
                    )
                dp_steps_per_client[client.bank_id] += int(fit_metrics["dp_steps"])
        weights = secure_fedavg(updates, seed=cfg.seed + rnd) if cfg.secure_agg else fedavg(updates)
        logs.append({"round": rnd, "clients": len(updates)})
    return weights, logs, dp_steps_per_client


def write_report(report: dict[str, Any], path: Path) -> None:
    """Write the JSON report atomically: a failure partway through (disk full,
    process killed) must never leave a truncated/corrupt file at `path`, and must
    never leave a stale-but-complete-looking old report silently overwritten by a
    half-written new one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(as_plain_floats(report), indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


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
    parser.add_argument(
        "--dp",
        action="store_true",
        help="M2: enable DP-SGD (per-example clipping + Gaussian noise)",
    )
    parser.add_argument("--noise-multiplier", type=float, default=1.0)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument("--dp-delta", type=float, default=1e-5)
    parser.add_argument(
        "--secure-agg",
        action="store_true",
        help="M2: aggregate via pairwise-masked secure sum instead of plain FedAvg",
    )
    args = parser.parse_args(argv)

    cfg = TrainConfig(
        num_banks=args.banks,
        rounds=args.rounds,
        local_epochs=args.epochs,
        max_train_rows=args.max_rows,
        dp_enabled=args.dp,
        noise_multiplier=args.noise_multiplier,
        max_grad_norm=args.max_grad_norm,
        dp_delta=args.dp_delta,
        secure_agg=args.secure_agg,
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
                    "differential_privacy": report["privacy"]["differential_privacy"],
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
