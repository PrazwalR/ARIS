"""Map local/global model outputs to RiskSignal fields (handoff to M3/M4)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from aris.fl.model import FraudMLP, new_model, predict_scores, risk_score_from_proba, set_weights


@dataclass
class ScoreResult:
    risk_score: int
    confidence: float
    probability: float
    model_version: str = "v0.4-fl"


class FraudScorer:
    def __init__(self, model: FraudMLP, model_version: str = "v0.4-fl") -> None:
        self.model = model
        self.model_version = model_version

    @classmethod
    def from_weights(
        cls,
        n_features: int,
        weights: list[np.ndarray],
        hidden: int = 16,
        model_version: str = "v0.4-fl",
    ) -> FraudScorer:
        model = new_model(n_features, hidden=hidden, seed=0)
        set_weights(model, weights)
        return cls(model, model_version=model_version)

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return predict_scores(self.model, x)

    def score_row(self, x_row: np.ndarray) -> ScoreResult:
        x = np.asarray(x_row, dtype=np.float32).reshape(1, -1)
        p = float(self.predict_proba(x)[0])
        score = int(risk_score_from_proba(np.array([p]))[0])
        conf = float(max(p, 1.0 - p))
        return ScoreResult(
            risk_score=score,
            confidence=round(conf, 4),
            probability=p,
            model_version=self.model_version,
        )


def save_weights(path: Path | str, weights: list[np.ndarray], meta: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, Any] = {f"w{i}": w for i, w in enumerate(weights)}
    arrays["meta"] = np.array([meta], dtype=object)
    np.savez(path, **arrays)


def load_weights(path: Path | str) -> tuple[list[np.ndarray], dict[str, Any]]:
    blob = np.load(path, allow_pickle=True)
    keys = sorted(k for k in blob.files if k.startswith("w"))
    weights = [blob[k] for k in keys]
    meta = blob["meta"][0] if "meta" in blob.files else {}
    return weights, dict(meta)
