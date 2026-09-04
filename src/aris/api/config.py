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
ADMIN_KEY_ENV_VAR: Final = "ARIS_API_ADMIN_KEY"
STEP_UP_AT_ENV_VAR: Final = "ARIS_API_STEP_UP_AT"
BLOCK_AT_ENV_VAR: Final = "ARIS_API_BLOCK_AT"
STEP_UP_ABOVE_AMOUNT_ENV_VAR: Final = "ARIS_API_STEP_UP_ABOVE_AMOUNT_MINOR"
MIN_CONFIDENCE_TO_BLOCK_ENV_VAR: Final = "ARIS_API_MIN_CONFIDENCE_TO_BLOCK"
MIN_BANKS_TO_BLOCK_ENV_VAR: Final = "ARIS_API_MIN_BANKS_TO_BLOCK"


@dataclass(frozen=True)
class ApiSettings:
    bus_backend: str = "memory"
    # None means the analyst audit-lookup endpoint is unreachable rather than
    # falling back to some default -- an unconfigured key must fail closed,
    # the same way a missing ARIS_SALT does in aris.hashing.
    admin_key: str | None = None
    policy: PolicyConfig = field(default_factory=PolicyConfig)


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

    return ApiSettings(
        bus_backend=os.environ.get(BUS_BACKEND_ENV_VAR, "memory"),
        admin_key=os.environ.get(ADMIN_KEY_ENV_VAR),
        policy=PolicyConfig(**policy_kwargs),  # type: ignore[arg-type]
    )
