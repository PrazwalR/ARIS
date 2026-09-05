"""M6+: load testing for the Shared Risk-Signal Bus.

Measures throughput, per-call latency, and -- just as important -- whether
concurrent access still gives correct answers (no lost updates, no quota
bypass) under contention, not just whether it's fast when nothing races.

    python -m aris.loadtest --backend memory --publishers 20 --signals 200
    python -m aris.loadtest --backend kafka   --publishers 20 --signals 200

See docs/LOADTEST.md for measured results on this machine against both
backends.
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import statistics
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from aris.attestation import Publisher, PublisherKeyring
from aris.bus import PublishOutcome, RiskBus
from aris.schema import RiskSignal


def _percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(int(len(sorted_values) * p), len(sorted_values) - 1)
    return sorted_values[idx]


@dataclass(frozen=True)
class LatencyStats:
    count: int
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float

    @classmethod
    def from_samples_s(cls, samples_s: list[float]) -> LatencyStats:
        if not samples_s:
            return cls(0, 0.0, 0.0, 0.0, 0.0, 0.0)
        ms = sorted(s * 1000.0 for s in samples_s)
        return cls(
            count=len(ms),
            mean_ms=statistics.mean(ms),
            p50_ms=_percentile(ms, 0.50),
            p95_ms=_percentile(ms, 0.95),
            p99_ms=_percentile(ms, 0.99),
            max_ms=ms[-1],
        )


@dataclass(frozen=True)
class LoadTestReport:
    backend: str
    num_publishers: int
    signals_per_publisher: int
    total_signals: int
    duration_s: float
    publish_throughput_per_s: float
    accepted: int
    rejected_quota: int
    errors: int
    lost_updates: int  # accepted count that a final lookup could not find
    publish_latency: LatencyStats
    lookup_latency: LatencyStats


def _risk_id_for(run_id: str, worker: int, i: int) -> str:
    # Deterministic, unique, and matches RiskSignal.risk_id's required shape
    # (64 lowercase hex chars) without needing the real HMAC derivation --
    # this is measuring the bus, not aris.hashing.
    return hashlib.sha256(f"loadtest-{run_id}-{worker}-{i}".encode()).hexdigest()


def run_load_test(
    bus: RiskBus,
    keyring: PublisherKeyring,
    num_publishers: int = 10,
    signals_per_publisher: int = 100,
    lookups_per_publisher: int = 100,
    backend_label: str = "unknown",
) -> LoadTestReport:
    """`num_publishers` threads each publish `signals_per_publisher` distinct
    signals concurrently, while `num_publishers` more threads concurrently
    look up already-published risk_ids -- read and write pressure at once,
    the way BankBot's lookups and a bank's publisher would coexist for real.
    """
    # A fresh run ID in every publisher/risk_id name: against a real (Kafka)
    # bus the topic is persistent across runs, and reusing names would let a
    # new run's consumer replay a *previous* run's records under the same
    # bank name but a different (regenerated, forgotten) signing key --
    # correctly rejected as unverifiable, but noisy and wasted replay effort.
    # In-memory runs don't strictly need this, but there's no reason for the
    # two code paths to differ.
    run_id = secrets.token_hex(4).upper()
    publishers = [Publisher.generate(f"LOADTEST-{run_id}-{i:04d}") for i in range(num_publishers)]
    for p in publishers:
        keyring.register(p.bank_id, p.public_key)

    publish_latencies: list[float] = []
    lookup_latencies: list[float] = []
    outcomes: list[PublishOutcome] = []
    published_risk_ids: list[str] = []
    errors = 0
    results_lock = threading.Lock()
    start_barrier = threading.Barrier(num_publishers + num_publishers)

    def publisher_worker(publisher: Publisher, worker_idx: int) -> None:
        nonlocal errors
        local_latencies = []
        local_outcomes = []
        local_ids = []
        start_barrier.wait()
        for i in range(signals_per_publisher):
            risk_id = _risk_id_for(run_id, worker_idx, i)
            signal = RiskSignal(
                risk_id=risk_id,
                risk_score=50 + (i % 51),
                confidence=0.8,
                reason_codes=("high_velocity",),
                model_version="loadtest-v1",
                source_bank_id=publisher.bank_id,
                timestamp=datetime.now(timezone.utc),
            )
            signed = publisher.sign(signal)
            t0 = time.perf_counter()
            try:
                outcome = bus.publish(signed)
            except Exception:
                with results_lock:
                    errors += 1
                continue
            local_latencies.append(time.perf_counter() - t0)
            local_outcomes.append(outcome)
            if outcome is PublishOutcome.ACCEPTED:
                local_ids.append(risk_id)
        with results_lock:
            publish_latencies.extend(local_latencies)
            outcomes.extend(local_outcomes)
            published_risk_ids.extend(local_ids)

    def lookup_worker(worker_idx: int) -> None:
        local_latencies = []
        start_barrier.wait()
        # Look up whatever this worker's own publisher has produced so far --
        # a lookup racing a publish from the same account is exactly the
        # concurrent-access case that matters, not an arbitrary unrelated id.
        for i in range(lookups_per_publisher):
            risk_id = _risk_id_for(run_id, worker_idx, i % max(signals_per_publisher, 1))
            t0 = time.perf_counter()
            bus.lookup(risk_id)
            local_latencies.append(time.perf_counter() - t0)
        with results_lock:
            lookup_latencies.extend(local_latencies)

    threads = [
        threading.Thread(target=publisher_worker, args=(publishers[i], i))
        for i in range(num_publishers)
    ] + [threading.Thread(target=lookup_worker, args=(i,)) for i in range(num_publishers)]

    t_start = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duration = time.perf_counter() - t_start

    accepted = sum(1 for o in outcomes if o is PublishOutcome.ACCEPTED)
    rejected_quota = sum(1 for o in outcomes if o is PublishOutcome.QUOTA_EXCEEDED)

    lost = 0
    for risk_id in published_risk_ids:
        result = bus.lookup(risk_id)
        if result.signal is None:
            lost += 1

    total = num_publishers * signals_per_publisher
    return LoadTestReport(
        backend=backend_label,
        num_publishers=num_publishers,
        signals_per_publisher=signals_per_publisher,
        total_signals=total,
        duration_s=duration,
        publish_throughput_per_s=total / duration if duration > 0 else 0.0,
        accepted=accepted,
        rejected_quota=rejected_quota,
        errors=errors,
        lost_updates=lost,
        publish_latency=LatencyStats.from_samples_s(publish_latencies),
        lookup_latency=LatencyStats.from_samples_s(lookup_latencies),
    )


def _print_report(report: LoadTestReport) -> None:
    print(f"backend:              {report.backend}")
    print(f"publishers:           {report.num_publishers}")
    print(f"signals/publisher:    {report.signals_per_publisher}")
    print(f"total signals:        {report.total_signals}")
    print(f"duration:             {report.duration_s:.3f}s")
    print(f"publish throughput:   {report.publish_throughput_per_s:.1f}/s")
    print(f"accepted:             {report.accepted}")
    print(f"rejected (quota):     {report.rejected_quota}")
    print(f"errors:               {report.errors}")
    print(f"lost updates:         {report.lost_updates}  (must be 0)")
    pl = report.publish_latency
    print(
        f"publish latency (ms): mean={pl.mean_ms:.2f} p50={pl.p50_ms:.2f} "
        f"p95={pl.p95_ms:.2f} p99={pl.p99_ms:.2f} max={pl.max_ms:.2f}"
    )
    ll = report.lookup_latency
    print(
        f"lookup latency (ms):  mean={ll.mean_ms:.2f} p50={ll.p50_ms:.2f} "
        f"p95={ll.p95_ms:.2f} p99={ll.p99_ms:.2f} max={ll.max_ms:.2f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ARIS Shared Risk-Signal Bus load test")
    parser.add_argument("--backend", choices=("memory", "kafka"), default="memory")
    parser.add_argument("--publishers", type=int, default=10)
    parser.add_argument("--signals", type=int, default=100, help="signals per publisher")
    args = parser.parse_args(argv)

    keyring = PublisherKeyring()
    bus: RiskBus
    if args.backend == "kafka":
        from aris.kafka_bus import KafkaRiskBus, load_kafka_config

        bootstrap, registry_url = load_kafka_config()
        # A dedicated topic, not the default risk-signals: every run adds a
        # fresh batch of never-reused risk_ids, which would otherwise grow the
        # real topic's compacted key-space on every load test run and slow
        # down every other KafkaRiskBus's from-scratch consumer catch-up.
        bus = KafkaRiskBus(
            keyring,
            bootstrap,
            registry_url,
            topic="risk-signals-loadtest",
            max_publisher_share=1.0,
        )
    else:
        from aris.bus import InMemoryRiskBus

        bus = InMemoryRiskBus(keyring, max_publisher_share=1.0)

    report = run_load_test(
        bus,
        keyring,
        num_publishers=args.publishers,
        signals_per_publisher=args.signals,
        backend_label=args.backend,
    )
    _print_report(report)
    if hasattr(bus, "close"):
        bus.close()
    return 0 if report.lost_updates == 0 and report.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
