"""M5: feature drift monitoring (Evidently) between a reference distribution
(what the model was trained/validated on) and a current batch (what it is
scoring in production). A model trained on one distribution silently degrades
as the input distribution shifts; this makes that shift visible and
quantified instead of only showing up later as a drop in caught fraud.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from evidently import Report
from evidently.presets import DataDriftPreset

DEFAULT_P_VALUE_THRESHOLD = 0.05


@dataclass(frozen=True)
class FeatureDrift:
    feature_name: str
    p_value: float
    drifted: bool


@dataclass(frozen=True)
class DriftReport:
    features: tuple[FeatureDrift, ...]
    drifted_share: float  # fraction of features flagged as drifted
    n_reference: int
    n_current: int

    @property
    def any_drift(self) -> bool:
        return any(f.drifted for f in self.features)

    @property
    def drifted_features(self) -> tuple[str, ...]:
        return tuple(f.feature_name for f in self.features if f.drifted)


def check_drift(
    reference: npt.NDArray[Any],
    current: npt.NDArray[Any],
    feature_names: list[str],
    p_value_threshold: float = DEFAULT_P_VALUE_THRESHOLD,
) -> DriftReport:
    """Compare `current` against `reference` column by column (Kolmogorov-Smirnov
    test, Evidently's default for numeric features) and report which columns
    have drifted at `p_value_threshold`.
    """
    if reference.shape[1] != len(feature_names) or current.shape[1] != len(feature_names):
        raise ValueError("reference/current column count must match feature_names length")
    if not (0.0 < p_value_threshold < 1.0):
        raise ValueError("p_value_threshold must be in (0, 1)")

    ref_df = pd.DataFrame(np.asarray(reference), columns=feature_names)
    cur_df = pd.DataFrame(np.asarray(current), columns=feature_names)

    report = Report(metrics=[DataDriftPreset()])
    snapshot = report.run(current_data=cur_df, reference_data=ref_df)
    result = snapshot.dict()

    features = []
    for metric in result["metrics"]:
        name = metric["metric_name"]
        if not name.startswith("ValueDrift(column="):
            continue
        column = metric["config"]["column"]
        p_value = float(metric["value"])
        features.append(
            FeatureDrift(
                feature_name=column,
                p_value=p_value,
                drifted=p_value < p_value_threshold,
            )
        )
    # Evidently orders metrics by internal id, not input column order -- report
    # in the caller's declared feature order so results are reproducible to read.
    order = {name: i for i, name in enumerate(feature_names)}
    features.sort(key=lambda f: order[f.feature_name])

    drifted_share = sum(1 for f in features if f.drifted) / len(features) if features else 0.0
    return DriftReport(
        features=tuple(features),
        drifted_share=drifted_share,
        n_reference=len(reference),
        n_current=len(current),
    )
