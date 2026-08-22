"""Publisher authentication for the Shared Risk-Signal Bus.

``source_bank_id`` is a field inside the payload. On its own it is an
unverified claim: any member of the consortium can set it to a peer's name,
publish a score of zero, and erase that peer's fraud signal -- which would let
whoever compromises a single participant unblock any mule account in the
network. Partitioning signals per bank only means something if the bank name is
authenticated.

Every signal is therefore signed by its publisher with Ed25519 and verified
against a registered public key before the bus will store it. Signing the
payload end-to-end -- rather than relying on the transport -- also covers the
bus operator itself, who would otherwise be able to alter signals in flight or
at rest without detection.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from aris.schema import RiskSignal

# Domain separator: keeps a signature made here from ever verifying as a
# signature over some other ARIS structure.
_SIGNING_DOMAIN: Final = b"ARIS-risk-signal-v1\x00"


class UnknownPublisher(Exception):
    """The signal names a bank with no registered signing key."""


class SignatureInvalid(Exception):
    """The signature does not verify, or does not match the claimed bank."""


def canonical_bytes(signal: RiskSignal) -> bytes:
    """Deterministically serialize a signal for signing.

    Sorted keys and fixed separators so that the publisher and every verifier
    reconstruct byte-identical input from the same signal.
    """
    payload = json.dumps(
        signal.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return _SIGNING_DOMAIN + payload.encode("ascii")


@dataclass(frozen=True)
class SignedRiskSignal:
    """A risk signal together with its publisher's signature."""

    signal: RiskSignal
    signature: bytes

    @property
    def source_bank_id(self) -> str:
        return self.signal.source_bank_id


class Publisher:
    """A member bank's signing identity."""

    def __init__(self, bank_id: str, private_key: Ed25519PrivateKey) -> None:
        self.bank_id = bank_id
        self._private_key = private_key

    @classmethod
    def generate(cls, bank_id: str) -> Publisher:
        return cls(bank_id, Ed25519PrivateKey.generate())

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._private_key.public_key()

    def sign(self, signal: RiskSignal) -> SignedRiskSignal:
        if signal.source_bank_id != self.bank_id:
            raise SignatureInvalid(
                f"{self.bank_id} cannot sign a signal attributed to {signal.source_bank_id}"
            )
        return SignedRiskSignal(signal, self._private_key.sign(canonical_bytes(signal)))


class PublisherKeyring:
    """The consortium's registry of member signing keys.

    In deployment this is provisioned out of band -- alongside the mTLS trust
    material -- and is the list a bank must be removed from to be ejected.
    """

    def __init__(self) -> None:
        self._keys: dict[str, Ed25519PublicKey] = {}

    def register(self, bank_id: str, public_key: Ed25519PublicKey) -> None:
        existing = self._keys.get(bank_id)
        if existing is not None and existing.public_bytes_raw() != public_key.public_bytes_raw():
            # Silent rebinding would let a rotation race hand a bank's identity
            # to someone else; re-keying goes through revoke() deliberately.
            raise SignatureInvalid(f"{bank_id} already has a different registered key")
        self._keys[bank_id] = public_key

    def revoke(self, bank_id: str) -> None:
        self._keys.pop(bank_id, None)

    def trusts(self, bank_id: str) -> bool:
        return bank_id in self._keys

    def verify(self, signed: SignedRiskSignal) -> RiskSignal:
        """Return the signal if it genuinely came from the bank it names."""
        public_key = self._keys.get(signed.source_bank_id)
        if public_key is None:
            raise UnknownPublisher(f"no registered signing key for {signed.source_bank_id!r}")
        try:
            public_key.verify(signed.signature, canonical_bytes(signed.signal))
        except InvalidSignature as exc:
            raise SignatureInvalid(
                f"signature does not verify for {signed.source_bank_id}"
            ) from exc
        return signed.signal
