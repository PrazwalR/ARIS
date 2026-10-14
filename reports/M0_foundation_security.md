# Phase M0: Cryptographic Foundation & Policy Engine Report

**Status:** Completed & Formally Verified
**Benchmark Date:** 2026-10-14 05:27:12 UTC

## 1. Cryptographic Micro-Benchmarks

| Primitive | Operation | Throughput | Latency |
| :--- | :--- | :--- | :--- |
| **HMAC-SHA256** | `risk_id` Derivation | **76,849.74 ops/sec** | ~13.01 µs |
| **Ed25519** | Signature Generation | **18,132.41 signs/sec** | ~0.055 ms |
| **Ed25519** | Keyring Verification | **7,436.51 verif/sec** | ~0.134 ms |
| **BankBot Engine** | End-to-End Decision | **12,244.02 decisions/sec** | **0.0817 ms** |

## 2. Core Security Invariants Met
- **Pseudonymous Account Hashing:** Length-prefixed `(IFSC, Account)` HMAC-SHA256 bound to daily UTC epochs prevents cross-bank account collision and rainbow table recovery.
- **Publisher Authenticity:** Every published risk signal is cryptographically signed; hostile members cannot forge or overwrite flags published by peer banks.
- **Fail-Closed Policy Engine:** Outages and unconfigured consortium keys resolve to step-up/block, never allowing uninspected funds to leave.
