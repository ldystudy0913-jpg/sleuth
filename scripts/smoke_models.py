"""Local smoke test: load .env, resolve/switch models, live API ping."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

from sleuth.config import load
from sleuth.provider.factory import resolve_model
from sleuth.session import Session
from sleuth.util.env import load_dotenv


def main() -> int:
    workdir = Path(__file__).resolve().parents[1]
    load_dotenv(workdir)
    cfg = load(workdir)

    print("=== config (no secrets) ===")
    print("SLEUTH_MODEL:", cfg.model)
    print("models keys:", sorted(cfg.models.keys()))
    for key, entry in sorted(cfg.models.items()):
        if isinstance(entry, dict):
            model = entry.get("model") or entry.get("id") or key
            base = entry.get("baseURL") or entry.get("base_url")
            has_key = bool(entry.get("apiKey") or entry.get("api_key"))
            print(f"  {key}: model={model} baseURL={base} has_key={has_key}")
        else:
            print(f"  {key}: {entry}")
    for pid in sorted(cfg.providers):
        opts = cfg.provider_options(pid)
        print(
            f"  provider {pid}: baseURL={opts.get('baseURL')} "
            f"has_key={bool(opts.get('apiKey'))}"
        )

    if not cfg.model and not cfg.models:
        print("FAIL: no model configured")
        return 2

    provider, model_id = resolve_model(cfg, cfg.default_agent)
    print("=== resolve default ===")
    print(
        f"ref={provider.id}/{model_id} base_url={provider.base_url} "
        f"has_key={bool(provider.api_key)}"
    )

    if cfg.models:
        sess = Session(
            provider=provider,
            registry=MagicMock(),
            config=cfg,
            workdir=workdir,
            permission=MagicMock(),
            model_id=model_id,
            store=None,
        )
        for key in list(cfg.models.keys())[:5]:
            ref = sess.set_model(key)
            print(
                f"set_model({key!r}) -> {ref} | "
                f"key={bool(sess.provider.api_key)} url={sess.provider.base_url}"
            )

    print("=== live API ping ===")
    p, mid = resolve_model(cfg, cfg.default_agent)
    if not p.api_key:
        print("SKIP: no api key")
        return 0
    try:
        resp = p._client.chat.completions.create(
            model=mid,
            messages=[{"role": "user", "content": "reply with exactly: pong"}],
            max_tokens=8,
        )
        text = (resp.choices[0].message.content or "").strip()
        print("OK:", repr(text)[:160])
        return 0
    except Exception as exc:
        print("LIVE_ERROR:", type(exc).__name__, str(exc)[:500])
        return 1


if __name__ == "__main__":
    sys.exit(main())
