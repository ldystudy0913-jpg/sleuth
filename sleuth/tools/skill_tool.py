"""skill tool — load an external SKILL.md into the conversation ."""
from __future__ import annotations

from pathlib import Path

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

        return ToolResult.success(
            f"Loaded skill: {info.name}",
            "\n".join(parts),
            name=info.name,
            dir=base,
            warnings=warnings,
        )


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
