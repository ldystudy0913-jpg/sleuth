"""HTTP API for DD-Check (Starlette). Does not depend on sleuth."""
from __future__ import annotations

import json
from typing import Any, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Settings, get_settings
from .models import BatchCheckRequest, CheckRequest
from .orchestrator import Orchestrator
from .store import ResultStore, build_store


def _json(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status)


def create_app(settings: Optional[Settings] = None) -> Starlette:
    settings = settings or get_settings()
    orch = Orchestrator(settings)
    store: ResultStore = build_store(settings)

    async def health(_: Request):
        return _json({"ok": True, "service": "dd-check"})

    async def check(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid json"}, 400)
        try:
            req = CheckRequest.model_validate(body)
        except Exception as exc:
            return _json({"error": f"invalid request: {exc}"}, 400)
        try:
            result = orch.check_one(req)
        except Exception as exc:
            return _json({"error": f"check failed: {exc}"}, 500)
        rid = store.save(result)
        payload = result.model_dump()
        payload["resultId"] = rid
        return _json(payload)

    async def batch(request: Request):
        try:
            body = await request.json()
        except Exception:
            return _json({"error": "invalid json"}, 400)
        try:
            req = BatchCheckRequest.model_validate(body)
        except Exception as exc:
            return _json({"error": f"invalid request: {exc}"}, 400)
        try:
            out = orch.check_batch(req)
        except Exception as exc:
            return _json({"error": f"batch failed: {exc}"}, 500)
        return _json(out)

    async def get_result(request: Request):
        rid = request.path_params["result_id"]
        row = store.get(rid)
        if row is None:
            return _json({"error": "not found"}, 404)
        return _json(row)

    async def list_results(request: Request):
        limit = int(request.query_params.get("limit", 50))
        return _json(store.list_recent(limit=limit))

    routes = [
        Route("/health", health),
        Route("/v1/check", check, methods=["POST"]),
        Route("/v1/batch", batch, methods=["POST"]),
        Route("/v1/results", list_results, methods=["GET"]),
        Route("/v1/results/{result_id}", get_result, methods=["GET"]),
    ]
    return Starlette(routes=routes)


def main(argv=None) -> int:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(prog="dd-check")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)
    settings = get_settings()
    host = args.host or settings.host
    port = args.port or settings.port
    app = create_app(settings)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
