import pytest

from aris.fl.config import TrainConfig
from aris.fl.run import run_experiment


def _dp_cfg(**overrides):
    base = {
        "num_banks": 5,
        "rounds": 8,
        "local_epochs": 4,
        "seed": 42,
        "dp_enabled": True,
        "max_grad_norm": 5.0,
        "noise_multiplier": 1.0,
    }
    base.update(overrides)
    return TrainConfig(**base)


def test_dp_training_still_converges():
    # M2 "done when": training still converges with a documented epsilon.
    report = run_experiment("synthetic", cfg=_dp_cfg())
    dp = report["privacy"]["differential_privacy"]
    assert dp["enabled"] is True
    assert dp["epsilon"] > 0
    assert dp["epsilon"] < float("inf")
    # Meaningfully better than random (0.5) and better than an un-federated
    # local-only model would get on its own shard -- DP-SGD noise has not wiped
    # out the federated learning signal.
    assert report["global"]["auc"] > 0.55


def test_dp_utility_degrades_relative_to_m1_baseline():
    # "Show the accuracy drop vs M1": same schedule/seed, only DP toggled.
    no_dp = TrainConfig(num_banks=5, rounds=8, local_epochs=4, seed=42, dp_enabled=False)
    baseline = run_experiment("synthetic", cfg=no_dp)
    dp_report = run_experiment("synthetic", cfg=_dp_cfg(noise_multiplier=8.0))

    assert dp_report["privacy"]["differential_privacy"]["enabled"] is True
    assert baseline["privacy"]["differential_privacy"]["enabled"] is False
    # DP-SGD with real noise should not do *better* than the noiseless baseline.
    assert dp_report["global"]["auc"] <= baseline["global"]["auc"]
    # ...but should still be clearly better than a coin flip.
    assert dp_report["global"]["auc"] > 0.55


def test_more_noise_costs_less_epsilon_in_a_real_run():
    low_noise = run_experiment("synthetic", cfg=_dp_cfg(noise_multiplier=1.0))
    high_noise = run_experiment("synthetic", cfg=_dp_cfg(noise_multiplier=8.0))
    eps_low = low_noise["privacy"]["differential_privacy"]["epsilon"]
    eps_high = high_noise["privacy"]["differential_privacy"]["epsilon"]
    assert eps_high < eps_low


def test_dp_disabled_by_default_report_shape():
    report = run_experiment("synthetic", cfg=TrainConfig(rounds=2, local_epochs=1))
    dp = report["privacy"]["differential_privacy"]
    assert dp == {"enabled": False}
    assert report["privacy"]["secure_aggregation"] is False


def test_secure_agg_produces_same_model_as_plain_fedavg():
    # Masking cancels exactly, so toggling secure_agg must not change the
    # trained model -- only who is able to see individual updates in transit.
    plain = run_experiment("synthetic", cfg=TrainConfig(rounds=3, local_epochs=2, seed=1))
    secure = run_experiment(
        "synthetic", cfg=TrainConfig(rounds=3, local_epochs=2, seed=1, secure_agg=True)
    )
    assert secure["global"]["auc"] == pytest.approx(plain["global"]["auc"], abs=1e-6)
    assert secure["privacy"]["secure_aggregation"] is True
