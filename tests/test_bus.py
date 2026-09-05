from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from aris.attestation import (
    Publisher,
    SignatureInvalid,
    SignedRiskSignal,
    UnknownPublisher,
    canonical_bytes,
)
from aris.bus import InMemoryRiskBus, LookupResult, PublishOutcome
from aris.schema import LookupStatus, RiskSignal

RID = "a" * 64
OTHER_RID = "b" * 64


def signal(risk_id=RID, score=92, bank="BANK-B", ttl=24, ts=None) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=0.9,
        reason_codes=("high_velocity",),
        model_version="v0.4-fl",
        source_bank_id=bank,
        ttl_hours=ttl,
        timestamp=ts or datetime.now(timezone.utc),
    )


class TestLookup:
    def test_unknown_account_reports_not_found_not_unavailable(self, keyring):
        result = InMemoryRiskBus(keyring).lookup(RID)
        assert result.status is LookupStatus.NOT_FOUND
        assert result.score is None

    def test_publish_then_lookup(self, keyring, bank_b):
        bus = InMemoryRiskBus(keyring)
        bus.publish(bank_b.sign(signal()))
        result = bus.lookup(RID)
        assert result.status is LookupStatus.FOUND
        assert result.score == 92
        assert result.contributing_banks == ("BANK-B",)


class TestPublisherAuthentication:
    def test_a_member_cannot_forge_another_banks_identity(self, keyring, bank_b, bank_evil):
        """Regression: source_bank_id was an unauthenticated payload field, so
        any member could impersonate a peer and erase its fraud signal."""
        bus = InMemoryRiskBus(keyring)
        bus.publish(bank_b.sign(signal(score=92, bank="BANK-B")))

        forged = signal(score=0, bank="BANK-B")
        wire = SignedRiskSignal(forged, bank_evil._private_key.sign(canonical_bytes(forged)))
        with pytest.raises(SignatureInvalid):
            bus.publish(wire)

        assert bus.lookup(RID).score == 92

    def test_a_publisher_refuses_to_sign_for_another_bank(self, bank_evil):
        with pytest.raises(SignatureInvalid):
            bank_evil.sign(signal(bank="BANK-B"))

    def test_unregistered_bank_is_rejected(self, keyring):
        stranger = Publisher.generate("BANK-X")
        with pytest.raises(UnknownPublisher):
            InMemoryRiskBus(keyring).publish(stranger.sign(signal(bank="BANK-X")))

    def test_tampering_with_a_signed_payload_is_detected(self, keyring, bank_b):
        signed = bank_b.sign(signal(score=92))
        tampered = SignedRiskSignal(signal(score=0), signed.signature)
        with pytest.raises(SignatureInvalid):
            InMemoryRiskBus(keyring).publish(tampered)

    def test_revoked_member_can_no_longer_publish(self, keyring, bank_b):
        bus = InMemoryRiskBus(keyring)
        keyring.revoke("BANK-B")
        with pytest.raises(UnknownPublisher):
            bus.publish(bank_b.sign(signal()))

    def test_rebinding_an_identity_to_a_new_key_is_refused(self, keyring, bank_b):
        impostor = Publisher.generate("BANK-B")
        with pytest.raises(SignatureInvalid):
            keyring.register("BANK-B", impostor.public_key)


class TestHostilePublisher:
    def test_a_bank_cannot_erase_another_banks_flag(self, keyring, bank_b, bank_evil):
        bus = InMemoryRiskBus(keyring)
        bus.publish(bank_b.sign(signal(score=92, bank="BANK-B")))
        bus.publish(bank_evil.sign(signal(score=0, bank="BANK-EVIL")))
        result = bus.lookup(RID)
        assert result.score == 92
        assert result.signal.source_bank_id == "BANK-B"
        assert result.contributing_banks == ("BANK-B", "BANK-EVIL")

    def test_a_bank_can_revise_its_own_assessment_downward(self, keyring, bank_b):
        bus = InMemoryRiskBus(keyring)
        now = datetime.now(timezone.utc)
        # >1 timestamp bucket apart: see schema.TIMESTAMP_BUCKET's documented
        # trade-off -- two publishes landing in the same minute bucket would
        # collapse to the same effective time and the second would (correctly)
        # read as no newer, which is not what this test means to exercise.
        bus.publish(bank_b.sign(signal(score=92, ts=now)))
        bus.publish(bank_b.sign(signal(score=10, ts=now + timedelta(minutes=2))))
        assert bus.lookup(RID).score == 10

    def test_stale_republish_is_rejected(self, keyring, bank_b):
        """A captured message must not reinstate a withdrawn assessment."""
        bus = InMemoryRiskBus(keyring)
        now = datetime.now(timezone.utc)
        old = bank_b.sign(signal(score=92, ts=now - timedelta(hours=1)))
        bus.publish(bank_b.sign(signal(score=5, ts=now)))
        assert bus.publish(old) is PublishOutcome.STALE
        assert bus.lookup(RID).score == 5

    def test_highest_score_wins_across_banks(self, keyring):
        bus = InMemoryRiskBus(keyring)
        for bank, score in [("BANK-A", 30), ("BANK-C", 95), ("BANK-D", 60)]:
            pub = Publisher.generate(bank)
            keyring.register(bank, pub.public_key)
            bus.publish(pub.sign(signal(score=score, bank=bank)))
        assert bus.lookup(RID).score == 95


class TestExpiry:
    def test_expired_signal_is_not_returned(self, keyring, bank_b):
        clock = {"now": datetime.now(timezone.utc)}
        bus = InMemoryRiskBus(keyring, now=lambda: clock["now"])
        bus.publish(bank_b.sign(signal(ttl=1)))
        clock["now"] += timedelta(hours=2)
        assert bus.lookup(RID).status is LookupStatus.NOT_FOUND

    def test_expiry_of_one_bank_leaves_the_others(self, keyring, bank_b):
        clock = {"now": datetime.now(timezone.utc)}
        bus = InMemoryRiskBus(keyring, now=lambda: clock["now"])
        bank_c = Publisher.generate("BANK-C")
        keyring.register("BANK-C", bank_c.public_key)
        bus.publish(bank_b.sign(signal(score=95, ttl=1)))
        bus.publish(bank_c.sign(signal(score=60, bank="BANK-C", ttl=48, ts=clock["now"])))
        clock["now"] += timedelta(hours=2)
        result = bus.lookup(RID)
        assert result.score == 60
        assert result.contributing_banks == ("BANK-C",)

    def test_already_expired_publish_is_dropped(self, keyring, bank_b):
        clock = {"now": datetime.now(timezone.utc)}
        bus = InMemoryRiskBus(keyring, now=lambda: clock["now"])
        stale = bank_b.sign(signal(ttl=1, ts=clock["now"] - timedelta(hours=5)))
        assert bus.publish(stale) is PublishOutcome.EXPIRED
        assert len(bus) == 0

    def test_purge_reclaims_accounts_nobody_queried(self, keyring, bank_b):
        """Lazy expiry alone leaks memory for never-looked-up accounts."""
        clock = {"now": datetime.now(timezone.utc)}
        bus = InMemoryRiskBus(keyring, max_entries=200, now=lambda: clock["now"])
        for i in range(50):
            bus.publish(bank_b.sign(signal(risk_id=f"{i:064x}", ttl=1)))
        clock["now"] += timedelta(hours=2)
        assert bus.purge_expired() == 50
        assert len(bus) == 0


class TestCapacity:
    def test_store_stays_bounded_under_a_flood(self, keyring, bank_evil):
        bus = InMemoryRiskBus(keyring, max_entries=10)
        for i in range(500):
            bus.publish(bank_evil.sign(signal(risk_id=f"{i:064x}", bank="BANK-EVIL")))
        assert len(bus) <= 10

    def test_a_flood_cannot_evict_genuine_high_risk_signals(self, keyring, bank_b, bank_evil):
        """Regression: eviction preferred the earliest expiry, so a flood sent at
        the maximum TTL outlived and displaced real flags sent at the default."""
        bus = InMemoryRiskBus(keyring, max_entries=50)
        bus.publish(bank_b.sign(signal(risk_id="f" * 64, score=99, ttl=24)))
        for i in range(5000):
            bus.publish(
                bank_evil.sign(signal(risk_id=f"{i:064x}", score=1, bank="BANK-EVIL", ttl=168))
            )
        result = bus.lookup("f" * 64)
        assert result.status is LookupStatus.FOUND
        assert result.score == 99

    def test_one_publisher_cannot_occupy_the_whole_store(self, keyring, bank_evil):
        bus = InMemoryRiskBus(keyring, max_entries=100, max_publisher_share=0.25)
        for i in range(500):
            bus.publish(bank_evil.sign(signal(risk_id=f"{i:064x}", bank="BANK-EVIL")))
        assert len(bus) <= 25

    def test_updating_an_existing_account_does_not_evict_the_others(self, keyring, bank_b):
        """Fills the store first, so an update that wrongly evicted a peer entry
        would actually be observable."""
        bus = InMemoryRiskBus(keyring, max_entries=5, max_publisher_share=1.0)
        held = [f"{i:064x}" for i in range(5)]
        for rid in held:
            bus.publish(bank_b.sign(signal(risk_id=rid, score=50)))
        assert len(bus) == 5

        for _ in range(20):
            bus.publish(bank_b.sign(signal(risk_id=held[0], score=60)))

        for rid in held:
            assert bus.lookup(rid).status is LookupStatus.FOUND, f"{rid[:8]} was evicted"

    def test_quota_rejects_rather_than_discarding_an_accepted_flag(self, keyring, bank_evil):
        """Regression: at quota the bus dropped one of the publisher's own live
        flags and still returned success, so a bank believed a flag was live when
        it had been thrown away."""
        bus = InMemoryRiskBus(keyring, max_entries=100, max_publisher_share=0.05)
        outcomes = [
            bus.publish(bank_evil.sign(signal(risk_id=f"{i:064x}", bank="BANK-EVIL")))
            for i in range(10)
        ]
        assert PublishOutcome.QUOTA_EXCEEDED in outcomes
        assert outcomes.count(PublishOutcome.ACCEPTED) == 5

    def test_a_peer_cannot_steer_which_flag_is_dropped(self, keyring, bank_b, bank_evil):
        """Regression: a publisher's quota victim was ranked by the NETWORK-wide
        max score for each account, which peers can raise at will. That let a
        registered member erase a specific rival flag without forging anything."""
        bus = InMemoryRiskBus(keyring, max_entries=100, max_publisher_share=0.05)
        target = "d" * 64
        bus.publish(bank_b.sign(signal(risk_id=target, score=99)))
        others = [f"{i:064x}" for i in range(4)]
        for rid in others:
            bus.publish(bank_b.sign(signal(risk_id=rid, score=99)))
        # BANK-EVIL raises the apparent value of every account except the target.
        for rid in others:
            bus.publish(bank_evil.sign(signal(risk_id=rid, score=100, bank="BANK-EVIL")))
        bus.publish(bank_b.sign(signal(risk_id="e" * 64, score=50)))

        assert bus.lookup(target).score == 99, "peer steered eviction of a rival's flag"


class TestConcurrency:
    def test_concurrent_publish_and_lookup_is_consistent(self, keyring, bank_b):
        """The bus is destined to sit behind a threaded API server (M4)."""
        bus = InMemoryRiskBus(keyring, max_entries=4000)
        errors: list[BaseException] = []

        def worker(start: int) -> None:
            try:
                for i in range(start, start + 200):
                    bus.publish(bank_b.sign(signal(risk_id=f"{i:064x}", score=i % 101)))
                    bus.lookup(f"{i:064x}")
                    bus.purge_expired()
            except BaseException as exc:  # surfaced to the main thread below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n * 200,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors

        # "No exception raised" would also hold if aggregation were inverted or
        # the publisher index had drifted, so assert the invariants directly.
        assert len(bus) <= 4000
        for bank, held in bus._by_publisher.items():
            assert len(held) <= bus._publisher_quota
            for rid in held:
                assert bank in bus._store[rid], "publisher index names a dropped entry"
        for rid, by_bank in bus._store.items():
            assert by_bank, "empty account left in the store"
            expected = max(s.risk_score for s in by_bank.values())
            assert bus.lookup(rid).score == expected, "lookup did not return the highest score"

    def test_lookup_returns_the_highest_score_not_the_lowest(self, keyring):
        """Pins the aggregation rule itself: the concurrency test above passed
        with max() mutated to min(), inverting the core safety property."""
        bus = InMemoryRiskBus(keyring)
        for bank, score in [("BANK-A", 10), ("BANK-B", 95), ("BANK-C", 40)]:
            pub = Publisher.generate(bank)
            keyring.register(bank, pub.public_key)
            bus.publish(pub.sign(signal(score=score, bank=bank)))
        assert bus.lookup(RID).score == 95


class TestReplayGuardDurability:
    def test_eviction_does_not_reinstate_a_withdrawn_flag(self, keyring, bank_b, bank_evil):
        """Regression: the replay high-water mark lived only in the store entry,
        so evicting the entry destroyed the guard and a captured signed message
        became replayable. Least-risk-first eviction made this easier, not
        harder: an account a bank has just cleared to 0 is the first thing chosen
        for eviction, so the policy actively selected the guards worth destroying.
        """
        bus = InMemoryRiskBus(keyring, max_entries=3, max_publisher_share=1.0)
        now = datetime.now(timezone.utc)
        captured = bank_b.sign(signal(risk_id=RID, score=99, ttl=168, ts=now))
        bus.publish(captured)
        bus.publish(
            bank_b.sign(signal(risk_id=RID, score=0, ttl=168, ts=now + timedelta(minutes=2)))
        )
        assert bus.lookup(RID).score == 0

        assert bus.publish(captured) is PublishOutcome.STALE

        for i in range(50):
            bus.publish(bank_evil.sign(signal(risk_id=f"{i:064x}", score=1, bank="BANK-EVIL")))
        assert bus.lookup(RID).status is LookupStatus.NOT_FOUND

        assert bus.publish(captured) is PublishOutcome.STALE
        assert bus.lookup(RID).status is LookupStatus.NOT_FOUND


class TestClockSkewAbuse:
    def test_a_future_dated_publish_cannot_pin_a_banks_own_slot(self, keyring, bank_b):
        """Regression: the replay guard trusted the publisher's timestamp, and
        the schema tolerates 5 minutes of skew, so a node stamping slightly ahead
        silently suppressed its own fraud engine's later, higher score for the
        whole skew window -- reachable through plain NTP drift between a bank's
        own nodes, not just by an attacker."""
        start = datetime.now(timezone.utc)
        clock = {"now": start}
        bus = InMemoryRiskBus(keyring, now=lambda: clock["now"])

        bus.publish(bank_b.sign(signal(score=0, ts=start + timedelta(minutes=4))))
        assert bus.lookup(RID).score == 0

        # >1 timestamp bucket after `start` (not +1s): the schema quantises
        # signal.timestamp to the minute *before* the bus ever clamps it, so a
        # gap inside one bucket isn't reliably distinguishable from the first
        # publish's clamped-to-arrival-time high water mark. See TIMESTAMP_BUCKET.
        clock["now"] = start + timedelta(minutes=2)
        outcome = bus.publish(bank_b.sign(signal(score=95, ts=clock["now"])))
        assert outcome is PublishOutcome.ACCEPTED
        assert bus.lookup(RID).score == 95


class TestLookupResultValidation:
    def test_a_deserialized_string_status_is_coerced(self, keyring):
        assert LookupResult(status="unavailable").status is LookupStatus.UNAVAILABLE

    def test_an_unknown_status_is_rejected_at_construction(self):
        with pytest.raises(ValueError):
            LookupResult(status="totally-bogus")

    def test_found_without_a_signal_is_rejected(self):
        with pytest.raises(ValueError, match="requires a signal"):
            LookupResult(status=LookupStatus.FOUND)
