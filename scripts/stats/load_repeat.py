"""Bus load test at identical loads on both backends, 5 repeats each.
Needs Kafka for the Kafka rows (`docker compose up -d`).

Each Kafka run uses its own fresh topic, deleted afterwards. Reusing one topic
across runs makes every run slower than the last (each new reader catches up
from the start of an ever-growing topic: 28 MB of old runs cut 10 x 50 from
~1,000/s to ~115/s), which measures the topic's history, not the bus.

    python scripts/stats/load_repeat.py   ->  docs/results/load_repeat.json
"""

from __future__ import annotations

import secrets
from dataclasses import asdict

import numpy as np
from _common import write

from aris.attestation import PublisherKeyring
from aris.bus import InMemoryRiskBus, RiskBus
from aris.loadtest import run_load_test

REPEATS = 5
LOADS = ((10, 50), (20, 200))


def _bus(backend: str, keyring: PublisherKeyring, topic: str) -> RiskBus:
    if backend == "kafka":
        from aris.kafka_bus import KafkaRiskBus, load_kafka_config

        bootstrap, registry = load_kafka_config()
        return KafkaRiskBus(keyring, bootstrap, registry, topic=topic, max_publisher_share=1.0)
    return InMemoryRiskBus(keyring, max_publisher_share=1.0)


def _drop_topic(topic: str) -> None:
    from kafka.admin import KafkaAdminClient

    from aris.kafka_bus import load_kafka_config

    admin = KafkaAdminClient(bootstrap_servers=load_kafka_config()[0])
    try:
        admin.delete_topics([topic])
    finally:
        admin.close()


def main() -> None:
    runs = []
    for publishers, signals in LOADS:
        for backend in ("memory", "kafka"):
            for rep in range(1, REPEATS + 1):
                keyring = PublisherKeyring()
                topic = f"risk-signals-loadtest-{secrets.token_hex(4)}"
                bus = _bus(backend, keyring, topic)
                try:
                    d = asdict(
                        run_load_test(bus, keyring, publishers, signals, backend_label=backend)
                    )
                finally:
                    close = getattr(bus, "close", None)
                    if close:
                        close()
                    if backend == "kafka":
                        _drop_topic(topic)
                runs.append(
                    {
                        "backend": backend,
                        "publishers": publishers,
                        "signals": d["total_signals"],
                        "repeat": rep,
                        "throughput_per_s": round(d["publish_throughput_per_s"], 1),
                        "lost_updates": d["lost_updates"],
                        "errors": d["errors"],
                        "publish_p50_ms": round(d["publish_latency"]["p50_ms"], 2),
                        "publish_p99_ms": round(d["publish_latency"]["p99_ms"], 2),
                        "lookup_p99_ms": round(d["lookup_latency"]["p99_ms"], 2),
                    }
                )
                print(runs[-1])
    summary = []
    for publishers, signals in LOADS:
        for backend in ("memory", "kafka"):
            group = [r for r in runs if r["backend"] == backend and r["publishers"] == publishers]
            t = [float(r["throughput_per_s"]) for r in group]
            summary.append(
                {
                    "backend": backend,
                    "load": f"{publishers} x {signals}",
                    "throughput_mean": round(float(np.mean(t))),
                    "throughput_sd": round(float(np.std(t, ddof=1))),
                    "throughput_min": min(t),
                    "throughput_max": max(t),
                    "lost_updates": sum(int(r["lost_updates"]) for r in group),
                }
            )
    write(
        "load_repeat.json",
        {"repeats": REPEATS, "kafka_topic": "fresh per run", "summary": summary, "runs": runs},
    )


if __name__ == "__main__":
    main()
