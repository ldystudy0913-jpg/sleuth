"""Build Agent Card JSON from agent.md + skills/ + catalog_skills names."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

_PACK_ROOT = Path(__file__).resolve().parents[1]

AGENT_NAME = "__AGENT_NAME__"
DEFAULT_SERVER = "__SERVER_NAME__"


def _parse_agent_md(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {
            "prompt": text.strip(),
            "permission": {},
            "description": "",
            "title": "",
            "mode": "primary",
            "catalog_skills": [],
        }
    data: Dict[str, Any] = {}
    i = 1
    perm_lines: List[str] = []
    catalog: List[str] = []
    in_perm = False
    in_catalog = False
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
        if in_catalog:
            stripped = line.strip()
            if stripped.startswith("- "):
                name = stripped[2:].strip()
                if name.startswith("#"):
                    i += 1
                    continue
                if name:
                    catalog.append(name)
                i += 1
                continue
            if line.startswith("  ") or line.startswith("\t"):
                i += 1
                continue
            in_catalog = False
        if line.strip().startswith("permission:"):
            in_perm = True
            i += 1
            continue
        if line.strip().startswith("catalog_skills:"):
            in_catalog = True
            rest = line.split(":", 1)[1].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                for part in inner.split(","):
                    token = part.strip().strip("'").strip('"')
                    if token:
                        catalog.append(token)
                in_catalog = False
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
    seen = set()
    catalog_skills: List[str] = []
    for name in catalog:
        if name not in seen:
            seen.add(name)
            catalog_skills.append(name)
    return {
        "description": data.get("description", ""),
        "title": data.get("title", ""),
        "mode": data.get("mode", "primary") or "primary",
        "permission": permission,
        "prompt": prompt,
        "catalog_skills": catalog_skills,
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
    body = skill_body if skill_body.strip() else ""
    if not body.strip():
        return None
    return {
        "name": skill_name,
        "description": skill_desc,
        "body": body,
        "tools": tools,
        "mcp": mcp,
    }


def _scan_local_skills(server_name: str) -> List[Dict[str, Any]]:
    root = _PACK_ROOT / "skills"
    if not root.is_dir():
        return []
    entries: List[Dict[str, Any]] = []
    seen = set()
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        parsed = _parse_skill_md(child / "SKILL.md")
        if parsed is None:
            continue
        name = parsed["name"]
        if name in seen:
            continue
        seen.add(name)
        entry: Dict[str, Any] = {
            "name": name,
            "description": parsed["description"] or "Private SOP for this agent",
            "content": parsed["body"],
            "mcp": parsed["mcp"] or [server_name],
        }
        if parsed["tools"]:
            entry["tools"] = parsed["tools"]
        entries.append(entry)
    return entries


def _apply_runtime_permissions(
    permission: Dict[str, str],
    *,
    server_name: str,
    settings: Any,
) -> Dict[str, str]:
    out = dict(permission)
    kb_key = f"{server_name}_kb_search"
    emit_key = f"{server_name}_emit_file"
    if settings is not None and getattr(settings, "kb_enabled", False):
        out.setdefault(kb_key, "allow")
    else:
        out.pop(kb_key, None)
    if settings is not None and getattr(settings, "output_enabled", False):
        out.setdefault(emit_key, "allow")
    else:
        out.pop(emit_key, None)
    out.setdefault("kb_lookup", "deny")
    out.setdefault("save_output_file", "deny")
    return out


def load_agent_card(
    *,
    server_name: str = DEFAULT_SERVER,
    settings: Any = None,
) -> Dict[str, Any]:
    agent_path = _PACK_ROOT / "agent.md"
    parsed = _parse_agent_md(agent_path.read_text(encoding="utf-8"))
    local = _scan_local_skills(server_name)
    local_names = {str(item.get("name") or "") for item in local}
    skills: List[Dict[str, Any]] = list(local)
    for name in parsed.get("catalog_skills") or []:
        if not name or name in local_names:
            continue
        skills.append(
            {
                "name": name,
                "description": "Catalog / COS skill (name only)",
                "mcp": [server_name],
            }
        )
    permission = _apply_runtime_permissions(
        dict(parsed.get("permission") or {}),
        server_name=server_name,
        settings=settings,
    )
    return {
        "name": AGENT_NAME,
        "title": parsed.get("title") or "__TITLE__",
        "description": parsed.get("description") or "__TITLE__",
        "mode": parsed.get("mode") or "primary",
        "prompt": parsed.get("prompt") or "",
        "permission": permission,
        "skills": skills,
        "mcp_server": server_name,
        "version": "1",
    }
