"""Build Agent Card JSON from agent.md + skills.

SKILL_MODE is set by the scaffold generator:
  private — embed SKILL.md body in Card.skills[].content (follows the agent; no skill grant)
  cos     — Card.skills[] name only; Sleuth loads the same name from COS / paths
  both    — one private SOP plus one COS name-only reference
  none    — tools only; Card still exists but Sleuth snippet uses agent:false
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

_PACK_ROOT = Path(__file__).resolve().parents[1]

# Replaced by generate.py. Allowed: private | cos | both | none
SKILL_MODE = "__SKILL_MODE__"

AGENT_NAME = "__AGENT_NAME__"
DEFAULT_SERVER = "__SERVER_NAME__"
PRIVATE_SKILL = "__PRIVATE_SKILL__"
COS_SKILL = "__COS_SKILL__"


def _parse_agent_md(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {
            "prompt": text.strip(),
            "permission": {},
            "description": "",
            "title": "",
            "mode": "primary",
        }
    data: Dict[str, Any] = {}
    i = 1
    perm_lines: List[str] = []
    in_perm = False
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            i += 1
            break
        if in_perm:
            if line.startswith("  ") or line.startswith("\t"):
                perm_lines.append(line.strip())
                i += 1
                continue
            in_perm = False
        if line.strip().startswith("permission:"):
            in_perm = True
            i += 1
            continue
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            data[k.strip()] = v.strip()
        i += 1
    prompt = "\n".join(lines[i:]).strip()
    permission: Dict[str, str] = {}
    for pl in perm_lines:
        if ":" not in pl:
            continue
        pk, _, pv = pl.partition(":")
        permission[pk.strip()] = pv.strip()
    return {
        "description": data.get("description", ""),
        "title": data.get("title", ""),
        "mode": data.get("mode", "primary") or "primary",
        "permission": permission,
        "prompt": prompt,
    }


def _parse_skill_md(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    skill_name = path.parent.name
    skill_desc = ""
    skill_body = text
    tools: List[str] = []
    mcp: List[str] = []
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm, skill_body = parts[1], parts[2].lstrip("\n")
            in_list = None
            for line in fm.splitlines():
                stripped = line.strip()
                if stripped.startswith("name:"):
                    skill_name = stripped.split(":", 1)[1].strip() or skill_name
                    in_list = None
                elif stripped.startswith("description:"):
                    skill_desc = stripped.split(":", 1)[1].strip()
                    in_list = None
                elif stripped.startswith("mcp:"):
                    in_list = mcp
                elif stripped.startswith("tools:"):
                    in_list = tools
                elif stripped.startswith("- ") and in_list is not None:
                    in_list.append(stripped[2:].strip())
                else:
                    in_list = None
    return {
        "name": skill_name,
        "description": skill_desc,
        "body": skill_body if skill_body.strip() else text,
        "tools": tools,
        "mcp": mcp,
    }


def _private_entry(server_name: str) -> Optional[Dict[str, Any]]:
    parsed = _parse_skill_md(_PACK_ROOT / "skills" / PRIVATE_SKILL / "SKILL.md")
    if parsed is None:
        return None
    entry: Dict[str, Any] = {
        "name": parsed["name"],
        "description": parsed["description"] or "Private SOP for this agent",
        "content": parsed["body"],
        "mcp": parsed["mcp"] or [server_name],
    }
    if parsed["tools"]:
        entry["tools"] = parsed["tools"]
    return entry


def _cos_entry(server_name: str) -> Optional[Dict[str, Any]]:
    parsed = _parse_skill_md(_PACK_ROOT / "skills_cos" / COS_SKILL / "SKILL.md")
    name = parsed["name"] if parsed else COS_SKILL
    desc = (parsed["description"] if parsed else "") or "Shared SOP loaded from COS / skill paths"
    entry: Dict[str, Any] = {
        "name": name,
        "description": desc,
        "mcp": (parsed["mcp"] if parsed else None) or [server_name],
    }
    if parsed and parsed["tools"]:
        entry["tools"] = parsed["tools"]
    return entry


def load_agent_card(*, server_name: str = DEFAULT_SERVER) -> Dict[str, Any]:
    agent_path = _PACK_ROOT / "agent.md"
    parsed = _parse_agent_md(agent_path.read_text(encoding="utf-8"))
    mode = (SKILL_MODE or "private").strip().lower()
    skills: List[Dict[str, Any]] = []
    if mode in ("private", "both"):
        item = _private_entry(server_name)
        if item is not None:
            skills.append(item)
    if mode in ("cos", "both"):
        item = _cos_entry(server_name)
        if item is not None:
            skills.append(item)
    return {
        "name": AGENT_NAME,
        "title": parsed.get("title") or "__TITLE__",
        "description": parsed.get("description") or "__TITLE__",
        "mode": parsed.get("mode") or "primary",
        "prompt": parsed.get("prompt") or "",
        "permission": dict(parsed.get("permission") or {}),
        "skills": skills,
        "mcp_server": server_name,
        "version": "1",
    }
