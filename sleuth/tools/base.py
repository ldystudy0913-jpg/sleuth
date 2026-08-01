"""Tool base types: Tool protocol, ToolResult, ToolContext."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from ..permission import Permission, PermissionDenied


@dataclass
class ToolResult:
    """What a tool returns.

    `output` is the text fed back into the model's context. `title` is a short
    label for the UI. `metadata` is structured info (exit code, counts, ...)
    the UI may render. `attachments` holds multimodal payloads (images/PDF)
    mirroring opencode's ExecuteResult.attachments.
    """

    title: str
    output: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    attachments: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def error(cls, title: str, message: str, **metadata: Any) -> "ToolResult":
        attachments = list(metadata.pop("attachments", []) or [])
        return cls(
            title=title, output=message, is_error=True,
            metadata=dict(metadata), attachments=attachments,
        )

    @classmethod
    def success(cls, title: str, output: str, **metadata: Any) -> "ToolResult":
        attachments = list(metadata.pop("attachments", []) or [])
        return cls(
            title=title, output=output, metadata=dict(metadata),
            attachments=attachments,
        )


# A question callback: (questions) -> answers (list[list[str]]). Blocks for
# human input, like opencode's Question.ask() Deferred. Mirrors the
# permission ask_fn pattern.
QuestionFn = Callable[[List[dict]], List[List[str]]]


def _console_question(questions: List[dict]) -> List[List[str]]:
    """Default console prompt for the question tool."""
    answers: List[List[str]] = []
    for q in questions:
        print(f"\n? {q.get('header', 'question')}: {q['question']}")
        opts = q.get("options", [])
        for i, o in enumerate(opts, start=1):
            print(f"  [{i}] {o['label']}" + (f" — {o['description']}" if o.get("description") else ""))
        try:
            raw = input("answer (number/label, or free text) > ").strip()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        # match option by number or label substring; else treat as free text
        chosen: List[str] = []
        if raw:
            for part in raw.split(","):
                part = part.strip()
                matched = None
                if part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < len(opts):
                        matched = opts[idx]["label"]
                if matched is None:
                    for o in opts:
                        if o["label"] == part:
                            matched = part
                            break
                chosen.append(matched or part)
        answers.append(chosen)
    return answers


@dataclass
class ToolContext:
    """Per-invocation state passed to every tool.execute.

    Carries the workdir (so file tools resolve relative paths), the session
    id/message id for logging, the permission gate, a human-question channel,
    and an abort flag the session loop can set to cancel a long-running tool.
    """

    workdir: Path
    session_id: str = ""
    message_id: str = ""
    agent: str = "build"
    permission: Permission = field(default_factory=Permission)
    ask_question: QuestionFn = field(default=_console_question)
    # set by Session.cancel(); tools may inspect it to short-circuit
    abort: Any = None
    # live Session for nested tools (task); optional
    session: Any = None

    def is_aborted(self) -> bool:
        flag = self.abort
        return bool(flag and getattr(flag, "is_set", lambda: False)())

    def ask(self, tool: str, patterns: List[str], always: Optional[List[str]] = None) -> None:
        """Permission gate using opencode's pattern model. Raises on deny."""
        self.permission.ask(tool, patterns, always or [])

    def ask_simple(self, tool: str, detail: str = "") -> None:
        """Back-compat for the single-string detail form."""
        try:
            self.permission.check(tool, detail)
        except PermissionDenied:
            raise


@runtime_checkable
class Tool(Protocol):
    """A single tool the agent can call.

    `name`    — identifier sent to the model (also the permission key).
    `description` — prose the model sees when choosing tools.
    `params`  — a pydantic model class; we validate args against it and
                generate the JSON schema from it.
    `execute(args, ctx)` — run the tool; returns a ToolResult.
    """

    name: str
    description: str
    params: type

    def execute(self, args: dict, ctx: ToolContext) -> ToolResult: ...


def to_provider_spec(tool: Tool) -> Dict[str, Any]:
    """Build the model-facing tool spec from a Tool.

    Produces a neutral dict both providers understand:
        {name, description, parameters_json_schema}
    """
    schema: Dict[str, Any] = {}
    try:
        schema = tool.params.model_json_schema()
    except Exception:  # pragma: no cover - schema generation should not fail
        schema = {"type": "object", "properties": {}}
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters_json_schema": schema,
    }


def validate_args(tool: Tool, raw: dict) -> tuple[Optional[dict], Optional[str]]:
    """Validate raw args against the tool's pydantic model.

    Returns (parsed, None) on success or (None, error_message).
    """
    try:
        instance = tool.params.model_validate(raw)
        return instance.model_dump(exclude_none=False), None
    except Exception as exc:
        return None, str(exc)
