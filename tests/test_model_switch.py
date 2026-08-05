"""Tests for multi-model config and mid-session model switching."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from sleuth.config import Config, _parse_models_env
from sleuth.cli import _expand_command
from sleuth.session import Session


class ParseModelsEnvTests(unittest.TestCase):
    def test_json_object(self):
        got = _parse_models_env(
            '{"fast":"openai/gpt-4o-mini","smart":"openai/gpt-4o"}'
        )
        self.assertEqual(
            got,
            {"fast": "openai/gpt-4o-mini", "smart": "openai/gpt-4o"},
        )

    def test_csv_aliases(self):
        got = _parse_models_env(
            "fast:openai/gpt-4o-mini,smart:openai/gpt-4o,ds:openai/deepseek-chat"
        )
        self.assertEqual(got["fast"], "openai/gpt-4o-mini")
        self.assertEqual(got["ds"], "openai/deepseek-chat")

    def test_invalid_json_returns_empty(self):
        self.assertEqual(_parse_models_env("{not-json"), {})


class ProvidersEnvTests(unittest.TestCase):
    def test_sleuth_providers_json_seeds_options(self):
        cfg = Config()
        raw = {
            "deepseek": {
                "apiKey": "sk-ds",
                "baseURL": "https://api.deepseek.com",
            },
            "qwen": {
                "options": {
                    "apiKey": "sk-qw",
                    "base_url": "https://qwen.example/v1",
                }
            },
        }
        with patch.dict(
            os.environ,
            {"SLEUTH_PROVIDERS": __import__("json").dumps(raw)},
            clear=False,
        ):
            from sleuth.config import _apply_env

            _apply_env(cfg)
        self.assertEqual(cfg.provider_options("deepseek")["apiKey"], "sk-ds")
        self.assertEqual(
            cfg.provider_options("deepseek")["baseURL"],
            "https://api.deepseek.com",
        )
        self.assertEqual(cfg.provider_options("qwen")["apiKey"], "sk-qw")
        self.assertEqual(
            cfg.provider_options("qwen")["baseURL"],
            "https://qwen.example/v1",
        )

    def test_build_provider_uses_per_provider_credentials(self):
        from sleuth.provider.factory import build_provider

        cfg = Config()
        cfg.providers = {
            "deepseek": {
                "options": {
                    "apiKey": "sk-ds",
                    "baseURL": "https://api.deepseek.com",
                }
            },
            "qwen": {
                "options": {
                    "apiKey": "sk-qw",
                    "baseURL": "https://qwen.example/v1",
                }
            },
        }
        ds = build_provider(cfg, "deepseek")
        qw = build_provider(cfg, "qwen")
        self.assertEqual(ds.id, "deepseek")
        self.assertEqual(ds.api_key, "sk-ds")
        self.assertEqual(ds.base_url, "https://api.deepseek.com")
        self.assertEqual(qw.id, "qwen")
        self.assertEqual(qw.api_key, "sk-qw")
        self.assertEqual(qw.base_url, "https://qwen.example/v1")

    def test_named_env_vars_as_fallback(self):
        from sleuth.provider.factory import build_provider

        cfg = Config()
        with patch.dict(
            os.environ,
            {
                "DEEPSEEK_API_KEY": "sk-from-env",
                "DEEPSEEK_BASE_URL": "https://from-env.example",
            },
            clear=False,
        ):
            p = build_provider(cfg, "deepseek")
        self.assertEqual(p.api_key, "sk-from-env")
        self.assertEqual(p.base_url, "https://from-env.example")

    def test_object_entry_without_provider_prefix(self):
        cfg = Config(
            models={
                "deepseek-chat": {
                    "apiKey": "sk-ds",
                    "baseURL": "https://api.deepseek.com",
                },
                "qwen-max": {
                    "model": "qwen-max",
                    "apiKey": "sk-qw",
                    "baseURL": "https://qwen.example/v1",
                },
            }
        )
        ref = cfg.prepare_model_ref("deepseek-chat")
        self.assertEqual(ref, "deepseek-chat/deepseek-chat")
        self.assertEqual(cfg.provider_options("deepseek-chat")["apiKey"], "sk-ds")
        self.assertEqual(
            cfg.provider_options("deepseek-chat")["baseURL"],
            "https://api.deepseek.com",
        )

        from sleuth.provider.factory import build_provider, resolve_model

        cfg.model = "deepseek-chat"
        provider, model_id = resolve_model(cfg, "build")
        self.assertEqual(model_id, "deepseek-chat")
        self.assertEqual(provider.id, "deepseek-chat")
        self.assertEqual(provider.api_key, "sk-ds")

        sess = Session(
            provider=provider,
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            model_id=model_id,
            store=None,
        )
        sess.set_model("qwen-max")
        self.assertEqual(sess.model_id, "qwen-max")
        self.assertEqual(sess.provider.api_key, "sk-qw")
        self.assertEqual(sess.provider.base_url, "https://qwen.example/v1")

    def test_parse_models_env_keeps_objects(self):
        got = _parse_models_env(
            '{"deepseek-chat":{"apiKey":"sk","baseURL":"https://x"}}'
        )
        self.assertEqual(got["deepseek-chat"]["apiKey"], "sk")
        self.assertEqual(got["deepseek-chat"]["baseURL"], "https://x")


class ConfigModelsTests(unittest.TestCase):
    def test_merge_and_alias_resolve(self):
        cfg = Config()
        cfg.merge({"models": {"fast": "openai/gpt-4o-mini"}})
        self.assertEqual(cfg.resolve_model_alias("fast"), "openai/gpt-4o-mini")
        self.assertEqual(cfg.resolve_model_alias("openai/gpt-4o"), "openai/gpt-4o")

    def test_env_models(self):
        cfg = Config()
        with patch.dict(
            os.environ,
            {"SLEUTH_MODELS": "fast:openai/gpt-4o-mini"},
            clear=False,
        ):
            from sleuth.config import _apply_env

            _apply_env(cfg)
        self.assertEqual(cfg.models.get("fast"), "openai/gpt-4o-mini")


class SessionSetModelTests(unittest.TestCase):
    def test_set_model_updates_provider_and_id(self):
        cfg = Config(model="openai/gpt-4o", models={"fast": "openai/gpt-4o-mini"})
        provider = MagicMock()
        provider.id = "openai"
        sess = Session(
            provider=provider,
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            model_id="gpt-4o",
            store=None,
        )
        fake = MagicMock()
        fake.id = "openai"
        with patch("sleuth.provider.factory.build_provider", return_value=fake) as build:
            ref = sess.set_model("fast")
        self.assertEqual(ref, "openai/gpt-4o-mini")
        self.assertEqual(sess.model_id, "gpt-4o-mini")
        self.assertEqual(sess.config.model, "openai/gpt-4o-mini")
        build.assert_called_once()
        self.assertEqual(sess.model_ref(), "openai/gpt-4o-mini")

    def test_set_model_persists_to_store(self):
        cfg = Config(model="openai/a")
        store = MagicMock()
        rec = MagicMock()
        rec.model = {"id": "a", "providerID": "openai"}
        rec.metadata = {}
        store.get_session.return_value = rec
        provider = MagicMock()
        provider.id = "openai"
        sess = Session(
            provider=provider,
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            model_id="a",
            store=store,
            id="sess_test",
        )
        fake = MagicMock()
        fake.id = "openai"
        with patch("sleuth.provider.factory.build_provider", return_value=fake):
            sess.set_model("openai/b")
        self.assertEqual(rec.model["id"], "b")
        self.assertEqual(rec.model["providerID"], "openai")
        store.update_session.assert_called()


class CliModelCommandTests(unittest.TestCase):
    def test_model_list_and_switch(self):
        cfg = Config(
            model="openai/gpt-4o",
            models={"fast": "openai/gpt-4o-mini"},
        )
        provider = MagicMock()
        provider.id = "openai"
        sess = Session(
            provider=provider,
            registry=MagicMock(),
            config=cfg,
            workdir=Path("."),
            permission=MagicMock(),
            model_id="gpt-4o",
            store=None,
        )
        fake = MagicMock()
        fake.id = "openai"
        self.assertIsNone(_expand_command(sess, "/model"))
        with patch("sleuth.provider.factory.build_provider", return_value=fake):
            self.assertIsNone(_expand_command(sess, "/model fast"))
        self.assertEqual(sess.model_id, "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
