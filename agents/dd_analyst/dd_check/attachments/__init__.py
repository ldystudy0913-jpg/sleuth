"""附件流水线：元数据查询 → 对象下载 → SM4 解密 → 文本摘要。

设计要点：不落盘到项目目录；超限/缺配置写入 skipped，不拖垮整次检查。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Protocol

from ..config import Settings
from .crypto_sm4 import Sm4CbcError, sm4_cbc_decrypt


@dataclass
class AttachmentMeta:
    """MySQL ddp_file 一行摘要。"""

    file_id: str
    location_path: str
    size: Optional[int] = None
    mime: str = ""


@dataclass
class AttachmentExcerpt:
    """解密后截取的可读文本（给规则用）。"""

    file_id: str
    location_path: str
    text: str
    truncated: bool = False


@dataclass
class AttachmentBundle:
    """一次 investId 拉附件的结果：成功摘要 + 跳过原因。"""

    excerpts: List[AttachmentExcerpt] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


class FileMetaStore(Protocol):
    def list_by_invest_id(self, invest_id: str) -> List[AttachmentMeta]:
        ...


class ObjectStore(Protocol):
    def open_stream(self, location_path: str) -> Iterable[bytes]:
        """按块产出对象体（避免一次读入过大文件的语义约定）。"""
        ...


class NullMetaStore:
    """未配 MySQL 时的空实现。"""

    def list_by_invest_id(self, invest_id: str) -> List[AttachmentMeta]:
        return []


class NullObjectStore:
    """未配 COS 时的空实现。"""

    def open_stream(self, location_path: str) -> Iterable[bytes]:
        if False:  # pragma: no cover
            yield b""
        return iter(())


def _decode_text_chunked(data: bytes, max_chars: int) -> tuple[str, bool]:
    """明文字节 → 文本；utf-8 / gbk / latin-1 依次尝试，并按 max_chars 截断。"""
    text = None
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = ""
    truncated = len(text) > max_chars
    return text[:max_chars], truncated


class AttachmentPipeline:
    """附件主流程：list → download → SM4 CBC decrypt → excerpt。"""

    def __init__(
        self,
        settings: Settings,
        meta_store: Optional[FileMetaStore] = None,
        object_store: Optional[ObjectStore] = None,
    ):
        self.settings = settings
        self.meta_store = meta_store or NullMetaStore()
        self.object_store = object_store or NullObjectStore()

    def available(self) -> bool:
        """是否具备解密密钥（无密钥则 run 直接 skip）。"""
        return bool(self.settings.ecs_emode_b_key)

    def run(self, invest_id: str) -> AttachmentBundle:
        """按调查单号拉附件；缺 investId / 密钥 / 行记录时只记 skipped。"""
        bundle = AttachmentBundle()
        invest_id = (invest_id or "").strip()
        if not invest_id:
            bundle.skipped.append("investId empty: attachment pipeline skipped")
            return bundle
        if not self.settings.ecs_emode_b_key:
            bundle.skipped.append("DD_CHECK_ECS_EMODE_B_KEY not configured: cannot decrypt")
            return bundle

        metas = self.meta_store.list_by_invest_id(invest_id)
        if not metas:
            bundle.skipped.append(f"no ddp_file rows for investId={invest_id}")
            return bundle

        max_files = self.settings.attachment_max_files
        max_bytes = self.settings.attachment_max_bytes
        max_chars = self.settings.attachment_excerpt_max_chars

        for i, meta in enumerate(metas):
            if i >= max_files:
                bundle.skipped.append(f"file cap reached ({max_files}), skipped remaining")
                break
            try:
                excerpt = self._process_one(meta, max_bytes=max_bytes, max_chars=max_chars)
                bundle.excerpts.append(excerpt)
            except Exception as exc:
                bundle.skipped.append(f"{meta.file_id or meta.location_path}: {exc}")
        return bundle

    def _process_one(self, meta: AttachmentMeta, *, max_bytes: int, max_chars: int) -> AttachmentExcerpt:
        """单文件：流式拼接到上限 → 解密 → 截取文本 → 尽快丢弃明文。"""
        buf = bytearray()
        for chunk in self.object_store.open_stream(meta.location_path):
            if not chunk:
                continue
            if len(buf) + len(chunk) > max_bytes:
                raise Sm4CbcError(
                    f"attachment exceeds DD_CHECK_ATTACHMENT_MAX_BYTES={max_bytes}"
                )
            buf.extend(chunk)
        cipher = bytes(buf)
        plain = sm4_cbc_decrypt(cipher, self.settings.ecs_emode_b_key or "")
        text, truncated = _decode_text_chunked(plain, max_chars)
        del plain
        del cipher
        del buf
        return AttachmentExcerpt(
            file_id=meta.file_id,
            location_path=meta.location_path,
            text=text,
            truncated=truncated,
        )


class InMemoryObjectStore:
    """测试用：location_path → 密文字节（分块 yield）。"""

    def __init__(self, mapping: dict[str, bytes]):
        self.mapping = mapping

    def open_stream(self, location_path: str) -> Iterable[bytes]:
        data = self.mapping.get(location_path)
        if data is None:
            raise FileNotFoundError(location_path)
        step = 1024
        for i in range(0, len(data), step):
            yield data[i : i + step]


class InMemoryMetaStore:
    """测试用：invest_id → AttachmentMeta 列表。"""

    def __init__(self, rows: dict[str, List[AttachmentMeta]]):
        self.rows = rows

    def list_by_invest_id(self, invest_id: str) -> List[AttachmentMeta]:
        return list(self.rows.get(invest_id, []))
