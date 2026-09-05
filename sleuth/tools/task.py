"""task tool — sync nested subagents (port of opencode `tool/task.ts`).

Creates a child Session with `parent_id`, runs the prompt loop for the chosen
`subagent_type`, and returns the child's final text. Background mode is not
ported (requires OPENCODE_EXPERIMENTAL_BACKGROUND_SUBAGENTS infrastructure).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .base import ToolContext, ToolResult

_DESCRIPTION = """Launch a new agent to handle complex, multistep tasks autonomously.

When using the Task tool, you must specify a subagent_type parameter to select which agent type to use.

When NOT to use the Task tool:
- If you want to read a specific file path, use the Read or Glob tool instead
- If you are searching for a specific class definition, use the Grep tool instead
- If you are searching for code within a specific file or set of 2-3 files, use the Read tool instead
- If no available agent is a good fit for the task, use other tools directly

Usage notes:
1. Once you have delegated work to an agent, do not duplicate that work yourself.
2. When the agent is done, it will return a single message back to you. The result is not visible to the user — summarise it if needed.
3. The output includes a task_id you can reuse later to continue the same subagent session.
4. Each agent invocation starts with a fresh context unless you provide task_id to resume.
"""


class TaskParams(BaseModel):
    description: str = Field(description="A short (3-5 words) description of the task")
    prompt: str = Field(description="The task for the agent to perform")
    subagent_type: str = Field(description="The type of specialized agent to use for this task")
    task_id: Optional[str] = Field(
        default=None,
        description="Resume a previous task session instead of creating a fresh one",
    )


class TaskTool:
    name = "task"
    description = _DESCRIPTION
    params = TaskParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = TaskParams(**args)
        session = getattr(ctx, "session", None)
        if session is None:
            return ToolResult.error("task", "task tool requires a live session context")

        try:
            ctx.ask("task", [p.subagent_type], ["*"])
        except Exception as exc:
            return ToolResult.error("task", f"permission denied: {exc}")

        from ..memory.acl import assert_resource_allowed
        from ..orchestration import agent_delegatable, orch_cfg

        user_id = getattr(session, "user_id", None) or getattr(session.config, "user_id", "local") or "local"
        try:
            assert_resource_allowed(session.config, user_id, "agent", p.subagent_type)
        except Exception as exc:
            return ToolResult.error("task", f"permission denied: {exc}")

        ocfg = orch_cfg(session.config)
        if ocfg.delegate_enabled and not agent_delegatable(session.config, p.subagent_type):
            return ToolResult.error("task", ocfg.err_delegate_not_allowed)

        from ..agent import known_agents, ruleset_for
        from ..config import parse_model_ref
        from ..permission import Permission, Rule, from_config as permission_from_config
        from ..provider.factory import build_provider, resolve_model
        from ..session import NullRenderer, Session
        from ..title import default_title

        agents = known_agents(session.config)
        if p.subagent_type not in agents:
            return ToolResult.error(
                "task",
                f"Unknown agent type: {p.subagent_type}. Available: {', '.join(sorted(agents))}",
            )

        depth = _session_depth(session)
        max_depth = getattr(session.config, "subagent_depth", 1) or 1
        if depth >= max_depth:
            return ToolResult.error(
                "task",
                f'Subagent depth limit reached ({max_depth}). '
                f'Increase "subagent_depth" to allow nested subagents.',
            )

        rules = ruleset_for(p.subagent_type)
        agent_cfg = session.config.agent(p.subagent_type)
        if agent_cfg.permission:
            rules = rules + permission_from_config(agent_cfg.permission)
        if not any(r.permission == "task" for r in rules):
            rules = rules + [Rule("task", "*", "deny")]
        if not any(r.permission in ("todo", "todowrite") for r in rules):
            rules = rules + [Rule("todo", "*", "deny")]
        child_perm = Permission(rules=rules, ask_fn=session.permission.ask_fn)

        try:
            if agent_cfg.model:
                pid, mid = parse_model_ref(agent_cfg.model)
                provider = build_provider(session.config, pid)
                model_id = mid
            else:
                provider, model_id = session.provider, session.model_id
                # Prefer agent/global model if set
                try:
                    provider, model_id = resolve_model(session.config, p.subagent_type)
                except Exception:
                    pass
        except Exception:
            provider, model_id = session.provider, session.model_id

        child: Optional[Session] = None
        if p.task_id and session.store is not None:
            try:
                child = Session.load(
                    provider=provider,
                    registry=session.registry,
                    config=session.config,
                    workdir=session.workdir,
                    permission=child_perm,
                    store=session.store,
                    session_id_value=p.task_id,
                    agent_name=p.subagent_type,
                    model_id=model_id,
                    renderer=NullRenderer(),
                )
                child.parent_id = session.id
            except Exception:
                child = None

        if child is None:
            child = Session(
                provider=provider,
                registry=session.registry,
                config=session.config,
                workdir=session.workdir,
                permission=child_perm,
                agent_name=p.subagent_type,
                model_id=model_id,
                renderer=NullRenderer(),
                store=session.store,
                title=default_title(child=True),
                parent_id=session.id,
                user_id=getattr(session, "user_id", None)
                or getattr(session.config, "user_id", "local")
                or "local",
            )

        try:
            from .memory.acl import attach_identity

            attach_identity(child)
        except Exception:
            child.role_id = None
            child.org_id = None

        try:
            text = child.prompt(p.prompt)
        except Exception as exc:
            return ToolResult.error(
                "task",
                _render_output(child.id, "error", str(exc), p.description),
            )

        return ToolResult.success(
            "task",
            _render_output(child.id, "completed", text or "(no output)", p.description),
            task_id=child.id,
            subagent_type=p.subagent_type,
        )


def _session_depth(session) -> int:
    depth = 0
    parent_id = getattr(session, "parent_id", None)
    store = getattr(session, "store", None)
    seen = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        depth += 1
        if store is None:
            break
        rec = store.get_session(parent_id)
        if rec is None:
            break
        parent_id = (rec.metadata or {}).get("parent_id")
    # If we only have parent_id on the object (no store chain), count at least 1
    if depth == 0 and getattr(session, "parent_id", None):
        return 1
    return depth


def _render_output(session_id: str, state: str, text: str, summary: str) -> str:
    tag = "task_error" if state == "error" else "task_result"
    return (
        f'<task id="{session_id}" state="{state}">\n'
        f"<summary>{summary}</summary>\n"
        f"<{tag}>\n{text}\n</{tag}>\n"
        f"</task>"
    )
