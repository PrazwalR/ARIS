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
# Which bank this API instance decides for -- the identity it presents to the
# OPRF authority.
BANK_ID_ENV_VAR: Final = "ARIS_API_BANK_ID"
# "BANK-B:<64 hex chars Ed25519 public key>,..." -- the publishers whose
# signals this bank acts on. Empty means it trusts nobody and can never block.
TRUSTED_PUBLISHERS_ENV_VAR: Final = "ARIS_API_TRUSTED_PUBLISHERS"
# "hmac" (default) | "hsm" | "oprf" -- must match what publishers sign under.
RISK_ID_MODE_ENV_VAR: Final = "ARIS_API_RISK_ID_MODE"
OPRF_URL_ENV_VAR: Final = "ARIS_API_OPRF_URL"
OPRF_BANK_KEY_ENV_VAR: Final = "ARIS_API_OPRF_BANK_KEY"
OPRF_FINGERPRINT_ENV_VAR: Final = "ARIS_API_OPRF_FINGERPRINT"
HSM_KEY_LABEL_ENV_VAR: Final = "ARIS_HSM_KEY_LABEL"

RISK_ID_MODES: Final = ("hmac", "hsm", "oprf")


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
    bank_id: str = "BANK-A"
    trusted_publishers: dict[str, str] = field(default_factory=dict)
    risk_id_mode: str = "hmac"
    oprf_url: str | None = None
    oprf_bank_key: str | None = None
    oprf_fingerprint: str | None = None
    hsm_key_label: str | None = None

    def __post_init__(self) -> None:
        if self.risk_id_mode not in RISK_ID_MODES:
            raise ValueError(
                f"risk_id_mode must be one of {RISK_ID_MODES}, got {self.risk_id_mode!r}"
            )
        if self.risk_id_mode == "oprf":
            missing = [
                name
                for name, value in (
                    (OPRF_URL_ENV_VAR, self.oprf_url),
                    (OPRF_BANK_KEY_ENV_VAR, self.oprf_bank_key),
                    (OPRF_FINGERPRINT_ENV_VAR, self.oprf_fingerprint),
                )
                if not value
            ]
            if missing:
                # The fingerprint above all: without it, any server that
                # answers at the URL defines this bank's risk_ids.
                raise ValueError(f"risk_id_mode=oprf needs {', '.join(missing)}")


def parse_credentials(raw: str, env_var: str) -> dict[str, str]:
    """Parse ``"id1:secret1,id2:secret2"`` into ``{id: secret}``. Fails loudly
    on a malformed entry rather than silently dropping one -- a dropped
    credential locks someone out with no indication why."""
    credentials: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise RuntimeError(f"{env_var} entry {pair!r} must be 'id:secret'")
        ident, secret = (part.strip() for part in pair.split(":", 1))
        if not ident or not secret:
            raise RuntimeError(f"{env_var} entry {pair!r} has an empty id or secret")
        credentials[ident] = secret
    return credentials


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
    investigators = (
        parse_credentials(raw_investigators, INVESTIGATORS_ENV_VAR) if raw_investigators else {}
    )

    raw_publishers = os.environ.get(TRUSTED_PUBLISHERS_ENV_VAR)
    trusted = (
        parse_credentials(raw_publishers, TRUSTED_PUBLISHERS_ENV_VAR) if raw_publishers else {}
    )

    return ApiSettings(
        bus_backend=os.environ.get(BUS_BACKEND_ENV_VAR, "memory"),
        investigators=investigators,
        policy=PolicyConfig(**policy_kwargs),  # type: ignore[arg-type]
        bank_id=os.environ.get(BANK_ID_ENV_VAR, "BANK-A"),
        trusted_publishers=trusted,
        risk_id_mode=os.environ.get(RISK_ID_MODE_ENV_VAR, "hmac"),
        oprf_url=os.environ.get(OPRF_URL_ENV_VAR),
        oprf_bank_key=os.environ.get(OPRF_BANK_KEY_ENV_VAR),
        oprf_fingerprint=os.environ.get(OPRF_FINGERPRINT_ENV_VAR),
        hsm_key_label=os.environ.get(HSM_KEY_LABEL_ENV_VAR),
    )
