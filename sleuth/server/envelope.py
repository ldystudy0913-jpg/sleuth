"""Starlette JSON helpers for the BizError envelope."""
from __future__ import annotations

from typing import Any

from ..bizerror import APPError, BizErrorCode, ok_payload


def json_ok(data: Any = None, status: int = 200):
    from starlette.responses import JSONResponse

    return JSONResponse(ok_payload(data), status_code=status)


def json_app(exc: APPError):
    from starlette.responses import JSONResponse

    return JSONResponse(exc.envelope(), status_code=exc.status)


async def app_error_handler(_request, exc: APPError):
    return json_app(exc)


def raise_code(item: BizErrorCode, *args, status: int = 400, data: Any = None) -> None:
    raise APPError.of(item, *args, status=status, data=data)
