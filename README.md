# ARIS: AI-Agent Risk Integration System

**Bridging AI-agent security and federated fraud detection through a Shared Risk-Signal Bus.**

Banks collaboratively train a fraud model with federated learning (no raw transactions leave the bank). When a receiver account is high risk, they publish only a **hashed risk ID + score** to a shared bus. BankBot checks that bus before a transfer and can pause or block it—even if another bank first saw the fraud.

**Team:** 2 AI · 1 backend — see [`docs/TEAM.md`](docs/TEAM.md). After each phase, update the **Phase log** below.

## What we are building (in order)

| Phase | Focus | Owner (lead) | Status |
| --- | --- | --- | --- |
| **M0** | Repo, hashed `risk_id`, policy, in-memory bus, BankBot demo | Shared | **Done** |
| **M1** | Flower FL with 3–5 simulated banks (ULB / PaySim / IEEE-CIS) | AI-1 | Next |
| **M2** | Differential privacy + secure aggregation; privacy–utility metrics | AI-1 | Later |
| **M3** | Kafka `risk-signals` topic, schema registry, ACLs | Backend | Later |
| **M4** | BankBot pre-transaction API, thresholds, audit log | Backend | Later |
| **M5** | SHAP/LIME, drift monitoring, model rollback | AI-2 | Later |
| **M6+** | More banks, graph features, robust aggregation | AI-1 | Later |

Full write-up: [`docs/PROJECT.md`](docs/PROJECT.md). Execution plan: [`docs/PHASES.md`](docs/PHASES.md). Team split: [`docs/TEAM.md`](docs/TEAM.md).

## Phase log

Update this section when a phase is finished (what shipped, how to run it).

### M0 — Foundation (completed)

- SHA-256 hashed risk IDs: `risk_id = SHA256(account ∥ SALT)` (plain account never on the bus).
- Shared message shape: `RiskSignal` (`risk_score`, `confidence`, `reason_codes`, `model_version`, `source_bank_id`, TTL).
- In-memory Shared Risk-Signal Bus (stand-in for Kafka in M3).
- Policy: allow / step-up / **block if score > 85**.
- BankBot `pre_transaction` hook + in-memory audit log.
- Demo: Bank B flags `ACC-999` (score 92); Anu at Bank A is blocked.

Run: `python -m aris.demo.anu_transfer` · tests: `pytest`

### M1 — Federated learning

_Not started. AI-1 lead; AI-2 baseline metrics; Backend data/config. See TEAM.md._

### M2 — Privacy on training

_Not started._

### M3 — Kafka risk bus

_Not started._

### M4 — BankBot API

_Not started (M0 has the policy stub)._

### M5 — Explainability & MLOps

_Not started._

### M6+ — Scale

_Not started._

## Domain split (quick)

| AI-1 (FL) | AI-2 (score + XAI) | Backend |
| --- | --- | --- |
| Flower, shards, FedAvg, DP | Scorer, reason codes, SHAP, drift | Hash, Kafka, FastAPI BankBot, audit, auth |

## End-to-end story (demo target)

1. Bank B scores receiver `ACC-999` as risk **92** and publishes `SHA256(ACC-999 ∥ SALT)` to the bus.
2. Anu (Bank A) asks BankBot to send ₹5,000 to `ACC-999`.
3. Bank A hashes the same account, finds score 92, policy **blocks** (`score > 85`).

## Quick start (after Python venv)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
python -m aris.demo.anu_transfer
pytest
```

## Layout

```
src/aris/          Core library (hashing, policy, bus, bankbot, FL)
docs/              Project report, phases, team split
data/              Dataset placeholders (not committed)
tests/             Hashing, policy, Anu block path
```
