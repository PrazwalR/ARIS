# ARIS implementation phases

Each phase has a **goal**, **deliverables**, and a **done when** check. We implement in this order so the Anu / ACC-999 story works as soon as M0, then we replace stubs with real FL and Kafka.

---

## M0 — Foundation (**completed**)

**Lead:** shared (all three). **Next:** M1 (AI-1 lead).

**Goal:** One repo, one privacy contract, one runnable demo of the Shared Risk-Signal Bus + BankBot policy—without Flower or Kafka yet.

**Deliverables**

- Keyed risk ID: `risk_id = HMAC-SHA256(consortium_key, normalize(account))`, loaded
  fail-closed (no key configured → raises, never a default)
- Ed25519 publisher attestation: signals are signed and verified against a consortium
  keyring, so a member cannot publish under a peer's name
- In-memory risk bus (same message shape as Kafka later): per-bank signals aggregated
  by highest live score, TTL expiry, replay rejection, per-publisher flood quotas
- Policy: low allow / medium step-up / high block, plus step-up on large amounts, a
  confidence floor before blocking, and a bus outage that resolves to step-up rather
  than allow
- BankBot `pre_transaction` hook, idempotent on `transfer_id`, plus a bounded audit log
  with ledger-reconcilable fields that counts anything it drops
- Demo: Bank B publishes ACC-999; Anu’s transfer is blocked

**Done when:** `python -m aris.demo.anu_transfer` prints a block decision with score 92
and a hashed ID (never the raw account on the bus), and a hostile member's attempt to
retract another bank's flag is rejected. ✅ Met.

---

## M1 — Federated learning (3–5 banks) (**completed**)

**Lead:** AI-1. **Support:** AI-2 (baseline + metrics), Backend (data scripts / bank IDs). **Next:** M2 (AI-1 lead).

**Goal:** Simulated banks keep local shards; Flower aggregates a global fraud model (FedAvg). Compare local-only vs federated.

**Deliverables**

- Dataset adapters: ULB Credit Card Fraud first (smallest), then PaySim / IEEE-CIS
- Non-IID shards (by bank / merchant / time)
- Flower server + client strategy
- Metrics: AUC, PR-AUC, recall@precision, false-positive rate

**Done when:** A global model beats the average of local models on a held-out shard, with no raw rows leaving clients.

---

## M2 — Privacy on training (**completed**)

**Lead:** AI-1. **Support:** AI-2 (utility), Backend (artifact/logging rules). **Next:** M3 (Backend lead).

**Goal:** Model updates are harder to invert; measure the utility cost.

**Deliverables**

- DP-SGD, implemented directly in NumPy (per-example gradient clipping + Gaussian
  noise) rather than via Opacus/PyTorch — M1 deliberately has no torch dependency,
  and Opacus requires it. A zCDP accountant (Bun & Steinke 2016) converts
  (noise multiplier, step count) to (epsilon, delta); it does not credit
  subsampling amplification, so reported epsilon is a conservative upper bound.
- Secure aggregation: pairwise-masked sum (Bonawitz et al. 2017, single-process
  simulation), numerically identical to plain FedAvg, opt-in and independent of DP.
- Privacy–utility table (ε vs AUC): `python -m aris.fl.privacy_sweep`.

**Post-completion audit fix:** an adversarial review of the first implementation
found the DP noise was seeded from `TrainConfig.seed` — the same value the
orchestrating/aggregating code already holds, making the noise reconstructible
by exactly the party the epsilon guarantee is meant to protect against, and the
reported epsilon meaningless. Fixed: `train_local_dp` now sources noise from OS
entropy by default; only example-shuffle order stays seed-reproducible. See the
README M2 phase log for the corrected numbers and full explanation.

**Done when:** Training still converges with a documented ε, and we can show the
accuracy drop vs M1. ✅ Met — synthetic dataset holds AUC in the 0.55–0.64 range
(vs 0.655 non-DP, 0.572 M1 mean-of-local baseline) across ε from 365 down to 0.9,
with individual runs now genuinely random rather than seed-derived. See the
README M2 phase log for the full table.

---

## M3 — Shared Risk-Signal Bus (Kafka) (**completed**)

**Lead:** Backend. **Support:** AI-2 (model → `RiskSignal`), AI-1 (`model_version`). **Next:** M4 (Backend lead).

**Goal:** Replace the in-memory bus with Kafka `risk-signals`, schema, TTL.

**Deliverables**

- `docker-compose` Kafka (KRaft, `apache/kafka`) + Schema Registry (Karapace --
  Confluent-API-compatible, chosen over `confluentinc/cp-schema-registry` for
  image size; the wire protocol is identical).
- JSON Schema (from `RiskSignal.model_json_schema()`) registered with the
  registry over its plain REST API -- `kafka-python` stays pure-Python, no
  `confluent-kafka`/librdkafka dependency.
- `KafkaRiskBus.publish()`: a bank's publisher, wired straight onto the same
  `RiskBus` interface `InMemoryRiskBus` implements.
- Query/lookup service: each `KafkaRiskBus` instance's background consumer
  thread builds a local materialized view (an internal `InMemoryRiskBus`) from
  the compacted topic; `lookup()` reads that, never the network.
- Auth placeholders: no SASL/mTLS/ACLs in the local-dev compose file by design
  (see `docs/SECURITY.md` §3.8 for the concrete `kafka-acls.sh` a real
  deployment needs) -- message-level Ed25519 verification still applies on
  every consumed record regardless, independent of transport auth.

**Done when:** Bank B can publish and Bank A can consume the same `risk_id`
across processes. ✅ Met -- verified against a live broker: a second
`KafkaRiskBus` instance, sharing nothing but the broker/registry, sees Bank B's
signal via its own `lookup()` once its consumer thread catches up. See the
README M3 phase log.

---

## M4 — BankBot in the transfer path (**completed**)

**Lead:** Backend. **Support:** AI-2 (reason codes), AI-1 (local-model fallback). **Next:** M5 (AI-2 lead).

**Goal:** Production-shaped API: parse intent → hash → query bus → policy → user message.

**Deliverables**

- FastAPI `POST /transfers` ("Send ₹5,000 to ACC-999") -- `src/aris/api/app.py`,
  a thin wrapper: every decision still comes from `aris.bankbot.BankBot`.
- Thresholds configurable (defaults: block at score >= 85, step-up at >= 50) --
  via `ARIS_API_BLOCK_AT` / `ARIS_API_STEP_UP_AT` / etc. (`src/aris/api/config.py`).
- Step-up auth stub for medium risk -- `POST /transfers` reports
  `step_up_required: true` honestly and does not simulate completing a
  challenge flow a real deployment already owns.
- Audit log: decision, score, reason codes, model version -- already captured
  by M0's `AuditRecord`; M4 adds the missing piece, an authenticated retrieval
  path (`GET /audit/{audit_ref}`, admin-key gated, `hmac.compare_digest`).

**Done when:** The Anu example works over HTTP with the Kafka bus from M3.
✅ Met -- `tests/test_api_kafka.py` runs the same blocked-transfer scenario
through `POST /transfers` against a live `KafkaRiskBus`, including a second
bus/app instance standing in for Bank A's own process. See the README M4
phase log.

---

## M5 — Explainability and MLOps (**completed**)

**Lead:** AI-2. **Support:** AI-1 (registry/rollback), Backend (metrics + audit fields). **Next:** M6+ (AI-1 lead).

**Goal:** Auditable “why” plus drift/rollback.

**Deliverables**

- SHAP on local scoring -- `src/aris/fl/explain.py::FraudExplainer`, permutation
  SHAP over `FraudScorer` (model-agnostic: the scorer wraps a custom NumPy MLP,
  not a SHAP-native model type).
- Map feature attributions → reason codes -- via an explicit, caller-supplied
  map, not an invented one: neither dataset this repo trains on has
  semantically meaningful feature names (synthetic is anonymous by
  construction; ULB is PCA-anonymized for the same privacy reasons the rest of
  this project cares about), so an unmapped top feature reports the honest
  thing (which feature, how much it moved the score) via a generic fallback
  code instead of a fabricated domain claim.
- Evidently metrics -- `src/aris/fl/drift.py::check_drift`, Kolmogorov-Smirnov
  drift per feature (`DataDriftPreset`), reference vs. current batch.
  (Prometheus/Grafana endpoints not built -- see M6+.)
- Model version + rollback -- `src/aris/fl/registry.py::ModelRegistry`:
  versioned checkpoints, an explicit active pointer, an atomically-written
  manifest, and `should_rollback(candidate, active)` criteria (regression
  beyond a tolerance on a named metric, e.g. AUC).

**Done when:** A blocked transfer has reason codes a bank analyst can defend.
✅ Met -- verified against ground truth, not just "runs without crashing":
`make_synthetic`'s fraud direction is known by construction, and
`FraudExplainer` correctly identifies the true driving feature as the dominant
SHAP contributor (`tests/test_fl_explain.py`); an end-to-end test traces that
attribution through `RiskSignal`'s own token validator to an actual BankBot
`BLOCK` decision. See the README M5 phase log.

---

## M6+ — Scale and robustness (**in progress**)

**Lead:** AI-1. **Support:** AI-2 (graph features), Backend (bus load test).

**Goal:** More banks, graph features, poisoning-resistant aggregation.

**Deliverables**

- Graph/receiver-velocity features still computed **on-prem** -- **not
  started.** Needs an account-level transaction *history*; neither dataset
  this repo trains on has one (the synthetic generator and ULB/PaySim/IEEE-CIS
  loaders all produce one row per transaction with no linked prior activity
  for the same receiver), so this needs new synthetic-data generation work,
  not just a feature-engineering pass on what's already loaded.
- Robust aggregation (Krum / coordinate-median) -- **done.**
  `src/aris/fl/robust_agg.py`, wired into `TrainConfig.aggregation_strategy`.
  Verified against an actual attack (a Byzantine client lying about both its
  update's values and its declared example count, present every round):
  degrades plain FedAvg to near-random, Krum keeps learning under the
  identical attack. See the README M6+ phase log.
- Load test the bus -- **done.** `src/aris/loadtest.py`, measured against
  both `InMemoryRiskBus` and a live `KafkaRiskBus`: see
  [`docs/LOADTEST.md`](LOADTEST.md) for numbers, and known limits.

**Done when:** Documented limits and attack notes for a report/defense.
Partially met -- robust aggregation and the load test both have real,
measured, documented results; graph/velocity features remain open, and so
does the mTLS/ACL hardening `docs/SECURITY.md` §3.8 already flags.

---

## Suggested next session

**M0–M5 are done; M6+ is partial** (robust aggregation and bus load testing
shipped; graph/velocity features and mTLS/ACL hardening remain). Next: AI-2
graph/velocity features (needs new synthetic transaction-history generation
first); Backend the mTLS/ACL hardening flagged in `docs/SECURITY.md` §3.8.
When a phase closes, append what shipped to the Phase log in `README.md`.
