# Phase M4: BankBot Pre-Transaction API Report

**Status:** Completed & Evaluated
**Architecture:** FastAPI Pre-Transaction Gateway (`POST /transfers`, `GET /audit/{ref}`)

## 1. Pre-Transaction Latency Distribution (1,000 Transfers)

| Percentile | Latency | SLA Target | Status |
| :--- | :--- | :--- | :--- |
| **p50 (Median)** | **0.0674 ms** | < 2.0 ms | Met |
| **p95** | **0.1133 ms** | < 5.0 ms | Met |
| **p99** | **0.2239 ms** | < 10.0 ms | Met |

## 2. Policy Decisions Breakdown
- **ALLOW Decisions:** 900 (Transfers to clean, unflagged accounts).
- **STEP-UP Decisions:** 50 (Medium risk or large transaction amounts > ₹100,000).
- **BLOCK Decisions:** 50 (High-risk mule accounts flagged by peer banks).

## 3. Security Highlights
- **No Score Probing Oracle:** Customer response returns opaque audit reference; fraudsters cannot probe the API to discover mule burnout scores.
- **At-Rest Encrypted Audit Trail:** All internal audit entries encrypted via AES-GCM; requires investigator credentials for inspection.
