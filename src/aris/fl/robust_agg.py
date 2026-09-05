"""M6+: Byzantine-robust aggregation.

Plain FedAvg (`aris.fl.fedavg`) is a weighted *mean*: a single client sending
an arbitrarily scaled or adversarially directed update can drag the global
model arbitrarily far, in one round, with no per-client scrutiny of the
update's content -- only its declared example count. Krum and coordinate-wise
median bound that influence by construction, at the cost of discarding
information a plain mean would have used.

Both are opt-in alternatives to `fedavg`/`secure_fedavg`, selected via
`TrainConfig.aggregation_strategy`, wired into `aris.fl.run._federated_train`
the same way `secure_agg` already is.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import numpy.typing as npt


def _flatten(weights: list[npt.NDArray[Any]]) -> npt.NDArray[Any]:
    return np.concatenate([w.reshape(-1).astype(np.float64) for w in weights])


def krum(
    updates: list[tuple[list[npt.NDArray[Any]], int]],
    num_byzantine: int,
) -> list[npt.NDArray[Any]]:
    """Krum (Blanchard et al. 2017): select the single client update whose sum
    of squared distances to its `n - num_byzantine - 2` closest OTHER updates
    is smallest, and return that update verbatim (not an average of it with
    anything else). An update far from the honest cluster -- which is what an
    attacker sending an extreme value produces -- scores badly on this measure
    and is never selected, regardless of how it is scaled or directed.

    Provably tolerant of up to `num_byzantine` Byzantine clients when
    `n > 2 * num_byzantine + 2`; this function enforces that bound rather than
    silently producing a result outside the regime the guarantee holds in.

    Ignores each client's declared example count on purpose: a Byzantine
    client can claim any weight it likes, so admitting a weighted vote would
    reopen exactly the influence this function exists to bound.
    """
    n = len(updates)
    if n <= 2 * num_byzantine + 2:
        raise ValueError(
            f"Krum needs n > 2*num_byzantine + 2 to guarantee robustness "
            f"(n={n}, num_byzantine={num_byzantine})"
        )
    flat = [_flatten(w) for w, _ in updates]
    scores = []
    for i in range(n):
        dists = sorted(float(np.sum((flat[i] - flat[j]) ** 2)) for j in range(n) if j != i)
        closest = dists[: n - num_byzantine - 2]
        scores.append(sum(closest))
    winner = int(np.argmin(scores))
    return [w.copy() for w in updates[winner][0]]


def coordinate_median(
    updates: list[tuple[list[npt.NDArray[Any]], int]],
) -> list[npt.NDArray[Any]]:
    """Per-coordinate median across clients (Yin et al. 2018): for each
    individual weight, take the median value across clients rather than the
    mean. Robust to up to `floor((n-1)/2)` outlying values *per coordinate*,
    independent of which clients they come from or how extreme they are --
    unlike a mean, an outlier arbitrarily far from the honest values cannot
    move a coordinate's median past the honest values nearest it.

    Also ignores declared example counts, for the same reason `krum` does.
    """
    if not updates:
        raise ValueError("coordinate_median needs at least one client update")
    n_layers = len(updates[0][0])
    out: list[npt.NDArray[Any]] = []
    for layer in range(n_layers):
        stacked = np.stack([w[layer] for w, _ in updates], axis=0)
        median = np.median(stacked, axis=0).astype(updates[0][0][layer].dtype)
        out.append(median)
    return out
