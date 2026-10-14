"""Tests for On-Prem Graph and Receiver Velocity Feature Extraction (Phase M6+)."""

from __future__ import annotations

import numpy as np
import pytest

from aris.fl.config import TrainConfig
from aris.fl.graph_features import (
    OnPremGraphFeatureExtractor,
    TransactionRecord,
    generate_multi_bank_transaction_stream,
)
from aris.fl.run import run_experiment


def test_graph_feature_extractor_basic() -> None:
    extractor = OnPremGraphFeatureExtractor(window_seconds=86400.0)
    base_time = 100000.0

    # First transaction to receiver ACC-1
    rec1 = TransactionRecord(
        tx_id="TX-001",
        timestamp=base_time,
        sender_ifsc="BKAA0001",
        sender_account="SENDER-1",
        receiver_ifsc="BKBB0002",
        receiver_account="ACC-1",
        amount=100.0,
        is_fraud=0,
    )
    f1 = extractor.extract_features(rec1, update_history=True)
    assert len(f1) == 8
    # amount, log1p, fan_in_1h, fan_in_24h, remitter_bank_count_24h, velocity_ratio, count_1h, novel_remitter
    assert f1[0] == 100.0
    assert f1[2] == 0.0  # fan_in_1h prior to this tx
    assert f1[7] == 1.0  # novel remitter

    # Second transaction from a DIFFERENT sender within 10 minutes
    rec2 = TransactionRecord(
        tx_id="TX-002",
        timestamp=base_time + 600.0,
        sender_ifsc="BKCC0003",
        sender_account="SENDER-2",
        receiver_ifsc="BKBB0002",
        receiver_account="ACC-1",
        amount=500.0,
        is_fraud=1,
    )
    f2 = extractor.extract_features(rec2, update_history=True)
    assert f2[2] == 1.0  # 1 previous sender in past 1h
    assert f2[4] == 1.0  # 1 previous remitter bank in 24h
    assert f2[5] == 5.0  # 500 / 100 = 5.0 velocity ratio


def test_graph_feature_extractor_window_pruning() -> None:
    extractor = OnPremGraphFeatureExtractor(window_seconds=3600.0)  # 1 hour window
    base_time = 100000.0

    rec1 = TransactionRecord(
        tx_id="TX-001",
        timestamp=base_time,
        sender_ifsc="BKAA0001",
        sender_account="SENDER-1",
        receiver_ifsc="BKBB0002",
        receiver_account="ACC-1",
        amount=100.0,
    )
    extractor.extract_features(rec1, update_history=True)

    # Transaction 2 hours later -- previous tx should be pruned
    rec2 = TransactionRecord(
        tx_id="TX-002",
        timestamp=base_time + 7200.0,
        sender_ifsc="BKAA0001",
        sender_account="SENDER-1",
        receiver_ifsc="BKBB0002",
        receiver_account="ACC-1",
        amount=100.0,
    )
    f2 = extractor.extract_features(rec2, update_history=True)
    assert f2[2] == 0.0  # fan_in_1h reset to 0
    assert f2[3] == 0.0  # fan_in_24h reset to 0 because window is 1h


def test_generate_multi_bank_stream() -> None:
    records, x, y, feature_names = generate_multi_bank_transaction_stream(
        num_banks=5,
        num_transactions=1000,
        fraud_ratio=0.08,
        seed=123,
    )
    assert len(records) == 1000
    assert x.shape == (1000, 8)
    assert len(y) == 1000
    assert y.sum() > 20
    assert len(feature_names) == 8


def test_federated_learning_on_graph_stream() -> None:
    cfg = TrainConfig(
        rounds=3,
        local_epochs=2,
        num_banks=3,
        batch_size=64,
        learning_rate=0.05,
        seed=42,
    )
    report = run_experiment("graph-stream", cfg=cfg, partition="dirichlet")
    assert report["dataset"] == "graph-stream"
    assert report["global"]["auc"] > 0.0
    assert "local_epoch_metrics" in report
    assert "federated_round_metrics" in report
