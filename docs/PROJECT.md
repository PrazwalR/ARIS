# ARIS: Bridging AI-Agent Security and Federated Fraud Detection through a Shared Risk-Signal Bus

## 1. Introduction

Digital banking has made transactions faster and easier, but it has also increased fraud risks. Fraudsters often target multiple banks, using the same receiver accounts across institutions. However, banks cannot simply share customer transaction data with each other due to privacy laws, data-sovereignty rules, and competitive concerns.

ARIS (AI-Agent Risk Integration System) is a banking-security project that solves this problem. It enables multiple banks to collaboratively train a fraud-detection model using federated learning without sharing raw transaction data. When the model identifies a risky receiver account, it sends only a risk score (not raw data) to a Shared Risk-Signal Bus. An AI banking assistant (BankBot) checks this bus before executing transactions. If the risk score is high, BankBot pauses or blocks the transfer, protecting users from potential fraud—even if the fraud was first detected in a different bank.

## 2. Problem Statement

- Fraudsters use the same receiver accounts across multiple banks.
- Individual banks have limited visibility into fraud happening elsewhere.
- Sharing raw transaction data across banks is not allowed due to privacy and regulatory constraints.
- Existing AI banking assistants mostly use internal data only and do not benefit from cross-bank fraud signals in real time.

**Goal:** Build a system where:

- Banks improve fraud detection together without sharing raw data.
- Risky accounts flagged by any bank become visible to all participating banks.
- An AI assistant can block risky transfers before money leaves the user’s account.

## 3. Proposed Solution: ARIS Architecture

ARIS has three core layers.

### 3.1 Federated Learning Layer (Model Training)

Each bank keeps its raw transaction data on-prem. A central FL server coordinates training of a global fraud model.

In each round:

1. The server sends the current global model to each bank.
2. Each bank trains locally on its own data and sends back model updates (weights/gradients).
3. The server aggregates updates (e.g. FedAvg) and sends back an improved global model.

No account numbers, transaction IDs, or customer data leave the bank in this layer.

**Outcome:** All banks get a stronger fraud model that has learned from everyone but never saw anyone else’s raw data.

### 3.2 Shared Risk-Signal Bus (Cross-Bank Risk Sharing)

When a bank’s model flags a receiver account as high risk, the bank publishes a risk signal to a secure message bus (e.g. Apache Kafka topic `risk-signals`).

To protect privacy, the bus does not store plain account numbers. Instead it uses a hashed risk ID.

All banks agree on:

- Hash function: `H = SHA-256`
- Shared secret salt: `SALT` (stored securely in each bank’s HSM/KMS)

For any account `ACC`:

`risk_id = SHA256(ACC ∥ SALT)`

All banks compute the same `risk_id` for the same account, but the bus never sees the real account number.

Example risk-signal message:

```json
{
  "risk_id": "a3f9c2b1...e7d4",
  "risk_score": 92,
  "confidence": 0.94,
  "reason_codes": ["new_beneficiary", "high_velocity", "suspicious_pattern"],
  "model_version": "v0.4-fl",
  "source_bank_id": "BANK-B",
  "ttl_hours": 24,
  "timestamp": "2026-08-18T14:10:00Z"
}
```

Access to the bus is restricted using mTLS, OAuth2/OIDC, and per-topic ACLs.

**Outcome:** All participating banks can see which accounts are currently risky, without anyone sharing raw transaction data or plain account numbers.

### 3.3 AI Banking Assistant (BankBot) Layer

BankBot is an AI assistant integrated into the bank’s transaction flow (similar in spirit to Erica, Eno, EVA, iPal, but with added cross-bank risk awareness).

Before executing a transfer, BankBot:

1. Takes the receiver account (e.g. `ACC-999`).
2. Computes the same `risk_id` using the shared hash function and salt.
3. Queries the Shared Risk-Signal Bus for that `risk_id`.
4. Applies policy:
   - Low → allow
   - Medium → pause and step-up auth (OTP, extra questions)
   - High → block and suggest contacting support
5. Logs the decision with risk score and reason codes for audit.

**Outcome:** Users are protected from sending money to accounts that have been flagged as risky by any participating bank.

## 4. End-to-End Example (Hashed IDs)

**Scenario:** Bank B has already seen fraud involving receiver account `ACC-999`. Anu, a customer of Bank A, tries to send ₹5,000 to `ACC-999` via BankBot.

### Step 1 – Fraud detected in Bank B

Bank B’s model scores `ACC-999` as high risk: `risk_score = 92`.

Bank B computes `risk_id_999 = SHA256("ACC-999" ∥ SALT)` → e.g. `a3f9c2b1...e7d4`.

Bank B publishes that signal to the bus (no plain account number).

### Step 2 – Anu requests transfer in Bank A

Anu asks BankBot: “Send ₹5,000 to ACC-999.”

Bank A computes the same `risk_id`, queries the bus, finds score 92. Policy: if `risk_score > 85` → block.

BankBot replies: *This transfer looks risky. The receiver account has been flagged for suspicious activity by our fraud network. For your safety, this transaction is blocked.*

**Result:** Anu is protected from fraud first detected in a different bank, without sharing raw transaction data or plain account numbers.

## 5. Datasets and Tools

**Datasets:** IEEE-CIS Fraud Detection (Kaggle), ULB Credit Card Fraud, PaySim.

**FL:** Flower, NVFlare. **Bus:** Apache Kafka, Schema Registry. **Privacy:** Opacus, TenSEAL / Concrete-ML. **XAI / MLOps:** SHAP, LIME, MLflow, Prometheus+Grafana, Evidently AI.

**References:** Federated-Fraud-Detection-System (Flower + RF), federated_credit_card_fraud (Flower + PyTorch + Streamlit), NVFlare `hello-dp`.

## 6. Relation to Existing Bankbots

Erica, Eno, EVA, and iPal already help users and surface fraud. ARIS adds a cross-bank Shared Risk-Signal Bus, a federated fraud model, and pre-transaction checks that can block transfers to accounts flagged by any participating bank.

## 7. Expected Benefits

Better detection, cross-bank protection, privacy (updates + hashed IDs only), user safety before funds leave, explainable reason codes.

## 8. Implementation Roadmap

See [PHASES.md](PHASES.md) for execution detail (M0–M6+) and [TEAM.md](TEAM.md) for the 2 AI + 1 backend split.

## 9. Conclusion

ARIS shows how banks can collaborate safely to fight fraud using federated learning, share only privacy-preserving risk signals via a common bus, and empower an AI banking assistant to block risky transfers in real time. The design builds on existing bankbot technology and open-source FL frameworks.
