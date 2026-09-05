from __future__ import annotations

from aris.attestation import Publisher, PublisherKeyring
from aris.bus import InMemoryRiskBus
from aris.canary import check_epoch_canary, publish_epoch_canary
from aris.hashing import current_epoch
from aris.schema import RiskSignal

WRONG_KEY = bytes.fromhex("1a2b3c4d" * 8)


class TestEpochCanary:
    def test_a_correct_key_node_confirms_the_canary(
        self, keyring: PublisherKeyring, bank_b: Publisher
    ):
        bus = InMemoryRiskBus(keyring)
        publish_epoch_canary(bus, bank_b)
        assert check_epoch_canary(bus) is True

    def test_a_wrong_key_node_cannot_confirm_the_canary(
        self, keyring: PublisherKeyring, bank_b: Publisher
    ):
        """The core property this exists for: a node whose key has drifted
        derives a different risk_id for the same reserved input, so it
        cannot find the canary another (correctly-keyed) member already
        published -- the mismatch surfaces on this one well-known check
        instead of silently on every real signal."""
        bus = InMemoryRiskBus(keyring)
        publish_epoch_canary(bus, bank_b)  # published under the real ARIS_SALT
        assert check_epoch_canary(bus, key=WRONG_KEY) is False

    def test_nobody_has_published_yet_also_reads_as_false(self, keyring: PublisherKeyring):
        """Documented ambiguity: an empty bus and a wrong key look identical
        from a single check."""
        bus = InMemoryRiskBus(keyring)
        assert check_epoch_canary(bus) is False

    def test_canary_is_scoped_to_its_own_epoch(self, keyring: PublisherKeyring, bank_b: Publisher):
        bus = InMemoryRiskBus(keyring)
        epoch = current_epoch()
        publish_epoch_canary(bus, bank_b, epoch=epoch)
        assert check_epoch_canary(bus, epoch=epoch) is True
        assert check_epoch_canary(bus, epoch=epoch + 1) is False

    def test_published_signal_carries_the_given_epoch(
        self, keyring: PublisherKeyring, bank_b: Publisher
    ):
        bus = InMemoryRiskBus(keyring)
        epoch = current_epoch() - 5
        signal = publish_epoch_canary(bus, bank_b, epoch=epoch)
        assert signal.key_epoch == epoch

    def test_canary_survives_eviction_pressure(self, keyring: PublisherKeyring, bank_b: Publisher):
        """Regression: a score-0 canary would be the bus's first eviction
        choice under load (least valuable by max risk_score), undermining a
        health check that matters most exactly when the bus is busy."""
        bus = InMemoryRiskBus(keyring, max_entries=3, max_publisher_share=1.0)
        publish_epoch_canary(bus, bank_b)
        for i in range(10):
            bus.publish(
                bank_b.sign(
                    RiskSignal(
                        risk_id=f"{i:064x}",
                        risk_score=1,
                        confidence=0.9,
                        reason_codes=("high_velocity",),
                        model_version="v0.4-fl",
                        source_bank_id="BANK-B",
                    )
                )
            )
        assert check_epoch_canary(bus) is True
