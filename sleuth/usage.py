"""Token usage + cost — port of opencode `session.getUsage`.

Normalises provider stream usage into the shape sleuth stores on messages and
session totals. Without models.dev pricing we keep cost at 0 unless optional
per-million rates are supplied via config `provider.<id>.options.cost`.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def normalize_usage(raw: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    """Map various provider usage field names to sleuth's canonical keys.

    Canonical keys (matching CLI / SQLite): input, output, reasoning,
    cache_read, cache_write, total.
    """
    if not raw:
        return {}

    def _i(*keys: str) -> int:
        for k in keys:
            if k in raw and raw[k] is not None:
                try:
                    v = int(raw[k])
                    return max(0, v) if v == v else 0  # NaN guard
                except (TypeError, ValueError):
                    continue
        return 0

    input_tokens = _i("input", "input_tokens", "prompt_tokens")
    output_tokens = _i("output", "output_tokens", "completion_tokens")
    reasoning = _i("reasoning", "reasoning_tokens", "completion_tokens_details.reasoning_tokens")
    cache_read = _i(
        "cache_read",
        "cache_read_input_tokens",
        "prompt_tokens_details.cached_tokens",
    )
    cache_write = _i("cache_write", "cache_write_input_tokens", "cache_creation_input_tokens")

    # Nested OpenAI completion/prompt details
    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    if isinstance(details, Mapping):
        if not cache_read:
            cache_read = _i_from(details, "cached_tokens", "cache_read")
    out_details = raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    if isinstance(out_details, Mapping):
        if not reasoning:
            reasoning = _i_from(out_details, "reasoning_tokens")

    # Non-cached input for cost (opencode subtracts cache tokens)
    adjusted_input = max(0, input_tokens - cache_read - cache_write)
    adjusted_output = max(0, output_tokens - reasoning)
    total = _i("total", "total_tokens") or (
        input_tokens + output_tokens + cache_read + cache_write
    )

    return {
        "input": adjusted_input,
        "output": adjusted_output,
        "reasoning": reasoning,
        "cache_read": cache_read,
        "cache_write": cache_write,
        "total": total,
        # keep raw prompt/completion for overflow checks
        "raw_input": input_tokens,
        "raw_output": output_tokens,
    }


def _i_from(m: Mapping[str, Any], *keys: str) -> int:
    for k in keys:
        if k in m and m[k] is not None:
            try:
                return max(0, int(m[k]))
            except (TypeError, ValueError):
                continue
    return 0


def compute_cost(usage: Mapping[str, int], rates: Optional[Mapping[str, float]] = None) -> float:
    """Cost in dollars from tokens × $/1M rates.

    `rates` keys: input, output, cache_read, cache_write (reasoning billed as output).
    """
    if not rates or not usage:
        return 0.0

    def rate(key: str) -> float:
        try:
            return float(rates.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    cost = 0.0
    cost += usage.get("input", 0) * rate("input") / 1_000_000
    cost += usage.get("output", 0) * rate("output") / 1_000_000
    cost += usage.get("reasoning", 0) * rate("output") / 1_000_000
    cost += usage.get("cache_read", 0) * rate("cache_read") / 1_000_000
    cost += usage.get("cache_write", 0) * rate("cache_write") / 1_000_000
    return max(0.0, cost)


def extract_openai_chunk_usage(chunk: Any) -> Dict[str, Any]:
    """Pull usage dict from an OpenAI stream chunk (final chunk usually)."""
    usage = getattr(chunk, "usage", None)
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return dict(usage)
    # SDK object
    out: Dict[str, Any] = {}
    for key in (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
    ):
        val = getattr(usage, key, None)
        if val is not None:
            out[key] = val
    for nested_name in ("prompt_tokens_details", "completion_tokens_details"):
        nested = getattr(usage, nested_name, None)
        if nested is None:
            continue
        if isinstance(nested, dict):
            out[nested_name] = nested
        else:
            out[nested_name] = {
                k: getattr(nested, k)
                for k in ("cached_tokens", "reasoning_tokens", "audio_tokens")
                if getattr(nested, k, None) is not None
            }
    return out
