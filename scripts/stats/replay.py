"""End-to-end replay of the synthetic multi-bank stream through the real BankBot,
with and without the shared network, 5 stream seeds.

Each transfer is decided by the paying bank's BankBot before money moves. A fraud
that goes through is confirmed by the victim's bank CONFIRM_LAG_S later (stream
time), which then signs and publishes a flag (score 92, confidence 0.94) on the
receiver. "local": each bank has a private bus. "aris": one shared bus.

    python scripts/stats/replay.py   ->  docs/results/replay.json
"""

from __future__ import annotations

import collections
import logging
from datetime import datetime, timezone

from _common import write

from aris.attestation import Publisher, PublisherKeyring
from aris.bankbot import BankBot, TransferRequest
from aris.bus import InMemoryRiskBus
from aris.fl.graph_features import TransactionRecord, generate_multi_bank_transaction_stream
from aris.risk_id import HmacRiskIdDeriver, publish_id
from aris.schema import CONFIRMED_FRAUD, RiskSignal

CONFIRM_LAG_S = 3600.0
SEEDS = (42, 43, 44, 45, 46)


def replay(records: list[TransactionRecord], shared: bool) -> dict[str, int]:
    banks = sorted({r.sender_ifsc for r in records} | {r.receiver_ifsc for r in records})
    keyring = PublisherKeyring()
    pubs = {b: Publisher.generate(f"BANK-{b[:4]}") for b in banks}
    for p in pubs.values():
        keyring.register(p.bank_id, p.public_key)
    one = InMemoryRiskBus(keyring, max_publisher_share=1.0)
    buses = {b: one if shared else InMemoryRiskBus(keyring, max_publisher_share=1.0) for b in banks}
    bots = {b: BankBot(buses[b]) for b in banks}
    ids = HmacRiskIdDeriver()
    pending: collections.deque[tuple[float, str, str, str]] = collections.deque()
    counts: collections.Counter[str] = collections.Counter()
    for r in records:
        while pending and pending[0][0] <= r.timestamp:
            _, victim, ifsc, account = pending.popleft()
            signal = RiskSignal(
                risk_id=publish_id(ids, ifsc, account, datetime.now(timezone.utc)),
                risk_score=92,
                confidence=0.94,
                reason_codes=(CONFIRMED_FRAUD,),
                model_version="replay",
                source_bank_id=pubs[victim].bank_id,
                ttl_hours=24,
            )
            buses[victim].publish(pubs[victim].sign(signal))
            counts["flags_published"] += 1
        decision = (
            bots[r.sender_ifsc]
            .pre_transaction(
                TransferRequest(
                    user_ref=r.sender_account,
                    bank_id=pubs[r.sender_ifsc].bank_id,
                    receiver_ifsc=r.receiver_ifsc,
                    receiver_account=r.receiver_account,
                    amount_minor=round(r.amount * 100),
                    transfer_id=r.tx_id,
                )
            )
            .decision.value
        )
        kind = "fraud" if r.is_fraud else "legit"
        counts[f"{kind}_{decision}"] += 1
        counts[f"{kind}_total"] += 1
        if r.is_fraud and decision != "block":  # money moved; step-up counted as passed
            pending.append(
                (r.timestamp + CONFIRM_LAG_S, r.sender_ifsc, r.receiver_ifsc, r.receiver_account)
            )
    return dict(counts)


def main() -> None:
    logging.disable(logging.CRITICAL)
    per_seed = {}
    for seed in SEEDS:
        records, *_ = generate_multi_bank_transaction_stream(seed=seed)
        records = sorted(records, key=lambda r: r.timestamp)
        per_seed[seed] = {"local": replay(records, False), "aris": replay(records, True)}
        print(seed, per_seed[seed])

    def mean(setting: str, key: str) -> float:
        return round(sum(per_seed[s][setting].get(key, 0) for s in SEEDS) / len(SEEDS), 1)

    keys = ("fraud_block", "fraud_step_up", "fraud_allow", "legit_block", "legit_step_up")
    write(
        "replay.json",
        {
            "confirm_lag_s": CONFIRM_LAG_S,
            "seeds": list(SEEDS),
            "mean": {s: {k: mean(s, k) for k in keys} for s in ("local", "aris")},
            "per_seed": per_seed,
            "caveats": [
                "all 300 frauds per stream go to 5 mule accounts, which favours the local setting",
                "the stream has no false flags, so legit_block = 0 says nothing about"
                " false-positive cost",
                "step-up is counted as money moved (worst case)",
            ],
        },
    )


if __name__ == "__main__":
    main()
