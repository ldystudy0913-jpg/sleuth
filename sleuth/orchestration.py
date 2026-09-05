"""Session-layer orchestration modes (A–G).

All tool execution goes through ``Session.execute_guarded_tool`` → registry →
``McpBridgeTool`` (ACL, permission, attachment injection, file harvest).
Never call ``McpManager.call_tool`` directly from here.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .config import AgentConfig, Config, OrchestrationConfig
from .tools.base import ToolContext, ToolResult

_jobs_lock = threading.Lock()
_jobs: Dict[str, Dict[str, Any]] = {}


@dataclass
class OrchestrationTurn:
    """Result of a non-host turn (invoke / auto_run / parallel / async accept)."""

    text: str = ""
    orchestrated: bool = True
    mode: str = ""
    job_id: Optional[str] = None
    error: Optional[str] = None
    parallel_results: List[Dict[str, Any]] = field(default_factory=list)


def orch_cfg(config: Config) -> OrchestrationConfig:
    return getattr(config, "orchestration", None) or OrchestrationConfig()


def valid_modes(config: Config) -> set:
    raw = orch_cfg(config).modes or ""
    return {m.strip() for m in raw.split(",") if m.strip()}


def valid_executions(config: Config) -> set:
    raw = orch_cfg(config).executions or ""
    return {m.strip() for m in raw.split(",") if m.strip()}


def _async_tokens(config: Config) -> set:
    raw = orch_cfg(config).async_tokens or ""
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def _session_metadata(session) -> dict:
    store = getattr(session, "store", None)
    if store is None:
        return {}
    rec = store.get_session(getattr(session, "id", "") or "")
    if rec is None:
        return {}
    meta = rec.metadata
    return dict(meta) if isinstance(meta, dict) else {}


def _agent_cfg(config: Config, agent_name: str) -> AgentConfig:
    return config.agent(agent_name)


def agent_delegatable(config: Config, agent_name: str) -> bool:
    cfg = _agent_cfg(config, agent_name)
    if cfg.delegatable is not None:
        return bool(cfg.delegatable)
    return bool(orch_cfg(config).default_delegatable)


def agent_execution(config: Config, agent_name: str) -> str:
    cfg = _agent_cfg(config, agent_name)
    if cfg.execution:
        return str(cfg.execution).strip()
    return str(orch_cfg(config).default_execution or "sync").strip() or "sync"


def resolve_orchestration(session, body: Dict[str, Any]) -> str:
    """Priority: request body → session metadata → Agent Card → global default."""
    config = session.config
    ocfg = orch_cfg(config)
    agent_cfg = _agent_cfg(config, session.agent_name)

    invoke_key = ocfg.body_invoke_key
    auto_key = ocfg.body_auto_run_key
    parallel_key = ocfg.body_parallel_key
    exec_key = ocfg.body_execution_key
    orch_key = ocfg.body_orchestration_key

    if body.get(invoke_key):
        return "invoke"
    if body.get(auto_key):
        return "auto_invoke"
    if body.get(parallel_key):
        return "parallel"
    exec_val = body.get(exec_key)
    if exec_val is not None and str(exec_val).strip().lower() in _async_tokens(config):
        return "async"
    if orch_key in body and body.get(orch_key):
        mode = str(body[orch_key]).strip()
        if mode in valid_modes(config):
            return mode

    meta = _session_metadata(session)
    meta_mode = meta.get(ocfg.metadata_key)
    if meta_mode:
        mode = str(meta_mode).strip()
        if mode in valid_modes(config):
            return mode

    if agent_cfg.orchestration:
        mode = str(agent_cfg.orchestration).strip()
        if mode in valid_modes(config):
            return mode

    default = str(ocfg.default_mode or "host").strip() or "host"
    return default if default in valid_modes(config) else "host"


def apply_orchestration_metadata(session, body: Dict[str, Any]) -> None:
    """Persist orchestration / execution from body into session metadata."""
    ocfg = orch_cfg(session.config)
    orch_key = ocfg.body_orchestration_key
    exec_key = ocfg.body_execution_key
    meta_key = ocfg.metadata_key
    exec_meta_key = ocfg.execution_metadata_key
    updates: Dict[str, Any] = {}
    if orch_key in body and body.get(orch_key):
        mode = str(body[orch_key]).strip()
        if mode in valid_modes(session.config):
            updates[meta_key] = mode
    if exec_key in body and body.get(exec_key):
        ex = str(body[exec_key]).strip()
        if ex in valid_executions(session.config):
            updates[exec_meta_key] = ex
    if not updates:
        return
    store = getattr(session, "store", None)
    if store is None:
        return
    rec = store.get_session(session.id)
    if rec is None:
        return
    rec.metadata = dict(rec.metadata or {})
    rec.metadata.update(updates)
    store.update_session(rec)


def message_requires_prompt(body: Dict[str, Any], config: Config) -> bool:
    ocfg = orch_cfg(config)
    if body.get(ocfg.body_invoke_key) or body.get(ocfg.body_auto_run_key):
        return False
    if body.get(ocfg.body_parallel_key):
        return False
    exec_val = body.get(ocfg.body_execution_key)
    if exec_val is not None and str(exec_val).strip().lower() in _async_tokens(config):
        return False
    return True


def parse_message_prompt(body: Dict[str, Any], config: Config) -> Tuple[Optional[str], Optional[str]]:
    prompt = body.get("prompt") or body.get("text") or ""
    if not prompt and message_requires_prompt(body, config):
        return None, orch_cfg(config).err_prompt_required
    return str(prompt), None


def _prompt_field(config: Config, agent_name: str) -> str:
    agent_cfg = _agent_cfg(config, agent_name)
    if agent_cfg.auto_invoke_prompt_field:
        return str(agent_cfg.auto_invoke_prompt_field).strip()
    return str(orch_cfg(config).auto_invoke_prompt_field or "question").strip() or "question"


def build_auto_invoke_args(session, prompt: str, body: Dict[str, Any]) -> Dict[str, Any]:
    agent_cfg = _agent_cfg(session.config, session.agent_name)
    args = dict(agent_cfg.auto_invoke_args or {})
    invoke_key = orch_cfg(session.config).body_invoke_key
    raw_invoke = body.get(invoke_key)
    if isinstance(raw_invoke, dict):
        extra = raw_invoke.get("args")
        if isinstance(extra, dict):
            args.update(extra)
    field_name = _prompt_field(session.config, session.agent_name)
    if prompt and field_name:
        args.setdefault(field_name, prompt)
    q_field = _prompt_field(session.config, session.agent_name)
    if prompt and q_field != field_name:
        args.setdefault(q_field, prompt)
    return args


def _resolve_tool_name(session, body: Dict[str, Any], mode: str) -> Tuple[Optional[str], Optional[str]]:
    ocfg = orch_cfg(session.config)
    agent_cfg = _agent_cfg(session.config, session.agent_name)
    invoke_key = ocfg.body_invoke_key
    if mode in ("invoke", "async"):
        raw = body.get(invoke_key)
        if isinstance(raw, dict):
            tool = str(raw.get("tool") or "").strip()
            if tool:
                return tool, None
    if mode in ("auto_invoke", "async") and not body.get(invoke_key):
        tool = (agent_cfg.primary_tool or "").strip()
        if tool:
            return tool, None
        return None, ocfg.err_missing_primary_tool
    if mode == "invoke":
        return None, ocfg.err_missing_tool
    return None, ocfg.err_missing_primary_tool


def _format_invoke_text(tool_name: str, result: ToolResult) -> str:
    if result.is_error:
        return result.output or f"{tool_name} failed"
    output = (result.output or "").strip()
    if not output:
        return f"{tool_name} completed"
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output
    if not isinstance(payload, dict):
        return output
    if payload.get("status") == "need_input":
        missing = payload.get("missing") or []
        if isinstance(missing, list) and missing:
            lines = ["需要补充信息："] + [f"- {m}" for m in missing]
            return "\n".join(lines)
    score = payload.get("score")
    findings = payload.get("findings")
    parts: List[str] = []
    if score is not None:
        parts.append(f"评分：{score}")
    if isinstance(findings, list) and findings:
        parts.append("发现问题：")
        for item in findings[:20]:
            if isinstance(item, dict):
                loc = item.get("location") or ""
                msg = item.get("message") or item.get("issue") or str(item)
                parts.append(f"- {msg}" + (f"（{loc}）" if loc else ""))
            else:
                parts.append(f"- {item}")
    if payload.get("files"):
        parts.append("已生成文件，可在会话中下载。")
    if parts:
        return "\n".join(parts)
    detail = payload.get("detail") or payload.get("message")
    if detail:
        return str(detail)
    return output


def _run_guarded(session, tool_name: str, args: dict, *, agent_override: Optional[str] = None) -> ToolResult:
    saved_agent = session.agent_name
    if agent_override:
        session.agent_name = session.config.resolve_agent_name(agent_override)
    try:
        result = session.execute_guarded_tool(tool_name, args or {})
    finally:
        session.agent_name = saved_agent
    return result


def _invoke_sync(session, tool_name: str, args: dict, *, agent_override: Optional[str] = None) -> OrchestrationTurn:
    result = _run_guarded(session, tool_name, args, agent_override=agent_override)
    session._harvest_turn_sources(result)
    text = _format_invoke_text(tool_name, result)
    if not result.is_error:
        text = session._append_sources_footer(text)
    return OrchestrationTurn(text=text, mode="invoke", error=result.output if result.is_error else None)


def _count_user_jobs(user_id: str) -> int:
    with _jobs_lock:
        return sum(
            1
            for j in _jobs.values()
            if j.get("user_id") == user_id and j.get("status") in ("queued", "running")
        )


def _store_job(session, job_id: str, payload: dict) -> None:
    store = getattr(session, "store", None)
    ocfg = orch_cfg(session.config)
    jobs_key = ocfg.jobs_metadata_key
    if store is None:
        return
    rec = store.get_session(session.id)
    if rec is None:
        return
    rec.metadata = dict(rec.metadata or {})
    jobs = list(rec.metadata.get(jobs_key) or [])
    jobs.append({"id": job_id, **payload})
    rec.metadata[jobs_key] = jobs[-50:]
    store.update_session(rec)


def _run_async_job(
    session_id: str,
    user_id: str,
    tool_name: str,
    args: dict,
    agent_name: str,
    job_id: str,
    build_session_fn,
    workdir,
    store,
) -> None:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job["status"] = "running"
            job["started_at"] = time.time()
    try:
        sess = build_session_fn(
            workdir=workdir,
            agent_name=agent_name,
            user_id=user_id,
            session_id=session_id,
            store=store,
        )
        result = _run_guarded(sess, tool_name, args)
        text = _format_invoke_text(tool_name, result)
        status = "failed" if result.is_error else "done"
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = status
                job["text"] = text
                job["error"] = result.output if result.is_error else None
                job["finished_at"] = time.time()
        _store_job(
            sess,
            job_id,
            {"status": status, "tool": tool_name, "text": text},
        )
    except Exception as exc:
        with _jobs_lock:
            job = _jobs.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = str(exc)
                job["finished_at"] = time.time()


def _enqueue_async(
    session,
    tool_name: str,
    args: dict,
    *,
    build_session_fn,
    workdir,
) -> OrchestrationTurn:
    ocfg = orch_cfg(session.config)
    if not ocfg.async_enabled:
        return OrchestrationTurn(error=ocfg.err_async_disabled, mode="async")
    user_id = getattr(session, "user_id", None) or "local"
    if _count_user_jobs(user_id) >= ocfg.async_max_jobs_per_user:
        return OrchestrationTurn(
            error=f"async job limit reached ({ocfg.async_max_jobs_per_user})",
            mode="async",
        )
    job_id = str(uuid.uuid4())
    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "session_id": session.id,
            "user_id": user_id,
            "agent": session.agent_name,
            "tool": tool_name,
            "status": "queued",
            "created_at": time.time(),
        }
    thread = threading.Thread(
        target=_run_async_job,
        args=(
            session.id,
            user_id,
            tool_name,
            args,
            session.agent_name,
            job_id,
            build_session_fn,
            workdir,
            session.store,
        ),
        daemon=True,
    )
    thread.start()
    _store_job(session, job_id, {"status": "queued", "tool": tool_name})
    return OrchestrationTurn(
        text="",
        mode="async",
        job_id=job_id,
    )


def get_job(job_id: str, *, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        if user_id and job.get("user_id") != user_id:
            return None
        return dict(job)


def _run_parallel(session, body: Dict[str, Any], prompt: str) -> OrchestrationTurn:
    ocfg = orch_cfg(session.config)
    if not ocfg.parallel_enabled:
        return OrchestrationTurn(error=ocfg.err_parallel_disabled, mode="parallel")
    branches = body.get(ocfg.body_parallel_key)
    if not isinstance(branches, list) or not branches:
        return OrchestrationTurn(error=ocfg.err_parallel_branch, mode="parallel")
    if len(branches) > ocfg.parallel_max_branches:
        return OrchestrationTurn(
            error=f"parallel branch limit ({ocfg.parallel_max_branches}) exceeded",
            mode="parallel",
        )
    from .memory.acl import assert_resource_allowed

    results: List[Dict[str, Any]] = []
    lines: List[str] = []
    for idx, branch in enumerate(branches):
        if not isinstance(branch, dict):
            results.append({"index": idx, "error": "branch must be an object"})
            continue
        branch_agent = str(branch.get("agent") or session.agent_name).strip()
        try:
            assert_resource_allowed(session.config, session.user_id, "agent", branch_agent)
        except Exception as exc:
            results.append({"index": idx, "agent": branch_agent, "error": str(exc)})
            lines.append(f"[{branch_agent}] 权限不足：{exc}")
            continue
        agent_cfg = _agent_cfg(session.config, branch_agent)
        tool = str(branch.get("tool") or agent_cfg.primary_tool or "").strip()
        if not tool:
            results.append({"index": idx, "agent": branch_agent, "error": ocfg.err_missing_primary_tool})
            continue
        args = dict(agent_cfg.auto_invoke_args or {})
        extra = branch.get("args")
        if isinstance(extra, dict):
            args.update(extra)
        branch_prompt = str(branch.get("prompt") or prompt or "").strip()
        field_name = _prompt_field(session.config, branch_agent)
        if branch_prompt and field_name:
            args.setdefault(field_name, branch_prompt)
        old_files = getattr(session, "_prompt_file_ids", None)
        branch_files = branch.get("file_ids")
        if isinstance(branch_files, list):
            session._prompt_file_ids = [str(x).strip() for x in branch_files if str(x).strip()]
        try:
            result = _run_guarded(session, tool, args, agent_override=branch_agent)
        finally:
            session._prompt_file_ids = old_files
        session._harvest_turn_sources(result)
        entry = {
            "index": idx,
            "agent": branch_agent,
            "tool": tool,
            "ok": not result.is_error,
            "text": _format_invoke_text(tool, result),
        }
        if result.is_error:
            entry["error"] = result.output
        results.append(entry)
        lines.append(f"## {branch_agent}\n{entry['text']}")
    text = "\n\n".join(lines)
    text = session._append_sources_footer(text)
    return OrchestrationTurn(text=text, mode="parallel", parallel_results=results)


def try_orchestrated_turn(
    session,
    body: Dict[str, Any],
    prompt: str,
    *,
    build_session_fn=None,
    workdir=None,
) -> Optional[OrchestrationTurn]:
    """Return a turn result when the request bypasses the host LLM loop; else None."""
    ocfg = orch_cfg(session.config)
    mode = resolve_orchestration(session, body)
    apply_orchestration_metadata(session, body)

    invoke_key = ocfg.body_invoke_key
    auto_key = ocfg.body_auto_run_key
    parallel_key = ocfg.body_parallel_key
    exec_key = ocfg.body_execution_key

    if body.get(parallel_key):
        return _run_parallel(session, body, prompt)

    exec_val = body.get(exec_key)
    is_async = exec_val is not None and str(exec_val).strip().lower() in _async_tokens(session.config)
    agent_async = agent_execution(session.config, session.agent_name) == "async"
    wants_async = is_async or (agent_async and (body.get(auto_key) or body.get(invoke_key)))

    if body.get(invoke_key):
        if not ocfg.invoke_enabled:
            return OrchestrationTurn(error=ocfg.err_invoke_disabled, mode="invoke")
        tool, err = _resolve_tool_name(session, body, "invoke")
        if err:
            return OrchestrationTurn(error=err, mode="invoke")
        raw = body.get(invoke_key) or {}
        args = raw.get("args") if isinstance(raw, dict) else {}
        if not isinstance(args, dict):
            args = {}
        if wants_async and build_session_fn is not None:
            return _enqueue_async(session, tool, args, build_session_fn=build_session_fn, workdir=workdir)
        return _invoke_sync(session, tool, args)

    if body.get(auto_key):
        if not ocfg.auto_run_enabled:
            return OrchestrationTurn(error=ocfg.err_auto_run_disabled, mode="auto_invoke")
        tool, err = _resolve_tool_name(session, body, "auto_invoke")
        if err:
            return OrchestrationTurn(error=err, mode="auto_invoke")
        args = build_auto_invoke_args(session, prompt, body)
        if wants_async and build_session_fn is not None:
            return _enqueue_async(session, tool, args, build_session_fn=build_session_fn, workdir=workdir)
        turn = _invoke_sync(session, tool, args)
        turn.mode = "auto_invoke"
        return turn

    if wants_async and build_session_fn is not None:
        tool, err = _resolve_tool_name(session, body, "async")
        if err:
            return OrchestrationTurn(error=err, mode="async")
        args = build_auto_invoke_args(session, prompt, body)
        return _enqueue_async(session, tool, args, build_session_fn=build_session_fn, workdir=workdir)

    if mode in ("host", "pipeline", "delegate"):
        return None

    return None
