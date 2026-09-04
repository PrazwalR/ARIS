"""M5: per-prediction SHAP explanations, mapped to the `reason_codes` vocabulary
risk signals already travel with (`aris.schema`).

**Honesty note on the reason-code mapping.** Mapping a feature to a reason code
(e.g. "this feature means high transaction velocity") requires knowing what the
feature actually represents. Neither dataset this repo trains on supports that
in general: the synthetic dataset's features (`f0`..`f7`) are anonymous by
construction, and ULB's `V1`..`V28` are PCA-anonymized for the same privacy
reasons this whole project cares about -- only its `log_amount` feature is
genuinely interpretable. `FraudExplainer` therefore takes an *explicit,
caller-supplied* `reason_code_map`; nothing here silently invents domain
meaning a feature doesn't have. Where a top-contributing feature has no entry
in that map, the explanation reports the honest thing: which feature, and how
much it moved the score, without pretending that maps to a named fraud
pattern. A real deployment scoring named, non-anonymized features (its own
`is_new_beneficiary`, `txns_last_hour`, ...) would populate a map that means
something; the empty default here does not pretend to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import shap

from aris.fl.scorer import FraudScorer
from aris.schema import MAX_REASON_CODES

# Always a valid reason_codes token (see aris.schema._TOKEN_PATTERN) and always
# available as a fallback: a flagged transfer must carry at least one code, but
# an unmapped feature must not be dressed up as a named fraud pattern.
FALLBACK_REASON_CODE = "model_flagged_pattern"

DEFAULT_BACKGROUND_SAMPLE_SIZE = 50


@dataclass(frozen=True)
class FeatureExplanation:
    """One feature's contribution to one prediction."""

    feature_name: str
    shap_value: float  # signed: positive pushes the score toward fraud
    feature_value: float


@dataclass(frozen=True)
class ScoreExplanation:
    reason_codes: tuple[str, ...]
    top_features: tuple[FeatureExplanation, ...]
    base_value: float  # the explainer's reference (background mean) score


class FraudExplainer:
    """SHAP attribution over a `FraudScorer`, model-agnostic (permutation-based):
    the scorer wraps a small custom NumPy MLP, not a SHAP-native model type, so
    this explains it through its `predict_proba` function plus a background
    sample rather than inspecting internal weights directly.
    """

    def __init__(
        self,
        scorer: FraudScorer,
        background: npt.NDArray[Any],
        feature_names: list[str],
        reason_code_map: dict[str, str] | None = None,
        max_reason_codes: int = MAX_REASON_CODES,
        background_sample_size: int = DEFAULT_BACKGROUND_SAMPLE_SIZE,
        seed: int = 0,
    ) -> None:
        if len(feature_names) == 0:
            raise ValueError("feature_names must be non-empty")
        background = np.asarray(background, dtype=np.float32)
        if background.shape[1] != len(feature_names):
            raise ValueError(
                f"background has {background.shape[1]} columns, "
                f"feature_names has {len(feature_names)}"
            )
        self.scorer = scorer
        self.feature_names = feature_names
        self.reason_code_map = reason_code_map or {}
        self.max_reason_codes = max_reason_codes

        if len(background) > background_sample_size:
            rng = np.random.default_rng(seed)
            idx = rng.choice(len(background), size=background_sample_size, replace=False)
            background = background[idx]
        self._background = background
        self._explainer = shap.Explainer(self._predict, background, feature_names=feature_names)

    def _predict(self, x: npt.NDArray[Any]) -> npt.NDArray[Any]:
        return self.scorer.predict_proba(np.asarray(x, dtype=np.float32))

    def explain_row(self, x_row: npt.NDArray[Any], top_k: int = 3) -> ScoreExplanation:
        """Explain one row. `top_k` bounds how many features are inspected for
        reason-code mapping and reported as evidence -- not a claim that only
        `top_k` features mattered, just how many an analyst needs to see.
        """
        x = np.asarray(x_row, dtype=np.float32).reshape(1, -1)
        result = self._explainer(x)
        values = np.asarray(result.values[0], dtype=np.float64).reshape(-1)
        base_value = float(np.asarray(result.base_values).reshape(-1)[0])

        order = np.argsort(-np.abs(values))[:top_k]
        top_features = tuple(
            FeatureExplanation(
                feature_name=self.feature_names[i],
                shap_value=float(values[i]),
                feature_value=float(x[0, i]),
            )
            for i in order
        )

        codes: list[str] = []
        for feat in top_features:
            if feat.shap_value <= 0:
                continue  # only features pushing the score toward fraud explain a flag
            code = self.reason_code_map.get(feat.feature_name)
            if code and code not in codes:
                codes.append(code)
        if not codes:
            codes.append(FALLBACK_REASON_CODE)

        return ScoreExplanation(
            reason_codes=tuple(codes[: self.max_reason_codes]),
            top_features=top_features,
            base_value=base_value,
        )
