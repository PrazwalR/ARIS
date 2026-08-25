# ARIS: AI-Agent Risk Integration System

**Bridging AI-agent security and federated fraud detection through a Shared Risk-Signal Bus.**

The goal: banks collaboratively train a fraud model with federated learning (no raw transactions leave the bank), and when a receiver account is high risk they publish only a **pseudonymous risk ID + score** to a shared bus. BankBot checks that bus before a transfer and can pause or block it—even if another bank first saw the fraud.

**Status: M0–M2 are built and tested — the bus, the policy, BankBot, federated learning, and DP-SGD privacy on training. See the phase table.**

**Team:** 2 AI · 1 backend — see [`docs/TEAM.md`](docs/TEAM.md). After each phase, update the **Phase log** below.

## What we are building (in order)

| Phase | Focus | Owner (lead) | Status |
| --- | --- | --- | --- |
| **M0** | Repo, keyed `risk_id` (HMAC-SHA256), signed signals (Ed25519), policy, in-memory bus, BankBot demo | Shared | **Done** |
| **M1** | Flower FL with 3–5 simulated banks (ULB / PaySim / IEEE-CIS) | AI-1 | **Done** |
| **M2** | Differential privacy (DP-SGD) + secure aggregation; privacy–utility metrics | AI-1 | **Done** |
| **M3** | Kafka `risk-signals` topic, schema registry, ACLs | Backend | Next |
| **M4** | BankBot pre-transaction API, thresholds, audit log | Backend | Later |
| **M5** | SHAP/LIME, drift monitoring, model rollback | AI-2 | Later |
| **M6+** | More banks, graph features, robust aggregation | AI-1 | Later |

Full write-up: [`docs/PROJECT.md`](docs/PROJECT.md). Execution plan: [`docs/PHASES.md`](docs/PHASES.md). Team split: [`docs/TEAM.md`](docs/TEAM.md). **Threat model and known limitations: [`docs/SECURITY.md`](docs/SECURITY.md).**

## Phase log

Update this section when a phase is finished (what shipped, how to run it).

### M0 — Foundation (completed)

- **Keyed pseudonymous IDs**: `risk_id = HMAC-SHA256(consortium_key, normalize(account))`. The plain account never reaches the bus. Fails closed if no key is configured. ASCII-only normalization (rejects ı→I, ß→SS collisions).
- **Shared message shape**: `RiskSignal` (`risk_score`, `confidence`, `reason_codes`, `model_version`, `source_bank_id`, TTL), validated and immutable. TTL bounds 1–168h; naive datetime rejected.
- **Signed publishers**: every signal is Ed25519-signed and verified against a consortium keyring, so a member cannot publish under a peer's name — and therefore cannot retract a peer's fraud flag.
- **In-memory Shared Risk-Signal Bus** (stand-in for Kafka in M3): per-bank per-risk_id storage (not last-writer-wins), TTL expiry, replay rejection by high-water mark (survives eviction), per-publisher quotas that reject (not discard).
- **Policy**: allow / step-up / **block at score ≥ 85**, plus step-up on large amounts, optional cross-bank min-banks-to-block gating, a confidence floor before an account can be blocked, and a bus outage that is never allowed to fail open.
- **BankBot** `pre_transaction` hook, idempotent on `transfer_id`, returning only the decision and an opaque audit reference — the evidence stays in the audit sink so the transfer form cannot be used as a probing oracle. Full audit record (15 fields) logged internally.
- **Demo**: Bank B flags `ACC-999` (score 92); Anu at Bank A is blocked; a hostile member's forged retraction is rejected.
- **153 tests**, ruff-clean, `mypy --strict` clean, and mutation-verified: reverting any of nine critical logic defects makes the suite fail.

Run: `python -m aris.demo.anu_transfer` · tests: `pytest` (see Quick start — a consortium key is required)

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

Tests: `pytest` → **188 passed** (M0 + M1 + M2), including `test_synthetic_global_beats_mean_local_auc`. M0 Anu demo still blocks ACC-999.

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

### M2 — Privacy on training (completed)

**Goal met:** local training is now DP-SGD (Abadi et al. 2016) — per-example L2 gradient clipping plus calibrated Gaussian noise on every mini-batch step — with a documented epsilon, and the model still converges at a meaningful privacy budget.

#### What shipped

| Piece | Where | What it does |
| --- | --- | --- |
| DP-SGD training | `src/aris/fl/model.py::train_local_dp` | Per-example gradient clip + noise, drop-in alternative to `train_local` |
| Privacy accountant | `src/aris/fl/privacy.py` | zCDP composition (Bun & Steinke 2016) → `(epsilon, delta)`; see "accounting is conservative" below |
| Secure aggregation | `src/aris/fl/secure_agg.py` | Pairwise-masked sum (Bonawitz et al. 2017, single-process simulation); optional, independent of DP |
| Sweep CLI | `src/aris/fl/privacy_sweep.py` | Runs the same experiment across noise multipliers, writes the epsilon-vs-AUC table |

**Accounting is conservative on purpose.** The accountant does not credit privacy amplification by subsampling — a correct amplification analysis needs a numerically-integrated subsampled-Gaussian RDP accountant (Abadi et al.'s "moments accountant"), and getting that wrong by *understating* epsilon is a privacy defect, not just an inaccuracy. Every mini-batch step is accounted as a full (non-amplified) Gaussian mechanism release, composed additively in zCDP. The epsilon reported is therefore always a valid upper bound, looser than what a full accountant would report for the same noise multiplier.

**The DP noise itself is sourced from OS entropy, not from the run's reproducibility seed.** Every other random draw in this demo (shard shuffling, model init, secure-agg masks) is deliberately seeded from `TrainConfig.seed` so results are reproducible. The Gaussian noise `clip_and_noise_gradients` adds is the one exception, and has to be: an adversarial audit of this module (see below) found that the original implementation *did* derive it from `seed`, which is reconstructible by the same orchestrating code that also aggregates client updates — making the noise predictable to exactly the party the accountant's epsilon is supposed to protect against, and the reported epsilon meaningless. `train_local_dp` now draws noise from a fresh, unseeded generator by default; only the shuffle order stays reproducible. One consequence: re-running a DP-enabled experiment with the same seed now produces a *different* trained model each time (though statistically similar — see the table below), unlike every other seeded part of this repo.

**Secure aggregation is a different protection, not a substitute for DP.** Masking hides each bank's individual update from the aggregator; it says nothing about what the resulting *aggregate* model can leak about the union of all banks' data. `secure_fedavg` is numerically identical to plain FedAvg (masks cancel exactly — verified in `tests/test_fl_secure_agg.py`), so it costs nothing in accuracy; it can be turned on independently of `dp_enabled`.

#### Verified privacy–utility table (synthetic, 5 banks, 8 rounds × 4 local epochs, `max_grad_norm=5.0`, `delta=1e-5`, one run per row)

| noise_multiplier | epsilon (upper bound) | Global AUC |
| --- | --- | --- |
| — (M1 baseline, no DP) | — | 0.655 |
| 0.5 | 364.6 | 0.634 |
| 1.0 | 118.3 | 0.635 |
| 2.0 | 43.1 | 0.632 |
| 4.0 | 17.6 | 0.633 |
| 8.0 | 7.8 | 0.629 |
| 15.0 | 3.9 | 0.638 |
| 30.0 | 1.9 | 0.613 |
| 60.0 | 0.9 | 0.556 |

Since the noise is now genuinely random rather than seed-derived, individual rows fluctuate a little run to run (across 20 repeated runs at `noise_multiplier=8.0`, AUC stayed within 0.627–0.643, well clear of both M1's mean-of-local baseline and the 60.0 tail) — the trend, not any single row, is the result: AUC degrades gracefully as epsilon shrinks and stays clearly better than a coin flip even at sub-1 epsilon. Tests: `pytest` → **188 passed** (M0 + M1 + M2), including convergence, epsilon-monotonicity, and RNG-separation checks in `tests/test_fl_m2.py` / `tests/test_fl_privacy.py`.

#### How to run M2

```bash
python -m aris.fl.run --dataset synthetic --dp --noise-multiplier 4.0 --max-grad-norm 5.0
python -m aris.fl.privacy_sweep --dataset synthetic
```

Writes `data/processed/m1_metrics_synthetic.json` (single run, `privacy.differential_privacy` block) and `data/processed/m2_privacy_utility_synthetic.json` (the sweep table). Add `--secure-agg` to the single run to aggregate through pairwise masking instead of plain FedAvg.

### M3 — Kafka risk bus

_Not started._ The in-memory bus implements the `RiskBus` interface a Kafka backend will also implement.

### M4 — BankBot API

_Not started._ The policy and audit layers exist; only the HTTP surface is missing.

### M5 — Explainability & MLOps

_Not started._

### M6+ — Scale

_Not started._

## Domain split (quick)

| AI-1 (FL) | AI-2 (score + XAI) | Backend |
| --- | --- | --- |
| Flower, shards, FedAvg, DP | Scorer, reason codes, SHAP, drift | Hashing, attestation, Kafka, FastAPI BankBot, audit, auth |

## End-to-end story (demo target)

1. Bank B scores receiver `ACC-999` as risk **92** and publishes `HMAC-SHA256(key, ACC-999)` to the bus, signed with Bank B's key.
2. Anu (Bank A) asks BankBot to send ₹5,000 to `ACC-999`.
3. Bank A derives the same `risk_id`, finds score 92, and policy **blocks** (`score ≥ 85`).
4. A hostile member that tries to clear the flag by impersonating Bank B is rejected on the signature.

## Quick start (macOS / Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# ARIS fails closed: without a consortium key, deriving a risk_id raises
# SaltNotConfigured. Generate one (32 random bytes, hex-encoded):
export ARIS_SALT=$(python -c "from aris.hashing import generate_key; print(generate_key())")

python -m aris.demo.anu_transfer
python -m aris.fl.run --dataset synthetic
pytest
```

`ARIS_SALT` must be **hex or base64 decoding to at least 32 bytes** — a passphrase is rejected, because one known `(account, risk_id)` pair makes it recoverable. Copy [`.env.example`](.env.example) for a template. For a throwaway run you can instead set `ARIS_DEV_MODE=1`, which uses a publicly known development key; never do that anywhere shared. **Every member bank must hold the same key**, or their risk IDs will not match and every lookup will silently miss.

<details><summary>Windows (PowerShell)</summary>

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
$env:ARIS_SALT = python -c "from aris.hashing import generate_key; print(generate_key())"
python -m aris.demo.anu_transfer
pytest
```
</details>

The federated-learning and HTTP stacks are optional extras, not installed by default: `pip install -e ".[ml]"` for M1/M2, `pip install -e ".[api]"` for M4.

## Layout

```
src/aris/
  hashing.py       risk_id derivation (HMAC-SHA256, key-derived, ASCII-only)
  attestation.py   Ed25519 signing / verification of published signals
  schema.py        RiskSignal, PolicyConfig, apply_policy — the shared contract
  bus.py           RiskBus interface + in-memory implementation
  bankbot.py       pre-transaction check, decisions, audit records
  demo/            the Anu / ACC-999 walkthrough
  fl/              M1: clients, FedAvg, datasets, metrics, scorer
                   M2: privacy.py (DP-SGD + accountant), secure_agg.py, privacy_sweep.py
docs/              project report, phases, team split, security model
data/raw/          CSVs (not committed)
data/processed/    M1/M2 metrics + weight checkpoints (not committed)
tests/             hashing, attestation, schema/policy, bus, bankbot, demo, fl
```
