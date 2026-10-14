"""The OPRF authority as a network service, and a BankBot API wired to it
(docs/SECURITY.md SS3.1). Real HTTP requests through FastAPI's ASGI stack;
the authority, each bank's client, and the API share nothing but HTTP."""

from __future__ import annotations

import os
import stat
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.api.config import ApiSettings, load_settings
from aris.api.wiring import build_keyring, build_risk_ids
from aris.attestation import Publisher, public_key_to_hex
from aris.bus import InMemoryRiskBus
from aris.oprf import (
    AuthorityKeyMismatch,
    OprfAuthority,
    RateLimitExceeded,
    public_key_fingerprint,
    risk_id_for_account_oprf,
)
from aris.oprf_service import (
    HttpOprfAuthorityClient,
    OprfServiceError,
    create_oprf_app,
    load_or_create_private_key,
)
from aris.risk_id import HmacRiskIdDeriver, OprfRiskIdDeriver, publish_id
from aris.schema import RiskSignal

IFSC = "HDFC0001234"
ACCOUNT = "ACC-999"
BANK_KEYS = {"BANK-A": "secret-a", "BANK-B": "secret-b"}


@pytest.fixture(scope="module")
def authority() -> OprfAuthority:
    return OprfAuthority(key_size=1024)


@pytest.fixture
def server(authority: OprfAuthority) -> TestClient:
    return TestClient(create_oprf_app(authority, BANK_KEYS))


def _fingerprint(authority: OprfAuthority) -> str:
    return public_key_fingerprint(*authority.public_numbers)


def _client(server: TestClient, authority: OprfAuthority, bank: str) -> HttpOprfAuthorityClient:
    return HttpOprfAuthorityClient(
        "http://testserver", bank, BANK_KEYS[bank], _fingerprint(authority), http=server
    )


class TestService:
    def test_public_key_advertises_its_fingerprint(self, server, authority):
        body = server.get("/public-key").json()
        assert body["fingerprint"] == _fingerprint(authority)
        assert int(body["n"], 16) == authority.public_numbers[0]

    @pytest.mark.parametrize(
        "headers",
        [{}, {"X-Bank-Id": "BANK-A"}, {"X-Bank-Id": "BANK-A", "X-Bank-Key": "wrong"}],
    )
    def test_evaluate_requires_valid_bank_credentials(self, server, headers):
        resp = server.post("/evaluate", json={"blinded": "2"}, headers=headers)
        assert resp.status_code == 401

    def test_unknown_bank_is_rejected(self, server):
        resp = server.post(
            "/evaluate", json={"blinded": "2"}, headers={"X-Bank-Id": "MALLORY", "X-Bank-Key": "x"}
        )
        assert resp.status_code == 401

    def test_non_hex_input_is_rejected(self, server):
        headers = {"X-Bank-Id": "BANK-A", "X-Bank-Key": "secret-a"}
        assert server.post("/evaluate", json={"blinded": "xyz"}, headers=headers).status_code == 422

    def test_out_of_range_input_is_rejected(self, server, authority):
        n, _ = authority.public_numbers
        headers = {"X-Bank-Id": "BANK-A", "X-Bank-Key": "secret-a"}
        resp = server.post("/evaluate", json={"blinded": format(n, "x")}, headers=headers)
        assert resp.status_code == 400

    def test_a_service_with_no_banks_refuses_to_start(self, authority):
        with pytest.raises(ValueError, match="no authorized banks"):
            create_oprf_app(authority, {})

    def test_queries_are_attributed_to_the_authenticated_bank(self, server, authority):
        before = len(authority.audit_log)
        risk_id_for_account_oprf(IFSC, ACCOUNT, _client(server, authority, "BANK-B"), "BANK-B")
        assert authority.audit_log[before].bank_id == "BANK-B"


class TestClientOverHttp:
    def test_matches_in_process_derivation(self, server, authority):
        over_http = risk_id_for_account_oprf(
            IFSC, ACCOUNT, _client(server, authority, "BANK-A"), "BANK-A"
        )
        in_process = risk_id_for_account_oprf(IFSC, ACCOUNT, authority, "BANK-A")
        assert over_http == in_process

    def test_two_banks_derive_the_same_id(self, server, authority):
        a = risk_id_for_account_oprf(IFSC, ACCOUNT, _client(server, authority, "BANK-A"), "BANK-A")
        b = risk_id_for_account_oprf(IFSC, ACCOUNT, _client(server, authority, "BANK-B"), "BANK-B")
        assert a == b

    def test_a_different_authority_key_is_refused(self, server):
        impostor_pin = public_key_fingerprint(*OprfAuthority(key_size=1024).public_numbers)
        client = HttpOprfAuthorityClient(
            "http://testserver", "BANK-A", "secret-a", impostor_pin, http=server
        )
        with pytest.raises(AuthorityKeyMismatch):
            _ = client.public_numbers

    def test_a_client_cannot_query_as_another_bank(self, server, authority):
        with pytest.raises(ValueError, match="BANK-A"):
            _client(server, authority, "BANK-A").evaluate("BANK-B", 2)

    def test_rate_limit_surfaces_as_rate_limit_exceeded(self):
        tight = OprfAuthority(key_size=1024, rate_limit=1)
        server = TestClient(create_oprf_app(tight, BANK_KEYS))
        client = _client(server, tight, "BANK-A")
        risk_id_for_account_oprf(IFSC, ACCOUNT, client, "BANK-A")
        with pytest.raises(RateLimitExceeded):
            risk_id_for_account_oprf(IFSC, ACCOUNT, client, "BANK-A")

    def test_a_bad_credential_surfaces_as_a_service_error(self, server, authority):
        client = HttpOprfAuthorityClient(
            "http://testserver", "BANK-A", "wrong", _fingerprint(authority), http=server
        )
        with pytest.raises(OprfServiceError):
            risk_id_for_account_oprf(IFSC, ACCOUNT, client, "BANK-A")


class TestAuthorityKeyPersistence:
    def test_refuses_to_invent_a_key_unless_asked(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="--generate-key"):
            load_or_create_private_key(tmp_path / "k.pem", create=False)

    def test_a_restart_keeps_the_same_risk_id_space(self, tmp_path: Path):
        path = tmp_path / "k.pem"
        first = OprfAuthority(private_key=load_or_create_private_key(path, create=True))
        second = OprfAuthority(private_key=load_or_create_private_key(path, create=False))
        assert first.public_numbers == second.public_numbers
        assert risk_id_for_account_oprf(IFSC, ACCOUNT, first, "BANK-A") == (
            risk_id_for_account_oprf(IFSC, ACCOUNT, second, "BANK-A")
        )

    def test_key_file_is_owner_only(self, tmp_path: Path):
        path = tmp_path / "k.pem"
        load_or_create_private_key(path, create=True)
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


class TestApiConfig:
    def test_oprf_mode_without_a_pinned_fingerprint_is_refused(self):
        with pytest.raises(ValueError, match="ARIS_API_OPRF_FINGERPRINT"):
            ApiSettings(risk_id_mode="oprf", oprf_url="http://x", oprf_bank_key="k")

    def test_unknown_mode_is_refused(self):
        with pytest.raises(ValueError, match="risk_id_mode"):
            ApiSettings(risk_id_mode="plaintext")

    def test_load_settings_reads_mode_bank_and_publishers(self, monkeypatch):
        pub = Publisher.generate("BANK-B")
        monkeypatch.setenv("ARIS_API_RISK_ID_MODE", "oprf")
        monkeypatch.setenv("ARIS_API_BANK_ID", "BANK-A")
        monkeypatch.setenv("ARIS_API_OPRF_URL", "http://authority")
        monkeypatch.setenv("ARIS_API_OPRF_BANK_KEY", "secret-a")
        monkeypatch.setenv("ARIS_API_OPRF_FINGERPRINT", "ab" * 32)
        monkeypatch.setenv(
            "ARIS_API_TRUSTED_PUBLISHERS", f"BANK-B:{public_key_to_hex(pub.public_key)}"
        )
        s = load_settings()
        assert (s.risk_id_mode, s.bank_id, s.oprf_url) == ("oprf", "BANK-A", "http://authority")
        assert build_keyring(s).trusts("BANK-B")

    def test_a_malformed_publisher_key_is_refused(self):
        with pytest.raises(ValueError, match="32 bytes"):
            build_keyring(ApiSettings(trusted_publishers={"BANK-B": "abcd"}))

    def test_hmac_mode_builds_the_hmac_deriver(self):
        with ExitStack() as resources:
            assert isinstance(build_risk_ids(ApiSettings(), resources), HmacRiskIdDeriver)

    def test_oprf_mode_checks_the_pin_at_startup(self, server):
        settings = ApiSettings(
            risk_id_mode="oprf",
            oprf_url="http://testserver",
            oprf_bank_key="secret-a",
            oprf_fingerprint="00" * 32,
        )
        with ExitStack() as resources, pytest.raises(AuthorityKeyMismatch):
            build_risk_ids(settings, resources, oprf_http=server)


class TestApiEndToEndOverOprf:
    """Bank B publishes under an OPRF id derived over HTTP; Bank A's API,
    configured from settings the way `python -m aris.api` is, blocks the
    transfer over HTTP. Neither bank ever computes an id offline."""

    def test_flag_published_via_the_authority_blocks_over_http(self, server, authority):
        bank_b = Publisher.generate("BANK-B")
        settings = ApiSettings(
            bank_id="BANK-A",
            risk_id_mode="oprf",
            oprf_url="http://testserver",
            oprf_bank_key="secret-a",
            oprf_fingerprint=_fingerprint(authority),
            trusted_publishers={"BANK-B": public_key_to_hex(bank_b.public_key)},
        )
        bus = InMemoryRiskBus(build_keyring(settings))

        bank_b_ids = OprfRiskIdDeriver(_client(server, authority, "BANK-B"), "BANK-B")
        risk_id = publish_id(bank_b_ids, IFSC, ACCOUNT, datetime.now(timezone.utc))
        bus.publish(
            bank_b.sign(
                RiskSignal(
                    risk_id=risk_id,
                    risk_score=92,
                    confidence=0.94,
                    reason_codes=("high_velocity",),
                    model_version="v0.4-fl",
                    source_bank_id="BANK-B",
                )
            )
        )

        with ExitStack() as resources:
            risk_ids = build_risk_ids(settings, resources, oprf_http=server)
            api = TestClient(create_app(bus, settings=settings, risk_ids=risk_ids))
            before = len(authority.audit_log)
            resp = api.post(
                "/transfers",
                json={
                    "user_ref": "anu",
                    "bank_id": "BANK-A",
                    "receiver_ifsc": IFSC,
                    "receiver_account": ACCOUNT,
                    "amount_minor": 500000,
                },
            )
        assert resp.status_code == 200
        assert resp.json()["decision"] == "block"
        assert [r.bank_id for r in authority.audit_log[before:]] == ["BANK-A"]
