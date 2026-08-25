import numpy as np

from aris.fl.fedavg import fedavg
from aris.fl.secure_agg import secure_fedavg, secure_sum


def test_masks_cancel_exactly_in_sum():
    values = [
        [np.array([1.0, 2.0], dtype=np.float64)],
        [np.array([3.0, -1.0], dtype=np.float64)],
        [np.array([0.5, 0.5], dtype=np.float64)],
    ]
    masked, true_sum = secure_sum(values, seed=0, mask_scale=10.0)
    server_sum = np.sum([m[0] for m in masked], axis=0)
    np.testing.assert_allclose(server_sum, true_sum[0], atol=1e-9)
    np.testing.assert_allclose(true_sum[0], np.array([4.5, 1.5]), atol=1e-9)


def test_single_masked_value_differs_from_raw_value():
    values = [
        [np.array([1.0, 2.0], dtype=np.float64)],
        [np.array([3.0, -1.0], dtype=np.float64)],
    ]
    masked, _ = secure_sum(values, seed=0, mask_scale=10.0)
    # Client 0's masked output should not equal its raw contribution -- an
    # honest-but-curious aggregator holding only masked[0] cannot read it off.
    assert not np.allclose(masked[0][0], values[0][0])
    assert not np.allclose(masked[1][0], values[1][0])


def test_secure_sum_rejects_empty_input():
    import pytest

    with pytest.raises(ValueError):
        secure_sum([], seed=0)


def test_secure_fedavg_matches_plain_fedavg():
    rng = np.random.default_rng(0)
    updates = [
        ([rng.normal(size=(4, 3)).astype(np.float32)], 100),
        ([rng.normal(size=(4, 3)).astype(np.float32)], 250),
        ([rng.normal(size=(4, 3)).astype(np.float32)], 40),
    ]
    plain = fedavg(updates)
    secure = secure_fedavg(updates, seed=7)
    np.testing.assert_allclose(secure[0], plain[0], atol=1e-4)


def test_secure_fedavg_rejects_empty_updates():
    import pytest

    with pytest.raises(ValueError):
        secure_fedavg([], seed=0)
