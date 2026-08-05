"""Provider factory.

Builds a concrete Provider from config + environment. opencode loads
providers from a remote model catalog and AI SDK packages; for the MVP we
support OpenAI and any OpenAI-compatible gateway (OpenRouter, Groq, local
servers, ...) selected by the `provider/model` ref in config. Credentials
are resolved from provider config options first, then environment vars.
"""
from __future__ import annotations

import os
from typing import Optional

from ..config import Config, parse_model_ref
from .base import Provider, ProviderError


def build_provider(config: Config, provider_id: str) -> Provider:
    """Construct a provider client by id, pulling credentials from config
    or the standard env vars.

    Precedence for both key and base_url: config `options` -> env var -> SDK
    default. So you can run entirely from .env without an opencode.json.
    """
    from .openai_provider import OpenAIProvider

    options = config.provider_options(provider_id)
    api_key = options.get("apiKey") or _env_key(provider_id)
    base_url = options.get("baseURL") or options.get("base_url") or _env_base_url(provider_id)

    # Everything is OpenAI-compatible: the official API, OpenRouter, Groq,
    # local llama.cpp servers, etc. They all speak the Chat Completions API.
    provider = OpenAIProvider(api_key=api_key, base_url=base_url)
    provider.id = provider_id
    return provider


def _env_key(provider_id: str) -> Optional[str]:
    mapping = {
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "groq": "GROQ_API_KEY",
        "together": "TOGETHER_API_KEY",
        "xai": "XAI_API_KEY",
        "mistral": "MISTRAL_API_KEY",
    }
    var = mapping.get(provider_id)
    if var:
        return os.environ.get(var)
    # generic fallback for any OpenAI-compatible provider
    return os.environ.get(f"{provider_id.upper().replace('-', '_')}_API_KEY")


def _env_base_url(provider_id: str) -> Optional[str]:
    mapping = {
        "openai": "OPENAI_BASE_URL",
        "openrouter": "OPENROUTER_BASE_URL",
        "groq": "GROQ_BASE_URL",
        "together": "TOGETHER_BASE_URL",
        "xai": "XAI_BASE_URL",
        "mistral": "MISTRAL_BASE_URL",
    }
    var = mapping.get(provider_id)
    if var:
        return os.environ.get(var)
    return os.environ.get(f"{provider_id.upper().replace('-', '_')}_BASE_URL")


def resolve_model(config: Config, agent_name: str) -> tuple[Provider, str]:
    """Pick the provider + model id for the given agent.

    Precedence: agent config `model` -> config top-level `model` ->
    OPENCODE_MODEL env var (so a pure .env setup works with no json file).

    Bare catalog names (``SLEUTH_MODELS`` object entries without a
    ``provider/`` prefix) are expanded via ``prepare_model_ref``.
    """
    agent = config.agent(agent_name)
    raw = agent.model or config.model or os.environ.get("OPENCODE_MODEL")
    if not raw:
        raise ProviderError(
            "no model configured. Set SLEUTH_MODEL (or `model` in "
            'config) to e.g. "openai/gpt-4o" or a SLEUTH_MODELS key.'
        )
    ref = config.prepare_model_ref(str(raw))
    provider_id, model_id = parse_model_ref(ref)
    provider = build_provider(config, provider_id)
    return provider, model_id
