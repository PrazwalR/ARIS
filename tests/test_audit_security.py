from __future__ import annotations

from datetime import datetime, timezone

import pytest
from cryptography.exceptions import InvalidTag
from pydantic import ValidationError

from aris.audit_security import (
    AUDIT_KEY_ENV_VAR,
    AuditAccessEvent,
    AuditAccessLog,
    AuditEncryptionKey,
    InvestigatorCredential,
    decrypt_record,
    encrypt_record,
)


class TestAuditEncryptionKey:
    def test_generate_produces_a_32_byte_key(self):
        key = AuditEncryptionKey.generate()
        assert len(key._raw) == 32

    def test_generate_produces_different_keys_each_time(self):
        assert AuditEncryptionKey.generate()._raw != AuditEncryptionKey.generate()._raw

    def test_rejects_a_key_of_the_wrong_length(self):
        with pytest.raises(ValueError, match="32 bytes"):
            AuditEncryptionKey(b"too-short")

    def test_from_env_fails_closed_when_unset(self, monkeypatch):
        monkeypatch.delenv(AUDIT_KEY_ENV_VAR, raising=False)
        with pytest.raises(RuntimeError, match=AUDIT_KEY_ENV_VAR):
            AuditEncryptionKey.from_env()

    def test_from_env_rejects_non_hex(self, monkeypatch):
        monkeypatch.setenv(AUDIT_KEY_ENV_VAR, "not hex at all!")
        with pytest.raises(RuntimeError, match="not valid hex"):
            AuditEncryptionKey.from_env()

    def test_from_env_loads_a_valid_key(self, monkeypatch):
        monkeypatch.setenv(AUDIT_KEY_ENV_VAR, "00" * 32)
        key = AuditEncryptionKey.from_env()
        assert key._raw == b"\x00" * 32


class TestEncryptDecryptRoundtrip:
    def test_roundtrips(self):
        key = AuditEncryptionKey.generate()
        blob = encrypt_record(key, "txn-1", b"secret payload")
        assert decrypt_record(key, "txn-1", blob) == b"secret payload"

    def test_ciphertext_does_not_contain_the_plaintext(self):
        key = AuditEncryptionKey.generate()
        blob = encrypt_record(key, "txn-1", b"ACC-0000999888")
        assert b"ACC-0000999888" not in blob.ciphertext

    def test_each_encryption_uses_a_fresh_nonce(self):
        key = AuditEncryptionKey.generate()
        a = encrypt_record(key, "txn-1", b"same payload")
        b = encrypt_record(key, "txn-1", b"same payload")
        assert a.nonce != b.nonce
        assert a.ciphertext != b.ciphertext

    def test_decrypting_under_the_wrong_audit_ref_fails(self):
        """The audit_ref is bound in as AEAD associated data -- a blob
        swapped onto a different reference must not decrypt."""
        key = AuditEncryptionKey.generate()
        blob = encrypt_record(key, "txn-1", b"payload")
        with pytest.raises(InvalidTag):
            decrypt_record(key, "txn-2", blob)

    def test_decrypting_with_the_wrong_key_fails(self):
        key_a = AuditEncryptionKey.generate()
        key_b = AuditEncryptionKey.generate()
        blob = encrypt_record(key_a, "txn-1", b"payload")
        with pytest.raises(InvalidTag):
            decrypt_record(key_b, "txn-1", blob)

    def test_tampered_ciphertext_fails_to_decrypt(self):
        key = AuditEncryptionKey.generate()
        blob = encrypt_record(key, "txn-1", b"payload")
        tampered_bytes = bytearray(blob.ciphertext)
        tampered_bytes[0] ^= 0xFF
        from aris.audit_security import EncryptedBlob

        tampered = EncryptedBlob(nonce=blob.nonce, ciphertext=bytes(tampered_bytes))
        with pytest.raises(InvalidTag):
            decrypt_record(key, "txn-1", tampered)


class TestInvestigatorCredential:
    def test_accepts_a_valid_identity_and_reason(self):
        cred = InvestigatorCredential(investigator_id="alice", reason="fraud case #42")
        assert cred.investigator_id == "alice"
        assert cred.reason == "fraud case #42"

    def test_rejects_a_blank_investigator_id(self):
        with pytest.raises(ValidationError):
            InvestigatorCredential(investigator_id="   ", reason="a reason")

    def test_rejects_a_blank_reason(self):
        with pytest.raises(ValidationError):
            InvestigatorCredential(investigator_id="alice", reason="")

    def test_rejects_non_ascii(self):
        with pytest.raises(ValidationError):
            InvestigatorCredential(investigator_id="alice", reason="café investigation")

    def test_rejects_control_characters_that_could_forge_a_log_line(self):
        with pytest.raises(ValidationError):
            InvestigatorCredential(investigator_id="alice", reason="reason\nFAKE: approved")

    def test_is_frozen(self):
        cred = InvestigatorCredential(investigator_id="alice", reason="r")
        with pytest.raises(ValidationError):
            cred.investigator_id = "mallory"  # type: ignore[misc]


class TestAuditAccessLog:
    def _event(self, ref: str = "txn-1", found: bool = True) -> AuditAccessEvent:
        return AuditAccessEvent(
            investigator_id="alice",
            reason="r",
            audit_ref=ref,
            found=found,
            accessed_at=datetime.now(timezone.utc),
        )

    def test_records_are_retrievable_in_order(self):
        log = AuditAccessLog()
        log.record(self._event("txn-1"))
        log.record(self._event("txn-2"))
        assert [e.audit_ref for e in log.entries] == ["txn-1", "txn-2"]
        assert len(log) == 2

    def test_is_bounded(self):
        log = AuditAccessLog(capacity=2)
        log.record(self._event("txn-1"))
        log.record(self._event("txn-2"))
        log.record(self._event("txn-3"))
        assert len(log) == 2
        assert [e.audit_ref for e in log.entries] == ["txn-2", "txn-3"]

    def test_rejects_a_non_positive_capacity(self):
        with pytest.raises(ValueError, match="positive"):
            AuditAccessLog(capacity=0)
