from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


class RiskSignal(BaseModel):
    risk_id: str
    risk_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str]
    model_version: str
    source_bank_id: str
    ttl_hours: int = 24
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


Decision = Literal["allow", "step_up", "block"]


class PolicyConfig(BaseModel):
    medium_min: int = 50
    high_min: int = 85


def apply_policy(score: int | None, cfg: PolicyConfig | None = None) -> Decision:
    cfg = cfg or PolicyConfig()
    if score is None:
        return "allow"
    if score > cfg.high_min:
        return "block"
    if score >= cfg.medium_min:
        return "step_up"
    return "allow"
