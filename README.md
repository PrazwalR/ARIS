# ARIS: AI-Agent Risk Integration System

**Bridging AI-agent security and federated fraud detection through a Shared Risk-Signal Bus.**

Banks collaboratively train a fraud model with federated learning (no raw transactions leave the bank). When a receiver account is high risk, they publish only a **hashed risk ID + score** to a shared bus. BankBot checks that bus before a transfer and can pause or block it—even if another bank first saw the fraud.

**Team:** 2 AI · 1 backend — see [`docs/TEAM.md`](docs/TEAM.md). After each phase, update the **Phase log** below.

## What we are building (in order)

| Phase | Focus | Owner (lead) | Status |
| --- | --- | --- | --- |
| **M0** | Repo, hashed `risk_id`, policy, in-memory bus, BankBot demo | Shared | **Done** |
| **M1** | Flower FL with 3–5 simulated banks (ULB / PaySim / IEEE-CIS) | AI-1 | **Done** |
| **M2** | Differential privacy + secure aggregation; privacy–utility metrics | AI-1 | Next |
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

### M1 — Federated learning (completed)

**Goal met:** A global FedAvg model beats the mean of local-only bank models on a held-out set. Clients send **weight arrays + example counts only** — no raw transaction rows, account numbers, or labels leave the bank process.

#### What shipped

| Piece | Where | What it does |
| --- | --- | --- |
| Flower bank client | `src/aris/fl/client.py` | `BankFlowerClient(NumPyClient)` trains on that bank’s shard only |
| FedAvg | `src/aris/fl/fedavg.py` | Weighted average via Flower’s `aggregate` (no Ray; reliable on Windows) |
| NumPy MLP | `src/aris/fl/model.py` | One hidden layer + class-weighted BCE (no PyTorch required) |
| Datasets | `src/aris/fl/datasets.py` | `synthetic`, `ulb` (auto-download), `paysim`, `ieee-cis` |
| Partitions | `src/aris/fl/partition.py` | Bank-identity (synthetic), **temporal** (ULB), Dirichlet label skew |
| Metrics | `src/aris/fl/metrics.py` | AUC, PR-AUC, recall@5% FPR, FPR@50% recall |
| Scorer handoff | `src/aris/fl/scorer.py` | `score_row` → `{risk_score 0–100, confidence, model_version}` for M3/M4 |
| Banks | `BANK-A` … `BANK-E` | Same IDs as the project report |

**Not used on purpose:** Flower `run_simulation` / Ray. That path is brittle on Windows. Aggregation still uses Flower’s official FedAvg math.

#### Verified results (this machine)

**Synthetic (5 banks, 8 rounds × 4 local epochs)** — each bank’s fraud depends on a different feature; holdout mixes all banks.

| Model | AUC | PR-AUC | Recall @ 5% FPR | FPR @ 50% recall |
| --- | --- | --- | --- | --- |
| Mean of 5 **local** models | 0.572 | 0.316 | 0.088 | 0.390 |
| **Global FedAvg** | **0.655** | **0.392** | **0.155** | **0.277** |

`global_beats_mean_local_auc = true` (exit code 0).

**ULB credit-card fraud** (auto-downloaded, capped at 30k rows keeping all fraud, 5 banks, temporal shards, 5 rounds × 2 epochs):

| Model | AUC | PR-AUC |
| --- | --- | --- |
| Mean of 5 **local** models | 0.969 | — |
| **Global FedAvg** | **0.985** | **0.877** |

`global_beats_mean_local_auc = true`.

Tests: `pytest` → **10 passed** (M0 + M1), including `test_synthetic_global_beats_mean_local_auc`. M0 Anu demo still blocks ACC-999.

#### How to run M1

From the repo root, with dependencies installed (`pip install -r requirements.txt` and `pip install -e .`, or set `PYTHONPATH=src`):

```bash
python -m aris.fl.run --dataset synthetic --banks 5 --rounds 8 --epochs 4
python -m aris.fl.run --dataset ulb --banks 5 --rounds 5 --epochs 2 --max-rows 30000
pytest
```

Writes `data/processed/m1_metrics_<dataset>.json` and `data/processed/m1_global_<dataset>.npz`.

#### Manual steps (only if something is missing)

1. **Python venv** (once):
   ```bash
   cd "C:\Users\my pc\Desktop\ARIS"
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   pip install -e .
   ```
2. **ULB download failed / no network:** get [ULB creditcard.csv](https://storage.googleapis.com/download.tensorflow.org/data/creditcard.csv) and save as `data/raw/creditcard.csv` (~150 MB). Then re-run the `ulb` command above.
3. **PaySim (optional):** Kaggle *Synthetic Financial Datasets For Fraud Detection* → save as `data/raw/paysim.csv` → `python -m aris.fl.run --dataset paysim --max-rows 50000`.
4. **IEEE-CIS (optional):** Kaggle *IEEE-CIS Fraud Detection* → `data/raw/train_transaction.csv` → `python -m aris.fl.run --dataset ieee-cis --max-rows 50000`.
5. **Confirm privacy:** open a metrics JSON — `privacy.raw_rows_shared` must be `false`. The bus still never sees plain account numbers (that is M0/M3).

### M2 — Privacy on training

_Not started. Next: DP-SGD / secure aggregation (AI-1)._

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
python -m aris.fl.run --dataset synthetic
pytest
```

## Layout

```
src/aris/          Core library (hashing, policy, bus, bankbot, FL)
src/aris/fl/       M1: clients, FedAvg, datasets, metrics, scorer
docs/              Project report, phases, team split
data/raw/          CSVs (not committed)
data/processed/    M1 metrics + weight checkpoints (not committed)
tests/             M0 BankBot + M1 FedAvg / local-vs-global
```
