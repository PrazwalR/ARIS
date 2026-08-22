"""Small NumPy MLP so M1 runs without PyTorch (Python 3.14-friendly)."""

from __future__ import annotations

import numpy as np


class FraudMLP:
    def __init__(self, n_features: int, hidden: int = 16, seed: int = 42) -> None:
        rng = np.random.default_rng(seed)
        scale = np.sqrt(2 / n_features)
        self.w1 = rng.normal(0, scale, size=(n_features, hidden)).astype(np.float32)
        self.b1 = np.zeros(hidden, dtype=np.float32)
        self.w2 = rng.normal(0, np.sqrt(2 / hidden), size=(hidden, 1)).astype(np.float32)
        self.b2 = np.zeros(1, dtype=np.float32)

    def parameters(self) -> list[np.ndarray]:
        return [self.w1, self.b1, self.w2, self.b2]


def get_weights(model: FraudMLP) -> list[np.ndarray]:
    return [p.copy() for p in model.parameters()]


def set_weights(model: FraudMLP, weights: list[np.ndarray]) -> None:
    model.w1, model.b1, model.w2, model.b2 = (w.copy() for w in weights)


def new_model(n_features: int, hidden: int = 16, seed: int = 42) -> FraudMLP:
    return FraudMLP(n_features, hidden=hidden, seed=seed)


def _forward(model: FraudMLP, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    h = np.maximum(0.0, x @ model.w1 + model.b1)
    logits = (h @ model.w2 + model.b2).reshape(-1)
    return logits, h, x


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -20, 20)
    return 1.0 / (1.0 + np.exp(-z))


def predict_scores(model: FraudMLP, x: np.ndarray, batch_size: int = 1024) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    parts = []
    for i in range(0, len(x), batch_size):
        logits, _, _ = _forward(model, x[i : i + batch_size])
        parts.append(_sigmoid(logits))
    return np.concatenate(parts, axis=0)


def train_local(
    model: FraudMLP,
    x: np.ndarray,
    y: np.ndarray,
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


def risk_score_from_proba(proba: np.ndarray) -> np.ndarray:
    return np.clip(np.round(proba * 100), 0, 100).astype(int)
