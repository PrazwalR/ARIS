"""Run the BankBot API for local dev / manual poking:

    ARIS_DEV_MODE=1 python -m aris.api

Set ``ARIS_API_BUS_BACKEND=kafka`` (with ``docker compose up -d`` already
running -- see M3) to back it with the real Kafka bus instead of an empty
in-memory one. The in-memory default starts with no signals published and an
empty keyring: useful to confirm the server runs and to POST allow-decisions,
but nothing will ever come back BLOCK until a bank's own process publishes to
whichever bus this points at.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from aris.api.app import create_app
from aris.api.config import load_settings
from aris.attestation import PublisherKeyring
from aris.bus import InMemoryRiskBus, RiskBus
from aris.hashing import DEV_MODE_ENV_VAR, SALT_ENV_VAR


def _bus(backend: str, keyring: PublisherKeyring) -> RiskBus:
    if backend == "kafka":
        from aris.kafka_bus import KafkaRiskBus, load_kafka_config

        bootstrap, registry_url = load_kafka_config()
        return KafkaRiskBus(keyring, bootstrap, registry_url)
    if backend == "memory":
        return InMemoryRiskBus(keyring)
    raise ValueError(f"unknown bus backend {backend!r}; expected 'memory' or 'kafka'")


def main() -> None:
    if not os.environ.get(SALT_ENV_VAR):
        os.environ[DEV_MODE_ENV_VAR] = "1"
        print(
            f"[api] {SALT_ENV_VAR} is not set - using the public development key.",
            file=sys.stderr,
        )

    settings = load_settings()
    app = create_app(_bus(settings.bus_backend, PublisherKeyring()), settings=settings)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
