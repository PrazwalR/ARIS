from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from aris.schema import (
    MAX_TTL_HOURS,
    Decision,
    LookupStatus,
    PolicyConfig,
    RiskSignal,
    apply_policy,
)

RID = "a" * 64


def signal(**kw) -> RiskSignal:
    base = {
        "risk_id": RID,
        "risk_score": 92,
        "confidence": 0.9,
        "reason_codes": ("high_velocity",),
        "model_version": "v0.4-fl",
        "source_bank_id": "BANK-B",
    }
    base.update(kw)
    return RiskSignal(**base)


class TestRiskSignalValidation:
    def test_naive_timestamp_rejected_at_the_boundary(self):
        """Regression: a naive timestamp used to crash expiry with TypeError."""
        with pytest.raises(ValidationError, match="timezone-aware"):
            signal(timestamp=datetime(2020, 1, 1))

    def test_oversized_ttl_rejected(self):
        """Regression: an unbounded ttl overflowed date arithmetic on lookup."""
        with pytest.raises(ValidationError):
            signal(ttl_hours=10**10)

    @pytest.mark.parametrize("ttl", [-5, 0, MAX_TTL_HOURS + 1])
    def test_ttl_bounds(self, ttl: int):
        with pytest.raises(ValidationError):
            signal(ttl_hours=ttl)

    def test_future_timestamp_rejected(self):
        """Otherwise a publisher mints a signal that never expires."""
        future = datetime.now(timezone.utc) + timedelta(days=365)
        with pytest.raises(ValidationError, match="future"):
            signal(timestamp=future)

    def test_small_clock_skew_tolerated(self):
        assert signal(timestamp=datetime.now(timezone.utc) + timedelta(minutes=1))

    @pytest.mark.parametrize("bad", ["", "xyz", "A" * 64, "a" * 63, "g" * 64])
    def test_risk_id_must_be_hex_digest(self, bad: str):
        with pytest.raises(ValidationError):
            signal(risk_id=bad)

    @pytest.mark.parametrize("bad", ["Has Spaces", "drop;table", "x" * 50, "1leading"])
    def test_reason_codes_are_log_safe_tokens(self, bad: str):
        with pytest.raises(ValidationError):
            signal(reason_codes=(bad,))

    def test_reason_codes_bounded_and_unique(self):
        with pytest.raises(ValidationError):
            signal(reason_codes=tuple(f"code_{i}" for i in range(50)))
        with pytest.raises(ValidationError):
            signal(reason_codes=("high_velocity", "high_velocity"))
        with pytest.raises(ValidationError):
            signal(reason_codes=())

    @pytest.mark.parametrize("bad", ["bank b", "lowercase", "", "B" * 40])
    def test_source_bank_id_format(self, bad: str):
        with pytest.raises(ValidationError):
            signal(source_bank_id=bad)

    def test_unknown_fields_rejected(self):
        with pytest.raises(ValidationError):
            signal(injected_field="surprise")

    def test_signal_is_immutable(self):
        with pytest.raises(ValidationError):
            signal().risk_score = 0

    def test_score_bounds(self):
        with pytest.raises(ValidationError):
            signal(risk_score=101)
        with pytest.raises(ValidationError):
            signal(risk_score=-1)

    def test_expiry(self):
        now = datetime.now(timezone.utc)
        s = signal(timestamp=now - timedelta(hours=2), ttl_hours=1)
        assert s.is_expired(now)
        assert not signal(timestamp=now, ttl_hours=1).is_expired(now)


class TestPolicyConfig:
    def test_inverted_thresholds_rejected(self):
        """Regression: medium above high silently produced nonsense decisions."""
        with pytest.raises(ValidationError, match="step_up_at"):
            PolicyConfig(step_up_at=90, block_at=50)

    def test_cannot_configure_fail_open_bus_behaviour(self):
        with pytest.raises(ValidationError, match="must not be ALLOW"):
            PolicyConfig(on_bus_unavailable=Decision.ALLOW)


class TestApplyPolicy:
    cfg = PolicyConfig(step_up_at=50, block_at=85)

    def call(self, status, score, amount=100_000, **kw):
        return apply_policy(status, score, amount, self.cfg, **kw)

    def test_score_on_the_block_threshold_blocks(self):
        """Regression: block used > while step-up used >=, so 85 slipped through."""
        assert self.call(LookupStatus.FOUND, 85) is Decision.BLOCK

    def test_thresholds_are_inclusive_on_both_bounds(self):
        assert self.call(LookupStatus.FOUND, 50) is Decision.STEP_UP
        assert self.call(LookupStatus.FOUND, 49) is Decision.ALLOW
        assert self.call(LookupStatus.FOUND, 84) is Decision.STEP_UP
        assert self.call(LookupStatus.FOUND, 86) is Decision.BLOCK

    def test_unavailable_bus_does_not_fail_open(self):
        """Regression: an outage was indistinguishable from a clean account."""
        assert self.call(LookupStatus.UNAVAILABLE, None) is not Decision.ALLOW

    def test_clean_account_is_allowed(self):
        assert self.call(LookupStatus.NOT_FOUND, None) is Decision.ALLOW

    def test_large_amount_still_steps_up_on_an_unflagged_account(self):
        """Regression: amount was collected and never used in any decision."""
        assert self.call(LookupStatus.NOT_FOUND, None, amount=10**9) is Decision.STEP_UP

    def test_found_without_score_is_a_programming_error(self):
        with pytest.raises(ValueError):
            self.call(LookupStatus.FOUND, None)

    @pytest.mark.parametrize(
        "status,score,expected",
        [
            ("unavailable", None, Decision.STEP_UP),
            ("found", 99, Decision.BLOCK),
            ("found", 10, Decision.ALLOW),
            ("not_found", None, Decision.ALLOW),
        ],
    )
    def test_a_plain_string_status_is_honoured_not_ignored(self, status, score, expected):
        """Regression: LookupStatus is a str enum, so "unavailable" compares equal
        but not identical. Both guards used `is`, so a string status matched
        nothing and fell out of the bottom of the function -- which returned
        ALLOW. A score of 99 resolved to allow. This is what a JSON or Kafka
        backed bus deserializes to."""
        assert self.call(status, score) is expected

    def test_an_unrecognised_status_raises_rather_than_allowing(self):
        with pytest.raises(ValueError, match="unrecognised"):
            self.call("totally-bogus", None)

    def test_allow_is_never_the_fall_through(self):
        """Every ALLOW must come from a branch that decided on it."""
        import inspect

        from aris import schema

        body = inspect.getsource(schema.apply_policy)
        assert body.rstrip().endswith('raise ValueError(f"unhandled lookup status: {status!r}")')


class TestBlockRequiresJustification:
    """A block freezes an innocent customer's payments for up to the full TTL,
    with no retraction path, on one member's say-so."""

    def test_a_low_confidence_signal_cannot_block(self):
        assert (
            apply_policy(LookupStatus.FOUND, 100, 1000, PolicyConfig(), confidence=0.01)
            is Decision.STEP_UP
        )

    def test_a_confident_signal_still_blocks(self):
        assert (
            apply_policy(LookupStatus.FOUND, 92, 1000, PolicyConfig(), confidence=0.94)
            is Decision.BLOCK
        )

    def test_a_single_bank_blocks_by_default(self):
        """Protecting against fraud first seen elsewhere is the point of ARIS,
        so corroboration is opt-in rather than required."""
        assert (
            apply_policy(
                LookupStatus.FOUND, 92, 1000, PolicyConfig(), confidence=0.9, contributing_banks=1
            )
            is Decision.BLOCK
        )

    def test_corroboration_can_be_required(self):
        cfg = PolicyConfig(min_banks_to_block=2)
        assert (
            apply_policy(LookupStatus.FOUND, 99, 1000, cfg, confidence=0.9, contributing_banks=1)
            is Decision.STEP_UP
        )
        assert (
            apply_policy(LookupStatus.FOUND, 99, 1000, cfg, confidence=0.9, contributing_banks=2)
            is Decision.BLOCK
        )
