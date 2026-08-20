from __future__ import annotations

import hashlib
import os


def load_salt() -> bytes:
    """Shared consortium salt. Prototype: ARIS_SALT env, else a documented demo salt.

    Production: load from HSM/KMS. Never put the real salt on the bus.
    """
    raw = os.environ.get("ARIS_SALT", "aris-demo-consortium-salt-not-for-prod")
    return raw.encode("utf-8")


def risk_id_for_account(account: str, salt: bytes | None = None) -> str:
    """risk_id = SHA256(ACC ∥ SALT). Same account → same id at every bank."""
    if salt is None:
        salt = load_salt()
    material = account.encode("utf-8") + salt
    return hashlib.sha256(material).hexdigest()
