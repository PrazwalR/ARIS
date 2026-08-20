from aris.hashing import risk_id_for_account
from aris.schema import PolicyConfig, apply_policy


def test_same_account_same_risk_id():
    a = risk_id_for_account("ACC-999", b"salt")
    b = risk_id_for_account("ACC-999", b"salt")
    assert a == b
    assert len(a) == 64


def test_different_accounts_differ():
    assert risk_id_for_account("ACC-999", b"salt") != risk_id_for_account("ACC-998", b"salt")


def test_policy_thresholds():
    cfg = PolicyConfig(medium_min=50, high_min=85)
    assert apply_policy(None, cfg) == "allow"
    assert apply_policy(20, cfg) == "allow"
    assert apply_policy(50, cfg) == "step_up"
    assert apply_policy(86, cfg) == "block"
