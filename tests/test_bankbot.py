from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aris.attestation import Publisher, PublisherKeyring, SignedRiskSignal
from aris.bankbot import (
    _ALLOW_MESSAGE,
    _BLOCK_MESSAGE,
    _STEP_UP_MESSAGE,
    BankBot,
    InMemoryAuditLog,
    TransferRequest,
)
from aris.bus import InMemoryRiskBus, LookupResult, PublishOutcome, RiskBus
from aris.hashing import risk_id_for_account
from aris.schema import Decision, LookupStatus, PolicyConfig, RiskSignal

ACCOUNT = "ACC-999"
IFSC = "HDFC0001234"


@pytest.fixture
def empty_bus(keyring: PublisherKeyring) -> InMemoryRiskBus:
    return InMemoryRiskBus(keyring)


@pytest.fixture
def flagged_bus(keyring: PublisherKeyring, bank_b: Publisher):
    """A bus where BANK-B has flagged ACC-999, at a caller-chosen score."""

    def build(score: int = 92) -> InMemoryRiskBus:
        bus = InMemoryRiskBus(keyring)
        bus.publish(
            bank_b.sign(
                RiskSignal(
                    risk_id=risk_id_for_account(IFSC, ACCOUNT),
                    risk_score=score,
                    confidence=0.94,
                    reason_codes=("high_velocity", "new_beneficiary"),
                    model_version="v0.4-fl",
                    source_bank_id="BANK-B",
                )
            )
        )
        return bus

    return build


def request(**kw) -> TransferRequest:
    base = {
        "user_ref": "anu",
        "bank_id": "BANK-A",
        "receiver_ifsc": IFSC,
        "receiver_account": ACCOUNT,
        "amount_minor": 500_000,
    }
    base.update(kw)
    return TransferRequest(**base)


class TestAnuStory:
    def test_anu_is_blocked_by_a_flag_raised_at_another_bank(self, flagged_bus):
        audit = InMemoryAuditLog()
        out = BankBot(flagged_bus(), audit=audit).pre_transaction(request())
        assert out.decision is Decision.BLOCK
        assert audit.entries[0].risk_score == 92
        assert audit.entries[0].contributing_banks == ("BANK-B",)

    def test_the_risk_id_is_a_real_keyed_function_of_the_account(self, flagged_bus):
        """The old assertion was `ACCOUNT not in risk_id`, which is tautologically
        true: "ACC-999" contains A, C and -, none of which occur in lowercase
        hex. It passed even when risk_id_for_account was mutated to return a
        constant. These assert the properties that actually matter."""
        audit = InMemoryAuditLog()
        BankBot(flagged_bus(), audit=audit).pre_transaction(request())
        rid = audit.entries[0].risk_id
        assert len(rid) == 64 and ACCOUNT not in rid

        other = InMemoryAuditLog()
        BankBot(InMemoryRiskBus(PublisherKeyring()), audit=other).pre_transaction(
            request(receiver_account="ACC-998")
        )
        assert other.entries[0].risk_id != rid, "different accounts collide"

    def test_same_account_at_a_different_bank_does_not_collide(self, flagged_bus):
        """SS3.3: an account number is unique only within its own bank, so
        risk_id must be keyed on (ifsc, account), not the account alone."""
        audit = InMemoryAuditLog()
        BankBot(flagged_bus(), audit=audit).pre_transaction(request())
        flagged_rid = audit.entries[0].risk_id

        other = InMemoryAuditLog()
        BankBot(InMemoryRiskBus(PublisherKeyring()), audit=other).pre_transaction(
            request(receiver_ifsc="ICIC0009876")
        )
        assert other.entries[0].risk_id != flagged_rid

    def test_unflagged_receiver_is_allowed(self, empty_bus):
        out = BankBot(empty_bus).pre_transaction(request(receiver_account="ACC-111"))
        assert out.decision is Decision.ALLOW


class TestNoInformationLeakToCustomer:
    """Substring-absence assertions were not enough: they passed against a
    message mutated to append reason codes, model version, contributing-bank
    count and threshold band. The copy is now pinned exactly."""

    @pytest.mark.parametrize(
        "score,expected,text",
        [(92, Decision.BLOCK, _BLOCK_MESSAGE), (60, Decision.STEP_UP, _STEP_UP_MESSAGE)],
    )
    def test_user_message_is_exactly_the_fixed_copy(self, flagged_bus, score, expected, text):
        out = BankBot(flagged_bus(score)).pre_transaction(request())
        assert out.decision is expected
        assert out.user_message == text

    def test_allow_message_is_exactly_the_fixed_copy(self, empty_bus):
        out = BankBot(empty_bus).pre_transaction(request(receiver_account="ACC-111"))
        assert out.user_message == _ALLOW_MESSAGE

    def test_the_returned_object_carries_no_evidence(self, flagged_bus):
        """Regression: BankBotDecision carried the whole AuditRecord, so any
        channel that serialised it handed the caller the score, the flagging
        banks and the exact policy thresholds -- the same oracle the message
        copy is careful not to leak, reopened one field along."""
        out = BankBot(flagged_bus()).pre_transaction(request(transfer_id="txn-0042"))
        exposed = out.model_dump()
        assert set(exposed) == {"decision", "user_message", "audit_ref"}
        assert exposed["audit_ref"] == "txn-0042"

        # audit_ref is an opaque handle by design; everything else must be free
        # of evidence a fraudster could probe for.
        blob = str({k: v for k, v in exposed.items() if k != "audit_ref"})
        for leak in ("92", "BANK-B", "high_velocity", "v0.4-fl", "risk_id", "block_at"):
            assert leak not in blob, f"{leak!r} leaked to the calling channel"


class TestBusFailure:
    class BrokenBus(RiskBus):
        def publish(self, signed: SignedRiskSignal) -> PublishOutcome:
            raise ConnectionError("bus down")

        def lookup(self, risk_id: str) -> LookupResult:
            raise ConnectionError("bus down")

    def test_a_bus_outage_does_not_fail_open(self):
        """Taking the bus offline must not become a way to wave transfers through."""
        audit = InMemoryAuditLog()
        out = BankBot(self.BrokenBus(), audit=audit).pre_transaction(request())
        assert out.decision is not Decision.ALLOW
        assert audit.entries[0].lookup_status is LookupStatus.UNAVAILABLE

    def test_outage_is_recorded_for_audit(self):
        audit = InMemoryAuditLog()
        BankBot(self.BrokenBus(), audit=audit).pre_transaction(request())
        assert audit.entries[0].lookup_status is LookupStatus.UNAVAILABLE


class TestTransferValidation:
    @pytest.mark.parametrize("amount", [0, -5000, 10**16])
    def test_invalid_amounts_rejected(self, amount):
        with pytest.raises(ValidationError):
            request(amount_minor=amount)

    def test_float_amounts_cannot_smuggle_nan(self):
        """Regression: amount was a float and accepted NaN and infinity."""
        for bad in (float("nan"), float("inf")):
            with pytest.raises(ValidationError):
                request(amount_minor=bad)

    def test_from_major_units_converts_rupees(self):
        req = TransferRequest.from_major_units(
            Decimal("5000.00"),
            user_ref="anu",
            bank_id="BANK-A",
            receiver_ifsc=IFSC,
            receiver_account=ACCOUNT,
        )
        assert req.amount_minor == 500_000
        assert req.amount_major == Decimal("5000.00")

    def test_sub_paise_precision_rejected(self):
        with pytest.raises(ValueError):
            TransferRequest.from_major_units(
                Decimal("100.005"),
                user_ref="a",
                bank_id="BANK-A",
                receiver_ifsc=IFSC,
                receiver_account=ACCOUNT,
            )

    @pytest.mark.parametrize(
        "codepoint,name",
        [
            (0x0A, "LINE FEED"),
            (0x0D, "CARRIAGE RETURN"),
            (0x7F, "DELETE"),
            (0x85, "NEXT LINE"),
            (0x2028, "LINE SEPARATOR"),
            (0x2029, "PARAGRAPH SEPARATOR"),
            (0x202E, "RIGHT-TO-LEFT OVERRIDE"),
        ],
    )
    def test_line_breaking_characters_cannot_forge_audit_lines(self, codepoint, name):
        """Regression: the check was a denylist on ASCII control characters, so
        U+0085, U+2028 and U+2029 -- which str.splitlines() and most log viewers
        break on -- passed straight through, and U+202E reversed displayed text.
        Only LINE FEED was ever tested."""
        with pytest.raises(ValidationError):
            request(user_ref=f"anu{chr(codepoint)}DECISION=allow")

    def test_blank_identifiers_rejected(self):
        with pytest.raises(ValidationError):
            request(user_ref="   ")

    def test_unknown_fields_rejected(self):
        with pytest.raises(ValidationError):
            request(override_decision="allow")

    def test_malformed_receiver_account_is_surfaced(self, empty_bus):
        bot = BankBot(empty_bus)
        with pytest.raises(ValueError):
            bot.pre_transaction(request(receiver_account="!!"))

    def test_malformed_receiver_ifsc_is_rejected_at_construction(self):
        """Unlike receiver_account (rejected inside pre_transaction, at lookup
        time), receiver_ifsc has a dedicated field_validator, so it fails
        before a TransferRequest even exists."""
        with pytest.raises(ValidationError):
            request(receiver_ifsc="not-an-ifsc")


class TestAuditTrail:
    def test_every_decision_is_recorded_including_allows(self, empty_bus):
        audit = InMemoryAuditLog()
        bot = BankBot(empty_bus, audit=audit)
        bot.pre_transaction(request(receiver_account="ACC-111"))
        bot.pre_transaction(request(receiver_account="ACC-222"))
        assert len(audit) == 2
        assert all(e.decision is Decision.ALLOW for e in audit.entries)

    def test_record_can_be_reconciled_against_the_ledger(self, flagged_bus):
        """Regression: the old record held only a decision and score."""
        audit = InMemoryAuditLog()
        req = request(transfer_id="txn-0042")
        BankBot(flagged_bus(), audit=audit).pre_transaction(req)
        entry = audit.entries[0]
        assert entry.transfer_id == "txn-0042"
        assert entry.amount_minor == req.amount_minor
        assert entry.currency == "INR"
        assert entry.bank_id == "BANK-A"
        assert entry.user_ref == "anu"
        assert entry.model_version == "v0.4-fl"
        assert entry.reason_codes == ("high_velocity", "new_beneficiary")
        assert entry.decided_at.tzinfo is not None

    def test_get_retrieves_by_audit_ref(self, empty_bus):
        audit = InMemoryAuditLog()
        bot = BankBot(empty_bus, audit=audit)
        decision = bot.pre_transaction(request(transfer_id="txn-lookup-1"))
        entry = audit.get(decision.audit_ref)
        assert entry is not None
        assert entry.transfer_id == "txn-lookup-1"

    def test_get_returns_none_for_unknown_ref(self, empty_bus):
        audit = InMemoryAuditLog()
        BankBot(empty_bus, audit=audit).pre_transaction(request())
        assert audit.get("never-issued") is None

    def test_get_forgets_evicted_records(self, empty_bus):
        audit = InMemoryAuditLog(capacity=2)
        bot = BankBot(empty_bus, audit=audit)
        bot.pre_transaction(request(transfer_id="txn-aaaaaaaa"))
        bot.pre_transaction(request(transfer_id="txn-bbbbbbbb"))
        bot.pre_transaction(request(transfer_id="txn-cccccccc"))  # evicts txn-a
        assert audit.get("txn-aaaaaaaa") is None
        assert audit.get("txn-bbbbbbbb") is not None
        assert audit.get("txn-cccccccc") is not None

    def test_record_captures_the_policy_that_produced_it(self, flagged_bus):
        """A past decision must be explainable under the thresholds then in force."""
        audit = InMemoryAuditLog()
        policy = PolicyConfig(step_up_at=10, block_at=20)
        BankBot(flagged_bus(), policy=policy, audit=audit).pre_transaction(request())
        assert audit.entries[0].policy.block_at == 20

    def test_audit_log_is_bounded_and_reports_what_it_dropped(self, empty_bus):
        """Bounding alone is evidence destruction by flooding: the record of a
        blocked fraud attempt can be pushed out by cheap allowed probes. A drop
        must never be silent."""
        audit = InMemoryAuditLog(capacity=5)
        bot = BankBot(empty_bus, audit=audit)
        for i in range(50):
            bot.pre_transaction(request(transfer_id=f"txn-{i:04d}"))
        assert len(audit) == 5
        assert audit.entries[-1].transfer_id == "txn-0049"
        assert audit.dropped == 45

    def test_a_healthy_log_reports_no_drops(self, empty_bus):
        audit = InMemoryAuditLog(capacity=100)
        bot = BankBot(empty_bus, audit=audit)
        for i in range(10):
            bot.pre_transaction(request(transfer_id=f"txn-{i:04d}"))
        assert audit.dropped == 0


class TestIdempotency:
    def test_a_retry_returns_the_decision_already_taken(self, keyring, bank_b):
        """Regression: transfer_id was documented as an idempotency key and never
        consulted. Bus state is volatile -- expiry, eviction, quotas -- so the
        same key could yield BLOCK then ALLOW, leaving two contradictory audit
        records under one key and nothing to say which governed the money."""
        bus = InMemoryRiskBus(keyring)
        bus.publish(
            bank_b.sign(
                RiskSignal(
                    risk_id=risk_id_for_account(IFSC, ACCOUNT),
                    risk_score=92,
                    confidence=0.94,
                    reason_codes=("high_velocity",),
                    model_version="v0.4-fl",
                    source_bank_id="BANK-B",
                )
            )
        )
        audit = InMemoryAuditLog()
        bot = BankBot(bus, audit=audit)
        req = request(transfer_id="txn-0042")
        first = bot.pre_transaction(req)
        assert first.decision is Decision.BLOCK

        # The flag expires out from under the retry.
        bus._store.clear()
        second = bot.pre_transaction(req)

        assert second.decision is first.decision
        assert len(audit) == 1, "a retry wrote a second, contradicting audit record"

    def test_distinct_transfers_are_decided_independently(self, empty_bus):
        audit = InMemoryAuditLog()
        bot = BankBot(empty_bus, audit=audit)
        bot.pre_transaction(request(transfer_id="txn-0001"))
        bot.pre_transaction(request(transfer_id="txn-0002"))
        assert len(audit) == 2

    def test_the_replay_cache_is_bounded(self, empty_bus):
        bot = BankBot(empty_bus, replay_cache_size=10)
        for i in range(100):
            bot.pre_transaction(request(transfer_id=f"txn-{i:04d}"))
        assert len(bot._decided) == 10
