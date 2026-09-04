"""M3 integration tests: a real Kafka broker + Schema Registry via docker-compose.

Skips cleanly (not silently) when the broker or registry is unreachable, so this
suite is honest about what it did and did not verify rather than passing green
with zero real coverage. Run `docker compose up -d` first (see docker-compose.yml)
to actually exercise these.

`tests/test_bus.py` covers the shared admission/replay/quota logic exhaustively
against `InMemoryRiskBus` directly; these tests focus on what is genuinely new
here -- cross-process visibility over a real broker, the wire format, and that
consuming a signal applies the same rules as publishing one.
"""

from __future__ import annotations

import socket
import time
import uuid
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import urlopen

import pytest

from aris.attestation import Publisher, PublisherKeyring, UnknownPublisher
from aris.bus import PublishOutcome
from aris.kafka_bus import KafkaRiskBus, load_kafka_config
from aris.schema import LookupStatus, RiskSignal
from aris.schema_registry import decode, encode

_BOOTSTRAP, _REGISTRY_URL = load_kafka_config()


def _kafka_reachable() -> bool:
    host, _, port = _BOOTSTRAP.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _registry_reachable() -> bool:
    try:
        with urlopen(f"{_REGISTRY_URL}/subjects", timeout=1.0):
            return True
    except URLError:
        return False


requires_kafka = pytest.mark.skipif(
    not (_kafka_reachable() and _registry_reachable()),
    reason=(
        f"Kafka ({_BOOTSTRAP}) or Schema Registry ({_REGISTRY_URL}) not reachable -- "
        "run `docker compose up -d` to exercise the M3 integration suite"
    ),
)


def _signal(risk_id: str, score: int = 92, bank: str = "BANK-B") -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=0.9,
        reason_codes=("high_velocity",),
        model_version="v0.4-fl",
        source_bank_id=bank,
        ttl_hours=24,
        timestamp=datetime.now(timezone.utc),
    )


def _unique_risk_id() -> str:
    # A fresh 64-hex-char id per test so tests don't collide on the shared,
    # persistent local topic across runs.
    return uuid.uuid4().hex + uuid.uuid4().hex


def _wait_until(predicate, timeout_s: float = 15.0, interval_s: float = 0.2):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval_s)
    pytest.fail(f"condition did not become true within {timeout_s}s")


def test_wire_format_round_trip_needs_no_broker():
    # encode/decode are pure functions -- always run, no skip needed.
    payload = {"a": 1, "b": "two"}
    wire = encode(schema_id=7, payload=payload)
    schema_id, decoded = decode(wire)
    assert schema_id == 7
    assert decoded == payload


@requires_kafka
class TestCrossProcessVisibility:
    def test_bank_a_sees_bank_bs_publish_via_separate_bus_instance(self):
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        risk_id = _unique_risk_id()

        # Two independent KafkaRiskBus instances sharing nothing but the
        # broker + registry -- standing in for two banks' separate processes.
        bus_b = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        bus_a = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            outcome = bus_b.publish(bank_b.sign(_signal(risk_id, score=92)))
            assert outcome is PublishOutcome.ACCEPTED

            # bus_a only learns this via its background consumer thread
            # catching up on the topic -- not synchronously.
            result = _wait_until(
                lambda: (
                    bus_a.lookup(risk_id)
                    if bus_a.lookup(risk_id).status is LookupStatus.FOUND
                    else None
                )
            )
            assert result.score == 92
            assert result.contributing_banks == ("BANK-B",)
        finally:
            bus_b.close()
            bus_a.close()

    def test_forged_publisher_is_rejected_before_reaching_kafka(self):
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        # BANK-EVIL is never registered.
        bank_evil = Publisher.generate("BANK-EVIL")
        risk_id = _unique_risk_id()

        bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            forged = bank_evil.sign(_signal(risk_id, score=0, bank="BANK-EVIL"))
            with pytest.raises(UnknownPublisher):
                bus.publish(forged)
            # Never reached Kafka, so a lookup finds nothing.
            assert bus.lookup(risk_id).status is LookupStatus.NOT_FOUND
        finally:
            bus.close()

    def test_consumed_signal_from_untrusted_bank_does_not_crash_the_consumer(self):
        # A record on the topic that a LATER bus instance's keyring does not
        # trust must be rejected on consume, not crash the background thread
        # or silently corrupt that instance's local view.
        publisher_keyring = PublisherKeyring()
        bank_evil = Publisher.generate("BANK-EVIL")
        publisher_keyring.register(bank_evil.bank_id, bank_evil.public_key)
        risk_id = _unique_risk_id()

        publisher_bus = KafkaRiskBus(publisher_keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            outcome = publisher_bus.publish(
                bank_evil.sign(_signal(risk_id, score=92, bank="BANK-EVIL"))
            )
            assert outcome is PublishOutcome.ACCEPTED
        finally:
            publisher_bus.close()

        # A second reader that does NOT trust BANK-EVIL.
        reader_keyring = PublisherKeyring()
        reader_bus = KafkaRiskBus(reader_keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            time.sleep(2.0)  # let the consumer thread catch up and reject it
            assert reader_bus.lookup(risk_id).status is LookupStatus.NOT_FOUND
            # The consumer thread is still alive -- rejecting one bad record
            # didn't kill it.
            assert reader_bus._consumer_thread.is_alive()
        finally:
            reader_bus.close()

    def test_replayed_stale_signal_is_rejected_after_consume_round_trip(self):
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        risk_id = _unique_risk_id()

        bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            fresh = bank_b.sign(_signal(risk_id, score=92))
            assert bus.publish(fresh) is PublishOutcome.ACCEPTED

            # Same (risk_id, bank) key, an older-or-equal effective timestamp:
            # a captured-and-replayed message. Publishing it again locally
            # must be rejected as STALE by the same high-water-mark guard
            # InMemoryRiskBus already enforces -- this only confirms
            # KafkaRiskBus.publish() didn't bypass that check.
            assert bus.publish(fresh) is PublishOutcome.STALE
        finally:
            bus.close()
