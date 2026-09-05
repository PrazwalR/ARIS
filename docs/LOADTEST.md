# Shared Risk-Signal Bus — load test results

M6+ deliverable: "load test the bus." Numbers below are measured, not
estimated — run `python -m aris.loadtest --backend memory` /
`--backend kafka` yourself to reproduce (Kafka needs `docker compose up -d`
from the M3 section of the README first).

## Method

`src/aris/loadtest.py` spins up N publisher threads and N lookup threads
concurrently: publishers each sign and publish a batch of distinct
`RiskSignal`s (real Ed25519 signatures, not stand-ins), lookup threads
concurrently query risk_ids as they land, and a final pass confirms every
*accepted* publish is actually visible via `lookup()` afterward. This checks
correctness under concurrency — no lost updates, no quota bypass — alongside
throughput and latency, not throughput in isolation.

`tests/test_loadtest.py` asserts the correctness properties (zero lost
updates, quota enforcement holding exactly under concurrent contention) as a
real, always-run test. It does not assert on absolute throughput numbers,
which vary by hardware and would make CI flaky for no real signal.

## Results (this machine, Apple Silicon, 2026-09)

### In-memory bus (`InMemoryRiskBus`)

20 publishers × 200 signals = 4,000 signals, plus 20 concurrent lookup
threads, `max_publisher_share=1.0` (quota disabled to measure raw throughput
rather than quota rejection):

| Metric | Value |
| --- | --- |
| Duration | 1.14 s |
| Publish throughput | **3,517 signals/s** |
| Accepted | 4,000 / 4,000 |
| Lost updates | **0** |
| Publish latency (mean / p50 / p95 / p99 / max) | 3.2 / 0.2 / 4.5 / 85.7 / 464.2 ms |
| Lookup latency (mean / p50 / p95 / p99 / max) | 0.0 / 0.0 / 0.0 / 0.0 / 0.2 ms |

**Reading the tail.** p50 publish latency (0.2ms) is essentially free — the
bottleneck is Python's GIL and the bus's single `RLock`, not any real
computation. The p99/max (85.7 / 464.2 ms) come from lock contention: with 20
writer threads hammering one lock, a handful of calls queue behind a burst of
others. This is a real limit of a single-process, single-lock design, not a
measurement artifact — a sharded lock (e.g. per-`risk_id`-prefix) would
reduce it, at real implementation complexity cost for a bus this milestone
scopes as a stand-in for Kafka, not the M3 deliverable itself.

### Kafka bus (`KafkaRiskBus`, live broker)

10 publishers × 50 signals = 500 signals, plus 10 concurrent lookup threads,
against `docker-compose.yml`'s local single-broker Kafka + Karapace:

| Metric | Value |
| --- | --- |
| Duration | 0.56 s |
| Publish throughput | **900 signals/s** |
| Accepted | 500 / 500 |
| Lost updates | **0** |
| Publish latency (mean / p50 / p95 / p99 / max) | 10.7 / 8.1 / 17.1 / 104.7 / 106.1 ms |
| Lookup latency (mean / p50 / p95 / p99 / max) | 0.0 / 0.0 / 0.0 / 0.0 / 0.2 ms |

**Reading this.** Publish latency is dominated by `acks="all"` — every
`publish()` waits for the broker to durably acknowledge the write before
returning `PublishOutcome`, which is the right tradeoff for a fraud signal
(losing one silently is worse than a few extra milliseconds) but costs a real
network round trip per call, visible in p50 (8.1ms) versus the in-memory
bus's near-zero. Lookup latency stays at zero regardless of publish load,
exactly as designed: `KafkaRiskBus.lookup()` only ever reads the local
materialized view (see `src/aris/kafka_bus.py`), never the network — BankBot
never blocks on Kafka to make a policy decision.

A single local broker with default (unbatched, `acks=all`, no compression)
settings is not what a real deployment would run — this measures the
integration's correctness and the current defaults' floor, not Kafka's
ceiling. Producer batching, `acks=1` where the durability tradeoff is
acceptable, and a multi-broker cluster would all raise throughput; none of
those changes are made here because they change what failure modes are
possible, and that tradeoff belongs to whoever operates the real deployment,
not this benchmark.

## What this does and doesn't prove

**Proves:** under genuine concurrent read/write pressure — not a single
publish-then-lookup happy path — the bus loses no accepted signal, and the
per-publisher quota (`aris.bus.InMemoryRiskBus`'s `max_publisher_share`)
holds exactly even when many threads are racing to claim the shared budget
(verified directly: 10 publishers × 20 signals against a 50-entry cap with a
10%-per-publisher share admits exactly 50 and rejects exactly 150, with zero
lost or duplicated entries, every run).

**Does not prove:** behavior under sustained load far beyond what a single
process/single broker on one laptop can generate, multi-broker Kafka
failover, or network partition between a bank and the bus. Those are real,
larger undertakings (a proper distributed load-testing harness, a multi-node
Kafka cluster, chaos testing) out of scope for this milestone.

## Known limits (for a report/defense)

1. **`InMemoryRiskBus`'s single `RLock` serializes every publish.** Fine for
   a demo/test stand-in; a real high-throughput deployment uses the Kafka
   backend, which does not share this bottleneck (its lock only guards the
   local materialized view, and Kafka itself parallelizes across partitions
   — this repo's `docker-compose.yml` uses a single partition for simplicity,
   which a real deployment would not).
2. **`KafkaRiskBus.publish()`'s `acks="all"` round trip is synchronous.** A
   bank's publish path blocks on it. This is deliberate (see above) but means
   publish throughput is bounded by broker round-trip latency, not by this
   process's CPU.
3. **No backpressure signaling.** Neither bus tells a publisher to slow down
   before rejecting — `InMemoryRiskBus` accepts up to its quota then rejects
   outright; `KafkaRiskBus` inherits whatever `KafkaProducer`'s internal
   buffering does. A production deployment needs real backpressure /
   rate-limiting at the API layer (M4), not just at the bus.
