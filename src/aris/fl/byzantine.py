"""A persistent Byzantine participant, and the experiment that measures what it
does to each aggregation strategy (M6+, ``aris.fl.robust_agg``).

The attacker ignores its data and returns ``scale`` times the global weights
every round, and over-reports its example count -- FedAvg weights by the
self-reported count with no way to check it, so a real attacker lies about it.
``tests/test_fl_robust_agg.py`` asserts the outcome; this module lets the
frontend export and the reports show the same measured numbers.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from aris.fl.client import BankFlowerClient
from aris.fl.config import TrainConfig
from aris.fl.datasets import make_synthetic
from aris.fl.metrics import classification_metrics
from aris.fl.model import new_model, predict_scores, set_weights
from aris.fl.partition import equal_contiguous_shards, pooled_holdout_from_shards


class ByzantineClient(BankFlowerClient):
    def __init__(
        self, *args: Any, scale: float = -1000.0, fake_n: int = 1000, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._scale = scale
        self._fake_n = fake_n

    def fit(
        self, parameters: list[npt.NDArray[Any]], config: dict[str, Any]
    ) -> tuple[list[npt.NDArray[Any]], int, dict[str, Any]]:
        del config  # ignores the training instructions entirely
        return [p.astype(np.float32) * self._scale for p in parameters], self._fake_n, {}


def auc_under_attack(strategy: str, rounds: int = 6, num_honest: int = 5) -> float:
    """Global-model holdout AUC after ``rounds`` rounds with one Byzantine
    client present every round, aggregated with ``strategy``."""
    from aris.fl.run import _aggregate

    data = make_synthetic(n_banks=num_honest, seed=42)
    shards = equal_contiguous_shards(data.x, data.y, num_honest)
    shards, x_te, y_te = pooled_holdout_from_shards(shards, frac=0.2, seed=42)
    clients: list[BankFlowerClient] = [
        BankFlowerClient(f"BANK-{i}", shards[i][0], shards[i][1], hidden=16, seed=42 + i)
        for i in range(num_honest)
    ]
    clients.append(ByzantineClient("BANK-EVIL", shards[0][0], shards[0][1], hidden=16, seed=99))
    cfg = TrainConfig(
        num_banks=num_honest + 1, aggregation_strategy=strategy, num_byzantine=1, seed=42
    )
    weights = clients[0].get_parameters({})
    for rnd in range(1, rounds + 1):
        config = {"local_epochs": 3, "batch_size": 128, "learning_rate": 0.08, "server_round": rnd}
        updates = []
        for client in clients:
            new_w, n, _ = client.fit(weights, config)
            updates.append((new_w, n))
        weights = _aggregate(updates, cfg, seed_offset=rnd)
    model = new_model(x_te.shape[1], hidden=16, seed=42)
    set_weights(model, weights)
    return float(classification_metrics(y_te, predict_scores(model, x_te))["auc"])
