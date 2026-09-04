"""Kafka-backed ``RiskBus`` (M3): the same interface as ``InMemoryRiskBus``, over
a real broker instead of one process's memory.

Architecture: ``publish()`` verifies and applies the signal to a *local*
materialized view -- an ``InMemoryRiskBus``, reusing every M0 admission, replay,
and quota rule rather than reimplementing it -- then produces it to the
compacted ``risk-signals`` topic for every other process to eventually see. A
background consumer thread applies every record it reads, including this
process's own, to that same local view through the identical code path; seeing
its own record again is naturally a no-op via the existing replay guard.
``lookup()`` only ever reads the local view, so it never blocks on the network.

Kafka key is ``f"{risk_id}:{source_bank_id}"``, not ``risk_id`` alone. Log
compaction keeps only the latest value per key, and per-bank tracking
(``min_banks_to_block``, ``contributing_banks``) needs each bank's contribution
to survive compaction independently -- a bare ``risk_id`` key would let one
bank's signal silently evict a different bank's on the next compaction pass.

Known limitation: applying a signal to the local view and producing it to Kafka
are two separate steps, not one transaction. If the local apply succeeds but the
Kafka send then fails (broker unreachable), this process's own view holds a
signal no other bank will ever see, and ``publish()`` raises so the caller knows
propagation did not complete -- but does not roll back the local apply. A
publisher that gets an exception here should treat the signal as *not yet*
network-visible and retry, not assume it never landed anywhere.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Final

from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import KafkaError, TopicAlreadyExistsError
from kafka.serializer import Deserializer, Serializer

from aris.attestation import PublisherKeyring, SignatureInvalid, SignedRiskSignal, UnknownPublisher
from aris.bus import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_MAX_PUBLISHER_SHARE,
    InMemoryRiskBus,
    LookupResult,
    PublishOutcome,
    RiskBus,
)
from aris.schema import RiskSignal
from aris.schema_registry import SchemaRegistryClient, decode, encode

logger = logging.getLogger(__name__)

DEFAULT_TOPIC: Final = "risk-signals"
SCHEMA_SUBJECT: Final = "risk-signals-value"
_PRODUCE_ACK_TIMEOUT_S: Final = 10

BOOTSTRAP_SERVERS_ENV_VAR: Final = "ARIS_KAFKA_BOOTSTRAP_SERVERS"
SCHEMA_REGISTRY_URL_ENV_VAR: Final = "ARIS_SCHEMA_REGISTRY_URL"
# Match docker-compose.yml's host-exposed ports -- convenient local-dev
# defaults, not a claim about any real deployment's addresses.
_DEV_BOOTSTRAP_SERVERS: Final = "localhost:9092"
_DEV_SCHEMA_REGISTRY_URL: Final = "http://localhost:8081"


def load_kafka_config() -> tuple[str, str]:
    """Return (bootstrap_servers, schema_registry_url) from the environment,
    falling back to the docker-compose.yml local-dev defaults.
    """
    bootstrap = os.environ.get(BOOTSTRAP_SERVERS_ENV_VAR, _DEV_BOOTSTRAP_SERVERS)
    registry = os.environ.get(SCHEMA_REGISTRY_URL_ENV_VAR, _DEV_SCHEMA_REGISTRY_URL)
    return bootstrap, registry


class KafkaPublishError(Exception):
    """The signal was accepted locally but could not be produced to Kafka.

    Other banks will not see this signal until a retry succeeds -- this process
    is not a substitute for the network, only a head start on it.
    """


def ensure_topic(
    bootstrap_servers: str,
    topic: str = DEFAULT_TOPIC,
    num_partitions: int = 1,
    replication_factor: int = 1,
) -> None:
    """Create the risk-signals topic with log compaction enabled, if it does not
    already exist. Idempotent -- safe to call from every process on startup.

    Compaction (not the default "delete" cleanup policy new topics get) is what
    makes this a bounded materialized-view source rather than an ever-growing
    log: Kafka keeps only the latest record per key, which is exactly what a
    consumer rebuilding `risk_id:bank -> latest signal` needs.
    """
    admin = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
        admin.create_topics(
            [
                NewTopic(
                    name=topic,
                    num_partitions=num_partitions,
                    replication_factor=replication_factor,
                    topic_configs={"cleanup.policy": "compact"},
                )
            ]
        )
    except TopicAlreadyExistsError:
        pass
    finally:
        admin.close()


class _Utf8KeySerializer(Serializer):  # type: ignore[misc]
    def serialize(self, _topic: str, _headers: list[tuple[str, bytes]], data: Any) -> bytes:
        key: str = data
        return key.encode("utf-8")


class _Utf8KeyDeserializer(Deserializer):  # type: ignore[misc]
    def deserialize(
        self, _topic: str, _headers: list[tuple[str, bytes]], data: bytes
    ) -> str | None:
        return data.decode("utf-8") if data is not None else None


class _SchemaWireValueSerializer(Serializer):  # type: ignore[misc]
    """Wraps a JSON-able payload in the Confluent wire format for a fixed
    schema ID, decided once at KafkaRiskBus construction time (see
    `aris.schema_registry.encode`).
    """

    def __init__(self, schema_id: int) -> None:
        self._schema_id = schema_id

    def serialize(self, _topic: str, _headers: list[tuple[str, bytes]], data: Any) -> bytes:
        return encode(self._schema_id, data)


class _PassthroughValueDeserializer(Deserializer):  # type: ignore[misc]
    """No-op: `KafkaRiskBus._apply_record` decodes the wire format itself,
    since it needs the schema ID carried in each message, not just the payload.
    """

    def deserialize(self, _topic: str, _headers: list[tuple[str, bytes]], data: bytes) -> bytes:
        return data


def _signal_to_wire(signed: SignedRiskSignal) -> dict[str, Any]:
    return {
        "signal": signed.signal.model_dump(mode="json"),
        "signature": signed.signature.hex(),
    }


def _signal_from_wire(payload: dict[str, Any]) -> SignedRiskSignal:
    return SignedRiskSignal(
        signal=RiskSignal.model_validate(payload["signal"]),
        signature=bytes.fromhex(payload["signature"]),
    )


class KafkaRiskBus(RiskBus):
    """See module docstring for the materialized-view architecture."""

    def __init__(
        self,
        keyring: PublisherKeyring,
        bootstrap_servers: str,
        schema_registry_url: str,
        topic: str = DEFAULT_TOPIC,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_publisher_share: float = DEFAULT_MAX_PUBLISHER_SHARE,
    ) -> None:
        self._topic = topic
        self._local = InMemoryRiskBus(
            keyring, max_entries=max_entries, max_publisher_share=max_publisher_share
        )
        ensure_topic(bootstrap_servers, topic=topic)
        registry = SchemaRegistryClient(schema_registry_url)
        self._schema_id = registry.register(SCHEMA_SUBJECT, RiskSignal.model_json_schema())

        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            key_serializer=_Utf8KeySerializer(),
            value_serializer=_SchemaWireValueSerializer(self._schema_id),
            acks="all",
        )
        # No consumer group: each instance needs its own full copy of the
        # topic's (compacted) history to build a private materialized view, not
        # a load-balanced slice of it the way group members would split work.
        self._consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=None,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            key_deserializer=_Utf8KeyDeserializer(),
            value_deserializer=_PassthroughValueDeserializer(),
            consumer_timeout_ms=1000,
        )
        self._stop = threading.Event()
        self._consumer_thread = threading.Thread(
            target=self._consume_loop, name="kafka-risk-bus-consumer", daemon=True
        )
        self._consumer_thread.start()

    def publish(self, signed: SignedRiskSignal) -> PublishOutcome:
        outcome = self._local.publish(signed)
        if outcome is PublishOutcome.ACCEPTED:
            key = f"{signed.signal.risk_id}:{signed.signal.source_bank_id}"
            try:
                self._producer.send(self._topic, key=key, value=_signal_to_wire(signed)).get(
                    timeout=_PRODUCE_ACK_TIMEOUT_S
                )
            except KafkaError as exc:
                logger.exception("kafka produce failed topic=%s key=%s", self._topic, key)
                raise KafkaPublishError(
                    f"signal for {key} accepted locally but not published to Kafka"
                ) from exc
        return outcome

    def lookup(self, risk_id: str) -> LookupResult:
        return self._local.lookup(risk_id)

    def _consume_loop(self) -> None:
        while not self._stop.is_set():
            for record in self._consumer:
                if self._stop.is_set():
                    break
                self._apply_record(record.value)

    def _apply_record(self, raw_value: bytes) -> None:
        try:
            _schema_id, payload = decode(raw_value)
            signed = _signal_from_wire(payload)
            self._local.publish(signed)
        except (UnknownPublisher, SignatureInvalid) as exc:
            # A message on the topic that does not verify -- whether from a
            # misconfigured peer or direct topic-write access bypassing
            # publish() -- must not crash the consumer loop or silently corrupt
            # the local view. Reject it the same way publish() would.
            logger.warning("rejected untrusted/invalid risk-signal record: %s", exc)
        except Exception:
            logger.exception("failed to apply consumed risk-signal record")

    def close(self) -> None:
        self._stop.set()
        self._consumer_thread.join(timeout=5)
        self._producer.close(timeout=5)
        self._consumer.close()

    def __enter__(self) -> KafkaRiskBus:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self._local)
