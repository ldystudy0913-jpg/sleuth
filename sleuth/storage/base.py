"""Store protocol + session record."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from ..messages import Message


@dataclass
class SessionRecord:
    id: str
    directory: str
    title: str
    agent: str = "build"
    user_id: str = "local"
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


@dataclass
class UsageEvent:
    user_id: str
    session_id: str
    message_id: str
    model: str = ""
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    tokens_cache_read: int = 0
    tokens_cache_write: int = 0
    cost: float = 0.0
    time_created: int = 0


@runtime_checkable
class Store(Protocol):
    def create_session(self, rec: SessionRecord) -> None: ...
    def update_session(self, rec: SessionRecord) -> None: ...
    def get_session(self, session_id: str) -> Optional[SessionRecord]: ...
    def list_sessions(
        self,
        directory: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[SessionRecord]: ...

    def save_message(self, session_id: str, message: Message) -> None: ...
    def load_messages(self, session_id: str) -> List[Message]: ...
    def replace_messages(self, session_id: str, messages: List[Message]) -> None: ...

    def save_todos(self, session_id: str, todos: List[dict]) -> None: ...
    def load_todos(self, session_id: str) -> List[dict]: ...

    def save_usage_event(self, event: UsageEvent) -> None: ...
    def sum_usage(self, user_id: str) -> Dict[str, Any]: ...

    def delete_session(self, session_id: str) -> None: ...
