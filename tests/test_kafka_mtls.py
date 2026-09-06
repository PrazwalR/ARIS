"""docs/SECURITY.md SS3.8: mTLS + per-principal Kafka ACLs.

Needs docker-compose.yml's SSL_HOST listener (port 9093) and local dev certs
(`python scripts/generate_dev_certs.py`) -- skips cleanly, not silently, when
either is unavailable, same as tests/test_kafka_bus.py does for the plain
broker.

This module grants its own ACLs on import (via the plaintext listener, where
User:ANONYMOUS is a super user -- see docker-compose.yml), so it does not
depend on scripts/setup_kafka_acls.sh having been run manually first, only on
the broker being up and the certs existing.
"""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

import pytest
from kafka.admin import (
    ACL,
    ACLOperation,
    ACLPermissionType,
    KafkaAdminClient,
    ResourcePattern,
    ResourceType,
)
from kafka.errors import KafkaTimeoutError

from aris.attestation import Publisher, PublisherKeyring
from aris.bus import PublishOutcome
from aris.hashing import risk_id_for_account
from aris.kafka_bus import (
    KafkaPublishError,
    KafkaRiskBus,
    KafkaTlsConfig,
    ensure_topic,
    load_kafka_config,
)
from aris.schema import LookupStatus, RiskSignal
from tests.test_kafka_bus import _kafka_reachable, _registry_reachable

_BOOTSTRAP, _REGISTRY_URL = load_kafka_config()
_SSL_BOOTSTRAP = f"{_BOOTSTRAP.rsplit(':', 1)[0]}:9093"
_CERTS_DIR = Path(__file__).resolve().parent.parent / "certs"
_IFSC = "HDFC0001234"


def _ssl_port_reachable() -> bool:
    host, _, port = _SSL_BOOTSTRAP.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _certs_present() -> bool:
    required = [
        _CERTS_DIR / "ca.pem",
        _CERTS_DIR / "banks" / "BANK-B" / "cert.pem",
        _CERTS_DIR / "banks" / "BANK-B" / "key.pem",
        _CERTS_DIR / "banks" / "BANK-EVIL" / "cert.pem",
        _CERTS_DIR / "banks" / "BANK-EVIL" / "key.pem",
    ]
    return all(p.exists() for p in required)


def _mtls_available() -> bool:
    return (
        _kafka_reachable() and _registry_reachable() and _ssl_port_reachable() and _certs_present()
    )


requires_mtls = pytest.mark.skipif(
    not _mtls_available(),
    reason=(
        "mTLS Kafka listener or dev certs not available -- run "
        "`python scripts/generate_dev_certs.py` then `docker compose up -d` "
        "(docker-compose.yml's SSL_HOST listener on :9093) to exercise this suite"
    ),
)


def _tls_for(bank_id: str) -> KafkaTlsConfig:
    return KafkaTlsConfig(
        ca_file=str(_CERTS_DIR / "ca.pem"),
        cert_file=str(_CERTS_DIR / "banks" / bank_id / "cert.pem"),
        key_file=str(_CERTS_DIR / "banks" / bank_id / "key.pem"),
    )


def _signal(risk_id: str, score: int, bank: str) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=0.9,
        reason_codes=("high_velocity",),
        model_version="v0.4-fl",
        source_bank_id=bank,
    )


def _new_risk_id() -> str:
    return risk_id_for_account(_IFSC, f"ACC-{uuid.uuid4().hex[:12]}")


@pytest.fixture(scope="module", autouse=True)
def _grant_acls():
    if not _mtls_available():
        yield
        return

    ensure_topic(_BOOTSTRAP)  # plaintext, User:ANONYMOUS -- always allowed
    admin = KafkaAdminClient(bootstrap_servers=_BOOTSTRAP)
    try:
        topic = ResourcePattern(ResourceType.TOPIC, "risk-signals")
        admin.create_acls(
            [
                ACL("User:BANK-B", "*", op, ACLPermissionType.ALLOW, topic)
                for op in (ACLOperation.WRITE, ACLOperation.DESCRIBE)
            ]
            + [
                ACL("User:*", "*", op, ACLPermissionType.ALLOW, topic)
                for op in (ACLOperation.READ, ACLOperation.DESCRIBE)
            ]
        )
    finally:
        admin.close()
    yield


@requires_mtls
class TestMtlsAndAcls:
    def test_an_authorized_bank_can_publish_and_lookup_over_mtls(self):
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        risk_id = _new_risk_id()

        bus = KafkaRiskBus(keyring, _SSL_BOOTSTRAP, _REGISTRY_URL, tls=_tls_for("BANK-B"))
        try:
            outcome = bus.publish(bank_b.sign(_signal(risk_id, 92, "BANK-B")))
            assert outcome is PublishOutcome.ACCEPTED
            result = bus.lookup(risk_id)
            assert result.status is LookupStatus.FOUND
            assert result.score == 92
        finally:
            bus.close()

    def test_an_authenticated_but_unauthorized_bank_cannot_publish(self):
        """BANK-EVIL has a valid mTLS identity -- the broker trusts its
        certificate, signed by the same CA as every other bank's -- but no
        Write ACL. The authorizer, not the TLS handshake, is what must
        reject it."""
        keyring = PublisherKeyring()
        bank_evil = Publisher.generate("BANK-EVIL")
        keyring.register(bank_evil.bank_id, bank_evil.public_key)
        risk_id = _new_risk_id()

        bus = KafkaRiskBus(keyring, _SSL_BOOTSTRAP, _REGISTRY_URL, tls=_tls_for("BANK-EVIL"))
        try:
            with pytest.raises(KafkaPublishError):
                bus.publish(bank_evil.sign(_signal(risk_id, 0, "BANK-EVIL")))
        finally:
            bus.close()

    def test_an_unauthorized_bank_can_still_read(self):
        """The Read ACL is granted broadly (User:*): SS3.8 restricts who may
        publish, not who may consume the compacted topic to build a local
        view -- that is not a per-bank-scoped operation in this design."""
        keyring = PublisherKeyring()
        bank_b = Publisher.generate("BANK-B")
        bank_evil = Publisher.generate("BANK-EVIL")
        keyring.register(bank_b.bank_id, bank_b.public_key)
        keyring.register(bank_evil.bank_id, bank_evil.public_key)
        risk_id = _new_risk_id()

        bus_b = KafkaRiskBus(keyring, _SSL_BOOTSTRAP, _REGISTRY_URL, tls=_tls_for("BANK-B"))
        try:
            bus_b.publish(bank_b.sign(_signal(risk_id, 92, "BANK-B")))
        finally:
            bus_b.close()

        bus_evil = KafkaRiskBus(keyring, _SSL_BOOTSTRAP, _REGISTRY_URL, tls=_tls_for("BANK-EVIL"))
        try:
            result = bus_evil.lookup(risk_id)
            assert result.status is LookupStatus.FOUND
            assert result.score == 92
        finally:
            bus_evil.close()

    def test_a_client_with_no_certificate_at_all_cannot_connect(self):
        """mTLS is enforced, not optional: a connection presenting no client
        certificate must fail before ever reaching the authorizer."""
        with pytest.raises(KafkaTimeoutError):
            admin = KafkaAdminClient(
                bootstrap_servers=_SSL_BOOTSTRAP,
                security_protocol="SSL",
                ssl_cafile=str(_CERTS_DIR / "ca.pem"),
                request_timeout_ms=5000,
                api_version=(3, 8, 0),
            )
            admin.list_topics()

    def test_ensure_topic_succeeds_for_a_bank_without_create_permission(self):
        """An individual bank's identity is not expected to hold Create on
        risk-signals -- provisioning it is an operator action. ensure_topic
        must still succeed against the already-existing topic, not fail just
        because this principal cannot also create one from scratch."""
        ensure_topic(_SSL_BOOTSTRAP, tls=_tls_for("BANK-B"))
