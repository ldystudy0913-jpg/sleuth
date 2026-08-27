"""Query / write embeddings via an OpenAI-compatible gateway."""
from __future__ import annotations

import json
import logging
import math
import re
import urllib.error
import urllib.request
from typing import List, Optional, Protocol

from . import settings

log = logging.getLogger("sleuth.memory.embed")

_TOKEN = re.compile(r"[a-z0-9\u4e00-\u9fff]+", re.I)
_EMBED_TIMEOUT_S = 60


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
        mem = settings.memory_cfg(self.config)
        api_key, base_url = _embed_credentials(self.config)
        if not api_key or not mem.embedding_model:
            raise RuntimeError("embedding is not configured")
        endpoint = embeddings_endpoint(base_url)
        if not endpoint:
            raise RuntimeError("embedding base URL is not configured")
        vec = _post_embedding(endpoint, api_key, mem.embedding_model, text or "")
        dim = settings.embedding_dim(self.config)
        if len(vec) != dim:
            raise RuntimeError(
                f"embedding dim {len(vec)} does not match configured {dim}"
            )
        return [float(x) for x in vec]


def embeddings_endpoint(base_url: str) -> str:
    """Resolve the POST URL.

    OpenAI's client always posts to ``{base_url}/embeddings``. Operators often
    paste the full embeddings URL into ``SLEUTH_EMBEDDING_BASE_URL``, which
    would otherwise become ``.../embeddings/embeddings`` (FastAPI 404).
    """
    url = (base_url or "").strip()
    if not url:
        return ""
    url = url.rstrip("/")
    if url.lower().endswith("/embeddings"):
        return url
    return url + "/embeddings"


def parse_embedding_vector(payload) -> List[float]:
    if isinstance(payload, list) and payload and isinstance(payload[0], (int, float)):
        return [float(x) for x in payload]
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"unexpected embedding response type: {type(payload).__name__}"
        )
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and first.get("embedding") is not None:
            return [float(x) for x in first["embedding"]]
        if isinstance(first, (list, tuple)):
            return [float(x) for x in first]
    emb = payload.get("embedding")
    if isinstance(emb, list):
        return [float(x) for x in emb]
    raise RuntimeError("embedding response missing vector")


def _post_embedding(url: str, api_key: str, model: str, text: str) -> List[float]:
    payload = {"input": text or ""}
    if model:
        payload["model"] = model
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_EMBED_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(
            f"embedding request failed ({exc.code}) at {url}: {detail or exc.reason}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"embedding request failed at {url}: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"embedding response is not JSON: {raw[:200]}") from exc
    return parse_embedding_vector(parsed)


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
