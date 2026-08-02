"""Bash tool — run a shell command in the project workdir."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from ..guardrails import bash_command_blocked
from ..util.truncate import Limits, truncate
from .base import Tool, ToolContext, ToolResult


class BashParams(BaseModel):
    command: str = Field(description="The shell command to execute.")
    timeout: Optional[int] = Field(
        default=None, description="Optional timeout in milliseconds."
    )
    workdir: Optional[str] = Field(
        default=None,
        description="Working directory to run the command in. Defaults to the project dir.",
    )


class BashTool:
    name = "bash"
    description = (
        "Execute a shell command. Runs in the project's working directory by "
        "default. Output (stdout+stderr combined) is returned and truncated "
        "to a reasonable size. Use this for running builds, tests, git, and "
        "other CLI tasks. Prefer to explain what the command does before "
        "running it when it makes changes to the user's system."
    )
    params = BashParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        p = BashParams(**args)
        workdir = p.workdir or str(ctx.workdir)
        blocked = bash_command_blocked(
            p.command,
            workdir=ctx.workdir,
            cwd=Path(workdir) if workdir else ctx.workdir,
            enabled=ctx.guardrails_enabled,
        )
        if blocked:
            return ToolResult.error("bash", blocked)

        # Permission pattern: full command + "<first-token> *" for always-allow.
        first_token = (p.command.strip().split()[:1] or [""])[0]
        always = [first_token + " *"] if first_token else []
        ctx.ask("bash", patterns=[p.command], always=always)

        timeout = (p.timeout / 1000.0) if p.timeout else None
        env = os.environ.copy()
        # ensure non-interactive, stable locale
        env.setdefault("CI", "1")

        try:
            proc = subprocess.run(
                p.command,
                shell=True,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.error("bash", f"command timed out after {timeout}s", exit=-1)
        except Exception as exc:
            return ToolResult.error("bash", f"failed to run command: {exc}", exit=-1)

        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            out = out + f"\n[exit code {proc.returncode}]"
        out, _trunc = truncate(out, Limits())

        title = p.command.strip().splitlines()[0][:60] if p.command.strip() else "bash"
        return ToolResult(
            title=title,
            output=out,
            metadata={"exit": proc.returncode},
            is_error=proc.returncode != 0,
        )
