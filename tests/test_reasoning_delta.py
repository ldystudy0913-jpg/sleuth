"""Tests for OpenAI-compatible reasoning delta extraction."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from sleuth.provider.openai_provider import _delta_reasoning_text


class DeltaReasoningTests(unittest.TestCase):
    def test_reasoning_field(self):
        delta = SimpleNamespace(reasoning="think", content="")
        self.assertEqual(_delta_reasoning_text(delta), "think")

    def test_reasoning_content_field_deepseek(self):
        delta = SimpleNamespace(reasoning_content="好的", content=None)
        self.assertEqual(_delta_reasoning_text(delta), "好的")

    def test_prefers_first_nonempty(self):
        delta = SimpleNamespace(reasoning="a", reasoning_content="b")
        self.assertEqual(_delta_reasoning_text(delta), "a")

    def test_empty_reasoning_content_ignored(self):
        delta = SimpleNamespace(reasoning_content="", content="hi")
        self.assertEqual(_delta_reasoning_text(delta), "")

    def test_model_extra_fallback(self):
        delta = SimpleNamespace(model_extra={"reasoning_content": "via-extra"})
        self.assertEqual(_delta_reasoning_text(delta), "via-extra")

    def test_model_dump_fallback(self):
        class DumpOnly:
            def model_dump(self, exclude_none=False):
                return {"reasoning": "from-dump"}

        self.assertEqual(_delta_reasoning_text(DumpOnly()), "from-dump")


if __name__ == "__main__":
    unittest.main()
