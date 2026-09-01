"""Business logic. MCP handlers call this; do not put side effects in mcp_server.py.

TODO: replace `ping` with your real workflow (API calls, scoring, generation, …).
Keep secrets in environment variables (__ENV_PREFIX___*), never in Skill / Card text.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def ping(
    message: str,
    *,
    attachment_refs: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    refs = [r for r in (attachment_refs or []) if isinstance(r, dict)]
    excerpts = [str(r.get("excerpt") or "").strip() for r in refs]
    excerpts = [e for e in excerpts if e]
    return {
        "ok": True,
        "echo": message or "pong",
        "attachment_count": len(refs),
        "excerpt_count": len(excerpts),
        # Sleuth harvests top-level sources[] (title + http(s) url) onto the reply.
        # Leave empty until you have real citations. Do not use data:/file: URLs.
        "sources": [],
    }
