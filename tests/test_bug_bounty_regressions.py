"""Regression tests for every confirmed bug-bounty finding. Each test failed
on the code as it was before its fix."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.api.config import ApiSettings
from aris.attestation import Publisher, PublisherKeyring
from aris.audit_security import AuditEncryptionKey
from aris.bankbot import (
    AuditRecord,
    BankBot,
    DuplicateAuditRef,
    InMemoryAuditLog,
    TransferIdConflict,
    TransferRequest,
)
from aris.bus import InMemoryRiskBus, LookupResult, RiskBus
from aris.fl import datasets
from aris.fl.metrics import _fpr_at_recall
from aris.hashing import current_epoch, risk_id_for_account
from aris.oprf import OprfAuthority
from aris.oprf_service import create_oprf_app
from aris.schema import Decision, LookupStatus, PolicyConfig, RiskSignal

IFSC = "HDFC0001234"
ACCOUNT = "ACC-999"


def _request(**kw: object) -> TransferRequest:
    base: dict[str, object] = {
        "user_ref": "anu",
        "bank_id": "BANK-A",
        "receiver_ifsc": IFSC,
        "receiver_account": ACCOUNT,
        "amount_minor": 500_000,
    }
    base.update(kw)
    return TransferRequest(**base)  # type: ignore[arg-type]


def _signal(bank: str, score: int, confidence: float, risk_id: str | None = None) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id or risk_id_for_account(IFSC, ACCOUNT),
        risk_score=score,
        confidence=confidence,
        reason_codes=("high_velocity",),
        model_version="v0.4-fl",
        source_bank_id=bank,
    )


@pytest.fixture
def two_banks() -> tuple[PublisherKeyring, Publisher, Publisher]:
    keyring = PublisherKeyring()
    b, evil = Publisher.generate("BANK-B"), Publisher.generate("BANK-EVIL")
    keyring.register(b.bank_id, b.public_key)
    keyring.register(evil.bank_id, evil.public_key)
    return keyring, b, evil


class TestPreviousEpochOutageFailsClosed:
    def test_unavailable_previous_epoch_is_not_treated_as_clean(self, two_banks):
        keyring, b, _ = two_banks
        inner = InMemoryRiskBus(keyring)
        now = datetime.now(timezone.utc)
        yesterday_id = risk_id_for_account(IFSC, ACCOUNT, epoch=current_epoch(now) - 1)
        inner.publish(b.sign(_signal("BANK-B", 99, 0.95, risk_id=yesterday_id)))

        class YesterdayDown(RiskBus):
            def publish(self, signed):  # pragma: no cover - not used
                return inner.publish(signed)

            def lookup(self, risk_id: str) -> LookupResult:
                if risk_id == yesterday_id:
                    return LookupResult(status=LookupStatus.UNAVAILABLE)
                return inner.lookup(risk_id)

        decision = BankBot(YesterdayDown(), now=lambda: now).pre_transaction(_request())
        assert decision.decision is not Decision.ALLOW
        assert decision.decision is Decision.STEP_UP


class TestIdempotencyIsBoundToTheRequest:
    def test_reused_transfer_id_with_a_different_body_is_a_conflict(self, two_banks):
        keyring, b, _ = two_banks
        bus = InMemoryRiskBus(keyring)
        bus.publish(b.sign(_signal("BANK-B", 95, 0.9)))
        bot = BankBot(bus)
        assert (
            bot.pre_transaction(
                _request(transfer_id="idem-0001", receiver_account="ACC-CLEAN")
            ).decision
            is Decision.ALLOW
        )
        with pytest.raises(TransferIdConflict):
            bot.pre_transaction(_request(transfer_id="idem-0001", amount_minor=10**15))

    def test_identical_retry_still_returns_the_recorded_decision(self, two_banks):
        keyring, _, _ = two_banks
        bot = BankBot(InMemoryRiskBus(keyring))
        first = bot.pre_transaction(_request(transfer_id="idem-0002"))
        assert bot.pre_transaction(_request(transfer_id="idem-0002")) == first

    def test_api_maps_the_conflict_to_409(self, two_banks):
        keyring, _, _ = two_banks
        client = TestClient(create_app(InMemoryRiskBus(keyring), audit=InMemoryAuditLog()))
        body = {
            "user_ref": "anu",
            "bank_id": "BANK-A",
            "receiver_ifsc": IFSC,
            "receiver_account": "ACC-CLEAN",
            "amount_minor": 1000,
            "transfer_id": "idem-http-1",
        }
        assert client.post("/transfers", json=body).status_code == 200
        resp = client.post("/transfers", json={**body, "receiver_account": ACCOUNT})
        assert resp.status_code == 409

    def test_concurrent_duplicates_get_one_decision_and_one_record(self, two_banks):
        keyring, b, _ = two_banks
        bus = InMemoryRiskBus(keyring)
        audit = InMemoryAuditLog()
        gate = threading.Event()

        class SlowBus(RiskBus):
            def publish(self, signed):  # pragma: no cover - not used
                return bus.publish(signed)

            def lookup(self, risk_id: str) -> LookupResult:
                gate.wait(2)
                return bus.lookup(risk_id)

        bot = BankBot(SlowBus(), audit=audit)
        results: list[Decision] = []

        def go() -> None:
            results.append(bot.pre_transaction(_request(transfer_id="tid-race-1")).decision)

        threads = [threading.Thread(target=go) for _ in range(4)]
        for t in threads:
            t.start()
        bus.publish(b.sign(_signal("BANK-B", 95, 0.9)))
        gate.set()
        for t in threads:
            t.join(5)
        assert len(set(results)) == 1 and len(results) == 4
        assert [e.transfer_id for e in audit.entries] == ["tid-race-1"]

    def test_audit_sink_refuses_a_duplicate_reference(self):
        audit = InMemoryAuditLog(encryption_key=AuditEncryptionKey.generate())
        bot_record = _record("dup-ref-01")
        audit.record(bot_record)
        with pytest.raises(DuplicateAuditRef):
            audit.record(bot_record)
        assert len(audit.entries) == 1

    def test_reuse_after_replay_cache_eviction_is_refused_not_redecided(self, two_banks):
        keyring, _, _ = two_banks
        bot = BankBot(InMemoryRiskBus(keyring), replay_cache_size=1)
        bot.pre_transaction(_request(transfer_id="evict-0001"))
        bot.pre_transaction(_request(transfer_id="evict-0002"))  # evicts 0001
        with pytest.raises(TransferIdConflict):
            bot.pre_transaction(_request(transfer_id="evict-0001"))


def _record(ref: str) -> AuditRecord:
    return AuditRecord(
        transfer_id=ref,
        user_ref="anu",
        bank_id="BANK-A",
        receiver_ifsc=IFSC,
        receiver_account=ACCOUNT,
        amount_minor=1,
        currency="INR",
        risk_id="a" * 64,
        decision=Decision.ALLOW,
        lookup_status=LookupStatus.NOT_FOUND,
        risk_score=None,
        reason_codes=(),
        contributing_banks=(),
        model_version=None,
        policy=PolicyConfig(),
        decided_at=datetime.now(timezone.utc),
    )


class TestPolicyCannotBeManipulatedByOneMember:
    def test_a_zero_confidence_high_score_cannot_mask_a_peers_block(self, two_banks):
        keyring, b, evil = two_banks
        bus = InMemoryRiskBus(keyring)
        bus.publish(b.sign(_signal("BANK-B", 95, 0.9)))
        assert BankBot(bus).pre_transaction(_request()).decision is Decision.BLOCK
        bus.publish(evil.sign(_signal("BANK-EVIL", 100, 0.0)))
        assert BankBot(bus).pre_transaction(_request()).decision is Decision.BLOCK

    def test_a_score_zero_signal_is_not_corroboration(self, two_banks):
        keyring, b, evil = two_banks
        bus = InMemoryRiskBus(keyring)
        policy = PolicyConfig(min_banks_to_block=2)
        bus.publish(b.sign(_signal("BANK-B", 95, 0.9)))
        bus.publish(evil.sign(_signal("BANK-EVIL", 0, 0.9)))
        assert BankBot(bus, policy=policy).pre_transaction(_request()).decision is (
            Decision.STEP_UP
        )

    def test_two_confident_banks_do_corroborate(self, two_banks):
        keyring, b, evil = two_banks
        bus = InMemoryRiskBus(keyring)
        policy = PolicyConfig(min_banks_to_block=2)
        bus.publish(b.sign(_signal("BANK-B", 95, 0.9)))
        bus.publish(evil.sign(_signal("BANK-EVIL", 90, 0.8)))
        assert BankBot(bus, policy=policy).pre_transaction(_request()).decision is (Decision.BLOCK)


class TestEdgeInputsAreClientErrors:
    def test_malformed_account_is_422_not_500(self, two_banks):
        keyring, _, _ = two_banks
        client = TestClient(create_app(InMemoryRiskBus(keyring), audit=InMemoryAuditLog()))
        resp = client.post(
            "/transfers",
            json={
                "user_ref": "anu",
                "bank_id": "BANK-A",
                "receiver_ifsc": IFSC,
                "receiver_account": "ab",
                "amount_minor": 1000,
            },
        )
        assert resp.status_code == 422

    def test_non_ascii_investigator_key_is_401_not_500(self, two_banks):
        keyring, _, _ = two_banks
        client = TestClient(
            create_app(
                InMemoryRiskBus(keyring),
                settings=ApiSettings(investigators={"alice": "k"}),
                audit=InMemoryAuditLog(),
            )
        )
        resp = client.get(
            "/audit/x",
            headers={
                "X-Investigator-Id": "alice",
                "X-Investigator-Key": "é".encode(),
                "X-Investigator-Reason": "r",
            },
        )
        assert resp.status_code == 401

    def test_non_ascii_bank_key_is_401_not_500(self):
        client = TestClient(create_oprf_app(OprfAuthority(key_size=1024), {"BANK-A": "k"}))
        resp = client.post(
            "/evaluate",
            json={"blinded": "2"},
            headers={"X-Bank-Id": "BANK-A", "X-Bank-Key": "é".encode()},
        )
        assert resp.status_code == 401


def test_api_refuses_to_start_without_a_consortium_key():
    env = {k: v for k, v in os.environ.items() if k not in ("ARIS_SALT", "ARIS_DEV_MODE")}
    proc = subprocess.run(
        [sys.executable, "-m", "aris.api", "--port", "1"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    assert "ARIS_SALT is not set" in (proc.stderr + proc.stdout)


class TestUlbLoaderRefusesPartialData:
    def _csv(self, path: Path, rows: int, fraud: int, truncate: bool = False) -> Path:
        df = pd.DataFrame(
            {
                "Time": np.arange(rows, dtype=float),
                **{f"V{i}": np.zeros(rows) for i in range(1, 3)},
                "Amount": np.ones(rows),
                "Class": [1] * fraud + [0] * (rows - fraud),
            }
        )
        df.to_csv(path, index=False)
        if truncate:
            text = path.read_text()
            path.write_text(text[: len(text) - 3])
        return path

    def test_a_truncated_file_is_rejected(self, tmp_path):
        p = self._csv(tmp_path / "c.csv", 100, 5, truncate=True)
        with pytest.raises(datasets.IncompleteDatasetError):
            datasets.load_ulb(path=p)

    def test_a_complete_but_wrong_size_file_is_rejected(self, tmp_path):
        p = self._csv(tmp_path / "c.csv", 1000, 10)
        with pytest.raises(datasets.IncompleteDatasetError, match="284,807"):
            datasets.load_ulb(path=p)

    def test_an_interrupted_download_leaves_no_file_behind(self, tmp_path, monkeypatch):
        dest = tmp_path / "creditcard.csv"

        def broken_download(_url, filename):
            Path(filename).write_text("Time,Class\n1,0\n")
            raise OSError("connection reset")

        monkeypatch.setattr(datasets, "urlretrieve", broken_download)
        with pytest.raises(OSError):
            datasets.ensure_ulb_csv(dest)
        assert not dest.exists()
        assert not dest.with_suffix(".csv.part").exists()


def test_fpr_at_recall_does_not_depend_on_row_order_under_ties():
    y = np.array([1, 0, 1, 0, 0, 0])
    s = np.array([0.9, 0.9, 0.2, 0.1, 0.1, 0.1])
    order = [1, 0, 2, 3, 4, 5]
    assert _fpr_at_recall(y, s, 0.5) == _fpr_at_recall(y[order], s[order], 0.5)


def test_graph_features_are_scaled_on_training_rows_only():
    from aris.fl.graph_features import generate_multi_bank_transaction_stream

    records, x, _, _ = generate_multi_bank_transaction_stream(num_transactions=1500, seed=7)
    order = np.argsort([r.timestamp for r in records], kind="stable")
    fit = x[order[: int(len(order) * 0.8)]]
    # Standardised on exactly those rows: mean ~0, std ~1 there.
    assert np.allclose(fit.mean(axis=0), 0.0, atol=1e-3)


def test_unique_transfer_ids_are_still_independent():
    keyring = PublisherKeyring()
    bot = BankBot(InMemoryRiskBus(keyring))
    a = bot.pre_transaction(_request(transfer_id=f"t-{uuid.uuid4().hex[:12]}"))
    b = bot.pre_transaction(_request(transfer_id=f"t-{uuid.uuid4().hex[:12]}"))
    assert a.audit_ref != b.audit_ref


class TestKafkaTopicCannotBeCompacted:
    """Under compaction any member with Write could erase a peer's flag by
    appending a record under its key; the topic must use plain retention."""

    @pytest.fixture(autouse=True)
    def _kafka(self):
        from tests.test_kafka_bus import _kafka_reachable

        if not _kafka_reachable():
            pytest.skip("Kafka not reachable")

    def test_new_topics_use_retention_not_compaction(self):
        from kafka.admin import ConfigResource, ConfigResourceType, KafkaAdminClient

        from aris.kafka_bus import TOPIC_RETENTION_MS, ensure_topic

        topic = f"aris-test-retention-{uuid.uuid4().hex[:8]}"
        ensure_topic("localhost:9092", topic, num_partitions=2)
        admin = KafkaAdminClient(bootstrap_servers="localhost:9092")
        try:
            cfg = admin.describe_configs([ConfigResource(ConfigResourceType.TOPIC, topic)])
            conf = cfg["topic"][topic]
            assert conf["cleanup.policy"]["value"] == "delete"
            assert int(conf["retention.ms"]["value"]) >= TOPIC_RETENTION_MS
        finally:
            admin.delete_topics([topic])
            admin.close()

    def test_an_existing_compacted_topic_is_refused(self):
        from kafka.admin import KafkaAdminClient, NewTopic

        from aris.kafka_bus import UnsafeTopicConfig, ensure_topic

        topic = f"aris-test-compact-{uuid.uuid4().hex[:8]}"
        admin = KafkaAdminClient(bootstrap_servers="localhost:9092")
        try:
            admin.create_topics(
                [NewTopic(topic, 2, 1, topic_configs={"cleanup.policy": "compact"})]
            )
            with pytest.raises(UnsafeTopicConfig, match="compact"):
                ensure_topic("localhost:9092", topic, num_partitions=2)
        finally:
            admin.delete_topics([topic])
            admin.close()
