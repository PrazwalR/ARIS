"""BankBot's pre-transaction check.

The bot sits in the transfer path: it derives the receiver's ``risk_id``, asks
the bus what the network knows, applies policy, and writes an audit record for
every decision -- including the ones that allow the transfer.
"""

from __future__ import annotations

import logging
import threading
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict, deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aris.bus import RiskBus
from aris.hashing import risk_id_for_account
from aris.schema import Decision, LookupStatus, PolicyConfig, apply_policy

logger = logging.getLogger(__name__)

MINOR_UNITS_PER_MAJOR: Final = 100
DEFAULT_AUDIT_CAPACITY: Final = 10_000
DEFAULT_REPLAY_CACHE: Final = 10_000

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


class InMemoryAuditLog(AuditSink):
    """Bounded in-process audit log for the demo and tests.

    A real deployment writes to append-only durable storage; this is capped so a
    long-running process cannot be driven out of memory by traffic alone.
    """

    def __init__(self, capacity: int = DEFAULT_AUDIT_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._entries: deque[AuditRecord] = deque(maxlen=capacity)
        self._dropped = 0

    def record(self, entry: AuditRecord) -> None:
        # Losing the record of a blocked fraud attempt to a flood of cheap
        # allowed probes is evidence destruction, so a drop is always counted and
        # always surfaced. A durable sink must not drop at all.
        if len(self._entries) == self._entries.maxlen:
            self._dropped += 1
            logger.warning(
                "audit log at capacity; discarded oldest record (%d dropped total)",
                self._dropped,
            )
        self._entries.append(entry)

    @property
    def dropped(self) -> int:
        """How many records this sink has discarded. Must be zero in production."""
        return self._dropped

    @property
    def entries(self) -> tuple[AuditRecord, ...]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


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
    ) -> None:
        self.bus = bus
        self.policy = policy or PolicyConfig()
        self.audit = audit if audit is not None else InMemoryAuditLog()
        # transfer_id -> the decision already taken for it.
        self._decided: OrderedDict[str, BankBotDecision] = OrderedDict()
        self._replay_cache_size = replay_cache_size
        self._lock = threading.Lock()

    def _remember(self, transfer_id: str, decision: BankBotDecision) -> None:
        with self._lock:
            self._decided[transfer_id] = decision
            while len(self._decided) > self._replay_cache_size:
                self._decided.popitem(last=False)

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

        risk_id = risk_id_for_account(req.receiver_account)

        try:
            result = self.bus.lookup(risk_id)
        except Exception:
            # An implementation is contracted to report UNAVAILABLE rather than
            # raise, but a transport fault must never fail open, so an escaped
            # exception is treated as an outage too.
            logger.exception("risk bus lookup failed for transfer %s", req.transfer_id)
            result = None

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
            decided_at=datetime.now(timezone.utc),
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
