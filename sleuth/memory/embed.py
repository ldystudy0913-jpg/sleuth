"""Query / write embeddings via an OpenAI-compatible gateway."""
from __future__ import annotations

import logging
import math
import re
from typing import List, Optional, Protocol

from . import settings

log = logging.getLogger("sleuth.memory.embed")

_TOKEN = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.I)


class Embedder(Protocol):
    def embed(self, text: str) -> List[float]:
        ...


class HashEmbedder:
    """Deterministic bag-of-tokens vector for tests (no network)."""

    def __init__(self, dim: int):
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        for tok in _TOKEN.findall(text or ""):
            vec[hash(tok.lower()) % self.dim] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]


class OpenAIEmbedder:
    def __init__(self, config):
        self.config = config

    def embed(self, text: str) -> List[float]:
        from openai import OpenAI

        mem = settings.memory_cfg(self.config)
        api_key, base_url = _embed_credentials(self.config)
        if not api_key or not mem.embedding_model:
            raise RuntimeError("embedding is not configured")
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        resp = client.embeddings.create(model=mem.embedding_model, input=text or "")
        vec = list(resp.data[0].embedding)
        dim = settings.embedding_dim(self.config)
        if len(vec) != dim:
            raise RuntimeError(
                f"embedding dim {len(vec)} does not match configured {dim}"
            )
        return [float(x) for x in vec]


def _embed_credentials(config) -> tuple:
    mem = settings.memory_cfg(config)
    api_key = (mem.embedding_api_key or "").strip()
    base_url = (mem.embedding_base_url or "").strip()
    if api_key and base_url:
        return api_key, base_url
    opts = {}
    model_ref = getattr(config, "model", None) or ""
    if model_ref:
        from ..config import parse_model_ref

        provider_id, _ = parse_model_ref(str(model_ref))
        opts = config.provider_options(provider_id) or {}
    if not opts:
        providers = getattr(config, "providers", None) or {}
        for entry in providers.values():
            if isinstance(entry, dict) and entry.get("options"):
                opts = entry["options"]
                break
    if not api_key:
        api_key = str(opts.get("apiKey") or opts.get("api_key") or "").strip()
    if not base_url:
        base_url = str(opts.get("baseURL") or opts.get("base_url") or "").strip()
    return api_key, base_url


def embedder_for(config) -> Optional[Embedder]:
    injected = getattr(config, "_embedder", None)
    if injected is not None:
        return injected
    mem = settings.memory_cfg(config)
    if mem is None or not (mem.embedding_model or "").strip():
        return None
    api_key, _ = _embed_credentials(config)
    if not api_key:
        log.warning("memory embedding model set but no API key; memory disabled")
        return None
    return OpenAIEmbedder(config)
