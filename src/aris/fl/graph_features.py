"""On-prem graph and receiver velocity feature extraction engine (Phase M6+).

Enables banks to compute local graph metrics (fan-in degree, incoming volume velocity,
remitter diversity, and rapid liquidation signatures) on-prem from local account
transaction history without centralizing graphs or sharing raw transaction records.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np
import numpy.typing as npt


@dataclass
class TransactionRecord:
    tx_id: str
    timestamp: float  # Unix timestamp in seconds
    sender_ifsc: str
    sender_account: str
    receiver_ifsc: str
    receiver_account: str
    amount: float
    is_fraud: int = 0


@dataclass
class ReceiverHistory:
    """Rolling temporal history for a specific receiver account."""

    timestamps: list[float] = field(default_factory=list)
    amounts: list[float] = field(default_factory=list)
    senders: list[tuple[str, str]] = field(default_factory=list)  # (ifsc, account)
    sender_ifscs: list[str] = field(default_factory=list)

    def prune(self, current_time: float, max_window_seconds: float = 86400.0) -> None:
        """Evict records older than max_window_seconds (default 24 hours)."""
        cutoff = current_time - max_window_seconds
        while self.timestamps and self.timestamps[0] < cutoff:
            self.timestamps.pop(0)
            self.amounts.pop(0)
            self.senders.pop(0)
            self.sender_ifscs.pop(0)


class OnPremGraphFeatureExtractor:
    """Extracts receiver-centric graph and velocity features on-prem.

    Privacy-preserving: the graph is strictly maintained within the bank.
    Only the resulting engineered numerical features enter the local MLP model.
    """

    FEATURE_NAMES: ClassVar[list[str]] = [
        "amount",
        "log_amount",
        "fan_in_1h",
        "fan_in_24h",
        "remitter_bank_count_24h",
        "velocity_amount_ratio_24h",
        "tx_frequency_1h",
        "novel_remitter_flag",
    ]

    def __init__(self, window_seconds: float = 86400.0) -> None:
        self.window_seconds = window_seconds
        # Key: (receiver_ifsc, receiver_account) -> ReceiverHistory
        self._histories: dict[tuple[str, str], ReceiverHistory] = defaultdict(ReceiverHistory)

    def extract_features(
        self,
        record: TransactionRecord,
        update_history: bool = True,
    ) -> npt.NDArray[np.float32]:
        """Compute the feature vector for an incoming transfer.

        Features computed:
        0: amount
        1: log1p(amount)
        2: fan_in_1h (unique senders in past 1 hour)
        3: fan_in_24h (unique senders in past 24 hours)
        4: remitter_bank_count_24h (distinct remitter IFSCs)
        5: velocity_amount_ratio_24h (current amount / mean previous amount in 24h, capped at 50.0)
        6: tx_frequency_1h (count of incoming transactions in 1h)
        7: novel_remitter_flag (1.0 if this sender has never sent to this receiver in 24h, else 0.0)
        """
        receiver_key = (record.receiver_ifsc, record.receiver_account)
        history = self._histories[receiver_key]
        history.prune(record.timestamp, self.window_seconds)

        t = record.timestamp
        one_hour_ago = t - 3600.0

        # 1-hour window calculations
        senders_1h: set[tuple[str, str]] = set()
        count_1h = 0
        # timestamps/senders are always appended and pruned together (see
        # ReceiverHistory.prune and the append block below), so strict=True
        # turns any future break of that invariant into a loud error instead
        # of zip() silently truncating to the shorter list.
        for ts, s in zip(history.timestamps, history.senders, strict=True):
            if ts >= one_hour_ago:
                senders_1h.add(s)
                count_1h += 1

        fan_in_1h = float(len(senders_1h))
        fan_in_24h = float(len(set(history.senders)))
        remitter_bank_count_24h = float(len(set(history.sender_ifscs)))

        # Velocity ratio
        if history.amounts:
            mean_amt = float(np.mean(history.amounts))
            velocity_ratio = min(record.amount / max(mean_amt, 1.0), 50.0)
        else:
            velocity_ratio = 1.0

        # Novel remitter flag
        sender_tuple = (record.sender_ifsc, record.sender_account)
        novel_remitter = 1.0 if sender_tuple not in history.senders else 0.0

        features = np.array(
            [
                float(record.amount),
                float(np.log1p(max(record.amount, 0.0))),
                fan_in_1h,
                fan_in_24h,
                remitter_bank_count_24h,
                velocity_ratio,
                float(count_1h),
                novel_remitter,
            ],
            dtype=np.float32,
        )

        if update_history:
            history.timestamps.append(record.timestamp)
            history.amounts.append(record.amount)
            history.senders.append(sender_tuple)
            history.sender_ifscs.append(record.sender_ifsc)

        return features

    def reset(self) -> None:
        """Clear local graph state."""
        self._histories.clear()


def generate_multi_bank_transaction_stream(
    num_banks: int = 5,
    num_transactions: int = 6000,
    fraud_ratio: float = 0.05,
    seed: int = 42,
    fit_fraction: float = 0.8,
) -> tuple[list[TransactionRecord], npt.NDArray[np.float32], npt.NDArray[np.int64], list[str]]:
    """Generates a realistic multi-bank transaction stream with cross-bank mule rings.

    Simulates:
    - Normal peer-to-peer and merchant transfers.
    - Coordinated mule rings: multiple victim senders from different banks funneling
      burst transfers into a shared receiver mule account within tight time windows.

    Returns:
    (records, feature_matrix, labels, feature_names)
    """
    rng = np.random.default_rng(seed)
    bank_ifscs = [f"BANK{chr(65 + i)}0001234" for i in range(num_banks)]

    # Pool of legitimate users per bank
    users_per_bank = 100
    bank_accounts = {
        ifsc: [f"ACC-{ifsc[-4:]}-{1000 + j}" for j in range(users_per_bank)] for ifsc in bank_ifscs
    }

    # Designated mule receiver accounts across banks
    mule_accounts = [
        (bank_ifscs[i % num_banks], f"MULE-REC-{5000 + i}") for i in range(max(num_banks, 4))
    ]

    records: list[TransactionRecord] = []
    base_time = 1770000000.0  # arbitrary epoch start
    current_time = base_time

    num_fraud = int(num_transactions * fraud_ratio)
    fraud_indices = set(rng.choice(num_transactions, size=num_fraud, replace=False))

    for i in range(num_transactions):
        current_time += float(rng.exponential(scale=15.0))  # avg 15 sec between txs
        is_fraud = 1 if i in fraud_indices else 0

        if is_fraud:
            # Mule pattern: distinct victim bank/sender targeting a shared mule receiver
            # with high burst amounts and sudden fan-in
            target_mule_ifsc, target_mule_acc = mule_accounts[rng.integers(len(mule_accounts))]
            # Pick a victim from a DIFFERENT bank
            other_ifscs = [b for b in bank_ifscs if b != target_mule_ifsc]
            sender_ifsc = rng.choice(other_ifscs)
            sender_acc = rng.choice(bank_accounts[sender_ifsc])
            amount = float(rng.uniform(15000.0, 95000.0))  # large suspicious transfer
            receiver_ifsc, receiver_acc = target_mule_ifsc, target_mule_acc
        else:
            # Legitimate pattern: regular transfer within or between banks
            sender_ifsc = rng.choice(bank_ifscs)
            sender_acc = rng.choice(bank_accounts[sender_ifsc])
            receiver_ifsc = rng.choice(bank_ifscs)
            receiver_acc = rng.choice(bank_accounts[receiver_ifsc])
            amount = float(rng.lognormal(mean=7.0, sigma=1.2))  # typical log-normal amount

        records.append(
            TransactionRecord(
                tx_id=f"TX-{i:06d}",
                timestamp=current_time,
                sender_ifsc=sender_ifsc,
                sender_account=sender_acc,
                receiver_ifsc=receiver_ifsc,
                receiver_account=receiver_acc,
                amount=amount,
                is_fraud=is_fraud,
            )
        )

    # Extract on-prem graph features
    extractor = OnPremGraphFeatureExtractor()
    features_list: list[npt.NDArray[np.float32]] = []
    labels_list: list[int] = []

    for rec in records:
        f = extractor.extract_features(rec, update_history=True)
        features_list.append(f)
        labels_list.append(rec.is_fraud)

    x = np.vstack(features_list)
    # Standardize for stable training, fitting the statistics on the earliest
    # `fit_fraction` of the stream by time only. The holdout is the latest
    # slice (aris.fl.partition.holdout_split with a time column; 0.8 matches
    # TrainConfig's default 0.2 holdout), so fitting on the whole stream would
    # let holdout rows shape how training rows are scaled.
    order = np.argsort([r.timestamp for r in records], kind="stable")
    fit_rows = order[: max(1, int(len(order) * fit_fraction))]
    means = np.mean(x[fit_rows], axis=0)
    stds = np.std(x[fit_rows], axis=0) + 1e-5
    x = ((x - means) / stds).astype(np.float32)
    y = np.array(labels_list, dtype=np.int64)

    return records, x, y, OnPremGraphFeatureExtractor.FEATURE_NAMES
