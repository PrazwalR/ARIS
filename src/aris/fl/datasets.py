"""Load ULB, PaySim, IEEE-CIS, or synthetic non-IID fraud set. No rows leave this module."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import urlretrieve

import numpy as np
import numpy.typing as npt
import pandas as pd

from aris.fl.config import (
    DATA_RAW,
    IEEE_CIS_TRANSACTION,
    PAYSIM_FILENAMES,
    ULB_FILENAME,
    ULB_URL,
)


@dataclass
class TabularFraud:
    name: str
    x: npt.NDArray[Any]
    y: npt.NDArray[Any]
    time: npt.NDArray[Any] | None
    feature_names: list[str]


def load_dataset(
    name: str,
    max_rows: int | None = None,
    seed: int = 42,
    num_banks: int = 5,
) -> TabularFraud:
    key = name.lower().strip()
    if key in {"synthetic", "synth"}:
        return make_synthetic(n_banks=num_banks, seed=seed, max_rows=max_rows)
    if key in {"ulb", "creditcard", "ieee-ulb"}:
        return load_ulb(max_rows=max_rows)
    if key in {"paysim"}:
        return load_paysim(max_rows=max_rows)
    if key in {"ieee-cis", "ieee", "ieee_cis"}:
        return load_ieee_cis(max_rows=max_rows)
    raise ValueError(f"Unknown dataset {name!r}. Use synthetic | ulb | paysim | ieee-cis")


def ensure_ulb_csv(dest: Path | None = None) -> Path:
    dest = dest or (DATA_RAW / ULB_FILENAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1_000_000:
        return dest
    urlretrieve(ULB_URL, dest)
    return dest


def load_ulb(max_rows: int | None = None, path: Path | None = None) -> TabularFraud:
    csv_path = path or (DATA_RAW / ULB_FILENAME)
    if not csv_path.exists():
        csv_path = ensure_ulb_csv(csv_path)
    df = pd.read_csv(csv_path)
    if max_rows is not None and len(df) > max_rows:
        # keep all fraud, downsample legit so the cap still has positives
        fraud = df[df["Class"] == 1]
        legit = df[df["Class"] == 0]
        n_legit = max(max_rows - len(fraud), 1000)
        legit = legit.sample(n=min(n_legit, len(legit)), random_state=42)
        df = pd.concat([fraud, legit], ignore_index=True).sample(frac=1.0, random_state=42)
    time = df["Time"].to_numpy(dtype=np.float32)
    amount = np.log1p(df["Amount"].to_numpy(dtype=np.float32))
    vcols = [c for c in df.columns if c.startswith("V")]
    x = np.column_stack([df[vcols].to_numpy(dtype=np.float32), amount])
    y = df["Class"].to_numpy(dtype=np.int64)
    return TabularFraud("ulb", x, y, time, [*vcols, "log_amount"])


def load_paysim(max_rows: int | None = None) -> TabularFraud:
    path = _first_existing(DATA_RAW, PAYSIM_FILENAMES)
    if path is None:
        raise FileNotFoundError(
            "PaySim CSV not found. Download from Kaggle (PaySim) and save as "
            f"{DATA_RAW / 'paysim.csv'}"
        )
    df = pd.read_csv(path, nrows=max_rows)
    y_col = "isFraud" if "isFraud" in df.columns else "Class"
    y = df[y_col].to_numpy(dtype=np.int64)
    time = df["step"].to_numpy(dtype=np.float32) if "step" in df.columns else None
    num = df.select_dtypes(include=["number"]).drop(columns=[y_col], errors="ignore")
    if "isFlaggedFraud" in num.columns:
        num = num.drop(columns=["isFlaggedFraud"])
    x = num.to_numpy(dtype=np.float32)
    x = np.nan_to_num(x, nan=0.0)
    return TabularFraud("paysim", x, y, time, list(num.columns))


def load_ieee_cis(max_rows: int | None = None) -> TabularFraud:
    path = DATA_RAW / IEEE_CIS_TRANSACTION
    if not path.exists():
        raise FileNotFoundError(
            "IEEE-CIS train_transaction.csv not found. Download the Kaggle "
            f"IEEE-CIS Fraud Detection set into {DATA_RAW}"
        )
    df = pd.read_csv(path, nrows=max_rows)
    y = df["isFraud"].to_numpy(dtype=np.int64)
    time = df["TransactionDT"].to_numpy(dtype=np.float32) if "TransactionDT" in df.columns else None
    num = df.select_dtypes(include=["number"]).drop(columns=["isFraud"], errors="ignore")
    x = np.nan_to_num(num.to_numpy(dtype=np.float32), nan=0.0)
    return TabularFraud("ieee-cis", x, y, time, list(num.columns))


def make_synthetic(
    n_banks: int = 5,
    rows_per_bank: int = 1200,
    n_features: int = 8,
    seed: int = 42,
    max_rows: int | None = None,
) -> TabularFraud:
    """Each bank has a distinct fraud direction; holdout mixes all of them.

    Local models miss other banks' fraud; FedAvg should improve holdout AUC.
    """
    rng = np.random.default_rng(seed)
    if max_rows is not None:
        rows_per_bank = max(200, max_rows // n_banks)
    xs, ys = [], []
    for bank in range(n_banks):
        n = rows_per_bank
        x = rng.normal(0.0, 1.0, size=(n, n_features)).astype(np.float32)
        # fraud if the bank-specific feature is large
        logit = 2.8 * x[:, bank % n_features] - 2.2
        p = 1 / (1 + np.exp(-logit))
        y = (rng.random(n) < p).astype(np.int64)
        # guarantee some fraud
        if y.sum() < 20:
            y[:40] = 1
            x[:40, bank % n_features] = 3.0
        xs.append(x)
        ys.append(y)
    x = np.concatenate(xs, axis=0)
    y = np.concatenate(ys, axis=0)
    names = [f"f{i}" for i in range(n_features)]
    # No global clock: random holdout + bank-identity shards (see run.py).
    return TabularFraud("synthetic", x, y, None, names)


def _first_existing(folder: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        p = folder / name
        if p.exists():
            return p
    return None
