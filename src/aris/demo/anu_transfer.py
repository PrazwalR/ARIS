"""M0 demo: Bank B flags ACC-999, and Anu's transfer at Bank A is blocked.

Run with ``python -m aris.demo.anu_transfer``.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from decimal import Decimal

from aris.attestation import Publisher, PublisherKeyring, SignatureInvalid
from aris.bankbot import BankBot, InMemoryAuditLog, TransferRequest
from aris.bus import InMemoryRiskBus
from aris.hashing import DEV_MODE_ENV_VAR, SALT_ENV_VAR, risk_id_for_account
from aris.schema import (
    CONFIRMED_FRAUD,
    HIGH_VELOCITY,
    NEW_BENEFICIARY,
    RiskSignal,
)

ACCOUNT = "ACC-999"
# Bank B's IFSC -- ACC-999 is one of Bank B's customer accounts, and risk_id is
# now derived from the (ifsc, account) pair, not the account alone (see
# docs/SECURITY.md SS3.3).
BANK_B_IFSC = "BKBB0001234"

# No federated model exists yet (M1). Named so the demo output cannot be
# mistaken for the product of a trained FL model.
MODEL_VERSION = "m0-demo-stub"


def _ensure_key_configured() -> None:
    """Use the public development key if no consortium key is present.

    Announced rather than silent: the production path fails closed, and it must
    stay obvious that this run is not using a real secret.
    """
    if not os.environ.get(SALT_ENV_VAR):
        os.environ[DEV_MODE_ENV_VAR] = "1"
        print(
            f"[demo] {SALT_ENV_VAR} is not set - using the public development key.\n"
            f"[demo] Real deployments load the consortium key from an HSM/KMS.\n",
            file=sys.stderr,
        )


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    _ensure_key_configured()

    # Every member signs what it publishes, and the consortium keyring is the
    # list of banks whose signatures the bus will accept.
    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    bank_evil = Publisher.generate("BANK-EVIL")
    keyring.register(bank_b.bank_id, bank_b.public_key)
    keyring.register(bank_evil.bank_id, bank_evil.public_key)

    bus = InMemoryRiskBus(keyring)
    risk_id = risk_id_for_account(BANK_B_IFSC, ACCOUNT)

    # Bank B saw the fraud first and publishes what it knows -- a pseudonymous
    # identifier and a score, never the account number.
    bank_b_signal = RiskSignal(
        risk_id=risk_id,
        risk_score=92,
        confidence=0.94,
        reason_codes=(NEW_BENEFICIARY, HIGH_VELOCITY, CONFIRMED_FRAUD),
        model_version=MODEL_VERSION,
        source_bank_id="BANK-B",
        ttl_hours=24,
    )
    bus.publish(bank_b.sign(bank_b_signal))

    print("=== Shared Risk-Signal Bus (no plain account number) ===")
    print(json.dumps(bank_b_signal.model_dump(mode="json"), indent=2))

    # Anu banks with Bank A, which has never seen ACC-999 before.
    audit = InMemoryAuditLog()
    bot = BankBot(bus, audit=audit)
    request = TransferRequest.from_major_units(
        Decimal("5000.00"),
        user_ref="anu",
        bank_id="BANK-A",
        receiver_ifsc=BANK_B_IFSC,
        receiver_account=ACCOUNT,
    )
    outcome = bot.pre_transaction(request)

    print("\n=== Bank A BankBot (what the customer sees) ===")
    print(f"Anu: Send Rs {request.amount_major:,.2f} to {ACCOUNT}")
    print(f"decision        : {outcome.decision.value}")
    print(f"BankBot         : {outcome.user_message}")
    print(f"audit reference : {outcome.audit_ref}")

    # The evidence lives in the audit sink, not in what BankBot hands back to the
    # channel: a reply carrying the score and thresholds would let a fraudster
    # probe the network through the transfer form.
    print("\n=== Internal audit record (analyst access only) ===")
    record = audit.entries[-1]
    print(f"derived risk_id  : {record.risk_id}")
    print(f"network score    : {record.risk_score}")
    live = bus.lookup(record.risk_id)
    print(f"confidence       : {live.signal.confidence if live.signal else None}")
    print(f"reason codes     : {', '.join(record.reason_codes)}")
    print(f"flagged by       : {', '.join(record.contributing_banks)}")
    print(f"model_version    : {record.model_version}")

    # A hostile member cannot clear another bank's flag. Publishing a low score
    # under its own name only adds an opinion...
    bus.publish(
        bank_evil.sign(
            RiskSignal(
                risk_id=risk_id,
                risk_score=0,
                confidence=0.99,
                reason_codes=("new_beneficiary",),
                model_version=MODEL_VERSION,
                source_bank_id="BANK-EVIL",
            )
        )
    )
    still = bot.pre_transaction(request.model_copy(update={"transfer_id": "retry-001"}))
    print("\n=== A hostile member publishes score 0 for the same account ===")
    print(f"decision remains : {still.decision.value} (score {audit.entries[-1].risk_score})")

    # ...and impersonating BANK-B to retract its signal fails on the signature.
    forged = RiskSignal(
        risk_id=risk_id,
        risk_score=0,
        confidence=0.99,
        reason_codes=("new_beneficiary",),
        model_version=MODEL_VERSION,
        source_bank_id="BANK-B",
    )
    try:
        bus.publish(bank_evil.sign(forged))
    except SignatureInvalid as exc:
        print(f"forged retraction : rejected ({exc})")
    print(f"network score     : {bus.lookup(risk_id).score} (unchanged)")

    # Retrying the same transfer returns the decision already taken, rather than
    # re-deciding against bus state that may have moved underneath it.
    replay = bot.pre_transaction(request.model_copy(update={"transfer_id": "retry-001"}))
    print(f"retry of retry-001: {replay.decision.value} (idempotent, no new audit row)")


if __name__ == "__main__":
    main()
