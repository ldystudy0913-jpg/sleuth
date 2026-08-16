"""Apply agent / model / skill selectors from an HTTP request body."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

SKILL_ONLY_DEFAULT_ERROR = "skill only allowed when agent is the default agent"


def optional_str(body: Dict[str, Any], key: str) -> Tuple[bool, str]:
    """Return (present, stripped value). Missing or JSON null is not present."""
    if key not in body:
        return False, ""
    raw = body[key]
    if raw is None:
        return False, ""
    return True, str(raw).strip()


def apply_session_selectors(sess: Any, body: Dict[str, Any], config: Any) -> Optional[str]:
    """Apply body agent/model/skill onto ``sess``. Return an error string or None.

    Present non-empty values are applied. ``skill: ""`` clears the pin.
    A non-empty skill with a non-default agent is rejected. Switching away
    from the default agent clears any leftover pin.
    """
    present_agent, agent_val = optional_str(body, "agent")
    present_model, model_val = optional_str(body, "model")
    present_skill, skill_val = optional_str(body, "skill")

    default_agent = (getattr(config, "default_agent", None) or "build").strip()
    target_agent = sess.agent_name
    if present_agent:
        target_agent = agent_val or default_agent

    if target_agent != default_agent and present_skill and skill_val:
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
        if getattr(sess, "skill_name", None):
            sess.set_skill("")
    elif present_skill:
        try:
            sess.set_skill(skill_val)
        except ValueError as exc:
            msg = str(exc)
            if "only allowed" in msg:
                return SKILL_ONLY_DEFAULT_ERROR
            return f"invalid skill: {exc}"
    return None


def skill_from_metadata(metadata: Any) -> Optional[str]:
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get("skill")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None
