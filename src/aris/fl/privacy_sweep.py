"""M2 deliverable: privacy-utility table (epsilon vs AUC).

Runs the same federated training as `aris.fl.run` across a range of DP-SGD noise
multipliers and records the resulting (epsilon, global AUC) pair for each, so the
accuracy cost of a given privacy budget is visible directly rather than asserted.

    python -m aris.fl.privacy_sweep --dataset synthetic

Writes data/processed/m2_privacy_utility_<dataset>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from aris.fl.config import DATA_PROCESSED, TrainConfig
from aris.fl.fedavg import as_plain_floats
from aris.fl.run import run_experiment

# Calibrated against this MLP's actual per-example gradient scale on the synthetic
# dataset (median L2 norm ~1.7, p75 ~3.5): a clip norm far below that (e.g. 1.0)
# over-clips the majority of examples and destroys the learning signal before
# noise is even added. 5.0 keeps clipping meaningful without doing that; see
# docs/PROJECT.md M2 section for the calibration run this came from.
DEFAULT_MAX_GRAD_NORM = 5.0
DEFAULT_NOISE_MULTIPLIERS = (0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0, 60.0)


def run_sweep(
    dataset: str = "synthetic",
    noise_multipliers: tuple[float, ...] = DEFAULT_NOISE_MULTIPLIERS,
    max_grad_norm: float = DEFAULT_MAX_GRAD_NORM,
    base_cfg: TrainConfig | None = None,
) -> dict[str, Any]:
    base_cfg = base_cfg or TrainConfig()
    baseline_cfg = replace(base_cfg, dp_enabled=False)
    baseline = run_experiment(dataset, cfg=baseline_cfg)

    rows: list[dict[str, Any]] = [
        {
            "noise_multiplier": None,
            "epsilon": None,
            "global_auc": baseline["global"]["auc"],
            "global_pr_auc": baseline["global"]["pr_auc"],
            "label": "M1 baseline (no DP)",
        }
    ]
    for nm in noise_multipliers:
        cfg = replace(
            base_cfg,
            dp_enabled=True,
            noise_multiplier=nm,
            max_grad_norm=max_grad_norm,
        )
        report = run_experiment(dataset, cfg=cfg)
        dp = report["privacy"]["differential_privacy"]
        rows.append(
            {
                "noise_multiplier": nm,
                "epsilon": dp["epsilon"],
                "global_auc": report["global"]["auc"],
                "global_pr_auc": report["global"]["pr_auc"],
                "label": f"DP-SGD noise_multiplier={nm}",
            }
        )

    return {
        "dataset": dataset,
        "max_grad_norm": max_grad_norm,
        "delta": TrainConfig().dp_delta,
        "mean_local_auc_m1": baseline["mean_local"]["auc"],
        "epsilon_is_conservative_upper_bound": True,
        "rows": rows,
    }


def write_sweep(sweep: dict[str, Any], path: Path) -> None:
    """Write the JSON report atomically (see `aris.fl.run.write_report`): a failure
    partway through the write must never leave a truncated/corrupt file at `path`.
    Note this is only called once, after every noise multiplier in the sweep has
    already run to completion -- if any single `run_experiment` call raises, that
    exception propagates out of `run_sweep` before this function is ever reached,
    so a mid-sweep failure cannot produce a report that looks complete but is
    silently missing rows either.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(as_plain_floats(sweep), indent=2)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARIS M2 privacy-utility sweep (epsilon vs AUC)")
    parser.add_argument("--dataset", default="synthetic", choices=("synthetic", "ulb"))
    parser.add_argument("--banks", type=int, default=5)
    parser.add_argument("--rounds", type=int, default=8)
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--max-grad-norm", type=float, default=DEFAULT_MAX_GRAD_NORM)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    base_cfg = TrainConfig(num_banks=args.banks, rounds=args.rounds, local_epochs=args.epochs)
    sweep = run_sweep(dataset=args.dataset, max_grad_norm=args.max_grad_norm, base_cfg=base_cfg)

    out = Path(args.out) if args.out else DATA_PROCESSED / f"m2_privacy_utility_{args.dataset}.json"
    write_sweep(sweep, out)

    print(f"{'noise_multiplier':>17} {'epsilon':>12} {'global_auc':>11}  label")
    for row in sweep["rows"]:
        nm = "-" if row["noise_multiplier"] is None else f"{row['noise_multiplier']:.1f}"
        eps = "-" if row["epsilon"] is None else f"{row['epsilon']:.3f}"
        print(f"{nm:>17} {eps:>12} {row['global_auc']:>11.4f}  {row['label']}")
    print(f"\nwritten to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
