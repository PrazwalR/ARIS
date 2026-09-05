# Team split — 2 AI, 1 backend

Three owners. Shared contract: `src/aris/schema.py` (`RiskSignal` JSON). Nobody ships a phase without updating the **Phase log** in `README.md`.

| Role | Owns | Does not own |
| --- | --- | --- |
| **AI-1 — Federated learning** | Flower/NVFlare, bank shards, FedAvg, DP training, model metrics | Kafka, FastAPI, docker-compose |
| **AI-2 — Scoring & explainability** | Local fraud scorer, reason codes, SHAP/LIME, drift, model versions | Bus ACLs, BankBot HTTP routes |
| **Backend — Platform** | Hashing/KMS salt, risk bus, lookup API, BankBot API, policy, audit, auth | Training loops, SHAP plots |

---

## How work flows (every transfer)

```
AI-1 trains global model (on-prem shards)
        ↓
AI-2 scores a receiver → risk_score, confidence, reason_codes
        ↓
Backend hashes ACC → risk_id, publishes to bus, BankBot queries + policy
```

AI never puts a plain account number on the bus. Backend never retrains the model.

---

## Phase-by-phase ownership

### M0 Foundation — **done** (all three, shared)

Already in repo: keyed hashing, Ed25519 publisher attestation, in-memory bus, policy,
BankBot demo, Anu story. 124 tests, ruff + `mypy --strict` clean. Threat model and known
limitations are written up in [SECURITY.md](SECURITY.md) — read it before extending the
bus, since several of the open items constrain the M3 design.

---

### M1 Federated learning — **done**

Shipped: Flower `NumPyClient` per bank, Flower FedAvg aggregator, ULB + synthetic (+ PaySim/IEEE-CIS loaders), local vs global metrics, `FraudScorer.score_row`.

**Handoff:** `src/aris/fl/scorer.py` → `{risk_score, confidence, probability, model_version}` (no raw account). Checkpoint: `data/processed/m1_global_<dataset>.npz`.

---

### M2 Privacy on training

| Person | Tasks |
| --- | --- |
| **AI-1 (lead)** | Opacus / Flower DP; ε vs AUC table |
| **AI-2** | Utility checks: recall at fixed FPR after DP |
| **Backend** | Document where salt + model artifacts live (env/HSM notes); do not log gradients |

---

### M3 Shared Risk-Signal Bus (Kafka) — **done**

Shipped: `KafkaRiskBus` (same `RiskBus` interface `InMemoryRiskBus` implements), a
compacted `risk-signals` topic keyed `risk_id:bank`, Karapace schema registry over
plain REST (no `confluent-kafka`), `docker-compose.yml` for local dev.

| Person | Tasks |
| --- | --- |
| **Backend (lead)** | docker-compose Kafka, topic `risk-signals`, schema registry, publish + lookup by `risk_id`, TTL, ACL notes |
| **AI-2** | Publisher adapter: model output → `RiskSignal` (no raw ACC) |
| **AI-1** | Hook: after FL round, export `model_version` string onto every signal |

**Handoff:** Backend exposes `publish(signed)` and `lookup(risk_id)` implementing the
`RiskBus` interface in `src/aris/bus.py`. Note `publish` takes a **`SignedRiskSignal`**,
not a bare `RiskSignal` — publishers sign with Ed25519 and the bus verifies against the
consortium keyring before storing. `lookup` returns a `LookupResult` that distinguishes
"no signal" from "bus unavailable", and must never raise into the transfer path.

---

### M4 BankBot API — **done**

Shipped: `POST /transfers`, `GET /audit/{ref}` (admin-key gated), `GET /health`
(`src/aris/api/`). Thin over `aris.bankbot.BankBot` -- no new decision logic.

| Person | Tasks |
| --- | --- |
| **Backend (lead)** | FastAPI: `POST /transfers` (“Send ₹5000 to ACC-999”); step-up stub; audit log store |
| **AI-2** | Map scores → reason_codes the user/analyst sees |
| **AI-1** | Optional: if no bus hit, fall back to local model score |

---

### M5 Explainability & MLOps — **done**

Shipped: `FraudExplainer` (SHAP → `reason_codes`, `src/aris/fl/explain.py`),
`check_drift` (Evidently K-S test, `drift.py`), `ModelRegistry` +
`should_rollback` (`registry.py`). Prometheus/Grafana not built (M6+).

| Person | Tasks |
| --- | --- |
| **AI-2 (lead)** | SHAP/LIME → reason codes; Evidently drift |
| **AI-1** | Model registry versions + rollback criteria |
| **Backend** | Prometheus/Grafana endpoints; attach `model_version` on audit rows |

---

### M6+ Scale — **in progress**

Shipped: robust aggregation (`src/aris/fl/robust_agg.py`), a bus load test
(`src/aris/loadtest.py`, results in `docs/LOADTEST.md`), and SECURITY.md
hardening §3.2/§3.3/§3.4/§3.5 (`aris/hashing.py`, `aris/canary.py`,
`aris/bankbot.py`, `aris/kafka_bus.py`). Not started: graph/velocity features
(blocked on new synthetic transaction-history data -- no current dataset has
one), the OPRF (§3.1) and HSM-resident key (§3.6), and ACL/mTLS hardening
(`docs/SECURITY.md` §3.8).

| Person | Tasks |
| --- | --- |
| **AI-1 (lead)** | Robust aggregation (Krum/median) -- done; more banks -- not started |
| **AI-2** | On-prem graph / velocity features -- not started |
| **Backend** | Bus load test -- done; SECURITY.md §3.2/§3.3/§3.4/§3.5 -- done; OPRF/HSM/ACL-mTLS hardening -- not started |

---

## File ownership (avoid merge conflicts)

| Path | Owner |
| --- | --- |
| `src/aris/fl/` | AI-1 |
| `src/aris/` scoring / explain (to be added as `scorer.py`, `explain.py`) | AI-2 |
| `src/aris/bus.py`, `src/aris/kafka_bus.py`, `src/aris/schema_registry.py`, `src/aris/bankbot.py`, `src/aris/hashing.py`, `src/aris/attestation.py`, `src/aris/canary.py`, `src/aris/api/` | Backend |
| `src/aris/schema.py` | **All — change only with the group** |
| `docs/PROJECT.md` | Shared report |
| `README.md` Phase log | Whoever closed the phase (must update) |

Suggested git branches: `ai1/m1-flower`, `ai2/m1-baseline`, `backend/m3-kafka`. Merge to `main` when the phase **Done when** in `docs/PHASES.md` is met.
