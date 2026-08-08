"""Thin Starlette HTTP API over the shared Session core."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ..app import build_session, reload_skills
from ..config import load
from ..session import NullRenderer
from ..storage.factory import create_store
from ..util.env import load_dotenv


def _json_response(data: Any, status: int = 200):
    from starlette.responses import JSONResponse

    return JSONResponse(data, status_code=status)


def _user_id(request) -> str:
    return (
        request.headers.get("x-user-id")
        or request.query_params.get("user_id")
        or "anonymous"
    )


def _check_admin(request, config) -> Optional[Any]:
    token = config.server.admin_token
    if not token:
        return None
    got = request.headers.get("x-admin-token") or ""
    if got != token:
        return _json_response({"error": "unauthorized"}, 401)
    return None


def _session_model_payload(sess) -> Dict[str, str]:
    return {
        "ref": sess.model_ref(),
        "id": sess.model_id,
        "providerID": getattr(sess.provider, "id", "") or "",
    }


def create_app(workdir: Optional[Path] = None):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.routing import Route

    import os

    workdir = workdir or Path.cwd()
    load_dotenv(workdir)
    config = load(workdir)
    if not os.environ.get("SLEUTH_STORAGE_BACKEND") and config.server.default_backend:
        config.storage.backend = config.server.default_backend
    store = create_store(config)

    async def health(_: Request):
        return _json_response({"ok": True})

    async def create_session(request: Request):
        body: Dict[str, Any] = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        user_id = body.get("user_id") or _user_id(request)
        agent = body.get("agent") or config.default_agent
        sess = build_session(
            config=load(workdir),
            workdir=workdir,
            agent_name=agent,
            user_id=user_id,
            yolo=bool(body.get("yolo", True)),
            renderer=NullRenderer(),
            store=store,
        )
        if body.get("model"):
            try:
                sess.set_model(str(body["model"]))
            except Exception as exc:
                return _json_response({"error": f"invalid model: {exc}"}, 400)
        sess._ensure_persisted()
        return _json_response(
            {
                "id": sess.id,
                "user_id": sess.user_id,
                "title": sess.title,
                "agent": sess.agent_name,
                "model": _session_model_payload(sess),
            }
        )

    async def list_sessions(request: Request):
        from ..session_browse import build_session_list_rows

        user_id = _user_id(request)
        limit = int(request.query_params.get("limit", 50))
        rows = build_session_list_rows(store, user_id=user_id, limit=limit)
        return _json_response(
            [
                {
                    "id": r["id"],
                    "user_id": r["user_id"],
                    "title": r["title"],
                    "agent": r["agent"],
                    "model": r["model"],
                    "cost": r["cost"],
                    "tokens_input": r["tokens_input"],
                    "tokens_output": r["tokens_output"],
                    "time_updated": r["time_updated"],
                    "time_updated_local": r["time_updated_local"],
                    "preview": r["preview"],
                }
                for r in rows
            ]
        )

    async def get_session(request: Request):
        sid = request.path_params["session_id"]
        user_id = _user_id(request)
        rec = store.get_session(sid)
        if rec is None or (rec.user_id and rec.user_id != user_id):
            return _json_response({"error": "not found"}, 404)
        messages = store.load_messages(sid)
        return _json_response(
            {
                "id": rec.id,
                "user_id": rec.user_id,
                "title": rec.title,
                "agent": rec.agent,
                "model": rec.model,
                "cost": rec.cost,
                "tokens": {
                    "input": rec.tokens_input,
                    "output": rec.tokens_output,
                    "reasoning": rec.tokens_reasoning,
                    "cache_read": rec.tokens_cache_read,
                    "cache_write": rec.tokens_cache_write,
                },
                "messages": [
                    {
                        "id": m.metadata.get("id"),
                        "role": m.role,
                        "text": m.text,
                        "usage": m.metadata.get("usage"),
                        "cost": m.metadata.get("cost"),
                    }
                    for m in messages
                ],
            }
        )

    async def post_message(request: Request):
        sid = request.path_params["session_id"]
        user_id = _user_id(request)
        rec = store.get_session(sid)
        if rec is None or (rec.user_id and rec.user_id != user_id):
            return _json_response({"error": "not found"}, 404)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "invalid json"}, 400)
        prompt = body.get("prompt") or body.get("text") or ""
        if not prompt:
            return _json_response({"error": "prompt required"}, 400)

        cfg = load(workdir)
        sess = build_session(
            config=cfg,
            workdir=workdir,
            agent_name=body.get("agent") or rec.agent,
            user_id=user_id,
            session_id=sid,
            yolo=bool(body.get("yolo", True)),
            renderer=NullRenderer(),
            store=store,
        )
        if body.get("model"):
            try:
                sess.set_model(str(body["model"]))
            except Exception as exc:
                return _json_response({"error": f"invalid model: {exc}"}, 400)
        text = sess.prompt(str(prompt))
        return _json_response(
            {
                "session_id": sess.id,
                "text": text,
                "title": sess.title,
                "model": _session_model_payload(sess),
                "usage": sess._last_usage,
                "cost": sess._session_cost,
            }
        )

    async def user_usage(request: Request):
        user_id = request.path_params["user_id"]
        header_user = _user_id(request)
        if header_user != user_id and header_user != "anonymous":
            # allow self-read; admin can read any if token matches
            denied = _check_admin(request, config)
            if denied is not None and header_user != user_id:
                return denied
        return _json_response(store.sum_usage(user_id))

    async def skills_reload(request: Request):
        denied = _check_admin(request, config)
        if denied is not None:
            return denied
        skills = reload_skills(load(workdir), workdir)
        return _json_response(
            {"ok": True, "count": len(skills), "names": sorted(skills.keys())}
        )

    async def skills_list(_: Request):
        from ..skill import ensure_skills_fresh

        skills = ensure_skills_fresh(load(workdir), workdir)
        return _json_response(
            [
                {"name": s.name, "description": s.description, "location": str(s.location)}
                for s in skills.values()
            ]
        )

    routes = [
        Route("/health", health),
        Route("/v1/sessions", create_session, methods=["POST"]),
        Route("/v1/sessions", list_sessions, methods=["GET"]),
        Route("/v1/sessions/{session_id}", get_session, methods=["GET"]),
        Route("/v1/sessions/{session_id}/messages", post_message, methods=["POST"]),
        Route("/v1/users/{user_id}/usage", user_usage, methods=["GET"]),
        Route("/v1/skills", skills_list, methods=["GET"]),
        Route("/v1/skills/reload", skills_reload, methods=["POST"]),
    ]
    return Starlette(routes=routes)


def main(argv=None) -> int:
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(prog="sleuth-server")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    cwd = Path.cwd()
    load_dotenv(cwd)
    cfg = load(cwd)
    # Prefer mysql for server when SLEUTH_STORAGE_BACKEND unset and default_backend=mysql
    if not os.environ.get("SLEUTH_STORAGE_BACKEND") and cfg.server.default_backend:
        cfg.storage.backend = cfg.server.default_backend

    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    app = create_app(cwd)
    # re-bind store backend after possible default_backend nudge
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main)
