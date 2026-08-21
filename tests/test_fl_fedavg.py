import numpy as np

from aris.fl.fedavg import fedavg
from aris.fl.metrics import classification_metrics
from aris.fl.model import get_weights, new_model, set_weights


def test_fedavg_is_weighted_mean():
    a = [np.ones((2, 2), dtype=np.float32)]
    b = [np.zeros((2, 2), dtype=np.float32)]
    out = fedavg([(a, 1), (b, 3)])
    np.testing.assert_allclose(out[0], 0.25 * np.ones((2, 2)), rtol=1e-5)


def test_metrics_on_perfect_scores():
    y = np.array([0, 0, 1, 1])
    s = np.array([0.1, 0.2, 0.8, 0.9])
    m = classification_metrics(y, s)
    assert m["auc"] == 1.0
    assert m["pr_auc"] == 1.0


def test_get_set_weights_roundtrip():
    m = new_model(4, hidden=8, seed=0)
    w = get_weights(m)
    assert all(isinstance(layer, np.ndarray) for layer in w)
    m2 = new_model(4, hidden=8, seed=1)
    set_weights(m2, w)
    for a, b in zip(get_weights(m2), w, strict=True):
        np.testing.assert_array_equal(a, b)
