"""Derivation of the pseudonymous ``risk_id`` that keys the Shared Risk-Signal Bus.

    risk_id = HMAC-SHA256(consortium_key, normalize(account))

Every member bank derives the same identifier for the same account, and the bus
never sees the account number itself.

Why HMAC rather than ``SHA256(account || salt)``
------------------------------------------------
HMAC is a standardized MAC with proper key/message separation, and is the right
default for any keyed digest. The bare secret-suffix form it replaced was *not*
practically exploitable here -- length extension needs the secret as a prefix,
and with the key as a suffix an attacker could only extend into input they do
not control -- but its security argument leans on collision resistance of the
message rather than on a MAC proof, and there is no reason to accept that.

The important point: this change bought **zero** resistance to enumeration.

What pseudonymity here does and does not buy you
------------------------------------------------
Account numbers come from a small, structured space -- on the order of 10^9 live
accounts. Any party holding the consortium key can enumerate that space offline
in well under a minute on one GPU and build a permanent ``risk_id -> account``
table. Every member bank holds that key.

So the identifier protects the account number from the **bus operator** and from
anyone who compromises the topic. It does **not** protect it from a
**participating bank**. No choice of hash fixes this, and a slow KDF only raises
the one-time cost to roughly the price of a cup of coffee while making the
derivation too slow to sit in the payment path.

Closing the gap needs a construction where no single party holds a key that maps
the whole space -- an OPRF (RFC 9497) issued by a consortium authority, so that
each guess costs one online, rate-limited, attributable round trip instead of a
free offline hash. That is tracked in ``docs/SECURITY.md`` as the M3 design
decision. Until then, state the limitation rather than claiming anonymity.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import re
import secrets
from hashlib import sha256
from typing import Final

SALT_ENV_VAR: Final = "ARIS_SALT"
DEV_MODE_ENV_VAR: Final = "ARIS_DEV_MODE"

# A MAC key should be full-entropy random material, not a passphrase. Given one
# known (account, risk_id) pair -- which every member bank has -- a human-chosen
# passphrase is recoverable with off-the-shelf tooling, so the key is required to
# arrive as encoded random bytes.
MIN_KEY_BYTES: Final = 32

# Used only when ARIS_DEV_MODE is explicitly enabled. Public by design, so it is
# unreachable unless a developer opts in.
_DEV_KEY: Final = b"\xa5" * MIN_KEY_BYTES

_ACCOUNT_ALLOWED: Final = re.compile(r"\A[A-Z0-9-]{4,34}\Z")
_HEX_KEY: Final = re.compile(r"\A[0-9a-fA-F]+\Z")


class SaltNotConfigured(RuntimeError):
    """Raised when no usable consortium key is configured."""


def generate_key() -> str:
    """Return a fresh hex-encoded consortium key suitable for ``ARIS_SALT``."""
    return secrets.token_hex(MIN_KEY_BYTES)


def _decode_key(raw: str) -> bytes:
    """Decode a hex or base64 key, rejecting anything that looks like a passphrase."""
    candidate = raw.strip()
    if _HEX_KEY.match(candidate) and len(candidate) % 2 == 0:
        return bytes.fromhex(candidate)
    try:
        return base64.b64decode(candidate, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SaltNotConfigured(
            f"{SALT_ENV_VAR} must be hex or base64 encoded random bytes. "
            f"Generate one with: python -c "
            f"'from aris.hashing import generate_key; print(generate_key())'"
        ) from exc


def load_salt() -> bytes:
    """Return the consortium key from the environment.

    Fails closed. A missing key raises rather than falling back to a default: a
    silent fallback would let a misconfigured node derive identifiers that match
    no other bank, so every lookup would miss and every transfer would look
    clean -- an outage that presents as an all-clear.

    The key is read from an environment variable, which is visible in
    ``/proc/<pid>/environ``, ``ps eww`` and container inspection output. That is
    acceptable for the prototype but is *not* the HSM-resident storage the
    project write-up describes; see ``docs/SECURITY.md``.
    """
    raw = os.environ.get(SALT_ENV_VAR)
    if raw:
        key = _decode_key(raw)
        if len(key) < MIN_KEY_BYTES:
            raise SaltNotConfigured(
                f"{SALT_ENV_VAR} must decode to at least {MIN_KEY_BYTES} bytes; got {len(key)}"
            )
        return key

    if os.environ.get(DEV_MODE_ENV_VAR) == "1":
        return _DEV_KEY

    raise SaltNotConfigured(
        f"{SALT_ENV_VAR} is not set. Export the consortium key, or set "
        f"{DEV_MODE_ENV_VAR}=1 to use the public development key."
    )


def normalize_account(account: str) -> str:
    """Canonicalise an account number so every bank derives the same identifier.

    Cross-bank matching only works if Bank A and Bank B agree byte-for-byte, so
    whitespace -- pure display formatting -- is folded away and the result is
    uppercased.

    Non-ASCII input is rejected *before* case folding. ``str.upper()`` applies
    Unicode case mappings, several of which are many-to-one or length-changing:
    DOTLESS I uppercases to "I", SHARP S expands to "SS", and the FF ligature
    expands to "FF". Folding first would therefore let crafted input collide
    with a real account's identifier, in the one function whose whole job is
    deterministic agreement across banks.

    Separators such as ``-`` stay significant: folding them too would merge
    identifiers banks may treat as distinct accounts, and a false merge flags an
    innocent receiver.
    """
    if not isinstance(account, str):
        raise TypeError("account must be a string")
    candidate = "".join(account.split())
    if not candidate.isascii():
        raise ValueError("account must be ASCII")
    candidate = candidate.upper()
    if not _ACCOUNT_ALLOWED.match(candidate):
        raise ValueError("account is not a well-formed account identifier")
    return candidate


def risk_id_for_account(account: str, key: bytes | None = None) -> str:
    """Return ``HMAC-SHA256(key, normalize(account))`` as lowercase hex."""
    if key is None:
        key = load_salt()
    elif len(key) < MIN_KEY_BYTES:
        raise ValueError(f"key must be at least {MIN_KEY_BYTES} bytes")
    material = normalize_account(account).encode("ascii")
    return hmac.new(key, material, sha256).hexdigest()
