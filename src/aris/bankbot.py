from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from aris.bus import InMemoryRiskBus
from aris.hashing import risk_id_for_account
from aris.schema import Decision, PolicyConfig, apply_policy


@dataclass
class TransferRequest:
    user: str
    bank_id: str
    receiver_account: str
    amount: float
    currency: str = "INR"


@dataclass
class BankBotDecision:
    decision: Decision
    risk_id: str
    risk_score: int | None
    reason_codes: list[str]
    source_bank_id: str | None
    user_message: str
    audited_at: datetime


def _message(decision: Decision, score: int | None) -> str:
    if decision == "block":
        return (
            "This transfer looks risky. The receiver account has been flagged "
            "for suspicious activity by our fraud network. For your safety, "
            "this transaction is blocked."
        )
    if decision == "step_up":
        return (
            "This transfer needs extra verification before we send the money. "
            f"Network risk score: {score}."
        )
    return "Transfer looks OK. Proceeding."


class BankBot:
    def __init__(self, bus: InMemoryRiskBus, policy: PolicyConfig | None = None) -> None:
        self.bus = bus
        self.policy = policy or PolicyConfig()
        self.audit_log: list[BankBotDecision] = []

    def pre_transaction(self, req: TransferRequest) -> BankBotDecision:
        rid = risk_id_for_account(req.receiver_account)
        signal = self.bus.lookup(rid)
        score = signal.risk_score if signal else None
        decision = apply_policy(score, self.policy)
        result = BankBotDecision(
            decision=decision,
            risk_id=rid,
            risk_score=score,
            reason_codes=list(signal.reason_codes) if signal else [],
            source_bank_id=signal.source_bank_id if signal else None,
            user_message=_message(decision, score),
            audited_at=datetime.now(timezone.utc),
        )
        self.audit_log.append(result)
        return result
