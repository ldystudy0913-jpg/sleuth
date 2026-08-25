"""In-process text extraction from decrypted session-file bytes."""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional

from ..config import Config, FilesConfig, parse_model_ref


def _files_cfg(config: Optional[Config]) -> FilesConfig:
    if config is None:
        return FilesConfig()
    return getattr(config, "files", None) or FilesConfig()

VISION_PROMPT = (
    "提取图中全部可见文字；若是证件/表单则按字段列出；不要编造看不清的内容。"
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_PDF_EXTS = {".pdf"}
_XLSX_EXTS = {".xlsx"}
_XLS_EXTS = {".xls"}
_DOCX_EXTS = {".docx"}
_TEXT_EXTS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
    ".log",
    ".yaml",
    ".yml",
}


@dataclass
class Excerpt:
    text: str = ""
    truncated: bool = False
    parser: str = ""
    skipped: str = ""


def _clip(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _ext(filename: str) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1]
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1].lower()


def _decode_text(data: bytes, max_chars: int) -> Excerpt:
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return Excerpt(skipped="text decode failed")
    clipped, truncated = _clip(text, max_chars)
    return Excerpt(text=clipped, truncated=truncated, parser="text")


def _extract_pdf(data: bytes, max_chars: int) -> Excerpt:
    try:
        from pypdf import PdfReader
    except ImportError:
        return Excerpt(skipped="pypdf not installed (pip install sleuth[files])")
    try:
        reader = PdfReader(io.BytesIO(data))
        parts = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        return Excerpt(skipped=f"pdf parse failed: {exc}")
    clipped, truncated = _clip("\n".join(parts).strip(), max_chars)
    if not clipped:
        return Excerpt(skipped="pdf has no extractable text", parser="pypdf")
    return Excerpt(text=clipped, truncated=truncated, parser="pypdf")


def _extract_xlsx(data: bytes, max_chars: int) -> Excerpt:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return Excerpt(skipped="openpyxl not installed (pip install sleuth[files])")
    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows: list[str] = []
        for sheet in wb.worksheets:
            rows.append(f"# {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if c is None else str(c) for c in row]
                if any(cells):
                    rows.append("\t".join(cells))
                if max_chars and sum(len(x) + 1 for x in rows) >= max_chars:
                    break
        wb.close()
    except Exception as exc:
        return Excerpt(skipped=f"xlsx parse failed: {exc}")
    clipped, truncated = _clip("\n".join(rows).strip(), max_chars)
    if not clipped:
        return Excerpt(skipped="xlsx has no extractable text", parser="openpyxl")
    return Excerpt(text=clipped, truncated=truncated, parser="openpyxl")


def _extract_docx(data: bytes, max_chars: int) -> Excerpt:
    try:
        from docx import Document
    except ImportError:
        return Excerpt(skipped="python-docx not installed (pip install sleuth[files])")
    try:
        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs if p.text]
        for table in doc.tables:
            for row in table.rows:
                parts.append("\t".join(c.text for c in row.cells))
    except Exception as exc:
        return Excerpt(skipped=f"docx parse failed: {exc}")
    clipped, truncated = _clip("\n".join(parts).strip(), max_chars)
    if not clipped:
        return Excerpt(skipped="docx has no extractable text", parser="python-docx")
    return Excerpt(text=clipped, truncated=truncated, parser="python-docx")


def vision_image_text(data: bytes, mime: str, config: Config) -> str:
    """One-shot vision call. Raises on failure so caller can fall back to OCR."""
    fcfg = _files_cfg(config)
    raw_ref = (fcfg.vision_model or "").strip() or (getattr(config, "model", None) or "")
    if not raw_ref:
        raise RuntimeError("no vision model configured")
    ref = config.prepare_model_ref(raw_ref) if hasattr(config, "prepare_model_ref") else raw_ref
    provider_id, model_id = parse_model_ref(ref)
    from ..provider.factory import build_provider

    provider = build_provider(config, provider_id)
    client = getattr(provider, "_client", None)
    if client is None:
        raise RuntimeError("vision client unavailable")
    mime_use = (mime or "").strip() or "image/jpeg"
    url = f"data:{mime_use};base64,{base64.b64encode(data).decode('ascii')}"
    timeout = max(5.0, float(fcfg.extract_timeout_s or 45))
    resp = client.chat.completions.create(
        model=model_id,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        timeout=timeout,
    )
    choice = (resp.choices or [None])[0]
    message = getattr(choice, "message", None) if choice is not None else None
    text = (getattr(message, "content", None) or "").strip()
    if not text:
        raise RuntimeError("vision returned empty text")
    return text


def ocr_image_text(data: bytes) -> str:
    engine = None
    try:
        from rapidocr_onnxruntime import RapidOCR

        engine = RapidOCR()
    except ImportError:
        try:
            from rapidocr import RapidOCR

            engine = RapidOCR()
        except ImportError as exc:
            raise RuntimeError("rapidocr not installed (pip install sleuth[ocr])") from exc
    result = engine(data)
    rows = result[0] if isinstance(result, tuple) else result
    if not rows:
        return ""
    texts: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            texts.append(str(row.get("txt") or row.get("text") or ""))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            texts.append(str(row[1]))
    return "\n".join(t for t in texts if t).strip()


def _extract_image(data: bytes, mime: str, config: Config, max_chars: int) -> Excerpt:
    mode = (_files_cfg(config).image_mode or "vision").strip().lower()
    if mode in ("0", "false", "no", "off", "disable", "disabled"):
        return Excerpt(skipped="image parsing disabled")
    errors: list[str] = []
    if mode != "ocr":
        try:
            text = vision_image_text(data, mime, config)
            clipped, truncated = _clip(text, max_chars)
            if clipped:
                return Excerpt(text=clipped, truncated=truncated, parser="vision")
            errors.append("vision returned empty text")
        except Exception as exc:
            errors.append(f"vision: {exc}")
    try:
        text = ocr_image_text(data)
        clipped, truncated = _clip(text, max_chars)
        if clipped:
            return Excerpt(text=clipped, truncated=truncated, parser="rapidocr")
        errors.append("rapidocr returned empty text")
    except Exception as exc:
        errors.append(f"rapidocr: {exc}")
    return Excerpt(skipped="image parse failed: " + "; ".join(errors) if errors else "image parse failed")


def _kind(data: bytes, mime: str, filename: str) -> str:
    mime_l = (mime or "").strip().lower()
    ext = _ext(filename)
    if mime_l.startswith("image/") or ext in _IMAGE_EXTS:
        return "image"
    if mime_l == "application/pdf" or ext in _PDF_EXTS or data.startswith(b"%PDF"):
        return "pdf"
    if mime_l in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
    ) or ext in _XLSX_EXTS:
        return "xlsx"
    if mime_l == "application/vnd.ms-excel" or ext in _XLS_EXTS:
        return "xls"
    if mime_l in (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ) or ext in _DOCX_EXTS:
        return "docx"
    if mime_l.startswith("text/") or mime_l in (
        "application/json",
        "application/xml",
        "application/javascript",
    ) or ext in _TEXT_EXTS:
        return "text"
    if data.startswith(b"%PDF"):
        return "pdf"
    if data[:3] == b"\xff\xd8\xff" or data.startswith(b"\x89PNG") or data[8:12] == b"WEBP":
        return "image"
    if b"\x00" not in data[:4096]:
        return "text"
    return "binary"


def extract_bytes(
    data: bytes,
    *,
    mime: str,
    filename: str,
    config: Optional[Config] = None,
    max_chars: int = 0,
) -> Excerpt:
    cfg = config or Config()
    fcfg = _files_cfg(cfg)
    limit = int(max_chars or fcfg.excerpt_max_chars or 8000)
    kind = _kind(data, mime, filename)
    if kind == "image":
        return _extract_image(data, mime, cfg, limit)
    if kind == "pdf":
        return _extract_pdf(data, limit)
    if kind == "xlsx":
        return _extract_xlsx(data, limit)
    if kind == "xls":
        return Excerpt(skipped="xls not supported; save as xlsx")
    if kind == "docx":
        return _extract_docx(data, limit)
    if kind == "text":
        return _decode_text(data, limit)
    return Excerpt(skipped="unsupported binary type")
