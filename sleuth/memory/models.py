"""Dataclasses for directory rows and memory items."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class UserRecord:
    user_id: str
    display_name: Optional[str] = None
    role_id: Optional[str] = None
    org_id: Optional[str] = None
    row_status: str = ""


@dataclass
class OrgRecord:
    org_id: str
    parent_id: Optional[str] = None
    org_category: str = ""
    org_name: str = ""
    row_status: str = ""


@dataclass
class RoleRecord:
    role_id: str
    role_name: str = ""
    scenario_list: Optional[str] = None
    row_status: str = ""


@dataclass
class GrantRecord:
    grant_id: str
    scope_kind: str
    scope_id: str
    resource_kind: str
    resource_id: str
    grant_effect: str
    row_status: str = ""


@dataclass
class MemoryItem:
    id: str
    scope_kind: str
    scope_id: str
    scenario_code: str
    mem_kind: str
    item_key: str
    title_text: str
    body_text: str
    payload_text: Optional[str] = None
    embedding: Optional[List[float]] = None
    importance_score: int = 3
    confidence_score: str = "1.0000"
    origin_type: str = ""
    row_status: str = ""
    expire_at: Optional[datetime] = None
    created_by: str = ""
    updated_by: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    use_cnt: int = 0
    score: Optional[float] = None

    def embed_text(self) -> str:
        title = (self.title_text or "").strip()
        body = (self.body_text or "").strip()
        if title and body:
            return title + "\n" + body
        return title or body

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "scenario_code": self.scenario_code,
            "mem_kind": self.mem_kind,
            "item_key": self.item_key,
            "title_text": self.title_text,
            "body_text": self.body_text,
            "payload_text": self.payload_text,
            "importance_score": self.importance_score,
            "confidence_score": self.confidence_score,
            "origin_type": self.origin_type,
            "row_status": self.row_status,
            "expire_at": self.expire_at.isoformat() if self.expire_at else None,
            "use_cnt": self.use_cnt,
            "score": self.score,
        }
