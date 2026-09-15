"""Build a .docx check report from structured findings. Layout labels come from rubric."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping


def render_docx_bytes(
    *,
    rubric: Mapping[str, Any],
    score: float,
    summary: str,
    findings: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    generated_at: str = "",
) -> bytes:
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.shared import Pt
    except ImportError as exc:
        raise RuntimeError("python-docx is required: pip install python-docx") from exc

    word = rubric.get("word") if isinstance(rubric.get("word"), dict) else {}
    title = str(word.get("title") or "")
    labels = rubric.get("severity_labels") if isinstance(rubric.get("severity_labels"), dict) else {}
    dim_label = {
        str(d.get("id")): str(d.get("label") or d.get("id"))
        for d in (rubric.get("dimensions") or [])
        if isinstance(d, dict)
    }
    when = generated_at or datetime.now(timezone.utc).strftime(str(word.get("date_format") or "%Y%m%d"))

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement

        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "Calibri")

    doc.add_heading(title, level=0)
    doc.add_paragraph(f"总分：{score}")
    doc.add_paragraph(f"生成日期：{when}")
    if summary:
        doc.add_heading("结论", level=1)
        doc.add_paragraph(summary)

    doc.add_heading("发现问题", level=1)
    if not findings:
        doc.add_paragraph("未列出具体问题。")
    else:
        table = doc.add_table(rows=1, cols=5)
        hdr = table.rows[0].cells
        hdr[0].text = "严重程度"
        hdr[1].text = "维度"
        hdr[2].text = "位置"
        hdr[3].text = "问题"
        hdr[4].text = "依据"
        for item in findings:
            row = table.add_row().cells
            sev = str(item.get("severity") or "")
            row[0].text = str(labels.get(sev) or sev)
            dim = str(item.get("dimension") or "")
            row[1].text = dim_label.get(dim, dim)
            row[2].text = str(item.get("location") or "")
            row[3].text = str(item.get("issue") or "")
            row[4].text = str(item.get("evidence") or "")

    doc.add_heading("知识来源", level=1)
    if not sources:
        doc.add_paragraph("本次未引用知识库。")
    else:
        for src in sources:
            title_s = str(src.get("title") or src.get("file_name") or "")
            url = str(src.get("url") or "")
            doc.add_paragraph(f"{title_s} {url}".strip())

    buf = __import__("io").BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _new_doc():
    from docx import Document
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement

        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), "Calibri")
    return doc


def render_conflicts_docx_bytes(
    *,
    rubric: Mapping[str, Any],
    summary: str,
    conflicts: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    scenario_title: str = "",
    generated_at: str = "",
) -> bytes:
    try:
        from docx import Document  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("python-docx is required: pip install python-docx") from exc

    word = rubric.get("word") if isinstance(rubric.get("word"), dict) else {}
    title = str(word.get("title") or scenario_title or "")
    when = generated_at or datetime.now(timezone.utc).strftime(
        str(word.get("date_format") or "%Y%m%d")
    )
    labels = {
        "obvious": "\u660e\u663e\u51b2\u7a81",
        "suspected": "\u7591\u4f3c\u4e0d\u4e00\u81f4",
    }
    doc = _new_doc()
    doc.add_heading(title, level=0)
    if scenario_title:
        doc.add_paragraph(scenario_title)
    doc.add_paragraph("\u751f\u6210\u65e5\u671f\uff1a" + when)
    if summary:
        doc.add_heading("\u7ed3\u8bba", level=1)
        doc.add_paragraph(summary)

    doc.add_heading("\u51b2\u7a81\u4e8b\u9879", level=1)
    if not conflicts:
        doc.add_paragraph("\u672a\u5217\u51fa\u51b2\u7a81\u3002")
    else:
        table = doc.add_table(rows=1, cols=6)
        hdr = table.rows[0].cells
        hdr[0].text = "\u6d89\u53ca\u4e8b\u9879"
        hdr[1].text = "\u4fe1\u606f1"
        hdr[2].text = "\u4fe1\u606f2"
        hdr[3].text = "\u5224\u65ad"
        hdr[4].text = "\u53d1\u73b0\u7684\u95ee\u9898"
        hdr[5].text = "\u5efa\u8bae"
        for item in conflicts:
            row = table.add_row().cells
            row[0].text = str(item.get("item") or "")
            info1 = str(item.get("info1") or "")
            src1 = str(item.get("info1_source") or "")
            row[1].text = f"{info1} ({src1})" if src1 else info1
            info2 = str(item.get("info2") or "")
            src2 = str(item.get("info2_source") or "")
            row[2].text = f"{info2} ({src2})" if src2 else info2
            verdict = str(item.get("verdict") or "")
            row[3].text = str(labels.get(verdict) or verdict)
            row[4].text = str(item.get("detail") or "")
            row[5].text = str(item.get("suggestion") or "")

    doc.add_heading("\u77e5\u8bc6\u6765\u6e90", level=1)
    if not sources:
        doc.add_paragraph("\u672c\u6b21\u672a\u5f15\u7528\u77e5\u8bc6\u5e93\u3002")
    else:
        for src in sources:
            title_s = str(src.get("title") or src.get("file_name") or "")
            url = str(src.get("url") or "")
            doc.add_paragraph(f"{title_s} {url}".strip())

    buf = __import__("io").BytesIO()
    doc.save(buf)
    return buf.getvalue()

