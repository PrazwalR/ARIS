# ARIS (AI-Agent Risk Integration System)
## Master Executive & Technical Dashboard (Phases M0 through M6)

**Overall Status:** **100% COMPLETE & VERIFIED**
**Generated:** 2026-10-14 04:48:43 UTC

---

## 1. Phase Completion Summary Table

| Phase | Description | Key Performance Indicator | Security & Novelty | Status |
| :--- | :--- | :--- | :--- | :---: |
| **M0** | Cryptographic Foundation | HMAC: **79,986.24 h/s** | Keyed `risk_id`, Ed25519 signatures | **Done** |
| **M1** | Federated Learning | Global FedAvg AUC: **0.7534** | Heterogeneous banks; 0 raw rows shared | **Done** |
| **M2** | Privacy on Training | $\epsilon=7.79$ @ $\sigma=4.0$ | DP-SGD + Exact Secure Aggregation | **Done** |
| **M3** | Kafka Shared Risk Bus | Compacted Topic: **256 Prefix Buckets** | mTLS auth, Schema Registry, Re-verification | **Done** |
| **M4** | BankBot Pre-Tx API | API Latency p50: **0.0674 ms** | Encrypted audit log; No-score-oracle | **Done** |
| **M5** | Explainability & MLOps | Permutation SHAP + K-S Drift | Automated rollback triggers on regression | **Done** |
| **M6** | Scale, Byzantine & Graph | Load: **3,734.7 sig/s** | Krum Byzantine defense; On-prem graph velocity | **Done** |

---

## 2. Key Novelties Implemented in this Project

1. **On-Prem Graph & Velocity Feature Extraction (Phase M6+)**:
   Banks independently track temporal receiver fan-in and volume spikes locally. This allows collaborative federated detection of cross-bank money mule rings without centralizing sensitive graphs.
2. **Cryptographic Pseudonymity with OPRF & Epoch Rotation**:
   Account numbers are mapped via length-prefixed `(IFSC, Account)` hashing with daily UTC subkey rotation, with complete support for RSA-FDH blind signatures (`aris/oprf.py`) to eliminate offline dictionary enumeration.
3. **Byzantine-Robust Collaborative Learning**:
   Krum and coordinate-median defenses maintain model convergence even when an adversarial bank submits poisoned weight arrays and fabricates training counts.
4. **No-Score-Oracle Pre-Transaction AI Assistant**:
   BankBot protects customers by checking the shared bus prior to money departure, withholding numeric scores to prevent fraudsters from probing mule account validity.

---

## 3. Directory of Individual Reports

All detailed metric files, JSON exports, and visual charts are located in the `reports/` folder:
- [`reports/M0_foundation_security.md`](M0_foundation_security.md)
- [`reports/M1_federated_learning.md`](M1_federated_learning.md)
- [`reports/M2_differential_privacy.md`](M2_differential_privacy.md)
- [`reports/M3_kafka_risk_bus.md`](M3_kafka_risk_bus.md)
- [`reports/M4_bankbot_pre_transaction_api.md`](M4_bankbot_pre_transaction_api.md)
- [`reports/M5_explainability_mlops.md`](M5_explainability_mlops.md)
- [`reports/M6_scale_robustness_graph.md`](M6_scale_robustness_graph.md)
