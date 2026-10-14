"""BankBot's pre-transaction check.

The bot sits in the transfer path: it derives the receiver's ``risk_id``, asks
the bus what the network knows, applies policy, and writes an audit record for
every decision -- including the ones that allow the transfer.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aris.audit_security import (
    AuditAccessEvent,
    AuditAccessLog,
    AuditEncryptionKey,
    EncryptedBlob,
    InvestigatorCredential,
    decrypt_record,
    encrypt_record,
)
from aris.bus import LookupResult, RiskBus
from aris.hashing import normalize_account, normalize_ifsc
from aris.risk_id import HmacRiskIdDeriver, RiskIdDeriver
from aris.schema import Decision, LookupStatus, PolicyConfig, apply_policy

logger = logging.getLogger(__name__)

MINOR_UNITS_PER_MAJOR: Final = 100
DEFAULT_AUDIT_CAPACITY: Final = 10_000
DEFAULT_REPLAY_CACHE: Final = 10_000
# Recorded in place of a risk_id when none could be derived at all (an OPRF
# authority that is down or rate limiting this bank).
RISK_ID_UNAVAILABLE: Final = ""

_BLOCK_MESSAGE: Final = (
    "This transfer looks risky. The receiver account has been flagged for "
    "suspicious activity by our fraud network. For your safety, this "
    "transaction is blocked. Please contact support if you believe this is a "
    "mistake."
)
_STEP_UP_MESSAGE: Final = (
    "For your security we need to verify this transfer before sending the "
    "money. Please complete the additional verification step."
)
_ALLOW_MESSAGE: Final = "Transfer looks OK. Proceeding."


class TransferRequest(BaseModel):
    """A customer's instruction to send money."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    user_ref: str = Field(min_length=1, max_length=64)
    bank_id: str = Field(min_length=1, max_length=32)
    # The receiver's IFSC, not the sender's: an account number is unique only
    # within its own bank, so risk_id must be derived from the pair. See
    # docs/SECURITY.md SS3.3.
    receiver_ifsc: str = Field(min_length=11, max_length=11)
    receiver_account: str = Field(min_length=1, max_length=64)

    # Money is held in integer minor units (paise). A float amount cannot
    # represent a decimal value exactly and silently accepts NaN and infinity,
    # neither of which should ever reach a threshold comparison.
    amount_minor: int = Field(gt=0, le=10**15)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")

    # Idempotency key. Supplied by the caller when it has one so a retried
    # request reconciles to a single audited decision.
    transfer_id: str = Field(default_factory=lambda: str(uuid.uuid4()), min_length=8, max_length=64)

    @field_validator("user_ref", "bank_id", "receiver_account", "transfer_id")
    @classmethod
    def _printable_ascii_only(cls, value: str) -> str:
        # These fields are written to the audit log, so anything a log viewer may
        # treat as a line break lets a crafted value forge an extra audit line.
        # This is an allowlist on purpose: a denylist on ASCII control characters
        # misses U+0085, U+2028 and U+2029, all of which str.splitlines() and
        # most viewers break on, and U+202E, which reverses the displayed text.
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        if not (stripped.isascii() and stripped.isprintable()):
            raise ValueError("value must be printable ASCII")
        return stripped

    @field_validator("receiver_ifsc")
    @classmethod
    def _check_receiver_ifsc(cls, value: str) -> str:
        # normalize_ifsc's strict [A-Z]{4}0[A-Z0-9]{6} pattern is already a
        # stronger guarantee than the printable-ASCII check above, so this
        # does not also need _printable_ascii_only.
        return normalize_ifsc(value)

    @classmethod
    def from_major_units(cls, amount: Decimal | int | str, **kwargs: object) -> TransferRequest:
        """Build a request from a rupee amount such as ``Decimal("5000.00")``."""
        try:
            quantised = Decimal(amount).scaleb(2)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError(f"invalid amount: {amount!r}") from exc
        if quantised != quantised.to_integral_value():
            raise ValueError("amount has more precision than the currency allows")
        return cls(amount_minor=int(quantised), **kwargs)  # type: ignore[arg-type]

    @property
    def amount_major(self) -> Decimal:
        return Decimal(self.amount_minor) / MINOR_UNITS_PER_MAJOR


class AuditRecord(BaseModel):
    """One durable decision record.

    Carries enough identity to reconcile against the core banking ledger --
    which transfer, whose, how much -- alongside the evidence the decision rested
    on. The original code logged only the decision and score, which cannot be
    tied back to a transaction during an investigation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    transfer_id: str
    user_ref: str
    bank_id: str
    receiver_ifsc: str
    receiver_account: str
    amount_minor: int
    currency: str
    risk_id: str
    decision: Decision
    lookup_status: LookupStatus
    risk_score: int | None
    reason_codes: tuple[str, ...]
    contributing_banks: tuple[str, ...]
    model_version: str | None
    policy: PolicyConfig
    decided_at: datetime


class AuditSink(ABC):
    """Where decision records are written."""

    @abstractmethod
    def record(self, entry: AuditRecord) -> None: ...

    @abstractmethod
    def get(self, audit_ref: str, investigator: InvestigatorCredential) -> AuditRecord | None:
        """Retrieve one record by the opaque reference BankBot handed the caller.

        The evidence behind a decision is only reachable through this method, not
        through anything the customer-facing response carries (see
        ``BankBotDecision``) -- an analyst path calls this, not the transfer path.
        ``investigator`` names who is asking and why (see docs/SECURITY.md SS3.7);
        implementations must log every call here, whether or not a record is
        found, not just successful ones.
        """


class InMemoryAuditLog(AuditSink):
    """Bounded in-process audit log for the demo and tests.

    A real deployment writes to append-only durable storage; this is capped so a
    long-running process cannot be driven out of memory by traffic alone.

    Encrypts each record at rest (AES-256-GCM) and requires a named
    ``InvestigatorCredential`` -- logged to ``access_log`` -- for every single-
    record read via ``get()``. ``entries`` (bulk introspection by code already
    running inside this trusted process -- tests, the demo script) decrypts
    without that check, matching ``get()``'s own docstring: it, not ``entries``,
    is the analyst/investigator boundary. See docs/SECURITY.md SS3.7.
    """

    def __init__(
        self,
        capacity: int = DEFAULT_AUDIT_CAPACITY,
        *,
        encryption_key: AuditEncryptionKey | None = None,
        retention: timedelta | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        access_log_capacity: int = DEFAULT_AUDIT_CAPACITY,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        # Generated per-instance when not given: adequate for this in-process
        # demo sink, since nothing here is read back after the process exits.
        # A durable deployment passes one from AuditEncryptionKey.from_env()
        # instead, so the key outlives any single process.
        self._key = encryption_key or AuditEncryptionKey.generate()
        self._retention = retention
        self._now = now
        self._order: deque[str] = deque(maxlen=capacity)
        self._blobs: dict[str, EncryptedBlob] = {}
        # audit_ref -> decided_at, kept outside the encrypted blob so a
        # retention sweep never has to decrypt a record just to check its age.
        self._decided_at: dict[str, datetime] = {}
        self._access_log = AuditAccessLog(capacity=access_log_capacity)
        self._dropped = 0

    def _purge_expired(self) -> None:
        if self._retention is None:
            return
        cutoff = self._now() - self._retention
        expired = [ref for ref, decided_at in self._decided_at.items() if decided_at < cutoff]
        for ref in expired:
            self._blobs.pop(ref, None)
            self._decided_at.pop(ref, None)
            with contextlib.suppress(ValueError):
                self._order.remove(ref)

    def record(self, entry: AuditRecord) -> None:
        self._purge_expired()
        # Losing the record of a blocked fraud attempt to a flood of cheap
        # allowed probes is evidence destruction, so a drop is always counted and
        # always surfaced. A durable sink must not drop at all.
        evicted_ref: str | None = None
        if len(self._order) == self._order.maxlen:
            self._dropped += 1
            evicted_ref = self._order[0]  # deque.append() below silently pops this
            logger.warning(
                "audit log at capacity; discarded oldest record (%d dropped total)",
                self._dropped,
            )
        self._order.append(entry.transfer_id)
        if evicted_ref is not None:
            self._blobs.pop(evicted_ref, None)
            self._decided_at.pop(evicted_ref, None)
        self._blobs[entry.transfer_id] = encrypt_record(
            self._key, entry.transfer_id, entry.model_dump_json().encode()
        )
        self._decided_at[entry.transfer_id] = entry.decided_at

    def get(self, audit_ref: str, investigator: InvestigatorCredential) -> AuditRecord | None:
        self._purge_expired()
        blob = self._blobs.get(audit_ref)
        record = (
            AuditRecord.model_validate_json(decrypt_record(self._key, audit_ref, blob))
            if blob is not None
            else None
        )
        self._access_log.record(
            AuditAccessEvent(
                investigator_id=investigator.investigator_id,
                reason=investigator.reason,
                audit_ref=audit_ref,
                found=record is not None,
                accessed_at=self._now(),
            )
        )
        return record

    @property
    def dropped(self) -> int:
        """How many records this sink has discarded. Must be zero in production."""
        return self._dropped

    @property
    def entries(self) -> tuple[AuditRecord, ...]:
        self._purge_expired()
        return tuple(
            AuditRecord.model_validate_json(decrypt_record(self._key, ref, self._blobs[ref]))
            for ref in self._order
        )

    @property
    def access_log(self) -> tuple[AuditAccessEvent, ...]:
        """Every ``get()`` call this sink has served, found or not -- see
        docs/SECURITY.md SS3.7."""
        return self._access_log.entries

    def __len__(self) -> int:
        self._purge_expired()
        return len(self._order)


class BankBotDecision(BaseModel):
    """What BankBot returns to the calling channel.

    Carries the decision, the customer-facing copy, and a reference into the
    audit trail -- and deliberately nothing else. It previously carried the whole
    ``AuditRecord``, which meant any channel that serialised this object handed
    the caller the score, the flagging banks, and the exact policy thresholds.
    That is the same oracle ``_user_message`` is careful not to leak, reopened
    one field along. Evidence is retrieved from the audit sink through an
    authenticated analyst path, keyed by ``audit_ref``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    user_message: str
    audit_ref: str


def _user_message(decision: Decision) -> str:
    """Customer-facing copy.

    Deliberately free of the numeric score and the flagging bank. Echoing those
    back would turn the transfer form into an oracle: a fraudster could probe
    accounts through the bot to learn exactly which of their mule accounts the
    network has burned, and tune around the threshold.
    """
    if decision is Decision.BLOCK:
        return _BLOCK_MESSAGE
    if decision is Decision.STEP_UP:
        return _STEP_UP_MESSAGE
    return _ALLOW_MESSAGE


class BankBot:
    def __init__(
        self,
        bus: RiskBus,
        policy: PolicyConfig | None = None,
        audit: AuditSink | None = None,
        replay_cache_size: int = DEFAULT_REPLAY_CACHE,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        risk_ids: RiskIdDeriver | None = None,
    ) -> None:
        self.bus = bus
        self.policy = policy or PolicyConfig()
        self.audit = audit if audit is not None else InMemoryAuditLog()
        # Must match whatever scheme publishers sign under -- see aris.risk_id.
        self.risk_ids: RiskIdDeriver = risk_ids if risk_ids is not None else HmacRiskIdDeriver()
        # transfer_id -> the decision already taken for it.
        self._decided: OrderedDict[str, BankBotDecision] = OrderedDict()
        self._replay_cache_size = replay_cache_size
        self._lock = threading.Lock()
        self._now = now

    def _remember(self, transfer_id: str, decision: BankBotDecision) -> None:
        with self._lock:
            self._decided[transfer_id] = decision
            while len(self._decided) > self._replay_cache_size:
                self._decided.popitem(last=False)

    def _lookup_receiver(
        self, ifsc: str, account: str, transfer_id: str
    ) -> tuple[str, LookupResult | None]:
        """Look up ``(ifsc, account)`` under every candidate id the deriver
        offers (for HMAC/HSM: current epoch, then the previous one).

        Without the previous-epoch fallback, a signal published just before a
        UTC-day boundary and looked up just after it would derive a different
        risk_id purely from crossing that boundary and would silently read as
        NOT_FOUND, which resolves the same way an unflagged account does. See
        docs/SECURITY.md SS3.2. Returns the risk_id the caller should report
        (the preferred candidate, even when an older one is what matched, for
        audit continuity) and the lookup result, or ``None`` in place of the
        result to mean the lookup could not be completed.
        """
        # Validate the input before the fail-closed block below: a malformed
        # account number is the caller's error and must surface as one, not
        # be swallowed and quietly decided as if the bus were down.
        ifsc, account = normalize_ifsc(ifsc), normalize_account(account)
        try:
            candidates = self.risk_ids.candidates(ifsc, account, self._now())
        except Exception:
            # An OPRF authority that is rate limiting, unreachable, or
            # returning unverifiable answers means this bank cannot learn
            # whether the receiver is flagged -- the same position as a bus
            # outage, and handled the same fail-closed way, never as allow.
            logger.exception("risk_id derivation failed for transfer %s", transfer_id)
            return RISK_ID_UNAVAILABLE, None

        primary_result: LookupResult | None = None
        for i, candidate_risk_id in enumerate(candidates):
            try:
                result = self.bus.lookup(candidate_risk_id)
            except Exception:
                # An implementation is contracted to report UNAVAILABLE rather
                # than raise, but a transport fault must never fail open, so
                # an escaped exception is treated as an outage too.
                logger.exception(
                    "risk bus lookup failed for transfer %s risk_id=%s",
                    transfer_id,
                    candidate_risk_id,
                )
                return candidates[0], None
            if i == 0:
                primary_result = result
            if result.status is LookupStatus.FOUND:
                return candidate_risk_id, result
        return candidates[0], primary_result

    def pre_transaction(self, req: TransferRequest) -> BankBotDecision:
        """Decide a transfer before any money moves.

        Idempotent on ``transfer_id``: a retry returns the decision already
        taken. Deciding afresh would let a retry disagree with the original --
        bus state is volatile, so the same key could yield BLOCK then ALLOW --
        leaving two contradictory records under one key and nothing to say which
        one governed the money.
        """
        with self._lock:
            seen = self._decided.get(req.transfer_id)
        if seen is not None:
            logger.info("transfer=%s replayed; returning recorded decision", req.transfer_id)
            return seen

        risk_id, result = self._lookup_receiver(
            req.receiver_ifsc, req.receiver_account, req.transfer_id
        )

        if result is None:
            status: LookupStatus = LookupStatus.UNAVAILABLE
            signal = None
            contributing: tuple[str, ...] = ()
        else:
            status = result.status
            signal = result.signal
            contributing = result.contributing_banks

        try:
            decision = apply_policy(
                status,
                signal.risk_score if signal else None,
                req.amount_minor,
                self.policy,
                confidence=signal.confidence if signal else None,
                contributing_banks=len(contributing),
            )
        except Exception:
            # Malformed bus data must not escape into the transfer path, and it
            # must not skip the audit record either: the safe decision is taken
            # here and written below like any other.
            logger.exception("policy evaluation failed for transfer %s", req.transfer_id)
            decision = self.policy.on_bus_unavailable

        entry = AuditRecord(
            transfer_id=req.transfer_id,
            user_ref=req.user_ref,
            bank_id=req.bank_id,
            receiver_ifsc=req.receiver_ifsc,
            receiver_account=req.receiver_account,
            amount_minor=req.amount_minor,
            currency=req.currency,
            risk_id=risk_id,
            decision=decision,
            lookup_status=status,
            risk_score=signal.risk_score if signal else None,
            reason_codes=signal.reason_codes if signal else (),
            contributing_banks=contributing,
            model_version=signal.model_version if signal else None,
            policy=self.policy,
            decided_at=self._now(),
        )
        self.audit.record(entry)

        logger.info(
            "transfer=%s bank=%s decision=%s status=%s risk_id=%s",
            req.transfer_id,
            req.bank_id,
            decision.value,
            status.value if isinstance(status, LookupStatus) else status,
            risk_id,
        )
        outcome = BankBotDecision(
            decision=decision,
            user_message=_user_message(decision),
            audit_ref=req.transfer_id,
        )
        self._remember(req.transfer_id, outcome)
        return outcome
