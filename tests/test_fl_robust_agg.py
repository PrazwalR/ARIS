"""M6+: proves the robustness property, not just that the functions run.

A synthetic "Byzantine" client that ignores its local data and sends an
extreme, adversarially-scaled update every round stands in for a compromised
or malicious FL participant -- the threat robust aggregation exists for,
distinct from a client that merely trained on bad *data* (which plain FedAvg
already handles reasonably, since one bank's noisy data is diluted by four
honest banks' clean data in the average).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from aris.fl.client import BankFlowerClient
from aris.fl.config import TrainConfig
from aris.fl.datasets import make_synthetic
from aris.fl.metrics import classification_metrics
from aris.fl.model import new_model, predict_scores, set_weights
from aris.fl.partition import equal_contiguous_shards, pooled_holdout_from_shards
from aris.fl.robust_agg import coordinate_median, krum
from aris.fl.run import _aggregate, run_experiment


class _ByzantineClient(BankFlowerClient):
    """Test-only: ignores local training entirely and returns an extreme,
    adversarially-scaled update every round -- a fully malicious participant,
    not a client with merely noisy data.

    Also claims a large example count (`fake_n`), not the honest `n=1` a
    less realistic attacker might send: FedAvg weights its mean by each
    client's *self-reported* count with no way to verify it, so a real
    attacker maximizing damage lies about that too. Krum and
    coordinate_median ignore declared counts entirely (see
    aris.fl.robust_agg), which is exactly why that lie doesn't help against
    them.
    """

    def __init__(
        self, *args: Any, scale: float = -1000.0, fake_n: int = 1000, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._scale = scale
        self._fake_n = fake_n

    def fit(
        self, parameters: list[np.ndarray], _config: dict[str, Any]
    ) -> tuple[list[np.ndarray], int, dict[str, Any]]:
        adversarial = [p.astype(np.float32) * self._scale for p in parameters]
        return adversarial, self._fake_n, {"bank_id": self.bank_id}


def _mixed_clients(num_honest: int = 5, byzantine_scale: float = -1000.0):
    data = make_synthetic(n_banks=num_honest, seed=42)
    shards = equal_contiguous_shards(data.x, data.y, num_honest)
    shards, x_te, y_te = pooled_holdout_from_shards(shards, frac=0.2, seed=42)
    honest = [
        BankFlowerClient(f"BANK-{i}", shards[i][0], shards[i][1], hidden=16, seed=42 + i)
        for i in range(num_honest)
    ]
    byzantine = _ByzantineClient(
        "BANK-EVIL", shards[0][0], shards[0][1], hidden=16, seed=99, scale=byzantine_scale
    )
    return honest, byzantine, x_te, y_te


def _one_round_updates(clients, weights, config):
    updates = []
    for client in clients:
        new_w, n, _ = client.fit(weights, config)
        updates.append((new_w, n))
    return updates


def _mean_layers(weight_lists):
    """Unweighted per-layer mean across a list of weight-lists. Each entry has
    several differently-shaped layers (w1, b1, w2, b2), so a plain np.mean
    over the whole nested list fails on shape mismatch -- average layer by
    layer instead.
    """
    n_layers = len(weight_lists[0])
    return [np.mean([w[layer] for w in weight_lists], axis=0) for layer in range(n_layers)]


class TestUnitLevelRobustness:
    """Synthetic honest cluster + one extreme outlier, no training involved --
    isolates the aggregation math from the FL machinery around it.
    """

    def _cluster_and_outlier(self, n_honest=6, seed=0):
        rng = np.random.default_rng(seed)
        center = rng.normal(size=(4, 3)).astype(np.float32)
        updates = []
        for _ in range(n_honest):
            noisy = [center[i] + rng.normal(scale=0.01, size=center[i].shape) for i in range(2)]
            updates.append(([w.astype(np.float32) for w in noisy], 100))
        outlier = [w * 1000.0 for w in updates[0][0]]
        updates.append((outlier, 100))
        return updates, center[:2]

    def test_fedavg_is_dragged_by_a_single_outlier(self):
        from aris.fl.fedavg import fedavg

        updates, honest_center = self._cluster_and_outlier()
        result = fedavg(updates)
        # The mean of 6 tight-cluster values and 1 value 1000x larger is
        # dominated by the outlier -- proving the vulnerability exists before
        # asserting the fix works.
        assert np.abs(result[0]).mean() > 50 * np.abs(honest_center[0]).mean()

    def test_krum_selects_an_honest_update_not_the_outlier(self):
        updates, honest_center = self._cluster_and_outlier()
        result = krum(updates, num_byzantine=1)
        # Krum returns one client's update verbatim; it must be close to the
        # honest cluster, not the 1000x-scaled outlier.
        assert np.abs(result[0] - honest_center[0]).mean() < 1.0

    def test_krum_rejects_n_too_small_for_the_bound(self):
        updates, _ = self._cluster_and_outlier(n_honest=1)  # n=2, needs n > 2*1+2=4
        with pytest.raises(ValueError):
            krum(updates, num_byzantine=1)

    def test_coordinate_median_stays_near_the_honest_cluster(self):
        updates, honest_center = self._cluster_and_outlier()
        result = coordinate_median(updates)
        assert np.abs(result[0] - honest_center[0]).mean() < 1.0

    def test_coordinate_median_rejects_empty_input(self):
        with pytest.raises(ValueError):
            coordinate_median([])


class TestEndToEndPoisoningResistance:
    """Real trained honest clients + one fully adversarial one, aggregated
    through _aggregate the same way aris.fl.run._federated_train does.
    """

    def test_fedavg_round_is_corrupted_by_the_byzantine_client(self):
        honest, byzantine, _x_te, _y_te = _mixed_clients(num_honest=5)
        clients = [*honest, byzantine]
        weights = clients[0].get_parameters({})
        config = {"local_epochs": 3, "batch_size": 128, "learning_rate": 0.08, "server_round": 1}
        updates = _one_round_updates(clients, weights, config)

        cfg = TrainConfig(num_banks=6, aggregation_strategy="fedavg")
        result = _aggregate(updates, cfg, seed_offset=1)
        # -1000x scaling (plus an inflated declared example count -- see
        # _ByzantineClient) on one of six updates should visibly drag the
        # weighted mean away from what the 5 honest clients alone would give.
        honest_only_mean = _mean_layers([w for w, _ in updates[:5]])
        assert np.abs(result[0] - honest_only_mean[0]).mean() > 10.0

    def test_krum_round_is_not_corrupted_by_the_byzantine_client(self):
        honest, byzantine, _x_te, _y_te = _mixed_clients(num_honest=5)
        clients = [*honest, byzantine]
        weights = clients[0].get_parameters({})
        config = {"local_epochs": 3, "batch_size": 128, "learning_rate": 0.08, "server_round": 1}
        updates = _one_round_updates(clients, weights, config)

        cfg = TrainConfig(num_banks=6, aggregation_strategy="krum", num_byzantine=1)
        result = _aggregate(updates, cfg, seed_offset=1)
        # Krum's pick must be one of the 5 honest updates verbatim, never the
        # -1000x-scaled Byzantine one.
        matches_some_honest = any(
            all(np.array_equal(result[i], honest_w[i]) for i in range(len(result)))
            for honest_w, _ in updates[:5]
        )
        assert matches_some_honest

    def test_global_model_survives_persistent_attack_under_krum_not_fedavg(self):
        """The headline property: train several rounds with one Byzantine
        client present every round, and check the *resulting model's AUC* --
        not just that one round's aggregate looks reasonable.
        """
        honest, byzantine, x_te, y_te = _mixed_clients(num_honest=5, byzantine_scale=-1000.0)

        def train_under(strategy: str) -> float:
            clients = [
                BankFlowerClient(c.bank_id, c.x, c.y, hidden=16, seed=c.seed) for c in honest
            ] + [_ByzantineClient(byzantine.bank_id, byzantine.x, byzantine.y, hidden=16, seed=99)]
            weights = clients[0].get_parameters({})
            cfg = TrainConfig(num_banks=6, aggregation_strategy=strategy, num_byzantine=1, seed=42)
            for rnd in range(1, 7):
                config = {
                    "local_epochs": 3,
                    "batch_size": 128,
                    "learning_rate": 0.08,
                    "server_round": rnd,
                }
                updates = _one_round_updates(clients, weights, config)
                weights = _aggregate(updates, cfg, seed_offset=rnd)
            model = new_model(x_te.shape[1], hidden=16, seed=42)
            set_weights(model, weights)
            scores = predict_scores(model, x_te)
            return float(classification_metrics(y_te, scores)["auc"])

        fedavg_auc = train_under("fedavg")
        krum_auc = train_under("krum")

        # A persistent -1000x attacker degrades plain FedAvg to (near-)random;
        # Krum, immune to that one client's influence, keeps learning.
        assert fedavg_auc < 0.55
        assert krum_auc > 0.55


class TestConfigValidation:
    def test_rejects_unknown_strategy(self):
        with pytest.raises(ValueError):
            TrainConfig(aggregation_strategy="not_a_real_strategy")

    def test_rejects_krum_with_too_few_banks_for_num_byzantine(self):
        with pytest.raises(ValueError):
            TrainConfig(num_banks=4, aggregation_strategy="krum", num_byzantine=1)

    def test_rejects_secure_agg_combined_with_robust_strategy(self):
        with pytest.raises(ValueError):
            TrainConfig(secure_agg=True, aggregation_strategy="krum")

    def test_fedavg_with_secure_agg_is_fine(self):
        TrainConfig(secure_agg=True, aggregation_strategy="fedavg")  # must not raise


def test_run_experiment_smoke_test_all_strategies():
    """Confirms each strategy is wired all the way through run_experiment
    (not just _aggregate in isolation) and produces a usable model."""
    for strategy, kwargs in [
        ("fedavg", {}),
        ("krum", {"num_byzantine": 1}),
        ("coordinate_median", {}),
    ]:
        cfg = TrainConfig(
            num_banks=5, rounds=3, local_epochs=2, seed=42, aggregation_strategy=strategy, **kwargs
        )
        report = run_experiment("synthetic", cfg=cfg)
        assert report["global"]["auc"] > 0.5
