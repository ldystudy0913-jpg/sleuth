"""Apply agent / model / skill selectors from an HTTP request body."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

SKILL_ONLY_DEFAULT_ERROR = "skill only allowed when agent is the default agent"
SKILL_CLEAR_TOKENS = frozenset({"", "off", "none", "default"})


def optional_str(body: Dict[str, Any], key: str) -> Tuple[bool, str]:
    """Return (present, stripped value). Missing or JSON null is not present."""
    if key not in body:
        return False, ""
    raw = body[key]
    if raw is None:
        return False, ""
    return True, str(raw).strip()


def parse_skill_names(raw: Any) -> List[str]:
    """Parse skill names from a string, list, or nested mix.

    Splits on commas and whitespace. Empty / off / none / default tokens are
    dropped. Order is preserved; duplicates are removed.
    """
    parts: List[str] = []
    if raw is None:
        return []
    if isinstance(raw, str):
        for chunk in raw.replace(",", " ").split():
            token = chunk.strip()
            if token:
                parts.append(token)
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            parts.extend(parse_skill_names(item))
    else:
        token = str(raw).strip()
        if token:
            parts.append(token)
    seen = set()
    out: List[str] = []
    for token in parts:
        if token.lower() in SKILL_CLEAR_TOKENS:
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def optional_skill_names(body: Dict[str, Any], key: str) -> Tuple[bool, List[str]]:
    """Return (present, names). Missing or JSON null is not present."""
    if key not in body:
        return False, []
    raw = body[key]
    if raw is None:
        return False, []
    return True, parse_skill_names(raw)


def skills_from_metadata(metadata: Any) -> List[str]:
    """Restore pinned skill names; prefer ``skills`` list, else ``skill`` string."""
    if not isinstance(metadata, dict):
        return []
    raw_list = metadata.get("skills")
    if isinstance(raw_list, list) and raw_list:
        names = parse_skill_names(raw_list)
        if names:
            return names
    raw = metadata.get("skill")
    if isinstance(raw, str) and raw.strip():
        return parse_skill_names(raw)
    if isinstance(raw, list) and raw:
        return parse_skill_names(raw)
    return []


def skill_from_metadata(metadata: Any) -> Optional[str]:
    names = skills_from_metadata(metadata)
    return names[0] if names else None


def _pinned_names(sess: Any) -> List[str]:
    names = getattr(sess, "skill_names", None)
    if isinstance(names, (list, tuple)) and names:
        return [str(n).strip() for n in names if str(n).strip()]
    name = getattr(sess, "skill_name", None)
    if isinstance(name, str) and name.strip():
        return [name.strip()]
    return []


def _apply_skills(sess: Any, names: List[str]) -> None:
    if hasattr(sess, "set_skills"):
        sess.set_skills(names)
        return
    if not names:
        sess.set_skill("")
        return
    sess.set_skill(names[0])


def apply_session_selectors(sess: Any, body: Dict[str, Any], config: Any) -> Optional[str]:
    """Apply body agent/model/skill onto ``sess``. Return an error string or None.

    Present non-empty values are applied. ``skill: ""`` or ``skills: []`` clears
    the pin. ``skills`` wins when both fields are present. A non-empty skill
    with a non-default agent is rejected. Switching away from the default
    agent clears any leftover pin.
    """
    present_agent, agent_val = optional_str(body, "agent")
    present_model, model_val = optional_str(body, "model")
    present_skills, skills_val = optional_skill_names(body, "skills")
    present_skill, skill_val = optional_skill_names(body, "skill")

    if present_skills:
        present_pin = True
        pin_names = skills_val
    elif present_skill:
        present_pin = True
        pin_names = skill_val
    else:
        present_pin = False
        pin_names = []

    default_agent = (getattr(config, "default_agent", None) or "build").strip()
    target_agent = sess.agent_name
    if present_agent:
        target_agent = agent_val or default_agent

    if target_agent != default_agent and present_pin and pin_names:
        return SKILL_ONLY_DEFAULT_ERROR

    if present_agent:
        try:
            sess.set_agent(target_agent, yolo=getattr(sess, "yolo", False))
        except Exception as exc:
            return f"invalid agent: {exc}"

    if present_model:
        try:
            if model_val:
                sess.set_model(model_val)
            else:
                sess.reset_model()
        except Exception as exc:
            return f"invalid model: {exc}"

    if target_agent != default_agent:
        if _pinned_names(sess):
            _apply_skills(sess, [])
    elif present_pin:
        try:
            _apply_skills(sess, pin_names)
        except ValueError as exc:
            msg = str(exc)
            if "only allowed" in msg:
                return SKILL_ONLY_DEFAULT_ERROR
            return f"invalid skill: {exc}"
    return None
