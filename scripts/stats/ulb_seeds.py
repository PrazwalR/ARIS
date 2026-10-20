"""ULB, full dataset: global FedAvg vs single-bank models over 5 seeds, plus a
stratified bootstrap 95% interval on seed 42's holdout.

    python scripts/stats/ulb_seeds.py   ->  docs/results/ulb_seeds.json
"""

from __future__ import annotations

import numpy as np
from _common import write
from sklearn.metrics import average_precision_score, roc_auc_score

from aris.fl.config import TrainConfig
from aris.fl.evaluation import train_and_score

SEEDS = (42, 43, 44, 45, 46)
BOOTSTRAP_RESAMPLES = 2000


def _ci(values: list[float]) -> list[float]:
    lo, hi = np.percentile(values, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def main() -> None:
    runs = {}
    scores = {}
    for seed in SEEDS:
        r = train_and_score("ulb", TrainConfig(rounds=5, local_epochs=2, seed=seed))
        y = r.y_holdout
        runs[seed] = {
            "global_auc": roc_auc_score(y, r.global_scores),
            "global_pr_auc": average_precision_score(y, r.global_scores),
            "local_auc": [roc_auc_score(y, s) for s in r.local_scores],
            "local_pr_auc": [average_precision_score(y, s) for s in r.local_scores],
        }
        scores[seed] = (y, r.global_scores, r.local_scores)
        print(seed, round(runs[seed]["global_auc"], 4), round(runs[seed]["global_pr_auc"], 4))

    def summary(key: str, local: bool = False) -> dict[str, float]:
        vals = [float(np.mean(v[key])) if local else v[key] for v in runs.values()]
        return {"mean": round(float(np.mean(vals)), 4), "sd": round(float(np.std(vals, ddof=1)), 4)}

    y, g, local = scores[42]
    rng = np.random.default_rng(0)
    pos, neg = np.flatnonzero(y == 1), np.flatnonzero(y == 0)
    boot: dict[str, list[float]] = {k: [] for k in ("gauc", "gpr", "lauc", "lpr", "dpr", "dauc")}
    for _ in range(BOOTSTRAP_RESAMPLES):
        i = np.concatenate([rng.choice(pos, len(pos)), rng.choice(neg, len(neg))])
        yy = y[i]
        ga, gp = roc_auc_score(yy, g[i]), average_precision_score(yy, g[i])
        la = float(np.mean([roc_auc_score(yy, s[i]) for s in local]))
        lp = float(np.mean([average_precision_score(yy, s[i]) for s in local]))
        for k, v in zip(boot, (ga, gp, la, lp, gp - lp, ga - la), strict=True):
            boot[k].append(v)

    write(
        "ulb_seeds.json",
        {
            "setup": "ULB full dataset, 5 banks (time windows), 5 rounds x 2 local epochs, "
            "holdout = last 20% by time (56,962 rows, 75 fraud)",
            "seeds": list(SEEDS),
            "per_seed": {
                s: {
                    k: (round(v, 4) if isinstance(v, float) else [round(x, 4) for x in v])
                    for k, v in r.items()
                }
                for s, r in runs.items()
            },
            "five_seed_summary": {
                "global_auc": summary("global_auc"),
                "global_pr_auc": summary("global_pr_auc"),
                "mean_local_auc": summary("local_auc", local=True),
                "mean_local_pr_auc": summary("local_pr_auc", local=True),
            },
            "seed42_bootstrap_95ci": {
                "resamples": BOOTSTRAP_RESAMPLES,
                "global_auc": _ci(boot["gauc"]),
                "global_pr_auc": _ci(boot["gpr"]),
                "mean_local_auc": _ci(boot["lauc"]),
                "mean_local_pr_auc": _ci(boot["lpr"]),
                "pr_auc_gain": _ci(boot["dpr"]),
                "auc_gain": _ci(boot["dauc"]),
            },
        },
    )


if __name__ == "__main__":
    main()
