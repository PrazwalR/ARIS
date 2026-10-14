"""Run the BankBot API for local dev / manual poking:

    ARIS_DEV_MODE=1 python -m aris.api

Set ``ARIS_API_BUS_BACKEND=kafka`` (with ``docker compose up -d`` already
running -- see M3) to back it with the real Kafka bus instead of an empty
in-memory one. ``ARIS_API_TRUSTED_PUBLISHERS`` lists the publisher keys this
bank acts on (empty: trusts nobody, can never block), and
``ARIS_API_RISK_ID_MODE`` picks the risk_id scheme (``hmac`` | ``hsm`` |
``oprf``) -- see ``aris.api.config`` for each mode's settings.
"""

from __future__ import annotations

import argparse
import os
import sys
from contextlib import ExitStack

import uvicorn

from aris.api.app import create_app
from aris.api.config import load_settings
from aris.api.wiring import build_keyring, build_risk_ids
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the ARIS BankBot API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    if not os.environ.get(SALT_ENV_VAR):
        os.environ[DEV_MODE_ENV_VAR] = "1"
        print(
            f"[api] {SALT_ENV_VAR} is not set - using the public development key.",
            file=sys.stderr,
        )

    settings = load_settings()
    with ExitStack() as resources:
        risk_ids = build_risk_ids(settings, resources)
        bus = _bus(settings.bus_backend, build_keyring(settings))
        resources.callback(getattr(bus, "close", lambda: None))
        print(
            f"[api] bank={settings.bank_id} risk_id_mode={settings.risk_id_mode} "
            f"trusted_publishers={sorted(settings.trusted_publishers)}",
            file=sys.stderr,
            flush=True,
        )
        app = create_app(bus, settings=settings, risk_ids=risk_ids)
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
