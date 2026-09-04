"""Minimal Confluent Schema Registry client for the ``risk-signals`` JSON schema.

Confluent Schema Registry is normally driven through ``confluent-kafka``, which
wraps librdkafka (a C library). ARIS deliberately stays on ``kafka-python`` (pure
Python, matching the project's "no heavy/exotic dependency" pattern from M1/M2),
so this talks to the registry's plain REST API instead -- registration and schema
ID lookups are a handful of JSON HTTP calls, not a reason to add a C dependency.

Wire format matches Confluent's convention exactly (`Schema Registry docs
<https://docs.confluent.io/platform/current/schema-registry/fundamentals/serdes-develop/index.html#wire-format>`_)
so any Confluent-ecosystem consumer (a Java service, ``kafkacat -s``, etc.) can
decode ARIS messages without knowing anything ARIS-specific: one magic byte
(0x0), a 4-byte big-endian schema ID, then the JSON payload.
"""

from __future__ import annotations

import json
import struct
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAGIC_BYTE: Final = 0x0
_HEADER = struct.Struct(">bi")  # magic byte + 4-byte big-endian schema ID


class SchemaRegistryError(Exception):
    """The registry rejected a request or could not be reached."""


class SchemaRegistryClient:
    """Talks to a Confluent-compatible Schema Registry over its REST API."""

    def __init__(self, base_url: str, timeout: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/vnd.schemaregistry.v1+json"},
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise SchemaRegistryError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise SchemaRegistryError(f"cannot reach schema registry at {self.base_url}") from exc

    def register(self, subject: str, json_schema: dict[str, Any]) -> int:
        """Register (or fetch the existing ID for) a JSON Schema under `subject`.

        Confluent Schema Registry treats JSON Schema as `schemaType: "JSON"`; the
        schema itself travels as a JSON-encoded string within the request body.
        """
        body = {"schemaType": "JSON", "schema": json.dumps(json_schema)}
        result = self._request("POST", f"/subjects/{subject}/versions", body)
        return int(result["id"])

    def schema_exists(self, subject: str) -> bool:
        try:
            self._request("GET", f"/subjects/{subject}/versions/latest")
            return True
        except SchemaRegistryError:
            return False


def encode(schema_id: int, payload: dict[str, Any]) -> bytes:
    """Wrap a JSON payload in Confluent's wire format for this schema ID."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return _HEADER.pack(MAGIC_BYTE, schema_id) + body


def decode(message: bytes) -> tuple[int, dict[str, Any]]:
    """Unwrap a Confluent-wire-format message into (schema_id, payload)."""
    if len(message) < _HEADER.size:
        raise SchemaRegistryError(f"message too short to carry a schema header: {len(message)}B")
    magic, schema_id = _HEADER.unpack_from(message, 0)
    if magic != MAGIC_BYTE:
        raise SchemaRegistryError(f"unexpected magic byte {magic!r}, expected {MAGIC_BYTE!r}")
    payload = json.loads(message[_HEADER.size :].decode("utf-8"))
    if not isinstance(payload, dict):
        raise SchemaRegistryError("decoded payload is not a JSON object")
    return schema_id, payload
