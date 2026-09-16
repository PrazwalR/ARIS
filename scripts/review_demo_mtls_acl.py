"""Live demo snippet for the review: proves mTLS + per-bank ACLs are real,
not just test assertions. Run with the aris_ci_sim venv active and
docker compose up (see the runbook)."""

import uuid

from kafka.admin import KafkaAdminClient
from kafka.errors import KafkaTimeoutError

from aris.attestation import Publisher, PublisherKeyring
from aris.hashing import risk_id_for_account
from aris.kafka_bus import KafkaPublishError, KafkaRiskBus, KafkaTlsConfig
from aris.schema import RiskSignal

CERTS = "/Users/prazw/Desktop/Web3/ARIS/certs"


def tls_for(bank: str) -> KafkaTlsConfig:
    return KafkaTlsConfig(
        ca_file=f"{CERTS}/ca.pem",
        cert_file=f"{CERTS}/banks/{bank}/cert.pem",
        key_file=f"{CERTS}/banks/{bank}/key.pem",
    )


keyring = PublisherKeyring()
bank_b = Publisher.generate("BANK-B")
bank_evil = Publisher.generate("BANK-EVIL")
keyring.register(bank_b.bank_id, bank_b.public_key)
keyring.register(bank_evil.bank_id, bank_evil.public_key)

account = f"ACC-{uuid.uuid4().hex[:12]}"
risk_id = risk_id_for_account("HDFC0001234", account)


def signal(score: int, bank: str) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=0.9,
        reason_codes=("high_velocity",),
        model_version="v0.4-fl",
        source_bank_id=bank,
    )


print(f"\naccount: {account}")
print(f"risk_id: {risk_id}\n")

print("--- BANK-B (has a Write ACL) publishes over mTLS ---")
bus_b = KafkaRiskBus(keyring, "localhost:9093", "http://localhost:8081", tls=tls_for("BANK-B"))
try:
    outcome = bus_b.publish(bank_b.sign(signal(92, "BANK-B")))
    print(f"  publish: {outcome}")
    result = bus_b.lookup(risk_id)
    print(f"  lookup:  {result.status.value}, score={result.score}\n")
finally:
    bus_b.close()

print("--- BANK-EVIL (valid mTLS cert, signed by the SAME CA, but NO Write ACL) ---")
bus_evil = KafkaRiskBus(
    keyring, "localhost:9093", "http://localhost:8081", tls=tls_for("BANK-EVIL")
)
try:
    try:
        bus_evil.publish(bank_evil.sign(signal(0, "BANK-EVIL")))
        print("  publish: SUCCEEDED  <-- should never print this")
    except KafkaPublishError as exc:
        print(f"  publish: REJECTED by the authorizer -- {exc}")
    result = bus_evil.lookup(risk_id)
    print(f"  lookup:  {result.status.value}, score={result.score}  (Read is still allowed)\n")
finally:
    bus_evil.close()

print("--- a client with NO certificate at all cannot even connect ---")
try:
    admin = KafkaAdminClient(
        bootstrap_servers="localhost:9093",
        security_protocol="SSL",
        ssl_cafile=f"{CERTS}/ca.pem",
        request_timeout_ms=5000,
        api_version=(3, 8, 0),
    )
    admin.list_topics()
    print("  connected  <-- should never print this")
except KafkaTimeoutError:
    print("  connection REJECTED -- no client certificate presented\n")
