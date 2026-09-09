"""HSM-resident consortium key via PKCS#11 (docs/SECURITY.md SS3.6).

``ARIS_SALT`` (``aris.hashing.load_salt``) loads the consortium key as raw
bytes into process memory -- readable from ``/proc/<pid>/environ``, ``ps
eww``, container inspection, and crash dumps. This module is the
alternative: the root key is generated *inside* a PKCS#11 token as
``CKA_SENSITIVE`` + ``CKA_EXTRACTABLE=false``, so it can be *used* (``C_Sign``)
but never *read* back out -- not by an attacker, and not by this process
either. Verified directly, not just configured: reading a key generated this
way raises ``pkcs11.AttributeSensitive`` (``tests/test_hsm.py``), the
PKCS#11-level enforcement of exactly the property this section asks for.

Epoch subkeys (docs/SECURITY.md SS3.2) are still derived by HMACing the
epoch string under the root key, but that HMAC now runs as one ``C_Sign``
call inside the token -- the *root* key never leaves it. The resulting
32-byte epoch subkey comes back to this process and is used directly for the
same ``aris.hashing``-shaped derivation, which is exactly the trade-off
SS3.2 already accepts and documents: a leaked epoch subkey exposes one day,
never the root.

Software stand-in, stated plainly. No HSM hardware is available to build or
verify this against, so it is developed and tested against SoftHSM2 -- a
real, widely-used PKCS#11 software token, not a mock of the PKCS#11 API. The
``C_Sign`` / ``CKA_SENSITIVE`` / ``CKA_EXTRACTABLE`` behavior this module
depends on is standard PKCS#11, identical in shape against a real HSM. What
SoftHSM2 cannot demonstrate is hardware-level key protection: a compromised
host OS can still read SoftHSM2's on-disk token store directly, just not
through the PKCS#11 API this module uses. That is the gap a real HSM closes
that no software stand-in can, and it is why this remains the one item in
docs/SECURITY.md's priority list not claimed as more than partially closed.

Not wired into the rest of the bus, same as ``aris.oprf``:
``aris.hashing.risk_id_for_account`` is still what ``BankBot`` and
``KafkaRiskBus`` use.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterator
from contextlib import contextmanager
from hashlib import sha256
from typing import Final, Protocol, cast

import pkcs11
from pkcs11 import Attribute, KeyType, Mechanism, SecretKey, Session

from aris.hashing import (
    _length_prefixed_material,
    current_epoch,
    normalize_account,
    normalize_ifsc,
)


class _Signer(Protocol):
    """What SecretKey actually supports at runtime once generated with SIGN
    capability (python-pkcs11's SignMixin), which its static types don't
    reflect -- see generate_consortium_key."""

    def sign(self, data: bytes, *, mechanism: Mechanism) -> bytes: ...


MODULE_ENV_VAR: Final = "ARIS_HSM_MODULE"
TOKEN_LABEL_ENV_VAR: Final = "ARIS_HSM_TOKEN_LABEL"
PIN_ENV_VAR: Final = "ARIS_HSM_PIN"

DEFAULT_KEY_LABEL: Final = "aris-consortium-key"
_HMAC_KEY_BITS: Final = 256


class HsmNotConfigured(RuntimeError):
    """No usable PKCS#11 module, token, or consortium key is configured.

    Fails closed, matching ``aris.hashing.SaltNotConfigured``: a missing HSM
    key must never silently fall back to some other key material.
    """


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise HsmNotConfigured(
            f"{name} is not set. A PKCS#11-backed consortium key needs "
            f"{MODULE_ENV_VAR}, {TOKEN_LABEL_ENV_VAR}, and {PIN_ENV_VAR} all set."
        )
    return value


@contextmanager
def open_hsm_session(
    module_path: str | None = None,
    token_label: str | None = None,
    user_pin: str | None = None,
) -> Iterator[Session]:
    """Open a read/write PKCS#11 session against the configured token.

    Falls back to ``ARIS_HSM_MODULE`` / ``ARIS_HSM_TOKEN_LABEL`` /
    ``ARIS_HSM_PIN`` for any argument left as ``None``. Raises
    ``HsmNotConfigured`` rather than letting the underlying ``pkcs11``
    exception surface directly, so a missing/misconfigured HSM fails the
    same recognisable way ``aris.hashing.load_salt`` does for a missing
    ``ARIS_SALT``.
    """
    module_path = module_path or _require_env(MODULE_ENV_VAR)
    token_label = token_label or _require_env(TOKEN_LABEL_ENV_VAR)
    user_pin = user_pin if user_pin is not None else _require_env(PIN_ENV_VAR)
    try:
        lib = pkcs11.lib(module_path)
        token = lib.get_token(token_label=token_label)
    except pkcs11.PKCS11Error as exc:
        raise HsmNotConfigured(f"cannot reach PKCS#11 token {token_label!r}: {exc}") from exc
    try:
        with token.open(user_pin=user_pin, rw=True) as session:
            yield session
    except pkcs11.PKCS11Error as exc:
        raise HsmNotConfigured(f"PKCS#11 session for token {token_label!r} failed: {exc}") from exc


def generate_consortium_key(session: Session, label: str = DEFAULT_KEY_LABEL) -> SecretKey:
    """Generate a fresh consortium key *inside* the token: non-extractable,
    sensitive, usable only via ``C_Sign``. A provisioning action, deliberately
    separate from ordinary runtime use -- see ``get_consortium_key``, which
    looks one up but never creates one, the same fail-closed split
    ``aris.hashing.generate_key`` (provisioning) and ``load_salt`` (runtime)
    already have.
    """
    # python-pkcs11 composes the runtime return type with a SignMixin because
    # the template above requests SIGN capability -- its static types don't
    # reflect that dynamic composition, so .sign() below type-checks as
    # missing even though it is always present at runtime for a key made
    # this way. See _derive_epoch_key_hsm.
    return session.generate_key(
        KeyType.GENERIC_SECRET,
        _HMAC_KEY_BITS,
        label=label,
        store=True,
        template={
            Attribute.SIGN: True,
            Attribute.SENSITIVE: True,
            Attribute.EXTRACTABLE: False,
        },
    )


def get_consortium_key(session: Session, label: str = DEFAULT_KEY_LABEL) -> SecretKey:
    """Look up the already-provisioned consortium key by label.

    Raises ``HsmNotConfigured`` if it does not exist -- this never
    auto-generates one, the same way a missing ``ARIS_SALT`` raises rather
    than silently deriving a fresh key no other bank would agree on.
    """
    try:
        key = session.get_key(label=label, key_type=KeyType.GENERIC_SECRET)
        return cast(SecretKey, key)
    except pkcs11.NoSuchKey as exc:
        raise HsmNotConfigured(
            f"no consortium key labelled {label!r} in this token -- "
            "provision one with generate_consortium_key first"
        ) from exc


def _derive_epoch_key_hsm(key: SecretKey, epoch: int) -> bytes:
    """HMAC the epoch string under the root key via one C_Sign call -- the
    root key's raw bytes never leave the token to compute this."""
    signer = cast("_Signer", key)  # see generate_consortium_key
    return signer.sign(f"aris-epoch:{epoch}".encode("ascii"), mechanism=Mechanism.SHA256_HMAC)


def risk_id_for_account_hsm(
    ifsc: str, account: str, key: SecretKey, *, epoch: int | None = None
) -> str:
    """HSM-backed equivalent of ``aris.hashing.risk_id_for_account``: same
    output format (64 lowercase hex chars), same ``(ifsc, account, epoch)``
    derivation shape, but the root key is a non-extractable PKCS#11 key
    object, never raw bytes in this process.
    """
    if epoch is None:
        epoch = current_epoch()
    epoch_key = _derive_epoch_key_hsm(key, epoch)
    material = _length_prefixed_material(normalize_ifsc(ifsc), normalize_account(account))
    return hmac.new(epoch_key, material, sha256).hexdigest()
