"""Kafka-backed ``RiskBus`` (M3): the same interface as ``InMemoryRiskBus``, over
a real broker instead of one process's memory.

Architecture: ``publish()`` verifies and applies the signal to a *local*
materialized view -- an ``InMemoryRiskBus``, reusing every M0 admission, replay,
and quota rule rather than reimplementing it -- then produces it to the
compacted ``risk-signals`` topic. ``lookup()`` only ever reads the local view,
so an already-warm bucket never blocks on the network.

Partitioned by ``risk_id`` prefix (docs/SECURITY.md SS3.4), not eagerly
replicated in full: the topic has ``PREFIX_BUCKET_PARTITIONS`` partitions, one
per possible first-byte value of ``risk_id``, and each ``KafkaRiskBus`` instance
consumes only the *buckets it has actually needed* -- the one(s) it has
published to or looked up -- catching each up to a snapshot of its current end
offset before answering, then keeping it live in the background from then on.
Originally (M3) every instance replicated the entire topic; that gave every
member bank visibility into every *other* bank's complete published risk_id
set, not just the accounts it actually queried -- a bigger leak than the
"bus operator learns your exact query" concern SS3.4 was written against, and
one full replication could not avoid by construction. A cold lookup here costs
a bounded, synchronous catch-up of one partition (~hundreds of records, not the
whole topic) instead of being instant against an already-fully-warm view --
the price of that other bank's visibility bound.

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
import time
from typing import Any, Final

from kafka import KafkaConsumer, KafkaProducer, TopicPartition
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
from aris.schema import LookupStatus, RiskSignal
from aris.schema_registry import SchemaRegistryClient, decode, encode

logger = logging.getLogger(__name__)

DEFAULT_TOPIC: Final = "risk-signals"
SCHEMA_SUBJECT: Final = "risk-signals-value"
_PRODUCE_ACK_TIMEOUT_S: Final = 10

# One partition per possible first-byte value of a risk_id (a 64-hex-char, i.e.
# 32-byte, HMAC digest -- uniform, so this bucketing is even). See module
# docstring for why this replaces full-topic replication.
PREFIX_BUCKET_PARTITIONS: Final = 256
# How long lookup() blocks synchronously catching up a cold bucket before
# giving up and reporting UNAVAILABLE -- the fail-closed contract RiskBus.lookup
# requires, not a silent fall-through to a local (and here, meaningless) miss.
_LOOKUP_WARMUP_TIMEOUT_S: Final = 10.0


def bucket_for_risk_id(risk_id: str) -> int:
    """Which of the topic's ``PREFIX_BUCKET_PARTITIONS`` partitions ``risk_id``
    lives in. A pure function of the id, so publisher and lookup sides always
    agree without coordination."""
    return int(risk_id[:2], 16) % PREFIX_BUCKET_PARTITIONS


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
    num_partitions: int = PREFIX_BUCKET_PARTITIONS,
    replication_factor: int = 1,
) -> None:
    """Create the risk-signals topic with log compaction enabled, if it does not
    already exist. Idempotent -- safe to call from every process on startup.

    Compaction (not the default "delete" cleanup policy new topics get) is what
    makes this a bounded materialized-view source rather than an ever-growing
    log: Kafka keeps only the latest record per key, which is exactly what a
    consumer rebuilding `risk_id:bank -> latest signal` needs.

    Verifies partition count on an already-existing topic rather than trusting
    it: `KafkaRiskBus` addresses partitions directly by `bucket_for_risk_id`,
    so a topic created under an old partition count (e.g. before
    PREFIX_BUCKET_PARTITIONS existed) would otherwise fail confusingly deep
    inside produce/assign calls instead of here, at the boundary.
    """
    admin = KafkaAdminClient(bootstrap_servers=bootstrap_servers)
    try:
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
            return
        except TopicAlreadyExistsError:
            pass
        (described,) = admin.describe_topics([topic])
        actual = len(described["partitions"])
        if actual != num_partitions:
            raise RuntimeError(
                f"topic {topic!r} already exists with {actual} partitions, "
                f"expected {num_partitions}; delete it (dev/test only -- never "
                "in a deployment with live data) or point at a fresh topic name"
            )
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
    """See module docstring for the bucketed-consumption architecture."""

    def __init__(
        self,
        keyring: PublisherKeyring,
        bootstrap_servers: str,
        schema_registry_url: str,
        topic: str = DEFAULT_TOPIC,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_publisher_share: float = DEFAULT_MAX_PUBLISHER_SHARE,
        lookup_timeout_s: float = _LOOKUP_WARMUP_TIMEOUT_S,
    ) -> None:
        self._topic = topic
        self._lookup_timeout_s = lookup_timeout_s
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
        # No consumer group and no topic-level subscribe(): partitions are
        # assigned manually, one bucket at a time, as publish()/lookup() need
        # them -- see the module docstring. group_id=None either way, since a
        # consumer group would load-balance partitions across members instead
        # of giving each process its own view.
        self._consumer = KafkaConsumer(
            bootstrap_servers=bootstrap_servers,
            group_id=None,
            enable_auto_commit=False,
            key_deserializer=_Utf8KeyDeserializer(),
            value_deserializer=_PassthroughValueDeserializer(),
            consumer_timeout_ms=200,
        )
        # Guards _wanted (buckets a caller has asked for) and _ready (buckets
        # caught up to their catch-up snapshot at least once); _catch_up_target
        # is touched only by the consumer thread and needs no lock.
        self._cv = threading.Condition()
        self._wanted: set[int] = set()
        self._ready: set[int] = set()
        self._catch_up_target: dict[int, int] = {}
        self._stop = threading.Event()
        self._consumer_thread = threading.Thread(
            target=self._consume_loop, name="kafka-risk-bus-consumer", daemon=True
        )
        self._consumer_thread.start()

    def publish(self, signed: SignedRiskSignal) -> PublishOutcome:
        outcome = self._local.publish(signed)
        if outcome is PublishOutcome.ACCEPTED:
            bucket = bucket_for_risk_id(signed.signal.risk_id)
            key = f"{signed.signal.risk_id}:{signed.signal.source_bank_id}"
            try:
                self._producer.send(
                    self._topic, key=key, value=_signal_to_wire(signed), partition=bucket
                ).get(timeout=_PRODUCE_ACK_TIMEOUT_S)
            except KafkaError as exc:
                logger.exception("kafka produce failed topic=%s key=%s", self._topic, key)
                raise KafkaPublishError(
                    f"signal for {key} accepted locally but not published to Kafka"
                ) from exc
            # Start tracking this bucket going forward (not blocking on it: the
            # local apply above already reflects this process's own write), so
            # a peer's later contribution to the same risk_id is picked up
            # without a separate lookup() ever having to trigger it.
            self._want_bucket(bucket)
        return outcome

    def lookup(self, risk_id: str) -> LookupResult:
        bucket = bucket_for_risk_id(risk_id)
        if not self._ensure_bucket_ready(bucket, timeout=self._lookup_timeout_s):
            # Contract (RiskBus.lookup): report UNAVAILABLE rather than fall
            # through to a local view that may not yet hold this bucket's
            # data -- an unwarmed bucket must never present as a clean account.
            logger.warning("lookup for risk_id=%s timed out warming bucket=%d", risk_id, bucket)
            return LookupResult(status=LookupStatus.UNAVAILABLE)
        return self._local.lookup(risk_id)

    def _want_bucket(self, bucket: int) -> None:
        with self._cv:
            if bucket not in self._wanted:
                self._wanted.add(bucket)
                self._cv.notify_all()

    def _ensure_bucket_ready(self, bucket: int, timeout: float) -> bool:
        self._want_bucket(bucket)
        with self._cv:
            deadline = time.monotonic() + timeout
            while bucket not in self._ready:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._cv.wait(timeout=remaining)
            return True

    def _mark_ready(self, buckets: set[int]) -> None:
        if not buckets:
            return
        with self._cv:
            self._ready |= buckets
            self._cv.notify_all()

    def _consume_loop(self) -> None:
        assigned: set[int] = set()
        while not self._stop.is_set():
            try:
                assigned = self._consume_loop_step(assigned)
            except Exception:
                # A transient broker error here must not kill this thread: an
                # already-dead consumer thread means every *future* lookup --
                # not just ones mid-flight -- times out and reports
                # UNAVAILABLE forever, a strictly worse failure mode than the
                # stale-but-answerable local view a brief outage leaves
                # otherwise. Back off briefly so a persistent outage does not
                # spin this thread hot while it retries.
                logger.exception("kafka consumer loop error; retrying")
                self._stop.wait(timeout=1.0)

    def _consume_loop_step(self, assigned: set[int]) -> set[int]:
        with self._cv:
            wanted = set(self._wanted)
        new_buckets = wanted - assigned
        if new_buckets:
            assigned = wanted
            self._consumer.assign([TopicPartition(self._topic, b) for b in assigned])
            new_tps = [TopicPartition(self._topic, b) for b in new_buckets]
            self._consumer.seek_to_beginning(*new_tps)
            end_offsets = self._consumer.end_offsets(new_tps)
            immediately_ready = set()
            for tp in new_tps:
                target = end_offsets[tp]
                self._catch_up_target[tp.partition] = target
                if target == 0:  # nothing on this bucket yet; nothing to catch up on
                    immediately_ready.add(tp.partition)
            self._mark_ready(immediately_ready)

        if not assigned:
            # Nothing warmed yet: avoid a tight spin waiting for a request.
            with self._cv:
                self._cv.wait(timeout=0.5)
            return assigned

        batch = self._consumer.poll(timeout_ms=200)
        for records in batch.values():
            for record in records:
                if self._stop.is_set():
                    break
                self._apply_record(record.value)

        newly_ready = set()
        for bucket in assigned - self._ready:
            target = self._catch_up_target.get(bucket)
            if target is None:
                continue
            if self._consumer.position(TopicPartition(self._topic, bucket)) >= target:
                newly_ready.add(bucket)
        self._mark_ready(newly_ready)
        return assigned

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
        with self._cv:
            self._cv.notify_all()  # wake any lookup() blocked waiting on a bucket
        self._consumer_thread.join(timeout=5)
        self._producer.close(timeout=5)
        self._consumer.close()

    def __enter__(self) -> KafkaRiskBus:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __len__(self) -> int:
        return len(self._local)
