"""Load check rubric from JSON. Weights and labels live in the file, not in code."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping


class RubricError(RuntimeError):
    """Rubric file missing or invalid."""


def load_rubric(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise RubricError(f"rubric file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RubricError(f"rubric is not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RubricError("rubric root must be an object")
    score = data.get("score")
    if not isinstance(score, dict):
        raise RubricError("rubric.score must be an object")
    if "max" not in score or "decimals" not in score:
        raise RubricError("rubric.score.max and score.decimals are required")
    dims = data.get("dimensions")
    if not isinstance(dims, list) or not dims:
        raise RubricError("rubric.dimensions must be a non-empty list")
    for item in dims:
        if not isinstance(item, dict) or not item.get("id"):
            raise RubricError("each dimension needs id")
        if "weight" not in item:
            raise RubricError(f"dimension {item.get('id')} missing weight")
    word = data.get("word")
    if not isinstance(word, dict) or not word.get("filename") or not word.get("date_format"):
        raise RubricError("rubric.word.filename and word.date_format are required")
    return data


def dimension_ids(rubric: Mapping[str, Any]) -> List[str]:
    return [str(d["id"]) for d in rubric.get("dimensions") or [] if isinstance(d, dict)]


def rubric_guidance(rubric: Mapping[str, Any]) -> str:
    lines = []
    for item in rubric.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('id')}: {item.get('label') or item.get('id')} "
            f"(weight={item.get('weight')}). {item.get('guidance') or ''}"
        )
    return "\n".join(lines)


def kb_max_queries(rubric: Mapping[str, Any], env_override: int) -> int:
    if env_override > 0:
        return int(env_override)
    kb = rubric.get("kb") if isinstance(rubric.get("kb"), dict) else {}
    try:
        return int(kb.get("max_queries") or 0)
    except (TypeError, ValueError):
        return 0


def seed_queries(rubric: Mapping[str, Any]) -> List[str]:
    raw = rubric.get("kb_seed_queries") or []
    if not isinstance(raw, list):
        return []
    return [str(q).strip() for q in raw if str(q).strip()]


def aggregate_score(
    dimension_scores: Mapping[str, Any],
    rubric: Mapping[str, Any],
) -> float:
    score_cfg = rubric.get("score") or {}
    max_s = float(score_cfg["max"])
    decimals = int(score_cfg["decimals"])
    num = 0.0
    den = 0.0
    for item in rubric.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "")
        if not sid or sid not in dimension_scores:
            continue
        try:
            weight = float(item.get("weight"))
            val = float(dimension_scores[sid])
        except (TypeError, ValueError):
            continue
        val = max(0.0, min(max_s, val))
        num += weight * val
        den += weight
    if den <= 0:
        return round(0.0, decimals)
    score = max(0.0, min(max_s, num / den))
    return round(score, decimals)
