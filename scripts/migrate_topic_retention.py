"""Move an existing risk-signals topic off log compaction (docs/SECURITY.md SS3.8).

Under compaction any member with Write could erase a peer's signed flag by
appending a record under the same key; see aris.kafka_bus's module docstring.
This switches the topic to time-based retention that outlasts every signal's
TTL. Operator action, run once per cluster:

    python scripts/migrate_topic_retention.py [--bootstrap localhost:9092]
"""

from __future__ import annotations

import argparse

from kafka.admin import ConfigResource, ConfigResourceType, KafkaAdminClient

from aris.kafka_bus import DEFAULT_TOPIC, TOPIC_CONFIGS, ensure_topic


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--bootstrap", default="localhost:9092")
    parser.add_argument("--topic", default=DEFAULT_TOPIC)
    args = parser.parse_args(argv)

    admin = KafkaAdminClient(bootstrap_servers=args.bootstrap)
    try:
        admin.alter_configs(
            [ConfigResource(ConfigResourceType.TOPIC, args.topic, configs=dict(TOPIC_CONFIGS))]
        )
    finally:
        admin.close()
    ensure_topic(args.bootstrap, args.topic)  # re-verifies; raises if still unsafe
    print(f"{args.topic}: {TOPIC_CONFIGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
