"""M4 API configuration -- environment-driven, matching the ARIS_SALT /
ARIS_KAFKA_* pattern used elsewhere in this repo rather than pulling in a
separate settings-management dependency for a handful of values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Final

from aris.schema import PolicyConfig

BUS_BACKEND_ENV_VAR: Final = "ARIS_API_BUS_BACKEND"  # "memory" | "kafka"
# "id1:key1,id2:key2" -- a named investigator per shared secret, not one
# shared admin key. See docs/SECURITY.md SS3.7.
INVESTIGATORS_ENV_VAR: Final = "ARIS_API_INVESTIGATORS"
STEP_UP_AT_ENV_VAR: Final = "ARIS_API_STEP_UP_AT"
BLOCK_AT_ENV_VAR: Final = "ARIS_API_BLOCK_AT"
STEP_UP_ABOVE_AMOUNT_ENV_VAR: Final = "ARIS_API_STEP_UP_ABOVE_AMOUNT_MINOR"
MIN_CONFIDENCE_TO_BLOCK_ENV_VAR: Final = "ARIS_API_MIN_CONFIDENCE_TO_BLOCK"
MIN_BANKS_TO_BLOCK_ENV_VAR: Final = "ARIS_API_MIN_BANKS_TO_BLOCK"


@dataclass(frozen=True)
class ApiSettings:
    bus_backend: str = "memory"
    # Empty means the analyst audit-lookup endpoint is unreachable rather than
    # falling back to some default -- no configured investigators must fail
    # closed, the same way a missing ARIS_SALT does in aris.hashing.
    # investigator_id -> shared secret. A shared secret per name, not one
    # admin key for everyone -- see docs/SECURITY.md SS3.7.
    investigators: dict[str, str] = field(default_factory=dict)
    policy: PolicyConfig = field(default_factory=PolicyConfig)


def _parse_investigators(raw: str) -> dict[str, str]:
    investigators: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise RuntimeError(f"{INVESTIGATORS_ENV_VAR} entry {pair!r} must be 'id:key'")
        investigator_id, key = (part.strip() for part in pair.split(":", 1))
        if not investigator_id or not key:
            raise RuntimeError(f"{INVESTIGATORS_ENV_VAR} entry {pair!r} has an empty id or key")
        investigators[investigator_id] = key
    return investigators


def load_settings() -> ApiSettings:
    policy_kwargs: dict[str, int | float] = {}
    if (v := os.environ.get(STEP_UP_AT_ENV_VAR)) is not None:
        policy_kwargs["step_up_at"] = int(v)
    if (v := os.environ.get(BLOCK_AT_ENV_VAR)) is not None:
        policy_kwargs["block_at"] = int(v)
    if (v := os.environ.get(STEP_UP_ABOVE_AMOUNT_ENV_VAR)) is not None:
        policy_kwargs["step_up_above_amount_minor"] = int(v)
    if (v := os.environ.get(MIN_CONFIDENCE_TO_BLOCK_ENV_VAR)) is not None:
        policy_kwargs["min_confidence_to_block"] = float(v)
    if (v := os.environ.get(MIN_BANKS_TO_BLOCK_ENV_VAR)) is not None:
        policy_kwargs["min_banks_to_block"] = int(v)

    raw_investigators = os.environ.get(INVESTIGATORS_ENV_VAR)
    investigators = _parse_investigators(raw_investigators) if raw_investigators else {}

    return ApiSettings(
        bus_backend=os.environ.get(BUS_BACKEND_ENV_VAR, "memory"),
        investigators=investigators,
        policy=PolicyConfig(**policy_kwargs),  # type: ignore[arg-type]
    )
