"""The Shared Risk-Signal Bus.

``RiskBus`` is the interface every backend implements. ``InMemoryRiskBus`` is
the single-process implementation used by the demo and tests; the Kafka-backed
implementation planned for M3 (docs/PHASES.md) implements the same interface, so
BankBot does not change when the transport does.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Final

from aris.attestation import PublisherKeyring, SignedRiskSignal
from aris.schema import LookupStatus, RiskSignal

DEFAULT_MAX_ENTRIES: Final = 100_000

# No single member may occupy more than this fraction of the store. A bank that
# floods the bus evicts only its own entries once past the line.
DEFAULT_MAX_PUBLISHER_SHARE: Final = 0.25

# Replay high-water marks outlive the entries they guard, so the table is capped
# separately from the store.
DEFAULT_HIGH_WATER_ENTRIES: Final = 500_000


class PublishOutcome(str, Enum):
    """Why a publish did or did not land.

    A bare ``False`` could not distinguish a dropped fraud flag from a harmless
    no-op, so a publisher had no way to learn its signal never took effect.
    """

    ACCEPTED = "accepted"
    STALE = "stale"  # superseded by a newer signal from the same bank
    EXPIRED = "expired"  # already past its TTL on arrival
    QUOTA_EXCEEDED = "quota_exceeded"


@dataclass(frozen=True)
class LookupResult:
    """The outcome of a bus query.

    ``signal`` carries the highest live score across every bank that flagged the
    account. ``contributing_banks`` names each bank holding a live signal, which
    is what lets an analyst see that a block rests on more than one institution.
    """

    status: LookupStatus
    signal: RiskSignal | None = None
    contributing_banks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Coerce here so a backend that deserializes a status from JSON cannot
        # hand the policy engine a plain string it will fail to match.
        object.__setattr__(self, "status", LookupStatus(self.status))
        if self.status is LookupStatus.FOUND and self.signal is None:
            raise ValueError("LookupStatus.FOUND requires a signal")
        if self.status is not LookupStatus.FOUND and self.signal is not None:
            raise ValueError(f"{self.status} must not carry a signal")

    @property
    def score(self) -> int | None:
        return self.signal.risk_score if self.signal is not None else None


class RiskBus(ABC):
    """Transport-agnostic view of the risk bus."""

    @abstractmethod
    def publish(self, signed: SignedRiskSignal) -> PublishOutcome:
        """Record a signal after verifying it came from the bank it names.

        Raises ``UnknownPublisher`` or ``SignatureInvalid`` for a signal the
        consortium does not vouch for; those are rejections, not stale data.
        """

    @abstractmethod
    def lookup(self, risk_id: str) -> LookupResult:
        """Return the live risk for ``risk_id``.

        Implementations must return ``LookupStatus.UNAVAILABLE`` rather than
        raising when the backing store cannot be reached, so the caller's policy
        decides how to fail instead of an exception escaping into the transfer
        path.
        """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryRiskBus(RiskBus):
    """In-process bus, safe for concurrent use.

    Signals are kept per source bank rather than in one slot per account, so a
    member can only ever replace its own assessment -- and the bank name is
    taken from a verified signature, never from the payload alone, so it cannot
    be forged (see ``aris.attestation``).
    """

    def __init__(
        self,
        keyring: PublisherKeyring,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_publisher_share: float = DEFAULT_MAX_PUBLISHER_SHARE,
        now: Callable[[], datetime] = _utc_now,
        max_high_water_entries: int = DEFAULT_HIGH_WATER_ENTRIES,
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if not 0 < max_publisher_share <= 1:
            raise ValueError("max_publisher_share must be in (0, 1]")
        self._keyring = keyring
        self._max_entries = max_entries
        self._publisher_quota = max(1, int(max_entries * max_publisher_share))
        self._now = now
        self._lock = threading.RLock()
        # risk_id -> source_bank_id -> signal
        self._store: dict[str, dict[str, RiskSignal]] = {}
        # source_bank_id -> the risk_ids it currently contributes to
        self._by_publisher: dict[str, set[str]] = {}
        # (risk_id, bank) -> newest effective timestamp ever accepted. Kept
        # separately from the store so that evicting an entry cannot reset the
        # replay guard; a captured message would otherwise become replayable
        # again the moment its entry was flushed out.
        self._high_water: OrderedDict[tuple[str, str], datetime] = OrderedDict()
        self._max_high_water = max_high_water_entries

    def publish(self, signed: SignedRiskSignal) -> PublishOutcome:
        """Record a signal after verifying it came from the bank it names.

        Raises ``UnknownPublisher`` or ``SignatureInvalid`` for a signal the
        consortium does not vouch for; those are rejections, not stale data.
        """
        signal = self._keyring.verify(signed)
        now = self._now()

        with self._lock:
            if signal.is_expired(now):
                return PublishOutcome.EXPIRED

            bank = signal.source_bank_id
            key = (signal.risk_id, bank)

            # Clamp the publisher's claimed time to arrival time for ordering.
            # The schema tolerates a few minutes of clock skew, and a publisher
            # that stamps a signal slightly ahead would otherwise pin its own
            # slot -- silently dropping its fraud engine's genuine higher score
            # for the length of the skew window. Clamping keeps replay ordering
            # intact (an old captured message still sorts old) while removing the
            # ability to reserve the future.
            effective = min(signal.timestamp, now)

            previous_at = self._high_water.get(key)
            if previous_at is not None and effective <= previous_at:
                return PublishOutcome.STALE

            existing = self._store.get(signal.risk_id)
            is_new_contribution = existing is None or bank not in existing

            if is_new_contribution and not self._admit(bank, is_new_account=existing is None):
                # Reject rather than silently discarding one of this bank's own
                # existing flags: the publisher learns its signal did not land,
                # and no already-accepted fraud flag is destroyed to make room.
                return PublishOutcome.QUOTA_EXCEEDED

            self._store.setdefault(signal.risk_id, {})[bank] = signal
            self._by_publisher.setdefault(bank, set()).add(signal.risk_id)
            self._note_high_water(key, effective)
            return PublishOutcome.ACCEPTED

    def _note_high_water(self, key: tuple[str, str], effective: datetime) -> None:
        self._high_water[key] = effective
        self._high_water.move_to_end(key)
        while len(self._high_water) > self._max_high_water:
            self._high_water.popitem(last=False)

    def lookup(self, risk_id: str) -> LookupResult:
        now = self._now()
        with self._lock:
            by_bank = self._store.get(risk_id)
            if not by_bank:
                return LookupResult(status=LookupStatus.NOT_FOUND)

            for bank, sig in list(by_bank.items()):
                if sig.is_expired(now):
                    self._drop_contribution(risk_id, bank)

            live = self._store.get(risk_id)
            if not live:
                return LookupResult(status=LookupStatus.NOT_FOUND)

            # Highest score wins; the most recent assessment breaks a tie.
            worst = max(live.values(), key=lambda s: (s.risk_score, s.timestamp))
            return LookupResult(
                status=LookupStatus.FOUND,
                signal=worst,
                contributing_banks=tuple(sorted(live)),
            )

    def purge_expired(self) -> int:
        """Drop every expired signal. Returns the number of accounts removed.

        Expiry is otherwise only noticed when an account is looked up, so an
        account flagged once and never queried would hold memory until it
        happened to be evicted. Callers run this on a timer.
        """
        now = self._now()
        with self._lock:
            before = len(self._store)
            for risk_id, by_bank in list(self._store.items()):
                for bank, sig in list(by_bank.items()):
                    if sig.is_expired(now):
                        self._drop_contribution(risk_id, bank)
            return before - len(self._store)

    def _drop_contribution(self, risk_id: str, bank: str) -> None:
        """Remove one bank's signal, keeping the publisher index consistent."""
        by_bank = self._store.get(risk_id)
        if by_bank is not None:
            by_bank.pop(bank, None)
            if not by_bank:
                del self._store[risk_id]
        held = self._by_publisher.get(bank)
        if held is not None:
            held.discard(risk_id)
            if not held:
                del self._by_publisher[bank]

    def _admit(self, bank: str, is_new_account: bool) -> bool:
        """Decide whether a bank may take a new slot, making global room if needed.

        The per-publisher quota rejects rather than evicting. Evicting one of the
        publisher's own entries to make space looked equivalent, but the victim
        was chosen by the *network-wide* maximum score for the account -- which
        peer banks can raise at will by publishing high scores on other accounts.
        That let a registered member steer which of a rival's flags got dropped,
        erasing a specific flag without ever forging an identity.
        """
        held = self._by_publisher.get(bank, set())
        if len(held) >= self._publisher_quota:
            return False

        if not is_new_account or len(self._store) < self._max_entries:
            return True
        if self.purge_expired() and len(self._store) < self._max_entries:
            return True

        # Global pressure: drop the account carrying the least risk information.
        # Deliberately not "expires soonest" -- a flood published at the maximum
        # TTL outlives genuine signals sent at the default, so that ordering
        # would evict real fraud flags first and the attacker's junk last.
        victim = self._least_valuable(self._store.keys())
        if victim is None:
            return False
        for holder in list(self._store.get(victim, {})):
            self._drop_contribution(victim, holder)
        return True

    def _least_valuable(self, candidates: Iterable[str]) -> str | None:
        """The account carrying the least risk information, for eviction."""
        present = [rid for rid in candidates if rid in self._store]
        if not present:
            return None

        def rank(risk_id: str) -> tuple[int, datetime]:
            live = self._store[risk_id].values()
            return (
                max(s.risk_score for s in live),
                min(s.expires_at for s in live),
            )

        return min(present, key=rank)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
