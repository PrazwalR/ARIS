"""OPRF-derived risk_id (docs/SECURITY.md SS3.1): a consortium authority holds
a secret key, banks send *blinded* queries, and no party can map the account
space alone -- every derivation costs one online, rate-limited, attributable
round trip to the authority instead of a free offline hash. This is the fix
SS3.1 calls "the honest version of the project's novelty claim": not
`risk_id = HMAC(key, account)`, which any key-holder can compute for every
account there is, but a scheme whose privacy failure mode is bounded and
logged rather than silent and total.

Construction, and why it is not literally RFC 9497
----------------------------------------------------
RFC 9497 defines OPRF as a Diffie-Hellman protocol over a prime-order group
(ristretto255 or NIST P-256/P-384/P-521). Implementing that correctly from
scratch needs either a constant-time hash-to-curve (RFC 9380's SSWU map,
nontrivial field arithmetic with no official test vectors available here to
verify against bit-for-bit) or a cofactor-8 group like raw edwards25519 (this
process's available `pynacl` build has no ristretto255 bindings), which is
vulnerable to small-subgroup attacks unless every scalar multiplication is
carefully clamped -- exactly the kind of subtle, hard-to-self-verify
cryptographic engineering this project's standards ask to be honest about
rather than quietly get wrong.

What is implemented instead is an **RSA-FDH blind signature** (Chaum 1982,
full-domain hash per RFC 8017 SSB.2.1's MGF1): the authority's RSA keypair
generation comes entirely from `cryptography` (audited prime generation, no
hand-rolled primality testing); blinding, signing, and unblinding are plain
modular exponentiation via Python's built-in `pow(base, exp, mod)`, which is
the one piece of "raw" arithmetic this construction needs and is not the part
that is hard to get right (Weierstrass point addition and hash-to-curve are).

This closes the *same* gap SS3.1 describes -- a bank cannot compute risk_id
without the authority's per-query cooperation, and that cooperation is
blinded, rate-limited, and logged -- through the same shape of protocol
(blind / evaluate / unblind) RFC 9497 uses, and its output is directly
self-verifiable against the authority's public key (`verify`), which a
base-mode (non-verifiable) DH-OPRF would not give without an added DLEQ
proof. It is not a drop-in RFC 9497 implementation and does not claim to be
one.

Not wired into the rest of the bus. `aris.hashing.risk_id_for_account` (the
HMAC-based derivation) is still what `BankBot`, `KafkaRiskBus`, and the demo
use. Swapping it for this would touch `TransferRequest`, every publisher call
site, and most of the existing test suite -- a decision left for whoever
reviews this module, not made here.
"""

from __future__ import annotations

import hashlib
import logging
import math
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from cryptography.hazmat.primitives.asymmetric import rsa

from aris.hashing import _length_prefixed_material, normalize_account, normalize_ifsc

logger = logging.getLogger(__name__)

DEFAULT_KEY_SIZE_BITS: Final = 2048
DEFAULT_AUDIT_LOG_CAPACITY: Final = 100_000
DEFAULT_RATE_LIMIT: Final = 100
DEFAULT_RATE_WINDOW_S: Final = 60.0


class OprfVerificationError(Exception):
    """The authority's response did not verify against its own public key.

    Either the authority evaluated with a different key than it advertises
    (a misbehaving or misconfigured authority -- unlike a base-mode DH-OPRF,
    this construction lets the client catch that directly, with no separate
    proof protocol needed) or the response was corrupted in transit.
    """


class RateLimitExceeded(Exception):
    """A caller exceeded its query rate limit.

    This is the mechanism that turns unbounded offline enumeration into
    bounded, prospective, attributable online enumeration -- the whole point
    of routing risk_id derivation through an authority at all. A caller
    hitting this is not necessarily malicious (a real deployment tunes the
    limit against genuine peak transfer volume), but it is always logged.
    """


def _mgf1(seed: bytes, length: int, hash_ctor: Callable[[bytes], hashlib._Hash]) -> bytes:
    """MGF1 (RFC 8017 SSB.2.1): expand `seed` to `length` bytes via repeated
    hashing with a 4-byte big-endian counter. Used to stretch a 256-bit
    SHA-256 digest to cover the full range [0, n) of a much larger RSA
    modulus -- a short, unpadded hash reduced mod n would leave the top bits
    of a 2048-bit modulus's range structurally unreachable.
    """
    digest_size = hash_ctor(b"").digest_size
    if length > (2**32) * digest_size:
        raise ValueError("mask too long for a 4-byte MGF1 counter")
    chunks = []
    counter = 0
    produced = 0
    while produced < length:
        chunks.append(hash_ctor(seed + counter.to_bytes(4, "big")).digest())
        produced += digest_size
        counter += 1
    return b"".join(chunks)[:length]


def _n_bytes(n: int) -> int:
    return (n.bit_length() + 7) // 8


def full_domain_hash(message: bytes, n: int) -> int:
    """RSA-FDH: expand SHA-256(message) to n's full byte length via MGF1,
    then reduce mod n. Full-domain hashing (not a bare short digest reduced
    mod n) is what makes this blind signature scheme provably secure in the
    random oracle model against the chosen-message forgery RSA's
    multiplicative homomorphism would otherwise allow.
    """
    digest = hashlib.sha256(message).digest()
    expanded = _mgf1(digest, _n_bytes(n), hashlib.sha256)
    return int.from_bytes(expanded, "big") % n


def blind(message_int: int, n: int, e: int) -> tuple[int, int]:
    """Return `(r, blinded)`: a fresh random blinding factor and
    `message_int * r^e mod n`. `r` is never sent anywhere and is required to
    unblind the authority's response -- keep it for exactly one round trip.
    """
    while True:
        r = secrets.randbelow(n - 3) + 2  # [2, n-2]: avoid the degenerate 0/1
        if math.gcd(r, n) == 1:
            break
    blinded = (message_int * pow(r, e, n)) % n
    return r, blinded


def unblind(blinded_result: int, r: int, n: int) -> int:
    """Invert `blind`'s multiplication: `blinded_result * r^-1 mod n`."""
    return (blinded_result * pow(r, -1, n)) % n


def verify(signature: int, message_int: int, n: int, e: int) -> bool:
    """Whether `signature` is a valid RSA signature over `message_int` under
    the public key `(n, e)` -- i.e. whether the authority actually evaluated
    with the key it claims to hold."""
    return pow(signature, e, n) == message_int


@dataclass(frozen=True)
class OprfQueryRecord:
    """One logged `evaluate()` call -- attributable, per SS3.1's requirement,
    without ever recording what was actually queried (the blinded value is
    meaningless without the caller's own, never-transmitted blinding factor).
    """

    bank_id: str
    at: float


class OprfAuthorityClient(Protocol):
    """What `risk_id_for_account_oprf` needs from an authority: its public
    key, and a way to submit a blinded query. An in-process `OprfAuthority`
    satisfies this directly; a real deployment's network client (HTTP/gRPC,
    authenticated some way SS3.8's mTLS/ACL work would need to cover) would
    implement the same shape.
    """

    @property
    def public_numbers(self) -> tuple[int, int]: ...

    def evaluate(self, bank_id: str, blinded: int) -> int: ...


class OprfAuthority:
    """The consortium authority: holds the RSA private key, evaluates blinded
    queries, and is the sole point of rate limiting and attribution.

    Never sees an unblinded query -- `evaluate()`'s `blinded` argument is
    `message * r^e mod n` for a random `r` only the caller knows, so this
    class genuinely cannot learn which `(ifsc, account)` pair was queried,
    only that *some* query happened, when, and by whom.
    """

    def __init__(
        self,
        key_size: int = DEFAULT_KEY_SIZE_BITS,
        rate_limit: int = DEFAULT_RATE_LIMIT,
        rate_window_s: float = DEFAULT_RATE_WINDOW_S,
        now: Callable[[], float] = time.monotonic,
        audit_log_capacity: int = DEFAULT_AUDIT_LOG_CAPACITY,
    ) -> None:
        if rate_limit < 1:
            raise ValueError("rate_limit must be positive")
        if audit_log_capacity < 1:
            raise ValueError("audit_log_capacity must be positive")
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        numbers = private_key.private_numbers()
        self._n = numbers.public_numbers.n
        self._e = numbers.public_numbers.e
        self._d = numbers.d
        self._rate_limit = rate_limit
        self._rate_window_s = rate_window_s
        self._now = now
        self._lock = threading.Lock()
        # bank_id -> monotonically increasing timestamps of recent queries,
        # oldest first, so expiring stale entries off the front is O(1) each.
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        # Bounded like aris.bankbot.InMemoryAuditLog: a long-lived authority
        # doing continuous evaluate() calls must not leak memory unboundedly.
        # Unlike that sink, a dropped record here is not evidence of a
        # decision -- it is one attribution record among many for a party
        # already subject to its own rate limit -- so this drops the oldest
        # silently rather than counting/logging every eviction.
        self._audit_log: deque[OprfQueryRecord] = deque(maxlen=audit_log_capacity)

    @property
    def public_numbers(self) -> tuple[int, int]:
        return self._n, self._e

    @property
    def audit_log(self) -> tuple[OprfQueryRecord, ...]:
        return tuple(self._audit_log)

    def evaluate(self, bank_id: str, blinded: int) -> int:
        if not (0 <= blinded < self._n):
            raise ValueError("blinded value out of range for this authority's modulus")
        now = self._now()
        with self._lock:
            window = self._windows[bank_id]
            cutoff = now - self._rate_window_s
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self._rate_limit:
                logger.warning(
                    "rate limit exceeded: bank=%s limit=%d window=%.0fs",
                    bank_id,
                    self._rate_limit,
                    self._rate_window_s,
                )
                raise RateLimitExceeded(
                    f"{bank_id} exceeded {self._rate_limit} queries / {self._rate_window_s:.0f}s"
                )
            window.append(now)
            self._audit_log.append(OprfQueryRecord(bank_id=bank_id, at=now))
        return pow(blinded, self._d, self._n)


def risk_id_for_account_oprf(
    ifsc: str, account: str, authority: OprfAuthorityClient, bank_id: str
) -> str:
    """OPRF-derived risk_id for `(ifsc, account)`, via `authority`.

    Deterministic across callers: every bank querying the same
    `(ifsc, account)` against the same authority derives the same risk_id,
    the same guarantee `aris.hashing.risk_id_for_account` gives -- but a bank
    cannot compute it without `authority.evaluate()`'s cooperation, which is
    blinded (the authority never learns `ifsc`/`account`), rate-limited, and
    logged per `bank_id`.

    Raises `OprfVerificationError` if the authority's response does not
    verify against its own advertised public key -- catching a misbehaving
    or misconfigured authority rather than silently returning a wrong id.
    """
    n, e = authority.public_numbers
    material = _length_prefixed_material(normalize_ifsc(ifsc), normalize_account(account))
    message_int = full_domain_hash(material, n)
    r, blinded = blind(message_int, n, e)
    blinded_result = authority.evaluate(bank_id, blinded)
    signature = unblind(blinded_result, r, n)
    if not verify(signature, message_int, n, e):
        raise OprfVerificationError(
            f"authority evaluation for {bank_id!r} did not verify against its own public key"
        )
    return hashlib.sha256(signature.to_bytes(_n_bytes(n), "big")).hexdigest()
