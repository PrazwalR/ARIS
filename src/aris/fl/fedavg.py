"""FedAvg using Flower's official aggregator — no Ray (Windows-safe)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np

flower_aggregate: Callable[[list[tuple[list[np.ndarray], int]]], list[np.ndarray]] | None
try:
    from flwr.server.strategy.aggregate import aggregate as flower_aggregate
except ImportError:  # pragma: no cover
    flower_aggregate = None


def fedavg(updates: list[tuple[list[np.ndarray], int]]) -> list[np.ndarray]:
    """Weighted average of client weight lists. `updates` is (weights, n_examples)."""
    if not updates:
        raise ValueError("FedAvg needs at least one client update")
    if flower_aggregate is not None:
        return flower_aggregate(updates)
    return _python_fedavg(updates)


def _python_fedavg(updates: list[tuple[list[np.ndarray], int]]) -> list[np.ndarray]:
    total = float(sum(n for _, n in updates))
    n_layers = len(updates[0][0])
    out: list[np.ndarray] = []
    for i in range(n_layers):
        acc = None
        for weights, n in updates:
            term = weights[i].astype(np.float64) * (n / total)
            acc = term if acc is None else acc + term
        assert acc is not None
        out.append(acc.astype(updates[0][0][i].dtype, copy=False))
    return out


def as_plain_floats(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: as_plain_floats(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [as_plain_floats(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, float) and obj != obj:  # NaN
        return None
    return obj
