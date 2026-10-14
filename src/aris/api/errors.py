"""Error handling shared by every ARIS HTTP service (BankBot API, OPRF
authority, showcase server).

FastAPI's default 422 body echoes the offending input. That is a 500 waiting
to happen -- a JSON NaN or Infinity cannot be serialised back out, so the
error handler itself crashes -- and a privacy leak besides: a rejected
transfer would reflect the submitted account number in the response.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


def _safe_error(err: dict[str, Any]) -> dict[str, Any]:
    return {
        "loc": [str(part) for part in err.get("loc", ())],
        "msg": str(err.get("msg", "")),
        "type": str(err.get("type", "")),
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422, content={"detail": [_safe_error(e) for e in exc.errors()]}
        )

    @app.exception_handler(RecursionError)
    async def _too_deep(_request: Request, _exc: RecursionError) -> JSONResponse:
        # Pathologically nested JSON exhausts the parser's recursion limit.
        return JSONResponse(status_code=400, content={"detail": "request body nested too deeply"})
