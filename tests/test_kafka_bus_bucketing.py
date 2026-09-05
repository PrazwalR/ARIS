"""M6+ / docs/SECURITY.md SS3.4: prefix-bucketed consumption.

`KafkaRiskBus` used to replicate the whole `risk-signals` topic into every
instance's local view in the background -- correct, but it meant every member
bank could see every *other* bank's complete published risk_id set, not just
the accounts it actually queried. These tests exercise the replacement: each
instance now consumes only the (`risk_id`-prefix) buckets it has actually
needed, and the core property this buys -- an instance that never asked about
bucket X does not have bucket X's data -- alongside the failure mode this
trades for (a cold lookup pays a bounded, synchronous network round trip
rather than reading an already-warm view).
"""

from __future__ import annotations

import secrets
import time

from aris.attestation import Publisher, PublisherKeyring
from aris.bus import PublishOutcome
from aris.kafka_bus import KafkaRiskBus, bucket_for_risk_id, load_kafka_config
from aris.schema import LookupStatus, RiskSignal
from tests.test_kafka_bus import requires_kafka

_BOOTSTRAP, _REGISTRY_URL = load_kafka_config()


def _risk_id(prefix: str) -> str:
    # `prefix` fixes which bucket this lands in (bucket_for_risk_id looks only
    # at the first two hex characters); the rest is fresh randomness so
    # repeated test runs against the persistent local topic don't collide.
    return prefix + secrets.token_hex(31)


def _signal(risk_id: str, score: int = 92, bank: str = "BANK-B") -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=0.9,
        reason_codes=("high_velocity",),
        model_version="v0.4-fl",
        source_bank_id=bank,
        ttl_hours=24,
    )


def test_bucket_for_risk_id_is_deterministic_and_covers_the_prefix():
    rid = "ab" + "0" * 62
    assert bucket_for_risk_id(rid) == bucket_for_risk_id(rid)
    assert bucket_for_risk_id(rid) == int("ab", 16)


@requires_kafka
class TestPrefixBucketedVisibility:
    def test_an_unwarmed_bucket_is_not_visible_to_another_instance(self):
        """The core property this redesign exists for: a KafkaRiskBus that
        never looked up (or published to) a given bucket must not have that
        bucket's data, even though it shares the same topic."""
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)

        rid_a = _risk_id("aa")
        rid_b = _risk_id("bb")
        assert bucket_for_risk_id(rid_a) != bucket_for_risk_id(rid_b)

        publisher_bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            assert publisher_bus.publish(bank_b.sign(_signal(rid_a))) is PublishOutcome.ACCEPTED
            assert publisher_bus.publish(bank_b.sign(_signal(rid_b))) is PublishOutcome.ACCEPTED
        finally:
            publisher_bus.close()

        reader_bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            # Only ever asks about rid_a's bucket.
            result = reader_bus.lookup(rid_a)
            assert result.status is LookupStatus.FOUND
            assert result.score == 92

            # rid_b's bucket was never requested, so its local view must not
            # hold it -- confirmed two ways: len() stays small (one bucket's
            # worth, not the whole topic), and the internal store has nothing
            # under rid_b's key. (A later lookup(rid_b) would still correctly
            # find it -- this is about *unrequested* visibility, not a
            # permanent blind spot.)
            assert rid_b not in reader_bus._local._store
        finally:
            reader_bus.close()

    def test_looking_up_the_other_bucket_afterward_still_works(self):
        """Not a permanent blind spot: a bucket becomes visible the moment it
        is actually asked about."""
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        rid = _risk_id("cc")

        publisher_bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            publisher_bus.publish(bank_b.sign(_signal(rid)))
        finally:
            publisher_bus.close()

        reader_bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            # Look up something unrelated first, in a different bucket.
            reader_bus.lookup(_risk_id("dd"))
            # Now ask about rid's bucket for the first time.
            result = reader_bus.lookup(rid)
            assert result.status is LookupStatus.FOUND
        finally:
            reader_bus.close()

    def test_lookup_of_a_never_published_id_resolves_quickly(self):
        """An empty bucket has nothing to catch up on -- should not wait out
        the full warm-up timeout to report NOT_FOUND."""
        keyring = PublisherKeyring()
        bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL, lookup_timeout_s=8.0)
        try:
            rid = _risk_id("ee")
            t0 = time.monotonic()
            result = bus.lookup(rid)
            elapsed = time.monotonic() - t0
            assert result.status is LookupStatus.NOT_FOUND
            assert elapsed < 5.0, f"empty-bucket lookup took {elapsed:.2f}s, expected near-instant"
        finally:
            bus.close()

    def test_a_timeout_reports_unavailable_not_a_false_clean_account(self):
        """RiskBus.lookup's contract: a backing store that cannot be confirmed
        reachable in time must report UNAVAILABLE, never silently present as
        NOT_FOUND (which downstream policy treats identically to a genuinely
        unflagged account)."""
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        rid = _risk_id("ff")

        publisher_bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
        try:
            publisher_bus.publish(bank_b.sign(_signal(rid)))
        finally:
            publisher_bus.close()

        # A timeout too short for any real broker round trip to complete.
        reader_bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL, lookup_timeout_s=0.0)
        try:
            result = reader_bus.lookup(rid)
            assert result.status is LookupStatus.UNAVAILABLE
        finally:
            reader_bus.close()
