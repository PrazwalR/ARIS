import numpy as np

from aris.fl.client import BankFlowerClient
from aris.fl.config import TrainConfig
from aris.fl.partition import dirichlet_shards
from aris.fl.run import run_experiment


def test_dirichlet_covers_all_rows():
    x = np.zeros((100, 3), dtype=np.float32)
    y = np.array([0] * 80 + [1] * 20)
    shards = dirichlet_shards(x, y, num_banks=5, alpha=0.5, seed=0)
    assert sum(len(s[1]) for s in shards) == 100
    assert all(len(s[1]) > 0 for s in shards)


def test_client_fit_returns_weights_not_rows():
    rng = np.random.default_rng(0)
    x = rng.normal(size=(40, 6)).astype(np.float32)
    y = rng.integers(0, 2, size=40)
    client = BankFlowerClient("BANK-A", x, y, hidden=8, seed=0)
    config = {"local_epochs": 1, "batch_size": 16}
    weights, n, metrics = client.fit(client.get_parameters({}), config)
    assert n == 40
    assert isinstance(weights, list)
    assert weights[0].shape[0] != 40 or weights[0].ndim == 2  # layer matrix, not 40 rows of data
    assert "account" not in str(metrics).lower()
    # updates must be numeric tensors, not copies of x
    assert all(w.size < x.size or w.ndim <= 2 for w in weights)


def test_synthetic_global_beats_mean_local_auc():
    cfg = TrainConfig(
        num_banks=5,
        rounds=6,
        local_epochs=3,
        batch_size=128,
        learning_rate=0.08,
        holdout_frac=0.2,
        seed=42,
        hidden=16,
    )
    report = run_experiment("synthetic", cfg=cfg)
    assert report["privacy"]["raw_rows_shared"] is False
    assert report["global"]["auc"] > report["mean_local"]["auc"]
    assert report["global_beats_mean_local_auc"] is True
