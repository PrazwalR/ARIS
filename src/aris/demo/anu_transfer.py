"""M0 demo: Bank B flags ACC-999; Anu at Bank A is blocked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from aris.bankbot import BankBot, TransferRequest
from aris.bus import InMemoryRiskBus
from aris.hashing import risk_id_for_account
from aris.schema import RiskSignal


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    bus = InMemoryRiskBus()
    account = "ACC-999"
    rid = risk_id_for_account(account)

    bank_b_signal = RiskSignal(
        risk_id=rid,
        risk_score=92,
        confidence=0.94,
        reason_codes=["new_beneficiary", "high_velocity", "suspicious_pattern"],
        model_version="v0.4-fl",
        source_bank_id="BANK-B",
        ttl_hours=24,
    )
    bus.publish(bank_b_signal)

    bot = BankBot(bus)
    outcome = bot.pre_transaction(
        TransferRequest(
            user="Anu",
            bank_id="BANK-A",
            receiver_account=account,
            amount=5000,
        )
    )

    bus_payload = bank_b_signal.model_dump(mode="json")
    print("=== Shared Risk-Signal Bus (no plain account number) ===")
    print(json.dumps(bus_payload, indent=2))
    print()
    print("=== Bank A BankBot ===")
    print(f"Anu: Send ₹5,000 to {account}")
    print(f"computed risk_id: {outcome.risk_id}")
    print(f"decision: {outcome.decision}  score={outcome.risk_score}")
    print(f"BankBot: {outcome.user_message}")


if __name__ == "__main__":
    main()
