"""Map local/global model outputs to RiskSignal fields (handoff to M3/M4)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from aris.fl.model import (
    FraudMLP,
    get_weights,
    new_model,
    predict_scores,
    risk_score_from_proba,
    set_weights,
)


class UnscorableInput(ValueError):
    """A row the model cannot score meaningfully (wrong width, NaN/inf, or so
    large the network overflows). Refused rather than scored: a NaN score
    would otherwise round to risk_score 0 -- the "clean" answer."""


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
        weights: list[npt.NDArray[Any]],
        hidden: int = 16,
        model_version: str = "v0.4-fl",
    ) -> FraudScorer:
        model = new_model(n_features, hidden=hidden, seed=0)
        expected = [w.shape for w in get_weights(model)]
        actual = [np.asarray(w).shape for w in weights]
        if actual != expected:
            # A checkpoint for a different feature set or architecture would
            # otherwise load silently and score garbage.
            raise ValueError(
                f"checkpoint layer shapes {actual} do not match a model with "
                f"n_features={n_features}, hidden={hidden} ({expected})"
            )
        set_weights(model, weights)
        return cls(model, model_version=model_version)

    @property
    def n_features(self) -> int:
        return int(self.model.w1.shape[0])

    def predict_proba(self, x: npt.NDArray[Any]) -> npt.NDArray[Any]:
        with np.errstate(over="ignore", invalid="ignore"):
            arr = np.asarray(x, dtype=np.float64)
        if arr.ndim != 2 or arr.shape[1] != self.n_features:
            raise UnscorableInput(
                f"expected rows of {self.n_features} features, got shape {arr.shape}"
            )
        if not np.all(np.isfinite(arr)) or np.any(np.abs(arr) > np.finfo(np.float32).max):
            raise UnscorableInput("features must be finite float32 values")
        with np.errstate(over="ignore", invalid="ignore"):
            proba = predict_scores(self.model, arr.astype(np.float32))
        if not np.all(np.isfinite(proba)):
            raise UnscorableInput("model output is not finite for this input (overflow)")
        return proba

    def score_row(self, x_row: npt.NDArray[Any]) -> ScoreResult:
        x = np.asarray(x_row, dtype=np.float64).reshape(1, -1)
        p = float(self.predict_proba(x)[0])
        score = int(risk_score_from_proba(np.array([p]))[0])
        conf = float(max(p, 1.0 - p))
        return ScoreResult(
            risk_score=score,
            confidence=round(conf, 4),
            probability=p,
            model_version=self.model_version,
        )


def save_weights(path: Path | str, weights: list[npt.NDArray[Any]], meta: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, Any] = {f"w{i}": np.asarray(w) for i, w in enumerate(weights)}
    # Metadata as a JSON string, not a pickled object: a checkpoint can come
    # from another party (a global model from the aggregator), and loading a
    # pickle executes whatever it contains.
    arrays["meta_json"] = np.array(json.dumps(meta))
    np.savez(path, **arrays)


def load_weights(path: Path | str) -> tuple[list[npt.NDArray[Any]], dict[str, Any]]:
    with np.load(path, allow_pickle=False) as blob:
        keys = sorted((k for k in blob.files if k.startswith("w")), key=lambda k: int(k[1:]))
        weights = [blob[k] for k in keys]
        if "meta_json" in blob.files:
            meta = json.loads(str(blob["meta_json"]))
        elif "meta" in blob.files:
            raise ValueError(
                f"{path} stores metadata as a pickle (old format); refusing to load it. "
                "Re-train to regenerate the checkpoint."
            )
        else:
            meta = {}
    return weights, dict(meta)
