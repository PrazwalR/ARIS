# ARIS: Bridging AI-Agent Security and Federated Fraud Detection through a Shared Risk-Signal Bus

## 1. Introduction

Digital banking has made transactions faster and easier, but it has also increased fraud risks. Fraudsters often target multiple banks, using the same receiver accounts across institutions. However, banks cannot simply share customer transaction data with each other due to privacy laws, data-sovereignty rules, and competitive concerns.

ARIS (AI-Agent Risk Integration System) is a banking-security project that solves this problem. It enables multiple banks to collaboratively train a fraud-detection model using federated learning without sharing raw transaction data. When the model identifies a risky receiver account, it sends only a risk score (not raw data) to a Shared Risk-Signal Bus. An AI banking assistant (BankBot) checks this bus before executing transactions. If the risk score is high, BankBot pauses or blocks the transfer, protecting users from potential fraud—even if the fraud was first detected in a different bank.

**Implementation status.** This document describes the full ARIS design. What is
built today is the risk-signal layer: pseudonymous IDs, signed publication, the
bus, the policy engine, and BankBot's pre-transaction check (M0). The federated
learning layer of §3.1 is **design only — no training code exists yet** and is
scheduled as M1 in [PHASES.md](PHASES.md). Sections below flag this where it
applies.

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

> **Status: design only.** No federated learning code exists in this repository
> yet; `src/aris/fl/` is an empty package. Scheduled as M1.

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

- MAC: `HMAC-SHA256` (RFC 2104)
- A shared **consortium key**: 32 bytes of random material, hex- or base64-encoded.
  The design target is HSM/KMS residency; the current implementation reads it from
  the `ARIS_SALT` environment variable (see [SECURITY.md](SECURITY.md) §3.6).
- A **normalization rule**, which is load-bearing: whitespace is stripped, non-ASCII
  is rejected *before* case folding, and the result is uppercased and restricted to
  `[A-Z0-9-]{4,34}`. Two implementations that normalize differently produce different
  IDs, and cross-bank matching then fails silently.

For any account `ACC`:

`risk_id = HMAC-SHA256(consortium_key, normalize(ACC))`

Every member bank derives the same `risk_id` for the same account, so accounts can be
matched across institutions without the bus ever receiving an account number.

**Scope of this protection — stated precisely, because it is narrower than it looks.**
Pseudonymity here defends the account number against the *bus operator*, and against
anyone who compromises the topic. It does **not** defend it against a *participating
bank*. Account numbers are drawn from a small, structured space (on the order of 10⁹
live accounts), so any key holder — which is every member — can enumerate that space
offline in under a minute on one GPU and build a permanent `risk_id → account` table.
No choice of hash function changes this, and a deliberately slow hash only raises the
one-time cost while making derivation too slow for the payment path.

Closing the gap requires a construction in which no single party holds a key that maps
the whole space: an **OPRF** (RFC 9497) issued by a consortium authority, so that each
guess costs one online, rate-limited, attributable round trip instead of a free offline
hash. Tracked as a design decision in [SECURITY.md](SECURITY.md) §3.1.

**Publisher authenticity.** `source_bank_id` is a field inside the payload, so on its
own it is an unverified claim: any member could set it to a peer's name, publish a
score of zero, and erase that peer's fraud flag. Every signal is therefore
**Ed25519-signed** by its publisher and verified against a registered key before the
bus stores it. Signing the payload end-to-end, rather than relying on transport
authentication alone, also covers the bus operator, who could otherwise alter signals
in flight or at rest undetected.

Example risk-signal message:

```json
{
  "risk_id": "a29e47bbbb41332e7e03cbfb324ef72bff897cf7d6e96a3950edecdb4768f420",
  "risk_score": 92,
  "confidence": 0.94,
  "reason_codes": ["new_beneficiary", "high_velocity", "suspicious_pattern"],
  "model_version": "m0-demo-stub",
  "source_bank_id": "BANK-B",
  "ttl_hours": 24,
  "timestamp": "2026-08-22T14:10:00Z"
}
```

Transport access is restricted using mTLS, OAuth2/OIDC, and per-topic ACLs — planned
for M3, not yet implemented. Those controls protect the channel; the Ed25519 signature
above protects the message, which is what makes the per-bank partitioning meaningful
even against the operator running the channel.

**Outcome:** All participating banks can see which accounts are currently risky without
sharing raw transaction data, and without any account number reaching the bus — subject
to the scope note above: a member bank holding the consortium key can still recover
account numbers from the IDs.

### 3.3 AI Banking Assistant (BankBot) Layer

BankBot is an AI assistant integrated into the bank’s transaction flow (similar in spirit to Erica, Eno, EVA, iPal, but with added cross-bank risk awareness).

Before executing a transfer, BankBot:

1. Takes the receiver account (e.g. `ACC-999`).
2. Normalizes the account and computes the same `risk_id` using the consortium key.
3. Queries the Shared Risk-Signal Bus for that `risk_id`.
4. Applies policy (`aris.schema.apply_policy`), which resolves on three inputs, not
   just the score:
   - **Bus unreachable** → never allow. Defaults to step-up, and `PolicyConfig`
     refuses any configuration that would fail open. A lookup that returns "no
     signal" is deliberately distinguished from one that did not answer, so an
     outage can never present as an all-clear.
   - **Score ≥ `block_at`** (default 85) → block and suggest contacting support — but
     only if the publishing bank's own `confidence` clears `min_confidence_to_block`.
     A block freezes an innocent customer's payments for up to the full TTL with no
     retraction path, so one member's low-confidence assertion is capped at step-up.
   - **Score ≥ `step_up_at`** (default 50) → step-up auth (OTP, extra questions).
   - **Amount above `step_up_above_amount_minor`** (default ₹100,000) → step-up even
     for an account the network has never flagged.
   - Otherwise → allow.
5. Writes an audit record for **every** decision, including allows. The record
   carries the transfer ID, customer reference, amount, currency, derived `risk_id`,
   decision, lookup status, score, reason codes, the banks that contributed, the
   model version, and the policy thresholds in force — enough to reconcile against
   the core banking ledger and to explain a past decision under the rules that
   applied at the time.

**No score oracle.** The reply shown to the customer deliberately omits the numeric
score, the flagging bank, and the `risk_id`. Echoing any of them back would turn the
transfer form into a probing oracle: a fraudster could test accounts through the
assistant to learn which of their mule accounts the network has burned, and tune their
behaviour just under the threshold.

**Outcome:** Users are protected from sending money to accounts that have been flagged as risky by any participating bank.

## 4. End-to-End Example (Hashed IDs)

**Scenario:** Bank B has already seen fraud involving receiver account `ACC-999`. Anu, a customer of Bank A, tries to send ₹5,000 to `ACC-999` via BankBot.

### Step 1 – Fraud detected in Bank B

Bank B’s model scores `ACC-999` as high risk: `risk_score = 92`.

Bank B computes `risk_id_999 = HMAC-SHA256(consortium_key, "ACC-999")` → e.g.
`a29e47bb…f420`, and signs the signal with its Ed25519 key.

Bank B publishes that signal to the bus (no plain account number).

### Step 2 – Anu requests transfer in Bank A

Anu asks BankBot: “Send ₹5,000 to ACC-999.”

Bank A derives the same `risk_id`, queries the bus, finds score 92. Policy: if
`risk_score >= 85` → block.

BankBot replies: *This transfer looks risky. The receiver account has been flagged for suspicious activity by our fraud network. For your safety, this transaction is blocked.*

**Result:** Anu is protected from fraud first detected in a different bank, without sharing raw transaction data or plain account numbers.

## 5. Datasets and Tools (planned)

None of the following are current dependencies of this repository; they are the
intended stack for M1–M5. The M0 code depends only on `pydantic` and `cryptography`.

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
