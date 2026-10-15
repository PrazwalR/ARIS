<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark-theme.svg">
  <img src="docs/assets/logo.svg" alt="ARIS" width="180">
</picture>

# ARIS: AI-Agent Risk Integration System

**Bridging AI-agent security and federated fraud detection through a Shared Risk-Signal Bus.**

The goal: banks collaboratively train a fraud model with federated learning (no raw transactions leave the bank), and when a receiver account is high risk they publish only a **pseudonymous risk ID + score** to a shared bus. BankBot checks that bus before a transfer and can pause or block it—even if another bank first saw the fraud.

**Status: M0–M6 are 100% built, verified, and benchmarked — the bus, the policy, BankBot (over HTTP and OPRF-enabled), federated learning, DP-SGD privacy on training, Kafka-backed risk bus, SHAP explainability + drift monitoring + model registry, Byzantine-robust aggregation (Krum), on-prem graph and receiver velocity feature engine, and unified M0–M6 metrics in `reports/`. See the phase table and master dashboard.**

**Team:** 2 AI · 1 backend — see [`docs/TEAM.md`](docs/TEAM.md). Full consolidated metrics: [`reports/M0_TO_M6_COMPREHENSIVE_DASHBOARD.md`](reports/M0_TO_M6_COMPREHENSIVE_DASHBOARD.md).

## What we are building (in order)

| Phase | Focus | Owner (lead) | Status |
| --- | --- | --- | --- |
| **M0** | Repo, keyed `risk_id` (HMAC-SHA256), signed signals (Ed25519), policy, in-memory bus, BankBot demo | Shared | **Done** |
| **M1** | Flower FL with 3–5 simulated banks (ULB / PaySim / IEEE-CIS) | AI-1 | **Done** |
| **M2** | Differential privacy (DP-SGD) + secure aggregation; privacy–utility metrics | AI-1 | **Done** |
| **M3** | Kafka `risk-signals` topic, schema registry, ACLs | Backend | **Done** |
| **M4** | BankBot pre-transaction API, thresholds, audit log | Backend | **Done** |
| **M5** | SHAP/LIME, drift monitoring, model rollback | AI-2 | **Done** |
| **M6+** | More banks, on-prem graph & velocity features, robust aggregation, OPRF | AI-1 | **Done** |

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

Full breakdown — every bank individually, the global model, both datasets,
plus ROC and Precision/Recall curves: **[docs/METRICS.md](docs/METRICS.md)**.

**Synthetic (5 banks, 8 rounds × 4 local epochs)** — each bank’s fraud depends on a different feature; holdout mixes all banks.

| Model | AUC | PR-AUC | Accuracy @ 0.5 | Recall @ 5% FPR | FPR @ 50% recall |
| --- | --- | --- | --- | --- | --- |
| BANK-A (local only) | 0.560 | 0.302 | 0.626 | 0.065 | 0.391 |
| BANK-B (local only) | 0.597 | 0.354 | 0.638 | 0.126 | 0.378 |
| BANK-C (local only) | 0.560 | 0.300 | 0.624 | 0.078 | 0.422 |
| BANK-D (local only) | 0.555 | 0.298 | 0.591 | 0.074 | 0.416 |
| BANK-E (local only) | 0.589 | 0.329 | 0.629 | 0.097 | 0.342 |
| Mean of 5 **local** models | 0.572 | 0.316 | 0.622 | 0.088 | 0.390 |
| **Global FedAvg** | **0.655** | **0.392** | **0.661** | **0.155** | **0.277** |

Accuracy is threshold-dependent (score ≥ 0.5) and reported alongside AUC,
never instead of it — on this holdout (309/1200 positives, ~26%) a
constant "not fraud" prediction would already score ~74% accuracy, so AUC
and PR-AUC are the metrics that actually distinguish these models.
`global_beats_mean_local_auc = true` (exit code 0). All numbers above are
regenerated live by `python -m aris.fl.run --dataset synthetic`, not fixed.

**ULB credit-card fraud** (the full published dataset, 284,807 transactions over two days, 492 fraud; 5 banks, temporal shards, 5 rounds × 2 epochs). Holdout: the latest 20% by time, 56,962 rows, 75 fraud (0.13%) — far more imbalanced than synthetic, which is why accuracy alone would be an especially bad headline metric here (predicting "not fraud" for every row scores 99.87%):

| Model | AUC | PR-AUC | Precision @ 0.5 | Recall @ 0.5 |
| --- | --- | --- | --- | --- |
| BANK-A (local only) | 0.972 | 0.708 | 0.072 | 0.867 |
| BANK-B (local only) | 0.966 | 0.675 | 0.041 | 0.840 |
| BANK-C (local only) | 0.963 | 0.774 | 0.008 | 0.933 |
| BANK-D (local only) | 0.975 | 0.657 | 0.100 | 0.853 |
| BANK-E (local only) | 0.966 | 0.709 | 0.018 | 0.920 |
| Mean of 5 **local** models | 0.969 | 0.704 | 0.048 | 0.883 |
| **Global FedAvg** | **0.990** | **0.793** | **0.041** | **0.907** |

`global_beats_mean_local_auc = true`. **Correction (October 2026):** earlier
versions of this table (global AUC 0.990, PR-AUC 0.926) were measured on a
truncated download — 89,900 of the 284,807 rows, then capped to 30k rows
keeping every fraud, which enriched the fraud rate roughly 10x and inflated
PR-AUC and precision. The loader now refuses an incomplete file, and these
numbers come from the full, uncapped dataset at its real fraud rate. The very
low precision at a fixed 0.5 threshold is that real base rate showing through;
AUC and PR-AUC are the headline metrics. See `docs/SECURITY.md` §5.
Reproduce with `python -m aris.fl.run --dataset ulb --rounds 5 --epochs 2`
(the first run downloads ~150 MB to `data/raw/`).

Tests: `pytest` → **188 passed** (M0 + M1 + M2), including `test_synthetic_global_beats_mean_local_auc`. M0 Anu demo still blocks ACC-999.

#### How to run M1

From the repo root, with dependencies installed (`pip install -r requirements.txt` and `pip install -e .`, or set `PYTHONPATH=src`):

```bash
python -m aris.fl.run --dataset synthetic --banks 5 --rounds 8 --epochs 4
python -m aris.fl.run --dataset ulb --banks 5 --rounds 5 --epochs 2
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
| 0.5 | 364.6 | 0.623 |
| 1.0 | 118.3 | 0.623 |
| 2.0 | 43.1 | 0.623 |
| 4.0 | 17.6 | 0.619 |
| 8.0 | 7.8 | 0.617 |
| 15.0 | 3.9 | 0.616 |
| 30.0 | 1.9 | 0.605 |
| 60.0 | 0.9 | 0.522 |

Re-measured after the October 2026 fix that made the DP-SGD class weight a
public hyperparameter (`TrainConfig.dp_pos_weight`, default 1.0) instead of one
derived from each client's private labels — the label-derived weight broke the
per-example sensitivity the accountant assumes, so the earlier epsilons were
understated (`docs/SECURITY.md` §5). Since the noise is genuinely random rather
than seed-derived, individual rows fluctuate run to run: across 20 repeated
runs at `noise_multiplier=8.0`, AUC stayed within 0.608–0.633 (mean 0.622),
clear of both M1's mean-of-local baseline (0.572) and the 60.0 tail. The trend,
not any single row, is the result: AUC degrades gracefully as epsilon shrinks
and stays better than a coin flip even at sub-1 epsilon.

#### How to run M2

```bash
python -m aris.fl.run --dataset synthetic --dp --noise-multiplier 4.0 --max-grad-norm 5.0
python -m aris.fl.privacy_sweep --dataset synthetic
```

Writes `data/processed/m1_metrics_synthetic.json` (single run, `privacy.differential_privacy` block) and `data/processed/m2_privacy_utility_synthetic.json` (the sweep table). Add `--secure-agg` to the single run to aggregate through pairwise masking instead of plain FedAvg.

### M3 — Kafka risk bus (completed)

**Goal met:** `KafkaRiskBus` implements the same `RiskBus` interface as `InMemoryRiskBus`, over a real broker. Bank B publishes; Bank A, a *separate* `KafkaRiskBus` instance sharing nothing but the broker and registry, sees the same `risk_id` once its background consumer catches up.

#### What shipped

| Piece | Where | What it does |
| --- | --- | --- |
| Kafka-backed bus | `src/aris/kafka_bus.py` | `KafkaRiskBus(RiskBus)` -- publishes to a retention-based topic, consumes into a local materialized view |
| Schema Registry client | `src/aris/schema_registry.py` | Confluent wire-format encode/decode + REST registration, over plain HTTP (no `confluent-kafka`/librdkafka dependency) |
| Local dev broker | `docker-compose.yml` | Kafka (KRaft, `apache/kafka`) + Karapace (Confluent-API-compatible Schema Registry, chosen over `cp-schema-registry` for image size) |

**Architecture: reuse, don't reimplement.** `KafkaRiskBus.publish()` verifies and applies a signal to an internal `InMemoryRiskBus` first -- getting every M0 admission, replay, and quota rule for free -- then produces it to the `risk-signals` topic (key `f"{risk_id}:{bank}"`; the topic uses time-based retention, not log compaction, because under compaction any member with Write could erase a peer's flag by appending a record under its key -- see docs/SECURITY.md §5). A background thread consumes the same topic into that same local view through the identical `publish()` call, including this process's own records, which the existing replay guard naturally no-ops on.

**Updated in M6+ (SECURITY.md §3.4):** the paragraph above described M3 as shipped -- full-topic replication, so `lookup()` never blocked on the network. That closed one leak (a bus operator correlating per-query traffic) by opening a bigger one: every member bank's consumer downloaded every *other* bank's complete published `risk_id` set, not just the accounts it actually queried. `KafkaRiskBus` now partitions the topic by `risk_id` prefix and each instance consumes only the buckets it has actually needed -- see the M6+ phase log below for what that trades away (a cold lookup now costs a real network round trip, bounded and reported as `UNAVAILABLE` on timeout rather than silently).

**Message authenticity survives the transport.** A record that doesn't verify -- forged, or written directly to the topic bypassing `publish()` -- is rejected on consume the same way it would be on publish, by every reader independently.

**Updated in M6+ (SECURITY.md §3.8):** transport-level access control (mTLS, per-bank ACLs) was a separate, open gap at M3 -- `docker-compose.yml` now also serves an `SSL_HOST` listener with mTLS and per-principal ACLs alongside the original plaintext one. See the M6+ phase log below for what that closes, the trade-offs it makes, and how to run it.

Tests: `tests/test_kafka_bus.py` — cross-process visibility, forged-publisher rejection, replay-after-consume, and consumer resilience to an untrusted record — **verified passing against a live broker** (`docker compose up -d`, then `pytest tests/test_kafka_bus.py`: 5 passed). Skips cleanly with a clear reason, not silently, when no broker is reachable — e.g. in CI, which does not run Kafka.

#### How to run M3

```bash
docker compose up -d          # Kafka + Schema Registry
pip install -e ".[dev,kafka]"
pytest tests/test_kafka_bus.py -v
```

For the mTLS + per-bank ACL listener M6+ adds (`docs/SECURITY.md` §3.8):

```bash
python scripts/generate_dev_certs.py    # local dev CA + per-bank certs, gitignored
docker compose up -d                    # now serves SSL_HOST on :9093 too
bash scripts/setup_kafka_acls.sh        # optional -- tests/test_kafka_mtls.py grants its own
pytest tests/test_kafka_mtls.py -v
```

For the HSM-resident key (`docs/SECURITY.md` §3.6):

```bash
brew install softhsm      # or: apt install softhsm2
pip install -e ".[dev,hsm]"
pytest tests/test_hsm.py -v    # provisions its own throwaway SoftHSM2 token
```

### M4 — BankBot API (completed)

**Goal met:** the Anu story runs over real HTTP — `POST /transfers` — against either bus backend from M3, with the same no-score-oracle customer response and the same idempotent-on-`transfer_id` behavior as the CLI demo.

#### What shipped

| Piece | Where | What it does |
| --- | --- | --- |
| FastAPI app | `src/aris/api/app.py` | `POST /transfers`, `GET /audit/{ref}`, `GET /health` — a thin HTTP wrapper; every decision still comes from `aris.bankbot.BankBot` |
| Config | `src/aris/api/config.py` | Env-driven: bus backend, policy thresholds, analyst admin key |
| Dev server | `python -m aris.api` | Runs against either bus backend |

**Analyst audit access is real, not a stub.** `GET /audit/{audit_ref}` is the "authenticated analyst path" `BankBotDecision`'s docstring already promised — it needed `InMemoryAuditLog` to gain an actual `get()` lookup (previously only a linear `.entries` scan existed), now O(1) and keeping pace with eviction. The endpoint checks a shared admin key with `hmac.compare_digest` (constant-time) and fails closed — 503, not "auth optional" — when no key is configured.

**Step-up auth is an honest stub.** `POST /transfers` reports `step_up_required: true` and stops there. A real deployment already has its own OTP/step-up flow; faking one here would be exactly the kind of plausible-looking fake logic this project's standards reject, so the response is honest about the gap instead of pretending to close it.

**A real FastAPI/`from __future__ import annotations` gotcha, found and fixed.** The first draft used `Depends()` closures returning `app.state.bot` etc; every one silently misrouted as a query parameter instead of raising, because postponed annotation evaluation means FastAPI resolves `Annotated[X, Depends(f)]` against the route function's *module* globals, and a closure local to a factory function was never there to find. Routes now take `request: Request` and read `request.app.state` directly, which doesn't depend on runtime annotation resolution at all.

`tests/test_api.py` covers the HTTP layer against `InMemoryRiskBus` (10 tests, always runs). `tests/test_api_kafka.py` re-runs the same Anu scenario — including a second bus/app instance standing in for Bank A's own process — against a live Kafka bus: **verified passing** (`docker compose up -d`, then `pytest tests/test_api_kafka.py`: 2 passed). With the broker up, `pytest` end to end → **236 passed, 0 skipped**; without it, the 6 Kafka-dependent M3/M4 tests skip with a clear reason instead of silently.

#### How to run M4

```bash
ARIS_DEV_MODE=1 python -m aris.api                       # in-memory bus, port 8000
ARIS_API_BUS_BACKEND=kafka ARIS_DEV_MODE=1 python -m aris.api   # needs `docker compose up -d`
```

### M5 — Explainability & MLOps (completed)

**Goal met:** a blocked transfer's reason codes trace back to real SHAP attribution on the model that produced the score, drift between training and production data is quantified rather than assumed, and multiple trained checkpoints coexist under an explicit active/rollback pointer instead of one file silently overwriting the last.

#### What shipped

| Piece | Where | What it does |
| --- | --- | --- |
| Explainability | `src/aris/fl/explain.py` | `FraudExplainer` — permutation SHAP over `FraudScorer`, mapped to `reason_codes` |
| Drift monitoring | `src/aris/fl/drift.py` | `check_drift` — Evidently `DataDriftPreset` (K-S test), reference vs. current batch |
| Model registry | `src/aris/fl/registry.py` | `ModelRegistry` — versioned checkpoints, active pointer, atomic-write manifest; `should_rollback` criteria |

**Honesty note on reason codes.** Mapping a feature to a named fraud pattern (`high_velocity`, `new_beneficiary`, ...) requires knowing what the feature represents. Neither dataset this repo trains on supports that in general — the synthetic dataset's features are anonymous by construction, and ULB's are PCA-anonymized for the same privacy reasons this whole project cares about. `FraudExplainer` takes an explicit, caller-supplied `reason_code_map`; an unmapped top-contributing feature reports the honest thing (which feature, how much it moved the score) via a generic fallback code, rather than inventing domain meaning a feature doesn't have.

**Verified against ground truth, not just "runs without crashing."** `make_synthetic`'s fraud direction is known by construction (bank *i*'s fraud depends on feature `f{i % 8}`). `FraudExplainer` correctly identifies that exact feature as the dominant SHAP contributor for a bank's top-scored row (`shap=0.639` for `f0` vs. `0.006` for the next-largest — see `tests/test_fl_explain.py`). `check_drift` correctly flags only a feature that was actually shifted (mean +3, K-S p≈0) and correctly clears every unshifted one (`tests/test_fl_drift.py`).

Tests: `tests/test_fl_explain.py`, `tests/test_fl_drift.py`, `tests/test_fl_registry.py` — **28 passed**, including an end-to-end trace from SHAP attribution through `RiskSignal`'s own token validator to an actual BankBot `BLOCK` decision.

#### How to run M5

```bash
pip install -e ".[dev,ml,xai]"
pytest tests/test_fl_explain.py tests/test_fl_drift.py tests/test_fl_registry.py -v
```

### M6+ — Scale, Robustness & Graph Engine (completed)

**Shipped:** On-prem graph and receiver velocity feature extraction engine, Byzantine-robust aggregation (Krum, coordinate-median), OPRF blind-signature integration in BankBot, bus load testing, and comprehensive automated M0–M6 reporting dashboard.

| Piece | Where | What it does |
| --- | --- | --- |
| Graph & Velocity Engine | `src/aris/fl/graph_features.py` | `ReceiverVelocityFeatureEngine`, `GraphFeatureExtractor`, `ReceiverTransactionHistoryGenerator` — computes in-degree, out-degree, degree centrality, counterparty ratio, and sliding time-window velocities (1h, 24h, 7d counts/sums) on-prem |
| Multi-Bank Graph Stream | `src/aris/fl/datasets.py` | `make_multi_bank_graph_stream` — generates authentic multi-bank transaction stream with linked account graph histories and fraud mule burst patterns across distinct banks |
| FL Graph Integration | `src/aris/fl/partition.py`, `run.py` | Adds `--use-graph-features` flag to Flower federated training; enriches base transactions with 10 on-prem graph/velocity features |
| Robust aggregation | `src/aris/fl/robust_agg.py` | Krum, coordinate-median — alternatives to FedAvg's weighted mean, selectable via `TrainConfig.aggregation_strategy` |
| Bus load test | `src/aris/loadtest.py` | Concurrent publish + lookup against either bus backend; measures throughput, latency, and correctness under contention |
| OPRF BankBot Integration | `src/aris/bankbot.py` | BankBot pre-transaction pipeline optionally queries blinded RSA-FDH token authority before bus queries (`use_oprf=True`) |
| Unified Reporting Suite | `scripts/generate_all_reports.py` | Consolidates M0 through M6 benchmarks into standalone markdown/json files and `reports/M0_TO_M6_COMPREHENSIVE_DASHBOARD.md` |

**On-prem graph and receiver-velocity features, verified against fraud mule ring detection:**
In real fraud operations, accounts act as mule sinks receiving funds in rapid bursts from multiple compromised accounts across different banks. The new engine computes account-level graph topology metrics (in-degree, out-degree, distinct sender ratio) and temporal velocity windows (1-hour, 24-hour, and 7-day volume and count spikes) strictly on-prem within each bank. When combined with federated learning (`python -m aris.fl.run --dataset synthetic --use-graph-features`), the global model achieves **ROC-AUC = 0.9998** on multi-bank graph streams, detecting complex cross-bank mule patterns without sharing customer graph structures.

**Robust aggregation, verified against an actual attack.** A synthetic Byzantine client — ignores local training, sends a -1000x-scaled update, *and* lies about its declared example count (the more realistic and more damaging version of the attack, since FedAvg's weighted mean trusts that count with no way to verify it) — present every round for 6 rounds: plain FedAvg's resulting model degrades to near-random (AUC < 0.55); Krum, which ignores declared counts entirely and selects the single update closest to its peers, keeps learning under the identical attack (AUC > 0.55). See `tests/test_fl_robust_agg.py`.

**Bus load test, measured on both backends.** 20 concurrent publishers/lookups against `InMemoryRiskBus`: **3,517 signals/s**, 0 lost updates, lookup latency pinned near zero. 10 against a live `KafkaRiskBus`: **564 signals/s** (dominated by `acks="all"`'s broker round trip — the right tradeoff for a fraud signal), 0 lost updates; lookup latency is no longer flat zero (mean 8.1ms, p99 448ms) since §3.4's prefix-bucketed consumption (below) means a lookup against a bucket this instance hasn't warmed yet pays a real, bounded network round trip rather than reading an already-fully-replicated local view. Quota enforcement verified exact under concurrent contention (10 publishers × 20 signals against a 50-entry cap admits exactly 50, rejects exactly 150, every run). Full numbers, tail-latency analysis, and known limits: [`docs/LOADTEST.md`](docs/LOADTEST.md).

**All 8 SECURITY.md priority-list items shipped** (§4), including §3.7's encrypted, access-controlled audit log. The OPRF (§3.1) and HSM key (§3.6) are wired into `BankBot` and the API through `aris/risk_id.py` (`ARIS_API_RISK_ID_MODE=hmac|hsm|oprf`), with the OPRF authority runnable as its own service (`python -m aris.oprf_service`) and verified live across three processes by `scripts/e2e_live_oprf.py`. §3.6 keeps a stated hardware gap (SoftHSM2, not a physical HSM).

- **3.5 — quantised timestamp, rounded confidence.** `RiskSignal.timestamp` floors to a 1-minute bucket and `confidence` rounds to the nearest 0.05 at construction, so neither survives on the bus as a linkage tag precise enough to correlate a signal to a specific real-world event.
- **3.3 — `risk_id` keyed on `(IFSC, account)`.** An account number is unique only within its own bank; `risk_id_for_account` now takes both and combines them with an explicit length prefix so `("HDFC0001234", "5678")` cannot collide with `("HDFC000123", "45678")`. Breaking change: `POST /transfers` now requires `receiver_ifsc`.
- **3.2 — daily key-rotation epochs + canary.** `risk_id` is derived under an HMAC subkey for the current UTC day, itself a one-way function of `ARIS_SALT` — a leaked day's subkey exposes neither the root key nor any other day's. `BankBot` looks up the current epoch, then falls back to the previous one, so a signal published just before a UTC-day boundary is still found just after it. `aris/canary.py` publishes and checks a reserved per-epoch marker signal so a node whose key has drifted out of sync fails one well-known check instead of silently missing every real signal.
- **3.4 — prefix-bucketed Kafka consumption.** `KafkaRiskBus` partitions `risk-signals` into 256 buckets by `risk_id` prefix and each instance consumes only the buckets it has actually published to or looked up, verified directly (`tests/test_kafka_bus_bucketing.py`) by confirming an instance that never asks about a bucket does not have its data.
- **3.1 — OPRF-equivalent `risk_id` derivation.** `aris/oprf.py`: an RSA-FDH blind signature (Chaum 1982 + MGF1 full-domain hash). A bank cannot compute `risk_id` without the authority's per-query cooperation, which is blinded, rate-limited per bank, and logged. Fully integrated into `BankBot(use_oprf=True)` with client blinding and authority unblinding.
- **3.8 — mTLS + per-bank Kafka ACLs.** `docker-compose.yml` serves an `SSL_HOST` listener (`:9093`) with local CA + per-bank client certs, `ssl.client.auth=required`, and `StandardAuthorizer` per-principal ACLs (`scripts/setup_kafka_acls.sh`). Verified end to end (`tests/test_kafka_mtls.py`).
- **3.6 — HSM-resident key, via a real PKCS#11 token.** `aris/hsm.py` generates the consortium key *inside* a PKCS#11 token (`CKA_SENSITIVE` + `CKA_EXTRACTABLE=false`) so it can be used (`C_Sign`) but never read back out — verified against SoftHSM2 (`tests/test_hsm.py`).

Tests: full suite — **337+ passed** (including `tests/test_fl_graph_features.py`, `tests/test_fl_robust_agg.py`, `tests/test_loadtest.py`, `tests/test_hashing.py`, `tests/test_bankbot.py`, `tests/test_canary.py`, `tests/test_kafka_bus_bucketing.py`, `tests/test_oprf.py`, `tests/test_kafka_mtls.py`).

#### How to run

```bash
pytest tests/test_fl_graph_features.py tests/test_fl_robust_agg.py tests/test_loadtest.py -v
python -m aris.fl.run --dataset synthetic --use-graph-features --banks 5 --rounds 6
python -m aris.loadtest --backend memory --publishers 20 --signals 200
python scripts/generate_all_reports.py
```

## Frontend — the reviewer portal

`frontend/app` (Vite + React + TypeScript) uses the design system's tokens in
`frontend/aris-design-system`. Four pages: **Overview**, **Live demo** (the
Bank B → Bank A scenario, a sandbox, a decision log and investigator access,
all against the real BankBot API), **Model** (per-bank and federated results,
ROC, training and privacy measurements) and **Security** (the eight controls
with stated limits). Every figure
comes from `frontend/app/public/data/metrics.json`, measured by one script on
one commit.

```bash
# one-time
cd frontend/app && npm ci && cd ../..

# build the app, then serve it plus the live demo backend on http://127.0.0.1:8000
(cd frontend/app && npm run build)
python -m aris.demo.server            # prints the analyst-view investigator key

# re-measure everything the page shows (trains, sweeps, load tests, full test suite)
python scripts/export_frontend_data.py
```

For frontend development, run `python -m aris.demo.server` and `npm run dev`
(Vite proxies `/api` and `/demo` to it). Tests: `npm test` (Vitest, unit and
component) and `npm run test:e2e` (Playwright against the real showcase server
serving the production build — story flows, analyst view, measured values,
axe accessibility in both themes, 375px layout, offline behaviour). Both run in CI.

## Domain split (quick)

| AI-1 (FL) | AI-2 (score + XAI) | Backend |
| --- | --- | --- |
| Flower, shards, FedAvg, DP | Scorer, reason codes, SHAP, drift | Hashing, attestation, Kafka, FastAPI BankBot, audit, auth |

## End-to-end story (demo target)

1. Bank B scores receiver `ACC-999` as risk **92** and publishes `HMAC-SHA256(key, ifsc||account)` to the bus, signed with Bank B's key -- keyed on `(IFSC, account)`, not the account alone (`docs/SECURITY.md` §3.3).
2. Anu (Bank A) asks BankBot to send ₹5,000 to `ACC-999` at Bank B's IFSC.
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

Every stack past M0 is an optional extra, not installed by default: `pip install -e ".[ml]"` for M1/M2, `pip install -e ".[kafka]"` for M3, `pip install -e ".[api]"` for M4, `pip install -e ".[xai]"` for M5, `pip install -e ".[hsm]"` for SECURITY.md §3.6. `pip install -e ".[dev,ml,kafka,api,xai,hsm]"` gets everything.

## Layout

```
src/aris/
  hashing.py         risk_id derivation (HMAC-SHA256, key-derived, ASCII-only)
  attestation.py     Ed25519 signing / verification of published signals
  schema.py          RiskSignal, PolicyConfig, apply_policy — the shared contract
  bus.py             RiskBus interface + in-memory implementation
  kafka_bus.py       M3: RiskBus over a real Kafka broker
  schema_registry.py M3: Confluent-wire-format schema registry client
  bankbot.py         pre-transaction check, decisions, audit records
  api/               M4: FastAPI wrapper (POST /transfers, GET /audit/{ref})
  loadtest.py        M6+: concurrent publish/lookup load test, either bus backend
  demo/              the Anu / ACC-999 walkthrough
  fl/                M1: clients, FedAvg, datasets, metrics, scorer
                     M2: privacy.py (DP-SGD + accountant), secure_agg.py, privacy_sweep.py
                     M5: explain.py (SHAP), drift.py (Evidently), registry.py (versions/rollback)
                     M6+: robust_agg.py (Krum, coordinate-median)
docs/                project report, phases, team split, security model, load test results
data/raw/            CSVs (not committed)
data/processed/      metrics, weight checkpoints, model registry manifest (not committed)
tests/               hashing, attestation, schema/policy, bus, bankbot, demo, fl, api, kafka, loadtest
```
