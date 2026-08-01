"""Store protocol + session record.

Mirrors the opencode table set (packages/core/src/session/sql.ts):
  session   metadata + totals
  message   one row per message (data JSON blob)
  part      one row per content block (data JSON blob, polymorphic via `type`)
  todo      per-session todo list
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from ..messages import Message


@dataclass
class SessionRecord:
    """The session metadata row (opencode `session` table)."""

    id: str
    directory: str
    title: str
    agent: str = "build"
    model: Optional[Dict[str, Any]] = None
    cost: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    permission: Optional[List[dict]] = None
    time_created: int = 0
    time_updated: int = 0


@runtime_checkable
class Store(Protocol):
    """Persistence surface the Session uses."""

    def create_session(self, rec: SessionRecord) -> None: ...
    def update_session(self, rec: SessionRecord) -> None: ...
    def get_session(self, session_id: str) -> Optional[SessionRecord]: ...
    def list_sessions(self, directory: Optional[str] = None, limit: int = 50) -> List[SessionRecord]: ...

    def save_message(self, session_id: str, message: Message) -> None: ...
    def load_messages(self, session_id: str) -> List[Message]: ...

    def save_todos(self, session_id: str, todos: List[dict]) -> None: ...
    def load_todos(self, session_id: str) -> List[dict]: ...

    def delete_session(self, session_id: str) -> None: ...
