"""System prompt assembly.

Loads the bundled prompt templates and stitches them together:
  base prompt (default or plan) + environment block + extra instructions
  + optional product disclosure guardrails and public catalogs.
"""
from __future__ import annotations

import datetime
import platform
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from ..config import Config

_PROMPT_DIR = Path(__file__).resolve().parent


def _load(name: str) -> str:
    p = _PROMPT_DIR / name
    try:
        return p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def environment(workdir: Path, model: str) -> str:
    today = datetime.date.today().isoformat()
    is_git = (workdir / ".git").is_dir()
    return (
        "Here is some useful information about the environment you are running in:\n"
        "<env>\n"
        f"  Working directory: {workdir}\n"
        f"  Is directory a git repo: {'yes' if is_git else 'no'}\n"
        f"  Platform: {platform.system()} {platform.machine()}\n"
        f"  Python: {sys.version.split()[0]}\n"
        f"  Today's date: {today}\n"
        f"  Model: {model}\n"
        "</env>"
    )


def assemble(
    *,
    workdir: Path,
    config: Config,
    agent_name: str,
    model: str,
    tool_specs: Optional[Sequence[dict]] = None,
    guardrails: Optional[bool] = None,
) -> str:
    """Build the full system prompt for a turn."""
    from ..instruction import (
        discover_paths,
        inline_instruction_lines,
        load_instruction_texts,
    )

    agent = config.agent(agent_name)
    use_guardrails = config.guardrails if guardrails is None else bool(guardrails)

    if agent_name == "plan":
        base = _load("plan.txt")
    elif agent.prompt and agent_name not in ("build", "plan"):
        # custom / subagent with its own prompt replaces the default base
        base = ""
    else:
        base = _load("default.txt")

    parts: List[str] = []
    if agent.prompt:
        parts.append(agent.prompt)
    if base:
        parts.append(base)
    parts.append(environment(workdir, model))

    # AGENTS.md / CLAUDE.md / remote instructions
    file_paths = discover_paths(workdir, config)
    parts.extend(load_instruction_texts(workdir, config))
    inline = inline_instruction_lines(config, file_paths)
    if inline:
        parts.append("\n".join(inline))

    if use_guardrails:
        from ..guardrails import (
            disclosure_policy_block,
            public_skills_block,
            public_tools_block,
        )

        parts.append(disclosure_policy_block())
        parts.append(public_tools_block(list(tool_specs or [])))
        parts.append(public_skills_block())

    parts.append(
        "\n".join(
            [
                "# Reminders",
                "- Reference code as `file_path:line_number`.",
                "- Prefer editing existing files over creating new ones.",
                "- Keep responses short; answer in 1-3 sentences unless detail is requested.",
                "- For AML conclusions, cite evidence; do not invent regulatory citations.",
                "- Treat customer/transaction data as sensitive; avoid unnecessary PII in output.",
                "- Never emit full ID numbers, mobile numbers, bank cards, passwords, or exact home addresses; use masked forms.",
            ]
        )
    )
    return "\n\n".join(p for p in parts if p.strip())
