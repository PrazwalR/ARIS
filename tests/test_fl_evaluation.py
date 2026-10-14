"""aris.fl.evaluation must agree with aris.fl.run to the last digit -- the
curves the frontend and reports draw are only honest if they come from the
same models the summary metrics describe."""

from __future__ import annotations

import pytest

from aris.fl.byzantine import auc_under_attack
from aris.fl.evaluation import train_and_score
from aris.fl.metrics import classification_metrics
from aris.fl.run import run_experiment


@pytest.fixture(scope="module")
def both():
    return train_and_score("synthetic"), run_experiment("synthetic")


def test_global_scores_match_the_report(both):
    ev, report = both
    assert classification_metrics(ev.y_holdout, ev.global_scores) == report["global"]


def test_every_local_model_matches_the_report(both):
    ev, report = both
    for scores, bank in zip(ev.local_scores, report["banks"], strict=True):
        assert classification_metrics(ev.y_holdout, scores) == bank["local"]


def test_per_round_trajectory_ends_at_the_final_model(both):
    ev, report = both
    assert len(ev.rounds) == report["rounds"]
    assert ev.rounds[-1]["auc"] == pytest.approx(report["global"]["auc"])


def test_library_attack_reproduces_the_robustness_property():
    assert auc_under_attack("fedavg") < 0.55
    assert auc_under_attack("krum") > 0.55
