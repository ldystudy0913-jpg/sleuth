"""从本包 agent.md + skills 组装 Agent Card JSON。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

_PACK_ROOT = Path(__file__).resolve().parents[1]


def _parse_agent_md(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"prompt": text.strip(), "permission": {}, "description": "", "title": "", "mode": "primary"}
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


def load_agent_card(*, server_name: str = "ddreply") -> Dict[str, Any]:
    agent_path = _PACK_ROOT / "agent.md"
    skill_path = _PACK_ROOT / "skills" / "dd-reply-framework" / "SKILL.md"
    parsed = _parse_agent_md(agent_path.read_text(encoding="utf-8"))
    skills: List[Dict[str, Any]] = []
    if skill_path.is_file():
        skill_text = skill_path.read_text(encoding="utf-8")
        skill_desc = ""
        skill_name = "dd-reply-framework"
        skill_body = skill_text
        if skill_text.startswith("---"):
            parts = skill_text.split("---", 2)
            if len(parts) >= 3:
                fm, skill_body = parts[1], parts[2].lstrip("\n")
                for line in fm.splitlines():
                    if line.startswith("name:"):
                        skill_name = line.split(":", 1)[1].strip() or skill_name
                    if line.startswith("description:"):
                        skill_desc = line.split(":", 1)[1].strip()
                if not skill_desc:
                    skill_desc = "尽调答复框架生成 SOP"
        skills.append(
            {
                "name": skill_name,
                "description": skill_desc or "尽调答复框架生成 SOP",
                "content": skill_body if skill_body.strip() else skill_text,
                "mcp": [server_name],
            }
        )
    return {
        "name": "dd_reply",
        "title": parsed.get("title") or "尽调答复框架生成助手",
        "description": parsed.get("description") or "尽调答复框架生成助手",
        "mode": parsed.get("mode") or "primary",
        "prompt": parsed.get("prompt") or "",
        "permission": dict(parsed.get("permission") or {}),
        "skills": skills,
        "mcp_server": server_name,
        "version": "1",
    }
