"""M4's literal 'done when': the Anu example over HTTP, backed by the real
Kafka bus from M3 -- not the in-memory stand-in `tests/test_api.py` uses for
its (faster, always-on) coverage of the HTTP layer itself.

Skips cleanly when no broker is reachable, same as `tests/test_kafka_bus.py`.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.attestation import Publisher, PublisherKeyring
from aris.hashing import risk_id_for_account
from aris.kafka_bus import KafkaRiskBus
from aris.schema import RiskSignal
from aris.kafka_bus import load_kafka_config
from tests.test_kafka_bus import requires_kafka


@requires_kafka
def test_anu_transfer_blocked_over_http_via_kafka_bus():
    bootstrap, registry_url = load_kafka_config()
    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    keyring.register(bank_b.bank_id, bank_b.public_key)

    account = f"ACC-{uuid.uuid4().hex[:12]}"
    risk_id = risk_id_for_account(account)

    bus = KafkaRiskBus(keyring, bootstrap, registry_url)
    try:
        bus.publish(
            bank_b.sign(
                RiskSignal(
                    risk_id=risk_id,
                    risk_score=92,
                    confidence=0.94,
                    reason_codes=("new_beneficiary", "high_velocity"),
                    model_version="v0.4-fl",
                    source_bank_id="BANK-B",
                    ttl_hours=24,
                    timestamp=datetime.now(timezone.utc),
                )
            )
        )

        app = create_app(bus)
        client = TestClient(app)
        resp = client.post(
            "/transfers",
            json={
                "user_ref": "anu",
                "bank_id": "BANK-A",
                "receiver_account": account,
                "amount_minor": 500000,
                "currency": "INR",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "block"
    finally:
        bus.close()


@requires_kafka
def test_second_bank_process_sees_it_too_over_http():
    # bus_b publishes (its own process); bus_a is a *separate* KafkaRiskBus,
    # standing in for Bank A's own process, wired into its own API instance.
    bootstrap, registry_url = load_kafka_config()
    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    keyring.register(bank_b.bank_id, bank_b.public_key)

    account = f"ACC-{uuid.uuid4().hex[:12]}"
    risk_id = risk_id_for_account(account)

    bus_b = KafkaRiskBus(keyring, bootstrap, registry_url)
    bus_a = KafkaRiskBus(keyring, bootstrap, registry_url)
    try:
        bus_b.publish(
            bank_b.sign(
                RiskSignal(
                    risk_id=risk_id,
                    risk_score=92,
                    confidence=0.94,
                    reason_codes=("new_beneficiary",),
                    model_version="v0.4-fl",
                    source_bank_id="BANK-B",
                )
            )
        )

        client_a = TestClient(create_app(bus_a))
        body = {
            "user_ref": "anu",
            "bank_id": "BANK-A",
            "receiver_account": account,
            "amount_minor": 500000,
        }

        deadline = time.monotonic() + 15.0
        decision = None
        while time.monotonic() < deadline:
            decision = client_a.post("/transfers", json=body).json()["decision"]
            if decision == "block":
                break
            time.sleep(0.3)
            body = {**body, "transfer_id": f"retry-{uuid.uuid4().hex[:8]}"}
        assert decision == "block"
    finally:
        bus_b.close()
        bus_a.close()
