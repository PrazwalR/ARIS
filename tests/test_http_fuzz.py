"""Negative-path and fuzz tests for every HTTP surface ARIS exposes.

Surfaces: the BankBot API (``aris.api.app.create_app``), the OPRF authority
(``aris.oprf_service.create_oprf_app``) and the showcase server
(``aris.demo.server.create_showcase_app``), including its SPA catch-all.

Invariants checked across hand-written and seeded-random inputs (see
docs/SECURITY.md):

- no client input ever produces a 5xx (the one deliberate 503 -- audit lookup
  with no investigators configured -- is a deployment state, tested separately);
- a customer-facing ``/transfers`` response never carries the score, the
  risk_id or the flagging bank;
- audit evidence is reachable only with valid investigator credentials;
- the OPRF service attributes every evaluation to the authenticated bank and
  never to an unauthenticated or mismatched bank id;
- ``/demo/feed`` never contains an account number;
- the SPA catch-all never serves a file outside ``dist``.

All requests are in-process (TestClient or a raw ASGI call); nothing touches
the network. The raw ASGI helper exists because httpx normalises ``..`` out of
URLs and refuses control characters in headers, both of which an attacker's own
client need not do.
"""

from __future__ import annotations

import asyncio
import json
import random
import string
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.api.config import ApiSettings
from aris.attestation import Publisher, PublisherKeyring
from aris.bankbot import InMemoryAuditLog
from aris.bus import InMemoryRiskBus
from aris.demo.server import INVESTIGATOR_ID, create_showcase_app
from aris.oprf import OprfAuthority
from aris.oprf_service import create_oprf_app
from aris.risk_id import HmacRiskIdDeriver, publish_id
from aris.schema import RiskSignal

IFSC = "HDFC0001234"
FLAGGED_ACCOUNT = "ACC-999"
FLAGGED_SCORE = 92
CLEAN_ACCOUNT = "ZQCLEAN-7781"
INVESTIGATOR_KEY = "fuzz-investigator-key"
INVESTIGATOR_HEADERS = {
    "X-Investigator-Id": INVESTIGATOR_ID,
    "X-Investigator-Key": INVESTIGATOR_KEY,
    "X-Investigator-Reason": "fuzz test",
}
BANK_KEYS = {"BANK-A": "secret-a", "BANK-C": "secret-c"}
TRANSFER_KEYS = {"decision", "user_message", "audit_ref", "step_up_required"}
FUZZ_CASES = 300
SECRET_CANARY = "TOP-SECRET-CANARY-6f1d"
INDEX_BODY = "<!doctype html><title>ARIS-INDEX</title>"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False so a crash surfaces as the 500 a real
    # client would see, which is exactly what these tests assert against.
    return TestClient(app, raise_server_exceptions=False)


def _post_raw(client: TestClient, url: str, body: bytes, **headers: str) -> Any:
    return client.post(url, content=body, headers={"content-type": "application/json", **headers})


def _post_json(client: TestClient, url: str, payload: object, **headers: str) -> Any:
    # json.dumps(allow_nan=True) rather than httpx's json=, so NaN/Infinity
    # (which Python's json module emits and parses) actually reach the server.
    return _post_raw(client, url, json.dumps(payload).encode(), **headers)


def _asgi(
    app: FastAPI,
    method: str,
    path: str,
    headers: Iterable[tuple[bytes, bytes]] = (),
    body: bytes = b"",
) -> tuple[int, bytes]:
    """Call the ASGI app directly with an unnormalised path and raw headers."""

    async def run() -> tuple[int, bytes]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("utf-8", "surrogateescape"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver"), *headers],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
        sent_body = False
        status = 0
        chunks: list[bytes] = []

        async def receive() -> dict[str, object]:
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": body, "more_body": False}
            await asyncio.sleep(3600)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        try:
            await app(scope, receive, send)
        except Exception:
            # ServerErrorMiddleware sends its 500 and then re-raises.
            status = status or 500
        return status, b"".join(chunks)

    return asyncio.run(run())


def _transfer_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "user_ref": "anu",
        "bank_id": "BANK-A",
        "receiver_ifsc": IFSC,
        "receiver_account": CLEAN_ACCOUNT,
        "amount_minor": 500_000,
        "currency": "INR",
    }
    body.update(overrides)
    return body


def _flag_signal(risk_id: str) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=FLAGGED_SCORE,
        confidence=0.94,
        reason_codes=("new_beneficiary", "high_velocity"),
        model_version="v0.4-fl",
        source_bank_id="BANK-B",
        ttl_hours=24,
        timestamp=datetime.now(timezone.utc),
    )


def _assert_no_customer_leak(resp: Any, risk_id: str, request_text: str) -> None:
    """The customer-facing /transfers response never carries evidence."""
    text = resp.text
    assert risk_id not in text, f"risk_id leaked: {text[:300]}"
    if "BANK-B" not in request_text:
        assert "BANK-B" not in text, f"flagging bank leaked: {text[:300]}"
    if resp.status_code == 200:
        body = resp.json()
        assert set(body) == TRANSFER_KEYS, body
        assert str(FLAGGED_SCORE) not in body["user_message"]
        assert "score" not in body["user_message"].lower()


def _fmt_failures(failures: list[str]) -> str:
    return f"{len(failures)} failing inputs, first ones:\n" + "\n".join(failures[:8])


# --------------------------------------------------------------------------
# seeded generators
# --------------------------------------------------------------------------

SPECIAL_CHARS = [
    "\u202e",  # RTL override
    "\u200b",  # zero-width space
    "\u200d",  # zero-width joiner
    "\ufeff",  # BOM
    "\x00",
    "\r",
    "\n",
    "\r\n",
    "\t",
    "\u0085",
    "\u2028",
    "\u2029",
    "\u0130",  # dotted capital I
    "\u00df",  # sharp s
    "\ufb00",  # ff ligature
    "\U0001f4a9",
    "\u00e9",
    "\u0661",  # arabic-indic digit one
    "\\",
    "/",
    "..",
    "'",
    '"',
    "%00",
    "${jndi:x}",
    "<script>",
]
LENGTHS = [0, 1, 3, 4, 8, 10, 11, 12, 32, 33, 34, 35, 64, 65, 200, 5000]
INTS = [0, 1, -1, 2, 99, 100, 101, 10**15, 10**15 + 1, 10**15 - 1, 2**31, 2**63, -(2**63), 10**30]
FLOATS = [0.0, -0.0, 0.5, 1.0, 1.5, 5.0, 1e308, -1e308, 1e-300, 0.94, 99.99]


def _rand_str(rng: random.Random) -> str:
    n = rng.choice(LENGTHS)
    kind = rng.randrange(5)
    if kind == 0:
        alphabet = string.printable
    elif kind == 1:
        alphabet = string.ascii_uppercase + string.digits + "-"
    elif kind == 2:
        alphabet = string.ascii_letters + string.digits + " ._-/"
    elif kind == 3:
        return "".join(rng.choice(SPECIAL_CHARS) for _ in range(min(n, 40)))
    else:
        alphabet = "".join(chr(c) for c in range(0x20, 0x2FF))
    out = "".join(rng.choice(alphabet) for _ in range(n))
    if out and rng.random() < 0.3:
        pos = rng.randrange(len(out))
        out = out[:pos] + rng.choice(SPECIAL_CHARS) + out[pos:]
    return out


def _rand_value(rng: random.Random, depth: int = 0) -> object:
    roll = rng.randrange(10)
    if roll <= 3:
        return _rand_str(rng)
    if roll == 4:
        return rng.choice(INTS) if rng.random() < 0.7 else rng.randint(-(10**18), 10**18)
    if roll == 5:
        return rng.choice(FLOATS)
    if roll == 6:
        return rng.choice([True, False, None])
    if roll == 7 and depth < 3:
        return [_rand_value(rng, depth + 1) for _ in range(rng.randrange(4))]
    if roll == 8 and depth < 3:
        return {_rand_str(rng)[:20]: _rand_value(rng, depth + 1) for _ in range(rng.randrange(4))}
    return rng.choice(["", " ", "null", "0", "-1", "1e3", "NaN"])


def _mutate(
    rng: random.Random, base: dict[str, object], plausible: dict[str, list[object]]
) -> object:
    """Mutate a valid body: swap, drop or add fields, or replace it outright."""
    roll = rng.random()
    if roll < 0.05:
        return _rand_value(rng)
    body = dict(base)
    for _ in range(rng.randint(1, 3)):
        op = rng.random()
        field = rng.choice(list(base))
        if op < 0.45 and field in plausible:
            body[field] = rng.choice(plausible[field])
        elif op < 0.75:
            body[field] = _rand_value(rng)
        elif op < 0.88:
            body.pop(field, None)
        else:
            body[_rand_str(rng)[:16] or "extra"] = _rand_value(rng)
    return body


def _ifsc_variants(rng: random.Random) -> list[object]:
    return [
        IFSC,
        IFSC.lower(),
        "HdFc0001234",
        " " + IFSC,
        IFSC + " ",
        "HDFC 001234",
        "HDFC1001234",
        "HDFC000123",
        "HDFC00012345",
        "HDFC000123\u0130",
        "\u202eHDFC000123",
        "".join(rng.choice(string.ascii_uppercase) for _ in range(4))
        + "0"
        + "".join(rng.choice(string.ascii_uppercase + string.digits) for _ in range(6)),
    ]


def _account_variants(rng: random.Random) -> list[object]:
    return [
        FLAGGED_ACCOUNT,
        FLAGGED_ACCOUNT.lower(),
        " ACC-999 ",
        "ACC 999",
        "ACC.999",
        "ACC_999",
        "ab",
        "A" * 34,
        "A" * 35,
        "ACC-\u0130",
        "ACC-999\u200b",
        "ZQ" + "".join(rng.choice(string.digits) for _ in range(rng.randint(2, 30))),
    ]


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def api_ctx() -> dict[str, Any]:
    """A BankBot API with ACC-999 flagged by BANK-B and one investigator."""
    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    keyring.register(bank_b.bank_id, bank_b.public_key)
    bus = InMemoryRiskBus(keyring)
    risk_id = publish_id(HmacRiskIdDeriver(), IFSC, FLAGGED_ACCOUNT, datetime.now(timezone.utc))
    bus.publish(bank_b.sign(_flag_signal(risk_id)))
    audit = InMemoryAuditLog()
    app = create_app(
        bus,
        settings=ApiSettings(investigators={INVESTIGATOR_ID: INVESTIGATOR_KEY}),
        audit=audit,
    )
    return {"app": app, "client": _client(app), "risk_id": risk_id, "audit": audit}


@pytest.fixture(scope="module")
def authority_key() -> rsa.RSAPrivateKey:
    # One 1024-bit key for the module: key generation dominates otherwise.
    return rsa.generate_private_key(public_exponent=65537, key_size=1024)


@pytest.fixture
def oprf_ctx(authority_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    # A fresh authority (fresh audit log and rate windows) per test.
    key = authority_key
    n = key.public_key().public_numbers().n
    authority = OprfAuthority(private_key=key, rate_limit=100_000)
    app = create_oprf_app(authority, BANK_KEYS)
    return {"authority": authority, "app": app, "client": _client(app), "n": n}


@pytest.fixture
def dist(tmp_path: Path) -> dict[str, Path]:
    """A dist dir with real files, an outside secret and hostile symlinks."""
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text(SECRET_CANARY)
    # Sibling whose name shares dist's prefix: catches a string-prefix check.
    evil = tmp_path / "dist-evil"
    evil.mkdir()
    (evil / "secret.txt").write_text(SECRET_CANARY)

    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "data").mkdir()
    (d / "index.html").write_text(INDEX_BODY)
    (d / "favicon.ico").write_text("FAVICON")
    (d / "assets" / "app.js").write_text("console.log('app')")
    (d / "data" / "metrics.json").write_text('{"ok": true}')
    (d / "leak.txt").symlink_to(secret)
    (d / "leakdir").symlink_to(outside, target_is_directory=True)
    (d / "assets" / "leak.js").symlink_to(secret)
    (d / "data" / "leak.json").symlink_to(secret)
    return {"dist": d, "outside": outside, "secret": secret, "evil": evil}


@pytest.fixture
def showcase(dist: dict[str, Path]) -> dict[str, Any]:
    app = create_showcase_app(investigator_key=INVESTIGATOR_KEY, dist_dir=dist["dist"])
    return {"app": app, "client": _client(app)}


@pytest.fixture
def showcase_api_only() -> dict[str, Any]:
    app = create_showcase_app(investigator_key=INVESTIGATOR_KEY, dist_dir=None)
    return {"app": app, "client": _client(app)}


# --------------------------------------------------------------------------
# BankBot API: POST /transfers
# --------------------------------------------------------------------------


class TestTransfersMalformedBodies:
    @pytest.mark.parametrize(
        "raw",
        [
            b"",
            b"{",
            b"}",
            b"null",
            b"[]",
            b'"string"',
            b"42",
            b"true",
            b"{'single': 'quotes'}",
            b'{"user_ref": "a",}',
            b"\xff\xfe\xfd",
            b"\x00\x00\x00",
            b'{"user_ref": "\xff"}',
            b"[" * 50 + b"]" * 50,
        ],
    )
    def test_malformed_json_is_rejected_cleanly(self, api_ctx, raw):
        # 400 (unparseable body) and 422 (parsed but invalid) are both correct.
        resp = _post_raw(api_ctx["client"], "/transfers", raw)
        assert resp.status_code in (400, 422), (raw, resp.status_code, resp.text[:200])

    def test_deeply_nested_json_is_a_client_error_not_a_crash(self, api_ctx):
        raw = b'{"a":' * 5000 + b"1" + b"}" * 5000
        resp = _post_raw(api_ctx["client"], "/transfers", raw)
        assert resp.status_code < 500, (resp.status_code, resp.text[:200])

    @pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
    def test_non_standard_json_numbers_for_amount_are_rejected(self, api_ctx, token):
        raw = (
            json.dumps(_transfer_body(amount_minor=0))
            .encode()
            .replace(b'"amount_minor": 0', b'"amount_minor": ' + token)
        )
        resp = _post_raw(api_ctx["client"], "/transfers", raw)
        assert resp.status_code == 422, (token, resp.status_code, resp.text[:200])

    def test_oversized_body_is_rejected(self, api_ctx):
        body = _transfer_body(user_ref="A" * 5_000_000)
        resp = _post_json(api_ctx["client"], "/transfers", body)
        assert resp.status_code == 422

    @pytest.mark.parametrize(
        "content_type",
        [
            "text/plain",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            "application/xml",
        ],
    )
    def test_wrong_content_type_never_crashes(self, api_ctx, content_type):
        resp = api_ctx["client"].post(
            "/transfers",
            content=json.dumps(_transfer_body()).encode(),
            headers={"content-type": content_type},
        )
        assert resp.status_code in (200, 422), resp.status_code

    @pytest.mark.parametrize("field", sorted(_transfer_body()))
    def test_missing_required_field(self, api_ctx, field):
        body = _transfer_body()
        body.pop(field)
        resp = _post_json(api_ctx["client"], "/transfers", body)
        if field == "currency":  # defaults to INR
            assert resp.status_code == 200
        else:
            assert resp.status_code == 422

    @pytest.mark.parametrize(
        "extra", ["risk_score", "risk_id", "decision", "is_admin", "__proto__"]
    )
    def test_extra_fields_are_forbidden(self, api_ctx, extra):
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(**{extra: 0}))
        assert resp.status_code == 422

    @pytest.mark.parametrize("field", sorted(_transfer_body()))
    @pytest.mark.parametrize("value", [None, [], {}, 3.14])
    def test_wrong_types(self, api_ctx, field, value):
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(**{field: value}))
        assert resp.status_code == 422, (field, value, resp.text[:200])


class TestTransfersAmountBoundaries:
    @pytest.mark.parametrize(
        ("amount", "status"),
        [
            (0, 422),
            (1, 200),
            (10**15, 200),
            (10**15 + 1, 422),
            (-1, 422),
            (-(10**15), 422),
            (10**400, 422),
            (1.5, 422),
            (0.01, 422),
            (-0.0, 422),
            (1e16, 422),
            ("abc", 422),
            ("", 422),
            ("1.5", 422),
            ("0x10", 422),
        ],
    )
    def test_amount_boundaries(self, api_ctx, amount, status):
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(amount_minor=amount))
        assert resp.status_code == status, (amount, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize("amount", [True, False])
    def test_boolean_is_not_a_money_amount(self, api_ctx, amount):
        # `true` silently becoming 1 paisa is a type confusion on the one field
        # the source comment says must be an exact integer amount.
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(amount_minor=amount))
        assert resp.status_code == 422, (amount, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize("currency", ["inr", "IN", "INRR", "", " INR", "IN\u0130", "123", None])
    def test_currency_must_be_three_upper_letters(self, api_ctx, currency):
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(currency=currency))
        assert resp.status_code == 422, currency


class TestTransfersStringFields:
    INJECTIONS: ClassVar[list[str]] = [
        "\u202eevil",
        "zero\u200bwidth",
        "nul\x00byte",
        "line\r\nINFO forged audit line",
        "line\nbreak",
        "next\u0085line",
        "sep\u2028arator",
        "tab\there",
        "\ufeffbom",
        "caf\u00e9",
        "   ",
        "",
    ]

    @pytest.mark.parametrize("field", ["user_ref", "bank_id", "receiver_account", "transfer_id"])
    @pytest.mark.parametrize("value", INJECTIONS)
    def test_log_injection_and_unicode_rejected(self, api_ctx, field, value):
        if field == "transfer_id" and value.strip():
            value = value + "-padding-to-8"  # past min_length, so the charset is tested
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(**{field: value}))
        if field == "receiver_account" and value == "tab\there":
            # Internal whitespace is folded into "TABHERE" by normalize_account:
            # documented display-formatting folding, not an injection.
            assert resp.status_code in (200, 422)
        else:
            assert resp.status_code == 422, (field, repr(value), resp.text[:200])
        assert resp.status_code < 500

    @pytest.mark.parametrize(
        ("field", "max_len"),
        [("user_ref", 64), ("bank_id", 32), ("receiver_account", 64), ("transfer_id", 64)],
    )
    def test_very_long_strings(self, api_ctx, field, max_len):
        for n in (max_len + 1, 10_000, 1_000_000):
            resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(**{field: "A" * n}))
            assert resp.status_code == 422, (field, n)

    @pytest.mark.parametrize(
        "account",
        ["ab", "abc", "ACC.999", "ACC_999", "ACC/999", "A" * 35, "ACC#1", "!!!!", "ACC+999"],
    )
    def test_malformed_account_is_a_client_error_not_a_crash(self, api_ctx, account):
        # These pass TransferRequest's printable-ASCII check but fail
        # normalize_account's [A-Z0-9-]{4,34}; bankbot.py's own comment says
        # this "is the caller's error and must surface as one".
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(receiver_account=account))
        assert resp.status_code == 422, (account, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize("transfer_id", ["short", "1234567", ""])
    def test_transfer_id_too_short(self, api_ctx, transfer_id):
        resp = _post_json(api_ctx["client"], "/transfers", _transfer_body(transfer_id=transfer_id))
        assert resp.status_code == 422


class TestTransfersReceiverCanonicalisation:
    """The same receiver must reach the same decision however it is spelled --
    a spelling that dodged the flag would be a trivial bypass."""

    @pytest.mark.parametrize("ifsc", [IFSC, IFSC.lower(), "HdFc0001234", "hDFC0001234"])
    @pytest.mark.parametrize(
        "account", [FLAGGED_ACCOUNT, "acc-999", " ACC-999", "ACC-999 ", "aCc-999"]
    )
    def test_case_and_padding_variants_of_flagged_receiver_block(self, api_ctx, ifsc, account):
        resp = _post_json(
            api_ctx["client"],
            "/transfers",
            _transfer_body(receiver_ifsc=ifsc, receiver_account=account),
        )
        assert resp.status_code == 200, resp.text[:200]
        assert resp.json()["decision"] == "block", (ifsc, account)
        _assert_no_customer_leak(resp, api_ctx["risk_id"], "")

    @pytest.mark.parametrize(
        "ifsc",
        [" " + IFSC, IFSC + " ", "\t" + IFSC, " hdfc0001234", "HDFC 0001234", IFSC + "\n"],
    )
    def test_whitespace_variants_of_flagged_ifsc_never_allow(self, api_ctx, ifsc):
        resp = _post_json(
            api_ctx["client"],
            "/transfers",
            _transfer_body(receiver_ifsc=ifsc, receiver_account=FLAGGED_ACCOUNT),
        )
        assert resp.status_code in (200, 422), resp.status_code
        if resp.status_code == 200:
            assert resp.json()["decision"] == "block", ifsc

    def test_showcase_flag_in_lowercase_blocks_uppercase_transfer(self, showcase_api_only):
        c = showcase_api_only["client"]
        flagged = c.post(
            "/demo/flag", json={"receiver_ifsc": IFSC.lower(), "receiver_account": "acc-999"}
        )
        assert flagged.status_code == 200
        for ifsc, account in [(IFSC, "ACC-999"), (IFSC.lower(), " acc-999 ")]:
            resp = c.post(
                "/api/transfers",
                json=_transfer_body(receiver_ifsc=ifsc, receiver_account=account),
            )
            assert resp.json()["decision"] == "block", (ifsc, account)


class TestTransfersFuzz:
    def test_generated_inputs_never_5xx_and_never_leak(self, api_ctx):
        rng = random.Random(0xA215)
        plausible = {
            "receiver_ifsc": _ifsc_variants(rng),
            "receiver_account": _account_variants(rng),
            "amount_minor": [0, 1, 10**15, 10**15 + 1, -1, 1.5, "100", 10**7, 10**7 + 1],
            "user_ref": ["anu", " anu ", "a" * 64, "a" * 65, "\u202eanu"],
            "bank_id": ["BANK-A", "", "x" * 33],
            "currency": ["INR", "inr", "USD", "", None],
            "transfer_id": ["abcdefgh", "abcdefg", "x" * 64, "x" * 65],
        }
        failures: list[str] = []
        statuses: dict[int, int] = {}
        for i in range(FUZZ_CASES):
            body = _mutate(rng, _transfer_body(transfer_id=f"fuzz-{i:06d}"), plausible)
            raw = json.dumps(body).encode()
            resp = _post_raw(api_ctx["client"], "/transfers", raw)
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
            if resp.status_code >= 500 or resp.status_code not in (200, 422):
                failures.append(f"{resp.status_code} <- {raw[:160]!r}")
                continue
            try:
                _assert_no_customer_leak(resp, api_ctx["risk_id"], raw.decode("utf-8", "replace"))
            except AssertionError as exc:
                failures.append(f"LEAK {exc} <- {raw[:160]!r}")
        assert statuses.get(200, 0) > 10, statuses  # the fuzzer reaches the handler
        assert not failures, _fmt_failures(failures)


# --------------------------------------------------------------------------
# BankBot API: GET /audit/{ref}
# --------------------------------------------------------------------------


def _make_record(api_ctx: dict[str, Any], account: str = FLAGGED_ACCOUNT) -> str:
    resp = api_ctx["client"].post("/transfers", json=_transfer_body(receiver_account=account))
    assert resp.status_code == 200
    return resp.json()["audit_ref"]


class TestAuditEndpoint:
    def test_positive_control(self, api_ctx):
        ref = _make_record(api_ctx)
        resp = api_ctx["client"].get(f"/audit/{ref}", headers=INVESTIGATOR_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["risk_score"] == FLAGGED_SCORE

    def test_unconfigured_investigators_fail_closed(self):
        app = create_app(InMemoryRiskBus(PublisherKeyring()), settings=ApiSettings())
        resp = _client(app).get("/audit/anything", headers=INVESTIGATOR_HEADERS)
        assert resp.status_code == 503  # deliberate: no investigators means no access

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"X-Investigator-Id": INVESTIGATOR_ID},
            {"X-Investigator-Key": INVESTIGATOR_KEY},
            {"X-Investigator-Id": "", "X-Investigator-Key": "", "X-Investigator-Reason": "r"},
            {
                "X-Investigator-Id": INVESTIGATOR_ID,
                "X-Investigator-Key": "",
                "X-Investigator-Reason": "r",
            },
            {
                "X-Investigator-Id": "",
                "X-Investigator-Key": INVESTIGATOR_KEY,
                "X-Investigator-Reason": "r",
            },
            {
                "X-Investigator-Id": "REVIEWER",
                "X-Investigator-Key": INVESTIGATOR_KEY,
                "X-Investigator-Reason": "r",
            },
            {
                "X-Investigator-Id": INVESTIGATOR_ID,
                "X-Investigator-Key": INVESTIGATOR_KEY.upper(),
                "X-Investigator-Reason": "r",
            },
            {
                "X-Investigator-Id": INVESTIGATOR_ID,
                "X-Investigator-Key": INVESTIGATOR_KEY[:-1],
                "X-Investigator-Reason": "r",
            },
            {
                "X-Investigator-Id": INVESTIGATOR_ID,
                "X-Investigator-Key": INVESTIGATOR_KEY + "x",
                "X-Investigator-Reason": "r",
            },
            # Underscored names are not the same header.
            {
                "X_Investigator_Id": INVESTIGATOR_ID,
                "X_Investigator_Key": INVESTIGATOR_KEY,
                "X_Investigator_Reason": "r",
            },
            {"Authorization": f"Bearer {INVESTIGATOR_KEY}"},
        ],
    )
    def test_bad_credentials_are_401(self, api_ctx, headers):
        ref = _make_record(api_ctx)
        resp = api_ctx["client"].get(f"/audit/{ref}", headers=headers)
        assert resp.status_code == 401, (headers, resp.status_code)
        assert "risk_score" not in resp.text

    @pytest.mark.parametrize("reason", ["", "   ", "\t", "x" * 257])
    def test_missing_or_blank_reason_is_400(self, api_ctx, reason):
        ref = _make_record(api_ctx)
        headers = {**INVESTIGATOR_HEADERS, "X-Investigator-Reason": reason}
        resp = api_ctx["client"].get(f"/audit/{ref}", headers=headers)
        assert resp.status_code == 400, (reason, resp.status_code)

    def test_reason_header_absent_is_400(self, api_ctx):
        ref = _make_record(api_ctx)
        headers = {k: v for k, v in INVESTIGATOR_HEADERS.items() if "Reason" not in k}
        assert api_ctx["client"].get(f"/audit/{ref}", headers=headers).status_code == 400

    def test_header_names_are_case_insensitive(self, api_ctx):
        ref = _make_record(api_ctx)
        headers = {k.lower(): v for k, v in INVESTIGATOR_HEADERS.items()}
        assert api_ctx["client"].get(f"/audit/{ref}", headers=headers).status_code == 200
        headers = {k.upper(): v for k, v in INVESTIGATOR_HEADERS.items()}
        assert api_ctx["client"].get(f"/audit/{ref}", headers=headers).status_code == 200

    @pytest.mark.parametrize(
        "raw_key",
        ["k\u00e9y".encode("latin-1"), b"\xff" * 8, INVESTIGATOR_KEY.encode() + b"\xe9"],
    )
    def test_non_ascii_key_is_401_not_a_crash(self, api_ctx, raw_key):
        # hmac.compare_digest raises TypeError on non-ASCII str, and Starlette
        # decodes header bytes as latin-1 -- so any byte >= 0x80 reaches it.
        ref = _make_record(api_ctx)
        headers = {**INVESTIGATOR_HEADERS, "X-Investigator-Key": raw_key}
        resp = api_ctx["client"].get(f"/audit/{ref}", headers=headers)
        assert resp.status_code == 401, (raw_key, resp.status_code)

    @pytest.mark.parametrize("raw_id", ["r\u00e9viewer".encode("latin-1"), b"\xff"])
    def test_non_ascii_id_is_401(self, api_ctx, raw_id):
        headers = {**INVESTIGATOR_HEADERS, "X-Investigator-Id": raw_id}
        assert api_ctx["client"].get("/audit/x", headers=headers).status_code == 401

    @pytest.mark.parametrize(
        "raw_reason",
        [
            b"ok\r\nINFO forged",
            b"ok\nforged",
            b"ok\x00",
            "caf\u00e9".encode("latin-1"),
            "\u202e".encode(),
        ],
    )
    def test_reason_log_injection_rejected_over_raw_asgi(self, api_ctx, raw_reason):
        ref = _make_record(api_ctx)
        headers = [
            (b"x-investigator-id", INVESTIGATOR_ID.encode()),
            (b"x-investigator-key", INVESTIGATOR_KEY.encode()),
            (b"x-investigator-reason", raw_reason),
        ]
        status, body = _asgi(api_ctx["app"], "GET", f"/audit/{ref}", headers)
        assert status == 400, (raw_reason, status, body[:200])
        logged = [e.reason for e in api_ctx["audit"].access_log]
        assert all("\n" not in r and "\r" not in r for r in logged)

    def test_duplicated_headers_never_grant_a_mismatched_identity(self, api_ctx):
        ref = _make_record(api_ctx)
        cases = [
            [("X-Investigator-Id", "evil"), ("X-Investigator-Id", INVESTIGATOR_ID)],
            [("X-Investigator-Id", INVESTIGATOR_ID), ("X-Investigator-Id", "evil")],
        ]
        for ids in cases:
            headers = [
                *ids,
                ("X-Investigator-Key", INVESTIGATOR_KEY),
                ("X-Investigator-Reason", "dup"),
            ]
            resp = api_ctx["client"].get(f"/audit/{ref}", headers=headers)
            assert resp.status_code in (200, 401), resp.status_code
            if resp.status_code == 200:
                assert api_ctx["audit"].access_log[-1].investigator_id == INVESTIGATOR_ID
        two_keys = [
            ("X-Investigator-Id", INVESTIGATOR_ID),
            ("X-Investigator-Key", "wrong"),
            ("X-Investigator-Key", INVESTIGATOR_KEY),
            ("X-Investigator-Reason", "dup"),
        ]
        assert api_ctx["client"].get(f"/audit/{ref}", headers=two_keys).status_code in (200, 401)

    @pytest.mark.parametrize(
        "ref",
        ["..%2f..%2fetc%2fpasswd", "%00", "%2e%2e", "a" * 10_000, "%E2%80%AE", "%0d%0aX", "%20"],
    )
    def test_odd_refs_are_404_not_5xx(self, api_ctx, ref):
        resp = api_ctx["client"].get(f"/audit/{ref}", headers=INVESTIGATOR_HEADERS)
        assert resp.status_code == 404, (ref, resp.status_code)

    def test_every_read_is_logged_with_the_authenticated_id(self, api_ctx):
        ref = _make_record(api_ctx)
        api_ctx["client"].get(f"/audit/{ref}", headers=INVESTIGATOR_HEADERS)
        api_ctx["client"].get("/audit/missing", headers=INVESTIGATOR_HEADERS)
        log = api_ctx["audit"].access_log
        assert [(e.audit_ref, e.found) for e in log[-2:]] == [(ref, True), ("missing", False)]
        assert {e.investigator_id for e in log} == {INVESTIGATOR_ID}

    def test_generated_headers_never_5xx_and_never_leak_evidence(self, api_ctx):
        rng = random.Random(0xA0D1)
        ref = _make_record(api_ctx)
        latin1 = bytes(range(0x20, 0x7F)) + bytes(range(0xA0, 0x100))
        id_pool = [INVESTIGATOR_ID, "", " ", "REVIEWER", "reviewer\t", "admin"]
        key_pool = [INVESTIGATOR_KEY, "", " ", INVESTIGATOR_KEY[:-1], INVESTIGATOR_KEY.upper()]
        failures: list[str] = []
        for _ in range(FUZZ_CASES):
            headers: dict[str, bytes] = {}
            for name, pool in (
                ("X-Investigator-Id", id_pool),
                ("X-Investigator-Key", key_pool),
                ("X-Investigator-Reason", ["fuzz", "", "  "]),
            ):
                roll = rng.random()
                if roll < 0.15:
                    continue
                if roll < 0.6:
                    headers[name] = rng.choice(pool).encode()
                else:
                    headers[name] = bytes(
                        rng.choice(latin1) for _ in range(rng.choice(LENGTHS[:12]))
                    )
            valid = (
                headers.get("X-Investigator-Id", b"").strip() == INVESTIGATOR_ID.encode()
                and headers.get("X-Investigator-Key", b"").strip() == INVESTIGATOR_KEY.encode()
            )
            resp = api_ctx["client"].get(f"/audit/{ref}", headers=headers)
            if resp.status_code >= 500:
                failures.append(f"{resp.status_code} <- {headers!r}")
            elif resp.status_code == 200 and not valid:
                failures.append(f"EVIDENCE WITHOUT CREDENTIALS <- {headers!r}")
        assert not failures, _fmt_failures(failures)


class TestBankBotApiMisc:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/transfers"),
            ("PUT", "/transfers"),
            ("DELETE", "/transfers"),
            ("PATCH", "/transfers"),
            ("POST", "/audit/x"),
            ("DELETE", "/audit/x"),
            ("POST", "/health"),
        ],
    )
    def test_method_not_allowed(self, api_ctx, method, path):
        assert api_ctx["client"].request(method, path).status_code == 405

    def test_health_and_unknown_paths(self, api_ctx):
        c = api_ctx["client"]
        assert c.get("/health").json() == {"status": "ok"}
        for path in ["/", "/admin", "/transfers/x", "/%00", "/..%2f"]:
            assert c.get(path).status_code == 404, path


# --------------------------------------------------------------------------
# OPRF authority
# --------------------------------------------------------------------------


def _evaluate(ctx: dict[str, Any], body: object, headers: dict[str, Any] | list[Any]) -> Any:
    return ctx["client"].post(
        "/evaluate",
        content=json.dumps(body).encode(),
        headers=headers,
    )


GOOD_A = {"X-Bank-Id": "BANK-A", "X-Bank-Key": "secret-a", "content-type": "application/json"}


class TestOprfService:
    def test_positive_control_and_attribution(self, oprf_ctx):
        resp = _evaluate(oprf_ctx, {"blinded": "abc123"}, GOOD_A)
        assert resp.status_code == 200
        assert [r.bank_id for r in oprf_ctx["authority"].audit_log] == ["BANK-A"]

    def test_public_key(self, oprf_ctx):
        body = oprf_ctx["client"].get("/public-key").json()
        assert int(body["n"], 16) == oprf_ctx["n"]

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"X-Bank-Id": "BANK-A"},
            {"X-Bank-Key": "secret-a"},
            {"X-Bank-Id": "", "X-Bank-Key": ""},
            {"X-Bank-Id": "BANK-A", "X-Bank-Key": ""},
            {"X-Bank-Id": "", "X-Bank-Key": "secret-a"},
            {"X-Bank-Id": "bank-a", "X-Bank-Key": "secret-a"},
            {"X-Bank-Id": "BANK-A", "X-Bank-Key": "SECRET-A"},
            {"X-Bank-Id": "BANK-A", "X-Bank-Key": "secret-c"},  # another bank's key
            {"X-Bank-Id": "BANK-C", "X-Bank-Key": "secret-a"},
            {"X-Bank-Id": "BANK-B", "X-Bank-Key": "secret-b"},  # not a member
            {"X-Bank-Id": "BANK-A", "X-Bank-Key": "secret-"},
            {"X_Bank_Id": "BANK-A", "X_Bank_Key": "secret-a"},
        ],
    )
    def test_unauthenticated_is_401_and_never_attributed(self, oprf_ctx, headers):
        resp = _evaluate(
            oprf_ctx, {"blinded": "abc123"}, {**headers, "content-type": "application/json"}
        )
        assert resp.status_code == 401, (headers, resp.status_code)
        assert oprf_ctx["authority"].audit_log == ()

    @pytest.mark.parametrize(
        "raw_key", ["secret-\u00e9".encode("latin-1"), b"\xff\xfe", b"secret-a\xa0"]
    )
    def test_non_ascii_bank_key_is_401_not_a_crash(self, oprf_ctx, raw_key):
        headers = {**GOOD_A, "X-Bank-Key": raw_key}
        resp = _evaluate(oprf_ctx, {"blinded": "abc123"}, headers)
        assert resp.status_code == 401, (raw_key, resp.status_code)
        assert oprf_ctx["authority"].audit_log == ()

    @pytest.mark.parametrize("raw_id", ["BANK-\u00c1".encode("latin-1"), b"\xff"])
    def test_non_ascii_bank_id_is_401(self, oprf_ctx, raw_id):
        resp = _evaluate(oprf_ctx, {"blinded": "abc123"}, {**GOOD_A, "X-Bank-Id": raw_id})
        assert resp.status_code == 401
        assert oprf_ctx["authority"].audit_log == ()

    @pytest.mark.parametrize(
        "raw_id", [b"BANK-A\r\nX-Bank-Id: BANK-C", b"BANK-A\x00", b" BANK-A", b"BANK-A "]
    )
    def test_control_chars_in_bank_id_over_raw_asgi(self, oprf_ctx, raw_id):
        headers = [
            (b"x-bank-id", raw_id),
            (b"x-bank-key", b"secret-a"),
            (b"content-type", b"application/json"),
        ]
        status, _ = _asgi(oprf_ctx["app"], "POST", "/evaluate", headers, b'{"blinded":"ab"}')
        assert status == 401, (raw_id, status)
        assert oprf_ctx["authority"].audit_log == ()

    def test_duplicated_bank_id_headers_attribute_to_the_key_holder(self, oprf_ctx):
        for ids in (["BANK-C", "BANK-A"], ["BANK-A", "BANK-C"]):
            before = len(oprf_ctx["authority"].audit_log)
            headers = [
                *[("X-Bank-Id", i) for i in ids],
                ("X-Bank-Key", "secret-a"),
                ("content-type", "application/json"),
            ]
            resp = _evaluate(oprf_ctx, {"blinded": "ab"}, headers)
            assert resp.status_code in (200, 401)
            new = oprf_ctx["authority"].audit_log[before:]
            if resp.status_code == 200:
                assert [r.bank_id for r in new] == ["BANK-A"]
            else:
                assert new == ()

    def test_failed_auth_does_not_spend_the_victims_rate_limit(self, authority_key):
        authority = OprfAuthority(private_key=authority_key, rate_limit=3)
        c = _client(create_oprf_app(authority, BANK_KEYS))
        for _ in range(20):
            c.post(
                "/evaluate",
                json={"blinded": "ab"},
                headers={"X-Bank-Id": "BANK-A", "X-Bank-Key": "wrong"},
            )
        for _ in range(3):
            assert c.post("/evaluate", json={"blinded": "ab"}, headers=GOOD_A).status_code == 200
        assert c.post("/evaluate", json={"blinded": "ab"}, headers=GOOD_A).status_code == 429
        # BANK-C's budget is untouched by BANK-A's exhaustion.
        headers_c = {"X-Bank-Id": "BANK-C", "X-Bank-Key": "secret-c"}
        assert c.post("/evaluate", json={"blinded": "ab"}, headers=headers_c).status_code == 200

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"blinded": ""}, 422),
            ({"blinded": "AB"}, 422),
            ({"blinded": "0x1f"}, 422),
            ({"blinded": "-1"}, 422),
            ({"blinded": " ab"}, 422),
            ({"blinded": "ab\n"}, 422),
            ({"blinded": "\u0661\u0662"}, 422),  # int() would accept these digits
            ({"blinded": "\uff11"}, 422),  # fullwidth one
            ({"blinded": "a_b"}, 422),
            ({"blinded": "f" * 1025}, 422),
            ({"blinded": "f" * 1024}, 400),  # >= n
            ({"blinded": 171}, 422),
            ({"blinded": None}, 422),
            ({"blinded": ["ab"]}, 422),
            ({}, 422),
            ({"blinded": "ab", "bank_id": "BANK-C"}, 422),
            ([], 422),
            ("ab", 422),
        ],
    )
    def test_body_validation(self, oprf_ctx, body, status):
        resp = _evaluate(oprf_ctx, body, GOOD_A)
        assert resp.status_code == status, (body, resp.status_code, resp.text[:200])

    def test_value_equal_to_modulus_is_rejected(self, oprf_ctx):
        n = oprf_ctx["n"]
        for value in (n, n + 1):
            assert _evaluate(oprf_ctx, {"blinded": format(value, "x")}, GOOD_A).status_code == 400
        assert _evaluate(oprf_ctx, {"blinded": format(n - 1, "x")}, GOOD_A).status_code == 200

    @pytest.mark.parametrize("raw", [b"", b"{", b"null", b"\xff", b'{"blinded": NaN}', b"[" * 3000])
    def test_malformed_raw_bodies(self, oprf_ctx, raw):
        resp = oprf_ctx["client"].post("/evaluate", content=raw, headers=GOOD_A)
        assert resp.status_code in (400, 422), (raw[:20], resp.status_code)

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/evaluate"),
            ("PUT", "/evaluate"),
            ("POST", "/public-key"),
            ("DELETE", "/health"),
        ],
    )
    def test_method_not_allowed(self, oprf_ctx, method, path):
        assert oprf_ctx["client"].request(method, path).status_code == 405

    def test_generated_inputs_never_5xx_and_attribution_holds(self, oprf_ctx):
        rng = random.Random(0x0F2F)
        authority = oprf_ctx["authority"]
        latin1 = bytes(range(0x20, 0x7F)) + bytes(range(0xA0, 0x100))
        id_pool = ["BANK-A", "BANK-C", "BANK-B", "bank-a", "", "BANK-A ", "x" * 300]
        key_pool = ["secret-a", "secret-c", "secret-b", "", "SECRET-A", "secret-a "]
        hex_pool = ["ab", "0", "1", "f" * 256, "f" * 255, "0" * 1024, "dead" * 10, "DEAD", "g1"]
        failures: list[str] = []
        ok = 0
        for _ in range(FUZZ_CASES):
            headers: dict[str, bytes] = {"content-type": b"application/json"}
            if rng.random() < 0.35:  # a real member's matching pair, to reach the handler
                bank = rng.choice(sorted(BANK_KEYS))
                headers["X-Bank-Id"], headers["X-Bank-Key"] = (
                    bank.encode(),
                    BANK_KEYS[bank].encode(),
                )
            for name, pool in (("X-Bank-Id", id_pool), ("X-Bank-Key", key_pool)):
                if name in headers and rng.random() < 0.8:
                    continue
                roll = rng.random()
                if roll < 0.1:
                    continue
                if roll < 0.75:
                    headers[name] = rng.choice(pool).encode()
                else:
                    headers[name] = bytes(
                        rng.choice(latin1) for _ in range(rng.choice(LENGTHS[:12]))
                    )
            body = _mutate(rng, {"blinded": rng.choice(hex_pool)}, {"blinded": hex_pool})
            before = len(authority.audit_log)
            resp = oprf_ctx["client"].post(
                "/evaluate", content=json.dumps(body).encode(), headers=headers
            )
            new = authority.audit_log[before:]
            presented_id = headers.get("X-Bank-Id", b"").decode("latin-1")
            presented_key = headers.get("X-Bank-Key", b"").decode("latin-1")
            authenticated = BANK_KEYS.get(presented_id) == presented_key
            if resp.status_code >= 500:
                failures.append(f"{resp.status_code} <- {headers!r} {body!r:.120}")
            if resp.status_code == 200:
                ok += 1
                if not authenticated:
                    failures.append(f"ACCEPTED UNAUTHENTICATED <- {headers!r}")
            if new and (not authenticated or [r.bank_id for r in new] != [presented_id]):
                failures.append(f"MISATTRIBUTED {[r.bank_id for r in new]} <- {headers!r}")
        assert ok > 5
        assert not failures, _fmt_failures(failures)


# --------------------------------------------------------------------------
# Showcase server: /demo/*
# --------------------------------------------------------------------------

ACCOUNT_BODY = {"receiver_ifsc": IFSC, "receiver_account": FLAGGED_ACCOUNT}


class TestDemoEndpoints:
    @pytest.mark.parametrize("path", ["/demo/flag", "/demo/forge", "/demo/risk-id"])
    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"receiver_ifsc": IFSC},
            {"receiver_account": FLAGGED_ACCOUNT},
            {**ACCOUNT_BODY, "extra": 1},
            {**ACCOUNT_BODY, "receiver_ifsc": "HDFC000123"},
            {**ACCOUNT_BODY, "receiver_ifsc": "HDFC 001234"},
            {**ACCOUNT_BODY, "receiver_ifsc": "\u202eHDFC000123"},
            {**ACCOUNT_BODY, "receiver_account": "ab"},
            {**ACCOUNT_BODY, "receiver_account": "ACC.999"},
            {**ACCOUNT_BODY, "receiver_account": "ACC-\u0130"},
            {**ACCOUNT_BODY, "receiver_account": "\x00\x00\x00\x00"},
            {**ACCOUNT_BODY, "receiver_account": "A" * 65},
            {**ACCOUNT_BODY, "receiver_account": 12345},
            {**ACCOUNT_BODY, "receiver_ifsc": None},
            [],
            "x",
        ],
    )
    def test_bad_account_bodies_are_422(self, showcase_api_only, path, body):
        resp = _post_json(showcase_api_only["client"], path, body)
        assert resp.status_code == 422, (path, body, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize(
        ("overrides", "status"),
        [
            ({"risk_score": -1}, 422),
            ({"risk_score": 101}, 422),
            ({"risk_score": 0}, 200),
            ({"risk_score": 100}, 200),
            ({"risk_score": 50.5}, 422),
            ({"risk_score": "92"}, 422),  # strict: a numeric string is not a score
            ({"confidence": -0.01}, 422),
            ({"confidence": 1.01}, 422),
            ({"confidence": 0.0}, 200),
            ({"confidence": 1.0}, 200),
            ({"confidence": 1e308}, 422),
            ({"confidence": "high"}, 422),
        ],
    )
    def test_flag_score_and_confidence_bounds(self, showcase_api_only, overrides, status):
        resp = _post_json(showcase_api_only["client"], "/demo/flag", {**ACCOUNT_BODY, **overrides})
        assert resp.status_code == status, (overrides, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
    @pytest.mark.parametrize("field", [b"confidence", b"risk_score"])
    def test_flag_non_standard_json_numbers(self, showcase_api_only, token, field):
        raw = (
            b'{"receiver_ifsc":"HDFC0001234","receiver_account":"ACC-999","'
            + field
            + b'":'
            + token
            + b"}"
        )
        resp = _post_raw(showcase_api_only["client"], "/demo/flag", raw)
        assert resp.status_code == 422, (field, token, resp.status_code, resp.text[:200])

    def test_risk_id_response_never_echoes_the_account(self, showcase_api_only):
        resp = showcase_api_only["client"].post(
            "/demo/risk-id", json={"receiver_ifsc": IFSC, "receiver_account": "ZQ55512345"}
        )
        assert resp.status_code == 200
        assert "ZQ55512345" not in resp.text

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/demo/flag"),
            ("PUT", "/demo/flag"),
            ("GET", "/demo/forge"),
            ("GET", "/demo/reset"),
            ("DELETE", "/demo/reset"),
            ("GET", "/demo/risk-id"),
            ("POST", "/demo/feed"),
            ("POST", "/demo/health"),
            ("GET", "/api/transfers"),
            ("POST", "/api/health"),
        ],
    )
    def test_method_not_allowed_without_spa(self, showcase_api_only, method, path):
        assert showcase_api_only["client"].request(method, path).status_code == 405

    def test_reset_ignores_garbage_bodies(self, showcase_api_only):
        c = showcase_api_only["client"]
        for raw in (b"", b"{", b"\xff", b"[]" * 1000):
            assert _post_raw(c, "/demo/reset", raw).status_code == 200

    def test_feed_never_contains_an_account_number(self, showcase_api_only):
        c = showcase_api_only["client"]
        rng = random.Random(0xFEED)
        accounts = [
            "ZQ" + "".join(rng.choice(string.digits) for _ in range(rng.randint(6, 20)))
            for _ in range(40)
        ]
        c.post("/demo/flag", json={"receiver_ifsc": IFSC, "receiver_account": accounts[0]})
        for i, account in enumerate(accounts):
            raw = account.lower() if i % 3 == 0 else account
            spelled = f" {raw} " if i % 2 else raw
            resp = c.post(
                "/api/transfers",
                json=_transfer_body(user_ref=f"user-{account}", receiver_account=spelled),
            )
            assert resp.status_code == 200
        feed = c.get("/demo/feed")
        assert feed.status_code == 200
        text = feed.text
        assert len(feed.json()["decisions"]) == 25
        for account in accounts:
            assert account not in text and account.lower() not in text, account
        assert "user-" not in text  # nor the customer reference
        assert IFSC not in text

    def test_generated_demo_inputs_never_5xx(self, showcase_api_only):
        rng = random.Random(0xDE30)
        c = showcase_api_only["client"]
        plausible = {
            "receiver_ifsc": _ifsc_variants(rng),
            "receiver_account": _account_variants(rng),
            "risk_score": [-1, 0, 100, 101, 92, "92", 1.5],
            "confidence": [-0.1, 0.0, 1.0, 1.1, 0.5, "0.5"],
        }
        base = {**ACCOUNT_BODY, "risk_score": 92, "confidence": 0.94}
        failures: list[str] = []
        statuses: dict[int, int] = {}
        for _ in range(FUZZ_CASES):
            path = rng.choice(["/demo/flag", "/demo/forge", "/demo/risk-id"])
            seed = base if path == "/demo/flag" else ACCOUNT_BODY
            body = _mutate(rng, dict(seed), plausible)
            raw = json.dumps(body).encode()
            resp = _post_raw(c, path, raw)
            statuses[resp.status_code] = statuses.get(resp.status_code, 0) + 1
            if resp.status_code not in (200, 422):
                failures.append(f"{resp.status_code} {path} <- {raw[:160]!r}")
        feed = c.get("/demo/feed")
        assert feed.status_code == 200
        assert statuses.get(200, 0) > 10, statuses
        assert not failures, _fmt_failures(failures)

    def test_api_mount_forwards_auth_and_hides_evidence(self, showcase_api_only):
        c = showcase_api_only["client"]
        c.post("/demo/flag", json=ACCOUNT_BODY)
        resp = c.post("/api/transfers", json=_transfer_body(receiver_account=FLAGGED_ACCOUNT))
        assert resp.json()["decision"] == "block"
        risk_id = c.post("/demo/risk-id", json=ACCOUNT_BODY).json()["risk_id"]
        _assert_no_customer_leak(resp, risk_id, "")
        ref = resp.json()["audit_ref"]
        assert c.get(f"/api/audit/{ref}").status_code == 401
        bad = {**INVESTIGATOR_HEADERS, "X-Investigator-Key": "wrong"}
        assert c.get(f"/api/audit/{ref}", headers=bad).status_code == 401
        assert c.get(f"/api/audit/{ref}", headers=INVESTIGATOR_HEADERS).status_code == 200


# --------------------------------------------------------------------------
# Showcase server: static SPA serving
# --------------------------------------------------------------------------


class TestSpaStaticServing:
    def _assert_safe(self, status: int, body: bytes, label: str) -> None:
        assert status < 500, f"{label}: {status}"
        assert SECRET_CANARY.encode() not in body, f"{label}: served a file outside dist"

    def test_positive_controls(self, showcase):
        c = showcase["client"]
        assert c.get("/").text == INDEX_BODY
        assert c.get("/some/client/route").text == INDEX_BODY
        assert c.get("/favicon.ico").text == "FAVICON"
        assert c.get("/assets/app.js").text == "console.log('app')"
        assert c.get("/data/metrics.json").json() == {"ok": True}
        assert c.get("/demo/health").json()["status"] == "ok"

    def test_symlinks_out_of_dist_are_not_followed(self, showcase):
        c = showcase["client"]
        for path in ["/leak.txt", "/leakdir/secret.txt", "/assets/leak.js", "/data/leak.json"]:
            resp = c.get(path)
            self._assert_safe(resp.status_code, resp.content, path)

    def test_encoded_traversal_via_http_client(self, showcase, dist):
        c = showcase["client"]
        secret = str(dist["secret"])
        paths = [
            "/..%2foutside%2fsecret.txt",
            "/..%2F..%2F..%2F..%2F..%2Fetc%2Fpasswd",
            "/%2e%2e/outside/secret.txt",
            "/%2e%2e%2foutside%2fsecret.txt",
            "/%252e%252e/outside/secret.txt",
            "/..%5coutside%5csecret.txt",
            "/..%2fdist-evil%2fsecret.txt",
            "/assets/..%2f..%2foutside%2fsecret.txt",
            "/assets/%2e%2e/%2e%2e/outside/secret.txt",
            "/data/..%2f..%2foutside%2fsecret.txt",
            "/%2f" + secret.lstrip("/"),
            "//" + secret.lstrip("/"),
            "/%2F%2F" + secret.lstrip("/").replace("/", "%2F"),
            "/" + secret.replace("/", "%2f"),
            "/.%2e/outside/secret.txt",
            "/%c0%ae%c0%ae/outside/secret.txt",
        ]
        for path in paths:
            resp = c.get(path)
            self._assert_safe(resp.status_code, resp.content, path)

    def test_unnormalised_traversal_via_raw_asgi(self, showcase, dist):
        secret = str(dist["secret"])
        rel = "../outside/secret.txt"
        paths = [
            "/" + rel,
            "/../../" + rel,
            "/assets/../../outside/secret.txt",
            "/data/../../outside/secret.txt",
            "/./../outside/secret.txt",
            "/..\\outside\\secret.txt",
            "\\..\\outside\\secret.txt",
            "/../dist-evil/secret.txt",
            "/" + secret,
            "//" + secret,
            "/" + secret.lstrip("/"),
            "/....//outside/secret.txt",
            "/leakdir/../leakdir/secret.txt",
            "/~/secret.txt",
            "/\u202e/../outside/secret.txt",
        ]
        for path in paths:
            status, body = _asgi(showcase["app"], "GET", path)
            self._assert_safe(status, body, path)

    @pytest.mark.parametrize(
        "path", ["/\x00", "/a\x00b", "/favicon.ico\x00.png", "/../outside/secret.txt\x00"]
    )
    def test_nul_byte_in_path_is_not_a_crash(self, showcase, path):
        status, body = _asgi(showcase["app"], "GET", path)
        self._assert_safe(status, body, repr(path))

    @pytest.mark.parametrize("path", ["/" + "a" * 256, "/assets/" + "b" * 300, "/x/" + "c" * 1000])
    def test_overlong_path_segment_is_not_a_crash(self, showcase, path):
        # A component past NAME_MAX (255) makes Path.is_file()/resolve() raise
        # OSError(ENAMETOOLONG) rather than return False.
        resp = showcase["client"].get(path)
        assert resp.status_code < 500, (len(path), resp.status_code)

    def test_nul_byte_percent_encoded_via_http_client(self, showcase):
        resp = showcase["client"].get("/%00")
        self._assert_safe(resp.status_code, resp.content, "/%00")

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("POST", "/"),
            ("PUT", "/anything"),
            ("DELETE", "/favicon.ico"),
            ("POST", "/assets/app.js"),
        ],
    )
    def test_non_get_methods_on_static(self, showcase, method, path):
        status = showcase["client"].request(method, path).status_code
        assert status in (404, 405), (method, path, status)

    def test_generated_paths_never_escape_dist(self, showcase, dist):
        rng = random.Random(0x5BA)
        secret = str(dist["secret"])
        segments = [
            "..",
            ".",
            "",
            "...",
            "%2e%2e",
            "%2E%2E",
            "..%2f",
            "%2f",
            "\\",
            "..\\..",
            "outside",
            "secret.txt",
            "assets",
            "data",
            "index.html",
            "favicon.ico",
            "leak.txt",
            "leakdir",
            "dist-evil",
            "~",
            "%5c",
            "%252e%252e",
            "....//",
            secret.lstrip("/"),
            "\u202e",
            "\u200b",
            "%c0%ae",
            "a" * 300,
        ]
        failures: list[str] = []
        for i in range(FUZZ_CASES):
            parts = [rng.choice(segments) for _ in range(rng.randint(1, 7))]
            sep = rng.choice(["/", "/", "//", "\\"])
            path = "/" + sep.join(parts)
            if i % 2:
                status, body = _asgi(showcase["app"], "GET", path)
            else:
                try:
                    resp = showcase["client"].get(path)
                except Exception:  # client-side URL rejection, not a server result
                    continue
                status, body = resp.status_code, resp.content
            if status >= 500 or SECRET_CANARY.encode() in body:
                failures.append(f"{status} <- {path!r}")
        assert not failures, _fmt_failures(failures)
