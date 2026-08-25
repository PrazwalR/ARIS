import math

import numpy as np
import pytest

from aris.fl.model import get_weights, new_model, train_local, train_local_dp
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


def test_compose_rho_rejects_negative_rho_per_step():
    with pytest.raises(ValueError):
        compose_rho(-0.1, 10)


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


def test_rho_to_epsilon_rejects_negative_rho():
    with pytest.raises(ValueError):
        rho_to_epsilon(-0.1, 1e-5)


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


def test_epsilon_for_dpsgd_matches_hand_composed_reference_value():
    # End-to-end check of the actual wiring (gaussian_mechanism_rho -> compose_rho
    # -> rho_to_epsilon), not just each piece in isolation: noise_multiplier=2.0 ->
    # rho_step = 1/(2*2^2) = 0.125; 10 steps -> rho_total = 1.25;
    # epsilon = 1.25 + 2*sqrt(1.25 * ln(1/1e-5)).
    noise_multiplier = 2.0
    num_steps = 10
    delta = 1e-5
    rho_step = 1.0 / (2.0 * noise_multiplier**2)
    rho_total = rho_step * num_steps
    expected = rho_total + 2.0 * math.sqrt(rho_total * math.log(1.0 / delta))
    actual = epsilon_for_dpsgd(noise_multiplier=noise_multiplier, num_steps=num_steps, delta=delta)
    assert actual == pytest.approx(expected)


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


def test_clip_and_noise_norm_combines_across_all_parameters():
    # The clip sensitivity is ONE scalar per example, computed by combining the
    # L2 norm across every parameter array together -- not a separate clip
    # factor per parameter. One example split across two "parameters": combined
    # norm is sqrt(3^2 + 4^2 + 12^2) = 13, so both arrays must be scaled by
    # 5/13 even though param1 alone (norm 5) would look already within budget
    # if clipping were (incorrectly) computed per-parameter.
    param1 = np.array([[3.0, 4.0]], dtype=np.float32)  # per-parameter norm 5
    param2 = np.array([[12.0]], dtype=np.float32)  # per-parameter norm 12
    rng = np.random.default_rng(0)
    clipped1, clipped2 = clip_and_noise_gradients(
        [param1, param2], max_grad_norm=5.0, noise_multiplier=0.0, rng=rng
    )
    # clip_and_noise_gradients returns the batch-summed (not per-example) result,
    # so with a single example the output shape drops the batch axis.
    factor = 5.0 / 13.0
    np.testing.assert_allclose(clipped1, param1[0] * factor, atol=1e-5)
    np.testing.assert_allclose(clipped2, param2[0] * factor, atol=1e-5)


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


def test_train_local_dp_step_count_matches_epochs_times_batches():
    # dp_steps is what the accountant (epsilon_for_dpsgd) treats as `num_steps`,
    # so an off-by-one here silently mis-states the reported privacy budget.
    # n=10, batch_size=3 -> batches of size 3,3,3,1 = 4 batches/epoch; 3 epochs
    # -> 12 steps total. Neither evenly divides the other, so an off-by-one in
    # the batch loop or the epoch loop changes this count.
    rng = np.random.default_rng(0)
    x = rng.normal(size=(10, 4)).astype(np.float32)
    y = rng.integers(0, 2, size=10).astype(np.float32)
    model = new_model(4, hidden=4, seed=0)
    metrics = train_local_dp(
        model,
        x,
        y,
        epochs=3,
        batch_size=3,
        lr=0.01,
        max_grad_norm=1.0,
        noise_multiplier=1.0,
        seed=0,
    )
    assert metrics["dp_steps"] == 12.0


def test_train_local_dp_matches_train_local_when_unclipped_and_noiseless():
    # DP-SGD's clip-then-sum-then-average-by-batch-size must reduce to exactly
    # plain batch gradient descent when clipping never triggers (huge
    # max_grad_norm) and noise is off (noise_multiplier=0.0). This is the
    # property that breaks if per-example grads were accidentally averaged
    # before clipping (instead of after, via the /b in the weight update) --
    # that would silently double-divide by the batch size and cripple the
    # effective learning rate.
    rng = np.random.default_rng(0)
    x = rng.normal(size=(20, 5)).astype(np.float32)
    y = rng.integers(0, 2, size=20).astype(np.float32)

    m_plain = new_model(5, hidden=4, seed=1)
    m_dp = new_model(5, hidden=4, seed=1)

    # batch_size == n: a single full batch, so intra-batch shuffle order can't
    # matter (sum/mean over the whole batch is order-invariant).
    train_local(m_plain, x, y, epochs=1, batch_size=20, lr=0.1, seed=0)
    train_local_dp(
        m_dp,
        x,
        y,
        epochs=1,
        batch_size=20,
        lr=0.1,
        max_grad_norm=1e6,
        noise_multiplier=0.0,
        seed=0,
    )

    for w_plain, w_dp in zip(get_weights(m_plain), get_weights(m_dp), strict=True):
        np.testing.assert_allclose(w_plain, w_dp, atol=1e-4)
