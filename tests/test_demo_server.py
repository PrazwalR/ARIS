"""The showcase server: the hero story over real HTTP, end to end."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aris.demo.server import INVESTIGATOR_ID, create_showcase_app

IFSC = "HDFC0001234"
ACCOUNT = "ACC-999"
KEY = "test-investigator-key"
ACCOUNT_BODY = {"receiver_ifsc": IFSC, "receiver_account": ACCOUNT}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_showcase_app(investigator_key=KEY, dist_dir=None))


def _transfer(client: TestClient, account: str = ACCOUNT, **extra: object) -> dict[str, object]:
    resp = client.post(
        "/api/transfers",
        json={
            "user_ref": "anu",
            "bank_id": "BANK-A",
            "receiver_ifsc": IFSC,
            "receiver_account": account,
            "amount_minor": 500_000,
            **extra,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestHeroStory:
    def test_unflagged_account_is_allowed(self, client):
        assert _transfer(client)["decision"] == "allow"

    def test_bank_b_flag_then_bank_a_blocks(self, client):
        flagged = client.post("/demo/flag", json=ACCOUNT_BODY).json()
        assert flagged["outcome"] == "accepted"
        assert flagged["network"] == {
            "status": "found",
            "score": 92,
            "contributing_banks": ["BANK-B"],
        }
        assert len(flagged["signature"]) == 128  # Ed25519, hex
        assert _transfer(client)["decision"] == "block"

    def test_forged_retraction_is_rejected_and_changes_nothing(self, client):
        client.post("/demo/flag", json=ACCOUNT_BODY)
        forged = client.post("/demo/forge", json=ACCOUNT_BODY).json()
        assert forged["result"] == "rejected"
        assert forged["reason"].startswith("signature invalid")
        assert forged["signer"] == "BANK-EVIL"
        assert forged["claimed_publisher"] == "BANK-B"
        assert forged["network"]["score"] == 92
        assert _transfer(client)["decision"] == "block"

    def test_flag_and_transfer_derive_the_same_risk_id(self, client):
        flagged = client.post("/demo/flag", json=ACCOUNT_BODY).json()
        seen = client.post("/demo/risk-id", json=ACCOUNT_BODY).json()
        assert seen["risk_id"] == flagged["risk_id"]
        ref = _transfer(client)["audit_ref"]
        record = client.get(
            f"/api/audit/{ref}",
            headers={
                "X-Investigator-Id": INVESTIGATOR_ID,
                "X-Investigator-Key": KEY,
                "X-Investigator-Reason": "test",
            },
        ).json()
        assert record["risk_id"] == flagged["risk_id"]
        assert record["risk_score"] == 92

    def test_reset_clears_the_bus_and_the_feed(self, client):
        client.post("/demo/flag", json=ACCOUNT_BODY)
        _transfer(client)
        client.post("/demo/reset")
        assert client.get("/demo/feed").json()["decisions"] == []
        assert _transfer(client)["decision"] == "allow"


class TestPolicyCases:
    """Every branch the Live page's policy table runs, decided by BankBot."""

    @pytest.mark.parametrize(
        ("score", "confidence", "amount", "expected"),
        [
            (None, None, 250_000, "allow"),
            (None, None, 15_000_000, "step_up"),
            (92, 0.94, 500_000, "block"),
            (85, 0.9, 500_000, "block"),
            (84, 0.9, 500_000, "step_up"),
            (70, 0.9, 500_000, "step_up"),
            (40, 0.9, 500_000, "allow"),
            (92, 0.3, 500_000, "step_up"),
        ],
    )
    def test_decision_follows_the_policy(self, client, score, confidence, amount, expected):
        if score is not None:
            flag = client.post(
                "/demo/flag",
                json={**ACCOUNT_BODY, "risk_score": score, "confidence": confidence},
            )
            assert flag.json()["outcome"] == "accepted"
        assert _transfer(client, amount_minor=amount)["decision"] == expected

    def test_outage_fails_closed_even_for_a_clean_account(self, client):
        assert _transfer(client)["decision"] == "allow"
        assert client.post("/demo/outage", json={"down": True}).json() == {"bus_down": True}
        assert client.get("/demo/health").json()["bus_down"] is True
        assert _transfer(client)["decision"] == "step_up"
        client.post("/demo/outage", json={"down": False})
        assert _transfer(client)["decision"] == "allow"

    def test_outage_does_not_erase_what_the_bus_holds(self, client):
        client.post("/demo/flag", json=ACCOUNT_BODY)
        client.post("/demo/outage", json={"down": True})
        view = client.post("/demo/risk-id", json=ACCOUNT_BODY).json()["network"]
        assert view["score"] == 92
        client.post("/demo/outage", json={"down": False})
        assert _transfer(client)["decision"] == "block"

    def test_reset_restores_the_bus(self, client):
        client.post("/demo/outage", json={"down": True})
        client.post("/demo/reset")
        assert client.get("/demo/health").json()["bus_down"] is False

    def test_outage_switch_rejects_non_boolean(self, client):
        assert client.post("/demo/outage", json={"down": "yes"}).status_code == 422


class TestPrivacy:
    def test_feed_never_carries_an_account_number(self, client):
        client.post("/demo/flag", json=ACCOUNT_BODY)
        _transfer(client)
        _transfer(client, account="ACC-CLEAN-1")
        body = client.get("/demo/feed").text
        assert ACCOUNT not in body
        assert "ACC-CLEAN-1" not in body
        rows = client.get("/demo/feed").json()["decisions"]
        assert [r["decision"] for r in rows] == ["allow", "block"]  # newest first
        assert set(rows[0]) == {
            "audit_ref",
            "risk_id",
            "decision",
            "lookup_status",
            "amount_minor",
            "currency",
            "decided_at",
        }

    def test_flag_response_does_not_echo_the_account(self, client):
        assert ACCOUNT not in client.post("/demo/flag", json=ACCOUNT_BODY).text

    def test_audit_needs_investigator_credentials(self, client):
        ref = _transfer(client)["audit_ref"]
        assert client.get(f"/api/audit/{ref}").status_code == 401
        wrong = {
            "X-Investigator-Id": INVESTIGATOR_ID,
            "X-Investigator-Key": "nope",
            "X-Investigator-Reason": "x",
        }
        assert client.get(f"/api/audit/{ref}", headers=wrong).status_code == 401


class TestInputValidation:
    @pytest.mark.parametrize(
        "body",
        [
            {"receiver_ifsc": "BAD", "receiver_account": ACCOUNT},
            {"receiver_ifsc": IFSC, "receiver_account": ""},
            {"receiver_ifsc": IFSC, "receiver_account": ACCOUNT, "extra": 1},
        ],
    )
    def test_malformed_requests_are_rejected(self, client, body):
        assert client.post("/demo/flag", json=body).status_code == 422

    def test_invalid_ifsc_shape_is_rejected_on_risk_id(self, client):
        resp = client.post(
            "/demo/risk-id", json={"receiver_ifsc": "hdfc-001234", "receiver_account": "A"}
        )
        assert resp.status_code == 422


class TestStaticServing:
    def test_serves_the_built_frontend_and_falls_back_to_it(self, tmp_path: Path):
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "app.js").write_text("console.log(1)")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "metrics.json").write_text('{"ok": true}')
        (tmp_path / "index.html").write_text("<!doctype html><title>ARIS</title>")
        client = TestClient(create_showcase_app(investigator_key=KEY, dist_dir=tmp_path))
        assert "ARIS" in client.get("/").text
        assert "ARIS" in client.get("/metrics").text  # SPA fallback
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/data/metrics.json").json() == {"ok": True}
        assert client.post("/demo/reset").status_code == 200  # API still routed

    def test_path_traversal_falls_back_to_the_app(self, tmp_path: Path):
        (tmp_path / "assets").mkdir()
        (tmp_path / "index.html").write_text("<!doctype html><title>ARIS</title>")
        client = TestClient(create_showcase_app(investigator_key=KEY, dist_dir=tmp_path))
        resp = client.get("/..%2F..%2Fpyproject.toml")
        assert "[project]" not in resp.text
