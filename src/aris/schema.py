"""Shared contract between every ARIS component.

This module is the one file all three owners depend on (see docs/TEAM.md), so it
carries the validation that the rest of the system is allowed to assume: a
``RiskSignal`` that constructs successfully is safe to put on the bus, safe to
store, and safe to evaluate without further defensive checks.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# A risk_id is the hex digest of a 256-bit MAC (see aris.hashing).
RISK_ID_PATTERN: Final = re.compile(r"\A[0-9a-f]{64}\Z")

# Reason codes and bank/model identifiers travel between institutions and land in
# audit logs, so they are restricted to a printable, log-safe charset rather than
# accepted as free text.
_TOKEN_PATTERN: Final = re.compile(r"\A[a-z][a-z0-9_]{1,39}\Z")
_BANK_ID_PATTERN: Final = re.compile(r"\A[A-Z][A-Z0-9-]{1,31}\Z")
_MODEL_VERSION_PATTERN: Final = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._+-]{0,31}\Z")

MAX_REASON_CODES: Final = 8
MIN_TTL_HOURS: Final = 1
MAX_TTL_HOURS: Final = 168  # 7 days; also keeps expiry arithmetic in range.

# Tolerance for clock skew between banks. A signal stamped further ahead than
# this is rejected, otherwise a publisher could mint an effectively immortal one.
MAX_CLOCK_SKEW: Final = timedelta(minutes=5)

# Canonical vocabulary. Publishers may use other codes that match _TOKEN_PATTERN;
# these are the ones BankBot and the analyst tooling understand.
NEW_BENEFICIARY: Final = "new_beneficiary"
HIGH_VELOCITY: Final = "high_velocity"
SUSPICIOUS_PATTERN: Final = "suspicious_pattern"
MULE_ACCOUNT: Final = "mule_account"
CONFIRMED_FRAUD: Final = "confirmed_fraud"


class RiskSignal(BaseModel):
    """One bank's assessment of one receiver account, as published to the bus.

    Immutable and closed to unknown fields: a signal is evidence in an audit
    trail, so it must not be mutated after publication, and a peer bank must not
    be able to smuggle extra attributes through the shared topic.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    risk_id: str
    risk_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: tuple[str, ...] = Field(min_length=1, max_length=MAX_REASON_CODES)
    model_version: str
    source_bank_id: str
    ttl_hours: int = Field(default=24, ge=MIN_TTL_HOURS, le=MAX_TTL_HOURS)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("risk_id")
    @classmethod
    def _check_risk_id(cls, value: str) -> str:
        if not RISK_ID_PATTERN.match(value):
            raise ValueError("risk_id must be 64 lowercase hex characters")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _check_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for code in value:
            if not _TOKEN_PATTERN.match(code):
                raise ValueError(f"reason code {code!r} is not a lowercase snake_case token")
        if len(set(value)) != len(value):
            raise ValueError("reason_codes must not contain duplicates")
        return value

    @field_validator("source_bank_id")
    @classmethod
    def _check_bank_id(cls, value: str) -> str:
        if not _BANK_ID_PATTERN.match(value):
            raise ValueError("source_bank_id must look like 'BANK-A'")
        return value

    @field_validator("model_version")
    @classmethod
    def _check_model_version(cls, value: str) -> str:
        if not _MODEL_VERSION_PATTERN.match(value):
            raise ValueError("model_version contains characters that are not safe to log")
        return value

    @field_validator("timestamp")
    @classmethod
    def _check_timestamp(cls, value: datetime) -> datetime:
        # A naive datetime cannot be compared against an aware "now", which would
        # raise deep inside expiry checking instead of here at the boundary.
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (UTC)")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _reject_future_timestamp(self) -> RiskSignal:
        if self.timestamp > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("timestamp is too far in the future")
        return self

    @property
    def expires_at(self) -> datetime:
        return self.timestamp + timedelta(hours=self.ttl_hours)

    def is_expired(self, now: datetime) -> bool:
        return now >= self.expires_at


class Decision(str, Enum):
    """What BankBot does with a transfer."""

    ALLOW = "allow"
    STEP_UP = "step_up"
    BLOCK = "block"


class LookupStatus(str, Enum):
    """Why the bus returned what it returned.

    ``NOT_FOUND`` means the bus answered and holds no live signal for the
    account. ``UNAVAILABLE`` means the bus did not answer at all. Collapsing the
    two would make an outage indistinguishable from a clean account, which is
    what turns a bus failure into an open door.
    """

    FOUND = "found"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"


class PolicyConfig(BaseModel):
    """Score thresholds, inclusive on both bounds.

    ``score >= block_at`` blocks; ``score >= step_up_at`` asks for step-up auth.
    Both comparisons are inclusive so a score sitting exactly on a threshold
    resolves to the safer side rather than slipping through.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_up_at: int = Field(default=50, ge=0, le=100)
    block_at: int = Field(default=85, ge=0, le=100)

    # A transfer this large is worth a second factor even against a quiet bus,
    # so a fraudster cannot simply pick an account nobody has flagged yet.
    step_up_above_amount_minor: int = Field(default=10_000_000, ge=0)  # ₹100,000.00

    # Blocking is the irreversible action, and any one member can trigger it for
    # up to a week. A signal the publisher itself is unsure of is capped at
    # step-up rather than allowed to freeze an innocent customer's payments.
    min_confidence_to_block: float = Field(default=0.5, ge=0.0, le=1.0)

    # How many independent banks must agree before a block. Default 1, because
    # protecting a customer from fraud *first seen elsewhere* is the point of the
    # network; raise it where false positives cost more than missed fraud.
    min_banks_to_block: int = Field(default=1, ge=1)

    # How to resolve a transfer when the bus could not be reached. Never ALLOW:
    # that is the fail-open path an attacker gets by taking the bus offline.
    on_bus_unavailable: Decision = Decision.STEP_UP

    @model_validator(mode="after")
    def _check_ordering(self) -> PolicyConfig:
        if self.step_up_at > self.block_at:
            raise ValueError("step_up_at must not exceed block_at")
        if self.on_bus_unavailable is Decision.ALLOW:
            raise ValueError("on_bus_unavailable must not be ALLOW; the bus is a safety control")
        return self


def _amount_only(amount_minor: int, cfg: PolicyConfig) -> Decision:
    """The decision for an account the network has nothing live on."""
    if amount_minor > cfg.step_up_above_amount_minor:
        return Decision.STEP_UP
    return Decision.ALLOW


def apply_policy(
    status: LookupStatus | str,
    score: int | None,
    amount_minor: int,
    cfg: PolicyConfig | None = None,
    *,
    confidence: float | None = None,
    contributing_banks: int = 1,
) -> Decision:
    """Resolve a transfer to a decision.

    ``amount_minor`` is the transfer value in minor units (paise), so a large
    transfer is never waved through purely because the receiver happens to be
    unknown to the network.

    ``status`` is coerced rather than compared by identity. ``LookupStatus`` is a
    ``str`` enum, so a plain ``"unavailable"`` -- exactly what a JSON or Kafka
    backed bus deserializes to -- compares equal but *not identical* to the
    member. Matching on ``is`` alone would let such a value miss every branch and
    fall out of the bottom of this function, and the bottom used to be ALLOW: a
    score of 99 resolved to "allow". Unknown values now raise, and ALLOW is only
    ever returned from a branch that explicitly decided on it.
    """
    cfg = cfg or PolicyConfig()
    try:
        status = LookupStatus(status)
    except ValueError as exc:
        raise ValueError(f"unrecognised lookup status: {status!r}") from exc

    if status is LookupStatus.UNAVAILABLE:
        return cfg.on_bus_unavailable

    if status is LookupStatus.NOT_FOUND:
        return _amount_only(amount_minor, cfg)

    if status is LookupStatus.FOUND:
        if score is None:
            raise ValueError("LookupStatus.FOUND requires a score")
        if score >= cfg.block_at:
            unsure = confidence is not None and confidence < cfg.min_confidence_to_block
            uncorroborated = contributing_banks < cfg.min_banks_to_block
            # Cap at step-up rather than block: the customer is still protected,
            # but one member's low-confidence assertion cannot freeze an account
            # network-wide for the full TTL with no retraction path.
            return Decision.STEP_UP if (unsure or uncorroborated) else Decision.BLOCK
        if score >= cfg.step_up_at:
            return Decision.STEP_UP
        return _amount_only(amount_minor, cfg)

    raise ValueError(f"unhandled lookup status: {status!r}")
