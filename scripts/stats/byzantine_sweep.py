"""Byzantine attack by attacker fraction: 6 honest banks, 0-4 attackers, FedAvg vs
coordinate median vs Krum, 3 seeds. Each attacker sends -1000x the global
weights and claims 1,000 examples (aris.fl.byzantine.ByzantineClient).

    python scripts/stats/byzantine_sweep.py   ->  docs/results/byzantine_sweep.json
"""

from __future__ import annotations

import numpy as np
from _common import write

from aris.fl.byzantine import ByzantineClient
from aris.fl.client import BankFlowerClient
from aris.fl.config import TrainConfig
from aris.fl.datasets import make_synthetic
from aris.fl.metrics import classification_metrics
from aris.fl.model import new_model, predict_scores, set_weights
from aris.fl.partition import equal_contiguous_shards, pooled_holdout_from_shards
from aris.fl.run import _aggregate

HONEST = 6
ROUNDS = 6
SEEDS = (42, 43, 44)
STRATEGIES = ("fedavg", "coordinate_median", "krum")


def run(strategy: str, attackers: int, seed: int) -> float | None:
    n = HONEST + attackers
    byz = max(attackers, 1)
    if strategy == "krum" and n <= 2 * byz + 2:
        return None  # outside Krum's guarantee; TrainConfig refuses it
    data = make_synthetic(n_banks=HONEST, seed=seed)
    shards = equal_contiguous_shards(data.x, data.y, HONEST)
    shards, x_te, y_te = pooled_holdout_from_shards(shards, frac=0.2, seed=seed)
    clients: list[BankFlowerClient] = [
        BankFlowerClient(f"BANK-{i}", shards[i][0], shards[i][1], hidden=16, seed=seed + i)
        for i in range(HONEST)
    ]
    clients += [
        ByzantineClient(f"EVIL-{j}", shards[0][0], shards[0][1], hidden=16, seed=99 + j)
        for j in range(attackers)
    ]
    cfg = TrainConfig(
        num_banks=n,
        aggregation_strategy=strategy,
        num_byzantine=byz if strategy == "krum" else attackers or 1,
        seed=seed,
    )
    weights = clients[0].get_parameters({})
    for rnd in range(1, ROUNDS + 1):
        config = {"local_epochs": 3, "batch_size": 128, "learning_rate": 0.08, "server_round": rnd}
        updates = [c.fit(weights, config)[:2] for c in clients]
        weights = _aggregate(updates, cfg, seed_offset=rnd)
    model = new_model(x_te.shape[1], hidden=16, seed=seed)
    set_weights(model, weights)
    scores = predict_scores(model, x_te)
    if not np.all(np.isfinite(scores)):
        return 0.5
    return round(float(classification_metrics(y_te, scores)["auc"]), 4)


def main() -> None:
    rows = []
    for attackers in range(5):
        row: dict[str, object] = {
            "attackers": attackers,
            "share": round(attackers / (HONEST + attackers), 3),
        }
        for strategy in STRATEGIES:
            vals = [run(strategy, attackers, s) for s in SEEDS]
            if None in vals:
                row[strategy] = None
                continue
            nums = [v for v in vals if v is not None]
            row[strategy] = {
                "per_seed": nums,
                "mean": round(float(np.mean(nums)), 3),
                "sd": round(float(np.std(nums, ddof=1)), 3),
            }
        print(row)
        rows.append(row)
    write(
        "byzantine_sweep.json",
        {
            "honest_banks": HONEST,
            "rounds": ROUNDS,
            "seeds": list(SEEDS),
            "rows": rows,
            "note": "null = not run: Krum needs more than 2f+2 banks",
        },
    )


if __name__ == "__main__":
    main()
