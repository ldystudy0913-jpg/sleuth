"""远程知识库检索客户端（POST question）。"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from ..config import Settings
from ..models import normalize_risk_query

logger = logging.getLogger(__name__)


class KbApiError(RuntimeError):
    """Remote KB HTTP / protocol failure."""


@dataclass
class SplitContent:
    type: str = ""
    content: str = ""
    id: str = ""
    url: str = ""

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SplitContent":
        return cls(
            type=str(d.get("type") or ""),
            content=str(d.get("content") or ""),
            id=str(d.get("id") or ""),
            url=str(d.get("url") or ""),
        )


@dataclass
class KbHit:
    id: str = ""
    title: str = ""
    paragraph: str = ""
    file_name: str = ""
    knowledge_id: str = ""
    paragraph_id: Any = None
    rank_score: float = 0.0
    comprehended: int = 0
    final_response: int = 0
    tool_url: str = ""
    url: str = ""
    source_name: str = ""
    title_path: List[str] = field(default_factory=list)
    tag_value_names: List[str] = field(default_factory=list)
    split_contents: List[SplitContent] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KbHit":
        splits_raw = d.get("splitContents") or []
        splits: List[SplitContent] = []
        if isinstance(splits_raw, list):
            for x in splits_raw:
                if isinstance(x, dict):
                    splits.append(SplitContent.from_dict(x))
        title_path = d.get("titlePath") or []
        if not isinstance(title_path, list):
            title_path = []
        tags = d.get("tagValueNames") or []
        if not isinstance(tags, list):
            tags = []
        try:
            score = float(d.get("rankScore") or 0)
        except (TypeError, ValueError):
            score = 0.0
        split_url = ""
        for sc in splits:
            if sc.url:
                split_url = sc.url
                break
        return cls(
            id=str(d.get("id") or ""),
            title=str(d.get("title") or ""),
            paragraph=str(d.get("paragraph") or ""),
            file_name=str(d.get("fileName") or d.get("file_name") or ""),
            knowledge_id=str(d.get("knowledgeId") or d.get("knowledge_id") or ""),
            paragraph_id=d.get("paragraphId"),
            rank_score=score,
            comprehended=int(d.get("comprehended") or 0),
            final_response=int(d.get("finalResponse") or 0),
            tool_url=str(d.get("toolUrl") or d.get("tool_url") or ""),
            url=str(
                d.get("url")
                or d.get("fileUrl")
                or d.get("file_url")
                or split_url
                or ""
            ),
            source_name=str(
                d.get("source")
                or d.get("sourceName")
                or d.get("origin")
                or d.get("docName")
                or d.get("documentName")
                or ""
            ),
            title_path=[str(x) for x in title_path],
            tag_value_names=[str(x) for x in tags],
            split_contents=splits,
            raw=d,
        )

    def source_url(self) -> str:
        return (self.tool_url or self.url or "").strip()

    def source_cite(self) -> str:
        """One-line citation for prompts and the 知识来源 section."""
        name = (
            self.file_name
            or self.source_name
            or self.title
            or self.id
            or "未命名知识条目"
        )
        bits = [name]
        if self.source_name and self.source_name != name:
            bits.append(self.source_name)
        if self.knowledge_id:
            bits.append(f"knowledgeId={self.knowledge_id}")
        if self.id:
            bits.append(f"id={self.id}")
        url = self.source_url()
        if url:
            bits.append(url)
        if self.title_path:
            bits.append("path=" + " > ".join(self.title_path))
        return "；".join(bits)

    def text_for_prompt(self, *, max_chars: int = 4000) -> str:
        parts: List[str] = []
        head = self.title or self.file_name or self.id or "hit"
        meta = f"score={self.rank_score:.4f} comprehended={self.comprehended} finalResponse={self.final_response}"
        parts.append(f"- [{head}] ({meta})")
        parts.append(f"  来源: {self.source_cite()}")
        if self.file_name:
            parts.append(f"  fileName: {self.file_name}")
        if self.title_path:
            parts.append(f"  titlePath: {' > '.join(self.title_path)}")
        body = (self.paragraph or "").strip()
        if not body and self.split_contents:
            texts = [
                sc.content.strip()
                for sc in self.split_contents
                if sc.content.strip() and sc.type in {"", "text", "title", "table", "image"}
            ]
            body = "\n".join(texts)
        if body:
            if len(body) > max_chars:
                body = body[: max_chars - 1] + "…"
            parts.append(f"  paragraph:\n{body}")
        return "\n".join(parts)


@dataclass
class RiskRetrieval:
    code: str
    question: str
    hits: List[KbHit] = field(default_factory=list)
    error: str = ""
    source: str = "remote"  # remote | local | fallback

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.hits)


def _auth_headers(settings: Settings) -> Dict[str, str]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    token = (settings.kb_api_token or "").strip()
    if not token:
        return headers
    name = settings.kb_api_auth_header or "Authorization"
    scheme = (settings.kb_api_auth_scheme or "").strip()
    if scheme:
        headers[name] = f"{scheme} {token}"
    else:
        headers[name] = token
    return headers


def _extra_body(settings: Settings) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    raw = (settings.kb_api_extra_body or "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise KbApiError(f"DD_REPLY_KB_API_EXTRA_BODY is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise KbApiError("DD_REPLY_KB_API_EXTRA_BODY must be a JSON object")
        out.update(parsed)
    kid = (settings.kb_knowledge_id or "").strip()
    if kid and "knowledgeId" not in out:
        out["knowledgeId"] = kid
    top_k = int(getattr(settings, "kb_top_k", 8) or 0)
    if top_k > 0 and "topK" not in out:
        out["topK"] = top_k
    return out


def search_knowledge(
    question: str,
    settings: Settings,
    *,
    client: Optional[httpx.Client] = None,
) -> List[KbHit]:
    """POST remote KB with required ``question`` (+ optional extra fields)."""
    q = (question or "").strip()
    if not q:
        raise KbApiError("question is required")
    if not settings.kb_api_configured():
        raise KbApiError("DD_REPLY_KB_API_URL is not configured")

    body: Dict[str, Any] = {"question": q}
    body.update(_extra_body(settings))
    headers = _auth_headers(settings)
    timeout = float(settings.kb_api_timeout or 30)

    owns_client = client is None
    http = client or httpx.Client(timeout=timeout)
    try:
        resp = http.post(settings.kb_api_url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise KbApiError(f"KB request failed: {exc}") from exc
    finally:
        if owns_client:
            http.close()

    if resp.status_code >= 400:
        raise KbApiError(
            f"KB HTTP {resp.status_code}: {(resp.text or '')[:500]}"
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise KbApiError("KB response is not JSON") from exc

    if not isinstance(data, dict):
        raise KbApiError("KB response root must be an object")

    code = str(data.get("returnCode") or "")
    if code and code != "SUC0000":
        raise KbApiError(
            f"KB returnCode={code} errorMsg={data.get('errorMsg')!r}"
        )

    body_list = data.get("body")
    if body_list is None:
        return []
    if not isinstance(body_list, list):
        raise KbApiError("KB body must be a list")

    hits: List[KbHit] = []
    for item in body_list:
        if isinstance(item, dict):
            hits.append(KbHit.from_dict(item))
    # Prefer higher score / final answers first
    hits.sort(
        key=lambda h: (h.final_response, h.comprehended, h.rank_score),
        reverse=True,
    )
    top_k = int(getattr(settings, "kb_top_k", 8) or 0)
    if top_k > 0:
        hits = hits[:top_k]
    return hits


def retrieve_risk_code(
    code: str,
    settings: Settings,
    *,
    client: Optional[httpx.Client] = None,
    question: Optional[str] = None,
) -> RiskRetrieval:
    """Search KB for one risk code or name (question = the query string)."""
    q = normalize_risk_query(question if question is not None else code)
    c = normalize_risk_query(code) or q
    try:
        hits = search_knowledge(q, settings, client=client)
    except KbApiError as exc:
        logger.warning("KB search failed for %s: %s", c, exc)
        return RiskRetrieval(code=c, question=q, hits=[], error=str(exc), source="remote")
    return RiskRetrieval(code=c, question=q, hits=hits, error="", source="remote")


def retrieve_risk_codes(
    codes: List[str],
    settings: Settings,
    *,
    client: Optional[httpx.Client] = None,
) -> List[RiskRetrieval]:
    """Batch retrieve; one HTTP call per distinct code or name."""
    seen: set[str] = set()
    ordered: List[str] = []
    for raw in codes:
        c = normalize_risk_query(raw)
        if not c or c in seen:
            continue
        seen.add(c)
        ordered.append(c)

    owns = client is None and settings.kb_api_configured()
    http = client
    if owns:
        http = httpx.Client(timeout=float(settings.kb_api_timeout or 30))
    try:
        return [
            retrieve_risk_code(c, settings, client=http) for c in ordered
        ]
    finally:
        if owns and http is not None:
            http.close()
