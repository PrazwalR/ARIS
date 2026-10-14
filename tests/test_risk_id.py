"""aris.risk_id: one derivation scheme shared by publisher and checker, wired
through BankBot (docs/SECURITY.md SS3.1, SS3.2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aris.bankbot import RISK_ID_UNAVAILABLE, BankBot, InMemoryAuditLog, TransferRequest
from aris.bus import InMemoryRiskBus
from aris.hashing import current_epoch, risk_id_for_account
from aris.oprf import OprfAuthority, RateLimitExceeded
from aris.risk_id import HmacRiskIdDeriver, OprfRiskIdDeriver, publish_id
from aris.schema import Decision, LookupStatus, RiskSignal

IFSC = "HDFC0001234"
ACCOUNT = "ACC-999"
# Real wall-clock time: RiskSignal rejects timestamps in the future.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _request(**kw: object) -> TransferRequest:
    base: dict[str, object] = {
        "user_ref": "anu",
        "bank_id": "BANK-A",
        "receiver_ifsc": IFSC,
        "receiver_account": ACCOUNT,
        "amount_minor": 500_000,
    }
    base.update(kw)
    return TransferRequest(**base)  # type: ignore[arg-type]


def _flag(risk_id: str) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=95,
        confidence=0.99,
        reason_codes=("confirmed_fraud",),
        model_version="v0.4-fl",
        source_bank_id="BANK-B",
        timestamp=NOW,
    )


@pytest.fixture(scope="module")
def authority() -> OprfAuthority:
    return OprfAuthority(key_size=1024)


class TestHmacDeriver:
    def test_offers_current_then_previous_epoch(self):
        epoch = current_epoch(NOW)
        assert HmacRiskIdDeriver().candidates(IFSC, ACCOUNT, NOW) == (
            risk_id_for_account(IFSC, ACCOUNT, epoch=epoch),
            risk_id_for_account(IFSC, ACCOUNT, epoch=epoch - 1),
        )

    def test_publish_id_is_the_preferred_candidate(self):
        d = HmacRiskIdDeriver()
        assert publish_id(d, IFSC, ACCOUNT, NOW) == d.candidates(IFSC, ACCOUNT, NOW)[0]


class TestOprfThroughBankBot:
    def test_a_flag_published_under_oprf_blocks_the_transfer(self, keyring, bank_b, authority):
        bus = InMemoryRiskBus(keyring, now=lambda: NOW)
        publisher_side = OprfRiskIdDeriver(authority, "BANK-B")
        bus.publish(bank_b.sign(_flag(publish_id(publisher_side, IFSC, ACCOUNT, NOW))))

        audit = InMemoryAuditLog(now=lambda: NOW)
        bot = BankBot(
            bus, audit=audit, now=lambda: NOW, risk_ids=OprfRiskIdDeriver(authority, "BANK-A")
        )
        assert bot.pre_transaction(_request()).decision is Decision.BLOCK

    def test_two_banks_get_the_same_id_but_each_query_is_attributed(self, authority):
        before = len(authority.audit_log)
        a = OprfRiskIdDeriver(authority, "BANK-A").candidates(IFSC, ACCOUNT, NOW)
        b = OprfRiskIdDeriver(authority, "BANK-B").candidates(IFSC, ACCOUNT, NOW)
        assert a == b
        assert len(a) == 1  # OPRF ids do not rotate by epoch
        assert [r.bank_id for r in authority.audit_log[before:]] == ["BANK-A", "BANK-B"]

    def test_schemes_do_not_interoperate(self, keyring, bank_b, authority):
        """Why publisher and checker must share one deriver: an HMAC-published
        flag is invisible to an OPRF-configured bank."""
        bus = InMemoryRiskBus(keyring, now=lambda: NOW)
        bus.publish(bank_b.sign(_flag(publish_id(HmacRiskIdDeriver(), IFSC, ACCOUNT, NOW))))
        oprf_ids = OprfRiskIdDeriver(authority, "BANK-A").candidates(IFSC, ACCOUNT, NOW)
        result = bus.lookup(oprf_ids[0])
        assert result.status is LookupStatus.NOT_FOUND

    def test_a_rate_limited_bank_fails_closed_not_open(self, keyring, bank_b):
        """The authority refusing a query means this bank cannot learn whether
        the receiver is flagged. That must never resolve to allow."""
        stingy = OprfAuthority(key_size=1024, rate_limit=1)
        bus = InMemoryRiskBus(keyring, now=lambda: NOW)
        bus.publish(
            bank_b.sign(_flag(publish_id(OprfRiskIdDeriver(stingy, "BANK-A"), IFSC, ACCOUNT, NOW)))
        )  # spends BANK-A's single query
        audit = InMemoryAuditLog(now=lambda: NOW)
        bot = BankBot(
            bus, audit=audit, now=lambda: NOW, risk_ids=OprfRiskIdDeriver(stingy, "BANK-A")
        )
        with pytest.raises(RateLimitExceeded):
            stingy.evaluate("BANK-A", 2)
        decision = bot.pre_transaction(_request())
        assert decision.decision is Decision.STEP_UP
        entry = audit.entries[0]
        assert entry.lookup_status is LookupStatus.UNAVAILABLE
        assert entry.risk_id == RISK_ID_UNAVAILABLE

    def test_an_authority_returning_garbage_fails_closed(self, keyring):
        class Lying:
            def __init__(self, real: OprfAuthority) -> None:
                self._real = real

            @property
            def public_numbers(self) -> tuple[int, int]:
                return self._real.public_numbers

            def evaluate(self, bank_id: str, blinded: int) -> int:
                return (self._real.evaluate(bank_id, blinded) + 1) % self._real.public_numbers[0]

        bot = BankBot(
            InMemoryRiskBus(keyring),
            audit=(audit := InMemoryAuditLog()),
            risk_ids=OprfRiskIdDeriver(Lying(OprfAuthority(key_size=1024)), "BANK-A"),
        )
        assert bot.pre_transaction(_request()).decision is Decision.STEP_UP
        assert audit.entries[0].lookup_status is LookupStatus.UNAVAILABLE


class TestDefaultSchemeUnchanged:
    def test_bankbot_still_defaults_to_hmac_with_epoch_fallback(self, keyring, bank_b):
        """A signal from yesterday's epoch is still found by today's lookup."""
        yesterday = NOW - timedelta(days=1)
        bus = InMemoryRiskBus(keyring, now=lambda: NOW)
        old_id = risk_id_for_account(IFSC, ACCOUNT, epoch=current_epoch(yesterday))
        bus.publish(bank_b.sign(_flag(old_id)))
        bot = BankBot(bus, now=lambda: NOW)
        assert bot.pre_transaction(_request()).decision is Decision.BLOCK
