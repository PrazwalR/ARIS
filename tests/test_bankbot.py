from aris.bankbot import BankBot, TransferRequest
from aris.bus import InMemoryRiskBus
from aris.hashing import risk_id_for_account
from aris.schema import RiskSignal


def test_anu_blocked_after_bank_b_publish():
    bus = InMemoryRiskBus()
    rid = risk_id_for_account("ACC-999")
    bus.publish(
        RiskSignal(
            risk_id=rid,
            risk_score=92,
            confidence=0.94,
            reason_codes=["high_velocity"],
            model_version="v0.4-fl",
            source_bank_id="BANK-B",
        )
    )
    bot = BankBot(bus)
    out = bot.pre_transaction(
        TransferRequest(user="Anu", bank_id="BANK-A", receiver_account="ACC-999", amount=5000)
    )
    assert out.decision == "block"
    assert out.risk_score == 92
    assert "ACC-999" not in out.risk_id
