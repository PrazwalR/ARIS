"""One interface for every way a bank can derive ``risk_id`` (docs/SECURITY.md
SS3.1, SS3.2, SS3.6).

Publisher and checker must derive the *same* id for the same ``(ifsc,
account)`` or a flag is silently never found -- and a miss resolves to allow.
Routing both sides through one ``RiskIdDeriver`` makes the scheme a single
deployment choice instead of something each call site picks for itself.

- ``HmacRiskIdDeriver``: HMAC under the consortium key from ``ARIS_SALT``,
  daily epoch subkeys. The default.
- ``HsmRiskIdDeriver``: the same derivation and output, but the root key is a
  non-extractable PKCS#11 object (SS3.6). Interoperable with the HMAC scheme
  when the token holds the same key material.
- ``OprfRiskIdDeriver``: blind evaluation by the consortium authority (SS3.1).
  A bank cannot compute an id offline at all. Ids do not rotate with epochs;
  rotating the authority's key is what rotates them.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from aris.hashing import current_epoch, risk_id_for_account

if TYPE_CHECKING:
    from pkcs11 import SecretKey

    from aris.oprf import OprfAuthorityClient


class RiskIdDeriver(Protocol):
    """Every candidate id a lookup should try, preferred first. The first one
    is also the id a publisher signs under."""

    def candidates(self, ifsc: str, account: str, at: datetime) -> tuple[str, ...]: ...


def publish_id(deriver: RiskIdDeriver, ifsc: str, account: str, at: datetime) -> str:
    """The id a publisher should sign a signal under."""
    return deriver.candidates(ifsc, account, at)[0]


class HmacRiskIdDeriver:
    """Current epoch first, then the previous one, so a signal published just
    before a UTC-day boundary is still found just after it (SS3.2)."""

    def candidates(self, ifsc: str, account: str, at: datetime) -> tuple[str, ...]:
        epoch = current_epoch(at)
        return (
            risk_id_for_account(ifsc, account, epoch=epoch),
            risk_id_for_account(ifsc, account, epoch=epoch - 1),
        )


class HsmRiskIdDeriver:
    def __init__(self, key: SecretKey) -> None:
        self._key = key

    def candidates(self, ifsc: str, account: str, at: datetime) -> tuple[str, ...]:
        from aris.hsm import risk_id_for_account_hsm

        epoch = current_epoch(at)
        return (
            risk_id_for_account_hsm(ifsc, account, self._key, epoch=epoch),
            risk_id_for_account_hsm(ifsc, account, self._key, epoch=epoch - 1),
        )


class OprfRiskIdDeriver:
    """One authority round trip per derivation. Raises whatever the authority
    raises (rate limit, transport failure, a response that fails to verify);
    ``BankBot`` treats any of those as the bus being unavailable, never as
    "not flagged"."""

    def __init__(self, authority: OprfAuthorityClient, bank_id: str) -> None:
        self._authority = authority
        self._bank_id = bank_id

    # `at` is unused: OPRF ids do not rotate by epoch.
    def candidates(self, ifsc: str, account: str, at: datetime) -> tuple[str, ...]:  # noqa: ARG002
        from aris.oprf import risk_id_for_account_oprf

        return (risk_id_for_account_oprf(ifsc, account, self._authority, self._bank_id),)
