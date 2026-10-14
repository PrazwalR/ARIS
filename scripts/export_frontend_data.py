"""Measure everything the frontend shows and write it to
frontend/app/public/data/metrics.json.

The design system's rule is "real, measured values only", so the frontend
renders this file and nothing else: per-bank and global holdout metrics with
real ROC points, per-round federated AUC, the DP-SGD epsilon/AUC sweep, secure
aggregation's mask-cancellation error, FedAvg vs Krum under a live Byzantine
attacker, bus load tests, and the test suite's own pass count -- each run here,
now, on this commit.

    python scripts/export_frontend_data.py              # everything
    python scripts/export_frontend_data.py --skip-tests --skip-kafka
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import roc_curve

from aris.attestation import PublisherKeyring
from aris.bus import InMemoryRiskBus
from aris.fl.byzantine import auc_under_attack
from aris.fl.config import TrainConfig
from aris.fl.evaluation import EvaluationRun, train_and_score
from aris.fl.fedavg import fedavg
from aris.fl.metrics import classification_metrics, mean_metric
from aris.fl.privacy_sweep import run_sweep
from aris.fl.secure_agg import secure_fedavg
from aris.loadtest import run_load_test

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "app" / "public" / "data" / "metrics.json"
METRIC_KEYS = ("auc", "pr_auc", "precision_at_0_5", "recall_at_0_5", "accuracy_at_0_5")
MAX_ROC_POINTS = 160

DATASETS: dict[str, dict[str, Any]] = {
    "ulb": {
        "label": "ULB credit-card fraud",
        # The full dataset, uncapped: the loader's row cap keeps every fraud row
        # and drops legitimate ones, which inflates the fraud rate ~10x and with
        # it PR-AUC and precision. The page reports the real 0.17% rate.
        "cfg": TrainConfig(rounds=5, local_epochs=2),
        "note": (
            "Real, PCA-anonymised card transactions, the full dataset; banks split by time window."
        ),
    },
    "synthetic": {
        "label": "Synthetic, five banks",
        "cfg": TrainConfig(),
        "note": "Each bank's fraud depends on a different feature; no bank sees the others'.",
    },
}


def _quiet(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """The training loop prints progress tables; keep them out of this
    script's own output."""
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args, **kwargs)


def _roc_points(y: Any, scores: Any) -> list[list[float]]:
    fpr, tpr, _ = roc_curve(y, scores, drop_intermediate=True)
    if len(fpr) > MAX_ROC_POINTS:
        idx = np.unique(np.linspace(0, len(fpr) - 1, MAX_ROC_POINTS).round().astype(int))
        fpr, tpr = fpr[idx], tpr[idx]
    return [[round(float(a), 4), round(float(b), 4)] for a, b in zip(fpr, tpr, strict=True)]


def _metrics(y: Any, scores: Any) -> dict[str, float]:
    m = classification_metrics(y, scores)
    return {k: round(float(m[k]), 4) for k in METRIC_KEYS}


def _dataset_block(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    cfg: TrainConfig = spec["cfg"]
    run: EvaluationRun = _quiet(train_and_score, name, cfg)
    y = run.y_holdout
    locals_ = []
    full_local_metrics = []
    for bank, n, pos, scores in zip(
        run.bank_ids, run.bank_train_sizes, run.bank_train_positives, run.local_scores, strict=True
    ):
        full_local_metrics.append(classification_metrics(y, scores))
        locals_.append(
            {
                "bank_id": bank,
                "train_rows": n,
                "train_fraud": pos,
                "metrics": _metrics(y, scores),
                "roc": _roc_points(y, scores),
            }
        )
    return {
        "label": spec["label"],
        "note": spec["note"],
        "partition": run.partition,
        "config": {
            "banks": cfg.num_banks,
            "rounds": cfg.rounds,
            "local_epochs": cfg.local_epochs,
            "max_rows": cfg.max_train_rows,
        },
        "holdout": {"rows": len(y), "fraud": int(y.sum())},
        "local": locals_,
        "mean_local": {k: round(mean_metric(full_local_metrics, k), 4) for k in METRIC_KEYS},
        "global": {
            "metrics": _metrics(y, run.global_scores),
            "roc": _roc_points(y, run.global_scores),
        },
        "rounds": [
            {"round": r["round"], "auc": round(r["auc"], 4), "pr_auc": round(r["pr_auc"], 4)}
            for r in run.rounds
        ],
    }


def _privacy_block() -> dict[str, Any]:
    sweep = _quiet(run_sweep, "synthetic")
    rows = [
        {
            "noise_multiplier": r["noise_multiplier"],
            "epsilon": None if r["epsilon"] is None else round(r["epsilon"], 2),
            "global_auc": round(r["global_auc"], 4),
        }
        for r in sweep["rows"]
    ]
    return {
        "dataset": "synthetic",
        "delta": sweep["delta"],
        "mean_local_auc": round(sweep["mean_local_auc_m1"], 4),
        "rows": rows,
    }


def _secure_agg_block() -> dict[str, Any]:
    rng = np.random.default_rng(7)
    updates = [
        ([rng.normal(size=(8, 16)).astype(np.float32), rng.normal(size=16).astype(np.float32)], 100)
        for _ in range(5)
    ]
    plain = fedavg(updates)
    masked = secure_fedavg(updates, seed=42)
    err = max(float(np.max(np.abs(a - b))) for a, b in zip(plain, masked, strict=True))
    return {"banks": 5, "max_abs_error_vs_plain_fedavg": err}


def _robustness_block() -> dict[str, Any]:
    return {
        "attack": "one bank sends -1000x the global weights every round and over-reports "
        "its example count",
        "rounds": 6,
        "honest_banks": 5,
        "fedavg_auc": round(_quiet(auc_under_attack, "fedavg"), 4),
        "krum_auc": round(_quiet(auc_under_attack, "krum"), 4),
    }


def _load_block(skip_kafka: bool) -> dict[str, Any]:
    keyring = PublisherKeyring()
    mem = run_load_test(InMemoryRiskBus(keyring), keyring, 20, 200, backend_label="memory")
    out: dict[str, Any] = {"memory": _load_summary(mem), "kafka": None}
    if skip_kafka:
        return out
    try:
        from aris.kafka_bus import KafkaRiskBus, load_kafka_config

        bootstrap, registry = load_kafka_config()
        kr = PublisherKeyring()
        bus = KafkaRiskBus(kr, bootstrap, registry)
        try:
            out["kafka"] = _load_summary(run_load_test(bus, kr, 10, 50, backend_label="kafka"))
        finally:
            bus.close()
    except Exception as exc:  # Kafka down is a recorded fact, not a crash
        out["kafka_unavailable"] = type(exc).__name__
    return out


def _load_summary(report: Any) -> dict[str, Any]:
    d = asdict(report)
    return {
        "publishers": d["num_publishers"],
        "signals": d["total_signals"],
        "throughput_per_s": round(d["publish_throughput_per_s"], 1),
        "lost_updates": d["lost_updates"],
        "lookup_p99_ms": round(d["lookup_latency"]["p99_ms"], 2),
    }


def _tests_block() -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:warnings"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?)", tail)}
    return {
        "command": "pytest",
        "summary": re.sub(r" in [\d.]+s.*$", "", tail.strip("= ")),
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "skipped": counts.get("skipped", 0),
        "duration_s": round(time.monotonic() - started, 1),
        "exit_code": proc.returncode,
    }


def _checks_block() -> dict[str, bool]:
    def ok(cmd: list[str]) -> bool:
        return subprocess.run(cmd, cwd=ROOT, capture_output=True).returncode == 0

    return {
        "ruff": ok([sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"]),
        "mypy_strict": ok([sys.executable, "-m", "mypy"]),
    }


def _commit() -> str | None:
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
            ).stdout.strip()
            or None
        )
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-kafka", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)

    data: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _commit(),
        "datasets": {},
    }
    for name, spec in DATASETS.items():
        print(f"[export] training + scoring {name} ...", flush=True)
        data["datasets"][name] = _dataset_block(name, spec)
    print("[export] DP-SGD sweep ...", flush=True)
    data["privacy"] = _privacy_block()
    data["secure_aggregation"] = _secure_agg_block()
    print("[export] Byzantine attack, FedAvg vs Krum ...", flush=True)
    data["robustness"] = _robustness_block()
    print("[export] bus load tests ...", flush=True)
    data["load"] = _load_block(args.skip_kafka)
    if not args.skip_tests:
        print("[export] running the test suite, ruff and mypy ...", flush=True)
        data["tests"] = _tests_block()
        data["checks"] = _checks_block()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, indent=1) + "\n")
    ulb = data["datasets"]["ulb"]["global"]["metrics"]
    print(f"[export] wrote {args.out}  (ULB global AUC {ulb['auc']:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
