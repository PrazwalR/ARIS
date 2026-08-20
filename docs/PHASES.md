# ARIS implementation phases

Each phase has a **goal**, **deliverables**, and a **done when** check. We implement in this order so the Anu / ACC-999 story works as soon as M0, then we replace stubs with real FL and Kafka.

---

## M0 — Foundation (**completed**)

**Lead:** shared (all three). **Next:** M1 (AI-1 lead).

**Goal:** One repo, one privacy contract, one runnable demo of the Shared Risk-Signal Bus + BankBot policy—without Flower or Kafka yet.

**Deliverables**

- Hashed risk ID: `risk_id = SHA256(account ∥ SALT)`
- In-memory risk bus (same message shape as Kafka later)
- Policy: low allow / medium step-up / high block
- BankBot `pre_transaction` hook + audit log
- Demo: Bank B publishes ACC-999; Anu’s transfer is blocked

**Done when:** `python -m aris.demo.anu_transfer` prints a block decision with score 92 and hashed ID (never the raw account on the bus).

---

## M1 — Federated learning (3–5 banks)

**Lead:** AI-1. **Support:** AI-2 (baseline + metrics), Backend (data scripts / bank IDs).

**Goal:** Simulated banks keep local shards; Flower aggregates a global fraud model (FedAvg). Compare local-only vs federated.

**Deliverables**

- Dataset adapters: ULB Credit Card Fraud first (smallest), then PaySim / IEEE-CIS
- Non-IID shards (by bank / merchant / time)
- Flower server + client strategy
- Metrics: AUC, PR-AUC, recall@precision, false-positive rate

**Done when:** A global model beats the average of local models on a held-out shard, with no raw rows leaving clients.

---

## M2 — Privacy on training

**Lead:** AI-1. **Support:** AI-2 (utility), Backend (artifact/logging rules).

**Goal:** Model updates are harder to invert; measure the utility cost.

**Deliverables**

- DP-SGD (Opacus) or Flower DP strategy
- Optional secure aggregation
- Privacy–utility table (ε vs AUC)

**Done when:** Training still converges with a documented ε, and we can show the accuracy drop vs M1.

---

## M3 — Shared Risk-Signal Bus (Kafka)

**Lead:** Backend. **Support:** AI-2 (model → `RiskSignal`), AI-1 (`model_version`).

**Goal:** Replace the in-memory bus with Kafka `risk-signals`, schema, TTL.

**Deliverables**

- `docker-compose` Kafka (+ Schema Registry)
- Avro/JSON schema matching the JSON in the project report
- Publisher from a bank “fraud scorer”
- Query/lookup service (compacted topic or sidecar store keyed by `risk_id`)
- Auth placeholders: mTLS / ACL notes (real PKI later)

**Done when:** Bank B can publish and Bank A can consume the same `risk_id` across processes.

---

## M4 — BankBot in the transfer path

**Lead:** Backend. **Support:** AI-2 (reason codes), AI-1 (local-model fallback).

**Goal:** Production-shaped API: parse intent → hash → query bus → policy → user message.

**Deliverables**

- FastAPI (or CLI) “Send ₹5,000 to ACC-999”
- Thresholds configurable (e.g. block if score > 85)
- Step-up auth stub for medium risk
- Audit log: decision, score, reason codes, model version

**Done when:** The Anu example works over HTTP with the Kafka bus from M3.

---

## M5 — Explainability and MLOps

**Lead:** AI-2. **Support:** AI-1 (registry/rollback), Backend (metrics + audit fields).

**Goal:** Auditable “why” plus drift/rollback.

**Deliverables**

- SHAP or LIME on local scoring
- Map feature attributions → reason codes (`new_beneficiary`, `high_velocity`, …)
- Evidently / Prometheus metrics; model version + rollback

**Done when:** A blocked transfer has reason codes a bank analyst can defend.

---

## M6+ — Scale and robustness

**Lead:** AI-1. **Support:** AI-2 (graph features), Backend (bus load test).

**Goal:** More banks, graph features, poisoning-resistant aggregation.

**Deliverables**

- Graph/receiver-velocity features still computed **on-prem**
- Robust aggregation (e.g. Krum / median) experiments
- Load test the bus

**Done when:** Documented limits and attack notes for a report/defense.

---

## Suggested next session

**M0 is done.** Start **M1**: AI-1 Flower clients on ULB shards; AI-2 baseline + metrics template; Backend dataset/config only. When a phase closes, append what shipped to the Phase log in `README.md`.
