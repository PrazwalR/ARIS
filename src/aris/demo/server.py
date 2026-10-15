"""The showcase server behind the ARIS frontend.

One process, three surfaces:

- ``/api/*``  -- the real BankBot HTTP API (``aris.api.app.create_app``),
  unchanged: ``POST /api/transfers``, ``GET /api/audit/{ref}`` with
  investigator credentials (SS3.7).
- ``/demo/*`` -- the other actors in the hero story, as real operations on the
  same bus: Bank B signs and publishes a flag; BANK-EVIL, a registered member,
  forges a "score 0" retraction in Bank B's name (rejected by the keyring's
  signature check, not by special-casing); a privacy-safe decision feed read
  from the audit sink; a reset.
- ``/``       -- the built frontend (``frontend/app/dist``) and its measured
  data, when present.

Nothing here simulates a decision: every block/allow the frontend shows comes
from ``BankBot.pre_transaction`` over HTTP.

    python -m aris.demo.server            # http://127.0.0.1:8000
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import Receive, Scope, Send

from aris.api.errors import install_error_handlers
from aris.api.app import create_app
from aris.api.config import ApiSettings
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aris.attestation import (
    Publisher,
    PublisherKeyring,
    SignatureInvalid,
    SignedRiskSignal,
    canonical_bytes,
)
from aris.bankbot import InMemoryAuditLog
from aris.bus import InMemoryRiskBus
from aris.hashing import DEV_MODE_ENV_VAR, SALT_ENV_VAR, normalize_ifsc
from aris.risk_id import HmacRiskIdDeriver, publish_id
from aris.schema import CONFIRMED_FRAUD, HIGH_VELOCITY, NEW_BENEFICIARY, RiskSignal

INVESTIGATOR_ID: Final = "reviewer"
INVESTIGATOR_KEY_ENV_VAR: Final = "ARIS_SHOWCASE_INVESTIGATOR_KEY"
MODEL_VERSION: Final = "v0.4-fl"
FEED_LIMIT: Final = 25
DEFAULT_DIST: Final = Path(__file__).resolve().parents[3] / "frontend" / "app" / "dist"


class FlagRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receiver_ifsc: str = Field(min_length=11, max_length=11)
    receiver_account: str = Field(min_length=1, max_length=64)
    risk_score: int = Field(default=92, ge=0, le=100, strict=True)
    confidence: float = Field(default=0.94, ge=0.0, le=1.0)


class AccountRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    receiver_ifsc: str = Field(min_length=11, max_length=11)
    receiver_account: str = Field(min_length=1, max_length=64)


@dataclass
class _World:
    """Everything a reset replaces, swapped atomically under one lock."""

    keyring: PublisherKeyring
    bank_b: Publisher
    bank_evil: Publisher
    bank_evil_key: Ed25519PrivateKey
    bus: InMemoryRiskBus
    audit: InMemoryAuditLog
    api: FastAPI


def _new_world(investigator_key: str) -> _World:
    keyring = PublisherKeyring()
    bank_b = Publisher.generate("BANK-B")
    # A registered member that behaves badly -- the realistic insider threat.
    bank_evil_key = Ed25519PrivateKey.generate()
    bank_evil = Publisher("BANK-EVIL", bank_evil_key)
    keyring.register(bank_b.bank_id, bank_b.public_key)
    keyring.register(bank_evil.bank_id, bank_evil.public_key)
    bus = InMemoryRiskBus(keyring)
    audit = InMemoryAuditLog()
    api = create_app(
        bus, settings=ApiSettings(investigators={INVESTIGATOR_ID: investigator_key}), audit=audit
    )
    return _World(keyring, bank_b, bank_evil, bank_evil_key, bus, audit, api)


def create_showcase_app(
    *, investigator_key: str | None = None, dist_dir: Path | None = DEFAULT_DIST
) -> FastAPI:
    investigator_key = investigator_key or secrets.token_urlsafe(12)
    lock = threading.Lock()
    world = _new_world(investigator_key)
    risk_ids = HmacRiskIdDeriver()

    def current() -> _World:
        with lock:
            return world

    app = FastAPI(title="ARIS showcase")
    install_error_handlers(app)
    app.state.investigator_key = investigator_key

    class _CurrentApi:
        """Forwards /api/* to whichever BankBot API the current world holds,
        so a reset swaps the whole stack rather than mutating it in place."""

        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            await current().api(scope, receive, send)

    app.mount("/api", _CurrentApi())

    def _risk_id(ifsc: str, account: str) -> str:
        try:
            return publish_id(risk_ids, normalize_ifsc(ifsc), account, datetime.now(timezone.utc))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    def _network_view(w: _World, risk_id: str) -> dict[str, object]:
        result = w.bus.lookup(risk_id)
        return {
            "status": result.status.value,
            "score": result.signal.risk_score if result.signal else None,
            "contributing_banks": list(result.contributing_banks),
        }

    @app.get("/demo/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "investigator_id": INVESTIGATOR_ID}

    @app.post("/demo/reset")
    def reset() -> dict[str, str]:
        nonlocal world
        fresh = _new_world(investigator_key)
        with lock:
            world = fresh
        return {"status": "reset"}

    @app.post("/demo/risk-id")
    def risk_id_for(req: AccountRequest) -> dict[str, object]:
        """What the network sees for an account: its pseudonymous id and any
        live signal -- never the account number itself."""
        risk_id = _risk_id(req.receiver_ifsc, req.receiver_account)
        return {"risk_id": risk_id, "network": _network_view(current(), risk_id)}

    @app.post("/demo/flag")
    def flag(req: FlagRequest) -> dict[str, object]:
        w = current()
        risk_id = _risk_id(req.receiver_ifsc, req.receiver_account)
        signal = RiskSignal(
            risk_id=risk_id,
            risk_score=req.risk_score,
            confidence=req.confidence,
            reason_codes=(NEW_BENEFICIARY, HIGH_VELOCITY, CONFIRMED_FRAUD),
            model_version=MODEL_VERSION,
            source_bank_id=w.bank_b.bank_id,
            ttl_hours=24,
        )
        signed = w.bank_b.sign(signal)
        outcome = w.bus.publish(signed)
        return {
            "publisher": w.bank_b.bank_id,
            "outcome": outcome.value,
            "risk_id": risk_id,
            "key_epoch": signal.key_epoch,
            "signature": signed.signature.hex(),
            "network": _network_view(w, risk_id),
        }

    @app.post("/demo/forge")
    def forge(req: AccountRequest) -> dict[str, object]:
        """BANK-EVIL signs a score-0 "retraction" that claims to come from
        BANK-B, signed with BANK-EVIL's own key. The bus verifies it against
        BANK-B's registered key and refuses it."""
        w = current()
        risk_id = _risk_id(req.receiver_ifsc, req.receiver_account)
        claim = RiskSignal(
            risk_id=risk_id,
            risk_score=0,
            confidence=0.99,
            reason_codes=(NEW_BENEFICIARY,),
            model_version=MODEL_VERSION,
            source_bank_id=w.bank_b.bank_id,
        )
        # A real attacker does not use the honest Publisher (which refuses to
        # sign under another bank's name); it signs the canonical bytes with
        # its own key. What stops it is the bus verifying against BANK-B's key.
        forged = SignedRiskSignal(claim, w.bank_evil_key.sign(canonical_bytes(claim)))
        try:
            outcome = w.bus.publish(forged)
            result, reason = outcome.value, None
        except SignatureInvalid as exc:
            result, reason = "rejected", f"signature invalid: {exc}"
        return {
            "signer": w.bank_evil.bank_id,
            "claimed_publisher": w.bank_b.bank_id,
            "result": result,
            "reason": reason,
            "network": _network_view(w, risk_id),
        }

    @app.get("/demo/feed")
    def feed() -> dict[str, object]:
        """Recent BankBot decisions as the network log would show them:
        pseudonymous id, decision, amount, time -- no account numbers."""
        entries = current().audit.entries[-FEED_LIMIT:]
        return {
            "decisions": [
                {
                    "audit_ref": e.transfer_id,
                    "risk_id": e.risk_id,
                    "decision": e.decision.value,
                    "lookup_status": e.lookup_status.value,
                    "amount_minor": e.amount_minor,
                    "currency": e.currency,
                    "decided_at": e.decided_at.isoformat(),
                }
                for e in reversed(entries)
            ]
        }

    if dist_dir is not None and (dist_dir / "index.html").exists():
        index = dist_dir / "index.html"
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")
        if (dist_dir / "data").exists():
            app.mount("/data", StaticFiles(directory=dist_dir / "data"), name="data")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            # Serve real files that live inside dist (favicon etc.); anything
            # else -- including paths the filesystem refuses outright (NUL
            # bytes, over-long names) -- falls back to the single-page app.
            try:
                candidate = (dist_dir / path).resolve()
                inside = dist_dir.resolve() in candidate.parents
                if path and inside and candidate.is_file():
                    return FileResponse(candidate)
            except (OSError, ValueError):
                pass
            return FileResponse(index)

    return app


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="Run the ARIS showcase (frontend + live demo)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    args = parser.parse_args(argv)

    # Show BankBot's own decision lines in this terminal, next to uvicorn's
    # request log, so a live audience can watch each decision being made.
    # They carry the pseudonymous risk_id, never an account number.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)s  %(message)s")
    logging.getLogger("aris").setLevel(logging.INFO)

    if not os.environ.get(SALT_ENV_VAR):
        os.environ[DEV_MODE_ENV_VAR] = "1"
        print(f"[showcase] {SALT_ENV_VAR} not set - using the public development key.", flush=True)
    key = os.environ.get(INVESTIGATOR_KEY_ENV_VAR) or secrets.token_urlsafe(12)
    app = create_showcase_app(investigator_key=key, dist_dir=args.dist)
    if not (args.dist / "index.html").exists():
        print(f"[showcase] no frontend build at {args.dist} - API only.", file=sys.stderr)
    print(f"[showcase] investigator: {INVESTIGATOR_ID} / {key}", flush=True)
    print(f"[showcase] http://{args.host}:{args.port}", flush=True)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
