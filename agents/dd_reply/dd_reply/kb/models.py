from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 请求配置数据类
# ---------------------------------------------------------------------------

@dataclass
class SortConfig:
    """排序配置（选填，不传以服务配置为准）"""
    sort_count: Optional[int] = None
    sort_score: Optional[float] = None
    time_combine: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if self.sort_count is not None:
            result["sortCount"] = self.sort_count
        if self.sort_score is not None:
            result["sortScore"] = self.sort_score
        if self.time_combine is not None:
            result["timeCombine"] = self.time_combine
        return result


@dataclass
class TimeFilterConfig:
    """时间过滤配置（选填，不使用该功能传null）"""
    time_filter_enable: bool = False
    by_day: Optional[int] = None
    by_time_start_time: Optional[str] = None
    by_time_end_time: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        result["timeFilterEnable"] = self.time_filter_enable
        if self.by_day is not None:
            result["byDay"] = self.by_day
        if self.by_time_start_time is not None:
            result["byTimeStartTime"] = self.by_time_start_time
        if self.by_time_end_time is not None:
            result["byTimeEndTime"] = self.by_time_end_time
        return result


@dataclass
class RecallConfig:
    """召回配置（选填，不传以服务配置为准）"""
    knowledge_id: str = ""
    recall_count: Optional[int] = None
    atom_ids: Optional[List[str]] = None
    node_ids: Optional[List[str]] = None
    html_clear: Optional[bool] = None
    qa_search_mode: Optional[str] = None
    time_filter_config: Optional[TimeFilterConfig] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if self.knowledge_id:
            result["knowledgeId"] = self.knowledge_id
        if self.recall_count is not None:
            result["recallCount"] = self.recall_count
        if self.atom_ids is not None:
            result["atomIds"] = self.atom_ids
        if self.node_ids is not None:
            result["nodeIds"] = self.node_ids
        if self.html_clear is not None:
            result["htmlClear"] = self.html_clear
        if self.qa_search_mode is not None:
            result["qaSearchMode"] = self.qa_search_mode
        if self.time_filter_config is not None:
            result["timeFilterConfig"] = self.time_filter_config.to_dict()
        return result
@dataclass
class TagConfig:
    """标签配置（选填，除标签检索外不需要传该字段）"""
    tag_name: str = ""
    tag_value_ids: Optional[List[str]] = None
    tag_value_names: Optional[List[str]] = None
    tag_search_operation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if self.tag_name:
            result["tagName"] = self.tag_name
        if self.tag_value_ids is not None:
            result["tagValueIds"] = self.tag_value_ids
        if self.tag_value_names is not None:
            result["tagValueNames"] = self.tag_value_names
        if self.tag_search_operation:
            result["tagSearchOperation"] = self.tag_search_operation
        return result


@dataclass
class ServiceConfig:
    """服务配置（选填，不传以服务配置为准）"""
    sort_config: Optional[SortConfig] = None
    recall_config: Optional[List[RecallConfig]] = None
    tag_config: Optional[List[TagConfig]] = None
    subnet_type: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        if self.sort_config is not None:
            sort_dict = self.sort_config.to_dict()
            if sort_dict:
                result["sortConfig"] = sort_dict
        if self.recall_config is not None:
            configs = [rc.to_dict() for rc in self.recall_config]
            if configs:
                result["recallConfig"] = configs
        if self.tag_config is not None:
            configs = [tc.to_dict() for tc in self.tag_config]
            if configs:
                result["tagConfig"] = configs
        if self.subnet_type is not None:
            result["subnetType"] = self.subnet_type
        return result
@dataclass
class SearchRequest:
    """搜索请求"""
    question: str = ""
    service_config: Optional[ServiceConfig] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"question": self.question}
        if self.service_config is not None:
            sc_dict = self.service_config.to_dict()
            if sc_dict:
                result["serviceConfig"] = sc_dict
        return result


# ---------------------------------------------------------------------------
# 响应数据类
# ---------------------------------------------------------------------------

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
    """知识库检索结果命中项。

    字段命名使用下划线（Python惯例），to_dict/from_dict 时自动转换为驼峰命名。
    所有字段都有默认值，确保响应方新增字段时不会报错。
    """
    # 核心字段
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
    # 路径/标签字段
    title_path: List[str] = field(default_factory=list)
    tag_value_names: List[str] = field(default_factory=list)
    tag_value_ids: List[str] = field(default_factory=list)
    # 内容分段
    split_contents: List[SplitContent] = field(default_factory=list)
    # 扩展字段（来自API文档）
    kplus_knowledge_id: str = ""
    update_time: int = 0
    node_id_path: List[str] = field(default_factory=list)
    node_name_path: List[str] = field(default_factory=list)
    dmz_url: str = ""
    resp_flag: str = ""
    type: str = ""
    img_ids: List[str] = field(default_factory=list)
    img_url_list: List[str] = field(default_factory=list)
    # 原始响应（保留完整数据）
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "KbHit":
        """从API响应字典构建KbHit实例。

        安全处理所有字段：
        - 所有字段都有默认值，响应方新增字段不会报错
        - 类型转换使用 try/except 保护
        - 列表字段验证类型
        """
        splits_raw = d.get("splitContents") or []
        splits: List[SplitContent] = []
        if isinstance(splits_raw, list):
            for x in splits_raw:
                if isinstance(x, dict):
                    splits.append(SplitContent.from_dict(x))

        # 通用列表字段处理
        def _to_list(v: Any) -> List[str]:
            if isinstance(v, list):
                return [str(x) for x in v]
            return []

        # 安全类型转换
        def _to_str(v: Any, default: str = "") -> str:
            if v is None:
                return default
            return str(v)

        def _to_int(v: Any, default: int = 0) -> int:
            if v is None:
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        def _to_float(v: Any, default: float = 0.0) -> float:
            if v is None:
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default
        return cls(
            id=_to_str(d.get("id")),
            title=_to_str(d.get("title")),
            paragraph=_to_str(d.get("paragraph")),
            file_name=_to_str(d.get("fileName")),
            knowledge_id=_to_str(d.get("knowledgeId")),
            paragraph_id=d.get("paragraphId"),
            rank_score=_to_float(d.get("rankScore")),
            comprehended=_to_int(d.get("comprehended")),
            final_response=_to_int(d.get("finalResponse")),
            tool_url=_to_str(d.get("toolUrl")),
            title_path=_to_list(d.get("titlePath")),
            tag_value_names=_to_list(d.get("tagValueNames")),
            tag_value_ids=_to_list(d.get("tagValueIds")),
            split_contents=splits,
            kplus_knowledge_id=_to_str(d.get("kplusKnowledgeId")),
            update_time=_to_int(d.get("updateTime")),
            node_id_path=_to_list(d.get("nodeIdPath")),
            node_name_path=_to_list(d.get("nodeNamePath")),
            dmz_url=_to_str(d.get("dmzUrl")),
            resp_flag=_to_str(d.get("respFlag")),
            type=_to_str(d.get("type")),
            img_ids=_to_list(d.get("imgIds")),
            img_url_list=_to_list(d.get("imgUrlList")),
            raw=d,
        )

    def source_url(self) -> str:
        """Prefer dmzUrl, then fileUrl / toolUrl / first splitContents.url."""
        if (self.dmz_url or "").strip():
            return self.dmz_url.strip()
        raw = self.raw or {}
        for key in ("fileUrl", "file_url", "url"):
            val = str(raw.get(key) or "").strip()
            if val:
                return val
        if (self.tool_url or "").strip():
            return self.tool_url.strip()
        for sc in self.split_contents:
            val = (sc.url or "").strip()
            if val:
                return val
        return ""

    def source_cite(self) -> str:
        """One-line citation for prompts and the 知识来源 section."""
        name = self.file_name
        bits = [name]
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

# ---------------------------------------------------------------------------
# 检索结果数据类
# ---------------------------------------------------------------------------

@dataclass
class RiskRetrieval:
    """单次风险代码检索结果。"""
    code: str
    question: str
    hits: List[KbHit] = field(default_factory=list)
    error: str = ""
    source: str = "remote"  # remote | local | fallback

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.hits)