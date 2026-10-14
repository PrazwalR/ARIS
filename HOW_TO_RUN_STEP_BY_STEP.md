# ARIS: Complete Step-by-Step Execution Runbook

**AI-Agent Risk Integration System (ARIS)**
*Bridging AI-Agent Security, Federated Fraud Detection, and Real-Time Cryptographic Attestation.*

This guide provides the complete, copy-pasteable execution steps to run, train, evaluate, and benchmark every component of the ARIS platform from **Phase M0 through Phase M6+**.

---

## Quick Reference Summary Table

| Step | Target Milestone | Primary Command | Key Output / Artifact |
| :---: | :--- | :--- | :--- |
| **0** | **Environment Setup** | `.venv\Scripts\Activate.ps1` | Python 3.11+ virtual environment ready |
| **1** | **M0 Cryptographic Demo** | `python -m aris.demo.anu_transfer` | Anu `ACC-999` blocked (score 92, HMAC-SHA256) |
| **2** | **M1 Federated Learning** | `python -m aris.fl.run --dataset ulb` | Flower FedAvg multi-bank consensus model |
| **3** | **M2 Differential Privacy** | `python -m aris.fl.privacy_sweep --dataset synthetic` | DP-SGD $(\epsilon, \delta)$ privacy-utility curve |
| **4** | **M3 Kafka Risk Bus** | `docker compose up -d` | Real-time distributed broker + Karapace registry |
| **5** | **M4 BankBot HTTP API** | `python -m aris.api` | FastAPI pre-transaction service (`POST /transfers`) |
| **6** | **M5 Explainability & MLOps**| `pytest tests/test_fl_explain.py tests/test_fl_drift.py` | Permutation SHAP attribution + K-S drift check |
| **7** | **M6+ Graph Velocity Engine**| `python -m aris.fl.run --dataset graph-stream` | On-prem graph velocity stream (**ROC-AUC = 1.000**) |
| **8** | **M6+ Byzantine Defense** | `pytest tests/test_fl_robust_agg.py -v` | Krum aggregation defeats $-1000\times$ poisoning |
| **9** | **Real-Time Interactive Demo**| `python scripts/interactive_fraud_test.py --no-pause` | Live end-to-end ALLOW $\to$ Flag $\to$ BLOCK cycle |
| **10**| **Compile All Reports** | `python scripts/generate_all_reports.py` | Consolidated [`reports/`](reports/) & Master Dashboard |
| **11**| **Full Test Suite** | `pytest` | **342 passed, 0 failures** |

---

## Detailed Step-by-Step Instructions

### Step 0: Environment Activation & Key Configuration

Open PowerShell in the project directory:

```powershell
cd "c:\Users\my pc\Desktop\airs\ARIS"

# 1. Activate the Python virtual environment
.venv\Scripts\Activate.ps1

# 2. Configure the consortium root salt (fail-closed security parameter)
# Set development mode or provide a 32-byte hex salt:
$env:ARIS_DEV_MODE = "1"
$env:ARIS_SALT = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
```

---

### Step 1: Run M0 Cryptographic Foundation & BankBot Demo

Simulate the foundational Anu / BankBot story: Bank B flags `ACC-999` with score 92; Anu at Bank A attempts a ₹5,000 transfer; BankBot derives the keyed risk ID and blocks the transaction before funds leave:

```powershell
python -m aris.demo.anu_transfer
```
*Expected Output:*
- Prints the signed `RiskSignal` on the shared bus (carrying pseudonymous `risk_id`, zero plain account numbers).
- Bank A BankBot intercepts the transfer and issues `decision: block`.
- Hostile member signature tampering attempt is verified and rejected.

---

### Step 2: Run M1 Multi-Bank Federated Learning

Train the federated model across 5 simulated banks using Flower FedAvg without centralizing raw customer rows:

#### A. Synthetic Heterogeneous Partitions:
```powershell
python -m aris.fl.run --dataset synthetic --banks 5 --rounds 8 --epochs 4
```
*Result:* Global FedAvg model outperforms mean of local banks (**ROC-AUC 0.655 vs 0.572**).

#### B. Real-World ULB European Credit Card Fraud Dataset:
```powershell
python -m aris.fl.run --dataset ulb --banks 5 --rounds 5 --epochs 2 --max-rows 30000
```
*Result:* Evaluates on unseen temporal holdout (**ROC-AUC = 0.9900, PR-AUC = 0.9260**, catching 32/34 fraud cases).

---

### Step 3: Run M2 Differential Privacy (DP-SGD) & Secure Aggregation

Train with per-example gradient clipping, calibrated Gaussian noise, and pairwise secure aggregation:

```powershell
# 1. Single run with DP-SGD noise multiplier 4.0 and pairwise secure aggregation
python -m aris.fl.run --dataset synthetic --dp --noise-multiplier 4.0 --max-grad-norm 5.0 --secure-agg

# 2. Run the full epsilon vs. utility sweep across sigma values:
python -m aris.fl.privacy_sweep --dataset synthetic
```
*Results:* Outputs [`data/processed/m2_privacy_utility_synthetic.json`](data/processed/m2_privacy_utility_synthetic.json). Pairwise perturbation masks cancel out with exact zero residual error ($\Delta = 0.00\times 10^0$).

---

### Step 4: Run M3 Kafka Distributed Risk Bus (Optional with Docker)

Spin up the local Kafka broker and Karapace Schema Registry:

```powershell
# 1. Start Kafka (KRaft mode) and Schema Registry
docker compose up -d

# 2. Run the Kafka bus test suite (verifying wire formats, mTLS, and 256 prefix buckets)
pytest tests/test_kafka_bus.py tests/test_kafka_bus_bucketing.py -v
```

---

### Step 5: Run M4 BankBot HTTP API Pre-Transaction Service

Launch the live FastAPI pre-transaction hook:

```powershell
# Start the FastAPI server on port 8000
python -m aris.api
```

In another PowerShell window, test sending a transfer request:
```powershell
curl -X POST http://127.0.0.1:8000/transfers `
  -H "Content-Type: application/json" `
  -d '{
    "user_ref": "USR-101",
    "bank_id": "BANK-A",
    "receiver_ifsc": "HDFC0001234",
    "receiver_account": "ACC-99999",
    "amount_minor": 500000
  }'
```
*Response:* Returns instantaneous decision (`allow` or `block`) with an opaque audit reference and sub-millisecond latency.

---

### Step 6: Run M5 Explainability (SHAP) & MLOps Drift Governance

Verify permutation SHAP attribution to named reason codes and Evidently Kolmogorov-Smirnov drift monitoring:

```powershell
pytest tests/test_fl_explain.py tests/test_fl_drift.py tests/test_fl_registry.py -v
```
*Result:* 28 passed. Attributions trace back from model weights to `RiskSignal` reason codes (`high_velocity`, `new_beneficiary`).

---

### Step 7: Run M6+ On-Prem Graph Velocity Engine & Byzantine Defense

Train with on-prem graph feature extraction, test Krum Byzantine robustness, and benchmark high-throughput load:

```powershell
# 1. Train federated model with the On-Prem Graph & Velocity Feature Engine enabled
python -m aris.fl.run --dataset graph-stream --banks 5 --rounds 6

# 2. Test Byzantine attack resilience (Krum vs FedAvg under poisoned updates)
pytest tests/test_fl_robust_agg.py tests/test_fl_graph_features.py -v

# 3. Run high-throughput concurrent bus stress test (20 concurrent publishers, 4,000 signals)
python -m aris.loadtest --backend memory --publishers 20 --signals 200
```
*Result:*
- Graph velocity federated model achieves **ROC-AUC = 1.0000** and **PR-AUC = 1.0000** on cross-bank mule ring bursts.
- Krum successfully rejects $-1000\times$ malicious gradient attacks while standard FedAvg collapses.
- Bus sustains **3,742.2 signals/second with 0 lost updates**.

---

### Step 8: Interactive Real-Time Fraud Lifecycle Demo

Execute the interactive manual testing tool demonstrating the complete real-time lifecycle:
1. User enters receiver details $\to$ BankBot queries bus $\to$ **ALLOW** (clean).
2. Account experiences rapid velocity burst $\to$ On-prem graph features extracted $\to$ ML model scores fraud (score 95).
3. Bank B publishes signed `RiskSignal` to the Shared Bus (plain account number NEVER leaves the bank).
4. User enters the *same* details again $\to$ BankBot immediately **BLOCKS** the transaction in real time!
5. Compliance officer inspects the decrypted audit trail.

```powershell
# Run the complete interactive simulation:
python scripts/interactive_fraud_test.py --account "ACC-88899" --amount 7500 --user "Priya" --no-pause
```

---

### Step 9: Generate Unified Master Reports & Dashboard

Compile all technical metrics, JSON files, markdown reports, and confusion matrix assets across phases M0 to M6:

```powershell
python scripts/generate_all_reports.py
```
This automatically updates all artifacts in [`reports/`](reports/):
- [`reports/M0_foundation_security.md`](reports/M0_foundation_security.md) & `.json`
- [`reports/M1_federated_learning.md`](reports/M1_federated_learning.md) & `.json`
- [`reports/M2_differential_privacy.md`](reports/M2_differential_privacy.md) & `.json`
- [`reports/M3_kafka_risk_bus.md`](reports/M3_kafka_risk_bus.md) & `.json`
- [`reports/M4_bankbot_pre_transaction_api.md`](reports/M4_bankbot_pre_transaction_api.md) & `.json`
- [`reports/M5_explainability_mlops.md`](reports/M5_explainability_mlops.md) & `.json`
- [`reports/M6_scale_robustness_graph.md`](reports/M6_scale_robustness_graph.md) & `.json`
- [`reports/M0_TO_M6_COMPREHENSIVE_DASHBOARD.md`](reports/M0_TO_M6_COMPREHENSIVE_DASHBOARD.md)
- [`reports/assets/m1_ulb_confusion_matrix.png`](reports/assets/m1_ulb_confusion_matrix.png)

---

### Step 10: Run the Entire Test Suite

Execute the entire test suite across all modules:

```powershell
pytest
```
*Expected Result:* **342 passed, 16 skipped in ~45s (0 failures)**.

---

### Step 11: View Conference Paper LaTeX Results

All empirical results, full confusion matrices, and backend benchmarks are prepared for academic publication in:
- [`conference_paper_results.tex`](conference_paper_results.tex)

You can copy this file directly into Overleaf or compile it locally with `pdflatex conference_paper_results.tex`.
