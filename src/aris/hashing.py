"""Derivation of the pseudonymous ``risk_id`` that keys the Shared Risk-Signal Bus.

    risk_id = HMAC-SHA256(consortium_key, length_prefixed(normalize(ifsc), normalize(account)))

Every member bank derives the same identifier for the same ``(ifsc, account)`` pair,
and the bus never sees the account number itself.

Why ``(ifsc, account)`` and not ``account`` alone
--------------------------------------------------
An account number is unique only within its own bank. Two customers at different
banks can share one, and hashing the account alone would derive the same
``risk_id`` for both -- a flag against one blocks the other. Keying on the pair
fixes it, and the pair is combined with an explicit length prefix rather than bare
concatenation: without it, ``("HDFC0001234", "5678")`` and ``("HDFC000123",
"45678")`` hash to the same bytes.

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

# Indian Financial System Code: 4-letter bank code, a literal '0' reserved for
# future use, 6-character alphanumeric branch code. Fixed-length by spec, but the
# encoding below length-prefixes it anyway rather than leaning on that as an
# implicit assumption -- see risk_id_for_account.
_IFSC_PATTERN: Final = re.compile(r"\A[A-Z]{4}0[A-Z0-9]{6}\Z")


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


def normalize_ifsc(ifsc: str) -> str:
    """Canonicalise an IFSC the same way ``normalize_account`` does an account.

    See ``normalize_account`` for why whitespace is folded and non-ASCII input
    is rejected before case folding.
    """
    if not isinstance(ifsc, str):
        raise TypeError("ifsc must be a string")
    candidate = "".join(ifsc.split())
    if not candidate.isascii():
        raise ValueError("ifsc must be ASCII")
    candidate = candidate.upper()
    if not _IFSC_PATTERN.match(candidate):
        raise ValueError("ifsc is not a well-formed IFSC code")
    return candidate


def _length_prefixed_material(ifsc: str, account: str) -> bytes:
    """Encode ``(ifsc, account)`` so the pair cannot collide with a different
    pair under bare concatenation, e.g. ``("HDFC0001234", "5678")`` colliding
    with ``("HDFC000123", "45678")``. The ifsc's own length -- not a fixed
    field width -- is the delimiter, so this stays correct even if the IFSC
    format's fixed length ever changes; it does not rely on that as an
    unstated assumption.
    """
    return f"{len(ifsc)}:{ifsc}:{account}".encode("ascii")


def risk_id_for_account(ifsc: str, account: str, key: bytes | None = None) -> str:
    """Return ``HMAC-SHA256(key, length_prefixed(normalize(ifsc), normalize(account)))``
    as lowercase hex.

    Keyed on the pair, not the account alone: an account number is unique only
    within its own bank, so two customers at different banks can otherwise share
    a `risk_id` and a flag against one blocks the other. See docs/SECURITY.md SS3.3.
    """
    if key is None:
        key = load_salt()
    elif len(key) < MIN_KEY_BYTES:
        raise ValueError(f"key must be at least {MIN_KEY_BYTES} bytes")
    material = _length_prefixed_material(normalize_ifsc(ifsc), normalize_account(account))
    return hmac.new(key, material, sha256).hexdigest()
