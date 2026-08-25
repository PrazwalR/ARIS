"""M2 -- optional secure aggregation (Bonawitz et al. 2017, simplified simulation).

Real secure aggregation additionally handles client dropout (via threshold secret
sharing of the pairwise seeds) and derives each pairwise seed through a
Diffie-Hellman key exchange that never touches the server. This module simulates
only the core additive-masking cancellation property inside a single process,
where every client is simultaneously present and pairwise seeds are generated
directly rather than negotiated -- it demonstrates that an aggregator can compute
the exact weighted sum of client updates without ever seeing any individual
update in the clear, not the full production protocol.

This is a *different* protection from differential privacy (`aris.fl.privacy`)
and does not substitute for it: masking hides each client's update from the
*aggregator*, but says nothing about what the resulting *aggregate* model can
leak about the union of all clients' data (membership inference, reconstruction
from repeated rounds, etc.). Use both together, not one instead of the other.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt


def _pair_seed(base_seed: int, i: int, j: int) -> int:
    """Deterministic per-pair seed. In a real protocol this is the output of a
    Diffie-Hellman exchange between clients i and j; here both sides can derive
    the same seed from (base_seed, i, j) because there is no adversary between
    them to exchange keys with in a single-process simulation.
    """
    lo, hi = (i, j) if i < j else (j, i)
    return hash((base_seed, lo, hi)) % (2**31 - 1)


def secure_sum(
    values: list[list[npt.NDArray[Any]]],
    seed: int,
    mask_scale: float = 1.0,
) -> tuple[list[list[npt.NDArray[Any]]], list[npt.NDArray[Any]]]:
    """Mask each client's per-parameter arrays with pairwise-cancelling noise.

    `values[c][p]` is client c's array for parameter p. For every pair (i, j),
    client i's masked output gets `+mask_ij` and client j's gets `-mask_ij` (same
    mask, opposite sign), so summing every client's masked output over c yields
    exactly the plaintext sum -- even though no single `masked[c]` reveals
    `values[c]` (it differs from it by a sum of independent Gaussian masks with
    the same order of magnitude as `mask_scale`, one per peer).

    Returns `(masked, true_sum)`. An honest aggregator only ever sees `masked`
    and computes its own sum from that; `true_sum` is returned for verification
    (tests, and honest demonstration here) and is not something a real
    aggregator would have independent access to.

    With a single client there are no pairs to mask against, so `masked[0] ==
    values[0]` exactly -- correct behavior, but zero protection, since there is
    no peer to hide from. ARIS's cross-bank model only makes sense with multiple
    banks, so this is not separately guarded against here.
    """
    n = len(values)
    if n == 0:
        raise ValueError("values must be non-empty")
    n_params = len(values[0])
    ref_shapes = [arr.shape for arr in values[0]]
    for i in range(1, n):
        if len(values[i]) != n_params:
            raise ValueError(
                f"client {i} has {len(values[i])} parameter arrays, expected {n_params} "
                f"(from client 0). All clients must submit the same model structure -- "
                "a mismatch here would otherwise silently drop the extra/missing "
                "parameter(s) from the aggregate instead of raising."
            )
        for p, (arr, ref_shape) in enumerate(zip(values[i], ref_shapes, strict=True)):
            if arr.shape != ref_shape:
                raise ValueError(
                    f"client {i} parameter {p} has shape {arr.shape}, expected "
                    f"{ref_shape} (from client 0). Mismatched shapes can silently "
                    "broadcast into a wrong aggregate instead of failing clearly, and "
                    "break the pairwise-mask cancellation guarantee."
                )

    masked = [[arr.copy() for arr in client_vals] for client_vals in values]
    for i in range(n):
        for j in range(i + 1, n):
            rng = np.random.default_rng(_pair_seed(seed, i, j))
            for p in range(n_params):
                shape = values[i][p].shape
                mask = rng.normal(0.0, mask_scale, size=shape).astype(values[i][p].dtype)
                masked[i][p] = masked[i][p] + mask
                masked[j][p] = masked[j][p] - mask

    true_sum = [np.sum([client_vals[p] for client_vals in values], axis=0) for p in range(n_params)]
    return masked, true_sum


def secure_fedavg(
    updates: list[tuple[list[npt.NDArray[Any]], int]],
    seed: int,
    mask_scale: float = 1.0,
) -> list[npt.NDArray[Any]]:
    """FedAvg computed via `secure_sum`: the aggregator never holds a single
    client's unmasked weighted contribution, only the masked values and their
    sum (which equals the plaintext weighted average exactly).
    """
    if not updates:
        raise ValueError("secure_fedavg needs at least one client update")
    total = float(sum(n for _, n in updates))
    weighted_terms = [
        [(arr.astype(np.float64) * (n / total)) for arr in weights] for weights, n in updates
    ]
    masked, _true_sum = secure_sum(weighted_terms, seed=seed, mask_scale=mask_scale)

    n_params = len(masked[0])
    server_sum = [
        np.sum([client_vals[p] for client_vals in masked], axis=0) for p in range(n_params)
    ]
    dtypes = [updates[0][0][p].dtype for p in range(n_params)]
    return [server_sum[p].astype(dtypes[p]) for p in range(n_params)]
