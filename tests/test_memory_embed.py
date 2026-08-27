"""Embedding gateway URL and response parsing."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO

from sleuth.config import Config
from sleuth.memory.embed import (
    OpenAIEmbedder,
    embeddings_endpoint,
    parse_embedding_vector,
)
from sleuth.memory.store import _bind_embedding


class EmbeddingUrlTests(unittest.TestCase):
    def test_full_embeddings_url_is_not_doubled(self):
        url = embeddings_endpoint(
            "http://illm.example/llm/bge-m3/v1/embeddings"
        )
        self.assertEqual(url, "http://illm.example/llm/bge-m3/v1/embeddings")

    def test_trailing_slash_stripped(self):
        url = embeddings_endpoint("http://illm.example/llm/bge-m3/v1/embeddings/")
        self.assertEqual(url, "http://illm.example/llm/bge-m3/v1/embeddings")

    def test_openai_style_base_appends_embeddings(self):
        url = embeddings_endpoint("https://api.openai.com/v1")
        self.assertEqual(url, "https://api.openai.com/v1/embeddings")


class EmbeddingParseTests(unittest.TestCase):
    def test_openai_data_shape(self):
        vec = parse_embedding_vector({"data": [{"embedding": [0.1, 0.2]}]})
        self.assertEqual(vec, [0.1, 0.2])

    def test_top_level_embedding(self):
        vec = parse_embedding_vector({"embedding": [1.0, 2.0]})
        self.assertEqual(vec, [1.0, 2.0])

    def test_missing_vector_raises(self):
        with self.assertRaisesRegex(RuntimeError, "missing vector"):
            parse_embedding_vector({"detail": "Not Found"})


class EmbeddingClientTests(unittest.TestCase):
    def _cfg(self):
        cfg = Config()
        cfg.memory.embedding_model = "bge-m3"
        cfg.memory.embedding_dim = 2
        cfg.memory.embedding_base_url = (
            "http://illm.example/llm/bge-m3/v1/embeddings"
        )
        cfg.memory.embedding_api_key = "sk-test"
        return cfg

    @patch("sleuth.memory.embed._post_embedding")
    def test_embed_posts_to_resolved_url_once(self, post):
        post.return_value = [0.25, 0.75]
        vec = OpenAIEmbedder(self._cfg()).embed("喜欢小猫")
        self.assertEqual(vec, [0.25, 0.75])
        post.assert_called_once_with(
            "http://illm.example/llm/bge-m3/v1/embeddings",
            "sk-test",
            "bge-m3",
            "喜欢小猫",
        )

    @patch("sleuth.memory.embed.urllib.request.urlopen")
    def test_http_404_mentions_url(self, urlopen):
        urlopen.side_effect = HTTPError(
            "http://illm.example/llm/bge-m3/v1/embeddings/embeddings",
            404,
            "Not Found",
            hdrs={},
            fp=BytesIO(json.dumps({"detail": "Not Found"}).encode("utf-8")),
        )
        with self.assertRaisesRegex(RuntimeError, "embedding request failed \\(404\\)"):
            OpenAIEmbedder(self._cfg()).embed("喜欢小猫")


class EmbeddingBindTests(unittest.TestCase):
    def test_floatvector_rejects_null(self):
        cfg = Config()
        cfg.memory.vector_kind = "floatvector"
        with self.assertRaisesRegex(RuntimeError, "do not accept NULL"):
            _bind_embedding(None, cfg)

    def test_floatvector_formats_values(self):
        cfg = Config()
        cfg.memory.vector_kind = "floatvector"
        text = _bind_embedding([0.1, 0.2], cfg)
        self.assertTrue(text.startswith("["))
        self.assertIn("0.10000000", text)


if __name__ == "__main__":
    unittest.main()
