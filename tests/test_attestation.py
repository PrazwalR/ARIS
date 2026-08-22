from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aris.attestation import (
    Publisher,
    PublisherKeyring,
    SignatureInvalid,
    SignedRiskSignal,
    UnknownPublisher,
    canonical_bytes,
)
from aris.schema import RiskSignal

RID = "a" * 64


def signal(**kw) -> RiskSignal:
    base = {
        "risk_id": RID,
        "risk_score": 92,
        "confidence": 0.94,
        "reason_codes": ("high_velocity",),
        "model_version": "v1",
        "source_bank_id": "BANK-B",
    }
    base.update(kw)
    return RiskSignal(**base)


class TestCanonicalSerialization:
    def test_is_deterministic(self):
        """Publisher and verifier must rebuild byte-identical input."""
        ts = datetime.now(timezone.utc)
        assert canonical_bytes(signal(timestamp=ts)) == canonical_bytes(signal(timestamp=ts))

    def test_field_order_does_not_matter(self):
        ts = datetime.now(timezone.utc)
        a = RiskSignal(
            risk_id=RID,
            risk_score=92,
            confidence=0.94,
            reason_codes=("high_velocity",),
            model_version="v1",
            source_bank_id="BANK-B",
            timestamp=ts,
        )
        b = RiskSignal(
            source_bank_id="BANK-B",
            model_version="v1",
            reason_codes=("high_velocity",),
            confidence=0.94,
            risk_score=92,
            risk_id=RID,
            timestamp=ts,
        )
        assert canonical_bytes(a) == canonical_bytes(b)

    @pytest.mark.parametrize(
        "change",
        [
            {"risk_score": 0},
            {"source_bank_id": "BANK-C"},
            {"risk_id": "b" * 64},
            {"confidence": 0.1},
            {"reason_codes": ("mule_account",)},
            {"model_version": "v2"},
            {"ttl_hours": 48},
        ],
    )
    def test_every_field_is_covered_by_the_signature(self, change):
        """A field outside the signed bytes could be altered in transit."""
        assert canonical_bytes(signal()) != canonical_bytes(signal(**change))

    def test_carries_a_domain_separator(self):
        """Stops a signature here from verifying over some other ARIS structure."""
        assert canonical_bytes(signal()).startswith(b"ARIS-risk-signal-v1\x00")


class TestSigning:
    def test_round_trip_verifies(self):
        keyring = PublisherKeyring()
        bank = Publisher.generate("BANK-B")
        keyring.register(bank.bank_id, bank.public_key)
        original = signal()
        assert keyring.verify(bank.sign(original)) == original

    def test_publisher_refuses_to_sign_for_another_bank(self):
        with pytest.raises(SignatureInvalid, match="cannot sign"):
            Publisher.generate("BANK-EVIL").sign(signal(source_bank_id="BANK-B"))

    def test_signature_from_the_wrong_key_is_rejected(self):
        keyring = PublisherKeyring()
        bank = Publisher.generate("BANK-B")
        keyring.register(bank.bank_id, bank.public_key)
        payload = signal()
        wrong = SignedRiskSignal(
            payload, Ed25519PrivateKey.generate().sign(canonical_bytes(payload))
        )
        with pytest.raises(SignatureInvalid):
            keyring.verify(wrong)

    def test_swapped_payload_under_a_valid_signature_is_rejected(self):
        keyring = PublisherKeyring()
        bank = Publisher.generate("BANK-B")
        keyring.register(bank.bank_id, bank.public_key)
        genuine = bank.sign(signal(risk_score=92))
        with pytest.raises(SignatureInvalid):
            keyring.verify(SignedRiskSignal(signal(risk_score=0), genuine.signature))

    def test_garbage_signature_is_rejected(self):
        keyring = PublisherKeyring()
        bank = Publisher.generate("BANK-B")
        keyring.register(bank.bank_id, bank.public_key)
        with pytest.raises(SignatureInvalid):
            keyring.verify(SignedRiskSignal(signal(), b"\x00" * 64))


class TestKeyring:
    def test_unregistered_publisher_is_rejected(self):
        stranger = Publisher.generate("BANK-X")
        with pytest.raises(UnknownPublisher):
            PublisherKeyring().verify(stranger.sign(signal(source_bank_id="BANK-X")))

    def test_registering_the_same_key_twice_is_idempotent(self):
        keyring = PublisherKeyring()
        bank = Publisher.generate("BANK-B")
        keyring.register(bank.bank_id, bank.public_key)
        keyring.register(bank.bank_id, bank.public_key)
        assert keyring.trusts("BANK-B")

    def test_silent_identity_rebinding_is_refused(self):
        """A rotation race must not hand a bank's identity to someone else."""
        keyring = PublisherKeyring()
        real = Publisher.generate("BANK-B")
        keyring.register("BANK-B", real.public_key)
        with pytest.raises(SignatureInvalid, match="already has a different"):
            keyring.register("BANK-B", Publisher.generate("BANK-B").public_key)

    def test_revoke_then_reregister_allows_deliberate_rotation(self):
        keyring = PublisherKeyring()
        keyring.register("BANK-B", Publisher.generate("BANK-B").public_key)
        keyring.revoke("BANK-B")
        assert not keyring.trusts("BANK-B")
        replacement = Publisher.generate("BANK-B")
        keyring.register("BANK-B", replacement.public_key)
        rotated = signal()
        assert keyring.verify(replacement.sign(rotated)) == rotated
