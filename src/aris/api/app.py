"""M4 -- BankBot's pre-transaction check over HTTP.

Thin: every actual decision still comes from `aris.bankbot.BankBot`. This layer
only does HTTP concerns -- request/response shaping, the analyst audit-lookup
endpoint's auth check, and wiring a `RiskBus` implementation in.

Step-up auth is a stub by design (see PHASES.md M4): a real deployment already
has an OTP/step-up flow, and reimplementing one here would be exactly the kind
of fake-but-plausible code this project's standards reject. `POST /transfers`
reports `step_up_required` honestly and stops there -- it does not pretend to
complete a challenge it cannot actually issue.
"""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Request

from aris import __version__
from aris.api.config import ApiSettings, load_settings
from aris.bankbot import (
    AuditRecord,
    AuditSink,
    BankBot,
    BankBotDecision,
    InMemoryAuditLog,
    TransferRequest,
)
from aris.bus import RiskBus
from aris.schema import Decision
from pydantic import BaseModel, ConfigDict


class TransferResponse(BaseModel):
    """What a caller gets back from `POST /transfers`.

    Deliberately the same shape `BankBotDecision` already enforces (no score,
    no flagging bank, no risk_id -- see `aris.bankbot._user_message`), plus one
    HTTP-facing convenience flag so a client doesn't need to string-compare
    `decision` to know whether to prompt for step-up.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: Decision
    user_message: str
    audit_ref: str
    step_up_required: bool

    @classmethod
    def from_decision(cls, decision: BankBotDecision) -> TransferResponse:
        return cls(
            decision=decision.decision,
            user_message=decision.user_message,
            audit_ref=decision.audit_ref,
            step_up_required=decision.decision is Decision.STEP_UP,
        )


def create_app(
    bus: RiskBus,
    settings: ApiSettings | None = None,
    audit: AuditSink | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    audit = audit if audit is not None else InMemoryAuditLog()
    bot = BankBot(bus, policy=settings.policy, audit=audit)

    app = FastAPI(title="ARIS BankBot API", version=__version__)
    # Per-instance state, not module-level: create_app() can be called more
    # than once (tests do, repeatedly) and each app must own its own bot/audit
    # sink/settings. Routes reach these through `request.app.state`, not a
    # Depends()-wrapped closure -- FastAPI resolves Annotated[...] dependency
    # markers via typing.get_type_hints() against the route function's module
    # globals, and this file uses `from __future__ import annotations`
    # (postponed evaluation), so a closure only reachable as a local variable
    # inside this factory function cannot be resolved that way; it silently
    # falls back to treating the parameter as an ordinary query param instead
    # of raising, which is a much worse failure mode than just not using that
    # pattern here.
    app.state.bot = bot
    app.state.audit = audit
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/transfers", response_model=TransferResponse)
    def create_transfer(req: TransferRequest, request: Request) -> TransferResponse:
        bot: BankBot = request.app.state.bot
        return TransferResponse.from_decision(bot.pre_transaction(req))

    @app.get("/audit/{audit_ref}", response_model=AuditRecord)
    def get_audit_record(
        audit_ref: str,
        request: Request,
        x_admin_key: Annotated[str | None, Header()] = None,
    ) -> AuditRecord:
        api_settings: ApiSettings = request.app.state.settings
        audit_sink: AuditSink = request.app.state.audit
        # Evidence is only reachable through this authenticated path -- see
        # BankBotDecision's docstring. An unconfigured admin key disables the
        # endpoint entirely rather than falling back to "no auth required".
        if api_settings.admin_key is None:
            raise HTTPException(status_code=503, detail="audit lookup is not configured")
        if x_admin_key is None or not hmac.compare_digest(x_admin_key, api_settings.admin_key):
            raise HTTPException(status_code=401, detail="missing or invalid admin key")
        record = audit_sink.get(audit_ref)
        if record is None:
            raise HTTPException(status_code=404, detail="no audit record for that reference")
        return record

    return app
