"""Agent-agnostic ``sources[]`` harvest and footer (not markdown parsing)."""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.config import AgentConfig, Config
from sleuth.provider.base import Stop, TextDelta, ToolUse
from sleuth.session import NullRenderer, Session
from sleuth.sources import (
    collect_sources,
    format_sources_footer,
    harvest_tool_sources,
    merge_sources,
    normalize_source_item,
)
from sleuth.tools.base import ToolResult


class HarvestSourcesTests(unittest.TestCase):
    def test_top_level_sources_only(self) -> None:
        payload = {
            "markdown": "## 知识来源\n风险点手册.pdf；id=1\n",
            "meta": {
                "kb": {
                    "sources": [
                        {"title": "nested.pdf", "url": "https://kb.example/nested.pdf"}
                    ]
                }
            },
            "sources": [
                {"file_name": "手册.pdf", "url": "https://kb.example/a.pdf"},
                {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
            ],
        }
        items = harvest_tool_sources(payload)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["title"], "手册.pdf")
        self.assertEqual(items[0]["url"], "https://kb.example/a.pdf")

    def test_ignores_markdown_and_cite_strings(self) -> None:
        self.assertEqual(
            harvest_tool_sources(
                {
                    "markdown": "---\n知识来源\n《x》：https://kb.example/x.pdf",
                    "sources": ["风险点手册.pdf；id=1；knowledgeId=10752"],
                }
            ),
            [],
        )
        self.assertIsNone(normalize_source_item("知识来源"))
        self.assertEqual(
            harvest_tool_sources("not a dict"),
            [],
        )

    def test_rejects_data_and_file_urls(self) -> None:
        items = harvest_tool_sources(
            {
                "sources": [
                    {"title": "bad", "url": "data:text/plain,hi"},
                    {"title": "local", "url": "file:///tmp/a.pdf"},
                    {"name": "ok", "href": "http://kb.internal/a.pdf"},
                ]
            }
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["url"], "http://kb.internal/a.pdf")

    def test_collect_from_metadata_and_output(self) -> None:
        found = collect_sources(
            output=json.dumps(
                {
                    "sources": [
                        {"title": "b.pdf", "url": "https://kb.example/b.pdf"},
                    ]
                }
            ),
            metadata={
                "sources": [
                    {"title": "a.pdf", "url": "https://kb.example/a.pdf"},
                ]
            },
        )
        self.assertEqual([x["title"] for x in found], ["a.pdf", "b.pdf"])

    def test_dedupes_same_url_even_if_titles_differ(self) -> None:
        items = harvest_tool_sources(
            {
                "sources": [
                    {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
                    {"file_name": "手册.pdf", "url": "https://kb.example/a.pdf"},
                    {"title": "受益所有人识别", "url": "https://kb.example/a.pdf/"},
                    {"title": "制度.docx", "url": "https://kb.example/b.docx"},
                ]
            }
        )
        self.assertEqual(
            [(x["title"], x["url"]) for x in items],
            [
                ("手册.pdf", "https://kb.example/a.pdf"),
                ("制度.docx", "https://kb.example/b.docx"),
            ],
        )
        merged = merge_sources(
            [{"title": "手册.pdf", "url": "https://kb.example/a.pdf"}],
            [{"title": "另一标题", "url": "https://kb.example/a.pdf"}],
        )
        self.assertEqual(len(merged), 1)


class FooterTests(unittest.TestCase):
    def test_gray_clickable_and_skips_urls_already_in_text(self) -> None:
        sources = [
            {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
            {"title": "制度.docx", "url": "https://kb.example/b.docx"},
        ]
        existing = "引用见 https://kb.example/a.pdf"
        footer = format_sources_footer(sources, existing_text=existing)
        self.assertNotIn("a.pdf", footer)
        self.assertIn("制度.docx", footer)
        self.assertIn("https://kb.example/b.docx", footer)
        self.assertIn('style="color:#888"', footer)
        self.assertIn("<a href=", footer)
        self.assertIn("---", footer)

    def test_empty_when_all_urls_already_present(self) -> None:
        footer = format_sources_footer(
            [{"title": "x", "url": "https://kb.example/x"}],
            existing_text="https://kb.example/x already",
        )
        self.assertEqual(footer, "")

    def test_empty_sources_adds_no_heading(self) -> None:
        self.assertEqual(format_sources_footer([]), "")
        self.assertEqual(format_sources_footer(None or []), "")


class CaptureRenderer(NullRenderer):
    def __init__(self):
        self.chunks: list[str] = []

    def on_text(self, text: str, **kwargs) -> None:
        self.chunks.append(text)


class SourcesFooterSessionTests(unittest.TestCase):
    def _session(self, provider, registry, renderer=None) -> Session:
        cfg = Config(
            default_agent="build",
            agents={"build": AgentConfig(name="build")},
            max_steps=5,
            output_desensitize=True,
        )
        return Session(
            provider=provider,
            registry=registry,
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            agent_name="build",
            model_id="m",
            renderer=renderer or NullRenderer(),
            store=None,
            title="src",
        )

    def test_appends_sources_from_any_agent_json(self) -> None:
        class Provider:
            id = "openai"

            def __init__(self):
                self.n = 0

            def stream(self, **_kwargs):
                self.n += 1
                if self.n == 1:
                    yield ToolUse(id="c1", name="other_agent_run", input={})
                    yield Stop("tool_use", usage={"input": 1, "output": 1})
                    return
                yield TextDelta("整理后的答复。")
                yield Stop("end_turn", usage={"input": 1, "output": 1})

        registry = MagicMock()
        registry.specs.return_value = []
        registry.names.return_value = []
        registry.execute.return_value = ToolResult.success(
            "other_agent_run",
            json.dumps(
                {
                    "body": "agent-specific schema, not dd_reply markdown",
                    "sources": [
                        {
                            "title": "手册.pdf",
                            "url": "https://kb.example/doc/6222021234567890123.pdf",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )
        renderer = CaptureRenderer()
        sess = self._session(Provider(), registry, renderer)
        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            text = sess.prompt("请整理")
        self.assertIn("整理后的答复", text)
        self.assertIn("手册.pdf", text)
        self.assertIn("https://kb.example/doc/6222021234567890123.pdf", text)
        self.assertIn("知识来源", text)
        self.assertTrue(any("知识来源" in c for c in renderer.chunks))

    def test_does_not_parse_agent_markdown_as_sources(self) -> None:
        class Provider:
            id = "openai"

            def __init__(self):
                self.n = 0

            def stream(self, **_kwargs):
                self.n += 1
                if self.n == 1:
                    yield ToolUse(id="c1", name="weird_agent", input={})
                    yield Stop("tool_use", usage={"input": 1, "output": 1})
                    return
                yield TextDelta("只有正文。")
                yield Stop("end_turn", usage={"input": 1, "output": 1})

        registry = MagicMock()
        registry.specs.return_value = []
        registry.names.return_value = []
        registry.execute.return_value = ToolResult.success(
            "weird_agent",
            json.dumps(
                {
                    "markdown": (
                        "## 知识来源\n《幽灵.pdf》：https://kb.example/ghost.pdf\n"
                    )
                },
                ensure_ascii=False,
            ),
        )
        sess = self._session(Provider(), registry)
        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            text = sess.prompt("请整理")
        self.assertEqual(text, "只有正文。")
        self.assertNotIn("幽灵.pdf", text)

    def test_empty_sources_does_not_add_footer(self) -> None:
        class Provider:
            id = "openai"

            def __init__(self):
                self.n = 0

            def stream(self, **_kwargs):
                self.n += 1
                if self.n == 1:
                    yield ToolUse(id="c1", name="kb_lookup", input={"question": "hello"})
                    yield Stop("tool_use", usage={"input": 1, "output": 1})
                    return
                yield TextDelta("闲聊答复。")
                yield Stop("end_turn", usage={"input": 1, "output": 1})

        registry = MagicMock()
        registry.specs.return_value = []
        registry.names.return_value = []
        registry.execute.return_value = ToolResult.success(
            "kb_lookup",
            json.dumps({"found": [], "missing": [], "sources": []}, ensure_ascii=False),
        )
        sess = self._session(Provider(), registry)
        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            text = sess.prompt("你好")
        self.assertEqual(text, "闲聊答复。")
        self.assertNotIn("知识来源", text)
        self.assertNotIn("---", text)

    def test_kb_lookup_hits_append_footer(self) -> None:
        class Provider:
            id = "openai"

            def __init__(self):
                self.n = 0

            def stream(self, **_kwargs):
                self.n += 1
                if self.n == 1:
                    yield ToolUse(id="c1", name="kb_lookup", input={"question": "C001"})
                    yield Stop("tool_use", usage={"input": 1, "output": 1})
                    return
                yield TextDelta("根据检索：请核实受益所有人。")
                yield Stop("end_turn", usage={"input": 1, "output": 1})

        registry = MagicMock()
        registry.specs.return_value = []
        registry.names.return_value = []
        registry.execute.return_value = ToolResult.success(
            "kb_lookup",
            json.dumps(
                {
                    "found": [{"question": "C001", "hits": []}],
                    "missing": [],
                    "sources": [
                        {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
                        {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
                    ],
                },
                ensure_ascii=False,
            ),
            sources=[
                {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
                {"title": "手册.pdf", "url": "https://kb.example/a.pdf"},
            ],
        )
        sess = self._session(Provider(), registry)
        with patch("sleuth.skill.ensure_skills_fresh", return_value={}), patch(
            "sleuth.app.sync_session_mcp"
        ), patch("sleuth.session.assemble", return_value="sys"):
            text = sess.prompt("查 C001")
        self.assertIn("请核实受益所有人", text)
        self.assertEqual(text.count("《手册.pdf》"), 1)
        self.assertIn("知识来源", text)
        self.assertIn("https://kb.example/a.pdf", text)


if __name__ == "__main__":
    unittest.main()
