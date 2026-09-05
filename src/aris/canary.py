"""Epoch canary: lets a node detect that its own key/epoch derivation has
drifted from the rest of the consortium, rather than silently mismatching on
every real signal (docs/SECURITY.md SS3.2).

A member with the correct key publishes a reserved, fixed-input marker signal
for the current epoch. Any node -- including the publisher itself -- can then
check whether *its own* derivation of that same reserved input finds it on the
bus. A mismatch (key rotated out of sync, wrong epoch, misconfigured salt)
shows up as this specific, well-known lookup failing, instead of as every real
account silently missing with no error and no alarm -- the exact failure mode
SS3.2 exists to close.
"""

from __future__ import annotations

from typing import Final

from aris.attestation import Publisher
from aris.bus import RiskBus
from aris.hashing import current_epoch, risk_id_for_account
from aris.schema import LookupStatus, RiskSignal

# Reserved, never a real account: CANARY_IFSC/CANARY_ACCOUNT exist only to give
# every node a shared, fixed input to derive and check against, independent of
# any live customer data.
CANARY_IFSC: Final = "ARIS0CANARY"
CANARY_ACCOUNT: Final = "EPOCH-CANARY"
CANARY_TTL_HOURS: Final = 48  # covers the 2-epoch dual-epoch lookup window
_CANARY_REASON_CODE: Final = "system_canary"
_CANARY_MODEL_VERSION: Final = "aris-canary"


def canary_risk_id(epoch: int, key: bytes | None = None) -> str:
    """The risk_id a canary for ``epoch`` derives to, under ``key`` (default:
    ``load_salt()``, via ``risk_id_for_account``)."""
    return risk_id_for_account(CANARY_IFSC, CANARY_ACCOUNT, key, epoch=epoch)


def publish_epoch_canary(
    bus: RiskBus, publisher: Publisher, epoch: int | None = None
) -> RiskSignal:
    """Publish a reserved marker signal for ``epoch`` (default: the current
    one), so peers can check their own key/epoch derivation against a
    known-good value already on the bus. Returns the published signal.
    """
    epoch = epoch if epoch is not None else current_epoch()
    signal = RiskSignal(
        risk_id=canary_risk_id(epoch),
        # 100, not 0: InMemoryRiskBus evicts the lowest max(risk_score) entry
        # first under global capacity pressure (see bus.py's _least_valuable),
        # so a score-0 "informational" canary would be the first thing
        # dropped under load -- exactly when a health check matters most. Safe
        # to max out here because CANARY_ACCOUNT is never a real transfer's
        # receiver, so this score is never evaluated by apply_policy.
        risk_score=100,
        confidence=1.0,
        reason_codes=(_CANARY_REASON_CODE,),
        model_version=_CANARY_MODEL_VERSION,
        source_bank_id=publisher.bank_id,
        ttl_hours=CANARY_TTL_HOURS,
        key_epoch=epoch,
    )
    bus.publish(publisher.sign(signal))
    return signal


def check_epoch_canary(bus: RiskBus, epoch: int | None = None, key: bytes | None = None) -> bool:
    """Return whether this node's key/epoch derivation matches what is
    already on the bus for ``epoch`` (default: the current one).

    True means some other member with the correct key has already published
    the canary for this epoch, and this node can find it under its own
    derivation -- the keys agree. False is ambiguous by construction: it means
    either nobody has published yet for a brand-new epoch, or this node's key
    is out of sync, and a single check cannot tell those apart. A real
    deployment runs this on a schedule and alarms only once it stays False
    past the grace period a fresh epoch needs for someone to publish into it.
    """
    epoch = epoch if epoch is not None else current_epoch()
    result = bus.lookup(canary_risk_id(epoch, key=key))
    return result.status is LookupStatus.FOUND
