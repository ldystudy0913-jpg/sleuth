"""OpenGauss layout variants: JSONB text columns and FLOATVECTOR."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from sleuth.config import Config, _apply_env
from sleuth.memory import settings
from sleuth.memory.store import (
    decode_text_field,
    encode_text_field,
    psycopg2_missing_message,
)
from sleuth.server.memory_api import _memory_unavailable


class OpenGaussCompatTests(unittest.TestCase):
    def test_env_loads_floatvector_and_jsonb(self):
        cfg = Config()
        env = {
            "SLEUTH_MEMORY_VECTOR_KIND": "floatvector",
            "SLEUTH_MEMORY_TEXT_KIND": "jsonb",
        }
        with patch.dict(os.environ, env, clear=False):
            _apply_env(cfg)
        self.assertEqual(cfg.memory.vector_kind, "floatvector")
        self.assertEqual(cfg.memory.text_kind, "jsonb")
        self.assertTrue(settings.uses_sql_ann(cfg))
        self.assertEqual(settings.vector_sql_type(cfg), "floatvector")
        score_sql, dist_sql = settings.ann_distance_sql(cfg)
        self.assertIn("cosine_distance", dist_sql)
        self.assertNotIn("<=>", dist_sql)
        self.assertIn("cosine_distance", score_sql)

    def test_pgvector_ann_uses_cosine_ops(self):
        cfg = Config()
        cfg.memory.vector_kind = "vector"
        _, dist_sql = settings.ann_distance_sql(cfg)
        self.assertIn("<=>", dist_sql)
        self.assertNotIn("cosine_distance", dist_sql)

    def test_env_loads_item_key_catalog(self):
        cfg = Config()
        env = {
            "SLEUTH_MEMORY_ITEM_KEY_DOMAINS": "output,str",
            "SLEUTH_MEMORY_ITEM_KEYS": "output.language,str.threshold",
        }
        with patch.dict(os.environ, env, clear=False):
            _apply_env(cfg)
        self.assertEqual(cfg.memory.item_key_domains, "output,str")
        self.assertEqual(cfg.memory.item_keys, "output.language,str.threshold")
        self.assertEqual(settings.item_keys(cfg), ["output.language", "str.threshold"])

    def test_default_catalog_includes_usage_habits(self):
        cfg = Config()
        self.assertIn("usage", settings.item_key_domains(cfg))
        keys = settings.item_keys(cfg)
        self.assertIn("usage.tables", keys)
        self.assertIn("usage.fields", keys)
        self.assertIn("usage.habit", keys)

    def test_env_loads_merge_knobs(self):
        cfg = Config()
        env = {
            "SLEUTH_MEMORY_MERGE_SCORE": "0.91",
            "SLEUTH_MEMORY_MERGE_ACROSS_SCOPES": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            _apply_env(cfg)
        self.assertEqual(cfg.memory.merge_score, "0.91")
        self.assertFalse(cfg.memory.merge_across_scopes)
        self.assertAlmostEqual(settings.merge_score(cfg), 0.91)
        self.assertFalse(settings.merge_across_scopes(cfg))

    def test_encode_jsonb_wraps_plain_text(self):
        cfg = Config()
        cfg.memory.text_kind = "jsonb"
        self.assertEqual(encode_text_field("用中文回复", cfg), '"用中文回复"')
        self.assertEqual(encode_text_field('{"k":1}', cfg), '{"k":1}')
        self.assertIsNone(encode_text_field(None, cfg))

    def test_encode_text_kind_passes_through(self):
        cfg = Config()
        cfg.memory.text_kind = "text"
        self.assertEqual(encode_text_field("用中文回复", cfg), "用中文回复")

    def test_decode_jsonb_object(self):
        self.assertEqual(decode_text_field({"k": 1}), '{"k": 1}')
        self.assertEqual(decode_text_field("已是字符串"), "已是字符串")
        self.assertIsNone(decode_text_field(None))

    def test_psycopg2_message_uses_this_interpreter(self):
        msg = psycopg2_missing_message()
        self.assertIn("-m pip install psycopg2-binary", msg)
        self.assertIn('pip install -e ".[memory]"', msg)
        self.assertIn("Do not pip install sleuth[memory]", msg)

    def test_unavailable_response_includes_detail(self):
        cfg = Config()
        cfg._memory_error = "psycopg2 is not importable"
        from sleuth.bizerror import APPError, BizErrorCode

        with self.assertRaises(APPError) as ctx:
            _memory_unavailable(cfg)
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(ctx.exception.code, BizErrorCode.MEMORY_UNAVAILABLE.code)
        self.assertIn("long-term memory is not configured", ctx.exception.msg)
        self.assertIn("psycopg2 is not importable", ctx.exception.msg)


if __name__ == "__main__":
    unittest.main()
