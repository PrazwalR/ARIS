"""Small NumPy MLP so M1 runs without PyTorch (Python 3.14-friendly)."""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt

from aris.fl.privacy import clip_and_noise_gradients


class FraudMLP:
    def __init__(self, n_features: int, hidden: int = 16, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        scale = np.sqrt(2 / n_features)
        self.w1: npt.NDArray[Any] = rng.normal(0, scale, size=(n_features, hidden)).astype(
            np.float32
        )
        self.b1: npt.NDArray[Any] = np.zeros(hidden, dtype=np.float32)
        self.w2: npt.NDArray[Any] = rng.normal(0, np.sqrt(2 / hidden), size=(hidden, 1)).astype(
            np.float32
        )
        self.b2: npt.NDArray[Any] = np.zeros(1, dtype=np.float32)

    def parameters(self) -> list[npt.NDArray[Any]]:
        return [self.w1, self.b1, self.w2, self.b2]


def get_weights(model: FraudMLP) -> list[npt.NDArray[Any]]:
    return [p.copy() for p in model.parameters()]


def set_weights(model: FraudMLP, weights: list[npt.NDArray[Any]]) -> None:
    model.w1, model.b1, model.w2, model.b2 = (w.copy() for w in weights)


def new_model(n_features: int, hidden: int = 16, seed: int = 42) -> FraudMLP:
    return FraudMLP(n_features, hidden=hidden, seed=seed)


def _forward(
    model: FraudMLP, x: npt.NDArray[Any]
) -> tuple[npt.NDArray[Any], npt.NDArray[Any], npt.NDArray[Any]]:
    h = np.maximum(0.0, x @ model.w1 + model.b1)
    logits = (h @ model.w2 + model.b2).reshape(-1)
    return logits, h, x


def _sigmoid(z: npt.NDArray[Any]) -> npt.NDArray[Any]:
    z = np.clip(z, -20, 20)
    result: npt.NDArray[Any] = 1.0 / (1.0 + np.exp(-z))
    return result


def predict_scores(
    model: FraudMLP, x: npt.NDArray[Any], batch_size: int = 1024
) -> npt.NDArray[Any]:
    x = np.asarray(x, dtype=np.float32)
    parts = []
    for i in range(0, len(x), batch_size):
        logits, _, _ = _forward(model, x[i : i + batch_size])
        parts.append(_sigmoid(logits))
    return np.concatenate(parts, axis=0)


def train_local(
    model: FraudMLP,
    x: npt.NDArray[Any],
    y: npt.NDArray[Any],
    epochs: int,
    batch_size: int,
    lr: float,
    seed: int = 0,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)
    pos = max(float(y.sum()), 1.0)
    neg = max(float(len(y) - y.sum()), 1.0)
    pos_weight = neg / pos
    n = len(y)
    last = 0.0
    for _ in range(epochs):
        order = rng.permutation(n)
        for start in range(0, n, batch_size):
            idx = order[start : start + batch_size]
            xb, yb = x[idx], y[idx]
            logits, h, _ = _forward(model, xb)
            p = _sigmoid(logits)
            # weighted BCE gradient on logits
            w = np.where(yb > 0.5, pos_weight, 1.0).astype(np.float32)
            grad_z = (w * (p - yb) / max(len(yb), 1)).astype(np.float32)
            last = float(np.mean(w * -(yb * np.log(p + 1e-8) + (1 - yb) * np.log(1 - p + 1e-8))))

            grad_w2 = h.T @ grad_z.reshape(-1, 1)
            grad_b2 = grad_z.sum(axis=0, keepdims=True)
            grad_h = grad_z.reshape(-1, 1) @ model.w2.T
            grad_h *= (h > 0).astype(np.float32)
            grad_w1 = xb.T @ grad_h
            grad_b1 = grad_h.sum(axis=0)

            model.w2 -= lr * grad_w2.astype(np.float32)
            model.b2 -= lr * grad_b2.reshape(-1).astype(np.float32)
            model.w1 -= lr * grad_w1.astype(np.float32)
            model.b1 -= lr * grad_b1.astype(np.float32)
    return {"loss": last}


def train_local_dp(
    model: FraudMLP,
    x: npt.NDArray[Any],
    y: npt.NDArray[Any],
    epochs: int,
    batch_size: int,
    lr: float,
    max_grad_norm: float,
    noise_multiplier: float,
    seed: int = 0,
) -> dict[str, float]:
    """DP-SGD variant of `train_local` (Abadi et al. 2016): every mini-batch step
    clips each example's gradient to `max_grad_norm` (L2, across all parameters
    combined) before summing, then adds Gaussian noise calibrated to
    `noise_multiplier * max_grad_norm`. See `aris.fl.privacy` for the matching
    accountant that turns (noise_multiplier, step count) into (epsilon, delta).

    Returns `dp_steps` alongside `loss` so the caller can accumulate the total
    step count actually taken (varies with shard size) for accounting.
    """
    rng = np.random.default_rng(seed)
    x = np.asarray(x, dtype=np.float32)
    y = np.asarray(y, dtype=np.float32)
    pos = max(float(y.sum()), 1.0)
    neg = max(float(len(y) - y.sum()), 1.0)
    pos_weight = neg / pos
    n = len(y)
    last = 0.0
    steps = 0
    for _ in range(epochs):
        order = rng.permutation(n)
        for start in range(0, n, batch_size):
            idx = order[start : start + batch_size]
            xb, yb = x[idx], y[idx]
            b = len(yb)
            logits, h, _ = _forward(model, xb)
            p = _sigmoid(logits)
            w = np.where(yb > 0.5, pos_weight, 1.0).astype(np.float32)
            # Per-example, NOT batch-averaged: DP-SGD clips each example's own
            # gradient before any reduction, so the average must wait until after
            # clipping and noising.
            grad_z = (w * (p - yb)).astype(np.float32)
            last = float(np.mean(w * -(yb * np.log(p + 1e-8) + (1 - yb) * np.log(1 - p + 1e-8))))

            per_example_grad_w2 = np.einsum("bh,bo->bho", h, grad_z.reshape(-1, 1))
            per_example_grad_b2 = grad_z.reshape(-1, 1)
            grad_h = grad_z.reshape(-1, 1) @ model.w2.T
            grad_h = grad_h * (h > 0).astype(np.float32)
            per_example_grad_w1 = np.einsum("bf,bh->bfh", xb, grad_h)
            per_example_grad_b1 = grad_h

            gw1, gb1, gw2, gb2 = clip_and_noise_gradients(
                [
                    per_example_grad_w1,
                    per_example_grad_b1,
                    per_example_grad_w2,
                    per_example_grad_b2,
                ],
                max_grad_norm=max_grad_norm,
                noise_multiplier=noise_multiplier,
                rng=rng,
            )

            model.w1 -= lr * (gw1 / b).astype(np.float32)
            model.b1 -= lr * (gb1 / b).astype(np.float32)
            model.w2 -= lr * (gw2 / b).astype(np.float32)
            model.b2 -= lr * (gb2.reshape(-1) / b).astype(np.float32)
            steps += 1
    return {"loss": last, "dp_steps": float(steps)}


def risk_score_from_proba(proba: npt.NDArray[Any]) -> npt.NDArray[Any]:
    result: npt.NDArray[Any] = np.clip(np.round(proba * 100), 0, 100).astype(int)
    return result
