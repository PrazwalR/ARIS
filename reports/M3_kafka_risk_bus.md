# Phase M3: Shared Risk-Signal Bus (Kafka) Report

**Status:** Completed & Verified
**Message Bus:** Apache Kafka (KRaft) + Karapace Schema Registry / Local Materialized View

## 1. Topic Architecture & Wire Schema
- **Compacted Topic:** `risk-signals` keyed by `risk_id:source_bank_id` so one bank's report never evicts another's.
- **Wire Format:** Confluent 5-byte header (magic byte 0 + 4-byte schema ID) + JSON payload registered via REST API.
- **Prefix-Bucketed Consumption (docs/SECURITY.md §3.4):** Topic is partitioned into 256 prefix buckets; banks consume only buckets relevant to their payment traffic, avoiding full bus history download.
- **Transport Security (docs/SECURITY.md §3.8):** mTLS listener on port 9093 with `StandardAuthorizer` enforcing per-bank Write ACLs.
- **Wire Reverification:** Every consumed record is re-verified against the Ed25519 keyring before being admitted into local state.
