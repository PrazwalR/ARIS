"""Live demo: a real HTTP BLOCK decision backed by the live Kafka bus --
ties M3 (Kafka) and M4 (the HTTP API) together in one request.

`python -m aris.api` on its own can't show this: its keyring starts empty,
so it trusts no bank and every lookup reads as unflagged. This script wires
`create_app()` up with a keyring that trusts BANK-B, same as
tests/test_api_kafka.py does, then drives the real FastAPI app through
Starlette's TestClient (real ASGI request handling, not a mock).
"""

import uuid

from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.attestation import Publisher, PublisherKeyring
from aris.hashing import risk_id_for_account
from aris.kafka_bus import KafkaRiskBus
from aris.schema import RiskSignal

keyring = PublisherKeyring()
bank_b = Publisher.generate("BANK-B")
keyring.register(bank_b.bank_id, bank_b.public_key)

account = f"ACC-{uuid.uuid4().hex[:8]}"
risk_id = risk_id_for_account("HDFC0001234", account)

bus = KafkaRiskBus(keyring, "localhost:9092", "http://localhost:8081")
try:
    print(f"\n--- BANK-B publishes a flag for {account} to the live Kafka bus ---")
    bus.publish(
        bank_b.sign(
            RiskSignal(
                risk_id=risk_id,
                risk_score=92,
                confidence=0.94,
                reason_codes=("new_beneficiary", "high_velocity"),
                model_version="v0.4-fl",
                source_bank_id="BANK-B",
            )
        )
    )

    print("--- Bank A's own process, sharing nothing but the broker, calls POST /transfers ---")
    client = TestClient(create_app(bus))
    resp = client.post(
        "/transfers",
        json={
            "user_ref": "anu",
            "bank_id": "BANK-A",
            "receiver_ifsc": "HDFC0001234",
            "receiver_account": account,
            "amount_minor": 500000,
        },
    )
    print(f"  HTTP {resp.status_code}")
    print(f"  {resp.json()}")
finally:
    bus.close()
