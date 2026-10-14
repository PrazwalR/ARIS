"""ML <-> backend compatibility, end to end.

The flow under test is the one a member bank actually runs:

    federated training (aris.fl.run) -> checkpoint on disk (save_weights)
    -> load_weights + FraudScorer.from_weights -> score_row
    -> (optional) FraudExplainer reason codes
    -> RiskSignal -> Publisher.sign -> RiskBus (in-memory / Kafka)
    -> another bank's BankBot or HTTP API -> allow / step_up / block

Every stage is the real implementation; nothing on the path is mocked. Where a
test pins a specific risk score (threshold boundaries), it does so by building a
checkpoint whose output layer emits exactly that probability and scoring it
through ``FraudScorer`` -- the score still comes out of the model, not out of a
hand-written ``RiskSignal``.
"""

from __future__ import annotations

import math
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import numpy as np
import pytest
from fastapi.testclient import TestClient

from aris.api.app import create_app
from aris.api.config import ApiSettings
from aris.attestation import Publisher, PublisherKeyring, SignatureInvalid, SignedRiskSignal
from aris.bankbot import BankBot, InMemoryAuditLog, TransferRequest
from aris.bus import InMemoryRiskBus, PublishOutcome, RiskBus
from aris.fl import run as fl_run
from aris.fl.config import TrainConfig
from aris.fl.evaluation import build_split, train_and_score
from aris.fl.explain import FALLBACK_REASON_CODE, FraudExplainer
from aris.fl.scorer import FraudScorer, ScoreResult, load_weights, save_weights
from aris.kafka_bus import _signal_from_wire, _signal_to_wire, load_kafka_config
from aris.oprf import OprfAuthority
from aris.risk_id import (
    HmacRiskIdDeriver,
    OprfRiskIdDeriver,
    RiskIdDeriver,
    publish_id,
)
from aris.schema import (
    _TOKEN_PATTERN,
    CONFIDENCE_BUCKET,
    CONFIRMED_FRAUD,
    HIGH_VELOCITY,
    MAX_REASON_CODES,
    MULE_ACCOUNT,
    NEW_BENEFICIARY,
    SUSPICIOUS_PATTERN,
    Decision,
    LookupStatus,
    PolicyConfig,
    RiskSignal,
    apply_policy,
)

IFSC = "HDFC0001234"
SMALL_AMOUNT = 500_000  # Rs 5,000.00 -- well under the amount-only step-up line
LARGE_AMOUNT = 20_000_000  # Rs 200,000.00 -- over it
POLICY = PolicyConfig()  # step_up_at=50, block_at=85, min_confidence_to_block=0.5

# The CLI defaults: on synthetic this trains in well under a second.
FAST_CFG = TrainConfig()

# A fixed map from the synthetic dataset's anonymous features to canonical
# reason codes. The mapping is arbitrary (the features mean nothing, see
# aris.fl.explain's docstring); what is under test is that whatever the
# explainer emits is publishable.
REASON_CODE_MAP = {
    "f0": HIGH_VELOCITY,
    "f1": NEW_BENEFICIARY,
    "f2": SUSPICIOUS_PATTERN,
    "f3": MULE_ACCOUNT,
    "f4": CONFIRMED_FRAUD,
    "f5": HIGH_VELOCITY,  # deliberate duplicate target: must be de-duplicated
}


# --------------------------------------------------------------------------- #
# Shared fixtures: one real federated training run per module
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def trained(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Train via the real CLI entry point (``run_experiment``), with the
    checkpoint directory redirected so the repo's own data/processed checkpoint
    is never overwritten by a test."""
    ckpt_dir = tmp_path_factory.mktemp("ckpt")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(fl_run, "DATA_PROCESSED", ckpt_dir)
        report = fl_run.run_experiment("synthetic", FAST_CFG)
    in_memory = train_and_score("synthetic", FAST_CFG)
    _, _, _, x_te, y_te = build_split("synthetic", FAST_CFG)
    weights, meta = load_weights(report["checkpoint"])
    scorer = FraudScorer.from_weights(
        n_features=meta["n_features"],
        weights=weights,
        hidden=meta["hidden"],
        model_version=meta["model_version"],
    )
    return {
        "report": report,
        "checkpoint": Path(report["checkpoint"]),
        "in_memory_scores": in_memory.global_scores,
        "x_te": x_te,
        "y_te": y_te,
        "weights": weights,
        "meta": meta,
        "scorer": scorer,
    }


@pytest.fixture
def bank_a_ids() -> RiskIdDeriver:
    return HmacRiskIdDeriver()


def _scorer_emitting(trained: dict[str, Any], score: int) -> FraudScorer:
    """A checkpoint, built from the trained one, whose output layer ignores the
    input and emits probability ``score/100`` -- so a boundary score is
    produced by the model path rather than typed into a RiskSignal."""
    w1, b1, _, _ = trained["weights"]
    p = score / 100.0
    logit = math.log(p / (1.0 - p))
    weights = [
        w1,
        b1,
        np.zeros((b1.shape[0], 1), dtype=np.float32),
        np.array([logit], dtype=np.float32),
    ]
    meta = trained["meta"]
    return FraudScorer.from_weights(
        meta["n_features"], weights, hidden=meta["hidden"], model_version=meta["model_version"]
    )


def _signal_from_score(
    result: ScoreResult,
    risk_id: str,
    *,
    reason_codes: tuple[str, ...] = (FALLBACK_REASON_CODE,),
    bank: str = "BANK-B",
    confidence: float | None = None,
) -> RiskSignal:
    return RiskSignal(
        risk_id=risk_id,
        risk_score=result.risk_score,
        confidence=result.confidence if confidence is None else confidence,
        reason_codes=reason_codes,
        model_version=result.model_version,
        source_bank_id=bank,
        ttl_hours=24,
        timestamp=datetime.now(timezone.utc),
    )


def _transfer(account: str, amount: int = SMALL_AMOUNT) -> TransferRequest:
    return TransferRequest(
        user_ref="anu",
        bank_id="BANK-A",
        receiver_ifsc=IFSC,
        receiver_account=account,
        amount_minor=amount,
    )


def _expected(score: int, bucketed_confidence: float, amount: int = SMALL_AMOUNT) -> Decision:
    """Independent restatement of the documented policy -- not a call into
    apply_policy -- so a regression there is caught, not mirrored."""
    if score >= POLICY.block_at:
        if bucketed_confidence < POLICY.min_confidence_to_block:
            return Decision.STEP_UP
        return Decision.BLOCK
    if score >= POLICY.step_up_at:
        return Decision.STEP_UP
    if amount > POLICY.step_up_above_amount_minor:
        return Decision.STEP_UP
    return Decision.ALLOW


def _bucket(confidence: float) -> float:
    # RiskSignal floors confidence to its bucket: rounding up could carry a
    # signal over min_confidence_to_block.
    floored = math.floor(round(confidence / CONFIDENCE_BUCKET, 9)) * CONFIDENCE_BUCKET
    return round(min(max(floored, 0.0), 1.0), 2)


def _publish_and_decide(
    keyring: PublisherKeyring,
    bank_b: Publisher,
    ids: RiskIdDeriver,
    result: ScoreResult,
    account: str,
    *,
    amount: int = SMALL_AMOUNT,
    confidence: float | None = None,
    reason_codes: tuple[str, ...] = (FALLBACK_REASON_CODE,),
    bus: RiskBus | None = None,
) -> tuple[Decision, RiskSignal, BankBot]:
    bus = bus if bus is not None else InMemoryRiskBus(keyring)
    risk_id = publish_id(ids, IFSC, account, datetime.now(timezone.utc))
    signal = _signal_from_score(result, risk_id, confidence=confidence, reason_codes=reason_codes)
    assert bus.publish(bank_b.sign(signal)) is PublishOutcome.ACCEPTED
    bot = BankBot(bus, policy=POLICY, risk_ids=ids)
    return bot.pre_transaction(_transfer(account, amount)).decision, signal, bot


# --------------------------------------------------------------------------- #
# (1) Train -> checkpoint -> reload
# --------------------------------------------------------------------------- #


class TestCheckpointRoundTrip:
    def test_training_produces_a_useful_model_and_a_checkpoint(self, trained):
        report = trained["report"]
        assert trained["checkpoint"].exists()
        assert trained["checkpoint"].name == "m1_global_synthetic.npz"
        # A model no better than chance would make every downstream check
        # vacuous. The bank-identity split is deliberately hard (each bank only
        # sees its own fraud feature); the default config lands around 0.65.
        assert report["global"]["auc"] > 0.6
        assert report["model_version"] == trained["meta"]["model_version"]

    def test_checkpoint_metadata_describes_the_weights(self, trained):
        meta, weights = trained["meta"], trained["weights"]
        assert meta["dataset"] == "synthetic"
        assert meta["n_features"] == weights[0].shape[0] == trained["x_te"].shape[1]
        assert meta["hidden"] == weights[0].shape[1] == weights[2].shape[0]
        assert len(meta["feature_names"]) == meta["n_features"]
        assert [w.shape for w in weights] == [
            (meta["n_features"], meta["hidden"]),
            (meta["hidden"],),
            (meta["hidden"], 1),
            (1,),
        ]

    def test_reloaded_scores_equal_in_memory_scores(self, trained):
        reloaded = trained["scorer"].predict_proba(trained["x_te"])
        np.testing.assert_array_equal(reloaded, trained["in_memory_scores"])

    def test_score_row_matches_batch_prediction_row_by_row(self, trained):
        scorer, x_te = trained["scorer"], trained["x_te"]
        batch = scorer.predict_proba(x_te)
        for i in range(0, len(x_te), 37):
            r = scorer.score_row(x_te[i])
            # float32 matmul of 1 row vs a batch differs in the last ulp (BLAS
            # blocking), so this is approx; the score may differ only when the
            # probability sits on a rounding edge.
            assert r.probability == pytest.approx(float(batch[i]), abs=1e-6)
            assert abs(r.risk_score - int(np.round(batch[i] * 100))) <= 1

    def test_save_load_save_is_stable(self, trained, tmp_path):
        path = tmp_path / "again.npz"
        save_weights(path, trained["weights"], trained["meta"])
        weights2, meta2 = load_weights(path)
        assert meta2 == trained["meta"]
        for a, b in zip(trained["weights"], weights2, strict=True):
            np.testing.assert_array_equal(a, b)
            assert a.dtype == b.dtype


# --------------------------------------------------------------------------- #
# (2) Scorer output contract
# --------------------------------------------------------------------------- #


def _assert_contract(r: ScoreResult, model_version: str) -> None:
    assert isinstance(r.risk_score, int)
    assert 0 <= r.risk_score <= 100
    assert math.isfinite(r.confidence)
    assert 0.5 <= r.confidence <= 1.0  # max(p, 1-p)
    assert math.isfinite(r.probability)
    assert 0.0 <= r.probability <= 1.0
    assert r.risk_score == int(np.clip(np.round(r.probability * 100), 0, 100))
    assert r.model_version == model_version


class TestScorerContract:
    def test_every_holdout_row_satisfies_the_contract(self, trained):
        scorer, version = trained["scorer"], trained["meta"]["model_version"]
        for row in trained["x_te"]:
            _assert_contract(scorer.score_row(row), version)

    def test_random_finite_rows_satisfy_the_contract(self, trained):
        scorer, version = trained["scorer"], trained["meta"]["model_version"]
        n = trained["meta"]["n_features"]
        rng = np.random.default_rng(7)
        rows = [rng.normal(0, s, size=n) for s in (0.1, 1, 10, 1e3, 1e6) for _ in range(40)]
        rows.append(np.zeros(n))
        for row in rows:
            _assert_contract(scorer.score_row(row), version)

    def test_extreme_finite_rows_are_scored_validly_or_refused(self, trained):
        """Finite but huge inputs overflow inside the float32 MLP (inf - inf in
        the output layer = NaN). Either outcome is acceptable -- a valid score
        or a ValueError -- but not a NaN that becomes "risk_score 0"."""
        scorer, version = trained["scorer"], trained["meta"]["model_version"]
        n = trained["meta"]["n_features"]
        rows = [
            np.full(n, 1e30),
            np.full(n, -1e30),
            np.full(n, np.finfo(np.float32).max),
            np.full(n, -np.finfo(np.float32).max),
            np.array([1e30, -1e30] * (n // 2)),
        ]
        bad = []
        for i, row in enumerate(rows):
            try:
                result = scorer.score_row(row)
            except ValueError:
                continue
            try:
                _assert_contract(result, version)
            except AssertionError:
                bad.append((i, result))
        assert not bad, f"extreme finite rows produced invalid scores: {bad}"

    def test_score_row_accepts_lists_and_2d_single_rows(self, trained):
        scorer = trained["scorer"]
        row = trained["x_te"][0]
        expected = scorer.score_row(row)
        assert scorer.score_row(list(map(float, row))) == expected
        assert scorer.score_row(row.reshape(1, -1)) == expected
        assert scorer.score_row(row.astype(np.float64)) == expected

    @pytest.mark.parametrize(
        "bad_value",
        [np.nan, np.inf, -np.inf],
        ids=["nan", "posinf", "neginf"],
    )
    @pytest.mark.parametrize("where", ["all", "one"])
    def test_non_finite_features_are_refused_not_scored(self, trained, bad_value, where):
        """A NaN/inf feature means the feature pipeline is broken. The scorer
        must refuse it (ValueError) rather than emit a number: today a NaN row
        comes back as risk_score 0 with confidence NaN -- a silent "clean"
        verdict -- and an all-+inf row comes back as a confident 100."""
        n = trained["meta"]["n_features"]
        row = trained["x_te"][0].astype(np.float64).copy()
        if where == "all":
            row[:] = bad_value
        else:
            row[n // 2] = bad_value
        with pytest.raises(ValueError):
            trained["scorer"].score_row(row)

    def test_values_that_overflow_float32_are_refused(self, trained):
        """1e39 is finite as float64 but becomes inf when the scorer casts to
        float32 -- the same corrupted-input case as above, just less obvious."""
        row = np.full(trained["meta"]["n_features"], 1e39)
        with pytest.raises(ValueError):
            trained["scorer"].score_row(row)

    @pytest.mark.parametrize("delta", [-1, 1, -7, 5])
    def test_wrong_length_row_raises(self, trained, delta):
        n = trained["meta"]["n_features"]
        with pytest.raises(ValueError):
            trained["scorer"].score_row(np.zeros(n + delta, dtype=np.float32))

    def test_empty_row_raises(self, trained):
        with pytest.raises(ValueError):
            trained["scorer"].score_row(np.zeros(0, dtype=np.float32))

    def test_bad_input_never_reaches_the_bus_or_breaks_the_receiver(
        self, trained, keyring, bank_b, bank_a_ids
    ):
        """Whatever the scorer does with garbage, the publishing bank's
        pipeline must fail before signing, and the receiving bank's decision
        path must keep working (it sees NOT_FOUND, never a crash)."""
        n = trained["meta"]["n_features"]
        bus = InMemoryRiskBus(keyring)
        bad_rows = [
            np.full(n, np.nan),
            np.full(n, np.inf),
            np.zeros(n - 1),
            np.zeros(n + 3),
        ]
        for i, row in enumerate(bad_rows):
            account = f"BAD-ROW-{i}"
            risk_id = publish_id(bank_a_ids, IFSC, account, datetime.now(timezone.utc))
            published = False
            try:
                result = trained["scorer"].score_row(row)
                signal = _signal_from_score(result, risk_id)
                if not (math.isfinite(result.probability) and 0 <= result.risk_score <= 100):
                    pytest.fail(f"garbage row {i} produced a publishable signal: {result}")
                published = bus.publish(bank_b.sign(signal)) is PublishOutcome.ACCEPTED
            except Exception:
                pass
            bot = BankBot(bus, policy=POLICY, risk_ids=bank_a_ids)
            decision = bot.pre_transaction(_transfer(account)).decision
            assert isinstance(decision, Decision)
            if not published:
                assert bot.audit.entries[-1].lookup_status is LookupStatus.NOT_FOUND
                assert decision is Decision.ALLOW


# --------------------------------------------------------------------------- #
# (3) Model score -> RiskSignal -> sign -> bus -> BankBot
# --------------------------------------------------------------------------- #


class TestPolicyThresholdsOnModelScores:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (1, Decision.ALLOW),
            (49, Decision.ALLOW),
            (50, Decision.STEP_UP),
            (51, Decision.STEP_UP),
            (84, Decision.STEP_UP),
            (85, Decision.BLOCK),
            (86, Decision.BLOCK),
            (99, Decision.BLOCK),
        ],
    )
    def test_boundary_scores_decide_per_policy(
        self, trained, keyring, bank_b, bank_a_ids, score, expected
    ):
        result = _scorer_emitting(trained, score).score_row(trained["x_te"][0])
        assert result.risk_score == score  # the model really produced this score
        decision, signal, bot = _publish_and_decide(
            keyring, bank_b, bank_a_ids, result, f"ACC-B{score}"
        )
        assert decision is expected
        rec = bot.audit.entries[-1]
        assert rec.lookup_status is LookupStatus.FOUND
        assert rec.risk_score == score
        assert rec.model_version == trained["meta"]["model_version"]
        assert rec.contributing_banks == ("BANK-B",)
        assert signal.confidence == _bucket(result.confidence)

    @pytest.mark.parametrize("score", [10, 49])
    def test_low_score_large_transfer_still_steps_up(
        self, trained, keyring, bank_b, bank_a_ids, score
    ):
        result = _scorer_emitting(trained, score).score_row(trained["x_te"][0])
        decision, _, _ = _publish_and_decide(
            keyring, bank_b, bank_a_ids, result, f"ACC-L{score}", amount=LARGE_AMOUNT
        )
        assert decision is Decision.STEP_UP

    @pytest.mark.parametrize(
        ("confidence", "expected"),
        [
            (0.10, Decision.STEP_UP),
            (0.30, Decision.STEP_UP),
            (0.44, Decision.STEP_UP),
            (0.50, Decision.BLOCK),
            (0.90, Decision.BLOCK),
        ],
    )
    def test_low_confidence_caps_a_block_at_step_up(
        self, trained, keyring, bank_b, bank_a_ids, confidence, expected
    ):
        result = _scorer_emitting(trained, 92).score_row(trained["x_te"][0])
        decision, _, _ = _publish_and_decide(
            keyring,
            bank_b,
            bank_a_ids,
            result,
            f"ACC-C{int(confidence * 100)}",
            confidence=confidence,
        )
        assert decision is expected

    @pytest.mark.parametrize("confidence", [0.46, 0.48, 0.49])
    def test_confidence_below_the_block_floor_never_blocks(
        self, trained, keyring, bank_b, bank_a_ids, confidence
    ):
        """The publisher said it is *less* than min_confidence_to_block sure.
        Quantisation is anti-fingerprinting; it must not move a signal across
        the policy line in the unsafe (block) direction. Today 0.48 rounds to
        0.50 in RiskSignal._bucket_confidence and the transfer is blocked."""
        result = _scorer_emitting(trained, 92).score_row(trained["x_te"][0])
        decision, signal, _ = _publish_and_decide(
            keyring,
            bank_b,
            bank_a_ids,
            result,
            f"ACC-Q{int(confidence * 100)}",
            confidence=confidence,
        )
        assert signal.confidence <= confidence
        assert decision is Decision.STEP_UP

    def test_model_confidence_is_never_below_one_half(self, trained):
        """Documents an interaction rather than a bug: score_row's confidence is
        max(p, 1-p), so a model-scored signal can never trip
        min_confidence_to_block=0.5 on its own -- only an explicit override can."""
        probs = trained["scorer"].predict_proba(trained["x_te"])
        assert np.all(np.maximum(probs, 1 - probs) >= 0.5)

    def test_every_holdout_row_round_trips_to_the_policy_decision(
        self, trained, keyring, bank_b, bank_a_ids
    ):
        scorer = trained["scorer"]
        bus = InMemoryRiskBus(keyring)
        bot = BankBot(bus, policy=POLICY, risk_ids=bank_a_ids)
        seen: set[Decision] = set()
        # Every 10th row plus the 15 highest-scored, so all three outcomes occur.
        probs = scorer.predict_proba(trained["x_te"])
        idx = sorted(set(range(0, len(probs), 10)) | set(np.argsort(-probs)[:15].tolist()))
        for i in idx:
            result = scorer.score_row(trained["x_te"][i])
            account = f"ACC-H{i}"
            risk_id = publish_id(bank_a_ids, IFSC, account, datetime.now(timezone.utc))
            signal = _signal_from_score(result, risk_id)
            assert bus.publish(bank_b.sign(signal)) is PublishOutcome.ACCEPTED
            decision = bot.pre_transaction(_transfer(account)).decision
            assert decision is _expected(result.risk_score, signal.confidence)
            assert decision is apply_policy(
                LookupStatus.FOUND,
                result.risk_score,
                SMALL_AMOUNT,
                POLICY,
                confidence=signal.confidence,
            )
            seen.add(decision)
        assert seen == {Decision.ALLOW, Decision.STEP_UP, Decision.BLOCK}

    def test_unflagged_account_is_allowed(self, keyring, bank_a_ids):
        bot = BankBot(InMemoryRiskBus(keyring), policy=POLICY, risk_ids=bank_a_ids)
        assert bot.pre_transaction(_transfer("ACC-CLEAN")).decision is Decision.ALLOW

    def test_signed_model_signal_survives_the_kafka_wire_format(self, trained, keyring, bank_b):
        """No broker: the JSON wire encoding KafkaRiskBus uses must preserve a
        model-produced signal byte-for-byte (quantised fields included), or the
        signature stops verifying on the consumer side."""
        result = trained["scorer"].score_row(trained["x_te"][3])
        signal = _signal_from_score(result, "ab" * 32)
        signed = bank_b.sign(signal)
        back = _signal_from_wire(_signal_to_wire(signed))
        assert back.signal == signal
        assert keyring.verify(back) == signal

    def test_tampered_model_score_is_rejected_by_the_bus(self, trained, keyring, bank_b):
        result = _scorer_emitting(trained, 92).score_row(trained["x_te"][0])
        signed = bank_b.sign(_signal_from_score(result, "cd" * 32))
        forged = SignedRiskSignal(
            signed.signal.model_copy(update={"risk_score": 0}), signed.signature
        )
        with pytest.raises(SignatureInvalid):
            InMemoryRiskBus(keyring).publish(forged)

    def test_model_version_from_checkpoint_is_a_valid_signal_field(self, trained):
        result = trained["scorer"].score_row(trained["x_te"][0])
        sig = _signal_from_score(result, "ef" * 32)
        assert sig.model_version == trained["meta"]["model_version"]


# --------------------------------------------------------------------------- #
# (4) Explainer reason codes are publishable
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def explainer(trained) -> FraudExplainer:
    return FraudExplainer(
        trained["scorer"],
        background=trained["x_te"][:200],
        feature_names=list(trained["meta"]["feature_names"]),
        reason_code_map=REASON_CODE_MAP,
    )


class TestExplainerReasonCodes:
    def test_canonical_codes_in_map_are_valid_tokens(self):
        for code in [*REASON_CODE_MAP.values(), FALLBACK_REASON_CODE]:
            assert _TOKEN_PATTERN.match(code)

    def test_explained_high_risk_rows_publish_and_reach_the_audit_trail(
        self, trained, explainer, keyring, bank_b, bank_a_ids
    ):
        scorer = trained["scorer"]
        probs = scorer.predict_proba(trained["x_te"])
        top = np.argsort(-probs)[:6].tolist() + np.argsort(probs)[:2].tolist()
        bus = InMemoryRiskBus(keyring)
        for i in top:
            row = trained["x_te"][i]
            exp = explainer.explain_row(row, top_k=4)
            codes = exp.reason_codes
            assert 1 <= len(codes) <= MAX_REASON_CODES
            assert len(set(codes)) == len(codes)
            assert all(_TOKEN_PATTERN.match(c) for c in codes)
            assert set(codes) <= set(REASON_CODE_MAP.values()) | {FALLBACK_REASON_CODE}
            result = scorer.score_row(row)
            decision, signal, bot = _publish_and_decide(
                keyring, bank_b, bank_a_ids, result, f"ACC-X{i}", reason_codes=codes, bus=bus
            )
            assert signal.reason_codes == codes
            rec = bot.audit.entries[-1]
            assert rec.reason_codes == codes
            assert decision is _expected(result.risk_score, signal.confidence)

    def test_empty_map_falls_back_to_a_publishable_code(self, trained):
        exp = FraudExplainer(
            trained["scorer"],
            background=trained["x_te"][:100],
            feature_names=list(trained["meta"]["feature_names"]),
        ).explain_row(trained["x_te"][0])
        assert exp.reason_codes == (FALLBACK_REASON_CODE,)
        _signal_from_score(
            trained["scorer"].score_row(trained["x_te"][0]),
            "12" * 32,
            reason_codes=exp.reason_codes,
        )

    def test_full_map_with_large_top_k_stays_within_signal_limits(self, trained):
        names = list(trained["meta"]["feature_names"])
        full_map = {name: f"feature_{name}_high" for name in names}
        exp = FraudExplainer(
            trained["scorer"],
            background=trained["x_te"][:100],
            feature_names=names,
            reason_code_map=full_map,
        )
        probs = trained["scorer"].predict_proba(trained["x_te"])
        for i in np.argsort(-probs)[:3]:
            codes = exp.explain_row(trained["x_te"][i], top_k=len(names)).reason_codes
            sig = _signal_from_score(
                trained["scorer"].score_row(trained["x_te"][i]), "34" * 32, reason_codes=codes
            )
            assert len(sig.reason_codes) <= MAX_REASON_CODES

    def test_non_token_code_in_map_is_stopped_before_the_bus(self, trained):
        exp = FraudExplainer(
            trained["scorer"],
            background=trained["x_te"][:100],
            feature_names=list(trained["meta"]["feature_names"]),
            reason_code_map=dict.fromkeys(trained["meta"]["feature_names"], "High Velocity!"),
        )
        probs = trained["scorer"].predict_proba(trained["x_te"])
        row = trained["x_te"][int(np.argmax(probs))]
        codes = exp.explain_row(row).reason_codes
        if codes != (FALLBACK_REASON_CODE,):
            with pytest.raises(ValueError):
                _signal_from_score(trained["scorer"].score_row(row), "56" * 32, reason_codes=codes)


# --------------------------------------------------------------------------- #
# (5) HTTP API and every risk-id scheme
# --------------------------------------------------------------------------- #

_MODULE_CANDIDATES = (
    "/opt/homebrew/lib/softhsm/libsofthsm2.so",
    "/usr/local/lib/softhsm/libsofthsm2.so",
    "/usr/lib/softhsm/libsofthsm2.so",
    "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
)


def _find_softhsm_module() -> str | None:
    for candidate in _MODULE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for path in Path("/opt/homebrew/Cellar/softhsm").glob("*/lib/softhsm/libsofthsm2.so"):
        return str(path)
    return None


def _softhsm_available() -> bool:
    return _find_softhsm_module() is not None and shutil.which("softhsm2-util") is not None


def _reload_softhsm(module_path: str) -> None:
    """SoftHSM2 reads SOFTHSM2_CONF (and its token directory) only in
    C_Initialize. If an earlier module (tests/test_hsm.py) already loaded the
    library against its own token directory, a token provisioned elsewhere is
    invisible until the library is finalized and initialized again. Finalizing
    is enough: the next ``pkcs11.lib()`` call (aris.hsm makes one per session)
    initializes it afresh under whatever SOFTHSM2_CONF is set at that point --
    which also hands the library back cleanly to any module that runs after."""
    import pkcs11

    lib = pkcs11.lib(module_path)
    if lib.initialized:
        lib.finalize()


@pytest.fixture(scope="module")
def hsm_config() -> Iterator[dict[str, str]]:
    """Token provisioning copied from tests/test_hsm.py: a throwaway SoftHSM2
    token in a fresh temp directory."""
    pytest.importorskip("pkcs11", reason="pkcs11 not installed (optional [hsm] extra)")
    if not _softhsm_available():
        pytest.skip("SoftHSM2 not installed")
    module_path = _find_softhsm_module()
    assert module_path is not None

    tmpdir = Path(tempfile.mkdtemp(prefix="aris-softhsm-mlint-"))
    token_dir = tmpdir / "tokens"
    token_dir.mkdir()
    conf_path = tmpdir / "softhsm2.conf"
    conf_path.write_text(
        f"directories.tokendir = {token_dir}\nobjectstore.backend = file\nlog.level = INFO\n"
    )
    previous = os.environ.get("SOFTHSM2_CONF")
    os.environ["SOFTHSM2_CONF"] = str(conf_path)
    label, pin = "aris-mlint-token", "1234"
    subprocess.run(
        [
            "softhsm2-util",
            "--init-token",
            "--slot",
            "0",
            "--label",
            label,
            "--pin",
            pin,
            "--so-pin",
            "5678",
        ],
        check=True,
        capture_output=True,
    )
    _reload_softhsm(module_path)
    try:
        yield {"module_path": module_path, "token_label": label, "user_pin": pin}
    finally:
        if previous is None:
            os.environ.pop("SOFTHSM2_CONF", None)
        else:
            os.environ["SOFTHSM2_CONF"] = previous
        _reload_softhsm(module_path)
        shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture(scope="module")
def oprf_authority() -> OprfAuthority:
    return OprfAuthority(rate_limit=10_000)


@pytest.fixture(params=["hmac", "oprf", "hsm"])
def scheme(request) -> Iterator[tuple[RiskIdDeriver, RiskIdDeriver]]:
    """(publisher's deriver, checker's deriver) -- separate objects, as two
    banks would hold, that must nonetheless agree on every risk_id."""
    if request.param == "hmac":
        yield HmacRiskIdDeriver(), HmacRiskIdDeriver()
    elif request.param == "oprf":
        authority = request.getfixturevalue("oprf_authority")
        yield (
            OprfRiskIdDeriver(authority, "BANK-B"),
            OprfRiskIdDeriver(authority, "BANK-A"),
        )
    else:
        cfg = request.getfixturevalue("hsm_config")
        from aris.hsm import generate_consortium_key, get_consortium_key, open_hsm_session
        from aris.risk_id import HsmRiskIdDeriver

        label = f"mlint-{uuid.uuid4().hex[:8]}"
        with open_hsm_session(**cfg) as session:
            generate_consortium_key(session, label=label)
            key = get_consortium_key(session, label=label)
            yield HsmRiskIdDeriver(key), HsmRiskIdDeriver(key)


INVESTIGATOR = {"inv-1": "s3cret-key"}
INV_HEADERS = {
    "X-Investigator-Id": "inv-1",
    "X-Investigator-Key": "s3cret-key",
    "X-Investigator-Reason": "ml-backend integration test",
}


def _api_post(client: TestClient, account: str, amount: int = SMALL_AMOUNT) -> dict[str, Any]:
    resp = client.post(
        "/transfers",
        json={
            "user_ref": "anu",
            "bank_id": "BANK-A",
            "receiver_ifsc": IFSC,
            "receiver_account": account,
            "amount_minor": amount,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestEveryRiskIdSchemeEndToEnd:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [(49, Decision.ALLOW), (50, Decision.STEP_UP), (85, Decision.BLOCK)],
    )
    def test_bankbot_decides_model_signal_under_scheme(
        self, trained, keyring, bank_b, scheme, score, expected
    ):
        pub_ids, check_ids = scheme
        account = f"ACC-S{score}-{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        assert publish_id(pub_ids, IFSC, account, now) == publish_id(check_ids, IFSC, account, now)
        bus = InMemoryRiskBus(keyring)
        result = _scorer_emitting(trained, score).score_row(trained["x_te"][0])
        risk_id = publish_id(pub_ids, IFSC, account, now)
        assert bus.publish(bank_b.sign(_signal_from_score(result, risk_id))) is (
            PublishOutcome.ACCEPTED
        )
        bot = BankBot(bus, policy=POLICY, risk_ids=check_ids)
        assert bot.pre_transaction(_transfer(account)).decision is expected
        assert bot.audit.entries[-1].risk_id == risk_id

    @pytest.mark.parametrize(
        ("score", "expected", "step_up_required"),
        [
            (49, Decision.ALLOW, False),
            (50, Decision.STEP_UP, True),
            (84, Decision.STEP_UP, True),
            (85, Decision.BLOCK, False),
        ],
    )
    def test_http_api_decides_model_signal_under_scheme(
        self, trained, keyring, bank_b, scheme, score, expected, step_up_required
    ):
        pub_ids, check_ids = scheme
        account = f"ACC-API{score}-{uuid.uuid4().hex[:6]}"
        bus = InMemoryRiskBus(keyring)
        result = _scorer_emitting(trained, score).score_row(trained["x_te"][0])
        codes = (HIGH_VELOCITY, NEW_BENEFICIARY)
        risk_id = publish_id(pub_ids, IFSC, account, datetime.now(timezone.utc))
        bus.publish(bank_b.sign(_signal_from_score(result, risk_id, reason_codes=codes)))

        app = create_app(
            bus,
            settings=ApiSettings(investigators=INVESTIGATOR, policy=POLICY),
            audit=InMemoryAuditLog(),
            risk_ids=check_ids,
        )
        client = TestClient(app)
        body = _api_post(client, account)
        assert body["decision"] == expected.value
        assert body["step_up_required"] is step_up_required
        # The customer-facing response must not leak model output.
        assert set(body) == {"decision", "user_message", "audit_ref", "step_up_required"}
        assert str(score) not in body["user_message"]

        audit = client.get(f"/audit/{body['audit_ref']}", headers=INV_HEADERS)
        assert audit.status_code == 200, audit.text
        rec = audit.json()
        assert rec["risk_score"] == score
        assert rec["model_version"] == trained["meta"]["model_version"]
        assert tuple(rec["reason_codes"]) == codes
        assert rec["risk_id"] == risk_id
        assert rec["lookup_status"] == "found"

    def test_http_api_low_confidence_caps_block(self, trained, keyring, bank_b, scheme):
        pub_ids, check_ids = scheme
        account = f"ACC-APILC-{uuid.uuid4().hex[:6]}"
        bus = InMemoryRiskBus(keyring)
        result = _scorer_emitting(trained, 95).score_row(trained["x_te"][0])
        risk_id = publish_id(pub_ids, IFSC, account, datetime.now(timezone.utc))
        bus.publish(bank_b.sign(_signal_from_score(result, risk_id, confidence=0.3)))
        client = TestClient(
            create_app(bus, settings=ApiSettings(policy=POLICY), risk_ids=check_ids)
        )
        assert _api_post(client, account)["decision"] == Decision.STEP_UP.value

    def test_http_api_explained_trained_model_signal(
        self, trained, explainer, keyring, bank_b, scheme
    ):
        pub_ids, check_ids = scheme
        scorer = trained["scorer"]
        probs = scorer.predict_proba(trained["x_te"])
        row = trained["x_te"][int(np.argmax(probs))]
        result = scorer.score_row(row)
        codes = explainer.explain_row(row).reason_codes
        account = f"ACC-APIX-{uuid.uuid4().hex[:6]}"
        bus = InMemoryRiskBus(keyring)
        risk_id = publish_id(pub_ids, IFSC, account, datetime.now(timezone.utc))
        signal = _signal_from_score(result, risk_id, reason_codes=codes)
        bus.publish(bank_b.sign(signal))
        client = TestClient(
            create_app(
                bus,
                settings=ApiSettings(investigators=INVESTIGATOR, policy=POLICY),
                risk_ids=check_ids,
            )
        )
        body = _api_post(client, account)
        assert body["decision"] == _expected(result.risk_score, signal.confidence).value
        rec = client.get(f"/audit/{body['audit_ref']}", headers=INV_HEADERS).json()
        assert tuple(rec["reason_codes"]) == codes

    def test_mismatched_schemes_do_not_find_the_signal(self, trained, keyring, bank_b):
        """A publisher on one scheme and a checker on another must not collide
        by accident -- and the miss shows up as NOT_FOUND in the audit trail,
        which is the operational symptom to alarm on."""
        authority = OprfAuthority(rate_limit=1000)
        account = "ACC-MISMATCH"
        bus = InMemoryRiskBus(keyring)
        result = _scorer_emitting(trained, 95).score_row(trained["x_te"][0])
        hmac_id = publish_id(HmacRiskIdDeriver(), IFSC, account, datetime.now(timezone.utc))
        bus.publish(bank_b.sign(_signal_from_score(result, hmac_id)))
        bot = BankBot(bus, policy=POLICY, risk_ids=OprfRiskIdDeriver(authority, "BANK-A"))
        bot.pre_transaction(_transfer(account))
        assert bot.audit.entries[-1].lookup_status is LookupStatus.NOT_FOUND


# --------------------------------------------------------------------------- #
# (6) Checkpoint / model mismatch must fail loudly
# --------------------------------------------------------------------------- #


class TestCheckpointMismatch:
    def test_from_weights_rejects_wrong_n_features(self, trained):
        """from_weights takes n_features and then silently ignores it: the
        loaded w1 keeps the checkpoint's shape. A bank whose feature pipeline
        has drifted from the checkpoint gets no error at load time."""
        meta = trained["meta"]
        with pytest.raises(ValueError):
            FraudScorer.from_weights(meta["n_features"] + 2, trained["weights"])

    def test_from_weights_rejects_wrong_hidden(self, trained):
        meta = trained["meta"]
        with pytest.raises(ValueError):
            FraudScorer.from_weights(meta["n_features"], trained["weights"], hidden=32)

    def test_scoring_rows_of_the_size_the_caller_declared_fails(self, trained):
        """Even if load succeeds, a row shaped for the declared n_features must
        not be scored by a checkpoint expecting a different width."""
        meta = trained["meta"]
        declared = meta["n_features"] + 2
        try:
            scorer = FraudScorer.from_weights(declared, trained["weights"])
        except ValueError:
            return  # failed at load: the better outcome
        with pytest.raises(ValueError):
            scorer.score_row(np.zeros(declared, dtype=np.float32))

    def test_checkpoint_for_a_different_feature_width_cannot_score_these_rows(
        self, trained, tmp_path
    ):
        meta = dict(trained["meta"])
        rng = np.random.default_rng(0)
        wide = [
            rng.normal(size=(12, meta["hidden"])).astype(np.float32),
            np.zeros(meta["hidden"], np.float32),
            rng.normal(size=(meta["hidden"], 1)).astype(np.float32),
            np.zeros(1, np.float32),
        ]
        meta["n_features"] = 12
        path = tmp_path / "wide.npz"
        save_weights(path, wide, meta)
        weights, loaded_meta = load_weights(path)
        scorer = FraudScorer.from_weights(loaded_meta["n_features"], weights)
        with pytest.raises(ValueError):
            scorer.score_row(trained["x_te"][0])  # 8 features into a 12-feature model

    def test_truncated_checkpoint_fails_to_load_into_a_scorer(self, trained, tmp_path):
        path = tmp_path / "truncated.npz"
        save_weights(path, trained["weights"][:3], trained["meta"])
        weights, meta = load_weights(path)
        with pytest.raises(ValueError):
            FraudScorer.from_weights(meta["n_features"], weights)

    def test_checkpoint_without_meta_cannot_be_wired_silently(self, trained, tmp_path):
        path = tmp_path / "nometa.npz"
        np.savez(path, **{f"w{i}": w for i, w in enumerate(trained["weights"])})
        _, meta = load_weights(path)
        assert meta == {}
        with pytest.raises(KeyError):
            _ = meta["n_features"]


# --------------------------------------------------------------------------- #
# (7) Live Kafka bus
# --------------------------------------------------------------------------- #

_BOOTSTRAP, _REGISTRY_URL = load_kafka_config()


def _kafka_reachable() -> bool:
    host, _, port = _BOOTSTRAP.partition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1.0):
            return True
    except OSError:
        return False


def _registry_reachable() -> bool:
    try:
        with urlopen(f"{_REGISTRY_URL}/subjects", timeout=1.0):
            return True
    except (URLError, OSError):
        return False


requires_kafka = pytest.mark.skipif(
    not (_kafka_reachable() and _registry_reachable()),
    reason=(
        f"Kafka ({_BOOTSTRAP}) or Schema Registry ({_REGISTRY_URL}) not reachable -- "
        "run `docker compose up -d` to exercise this test"
    ),
)


def _wait_until(predicate, timeout_s: float = 20.0, interval_s: float = 0.2):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval_s)
    pytest.fail(f"condition did not become true within {timeout_s}s")


@requires_kafka
def test_model_signal_published_on_kafka_drives_a_peer_banks_decisions(trained):
    from aris.kafka_bus import KafkaRiskBus

    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    keyring.register(bank_b.bank_id, bank_b.public_key)
    ids = HmacRiskIdDeriver()
    run_tag = uuid.uuid4().hex[:10].upper()

    scorer = trained["scorer"]
    probs = scorer.predict_proba(trained["x_te"])
    top_row = trained["x_te"][int(np.argmax(probs))]
    cases = {
        f"KAFKA-TOP-{run_tag}": scorer.score_row(top_row),
        f"KAFKA-50-{run_tag}": _scorer_emitting(trained, 50).score_row(top_row),
        f"KAFKA-85-{run_tag}": _scorer_emitting(trained, 85).score_row(top_row),
        f"KAFKA-49-{run_tag}": _scorer_emitting(trained, 49).score_row(top_row),
    }

    bus_b = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
    bus_a = KafkaRiskBus(keyring, _BOOTSTRAP, _REGISTRY_URL)
    try:
        published: dict[str, RiskSignal] = {}
        for account, result in cases.items():
            risk_id = publish_id(ids, IFSC, account, datetime.now(timezone.utc))
            signal = _signal_from_score(result, risk_id, reason_codes=(HIGH_VELOCITY,))
            assert bus_b.publish(bank_b.sign(signal)) is PublishOutcome.ACCEPTED
            published[account] = signal

        bot_a = BankBot(bus_a, policy=POLICY, risk_ids=ids)
        for account, signal in published.items():
            _wait_until(lambda rid=signal.risk_id: bus_a.lookup(rid).status is LookupStatus.FOUND)
            decision = bot_a.pre_transaction(_transfer(account)).decision
            assert decision is _expected(signal.risk_score, signal.confidence), account
            rec = bot_a.audit.entries[-1]
            assert rec.risk_score == signal.risk_score
            assert rec.model_version == trained["meta"]["model_version"]
            assert rec.contributing_banks == ("BANK-B",)

        # Over HTTP, against the same Kafka-backed bus.
        client = TestClient(create_app(bus_a, settings=ApiSettings(policy=POLICY), risk_ids=ids))
        for account, signal in published.items():
            body = _api_post(client, account)
            assert body["decision"] == _expected(signal.risk_score, signal.confidence).value
    finally:
        bus_b.close()
        bus_a.close()
