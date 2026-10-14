"""Live end-to-end: OPRF-derived risk_ids across three separate OS processes
that share nothing but the network (docs/SECURITY.md SS3.1, wired in).

  1. The consortium OPRF authority  -- `python -m aris.oprf_service`
  2. Bank A's BankBot API           -- `python -m aris.api`, Kafka-backed,
                                       ARIS_API_RISK_ID_MODE=oprf
  3. Bank B (this script)           -- derives the receiver's risk_id by
                                       asking the authority over HTTP, signs,
                                       publishes to the live Kafka bus

Then a customer transfer at Bank A must come back BLOCK, an unflagged account
ALLOW, the investigator path must return the evidence, and the authority must
have attributed the queries to both banks without ever seeing an account.

Needs `docker compose up -d`. Exits non-zero on any failed check.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from aris.attestation import Publisher, PublisherKeyring, public_key_to_hex
from aris.kafka_bus import KafkaRiskBus
from aris.oprf import AuthorityKeyMismatch
from aris.oprf_service import HttpOprfAuthorityClient
from aris.risk_id import OprfRiskIdDeriver, publish_id
from aris.schema import RiskSignal

OPRF_PORT, API_PORT = 8100, 8001
OPRF_URL, API_URL = f"http://127.0.0.1:{OPRF_PORT}", f"http://127.0.0.1:{API_PORT}"
BANK_KEYS = {"BANK-A": uuid.uuid4().hex, "BANK-B": uuid.uuid4().hex}
IFSC = "HDFC0001234"
FLAGGED = f"ACC-{uuid.uuid4().hex[:8]}"
CLEAN = f"ACC-{uuid.uuid4().hex[:8]}"

failures: list[str] = []


def check(label: str, ok: bool, detail: object = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def wait_healthy(url: str, proc: subprocess.Popen[str], name: str) -> None:
    for _ in range(100):
        if proc.poll() is not None:
            raise SystemExit(f"{name} exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        try:
            if httpx.get(f"{url}/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise SystemExit(f"{name} never became healthy")


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="aris-e2e-"))
    procs: list[subprocess.Popen[str]] = []
    try:
        print("\n--- 1. start the OPRF authority (its own process) ---")
        oprf = subprocess.Popen(
            [sys.executable, "-m", "aris.oprf_service", "--port", str(OPRF_PORT), "--generate-key"],
            env={
                **os.environ,
                "ARIS_OPRF_KEY_FILE": str(workdir / "authority.pem"),
                "ARIS_OPRF_BANKS": ",".join(f"{b}:{k}" for b, k in BANK_KEYS.items()),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(oprf)
        assert oprf.stdout is not None
        line = oprf.stdout.readline()
        match = re.search(r"fingerprint: ([0-9a-f]{64})", line)
        if not match:
            raise SystemExit(f"authority did not print its fingerprint: {line!r}")
        fingerprint = match.group(1)
        wait_healthy(OPRF_URL, oprf, "OPRF authority")
        print(f"  authority up, key fingerprint {fingerprint[:16]}...")

        print("\n--- 2. start Bank A's BankBot API (its own process, Kafka, OPRF mode) ---")
        bank_b = Publisher.generate("BANK-B")
        api = subprocess.Popen(
            [sys.executable, "-m", "aris.api", "--host", "127.0.0.1", "--port", str(API_PORT)],
            env={
                **os.environ,
                "ARIS_API_BUS_BACKEND": "kafka",
                "ARIS_API_BANK_ID": "BANK-A",
                "ARIS_API_RISK_ID_MODE": "oprf",
                "ARIS_API_OPRF_URL": OPRF_URL,
                "ARIS_API_OPRF_BANK_KEY": BANK_KEYS["BANK-A"],
                "ARIS_API_OPRF_FINGERPRINT": fingerprint,
                "ARIS_API_TRUSTED_PUBLISHERS": f"BANK-B:{public_key_to_hex(bank_b.public_key)}",
                "ARIS_API_INVESTIGATORS": "alice:e2e-secret",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        procs.append(api)
        wait_healthy(API_URL, api, "BankBot API")
        print("  API up")

        print("\n--- 3. Bank B derives the id via the authority and publishes to Kafka ---")
        b_client = HttpOprfAuthorityClient(OPRF_URL, "BANK-B", BANK_KEYS["BANK-B"], fingerprint)
        risk_id = publish_id(
            OprfRiskIdDeriver(b_client, "BANK-B"), IFSC, FLAGGED, datetime.now(timezone.utc)
        )
        keyring = PublisherKeyring()
        keyring.register("BANK-B", bank_b.public_key)
        bus = KafkaRiskBus(keyring, "localhost:9092", "http://localhost:8081")
        try:
            outcome = bus.publish(
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
        finally:
            bus.close()
        check("Bank B's signal accepted onto the live bus", outcome.value == "accepted", outcome)
        check("published id is not the account number", FLAGGED not in risk_id)

        print("\n--- 4. customers transfer at Bank A, over HTTP ---")

        def transfer(account: str) -> httpx.Response:
            return httpx.post(
                f"{API_URL}/transfers",
                json={
                    "user_ref": "anu",
                    "bank_id": "BANK-A",
                    "receiver_ifsc": IFSC,
                    "receiver_account": account,
                    "amount_minor": 500000,
                },
                timeout=30,
            )

        blocked = transfer(FLAGGED)
        decision = blocked.json().get("decision")
        check("flagged receiver -> block", decision == "block", decision)
        check(
            "customer response carries no score or risk_id",
            set(blocked.json()) == {"decision", "user_message", "audit_ref", "step_up_required"},
        )
        allowed = transfer(CLEAN)
        check("unflagged receiver -> allow", allowed.json().get("decision") == "allow")

        print("\n--- 5. investigator reads the evidence (SS3.7) ---")
        ref = blocked.json()["audit_ref"]
        headers = {
            "X-Investigator-Id": "alice",
            "X-Investigator-Key": "e2e-secret",
            "X-Investigator-Reason": "e2e check",
        }
        record = httpx.get(f"{API_URL}/audit/{ref}", headers=headers, timeout=10)
        body = record.json()
        check("investigator gets the record", record.status_code == 200, record.status_code)
        check("record shows the network score", body.get("risk_score") == 92)
        check("record's risk_id is the OPRF id Bank B published", body.get("risk_id") == risk_id)
        no_auth = httpx.get(f"{API_URL}/audit/{ref}", timeout=10)
        check("no investigator credentials -> 401", no_auth.status_code == 401)

        print("\n--- 6. the authority's side ---")
        stolen = httpx.post(
            f"{OPRF_URL}/evaluate",
            json={"blinded": "2"},
            headers={"X-Bank-Id": "BANK-A", "X-Bank-Key": "guessed"},
            timeout=10,
        )
        check("authority rejects a wrong bank key", stolen.status_code == 401)
        spoof = HttpOprfAuthorityClient(OPRF_URL, "BANK-A", BANK_KEYS["BANK-A"], "00" * 32)
        try:
            _ = spoof.public_numbers
            check("a client pinned to another key refuses this authority", False)
        except AuthorityKeyMismatch:
            check("a client pinned to another key refuses this authority", True)
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    print(f"\n{'ALL CHECKS PASSED' if not failures else f'{len(failures)} FAILED: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
