"""M4: the same Anu / ACC-999 story as `aris.demo.anu_transfer`, but driven
entirely over HTTP against the FastAPI app -- request bodies, response JSON,
header-based auth, and status codes, not direct Python calls into BankBot.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.api.config import ApiSettings
from aris.attestation import Publisher, PublisherKeyring
from aris.bankbot import InMemoryAuditLog
from aris.bus import InMemoryRiskBus
from aris.hashing import risk_id_for_account
from aris.schema import RiskSignal

ACCOUNT = "ACC-999"
IFSC = "HDFC0001234"


@pytest.fixture
def keyring() -> PublisherKeyring:
    return PublisherKeyring()


@pytest.fixture
def bank_b(keyring: PublisherKeyring) -> Publisher:
    publisher = Publisher.generate("BANK-B")
    keyring.register(publisher.bank_id, publisher.public_key)
    return publisher


def _signal(risk_id: str, score: int = 92) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=0.94,
        reason_codes=("new_beneficiary", "high_velocity"),
        model_version="v0.4-fl",
        source_bank_id="BANK-B",
        ttl_hours=24,
        timestamp=datetime.now(timezone.utc),
    )


class TestTransfersEndpoint:
    def test_flagged_account_is_blocked_over_http(self, keyring, bank_b):
        bus = InMemoryRiskBus(keyring)
        risk_id = risk_id_for_account(IFSC, ACCOUNT)
        bus.publish(bank_b.sign(_signal(risk_id, score=92)))

        app = create_app(bus, audit=InMemoryAuditLog())
        client = TestClient(app)

        resp = client.post(
            "/transfers",
            json={
                "user_ref": "anu",
                "bank_id": "BANK-A",
                "receiver_ifsc": IFSC,
                "receiver_account": ACCOUNT,
                "amount_minor": 500000,
                "currency": "INR",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["decision"] == "block"
        assert "score" not in body["user_message"].lower()
        assert body["step_up_required"] is False
        # No score oracle over HTTP either -- checked on the fields that could
        # actually carry it, not the whole body: audit_ref is a random UUID
        # and can coincidentally contain "92" as a substring.
        assert "92" not in body["user_message"]
        assert set(body) == {"decision", "user_message", "audit_ref", "step_up_required"}

    def test_unflagged_account_is_allowed(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(bus, audit=InMemoryAuditLog())
        client = TestClient(app)

        resp = client.post(
            "/transfers",
            json={
                "user_ref": "anu",
                "bank_id": "BANK-A",
                "receiver_ifsc": IFSC,
                "receiver_account": "ACC-CLEAN",
                "amount_minor": 500000,
            },
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "allow"

    def test_invalid_request_body_is_rejected(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(bus, audit=InMemoryAuditLog())
        client = TestClient(app)

        resp = client.post(
            "/transfers",
            json={
                "user_ref": "anu",
                "bank_id": "BANK-A",
                "receiver_ifsc": IFSC,
                "receiver_account": "ACC-CLEAN",
                "amount_minor": -100,  # must be > 0
            },
        )
        assert resp.status_code == 422

    def test_replayed_transfer_id_is_idempotent_over_http(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(bus, audit=InMemoryAuditLog())
        client = TestClient(app)
        body = {
            "user_ref": "anu",
            "bank_id": "BANK-A",
            "receiver_ifsc": IFSC,
            "receiver_account": "ACC-CLEAN",
            "amount_minor": 500000,
            "transfer_id": "retry-http-001",
        }
        first = client.post("/transfers", json=body).json()
        second = client.post("/transfers", json=body).json()
        assert first["audit_ref"] == second["audit_ref"] == "retry-http-001"


class TestHealthEndpoint:
    def test_health_ok(self, keyring):
        app = create_app(InMemoryRiskBus(keyring))
        assert TestClient(app).get("/health").status_code == 200


INVESTIGATOR_HEADERS = {
    "X-Investigator-Id": "alice",
    "X-Investigator-Key": "secret",
    "X-Investigator-Reason": "fraud case #42",
}


class TestAuditEndpoint:
    def test_no_investigators_configured_disables_lookup(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(bus, settings=ApiSettings(investigators={}), audit=InMemoryAuditLog())
        client = TestClient(app)
        resp = client.get("/audit/whatever", headers=INVESTIGATOR_HEADERS)
        assert resp.status_code == 503

    def test_missing_credentials_are_rejected(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(
            bus, settings=ApiSettings(investigators={"alice": "secret"}), audit=InMemoryAuditLog()
        )
        resp = TestClient(app).get("/audit/whatever")
        assert resp.status_code == 401

    def test_unknown_investigator_is_rejected(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(
            bus, settings=ApiSettings(investigators={"alice": "secret"}), audit=InMemoryAuditLog()
        )
        resp = TestClient(app).get(
            "/audit/whatever",
            headers={**INVESTIGATOR_HEADERS, "X-Investigator-Id": "mallory"},
        )
        assert resp.status_code == 401

    def test_wrong_key_for_a_known_investigator_is_rejected(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(
            bus, settings=ApiSettings(investigators={"alice": "secret"}), audit=InMemoryAuditLog()
        )
        resp = TestClient(app).get(
            "/audit/whatever",
            headers={**INVESTIGATOR_HEADERS, "X-Investigator-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_a_blank_reason_is_rejected(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(
            bus, settings=ApiSettings(investigators={"alice": "secret"}), audit=InMemoryAuditLog()
        )
        resp = TestClient(app).get(
            "/audit/whatever",
            headers={**INVESTIGATOR_HEADERS, "X-Investigator-Reason": ""},
        )
        assert resp.status_code == 400

    def test_correct_credentials_retrieve_the_full_record_including_score(self, keyring, bank_b):
        bus = InMemoryRiskBus(keyring)
        risk_id = risk_id_for_account(IFSC, ACCOUNT)
        bus.publish(bank_b.sign(_signal(risk_id, score=92)))

        audit = InMemoryAuditLog()
        app = create_app(bus, settings=ApiSettings(investigators={"alice": "secret"}), audit=audit)
        client = TestClient(app)

        transfer = client.post(
            "/transfers",
            json={
                "user_ref": "anu",
                "bank_id": "BANK-A",
                "receiver_ifsc": IFSC,
                "receiver_account": ACCOUNT,
                "amount_minor": 500000,
            },
        ).json()

        resp = client.get(
            f"/audit/{transfer['audit_ref']}",
            headers=INVESTIGATOR_HEADERS,
        )
        assert resp.status_code == 200
        record = resp.json()
        assert record["risk_score"] == 92  # only the analyst path may see this
        assert record["decision"] == "block"

        # The read is attributed to the named investigator who made it, not
        # just logged as "the admin key was used" -- see docs/SECURITY.md SS3.7.
        access = audit.access_log
        assert len(access) == 1
        assert access[0].investigator_id == "alice"
        assert access[0].reason == "fraud case #42"
        assert access[0].found is True

    def test_unknown_ref_is_404(self, keyring):
        bus = InMemoryRiskBus(keyring)
        app = create_app(
            bus, settings=ApiSettings(investigators={"alice": "secret"}), audit=InMemoryAuditLog()
        )
        resp = TestClient(app).get("/audit/never-issued-ref", headers=INVESTIGATOR_HEADERS)
        assert resp.status_code == 404
