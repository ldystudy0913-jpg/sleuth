"""Load check-scenario catalog from config/scenarios."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class ScenarioError(RuntimeError):
    """Catalog or scenario pack is missing or invalid."""


@dataclass(frozen=True)
class ScenarioPack:
    scenario_id: str
    title: str
    aliases: tuple
    description: str
    output: str
    system_path: Path
    user_path: Path
    rubric_path: Path

    def public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.scenario_id,
            "title": self.title,
            "aliases": list(self.aliases),
            "description": self.description,
            "output": self.output,
        }


def _as_str_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise ScenarioError(f"missing file: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"not JSON: {path}: {exc}") from exc


def _pack_from_inline(config_dir: Path, item: Dict[str, Any]) -> ScenarioPack:
    sid = str(item.get("id") or "").strip()
    if not sid:
        raise ScenarioError("catalog entry missing id")
    prompt_dir = Path(str(item.get("prompt_dir") or "prompts"))
    if not prompt_dir.is_absolute():
        prompt_dir = config_dir / prompt_dir
    rubric_rel = Path(str(item.get("rubric_path") or "rubric.json"))
    rubric_path = rubric_rel if rubric_rel.is_absolute() else config_dir / rubric_rel
    return ScenarioPack(
        scenario_id=sid,
        title=str(item.get("title") or sid).strip() or sid,
        aliases=tuple(_as_str_list(item.get("aliases"))),
        description=str(item.get("description") or "").strip(),
        output=str(item.get("output") or "findings").strip() or "findings",
        system_path=prompt_dir / "system.md",
        user_path=prompt_dir / "user.md",
        rubric_path=rubric_path,
    )


def _pack_from_dir(root: Path, sid: str) -> ScenarioPack:
    folder = root / sid
    meta = _read_json(folder / "scenario.json")
    if not isinstance(meta, dict):
        raise ScenarioError(f"scenario.json must be an object: {sid}")
    output = str(meta.get("output") or "conflicts").strip() or "conflicts"
    return ScenarioPack(
        scenario_id=str(meta.get("id") or sid).strip() or sid,
        title=str(meta.get("title") or sid).strip() or sid,
        aliases=tuple(_as_str_list(meta.get("aliases"))),
        description=str(meta.get("description") or "").strip(),
        output=output,
        system_path=folder / "system.md",
        user_path=folder / "user.md",
        rubric_path=folder / "rubric.json",
    )


def load_catalog(config_dir: Path) -> List[ScenarioPack]:
    root = Path(config_dir) / "scenarios"
    data = _read_json(root / "catalog.json")
    if not isinstance(data, dict):
        raise ScenarioError("catalog.json root must be an object")
    raw = data.get("scenarios")
    if not isinstance(raw, list) or not raw:
        raise ScenarioError("catalog.json scenarios must be a non-empty list")
    packs: List[ScenarioPack] = []
    seen = set()
    for item in raw:
        if isinstance(item, str):
            pack = _pack_from_dir(root, item.strip())
        elif isinstance(item, dict):
            pack = _pack_from_inline(Path(config_dir), item)
        else:
            raise ScenarioError("catalog entry must be a string id or object")
        if pack.scenario_id in seen:
            raise ScenarioError(f"duplicate scenario id: {pack.scenario_id}")
        seen.add(pack.scenario_id)
        packs.append(pack)
    return packs


def public_catalog(config_dir: Path) -> List[Dict[str, Any]]:
    return [pack.public_dict() for pack in load_catalog(config_dir)]


def get_pack(config_dir: Path, scenario_id: str) -> ScenarioPack:
    wanted = (scenario_id or "").strip()
    for pack in load_catalog(config_dir):
        if pack.scenario_id == wanted:
            return pack
    raise ScenarioError(f"unknown scenario: {wanted}")


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def resolve_scenario_token(config_dir: Path, token: str) -> Optional[ScenarioPack]:
    raw = (token or "").strip()
    if not raw:
        return None
    packs = load_catalog(config_dir)
    lowered = _norm(raw)
    for pack in packs:
        if pack.scenario_id == raw or _norm(pack.scenario_id) == lowered:
            return pack
        if raw in pack.aliases or lowered in {_norm(a) for a in pack.aliases}:
            return pack
    return None


def match_from_utterance(config_dir: Path, text: str) -> Optional[ScenarioPack]:
    blob = (text or "").strip()
    if not blob:
        return None
    hits: Dict[str, ScenarioPack] = {}
    for pack in load_catalog(config_dir):
        needles = [pack.scenario_id, pack.title, *pack.aliases]
        for needle in needles:
            n = (needle or "").strip()
            if n and n in blob:
                hits[pack.scenario_id] = pack
                break
    if len(hits) == 1:
        return next(iter(hits.values()))
    return None


def resolve_pack(
    config_dir: Path,
    *,
    scenario: str = "",
    question: str = "",
    proceed_with_gaps: bool = False,
    hitl_enabled: bool = True,
) -> Optional[ScenarioPack]:
    found = resolve_scenario_token(config_dir, scenario)
    if found is None and not (scenario or "").strip():
        found = match_from_utterance(config_dir, question)
    if found is not None:
        return found
    if proceed_with_gaps or not hitl_enabled:
        return get_pack(config_dir, "default")
    return None


def default_pack(config_dir: Path) -> ScenarioPack:
    return get_pack(config_dir, "default")
