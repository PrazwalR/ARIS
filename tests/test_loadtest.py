"""Correctness under concurrency, not absolute throughput (which varies by
hardware and would make a CI assertion flaky either way). See
docs/LOADTEST.md for measured throughput/latency numbers against both
backends on real hardware.
"""

from __future__ import annotations

from aris.attestation import PublisherKeyring
from aris.bus import InMemoryRiskBus
from aris.loadtest import run_load_test


def test_no_lost_updates_under_concurrent_publish_and_lookup():
    keyring = PublisherKeyring()
    bus = InMemoryRiskBus(keyring, max_publisher_share=1.0)
    report = run_load_test(
        bus,
        keyring,
        num_publishers=8,
        signals_per_publisher=25,
        backend_label="memory-test",
    )
    assert report.errors == 0
    assert report.lost_updates == 0
    assert report.accepted == report.total_signals
    assert report.rejected_quota == 0


def test_quota_is_enforced_correctly_under_concurrent_publishers():
    # A tight global cap shared across many concurrent publishers: the sum of
    # per-publisher quotas will exceed max_entries, so some publishes must be
    # rejected -- and, critically, none of the *accepted* ones may be lost to
    # a race in the admission/eviction bookkeeping under contention.
    keyring = PublisherKeyring()
    bus = InMemoryRiskBus(keyring, max_entries=50, max_publisher_share=0.1)
    report = run_load_test(
        bus,
        keyring,
        num_publishers=10,
        signals_per_publisher=20,
        backend_label="memory-quota-test",
    )
    assert report.errors == 0
    assert report.lost_updates == 0
    assert report.rejected_quota > 0
    assert report.accepted + report.rejected_quota == report.total_signals
    assert report.accepted <= 50


def test_run_ids_do_not_collide_across_back_to_back_runs():
    # Regression: an earlier version derived publisher bank_id and risk_id
    # from (worker, i) alone. Against a persistent bus (Kafka in practice,
    # simulated here by reusing one InMemoryRiskBus across two calls) that
    # let a second run's fresh keyring reject the first run's still-present
    # records as unverifiable -- correct given an unknown key, but it meant
    # back-to-back runs against the same bus produced spurious rejections
    # that had nothing to do with the second run's own correctness.
    keyring = PublisherKeyring()
    bus = InMemoryRiskBus(keyring, max_publisher_share=1.0)
    first = run_load_test(bus, keyring, num_publishers=3, signals_per_publisher=5)
    second = run_load_test(bus, keyring, num_publishers=3, signals_per_publisher=5)
    assert first.errors == 0
    assert second.errors == 0
    assert first.lost_updates == 0
    assert second.lost_updates == 0
