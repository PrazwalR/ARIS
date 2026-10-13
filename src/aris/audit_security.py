"""Access controls and encryption-at-rest for the audit log (SS3.7).

AuditRecord already carries a ready-made (account -> risk_id) reverse table --
correct for an investigation, dangerous if a durable store holding it leaks.
This module is the three things docs/SECURITY.md SS3.7 calls out as missing:

- Records are encrypted before they sit in a sink's storage (AES-256-GCM,
  per-record random nonce, the audit_ref bound in as AEAD associated data so
  one record's ciphertext can't be replayed onto another's reference).
- A single read requires a named investigator identity and a stated reason,
  not a shared on/off secret.
- Every read attempt -- found or not -- is itself logged, so "who looked at
  this customer's account, and why" is always answerable.

Software stand-in, the same honest framing as aris.hsm for SS3.6: a real
deployment sources the key from a KMS/HSM, not an env var.
"""

from __future__ import annotations

import os
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, field_validator

AUDIT_KEY_ENV_VAR: Final = "ARIS_AUDIT_KEY"
_KEY_BYTES: Final = 32  # AES-256
_NONCE_BYTES: Final = 12
DEFAULT_ACCESS_LOG_CAPACITY: Final = 10_000


class AuditEncryptionKey:
    """A 32-byte AES-256-GCM key for audit records at rest."""

    __slots__ = ("_raw",)

    def __init__(self, raw: bytes) -> None:
        if len(raw) != _KEY_BYTES:
            raise ValueError(f"audit encryption key must be {_KEY_BYTES} bytes, got {len(raw)}")
        self._raw = raw

    @classmethod
    def generate(cls) -> AuditEncryptionKey:
        return cls(secrets.token_bytes(_KEY_BYTES))

    @classmethod
    def from_env(cls, var: str = AUDIT_KEY_ENV_VAR) -> AuditEncryptionKey:
        """Fails closed, the same way aris.hashing's ARIS_SALT does: an
        unconfigured key must never silently fall back to a generated one
        that no restart could recover the data under."""
        hex_key = os.environ.get(var)
        if not hex_key:
            raise RuntimeError(
                f"{var} is not set -- refusing to start an encrypted audit log without a "
                "durable key."
            )
        try:
            raw = bytes.fromhex(hex_key)
        except ValueError as exc:
            raise RuntimeError(f"{var} is not valid hex") from exc
        return cls(raw)

    def _aead(self) -> AESGCM:
        return AESGCM(self._raw)


@dataclass(frozen=True)
class EncryptedBlob:
    nonce: bytes
    ciphertext: bytes


def encrypt_record(key: AuditEncryptionKey, audit_ref: str, plaintext: bytes) -> EncryptedBlob:
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = key._aead().encrypt(nonce, plaintext, audit_ref.encode())
    return EncryptedBlob(nonce=nonce, ciphertext=ciphertext)


def decrypt_record(key: AuditEncryptionKey, audit_ref: str, blob: EncryptedBlob) -> bytes:
    """Raises cryptography.exceptions.InvalidTag if the ciphertext was
    tampered with, or if it was encrypted under a different audit_ref."""
    result: bytes = key._aead().decrypt(blob.nonce, blob.ciphertext, audit_ref.encode())
    return result


class InvestigatorCredential(BaseModel):
    """A named identity for one audit-log read, not a shared on/off secret.

    What AuditAccessLog records each access against -- see that class.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    investigator_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=256)

    @field_validator("investigator_id", "reason")
    @classmethod
    def _printable_ascii_only(cls, value: str) -> str:
        # Same rationale as TransferRequest's validator: both fields can end
        # up in a log line, so a control character or line separator could
        # forge an extra entry.
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        if not (stripped.isascii() and stripped.isprintable()):
            raise ValueError("value must be printable ASCII")
        return stripped


class AuditAccessEvent(BaseModel):
    """One read attempt against the audit log, successful or not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    investigator_id: str
    reason: str
    audit_ref: str
    found: bool
    accessed_at: datetime


class AuditAccessLog:
    """Append-only, bounded record of who read which audit_ref, when, and
    why -- including attempts against a reference that didn't exist, since
    that is itself an access attempt worth keeping.
    """

    def __init__(self, capacity: int = DEFAULT_ACCESS_LOG_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._entries: deque[AuditAccessEvent] = deque(maxlen=capacity)

    def record(self, event: AuditAccessEvent) -> None:
        self._entries.append(event)

    @property
    def entries(self) -> tuple[AuditAccessEvent, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
