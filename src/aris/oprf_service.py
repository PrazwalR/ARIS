"""The consortium OPRF authority as a network service (docs/SECURITY.md SS3.1).

``aris.oprf.OprfAuthority`` is an in-process object; a member bank runs in its
own process and must reach the authority over the network. This module is both
halves of that:

- ``create_oprf_app``: the authority's HTTP surface. ``POST /evaluate`` takes a
  *blinded* value only -- the authority still never sees an ``(ifsc,
  account)`` pair -- authenticates the calling bank, and maps the authority's
  per-bank rate limit to HTTP 429. Every evaluation is attributed to the
  authenticated bank, not to whatever bank id a caller claims.
- ``HttpOprfAuthorityClient``: satisfies ``aris.oprf.OprfAuthorityClient``, so
  it drops into ``aris.risk_id.OprfRiskIdDeriver`` unchanged. It pins the
  authority's public-key fingerprint (see ``aris.oprf.public_key_fingerprint``).

Bank authentication here is a per-bank shared secret over HTTP. A real
deployment puts this behind the same mTLS + per-principal identity SS3.8 built
for Kafka; the attribution/rate-limit logic would not change.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Annotated, Final

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from aris.oprf import (
    OprfAuthority,
    RateLimitExceeded,
    check_pinned_key,
    public_key_fingerprint,
)

KEY_FILE_ENV_VAR: Final = "ARIS_OPRF_KEY_FILE"
BANKS_ENV_VAR: Final = "ARIS_OPRF_BANKS"  # "BANK-A:secret1,BANK-B:secret2"
RATE_LIMIT_ENV_VAR: Final = "ARIS_OPRF_RATE_LIMIT"
RATE_WINDOW_ENV_VAR: Final = "ARIS_OPRF_RATE_WINDOW_S"

# Hex of a value below a 2048-4096-bit modulus; bounds request size up front.
_MAX_HEX_LEN: Final = 1024


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    blinded: str = Field(min_length=1, max_length=_MAX_HEX_LEN, pattern=r"^[0-9a-f]+$")


class EvaluateResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluated: str


class PublicKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    n: str
    e: int
    fingerprint: str


def create_oprf_app(authority: OprfAuthority, bank_keys: dict[str, str]) -> FastAPI:
    if not bank_keys:
        # No configured banks would make every request a 401; failing at
        # startup says why instead.
        raise ValueError("an OPRF service with no authorized banks cannot serve anyone")

    app = FastAPI(title="ARIS OPRF authority")
    app.state.authority = authority
    app.state.bank_keys = dict(bank_keys)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/public-key", response_model=PublicKeyResponse)
    def public_key(request: Request) -> PublicKeyResponse:
        n, e = request.app.state.authority.public_numbers
        return PublicKeyResponse(n=format(n, "x"), e=e, fingerprint=public_key_fingerprint(n, e))

    @app.post("/evaluate", response_model=EvaluateResponse)
    def evaluate(
        body: EvaluateRequest,
        request: Request,
        x_bank_id: Annotated[str | None, Header()] = None,
        x_bank_key: Annotated[str | None, Header()] = None,
    ) -> EvaluateResponse:
        keys: dict[str, str] = request.app.state.bank_keys
        if x_bank_id is None or x_bank_key is None:
            raise HTTPException(status_code=401, detail="missing bank credentials")
        expected = keys.get(x_bank_id)
        if expected is None or not hmac.compare_digest(x_bank_key, expected):
            raise HTTPException(status_code=401, detail="unknown bank or invalid key")
        auth: OprfAuthority = request.app.state.authority
        try:
            # Attributed to the authenticated bank id, so one bank can never
            # spend another's rate limit.
            result = auth.evaluate(x_bank_id, int(body.blinded, 16))
        except RateLimitExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return EvaluateResponse(evaluated=format(result, "x"))

    return app


class OprfServiceError(Exception):
    """The authority answered with something other than a usable result."""


class HttpOprfAuthorityClient:
    """``aris.oprf.OprfAuthorityClient`` over HTTP, speaking as one bank.

    ``expected_fingerprint`` is required, not optional: it is the only thing
    that ties the answers to the real consortium authority rather than to
    whatever server answered (see ``aris.oprf.public_key_fingerprint``).
    """

    def __init__(
        self,
        base_url: str,
        bank_id: str,
        bank_key: str,
        expected_fingerprint: str,
        *,
        http: httpx.Client | None = None,
        timeout_s: float = 5.0,
    ) -> None:
        self._bank_id = bank_id
        self._bank_key = bank_key
        self._expected_fingerprint = expected_fingerprint
        self._http = http if http is not None else httpx.Client(timeout=timeout_s)
        self._base_url = base_url.rstrip("/")
        self._public: tuple[int, int] | None = None

    @property
    def public_numbers(self) -> tuple[int, int]:
        if self._public is None:
            resp = self._http.get(f"{self._base_url}/public-key")
            if resp.status_code != 200:
                raise OprfServiceError(f"GET /public-key returned {resp.status_code}")
            body = resp.json()
            n, e = int(body["n"], 16), int(body["e"])
            check_pinned_key(n, e, self._expected_fingerprint)
            self._public = (n, e)
        return self._public

    def evaluate(self, bank_id: str, blinded: int) -> int:
        if bank_id != self._bank_id:
            raise ValueError(
                f"this client holds credentials for {self._bank_id!r}, not {bank_id!r}"
            )
        resp = self._http.post(
            f"{self._base_url}/evaluate",
            json={"blinded": format(blinded, "x")},
            headers={"X-Bank-Id": self._bank_id, "X-Bank-Key": self._bank_key},
        )
        if resp.status_code == 429:
            raise RateLimitExceeded(resp.json().get("detail", "rate limited"))
        if resp.status_code != 200:
            raise OprfServiceError(f"POST /evaluate returned {resp.status_code}")
        return int(resp.json()["evaluated"], 16)

    def close(self) -> None:
        self._http.close()


def load_or_create_private_key(path: Path, *, create: bool) -> rsa.RSAPrivateKey:
    """Load the authority's PEM key from ``path``. Only creates one when
    explicitly asked -- silently minting a new key over a missing file would
    change every risk_id the consortium has ever published under."""
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError(f"{path} does not hold an RSA private key")
        return key
    if not create:
        raise FileNotFoundError(
            f"{path} does not exist. Pass --generate-key to create a new authority key "
            "(this defines a new risk_id space -- existing signals will not match)."
        )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    return key


def main(argv: list[str] | None = None) -> None:
    import argparse

    import uvicorn

    from aris.api.config import parse_credentials

    parser = argparse.ArgumentParser(description="Run the ARIS consortium OPRF authority")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument(
        "--generate-key",
        action="store_true",
        help=f"create the key at ${KEY_FILE_ENV_VAR} if it does not exist yet",
    )
    args = parser.parse_args(argv)

    key_file = os.environ.get(KEY_FILE_ENV_VAR)
    raw_banks = os.environ.get(BANKS_ENV_VAR)
    if not key_file or not raw_banks:
        raise SystemExit(f"set {KEY_FILE_ENV_VAR} and {BANKS_ENV_VAR} ('BANK-A:secret,...')")

    authority = OprfAuthority(
        private_key=load_or_create_private_key(Path(key_file), create=args.generate_key),
        rate_limit=int(os.environ.get(RATE_LIMIT_ENV_VAR, "100")),
        rate_window_s=float(os.environ.get(RATE_WINDOW_ENV_VAR, "60")),
    )
    n, e = authority.public_numbers
    # Banks pin this; print it so an operator can distribute it out of band.
    print(f"[oprf] public key fingerprint: {public_key_fingerprint(n, e)}", flush=True)
    app = create_oprf_app(authority, parse_credentials(raw_banks, BANKS_ENV_VAR))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
