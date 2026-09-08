"""Starlette JSON helpers for the BizError envelope."""
from __future__ import annotations

from typing import Any

from ..bizerror import APPError, BizErrorCode, ok_payload


def json_ok(data: Any = None, status: int = 200):
    from starlette.responses import JSONResponse

    from ..logtrace import envelope_uses_content, return_code_headers

    if envelope_uses_content():
        body = {
            "code": BizErrorCode.SUC0000.code,
            "msg": BizErrorCode.SUC0000.error_message,
            "content": data,
        }
        return JSONResponse(
            body,
            status_code=status,
            headers=return_code_headers("SUC0000"),
        )
    return JSONResponse(ok_payload(data), status_code=status)


def json_app(exc: APPError):
    from starlette.responses import JSONResponse

    from ..logtrace import envelope_uses_content, return_code_headers

    body = exc.envelope()
    if envelope_uses_content():
        return JSONResponse(
            body,
            status_code=200,
            headers=return_code_headers(exc.code),
        )
    return JSONResponse(body, status_code=exc.status)


async def app_error_handler(_request, exc: APPError):
    return json_app(exc)


def raise_code(item: BizErrorCode, *args, status: int = 400, data: Any = None) -> None:
    raise APPError.of(item, *args, status=status, data=data)
