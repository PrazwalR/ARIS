"""M2 -- differential privacy for local training (DP-SGD, Abadi et al. 2016).

Two pieces live here:

1. ``clip_and_noise_gradients``: the DP-SGD mechanism itself -- per-example L2
   gradient clipping (bounds any one example's influence) followed by calibrated
   Gaussian noise (hides whether any one example was included at all).
2. A zCDP (zero-concentrated differential privacy, Bun & Steinke 2016) accountant
   that turns a sequence of such releases into a single (epsilon, delta) budget.

Accounting is deliberately conservative: it does **not** credit privacy
amplification by subsampling. A correct amplification analysis needs a
numerically-integrated subsampled-Gaussian RDP accountant (as in Abadi et al.'s
"moments accountant" or Google's `dp-accounting` library) -- getting that wrong in
either direction is a real risk, and getting it wrong by *understating* the true
epsilon is a privacy defect, not just an inaccuracy. Treating every mini-batch step
as a full (non-subsampled) Gaussian mechanism release is simple to verify and can
only overstate the true privacy loss, never hide it. The epsilon this module
reports is therefore always a valid upper bound, at the cost of being looser than
what a full accountant would report for the same noise multiplier.

This accounting is only meaningful if the noise `clip_and_noise_gradients` adds
is actually unpredictable to whoever might see the released update -- an
accountant cannot detect noise that was technically added but is reconstructible
from a known seed. That sourcing is `aris.fl.model.train_local_dp`'s
responsibility (it draws from OS entropy by default, not from its reproducible
`seed` parameter); this module only computes what a given noise_multiplier is
worth *assuming* the noise was genuinely random.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import numpy.typing as npt


def gaussian_mechanism_rho(noise_multiplier: float) -> float:
    """zCDP cost of one Gaussian-mechanism release with this noise multiplier.

    Standard DP-SGD parameterization: noise std = noise_multiplier * sensitivity,
    so rho = sensitivity^2 / (2 * std^2) = 1 / (2 * noise_multiplier^2) regardless
    of the actual sensitivity (clip norm) value.
    """
    if noise_multiplier <= 0:
        raise ValueError("noise_multiplier must be positive")
    return 1.0 / (2.0 * noise_multiplier**2)


def compose_rho(rho_per_step: float, num_steps: int) -> float:
    """zCDP composes additively across independent releases (Bun & Steinke 2016)."""
    if rho_per_step < 0:
        raise ValueError("rho_per_step must be non-negative")
    if num_steps < 0:
        raise ValueError("num_steps must be non-negative")
    return rho_per_step * num_steps


def rho_to_epsilon(rho: float, delta: float) -> float:
    """Convert rho-zCDP to (epsilon, delta)-DP (Bun & Steinke 2016, Prop 1.3)."""
    if rho < 0:
        raise ValueError("rho must be non-negative")
    if not (0.0 < delta < 1.0):
        raise ValueError("delta must be in (0, 1)")
    return rho + 2.0 * math.sqrt(rho * math.log(1.0 / delta))


def epsilon_for_dpsgd(noise_multiplier: float, num_steps: int, delta: float) -> float:
    """Total (epsilon, delta)-DP cost of `num_steps` DP-SGD updates.

    Conservative upper bound -- see module docstring. `num_steps` is the number of
    mini-batch gradient releases one client's data actually underwent (local_epochs
    * rounds * batches_per_epoch for that client's shard size), not a global count.
    """
    rho_step = gaussian_mechanism_rho(noise_multiplier)
    return rho_to_epsilon(compose_rho(rho_step, num_steps), delta)


def clip_and_noise_gradients(
    per_example_grads: list[npt.NDArray[Any]],
    max_grad_norm: float,
    noise_multiplier: float,
    rng: np.random.Generator,
) -> list[npt.NDArray[Any]]:
    """DP-SGD's core primitive (Abadi et al. 2016): per-example L2 clip, sum, noise.

    `per_example_grads[p]` has shape (batch, *param_p_shape); the leading axis is
    the per-example axis and is shared across every array in the list, since the
    L2 norm used for clipping is computed over each example's gradient across ALL
    parameters combined (a single scalar sensitivity bound for the whole update,
    not one per parameter).

    Returns the summed (not yet averaged by batch size) clipped-and-noised
    gradient for each parameter, in the same order as the input.
    """
    if not per_example_grads:
        raise ValueError("per_example_grads must be non-empty")
    if max_grad_norm <= 0:
        raise ValueError("max_grad_norm must be positive")
    if noise_multiplier < 0:
        raise ValueError("noise_multiplier must be non-negative")

    batch = per_example_grads[0].shape[0]
    sq_sums = np.zeros(batch, dtype=np.float64)
    for g in per_example_grads:
        sq_sums += np.sum(g.reshape(batch, -1).astype(np.float64) ** 2, axis=1)
    norms = np.sqrt(sq_sums)
    clip_factor = np.minimum(1.0, max_grad_norm / (norms + 1e-12))

    noise_std = noise_multiplier * max_grad_norm
    out: list[npt.NDArray[Any]] = []
    for g in per_example_grads:
        scale = clip_factor.reshape((batch,) + (1,) * (g.ndim - 1))
        clipped_sum = np.sum(g * scale, axis=0)
        noise = rng.normal(0.0, noise_std, size=clipped_sum.shape)
        out.append((clipped_sum + noise).astype(g.dtype))
    return out
