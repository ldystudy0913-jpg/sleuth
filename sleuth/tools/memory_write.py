"""Write a user-scoped long-term memory after privacy checks."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from ..memory import settings as memory_settings
from ..memory.service import MemoryPrivacyError, MemoryUnavailable, write_user_memory
from .base import ToolContext, ToolResult


class MemoryWriteParams(BaseModel):
    item_key: str = Field(
        description=(
            "Stable catalog key as domain.aspect. Must be one of the configured "
            "item_keys (default: output.language, str.threshold, workflow.steps, ...). "
            "Pass the catalog key only; do not invent suffixes. Similar text updates "
            "the matching instance, a different meaning stores a new instance."
        )
    )
    title_text: str = Field(description="Short title, preferably under 40 characters.")
    body_text: str = Field(description="Already-generalized note. Never include raw ID or card numbers.")
    scenario_code: Optional[str] = Field(
        default=None,
        description="Scenario code from the configured scenario list. Defaults to the first entry.",
    )
    mem_kind: Optional[str] = Field(
        default=None,
        description="Memory kind from the configured kind list. Defaults to the first entry.",
    )


class MemoryWriteTool:
    name = "memory_write"
    description = (
        "Store a durable personal note for this user using an existing catalog item_key "
        "(domain.aspect such as output.language or str.threshold). "
        "Call this when the user states a stable preference or asks you to remember something. "
        "Pass the catalog key only; never invent a suffix. Similar wording updates that "
        "instance; a different meaning under the same catalog key is stored as another row. "
        "Do not store full conversations, attachments, or raw identity numbers."
    )
    params = MemoryWriteParams

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult:
        try:
            ctx.ask(self.name, ["*"], ["*"])
        except Exception as exc:
            return ToolResult.error(self.name, f"permission denied: {exc}")
        session = ctx.session
        config = getattr(session, "config", None) if session is not None else None
        if config is None:
            return ToolResult.error(self.name, "session config is unavailable")
        user_id = getattr(session, "user_id", None) or ""
        scenarios = memory_settings.scenarios(config)
        kinds = memory_settings.kinds(config)
        scenario = str(args.get("scenario_code") or (scenarios[0] if scenarios else "")).strip()
        kind = str(args.get("mem_kind") or (kinds[0] if kinds else "")).strip()
        try:
            item = write_user_memory(
                config,
                user_id,
                scenario_code=scenario,
                mem_kind=kind,
                item_key=str(args.get("item_key") or ""),
                title_text=str(args.get("title_text") or ""),
                body_text=str(args.get("body_text") or ""),
            )
        except MemoryPrivacyError as exc:
            return ToolResult.error(self.name, str(exc))
        except MemoryUnavailable as exc:
            return ToolResult.error(self.name, str(exc))
        except ValueError as exc:
            return ToolResult.error(self.name, str(exc))
        except Exception as exc:
            return ToolResult.error(self.name, f"{type(exc).__name__}: {exc}")
        return ToolResult.success(
            self.name,
            f"stored {item.id} {item.item_key}",
            id=item.id,
            item_key=item.item_key,
        )
