"""Thin Starlette HTTP API over the shared Session core."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from ..app import build_session, reload_mcp, reload_skills
from ..catalog import agents_payload, mcp_status_dict, models_payload, skills_payload
from ..config import load
from ..session import NullRenderer
from ..session_select import apply_session_selectors, skill_from_metadata, skills_from_metadata
from ..trace import message_timing_fields, project_session_trace
from ..storage.factory import create_store
from ..util.env import load_dotenv
from .streaming import StreamingRenderer, run_prompt_in_thread, sse_pack


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


def _session_skills_payload(sess) -> list:
    names = getattr(sess, "skill_names", None)
    if isinstance(names, (list, tuple)):
        return [str(n).strip() for n in names if str(n).strip()]
    name = getattr(sess, "skill_name", None)
    if isinstance(name, str) and name.strip():
        return [name.strip()]
    return []


def _skill_fields(sess) -> Dict[str, Any]:
    names = _session_skills_payload(sess)
    return {"skill": names[0] if names else None, "skills": names}


def _turn_files_fields(sess) -> Dict[str, Any]:
    from ..files.mailbox import public_files
    from ..storage.base import SessionRecord

    store = getattr(sess, "store", None)
    rec = None
    if store is not None:
        rec = store.get_session(sess.id)
    if rec is None:
        rec = SessionRecord(
            id=sess.id,
            directory=str(getattr(sess, "workdir", "") or ""),
            title=getattr(sess, "title", "") or "",
            user_id=getattr(sess, "user_id", "") or "",
            metadata={"files": list(getattr(sess, "_files", None) or [])},
        )
    ids = list(getattr(sess, "_turn_file_ids", None) or [])
    return {"files": public_files(rec, file_ids=ids or None) if ids else []}


def _mailbox_error_response(exc) -> Any:
    status = int(getattr(exc, "status", 400) or 400)
    return _json_response({"error": str(exc)}, status)


def create_app(workdir: Optional[Path] = None):
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import StreamingResponse
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
        cfg = load(workdir)
        sess = build_session(
            config=cfg,
            workdir=workdir,
            agent_name=cfg.default_agent,
            user_id=user_id,
            yolo=bool(body.get("yolo", True)),
            renderer=NullRenderer(),
            store=store,
        )
        err = apply_session_selectors(sess, body, cfg)
        if err:
            return _json_response({"error": err}, 400)
        sess._ensure_persisted()
        return _json_response(
            {
                "id": sess.id,
                "user_id": sess.user_id,
                "title": sess.title,
                "agent": sess.agent_name,
                "model": _session_model_payload(sess),
                **_skill_fields(sess),
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
                    "skill": r.get("skill"),
                    "skills": r.get("skills") or [],
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
        from ..privacy import maybe_desensitize

        sid = request.path_params["session_id"]
        user_id = _user_id(request)
        rec = store.get_session(sid)
        if rec is None or (rec.user_id and rec.user_id != user_id):
            return _json_response({"error": "not found"}, 404)
        messages = store.load_messages(sid)
        scrub_on = bool(getattr(config, "output_desensitize", True))
        return _json_response(
            {
                "id": rec.id,
                "user_id": rec.user_id,
                "title": rec.title,
                "agent": rec.agent,
                "model": rec.model,
                "skill": skill_from_metadata(rec.metadata),
                "skills": skills_from_metadata(rec.metadata),
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
                        "text": maybe_desensitize(m.text or "", enabled=scrub_on),
                        "usage": m.metadata.get("usage"),
                        "cost": m.metadata.get("cost"),
                        **message_timing_fields(m.metadata),
                    }
                    for m in messages
                ],
            }
        )

    async def get_session_trace(request: Request):
        from ..privacy import maybe_desensitize

        sid = request.path_params["session_id"]
        user_id = _user_id(request)
        rec = store.get_session(sid)
        if rec is None or (rec.user_id and rec.user_id != user_id):
            return _json_response({"error": "not found"}, 404)
        messages = store.load_messages(sid)
        payload = project_session_trace(messages, session_id=rec.id)
        scrub_on = bool(getattr(config, "output_desensitize", True))
        if scrub_on:
            for row in payload.get("records") or []:
                if isinstance(row, dict) and "preview" in row:
                    row["preview"] = maybe_desensitize(
                        row.get("preview") or "", enabled=True
                    )
        return _json_response(payload)

    def _owned_rec(request: Request):
        sid = request.path_params["session_id"]
        user_id = _user_id(request)
        rec = store.get_session(sid)
        if rec is None or (rec.user_id and rec.user_id != user_id):
            return None, _json_response({"error": "not found"}, 404)
        return rec, None

    def _bind_prompt_files(sess, body: Dict[str, Any]):
        if "file_ids" not in body:
            sess._prompt_file_ids = None
            return None
        raw = body.get("file_ids")
        if raw is None:
            sess._prompt_file_ids = []
            return None
        if not isinstance(raw, list):
            return "file_ids must be an array"
        ids = [str(x).strip() for x in raw if str(x).strip()]
        from ..files.mailbox import get_file, session_files

        files = session_files(sess)
        unknown = [fid for fid in ids if get_file(files, fid) is None]
        if unknown:
            return f"unknown file_ids: {', '.join(unknown)}"
        not_ready = [
            fid
            for fid in ids
            if str((get_file(files, fid) or {}).get("status") or "") != "ready"
        ]
        if not_ready:
            return f"file_ids not ready: {', '.join(not_ready)}"
        sess._prompt_file_ids = ids
        return None

    def _parse_message_body(body: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        """Return (prompt, error_message)."""
        prompt = body.get("prompt") or body.get("text") or ""
        if not prompt:
            return None, "prompt required"
        return str(prompt), None

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
        prompt, err = _parse_message_body(body)
        if err:
            return _json_response({"error": err}, 400)

        cfg = load(workdir)
        sess = build_session(
            config=cfg,
            workdir=workdir,
            agent_name=rec.agent or cfg.default_agent,
            user_id=user_id,
            session_id=sid,
            yolo=bool(body.get("yolo", True)),
            renderer=NullRenderer(),
            store=store,
        )
        err = apply_session_selectors(sess, body, cfg)
        if err:
            return _json_response({"error": err}, 400)
        ferr = _bind_prompt_files(sess, body)
        if ferr:
            return _json_response({"error": ferr}, 400)
        sess.block_on_question = False
        text = sess.prompt(str(prompt))
        return _json_response(
            {
                "session_id": sess.id,
                "text": text,
                "title": sess.title,
                "agent": sess.agent_name,
                "model": _session_model_payload(sess),
                **_skill_fields(sess),
                **_turn_files_fields(sess),
                "usage": sess._last_usage,
                "cost": sess._session_cost,
                **sess.ask_payload(),
            }
        )

    async def post_message_stream(request: Request):
        """SSE stream of one agent turn (text deltas + tool events + done)."""
        sid = request.path_params["session_id"]
        user_id = _user_id(request)
        rec = store.get_session(sid)
        if rec is None or (rec.user_id and rec.user_id != user_id):
            return _json_response({"error": "not found"}, 404)
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "invalid json"}, 400)
        prompt, err = _parse_message_body(body)
        if err:
            return _json_response({"error": err}, 400)

        cfg = load(workdir)
        renderer = StreamingRenderer(session_id=sid)
        sess = build_session(
            config=cfg,
            workdir=workdir,
            agent_name=rec.agent or cfg.default_agent,
            user_id=user_id,
            session_id=sid,
            yolo=bool(body.get("yolo", True)),
            renderer=renderer,
            store=store,
        )
        err = apply_session_selectors(sess, body, cfg)
        if err:
            return _json_response({"error": err}, 400)
        ferr = _bind_prompt_files(sess, body)
        if ferr:
            return _json_response({"error": ferr}, 400)
        sess.block_on_question = False

        run_prompt_in_thread(sess, str(prompt), renderer)

        async def event_gen():
            disconnected = False
            try:
                while True:
                    if await request.is_disconnected():
                        disconnected = True
                        sess.cancel()
                        break
                    event = await asyncio.to_thread(renderer.get_event, timeout=0.4)
                    if event is None:
                        break
                    if event.get("type") == "_poll":
                        yield b": ping\n\n"
                        continue
                    yield sse_pack(event)
            finally:
                if disconnected:
                    sess.cancel()
                done = {
                    "type": "done",
                    "session_id": sess.id,
                    "text": sess.last_assistant_text(),
                    "title": sess.title,
                    "agent": sess.agent_name,
                    "model": _session_model_payload(sess),
                    **_skill_fields(sess),
                    **_turn_files_fields(sess),
                    "usage": dict(sess._last_usage or {}),
                    "cost": float(sess._session_cost or 0),
                    **sess.ask_payload(),
                }
                yield sse_pack(done)

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def user_usage(request: Request):
        user_id = request.path_params["user_id"]
        header_user = _user_id(request)
        if header_user != user_id and header_user != "anonymous":
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
        return _json_response(skills_payload(load(workdir), workdir))

    async def models_list(_: Request):
        return _json_response(models_payload(load(workdir)))

    async def agents_list(request: Request):
        include_hidden = request.query_params.get("include_hidden", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cfg = load(workdir)
        mcp_manager = None
        try:
            from ..mcp import get_manager

            mcp_manager = get_manager(cfg)
        except Exception:
            mcp_manager = None
        return _json_response(
            agents_payload(
                cfg, include_hidden=include_hidden, mcp_manager=mcp_manager
            )
        )

    async def mcp_status(_: Request):
        return _json_response(mcp_status_dict(load(workdir)))

    async def mcp_reload(request: Request):
        denied = _check_admin(request, config)
        if denied is not None:
            return denied
        return _json_response(reload_mcp(load(workdir), workdir))

    async def create_file_upload(request: Request):
        rec, denied = _owned_rec(request)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "invalid json"}, 400)
        filename = str(body.get("filename") or "")
        mime = str(body.get("mime") or body.get("content_type") or "")
        try:
            size = int(body.get("size") if body.get("size") is not None else -1)
        except (TypeError, ValueError):
            return _json_response({"error": "size must be an integer"}, 400)
        cfg = load(workdir)
        from ..files.cos import CosNotConfigured
        from ..files.mailbox import MailboxError, create_upload

        try:
            payload = create_upload(
                config=cfg,
                store=store,
                rec=rec,
                filename=filename,
                mime=mime,
                size=size,
            )
        except CosNotConfigured as exc:
            return _json_response({"error": str(exc)}, 503)
        except MailboxError as exc:
            return _mailbox_error_response(exc)
        return _json_response(payload)

    async def complete_file_upload(request: Request):
        rec, denied = _owned_rec(request)
        if denied is not None:
            return denied
        try:
            body = await request.json()
        except Exception:
            return _json_response({"error": "invalid json"}, 400)
        file_id = str(body.get("file_id") or body.get("id") or "")
        if not file_id:
            return _json_response({"error": "file_id required"}, 400)
        cfg = load(workdir)
        from ..files.cos import CosNotConfigured
        from ..files.mailbox import MailboxError, complete_upload, public_file

        try:
            item = complete_upload(config=cfg, store=store, rec=rec, file_id=file_id)
        except CosNotConfigured as exc:
            return _json_response({"error": str(exc)}, 503)
        except MailboxError as exc:
            return _mailbox_error_response(exc)
        rec = store.get_session(rec.id) or rec
        view = public_file(rec, item, include_pending=True)
        return _json_response(view or {"id": item.get("id"), "status": item.get("status")})

    async def list_session_files(request: Request):
        rec, denied = _owned_rec(request)
        if denied is not None:
            return denied
        from ..files.mailbox import public_files

        include_pending = request.query_params.get("include_pending", "").lower() in (
            "1",
            "true",
            "yes",
        )
        return _json_response({"files": public_files(rec, include_pending=include_pending)})

    async def download_session_file(request: Request):
        rec, denied = _owned_rec(request)
        if denied is not None:
            return denied
        file_id = request.path_params["file_id"]
        cfg = load(workdir)
        from ..files.cos import CosNotConfigured
        from ..files.mailbox import MailboxError, download_target

        try:
            url = download_target(config=cfg, rec=rec, file_id=file_id)
        except CosNotConfigured as exc:
            return _json_response({"error": str(exc)}, 503)
        except MailboxError as exc:
            status = 404 if "not found" in str(exc).lower() else int(getattr(exc, "status", 400) or 400)
            return _json_response({"error": str(exc)}, status)
        want_json = request.query_params.get("json", "").lower() in ("1", "true", "yes")
        accept = (request.headers.get("accept") or "").lower()
        if want_json or "application/json" in accept:
            return _json_response({"download_url": url})
        from starlette.responses import RedirectResponse

        return RedirectResponse(url, status_code=302)

    routes = [
        Route("/health", health),
        Route("/v1/sessions", create_session, methods=["POST"]),
        Route("/v1/sessions", list_sessions, methods=["GET"]),
        Route("/v1/sessions/{session_id}", get_session, methods=["GET"]),
        Route("/v1/sessions/{session_id}/trace", get_session_trace, methods=["GET"]),
        Route("/v1/sessions/{session_id}/messages", post_message, methods=["POST"]),
        Route(
            "/v1/sessions/{session_id}/messages/stream",
            post_message_stream,
            methods=["POST"],
        ),
        Route(
            "/v1/sessions/{session_id}/files/uploads",
            create_file_upload,
            methods=["POST"],
        ),
        Route(
            "/v1/sessions/{session_id}/files/complete",
            complete_file_upload,
            methods=["POST"],
        ),
        Route("/v1/sessions/{session_id}/files", list_session_files, methods=["GET"]),
        Route(
            "/v1/sessions/{session_id}/files/{file_id}",
            download_session_file,
            methods=["GET"],
        ),
        Route("/v1/users/{user_id}/usage", user_usage, methods=["GET"]),
        Route("/v1/models", models_list, methods=["GET"]),
        Route("/v1/agents", agents_list, methods=["GET"]),
        Route("/v1/mcp", mcp_status, methods=["GET"]),
        Route("/v1/mcp/reload", mcp_reload, methods=["POST"]),
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
    if not os.environ.get("SLEUTH_STORAGE_BACKEND") and cfg.server.default_backend:
        cfg.storage.backend = cfg.server.default_backend

    host = args.host or cfg.server.host
    port = args.port or cfg.server.port
    app = create_app(cwd)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main)
