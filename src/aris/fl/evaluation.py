"""Train local baselines and the federated model on one split, keeping the raw
holdout scores so callers can draw ROC/PR curves and confusion matrices.

``aris.fl.run.run_experiment`` keeps only summary metrics; anything that needs
the curves themselves (the metrics report, the frontend's data export) uses
this instead. The split, partitioning and training steps are the same ones
``run_experiment`` uses, so the two agree to the last digit -- see
``tests/test_fl_evaluation.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from aris.fl.client import BankFlowerClient
from aris.fl.config import TrainConfig
from aris.fl.datasets import TabularFraud, load_dataset
from aris.fl.model import new_model, predict_scores, set_weights, train_local
from aris.fl.partition import (
    dirichlet_shards,
    equal_contiguous_shards,
    heterogeneous_bank_shards,
    holdout_split,
    named_banks,
    pooled_holdout_from_shards,
    temporal_shards,
)


@dataclass
class EvaluationRun:
    dataset: str
    partition: str
    bank_ids: list[str]
    bank_train_sizes: list[int]
    bank_train_positives: list[int]
    y_holdout: npt.NDArray[Any]
    local_scores: list[npt.NDArray[Any]]
    global_scores: npt.NDArray[Any]
    # Global-model holdout metrics after each federated round.
    rounds: list[dict[str, Any]]


def build_split(
    dataset: str, cfg: TrainConfig, partition: str = "auto"
) -> tuple[TabularFraud, str, list[BankFlowerClient], npt.NDArray[Any], npt.NDArray[Any]]:
    """Exactly ``run_experiment``'s dataset, holdout and partition steps."""
    data = load_dataset(
        dataset, max_rows=cfg.max_train_rows, seed=cfg.seed, num_banks=cfg.num_banks
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
            data.x, data.y, frac=cfg.holdout_frac, seed=cfg.seed, time_col=data.time
        )
        if part == "temporal" and data.time is not None:
            t_tr = np.arange(len(y_tr), dtype=np.float32)
            shards = temporal_shards(x_tr, y_tr, t_tr, cfg.num_banks)
        elif part == "heterogeneous":
            shards = heterogeneous_bank_shards(x_tr, y_tr, cfg.num_banks, cfg.seed)
        else:
            shards = dirichlet_shards(x_tr, y_tr, cfg.num_banks, cfg.dirichlet_alpha, cfg.seed)
    if any(len(s[1]) == 0 for s in shards):
        raise RuntimeError("A bank shard is empty. Use fewer banks or a larger dataset.")
    clients = [
        BankFlowerClient(banks[i], shards[i][0], shards[i][1], hidden=cfg.hidden, seed=cfg.seed + i)
        for i in range(cfg.num_banks)
    ]
    return data, part, clients, x_te, y_te


def _train_local_models(clients: list[BankFlowerClient], cfg: TrainConfig) -> list[Any]:
    """Same steps as ``run._train_local_baselines`` (one epoch at a time, same
    per-epoch seeds), so the local scores match the report's local metrics."""
    total_epochs = cfg.local_epochs * cfg.rounds
    models = []
    for i, client in enumerate(clients):
        model = new_model(client.x.shape[1], hidden=cfg.hidden, seed=cfg.seed + 100 + i)
        for ep in range(1, total_epochs + 1):
            train_local(
                model,
                client.x,
                client.y,
                epochs=1,
                batch_size=cfg.batch_size,
                lr=cfg.learning_rate,
                seed=cfg.seed + i + ep * 1000,
                momentum=cfg.momentum,
            )
        models.append(model)
    return models


def train_and_score(
    dataset: str, cfg: TrainConfig | None = None, partition: str = "auto"
) -> EvaluationRun:
    from aris.fl.run import _federated_train

    cfg = cfg or TrainConfig()
    data, part, clients, x_te, y_te = build_split(dataset, cfg, partition)
    local_models = _train_local_models(clients, cfg)
    local_scores = [predict_scores(m, x_te) for m in local_models]
    weights, _, _, round_trajectories = _federated_train(clients, cfg, x_te=x_te, y_te=y_te)
    global_model = new_model(clients[0].x.shape[1], hidden=cfg.hidden, seed=cfg.seed)
    set_weights(global_model, weights)
    return EvaluationRun(
        dataset=data.name,
        partition=part,
        bank_ids=[c.bank_id for c in clients],
        bank_train_sizes=[len(c.y) for c in clients],
        bank_train_positives=[int(c.y.sum()) for c in clients],
        y_holdout=y_te,
        local_scores=local_scores,
        global_scores=predict_scores(global_model, x_te),
        rounds=[{"round": r["round"], **r["global"]} for r in round_trajectories],
    )
