"""Non-IID bank shards. Raw rows stay on the simulated bank; only used locally."""

from __future__ import annotations

import numpy as np

from aris.fl.config import BANK_IDS


def equal_contiguous_shards(
    x: np.ndarray,
    y: np.ndarray,
    num_banks: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    splits = np.array_split(np.arange(len(y)), num_banks)
    return [(x[idx], y[idx]) for idx in splits]


def temporal_shards(
    x: np.ndarray,
    y: np.ndarray,
    time_col: np.ndarray,
    num_banks: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split by time windows so banks see different periods (non-IID)."""
    order = np.argsort(time_col)
    x, y = x[order], y[order]
    splits = np.array_split(np.arange(len(y)), num_banks)
    return [(x[idx], y[idx]) for idx in splits]


def dirichlet_shards(
    x: np.ndarray,
    y: np.ndarray,
    num_banks: int,
    alpha: float,
    seed: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Label-skew non-IID (Dirichlet), common FL benchmark."""
    rng = np.random.default_rng(seed)
    shards: list[list[int]] = [[] for _ in range(num_banks)]
    for label in (0, 1):
        idx = np.where(y == label)[0]
        rng.shuffle(idx)
        props = rng.dirichlet([alpha] * num_banks)
        # at least 1 row per bank when possible
        counts = _counts_from_props(len(idx), props)
        start = 0
        for bank, count in enumerate(counts):
            shards[bank].extend(idx[start : start + count].tolist())
            start += count
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for bank in range(num_banks):
        ix = np.array(shards[bank], dtype=int)
        rng.shuffle(ix)
        out.append((x[ix], y[ix]))
    return out


def _counts_from_props(n: int, props: np.ndarray) -> list[int]:
    props = props / props.sum()
    counts = np.floor(props * n).astype(int)
    remainder = n - int(counts.sum())
    frac_order = np.argsort(-(props * n - counts))
    for i in range(remainder):
        counts[int(frac_order[i % len(counts)])] += 1
    return counts.tolist()


def holdout_split(
    x: np.ndarray,
    y: np.ndarray,
    frac: float,
    seed: int,
    time_col: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Global holdout. Time-aware if time_col is given (last frac by time)."""
    if time_col is not None:
        order = np.argsort(time_col)
        x, y, time_col = x[order], y[order], time_col[order]
        cut = int(len(y) * (1.0 - frac))
        return x[:cut], y[:cut], x[cut:], y[cut:]
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(y))
    cut = int(len(y) * (1.0 - frac))
    train_i, test_i = idx[:cut], idx[cut:]
    return x[train_i], y[train_i], x[test_i], y[test_i]


def pooled_holdout_from_shards(
    shards: list[tuple[np.ndarray, np.ndarray]],
    frac: float,
    seed: int,
) -> tuple[list[tuple[np.ndarray, np.ndarray]], np.ndarray, np.ndarray]:
    """Take a holdout slice from every bank, then pool those rows for evaluation."""
    rng = np.random.default_rng(seed)
    kept: list[tuple[np.ndarray, np.ndarray]] = []
    hx, hy = [], []
    for x, y in shards:
        n = len(y)
        n_te = max(1, round(n * frac))
        idx = rng.permutation(n)
        te, tr = idx[:n_te], idx[n_te:]
        kept.append((x[tr], y[tr]))
        hx.append(x[te])
        hy.append(y[te])
    return kept, np.concatenate(hx, axis=0), np.concatenate(hy, axis=0)


def named_banks(num_banks: int) -> tuple[str, ...]:
    if num_banks > len(BANK_IDS):
        extra = tuple(f"BANK-{i}" for i in range(len(BANK_IDS), num_banks))
        return BANK_IDS + extra
    return BANK_IDS[:num_banks]
