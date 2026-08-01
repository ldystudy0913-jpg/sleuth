"""Message and content-block data model.

opencode stores a conversation as a flat list of messages, each carrying an
ordered list of content parts (text, reasoning, tool-use, tool-result, ...).
The on-the-wire shape is the OpenAI Chat Completions content format, which is
also what we use internally as the canonical representation. The OpenAI
provider maps this to/from its wire format at the boundary
(see provider/openai_provider.py).

Modelled block types (a deliberate subset of opencode's part union):
  - TextBlock         plain assistant/user text
  - ReasoningBlock    the model's "thinking" (rendered dim/gray; not sent
                       back to the model on OpenAI-compatible unless the
                       model is reasoning-capable)
  - ToolUseBlock      the model requesting a tool call
  - ToolResultBlock   the tool's output fed back to the model
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Union


@dataclass
class TextBlock:
    """A plain text span."""

    text: str
    type: str = "text"


@dataclass
class ReasoningBlock:
    """The model's chain-of-thought / thinking.

    Mirrors opencode's `ReasoningPart` / `AssistantReasoning`. It is NOT sent
    back to the provider on the OpenAI-compatible path by default (it is for
    display only), matching opencode's treatment of non-interleaved models.
    """

    text: str
    type: str = "reasoning"


@dataclass
class ToolUseBlock:
    """The model emitting a tool call.

    `id` is the tool-use id the model produced (or one we synthesise); it is
    echoed back on the matching ToolResultBlock so the provider can pair them.
    `input` is the parsed argument object.
    """

    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class ToolResultBlock:
    """The result of executing a ToolUseBlock, returned to the model."""

    tool_use_id: str
    content: str
    is_error: bool = False
    # multimodal attachments (images/PDF) — port of opencode tool attachments
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    type: str = "tool_result"


@dataclass
class FileBlock:
    """A multimodal file/image part (data URL), for provider wire format."""

    mime: str
    url: str  # data:...;base64,... or https://
    type: str = "file"


ContentBlock = Union[TextBlock, ReasoningBlock, ToolUseBlock, ToolResultBlock, FileBlock]


@dataclass
class Message:
    """A single turn. role is "user" or "assistant".

    Tool results are stored as a *user* message whose content is a list of
    ToolResultBlocks — this keeps the alternation invariant
    (user/assistant/user/assistant) intact, matching the OpenAI "tool" role.
    """

    role: str
    content: List[ContentBlock] = field(default_factory=list)
    # opaque metadata: created time, model, cost, tokens, agent, snapshots...
    metadata: dict = field(default_factory=dict)

    # ---- convenience constructors ----

    @classmethod
    def user_text(cls, text: str, **metadata: Any) -> "Message":
        return cls(role="user", content=[TextBlock(text=text)], metadata=dict(metadata))

    @classmethod
    def assistant(cls, blocks: List[ContentBlock], **metadata: Any) -> "Message":
        return cls(role="assistant", content=list(blocks), metadata=dict(metadata))

    @classmethod
    def tool_results(cls, results: List[ToolResultBlock], **metadata: Any) -> "Message":
        return cls(role="user", content=list(results), metadata=dict(metadata))

    # ---- inspection ----

    @property
    def text(self) -> str:
        """Concatenated text of all TextBlocks (handy for display/logging)."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def reasoning(self) -> str:
        """Concatenated reasoning text."""
        return "".join(b.text for b in self.content if isinstance(b, ReasoningBlock))

    @property
    def tool_uses(self) -> List[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    def has_tool_use(self) -> bool:
        return any(isinstance(b, ToolUseBlock) for b in self.content)


# ---------------------------------------------------------------------------
# (de)serialisation — used by the SQLite persistence layer (storage/sqlite.py).
# Parts are stored as JSON blobs with an inner `type` discriminator, exactly
# like opencode's `part.data` column.
# ---------------------------------------------------------------------------


def block_to_dict(block: ContentBlock) -> Dict[str, Any]:
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ReasoningBlock):
        return {"type": "reasoning", "text": block.text}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": block.is_error,
            "attachments": list(block.attachments or []),
        }
    if isinstance(block, FileBlock):
        return {"type": "file", "mime": block.mime, "url": block.url}
    raise TypeError(f"unknown block type: {type(block).__name__}")


def block_from_dict(d: Dict[str, Any]) -> ContentBlock:
    t = d.get("type")
    if t == "text":
        return TextBlock(text=d.get("text", ""))
    if t == "reasoning":
        return ReasoningBlock(text=d.get("text", ""))
    if t == "tool_use":
        return ToolUseBlock(id=d.get("id", ""), name=d.get("name", ""), input=d.get("input") or {})
    if t == "tool_result":
        return ToolResultBlock(
            tool_use_id=d.get("tool_use_id", ""),
            content=d.get("content", ""),
            is_error=bool(d.get("is_error", False)),
            attachments=list(d.get("attachments") or []),
        )
    if t == "file":
        return FileBlock(mime=d.get("mime", "application/octet-stream"), url=d.get("url", ""))
    # Unknown part type from a newer schema — degrade to text so old stores
    # still load. Never raise during hydration.
    return TextBlock(text=str(d))


def message_to_dict(msg: Message) -> Dict[str, Any]:
    return {
        "role": msg.role,
        "content": [block_to_dict(b) for b in msg.content],
        "metadata": msg.metadata,
    }


def message_from_dict(d: Dict[str, Any]) -> Message:
    content = [block_from_dict(c) for c in d.get("content", [])]
    return Message(role=d.get("role", "user"), content=content, metadata=dict(d.get("metadata") or {}))


def merge_text(messages: List[Message]) -> str:
    """Flatten all TextBlocks across messages into one string."""
    return "".join(m.text for m in messages)
