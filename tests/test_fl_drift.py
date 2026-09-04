import numpy as np
import pytest

from aris.fl.drift import check_drift


def test_shifted_feature_is_flagged_unshifted_is_not():
    rng = np.random.default_rng(0)
    ref = np.column_stack([rng.normal(0, 1, 500), rng.normal(0, 1, 500)])
    cur = ref.copy()[:400]
    cur = np.column_stack([rng.normal(3, 1, 400), rng.normal(0, 1, 400)])  # f0 shifted, f1 not

    report = check_drift(ref, cur, feature_names=["f0", "f1"])
    assert report.drifted_features == ("f0",)
    assert report.any_drift is True
    assert report.drifted_share == pytest.approx(0.5)
    assert report.n_reference == 500
    assert report.n_current == 400


def test_identical_distributions_report_no_drift():
    rng = np.random.default_rng(1)
    ref = rng.normal(0, 1, size=(500, 3))
    cur = rng.normal(0, 1, size=(500, 3))  # same distribution, different draw
    report = check_drift(ref, cur, feature_names=["a", "b", "c"])
    assert report.any_drift is False
    assert report.drifted_share == 0.0


def test_features_reported_in_caller_order():
    rng = np.random.default_rng(2)
    ref = rng.normal(0, 1, size=(200, 3))
    cur = rng.normal(0, 1, size=(200, 3))
    report = check_drift(ref, cur, feature_names=["zeta", "alpha", "mu"])
    assert [f.feature_name for f in report.features] == ["zeta", "alpha", "mu"]


def test_rejects_mismatched_column_count():
    ref = np.zeros((10, 3))
    cur = np.zeros((10, 3))
    with pytest.raises(ValueError):
        check_drift(ref, cur, feature_names=["a", "b"])


def test_rejects_bad_p_value_threshold():
    ref = np.zeros((10, 2))
    cur = np.zeros((10, 2))
    with pytest.raises(ValueError):
        check_drift(ref, cur, feature_names=["a", "b"], p_value_threshold=0.0)
    with pytest.raises(ValueError):
        check_drift(ref, cur, feature_names=["a", "b"], p_value_threshold=1.0)
