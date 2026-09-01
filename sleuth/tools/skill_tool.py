"""skill tool — load an external SKILL.md into the conversation ."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field

from .base import ToolContext, ToolResult
from ..skill import check_skill_deps, get_skill, get_skills


class SkillParams(BaseModel):
    name: str = Field(description="The name of the skill from available_skills")


class SkillTool:
    name = "skill"
    description = (
        "Load a skill (instruction pack) by name into the conversation. "
        "Skills are discovered from global skill dirs, .sleuth/skill(s), "
        "and config skills.paths / skills.urls — they do not need to live "
        "inside the project. After loading, follow the skill instructions "
        "and use normal tools (builtin or MCP) to do the work."
    )
    params = SkillParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = SkillParams(**args)
        try:
            ctx.ask("skill", [p.name], [p.name])
        except Exception as exc:
            return ToolResult.error("skill", f"permission denied: {exc}")

        info = get_skill(p.name)
        if info is None:
            available = ", ".join(sorted(get_skills())) or "none"
            return ToolResult.error(
                "skill",
                f'Skill "{p.name}" not found. Available skills: {available}',
            )

        session = getattr(ctx, "session", None)
        cfg = getattr(session, "config", None) if session is not None else None
        if session is not None and cfg is not None:
            from ..memory.acl import resource_allowed

            owner = (getattr(info, "owner_agent", None) or "").strip()
            user_id = getattr(session, "user_id", None) or ""
            if owner:
                current = cfg.resolve_agent_name(getattr(session, "agent_name", None) or "")
                if cfg.resolve_agent_name(owner) != current:
                    return ToolResult.error(
                        "skill",
                        f'permission denied: skill "{info.name}" is private to another agent',
                    )
            elif not resource_allowed(cfg, user_id, "skill", info.name):
                return ToolResult.error(
                    "skill",
                    f'permission denied: skill not authorized: {info.name}',
                )

        # Dependency warnings (mcp / tools frontmatter)
        tool_names: list = []
        session = getattr(ctx, "session", None)
        mcp_manager = None
        if session is not None:
            reg = getattr(session, "registry", None)
            if reg is not None:
                tool_names = list(reg.names())
            mcp_manager = getattr(session, "_mcp_manager", None)
        warnings = check_skill_deps(info, tool_names=tool_names, mcp_manager=mcp_manager)
        body = format_skill_content(info, warnings=warnings)
        return ToolResult.success(
            f"Loaded skill: {info.name}",
            body,
            name=info.name,
            dir=str(info.location.parent),
            warnings=warnings,
        )


def format_skill_content(info, *, warnings: Optional[list] = None) -> str:
    """Render SKILL.md plus base-dir notes for the skill tool or a pinned prompt."""
    base = str(info.location.parent)
    files_note = _sample_files(info.location.parent)
    parts = [
        f'<skill_content name="{info.name}">',
        f"# Skill: {info.name}",
        "",
        info.content.strip(),
        "",
        f"Base directory for this skill: {base}",
        "Relative paths in this skill (e.g., scripts/, reference/) are relative to this base directory.",
        "",
        "<skill_files>",
        files_note,
        "</skill_files>",
    ]
    if warnings:
        parts.append("")
        parts.append("<skill_warnings>")
        parts.extend(f"- {w}" for w in warnings)
        parts.append("</skill_warnings>")
    parts.append("</skill_content>")
    return "\n".join(parts)


def pinned_skill_system_block(name: str, session=None) -> str:
    """System-prompt block for a session-pinned skill (already loaded)."""
    return pinned_skills_system_block([name], session=session)


def pinned_skills_system_block(names: List[str], session=None) -> str:
    """System-prompt blocks for one or more session-pinned skills."""
    bodies: List[str] = []
    tool_names: list = []
    mcp_manager = None
    if session is not None:
        reg = getattr(session, "registry", None)
        if reg is not None:
            tool_names = list(reg.names())
        mcp_manager = getattr(session, "_mcp_manager", None)
    for name in names or []:
        info = get_skill(name)
        if info is None:
            continue
        warnings = check_skill_deps(info, tool_names=tool_names, mcp_manager=mcp_manager)
        bodies.append(format_skill_content(info, warnings=warnings))
    if not bodies:
        return ""
    if len(bodies) == 1:
        header = (
            "# Pinned skill (already loaded; do not call the skill tool for this name)\n"
            "The user selected this skill for the session. Follow its instructions.\n\n"
        )
    else:
        header = (
            "# Pinned skill (already loaded; do not call the skill tool for these names)\n"
            "The user selected these skills for the session. Follow their instructions. "
            "If they conflict, prefer the user's current request.\n\n"
        )
    return header + "\n\n".join(bodies)


def _sample_files(dir_path: Path, limit: int = 10) -> str:
    lines = []
    try:
        for p in sorted(dir_path.rglob("*")):
            if not p.is_file():
                continue
            if p.name == "SKILL.md":
                continue
            lines.append(f"<file>{p.resolve}</file>")
            if len(lines) >= limit:
                break
    except OSError:
        pass
    return "\n".join(lines) if lines else "(no extra files sampled)"
