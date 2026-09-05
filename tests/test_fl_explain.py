import numpy as np
import pytest

from aris.fl.datasets import make_synthetic
from aris.fl.explain import FALLBACK_REASON_CODE, FraudExplainer
from aris.fl.model import new_model, train_local
from aris.fl.partition import equal_contiguous_shards
from aris.fl.scorer import FraudScorer


@pytest.fixture(scope="module")
def bank_a_scorer():
    # Bank A's shard: fraud is driven by feature f0 by construction (see
    # aris.fl.datasets.make_synthetic -- bank i's fraud direction is
    # feature i % n_features).
    data = make_synthetic(n_banks=5, seed=42)
    shards = equal_contiguous_shards(data.x, data.y, 5)
    x, y = shards[0]
    model = new_model(x.shape[1], hidden=16, seed=42)
    train_local(model, x, y, epochs=20, batch_size=128, lr=0.1, seed=0)
    return FraudScorer(model, model_version="test"), x, y


def test_explainer_identifies_the_true_driving_feature(bank_a_scorer):
    scorer, x, _y = bank_a_scorer
    feature_names = [f"f{i}" for i in range(8)]
    explainer = FraudExplainer(scorer, background=x[:100], feature_names=feature_names)

    scores = scorer.predict_proba(x)
    idx = int(np.argsort(-scores)[0])  # the row scored most fraud-like
    exp = explainer.explain_row(x[idx])

    # f0 is the ground-truth driver for this bank; its |SHAP value| must
    # dominate every other feature's for the model's top-scored row.
    by_name = {f.feature_name: f.shap_value for f in exp.top_features}
    assert "f0" in by_name
    assert abs(by_name["f0"]) > max(abs(v) for k, v in by_name.items() if k != "f0")


def test_unmapped_top_feature_falls_back_to_generic_code(bank_a_scorer):
    scorer, x, _y = bank_a_scorer
    feature_names = [f"f{i}" for i in range(8)]
    # No reason_code_map supplied -- every code must be the honest fallback,
    # never an invented domain claim about an anonymous feature.
    explainer = FraudExplainer(scorer, background=x[:100], feature_names=feature_names)
    scores = scorer.predict_proba(x)
    idx = int(np.argsort(-scores)[0])
    exp = explainer.explain_row(x[idx])
    assert exp.reason_codes == (FALLBACK_REASON_CODE,)


def test_mapped_feature_produces_named_reason_code(bank_a_scorer):
    scorer, x, _y = bank_a_scorer
    feature_names = [f"f{i}" for i in range(8)]
    explainer = FraudExplainer(
        scorer,
        background=x[:100],
        feature_names=feature_names,
        reason_code_map={"f0": "high_velocity"},
    )
    scores = scorer.predict_proba(x)
    idx = int(np.argsort(-scores)[0])
    exp = explainer.explain_row(x[idx])
    assert "high_velocity" in exp.reason_codes


def test_reason_codes_respect_max_reason_codes(bank_a_scorer):
    scorer, x, _y = bank_a_scorer
    feature_names = [f"f{i}" for i in range(8)]
    explainer = FraudExplainer(
        scorer,
        background=x[:100],
        feature_names=feature_names,
        reason_code_map={f"f{i}": f"code_{i}" for i in range(8)},
        max_reason_codes=2,
    )
    scores = scorer.predict_proba(x)
    idx = int(np.argsort(-scores)[0])
    exp = explainer.explain_row(x[idx], top_k=8)
    assert len(exp.reason_codes) <= 2


def test_explain_row_reports_requested_top_k(bank_a_scorer):
    scorer, x, _y = bank_a_scorer
    feature_names = [f"f{i}" for i in range(8)]
    explainer = FraudExplainer(scorer, background=x[:100], feature_names=feature_names)
    exp = explainer.explain_row(x[0], top_k=4)
    assert len(exp.top_features) == 4


def test_rejects_mismatched_feature_names():
    model = new_model(4, hidden=4, seed=0)
    scorer = FraudScorer(model)
    bg = np.zeros((10, 4), dtype=np.float32)
    with pytest.raises(ValueError):
        FraudExplainer(scorer, background=bg, feature_names=["a", "b"])  # only 2 names for 4 cols


def test_rejects_empty_feature_names():
    model = new_model(4, hidden=4, seed=0)
    scorer = FraudScorer(model)
    bg = np.zeros((10, 4), dtype=np.float32)
    with pytest.raises(ValueError):
        FraudExplainer(scorer, background=bg, feature_names=[])


def test_shap_derived_reason_codes_flow_end_to_end_to_a_block_decision(bank_a_scorer):
    """M5 'done when': a blocked transfer has reason codes an analyst can
    defend. This traces SHAP attribution all the way through RiskSignal's own
    validation and BankBot's policy to an actual BLOCK -- not just that
    FraudExplainer produces plausible-looking strings in isolation.
    """
    from aris.attestation import Publisher, PublisherKeyring
    from aris.bankbot import BankBot, InMemoryAuditLog, TransferRequest
    from aris.bus import InMemoryRiskBus
    from aris.hashing import risk_id_for_account
    from aris.schema import Decision, RiskSignal

    scorer, x, _y = bank_a_scorer
    feature_names = [f"f{i}" for i in range(8)]
    explainer = FraudExplainer(
        scorer,
        background=x[:100],
        feature_names=feature_names,
        reason_code_map={"f0": "high_velocity"},
    )
    scores = scorer.predict_proba(x)
    idx = int(np.argsort(-scores)[0])
    exp = explainer.explain_row(x[idx])

    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    keyring.register(bank_b.bank_id, bank_b.public_key)

    signal = RiskSignal(
        risk_id=risk_id_for_account("HDFC0001234", "ACC-999"),
        risk_score=round(scores[idx] * 100),
        confidence=0.9,
        reason_codes=exp.reason_codes,  # must pass RiskSignal's own token validator
        model_version="test-explain",
        source_bank_id="BANK-B",
    )
    bus = InMemoryRiskBus(keyring)
    bus.publish(bank_b.sign(signal))

    bot = BankBot(bus, audit=InMemoryAuditLog())
    req = TransferRequest(
        user_ref="anu",
        bank_id="BANK-A",
        receiver_ifsc="HDFC0001234",
        receiver_account="ACC-999",
        amount_minor=500000,
        transfer_id="explain-e2e-001",
    )
    outcome = bot.pre_transaction(req)

    assert outcome.decision is Decision.BLOCK
    audit_entry = bot.audit.entries[0]
    assert audit_entry.reason_codes == exp.reason_codes
    assert "high_velocity" in audit_entry.reason_codes
