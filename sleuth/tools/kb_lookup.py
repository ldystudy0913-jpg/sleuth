"""Remote knowledge lookup for the default (build) agent."""
from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel, Field

from ..kb import KbError, kb_config, search_knowledge
from .base import ToolContext, ToolResult


class KbLookupParams(BaseModel):
    question: str = Field(
        description="Search question: a risk code (C001), a risk name, or free-text."
    )
    questions_json: Optional[str] = Field(
        default=None,
        description='Optional JSON array of extra questions, e.g. ["C001","行政处罚记录"].',
    )


class KbLookupTool:
    name = "kb_lookup"
    description = (
        "Search the configured remote knowledge base "
        "(SLEUTH_KB_API_URL + login Cookie ragToken). "
        "Use this on the default agent when the user asks to look up risk-point "
        "knowledge, regulations, or similar materials. Returns titles, excerpts, "
        "and source URLs. Does not download session attachments."
    )
    params = KbLookupParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        session = ctx.session
        config = getattr(session, "config", None) if session is not None else None
        if config is None:
            return ToolResult.error(self.name, "session config is unavailable")
        if not kb_config(config).configured():
            return ToolResult.error(
                self.name,
                "SLEUTH_KB_API_URL / SLEUTH_KB_LOGIN_URL / SLEUTH_KB_OPENID / "
                "SLEUTH_KB_SERVICEID are not configured; set them in .env so the "
                "default agent can search the knowledge base",
            )
        questions: list[str] = []
        q0 = str(args.get("question") or "").strip()
        if q0:
            questions.append(q0)
        extra = args.get("questions_json")
        if extra:
            try:
                parsed = json.loads(extra) if isinstance(extra, str) else extra
            except json.JSONDecodeError:
                parsed = [p.strip() for p in str(extra).split(",") if p.strip()]
            if isinstance(parsed, list):
                questions.extend(str(x).strip() for x in parsed if str(x).strip())
            elif parsed:
                questions.append(str(parsed).strip())
        seen: set[str] = set()
        ordered: list[str] = []
        for q in questions:
            if q and q not in seen:
                seen.add(q)
                ordered.append(q)
        if not ordered:
            return ToolResult.error(self.name, "question is required")
        found = []
        missing = []
        for q in ordered:
            try:
                hits = search_knowledge(q, config)
            except KbError as exc:
                missing.append({"question": q, "error": str(exc)})
                continue
            if hits:
                found.append({"question": q, "hit_count": len(hits), "hits": hits})
            else:
                missing.append({"question": q, "error": "empty_hits"})
        return ToolResult.success(
            self.name,
            json.dumps({"found": found, "missing": missing}, ensure_ascii=False),
        )
