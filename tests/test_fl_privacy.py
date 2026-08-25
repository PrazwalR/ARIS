import math

import numpy as np
import pytest

from aris.fl.privacy import (
    clip_and_noise_gradients,
    compose_rho,
    epsilon_for_dpsgd,
    gaussian_mechanism_rho,
    rho_to_epsilon,
)


def test_gaussian_mechanism_rho_reference_value():
    # rho = 1 / (2 * noise_multiplier^2); noise_multiplier=1.0 -> rho=0.5 exactly.
    assert gaussian_mechanism_rho(1.0) == pytest.approx(0.5)
    assert gaussian_mechanism_rho(2.0) == pytest.approx(0.125)


def test_gaussian_mechanism_rho_rejects_nonpositive():
    with pytest.raises(ValueError):
        gaussian_mechanism_rho(0.0)
    with pytest.raises(ValueError):
        gaussian_mechanism_rho(-1.0)


def test_compose_rho_is_additive():
    rho_step = gaussian_mechanism_rho(1.0)
    assert compose_rho(rho_step, 10) == pytest.approx(rho_step * 10)
    assert compose_rho(rho_step, 0) == 0.0


def test_compose_rho_rejects_negative_steps():
    with pytest.raises(ValueError):
        compose_rho(0.5, -1)


def test_rho_to_epsilon_reference_value():
    # epsilon = rho + 2*sqrt(rho * ln(1/delta)); rho=0.5, delta=1e-5:
    rho = 0.5
    delta = 1e-5
    expected = rho + 2.0 * math.sqrt(rho * math.log(1.0 / delta))
    assert rho_to_epsilon(rho, delta) == pytest.approx(expected)


def test_rho_to_epsilon_rejects_bad_delta():
    with pytest.raises(ValueError):
        rho_to_epsilon(0.5, 0.0)
    with pytest.raises(ValueError):
        rho_to_epsilon(0.5, 1.0)


def test_epsilon_more_steps_costs_more_privacy():
    eps_10 = epsilon_for_dpsgd(noise_multiplier=1.0, num_steps=10, delta=1e-5)
    eps_100 = epsilon_for_dpsgd(noise_multiplier=1.0, num_steps=100, delta=1e-5)
    assert eps_100 > eps_10


def test_epsilon_more_noise_costs_less_privacy_budget():
    eps_low_noise = epsilon_for_dpsgd(noise_multiplier=1.0, num_steps=50, delta=1e-5)
    eps_high_noise = epsilon_for_dpsgd(noise_multiplier=5.0, num_steps=50, delta=1e-5)
    assert eps_high_noise < eps_low_noise


def test_epsilon_zero_steps_is_zero():
    assert epsilon_for_dpsgd(noise_multiplier=1.0, num_steps=0, delta=1e-5) == 0.0


def test_clip_and_noise_bounds_per_example_contribution():
    # Two examples: one with a small gradient (under the clip norm, untouched)
    # and one with a huge gradient (must be scaled down to exactly max_grad_norm
    # before it contributes to the sum).
    small = np.array([[0.1, 0.0]], dtype=np.float32)  # norm ~0.1
    huge = np.array([[30.0, 40.0]], dtype=np.float32)  # norm = 50.0
    per_example = np.concatenate([small, huge], axis=0)  # shape (2, 2)

    rng = np.random.default_rng(0)
    (summed,) = clip_and_noise_gradients(
        [per_example], max_grad_norm=1.0, noise_multiplier=0.0, rng=rng
    )
    # small example is untouched (norm 0.1 < 1.0), huge is clipped to unit norm
    # in its own direction: expected sum = small + huge/50.
    expected = small[0] + huge[0] / 50.0
    np.testing.assert_allclose(summed, expected, atol=1e-5)


def test_clip_and_noise_adds_noise_when_requested():
    grads = np.ones((5, 3), dtype=np.float32) * 0.01  # well under the clip norm
    rng1 = np.random.default_rng(1)
    rng2 = np.random.default_rng(2)
    (out1,) = clip_and_noise_gradients([grads], max_grad_norm=1.0, noise_multiplier=1.0, rng=rng1)
    (out2,) = clip_and_noise_gradients([grads], max_grad_norm=1.0, noise_multiplier=1.0, rng=rng2)
    # different rngs -> different noise draws -> different (noised) outputs
    assert not np.allclose(out1, out2)


def test_clip_and_noise_rejects_bad_max_grad_norm():
    grads = np.ones((2, 2), dtype=np.float32)
    with pytest.raises(ValueError):
        clip_and_noise_gradients(
            [grads], max_grad_norm=0.0, noise_multiplier=1.0, rng=np.random.default_rng(0)
        )


def test_clip_and_noise_rejects_empty_input():
    with pytest.raises(ValueError):
        clip_and_noise_gradients(
            [], max_grad_norm=1.0, noise_multiplier=1.0, rng=np.random.default_rng(0)
        )
