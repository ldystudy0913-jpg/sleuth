"""MCP Agent Card: parse remote agent metadata for Sleuth registration.

Opt-in via McpServerConfig.agent=True. Does not change default MCP tool bridging.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import AgentConfig, Config
from ..skill import SkillInfo

# Remote "allow" on these is downgraded to "ask" unless trust env is set.
_SENSITIVE_TOOLS = frozenset({"bash", "edit", "write", "task"})


def trust_remote_permissions() -> bool:
    raw = os.environ.get("SLEUTH_MCP_AGENT_TRUST_PERMISSIONS", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def sanitize_permissions(permission: Dict[str, Any]) -> Dict[str, Any]:
    """Apply local safety policy to card-suggested permissions."""
    out: Dict[str, Any] = {}
    trust = trust_remote_permissions()
    for key, value in (permission or {}).items():
        k = str(key)
        v = value
        if not trust and k in _SENSITIVE_TOOLS and str(v).lower() == "allow":
            v = "ask"
        out[k] = v
    return out


def parse_agent_card(
    raw: Any,
    *,
    server_name: str,
) -> Tuple[AgentConfig, List[SkillInfo]]:
    """Parse get_agent_card JSON into AgentConfig + synthetic skills."""
    if isinstance(raw, str):
        data = json.loads(raw)
    elif isinstance(raw, dict):
        data = raw
    else:
        raise ValueError(f"agent card must be object or JSON string, got {type(raw)}")

    if not isinstance(data, dict):
        raise ValueError("agent card JSON must be an object")

    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("agent card missing name")

    title = data.get("title") or data.get("label")
    if title is not None:
        title = str(title).strip() or None

    prompt = data.get("prompt")
    if prompt is not None:
        prompt = str(prompt)

    description = data.get("description")
    if description is not None:
        description = str(description)

    mode = str(data.get("mode") or "primary")
    permission = data.get("permission") or {}
    if not isinstance(permission, dict):
        permission = {}
    permission = sanitize_permissions({str(k): v for k, v in permission.items()})

    steps = data.get("steps")
    orchestration = data.get("orchestration")
    primary_tool = data.get("primary_tool") or data.get("primaryTool")
    delegatable = data.get("delegatable")
    if delegatable is not None and not isinstance(delegatable, bool):
        delegatable = str(delegatable).strip().lower() in ("1", "true", "yes", "on")
    execution = data.get("execution")
    auto_invoke_prompt_field = data.get("auto_invoke_prompt_field") or data.get("autoInvokePromptField")
    auto_invoke_args: Dict[str, Any] = {}
    raw_args = data.get("auto_invoke_args") or data.get("autoInvokeArgs")
    if isinstance(raw_args, dict):
        auto_invoke_args.update(raw_args)
    raw_args_json = data.get("auto_invoke_args_json") or data.get("autoInvokeArgsJson")
    if isinstance(raw_args_json, str) and raw_args_json.strip():
        try:
            parsed = json.loads(raw_args_json)
            if isinstance(parsed, dict):
                auto_invoke_args.update(parsed)
        except json.JSONDecodeError:
            pass

    agent = AgentConfig(
        name=name,
        title=title,
        prompt=prompt,
        description=description,
        mode=mode,
        permission=permission,
        steps=int(steps) if steps is not None else 50,
        model=str(data["model"]) if data.get("model") else None,
        skill_names=[],
        orchestration=str(orchestration).strip() if orchestration else None,
        primary_tool=str(primary_tool).strip() if primary_tool else None,
        delegatable=bool(delegatable) if delegatable is not None else None,
        execution=str(execution).strip() if execution else None,
        auto_invoke_prompt_field=str(auto_invoke_prompt_field).strip() if auto_invoke_prompt_field else None,
        auto_invoke_args=auto_invoke_args,
    )

    skills: List[SkillInfo] = []
    raw_skills = data.get("skills") or []
    if not isinstance(raw_skills, list):
        raw_skills = []
    listed: List[str] = []
    seen_names = set()
    for item in raw_skills:
        if isinstance(item, str):
            sname = item.strip()
            if sname and sname not in seen_names:
                seen_names.add(sname)
                listed.append(sname)
            continue
        if not isinstance(item, dict):
            continue
        sname = str(item.get("name") or "").strip()
        if not sname:
            continue
        if sname not in seen_names:
            seen_names.add(sname)
            listed.append(sname)
        content = item.get("content")
        if content is None:
            continue
        content_s = str(content)
        desc = str(item.get("description") or "").strip()
        mcp_req = item.get("mcp") or [server_name]
        if isinstance(mcp_req, str):
            mcp_req = [mcp_req]
        if not isinstance(mcp_req, list):
            mcp_req = [server_name]
        tools_req = item.get("tools") or []
        if isinstance(tools_req, str):
            tools_req = [tools_req]
        if not isinstance(tools_req, list):
            tools_req = []
        skills.append(
            SkillInfo(
                name=sname,
                description=desc,
                location=Path(f"mcp_agent/{server_name}/{sname}/SKILL.md"),
                content=content_s,
                required_mcp=[str(x) for x in mcp_req],
                required_tools=[str(x) for x in tools_req],
                owner_agent=name,
            )
        )
    agent.skill_names = listed

    return agent, skills


def merge_agent_fill_empty(existing: AgentConfig, incoming: AgentConfig) -> AgentConfig:
    """Fill only unset fields on existing from incoming (local wins)."""
    if existing.prompt is None and incoming.prompt is not None:
        existing.prompt = incoming.prompt
    if existing.title is None and incoming.title is not None:
        existing.title = incoming.title
    if existing.description is None and incoming.description is not None:
        existing.description = incoming.description
    if existing.model is None and incoming.model is not None:
        existing.model = incoming.model
    if not existing.permission and incoming.permission:
        existing.permission = dict(incoming.permission)
    else:
        for k, v in incoming.permission.items():
            if k not in existing.permission:
                existing.permission[k] = v
    if existing.mode == "all" and incoming.mode and incoming.mode != "all":
        existing.mode = incoming.mode
    if incoming.skill_names:
        seen = set(existing.skill_names or [])
        merged_names = list(existing.skill_names or [])
        for n in incoming.skill_names:
            if n not in seen:
                seen.add(n)
                merged_names.append(n)
        existing.skill_names = merged_names
    if existing.orchestration is None and incoming.orchestration:
        existing.orchestration = incoming.orchestration
    if existing.primary_tool is None and incoming.primary_tool:
        existing.primary_tool = incoming.primary_tool
    if existing.delegatable is None and incoming.delegatable is not None:
        existing.delegatable = incoming.delegatable
    if existing.execution is None and incoming.execution:
        existing.execution = incoming.execution
    if existing.auto_invoke_prompt_field is None and incoming.auto_invoke_prompt_field:
        existing.auto_invoke_prompt_field = incoming.auto_invoke_prompt_field
    if incoming.auto_invoke_args:
        for k, v in incoming.auto_invoke_args.items():
            existing.auto_invoke_args.setdefault(k, v)
    return existing


def apply_agent_cards_to_config(
    config: Config,
    cards: Dict[str, dict],
    *,
    server_by_agent: Optional[Dict[str, str]] = None,
) -> List[SkillInfo]:
    """Register cards into config.agents (fill-empty). Return skills to merge."""
    all_skills: List[SkillInfo] = []
    server_by_agent = server_by_agent or {}
    for agent_name, raw in cards.items():
        if not isinstance(raw, dict):
            continue
        server = str(raw.get("mcp_server") or server_by_agent.get(agent_name) or "mcp")
        try:
            agent_cfg, skills = parse_agent_card(raw, server_name=server)
        except Exception:
            continue
        canonical = (agent_cfg.name or agent_name).strip() or agent_name
        if canonical not in config.agents:
            config.agents[canonical] = agent_cfg
        else:
            merge_agent_fill_empty(config.agents[canonical], agent_cfg)
        config.register_agent_alias(canonical, canonical)
        config.register_agent_alias(agent_name, canonical)
        config.register_agent_alias(server, canonical)
        all_skills.extend(skills)
    return all_skills
