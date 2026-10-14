"""Multi-process workflow tests against LIVE infrastructure.

Builds on ``scripts/e2e_live_oprf.py`` (one happy path, three processes) by
driving the real entry points -- ``python -m aris.oprf_service``,
``python -m aris.api``, ``python -m aris.demo.server`` -- as separate OS
processes on dynamically chosen free ports, against the docker-compose Kafka
broker + Schema Registry, and covering what that script does not:

1. every API risk-id mode (``hmac``, ``oprf``, ``hsm``) end to end over HTTP
   with the publisher in this process deriving under the same scheme;
2. failure modes: the OPRF authority dying mid-run (fail closed to step-up,
   audited as ``unavailable``) and coming back on the same key; a wrong pinned
   fingerprint (and an unreachable authority) refusing to start; a second,
   cold-started API process finding a flag that predates it;
3. concurrency: 60 mixed transfers against one API process, and
   ``POST /demo/reset`` racing transfers on the showcase server;
4. the SS3.7 investigator path over HTTP.

Skips cleanly when Kafka / the Schema Registry are unreachable (the same
``requires_kafka`` gate as ``tests/test_kafka_bus.py``); the HSM workflow also
skips when SoftHSM2 is absent. Every subprocess is torn down on failure.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from aris.attestation import Publisher, PublisherKeyring, public_key_to_hex
from aris.bus import PublishOutcome
from aris.kafka_bus import KafkaRiskBus, load_kafka_config
from aris.oprf import public_key_fingerprint
from aris.oprf_service import HttpOprfAuthorityClient, load_or_create_private_key
from aris.risk_id import HmacRiskIdDeriver, OprfRiskIdDeriver, publish_id
from aris.schema import RiskSignal
from tests.conftest import TEST_SALT_HEX
from tests.test_kafka_bus import requires_kafka

pytestmark = requires_kafka

_REPO = Path(__file__).resolve().parent.parent
_BOOTSTRAP, _REGISTRY_URL = load_kafka_config()
_IFSC = "HDFC0001234"
_FORBIDDEN_PORTS = {8000, 8001, 8100, 8787}
_INVESTIGATOR = ("alice", "wf-secret-key")
_BANK_KEYS = {"BANK-A": uuid.uuid4().hex, "BANK-B": uuid.uuid4().hex}
_SMALL = 500_000  # INR 5,000.00 -- below the amount-only step-up threshold
_LARGE = 20_000_000  # above PolicyConfig.step_up_above_amount_minor


# --------------------------------------------------------------------------
# process plumbing
# --------------------------------------------------------------------------


def _free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port not in _FORBIDDEN_PORTS:
            return port


@dataclass
class _Proc:
    name: str
    popen: subprocess.Popen[bytes]
    log_path: Path
    url: str | None = None

    def log(self) -> str:
        try:
            return self.log_path.read_text(errors="replace")
        except OSError:
            return ""


@dataclass
class _Procs:
    """Owns every subprocess a test starts; ``close()`` reaps them all, so a
    failed assertion never leaves a server bound to a port."""

    workdir: Path
    items: list[_Proc] = field(default_factory=list)

    def start(self, name: str, args: list[str], env: dict[str, str]) -> _Proc:
        log_path = self.workdir / f"{name}-{uuid.uuid4().hex[:6]}.log"
        log = log_path.open("wb")
        try:
            popen = subprocess.Popen(
                [sys.executable, *args],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                cwd=str(_REPO),
            )
        finally:
            log.close()  # the child holds its own descriptor
        proc = _Proc(name, popen, log_path)
        self.items.append(proc)
        return proc

    @staticmethod
    def stop(proc: _Proc, *, hard: bool = False) -> None:
        if proc.popen.poll() is not None:
            return
        proc.popen.send_signal(signal.SIGKILL if hard else signal.SIGTERM)
        try:
            proc.popen.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.popen.kill()
            proc.popen.wait(timeout=5)

    def close(self) -> None:
        for proc in self.items:
            if proc.popen.poll() is None:
                proc.popen.terminate()
        for proc in self.items:
            try:
                proc.popen.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.popen.kill()
                proc.popen.wait(timeout=5)


def _wait_healthy(proc: _Proc, url: str, path: str = "/health", timeout_s: float = 45.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.popen.poll() is not None:
            raise AssertionError(
                f"{proc.name} exited early (rc={proc.popen.returncode}):\n{proc.log()}"
            )
        try:
            if httpx.get(f"{url}{path}", timeout=1.0).status_code == 200:
                proc.url = url
                return url
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise AssertionError(f"{proc.name} never became healthy:\n{proc.log()}")


def _wait_for_log(proc: _Proc, pattern: str, timeout_s: float = 20.0) -> re.Match[str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if m := re.search(pattern, proc.log()):
            return m
        if proc.popen.poll() is not None:
            break
        time.sleep(0.1)
    raise AssertionError(f"{proc.name}: {pattern!r} never appeared in log:\n{proc.log()}")


def _base_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ARIS_", "SOFTHSM2_"))}
    env.update(
        ARIS_SALT=TEST_SALT_HEX,
        ARIS_KAFKA_BOOTSTRAP_SERVERS=_BOOTSTRAP,
        ARIS_SCHEMA_REGISTRY_URL=_REGISTRY_URL,
        PYTHONUNBUFFERED="1",
    )
    return env


def _api_env(bank_b: Publisher, **extra: str) -> dict[str, str]:
    env = _base_env()
    env.update(
        ARIS_API_BUS_BACKEND="kafka",
        ARIS_API_BANK_ID="BANK-A",
        ARIS_API_TRUSTED_PUBLISHERS=f"BANK-B:{public_key_to_hex(bank_b.public_key)}",
        ARIS_API_INVESTIGATORS=f"{_INVESTIGATOR[0]}:{_INVESTIGATOR[1]}",
    )
    env.update(extra)
    return env


def _start_api(procs: _Procs, env: dict[str, str], name: str = "api") -> _Proc:
    port = _free_port()
    proc = procs.start(name, ["-m", "aris.api", "--host", "127.0.0.1", "--port", str(port)], env)
    _wait_healthy(proc, f"http://127.0.0.1:{port}")
    return proc


@dataclass
class _Authority:
    proc: _Proc
    url: str
    port: int
    key_file: Path
    fingerprint: str


def _start_authority(
    procs: _Procs, key_file: Path, port: int | None = None, name: str = "oprf"
) -> _Authority:
    port = port or _free_port()
    env = _base_env()
    env.update(
        ARIS_OPRF_KEY_FILE=str(key_file),
        ARIS_OPRF_BANKS=",".join(f"{b}:{k}" for b, k in _BANK_KEYS.items()),
        ARIS_OPRF_RATE_LIMIT="100000",
    )
    # No --generate-key: the key file is provisioned up front, so a restart is
    # guaranteed to come back under the same key or fail loudly.
    proc = procs.start(name, ["-m", "aris.oprf_service", "--port", str(port)], env)
    printed = _wait_for_log(proc, r"fingerprint: ([0-9a-f]{64})").group(1)
    url = _wait_healthy(proc, f"http://127.0.0.1:{port}")
    return _Authority(proc, url, port, key_file, printed)


def _provision_authority_key(path: Path) -> str:
    key = load_or_create_private_key(path, create=True)
    numbers = key.public_key().public_numbers()
    assert key.private_bytes(  # sanity: a real PEM landed on disk
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).startswith(b"-----BEGIN PRIVATE KEY-----")
    return public_key_fingerprint(numbers.n, numbers.e)


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------


def _account() -> str:
    return f"ACC-{uuid.uuid4().hex[:12]}"


def _transfer(
    url: str,
    account: str,
    *,
    amount: int = _SMALL,
    transfer_id: str | None = None,
    client: httpx.Client | None = None,
    path: str = "/transfers",
) -> httpx.Response:
    body: dict[str, object] = {
        "user_ref": "anu",
        "bank_id": "BANK-A",
        "receiver_ifsc": _IFSC,
        "receiver_account": account,
        "amount_minor": amount,
    }
    if transfer_id is not None:
        body["transfer_id"] = transfer_id
    poster = client.post if client is not None else httpx.post
    return poster(f"{url}{path}", json=body, timeout=30)


def _decision(url: str, account: str, **kw: object) -> str:
    resp = _transfer(url, account, **kw)  # type: ignore[arg-type]
    assert resp.status_code == 200, resp.text
    return str(resp.json()["decision"])


def _investigator_headers(
    ident: str = _INVESTIGATOR[0], key: str = _INVESTIGATOR[1], reason: str | None = "wf test"
) -> dict[str, str]:
    headers = {"X-Investigator-Id": ident, "X-Investigator-Key": key}
    if reason is not None:
        headers["X-Investigator-Reason"] = reason
    return headers


def _audit(url: str, ref: str, client: httpx.Client | None = None) -> dict[str, object]:
    getter = client.get if client is not None else httpx.get
    resp = getter(f"{url}/audit/{ref}", headers=_investigator_headers(), timeout=10)
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


def _signal(risk_id: str, score: int = 92, confidence: float = 0.94) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=score,
        confidence=confidence,
        reason_codes=("new_beneficiary", "high_velocity"),
        model_version="v0.4-fl",
        source_bank_id="BANK-B",
        ttl_hours=24,
        timestamp=datetime.now(timezone.utc),
    )


def _hmac_id(account: str) -> str:
    return publish_id(HmacRiskIdDeriver(), _IFSC, account, datetime.now(timezone.utc))


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def module_procs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Procs]:
    procs = _Procs(tmp_path_factory.mktemp("wf-module"))
    try:
        yield procs
    finally:
        procs.close()


@pytest.fixture
def procs(tmp_path: Path) -> Iterator[_Procs]:
    p = _Procs(tmp_path)
    try:
        yield p
    finally:
        p.close()


@dataclass
class _BankB:
    publisher: Publisher
    bus: KafkaRiskBus

    def flag(self, risk_id: str, score: int = 92, confidence: float = 0.94) -> None:
        outcome = self.bus.publish(self.publisher.sign(_signal(risk_id, score, confidence)))
        assert outcome is PublishOutcome.ACCEPTED, outcome


@pytest.fixture(scope="module")
def bank_b_live() -> Iterator[_BankB]:
    """Bank B: this test process, publishing to the live Kafka bus."""
    publisher = Publisher.generate("BANK-B")
    keyring = PublisherKeyring()
    keyring.register("BANK-B", publisher.public_key)
    bus = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
    try:
        yield _BankB(publisher, bus)
    finally:
        bus.close()


@pytest.fixture(scope="module")
def hmac_api(module_procs: _Procs, bank_b_live: _BankB) -> _Proc:
    """One long-lived Bank A API (default hmac mode) shared by the hmac,
    cold-start, concurrency and investigator workflows."""
    return _start_api(module_procs, _api_env(bank_b_live.publisher), name="api-hmac")


@pytest.fixture(scope="module")
def authority(module_procs: _Procs, tmp_path_factory: pytest.TempPathFactory) -> _Authority:
    key_file = tmp_path_factory.mktemp("wf-oprf") / "authority.pem"
    expected = _provision_authority_key(key_file)
    auth = _start_authority(module_procs, key_file, name="oprf-shared")
    assert auth.fingerprint == expected
    return auth


def _oprf_id(auth: _Authority, account: str, bank: str = "BANK-B") -> str:
    client = HttpOprfAuthorityClient(auth.url, bank, _BANK_KEYS[bank], auth.fingerprint)
    try:
        return publish_id(
            OprfRiskIdDeriver(client, bank), _IFSC, account, datetime.now(timezone.utc)
        )
    finally:
        client.close()


def _oprf_api_env(bank_b: Publisher, auth: _Authority, fingerprint: str | None = None):
    return _api_env(
        bank_b,
        ARIS_API_RISK_ID_MODE="oprf",
        ARIS_API_OPRF_URL=auth.url,
        ARIS_API_OPRF_BANK_KEY=_BANK_KEYS["BANK-A"],
        ARIS_API_OPRF_FINGERPRINT=fingerprint or auth.fingerprint,
    )


# --------------------------------------------------------------------------
# 1. every risk-id mode end to end over HTTP on Kafka
# --------------------------------------------------------------------------


class TestRiskIdModesEndToEnd:
    def test_hmac_mode_blocks_flagged_and_allows_clean(
        self, hmac_api: _Proc, bank_b_live: _BankB
    ) -> None:
        assert hmac_api.url is not None
        flagged, clean = _account(), _account()
        bank_b_live.flag(_hmac_id(flagged))

        blocked = _transfer(hmac_api.url, flagged)
        assert blocked.status_code == 200
        assert blocked.json()["decision"] == "block"
        # The customer-facing response never carries score / risk_id / source.
        assert set(blocked.json()) == {"decision", "user_message", "audit_ref", "step_up_required"}
        assert _decision(hmac_api.url, clean) == "allow"

        record = _audit(hmac_api.url, blocked.json()["audit_ref"])
        assert record["risk_id"] == _hmac_id(flagged)
        assert record["lookup_status"] == "found"
        assert record["contributing_banks"] == ["BANK-B"]

    def test_oprf_mode_blocks_flagged_and_allows_clean(
        self, procs: _Procs, authority: _Authority, bank_b_live: _BankB
    ) -> None:
        api = _start_api(procs, _oprf_api_env(bank_b_live.publisher, authority), "api-oprf")
        assert api.url is not None
        flagged, clean = _account(), _account()
        risk_id = _oprf_id(authority, flagged)
        # The OPRF id is not the HMAC id: a bank cannot compute it offline.
        assert risk_id != _hmac_id(flagged)
        bank_b_live.flag(risk_id)

        blocked = _transfer(api.url, flagged).json()
        assert blocked["decision"] == "block"
        assert _decision(api.url, clean) == "allow"
        assert _audit(api.url, blocked["audit_ref"])["risk_id"] == risk_id

        # An HMAC-scheme flag is invisible to an OPRF-mode bank: schemes must
        # match, which is exactly why the mode is one deployment-wide choice.
        hmac_only = _account()
        bank_b_live.flag(_hmac_id(hmac_only))
        assert _decision(api.url, hmac_only) == "allow"

    def test_hsm_mode_blocks_flagged_and_allows_clean(
        self, procs: _Procs, tmp_path: Path, bank_b_live: _BankB
    ) -> None:
        hsm = _provision_softhsm(tmp_path)
        flagged, clean = _account(), _account()
        risk_id = _hsm_publish_id(hsm, flagged)
        assert re.fullmatch(r"[0-9a-f]{64}", risk_id)
        assert risk_id != _hmac_id(flagged)  # a token-generated key, not ARIS_SALT
        bank_b_live.flag(risk_id)

        env = _api_env(bank_b_live.publisher, ARIS_API_RISK_ID_MODE="hsm", **hsm)
        api = _start_api(procs, env, "api-hsm")
        assert api.url is not None
        blocked = _transfer(api.url, flagged).json()
        assert blocked["decision"] == "block"
        assert _decision(api.url, clean) == "allow"
        assert _audit(api.url, blocked["audit_ref"])["risk_id"] == risk_id


_SOFTHSM_MODULES = (
    "/opt/homebrew/lib/softhsm/libsofthsm2.so",
    "/usr/local/lib/softhsm/libsofthsm2.so",
    "/usr/lib/softhsm/libsofthsm2.so",
    "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
)


def _provision_softhsm(tmp_path: Path) -> dict[str, str]:
    """A throwaway SoftHSM2 token (like tests/test_hsm.py) plus a consortium
    key generated inside it. Everything runs in subprocesses with their own
    SOFTHSM2_CONF -- the PKCS#11 library in *this* process may already be
    initialised against another test module's token directory."""
    pytest.importorskip("pkcs11", reason="pkcs11 not installed (optional [hsm] extra)")
    module = next((m for m in _SOFTHSM_MODULES if Path(m).exists()), None)
    if module is None:
        module = next(
            (str(p) for p in Path("/opt/homebrew/Cellar/softhsm").glob("*/lib/softhsm/*.so")),
            None,
        )
    if module is None or shutil.which("softhsm2-util") is None:
        pytest.skip("SoftHSM2 not installed")

    token_dir = tmp_path / "tokens"
    token_dir.mkdir()
    conf = tmp_path / "softhsm2.conf"
    conf.write_text(f"directories.tokendir = {token_dir}\nobjectstore.backend = file\n")
    cfg = {
        "SOFTHSM2_CONF": str(conf),
        "ARIS_HSM_MODULE": module,
        "ARIS_HSM_TOKEN_LABEL": "aris-wf-token",
        "ARIS_HSM_PIN": "1234",
    }
    subprocess.run(
        [
            "softhsm2-util",
            "--init-token",
            "--free",
            "--label",
            cfg["ARIS_HSM_TOKEN_LABEL"],
            "--pin",
            cfg["ARIS_HSM_PIN"],
            "--so-pin",
            "5678",
        ],
        env={**_base_env(), **cfg},
        check=True,
        capture_output=True,
    )
    _hsm_script(
        cfg,
        "from aris.hsm import open_hsm_session, generate_consortium_key\n"
        "with open_hsm_session() as s:\n"
        "    generate_consortium_key(s)\n",
    )
    return cfg


def _hsm_script(cfg: dict[str, str], code: str) -> str:
    out = subprocess.run(
        [sys.executable, "-c", code],
        env={**_base_env(), **cfg},
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_REPO),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _hsm_publish_id(cfg: dict[str, str], account: str) -> str:
    return _hsm_script(
        cfg,
        "from datetime import datetime, timezone\n"
        "from aris.hsm import open_hsm_session, get_consortium_key\n"
        "from aris.risk_id import HsmRiskIdDeriver, publish_id\n"
        "with open_hsm_session() as s:\n"
        "    d = HsmRiskIdDeriver(get_consortium_key(s))\n"
        f"    print(publish_id(d, {_IFSC!r}, {account!r}, datetime.now(timezone.utc)))\n",
    )


# --------------------------------------------------------------------------
# 2. failure-mode workflows
# --------------------------------------------------------------------------


class TestFailureModes:
    def test_authority_killed_mid_run_fails_closed_then_recovers_on_same_key(
        self, procs: _Procs, tmp_path: Path, bank_b_live: _BankB
    ) -> None:
        key_file = tmp_path / "authority.pem"
        expected_fp = _provision_authority_key(key_file)
        auth = _start_authority(procs, key_file, name="oprf-1")
        assert auth.fingerprint == expected_fp
        api = _start_api(procs, _oprf_api_env(bank_b_live.publisher, auth), "api-oprf-fail")
        assert api.url is not None

        flagged, clean = _account(), _account()
        risk_id = _oprf_id(auth, flagged)
        bank_b_live.flag(risk_id)
        assert _decision(api.url, flagged) == "block"
        assert _decision(api.url, clean) == "allow"

        # --- the authority dies (SIGKILL: no graceful shutdown) ---
        procs.stop(auth.proc, hard=True)
        for account in (flagged, clean, flagged):
            resp = _transfer(api.url, account)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            # Fail closed: cannot learn whether the receiver is flagged, so
            # never allow -- and the flagged account is never let through.
            assert body["decision"] == "step_up", body
            assert body["step_up_required"] is True
            record = _audit(api.url, body["audit_ref"])
            assert record["lookup_status"] == "unavailable"
            assert record["decision"] == "step_up"
            assert record["risk_score"] is None
        assert api.popen.poll() is None, "API must survive the authority outage"

        # --- restart on the SAME port and SAME key file ---
        auth2 = _start_authority(procs, key_file, port=auth.port, name="oprf-2")
        assert auth2.fingerprint == auth.fingerprint
        assert _oprf_id(auth2, flagged) == risk_id, "same key must give the same ids"

        # The API's pooled connection to the dead process may need one request
        # to notice; whatever it answers meanwhile must never be allow.
        decisions: list[str] = []
        for _ in range(10):
            decisions.append(_decision(api.url, flagged))
            if decisions[-1] == "block":
                break
            time.sleep(0.3)
        assert decisions[-1] == "block", decisions
        assert set(decisions) <= {"block", "step_up"}, decisions
        assert _decision(api.url, clean) == "allow"

    def test_api_with_wrong_pinned_fingerprint_refuses_to_start(
        self, procs: _Procs, authority: _Authority, bank_b_live: _BankB
    ) -> None:
        env = _oprf_api_env(bank_b_live.publisher, authority, fingerprint="ab" * 32)
        port = _free_port()
        api = procs.start(
            "api-wrong-fp", ["-m", "aris.api", "--host", "127.0.0.1", "--port", str(port)], env
        )
        rc = api.popen.wait(timeout=60)
        assert rc != 0
        assert "AuthorityKeyMismatch" in api.log(), api.log()
        with pytest.raises(httpx.HTTPError):
            httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0)

    def test_api_with_unreachable_authority_refuses_to_start(
        self, procs: _Procs, authority: _Authority, bank_b_live: _BankB
    ) -> None:
        dead = _Authority(
            authority.proc,
            f"http://127.0.0.1:{_free_port()}",
            0,
            authority.key_file,
            authority.fingerprint,
        )
        env = _oprf_api_env(bank_b_live.publisher, dead)
        api = procs.start(
            "api-no-authority",
            ["-m", "aris.api", "--host", "127.0.0.1", "--port", str(_free_port())],
            env,
        )
        assert api.popen.wait(timeout=60) != 0, api.log()

    def test_cold_started_second_api_blocks_a_flag_that_predates_it(
        self, procs: _Procs, hmac_api: _Proc, bank_b_live: _BankB
    ) -> None:
        assert hmac_api.url is not None
        flagged, clean = _account(), _account()
        bank_b_live.flag(_hmac_id(flagged))
        assert _decision(hmac_api.url, flagged) == "block"

        # A brand-new process: empty local view, every bucket cold. Its very
        # first lookup must catch the bucket up synchronously and block --
        # no retry loop, a cold bucket must never read as clean.
        cold = _start_api(procs, _api_env(bank_b_live.publisher), "api-cold")
        assert cold.url is not None
        first = _transfer(cold.url, flagged).json()
        assert first["decision"] == "block", first
        assert _audit(cold.url, first["audit_ref"])["lookup_status"] == "found"
        assert _decision(cold.url, clean) == "allow"

        # And a flag published *after* the cold API warmed that bucket reaches
        # it through the live background consumer.
        late = _account()
        while _hmac_id(late)[:2] != _hmac_id(flagged)[:2]:
            late = _account()  # same prefix bucket, which is already warm
        assert _decision(cold.url, late) == "allow"
        bank_b_live.flag(_hmac_id(late))
        deadline, seen = time.monotonic() + 15, "allow"
        while time.monotonic() < deadline and seen != "block":
            seen = _decision(cold.url, late)
            assert seen in {"allow", "block"}
            time.sleep(0.2)
        assert seen == "block"


# --------------------------------------------------------------------------
# 3. concurrency
# --------------------------------------------------------------------------


class TestConcurrency:
    def test_sixty_concurrent_mixed_transfers_against_one_api(
        self, hmac_api: _Proc, bank_b_live: _BankB
    ) -> None:
        url = hmac_api.url
        assert url is not None
        blocked_accts = [_account() for _ in range(5)]
        stepup_accts = [_account() for _ in range(3)]  # score 60 -> step_up
        clean_accts = [_account() for _ in range(5)]
        for a in blocked_accts:
            bank_b_live.flag(_hmac_id(a))
        for a in stepup_accts:
            bank_b_live.flag(_hmac_id(a), score=60)

        jobs: list[tuple[str, int, str]] = []  # (account, amount, expected)
        for i in range(60):
            kind = i % 4
            if kind == 0:
                jobs.append((blocked_accts[i % 5], _SMALL, "block"))
            elif kind == 1:
                jobs.append((clean_accts[i % 5], _SMALL, "allow"))
            elif kind == 2:
                jobs.append((stepup_accts[i % 3], _SMALL, "step_up"))
            else:
                jobs.append((clean_accts[i % 5], _LARGE, "step_up"))  # amount-only

        with httpx.Client(limits=httpx.Limits(max_connections=32)) as client:

            def run(job: tuple[str, int, str]) -> tuple[tuple[str, int, str], httpx.Response]:
                tid = f"wf-{uuid.uuid4().hex}"
                return job, _transfer(url, job[0], amount=job[1], transfer_id=tid, client=client)

            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(run, jobs))

            wrong = []
            for (account, amount, expected), resp in results:
                assert resp.status_code < 500, resp.text
                assert resp.status_code == 200, resp.text
                if resp.json()["decision"] != expected:
                    wrong.append((account, amount, expected, resp.json()))
            assert not wrong, wrong

            # The audit trail agrees with what every caller was told.
            refs = {resp.json()["audit_ref"] for _, resp in results}
            assert len(refs) == len(jobs)
            for (account, amount, expected), resp in results:
                record = _audit(url, resp.json()["audit_ref"], client)
                assert record["transfer_id"] == resp.json()["audit_ref"]
                assert record["receiver_account"] == account
                assert record["amount_minor"] == amount
                assert record["decision"] == expected
                assert record["risk_id"] == _hmac_id(account)
                is_flagged = account in blocked_accts or account in stepup_accts
                assert record["lookup_status"] == ("found" if is_flagged else "not_found")

            # Idempotency under load: replaying a transfer_id returns the
            # recorded decision.
            (account, amount, expected), resp = results[0]
            replay = _transfer(
                url, account, amount=amount, transfer_id=resp.json()["audit_ref"], client=client
            )
            assert replay.json() == resp.json()

    def test_demo_reset_racing_transfers_never_5xx_or_misdecides(self, procs: _Procs) -> None:
        port = _free_port()
        env = _base_env()
        env["ARIS_SHOWCASE_INVESTIGATOR_KEY"] = "wf-demo-key"
        demo = procs.start(
            "demo",
            ["-m", "aris.demo.server", "--port", str(port), "--dist", str(procs.workdir / "nd")],
            env,
        )
        url = _wait_healthy(demo, f"http://127.0.0.1:{port}", path="/demo/health")

        # reset_epoch is odd while a reset is in flight, bumped before and
        # after each one, so a worker can tell whether a reset could have
        # landed between its flag and its transfer.
        state = {"epoch": 0}
        state_lock = threading.Lock()
        stop = threading.Event()
        failures: list[str] = []
        counts = {"strict_block": 0, "transfers": 0, "resets": 0}

        def bump() -> None:
            with state_lock:
                state["epoch"] += 1

        def epoch() -> int:
            with state_lock:
                return state["epoch"]

        def tally(key: str) -> None:
            with state_lock:
                counts[key] += 1

        def call(
            client: httpx.Client, method: str, path: str, **kw: object
        ) -> httpx.Response | None:
            """One request; a 5xx or a dropped connection is a failure, not
            an exception that would hide every other result."""
            try:
                resp = client.request(method, f"{url}{path}", timeout=30, **kw)  # type: ignore[arg-type]
            except httpx.HTTPError as exc:
                failures.append(f"{method} {path}: {exc!r}")
                return None
            if resp.status_code != 200:
                failures.append(f"{method} {path}: {resp.status_code} {resp.text[:200]}")
                return None
            return resp

        def resetter(client: httpx.Client) -> None:
            while not stop.is_set():
                bump()
                call(client, "POST", "/demo/reset")
                bump()
                tally("resets")
                time.sleep(0.005)

        def feed_poller(client: httpx.Client) -> None:
            # The showcase frontend polls the feed while transfers land.
            while not stop.is_set():
                feed = call(client, "GET", "/demo/feed")
                if feed is not None and any(
                    e["decision"] not in {"block", "allow", "step_up"} or "receiver_account" in e
                    for e in feed.json()["decisions"]
                ):
                    failures.append(f"malformed feed entry: {feed.json()}")

        # Time-boxed rather than a fixed count: long enough for the reset /
        # feed / record interleavings to actually occur on a fast machine.
        deadline = time.monotonic() + 6.0

        def worker(client: httpx.Client) -> None:
            while time.monotonic() < deadline:
                flagged, clean = _account(), _account()
                e0 = epoch()
                body = {"receiver_ifsc": _IFSC, "receiver_account": flagged}
                if call(client, "POST", "/demo/flag", json=body) is None:
                    continue
                for account, allowed in ((flagged, {"block", "allow"}), (clean, {"allow"})):
                    tx = {
                        "user_ref": "anu",
                        "bank_id": "BANK-A",
                        "receiver_ifsc": _IFSC,
                        "receiver_account": account,
                        "amount_minor": _SMALL,
                    }
                    r = call(client, "POST", "/api/transfers", json=tx)
                    tally("transfers")
                    if r is None:
                        continue
                    d = r.json()["decision"]
                    if d not in allowed:
                        failures.append(f"{account}: {d} not in {allowed}")
                    if account == flagged and e0 % 2 == 0 and epoch() == e0:
                        # No reset could have run between flag and transfer:
                        # the world this transfer saw holds the flag.
                        tally("strict_block")
                        if d != "block":
                            failures.append(f"{account}: {d} with no reset in between")

        with httpx.Client(limits=httpx.Limits(max_connections=32)) as client:
            background = [
                threading.Thread(target=resetter, args=(client,)),
                *(threading.Thread(target=feed_poller, args=(client,)) for _ in range(4)),
            ]
            for t in background:
                t.start()
            try:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    for fut in [pool.submit(worker, client) for _ in range(8)]:
                        fut.result()
            finally:
                stop.set()
                for t in background:
                    t.join(timeout=30)

        server_errors = sorted(set(re.findall(r"^\w+(?:Error|Exception): .*$", demo.log(), re.M)))
        assert not failures, {"client": failures[:8], "server": server_errors[:5]}
        assert counts["transfers"] >= 200, counts
        assert counts["resets"] >= 5, counts
        assert demo.popen.poll() is None
        assert not server_errors, server_errors

    def test_malformed_receiver_account_is_a_client_error_not_a_5xx(self, hmac_api: _Proc) -> None:
        """BankBot._lookup_receiver deliberately lets a malformed account
        escape as ValueError ("the caller's error and must surface as one");
        over HTTP that must be a 4xx, never a 500 that reads as an outage."""
        assert hmac_api.url is not None
        for bad in ("X1", "A" * 40, "ACC_123456"):
            resp = _transfer(hmac_api.url, bad)
            assert 400 <= resp.status_code < 500, (bad, resp.status_code, resp.text[:200])
        assert _decision(hmac_api.url, _account()) == "allow"  # still serving


# --------------------------------------------------------------------------
# 4. investigator workflow (SS3.7) over HTTP
# --------------------------------------------------------------------------


class TestInvestigatorWorkflow:
    def test_credentials_and_reason_are_enforced_and_the_record_is_returned(
        self, hmac_api: _Proc, bank_b_live: _BankB
    ) -> None:
        url = hmac_api.url
        assert url is not None
        flagged = _account()
        bank_b_live.flag(_hmac_id(flagged), score=93)
        blocked = _transfer(url, flagged).json()
        assert blocked["decision"] == "block"
        ref = blocked["audit_ref"]

        def get(headers: dict[str, str], audit_ref: str = ref) -> httpx.Response:
            return httpx.get(f"{url}/audit/{audit_ref}", headers=headers, timeout=10)

        cases: list[tuple[str, Callable[[], httpx.Response], int]] = [
            ("no credentials", lambda: get({}), 401),
            ("id without key", lambda: get({"X-Investigator-Id": "alice"}), 401),
            ("wrong key", lambda: get(_investigator_headers(key="not-the-key")), 401),
            ("unknown investigator", lambda: get(_investigator_headers(ident="mallory")), 401),
            ("missing reason", lambda: get(_investigator_headers(reason=None)), 400),
            ("blank reason", lambda: get(_investigator_headers(reason="")), 400),
            ("unknown ref", lambda: get(_investigator_headers(), "no-such-ref-123"), 404),
        ]
        for label, call, expected in cases:
            resp = call()
            assert resp.status_code == expected, (label, resp.status_code, resp.text)
            if expected != 200:
                assert "receiver_account" not in resp.text, label

        record = get(_investigator_headers(reason="case 4711: chargeback dispute"))
        assert record.status_code == 200
        body = record.json()
        assert body["transfer_id"] == ref
        assert body["receiver_account"] == flagged
        assert body["receiver_ifsc"] == _IFSC
        assert body["risk_id"] == _hmac_id(flagged)
        assert body["risk_score"] == 93
        assert body["decision"] == "block"
        assert body["contributing_banks"] == ["BANK-B"]
        assert body["reason_codes"] == ["new_beneficiary", "high_velocity"]
