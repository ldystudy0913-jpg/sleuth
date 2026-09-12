"""从 agent.md + skills/ + catalog_skills 拼 Agent Card。二次开发不要改本文件，改 agent.md。

打开 KB / output 时会自动给 {server}_kb_search / {server}_emit_file 补 allow。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

_PACK_ROOT = Path(__file__).resolve().parents[1]

AGENT_NAME = "__AGENT_NAME__"
DEFAULT_SERVER = "__SERVER_NAME__"


def _empty_agent_md(prompt: str) -> Dict[str, Any]:
    """没有 YAML 头时的回退结构。"""
    return {
        "prompt": prompt.strip(),
        "permission": {},
        "description": "",
        "title": "",
        "mode": "primary",
        "catalog_skills": [],
    }


def _step_perm(state: Dict[str, Any], line: str) -> bool:
    """解析 agent.md 里 permission: 块的一行。"""
    if not state["in_perm"]:
        return False
    if line.startswith("  ") or line.startswith("\t"):
        state["perm_lines"].append(line.strip())
        return True
    state["in_perm"] = False
    return False


def _step_catalog_item(state: Dict[str, Any], line: str) -> bool:
    """解析 catalog_skills 列表项。"""
    if not state["in_catalog"]:
        return False
    stripped = line.strip()
    if stripped.startswith("- "):
        name = stripped[2:].strip()
        if name and not name.startswith("#"):
            state["catalog"].append(name)
        return True
    if line.startswith("  ") or line.startswith("\t"):
        return True
    state["in_catalog"] = False
    return False


def _start_catalog(state: Dict[str, Any], line: str) -> bool:
    """遇到 catalog_skills: 进入列表状态。"""
    if not line.strip().startswith("catalog_skills:"):
        return False
    state["in_catalog"] = True
    rest = line.split(":", 1)[1].strip()
    if rest.startswith("[") and rest.endswith("]"):
        for part in rest[1:-1].split(","):
            token = part.strip().strip("'").strip('"')
            if token:
                state["catalog"].append(token)
        state["in_catalog"] = False
    return True


def _parse_permission_lines(perm_lines: List[str]) -> Dict[str, str]:
    """permission 行转成 {合格工具名: allow|ask|deny}。"""
    permission: Dict[str, str] = {}
    for pl in perm_lines:
        if ":" not in pl:
            continue
        pk, _, pv = pl.partition(":")
        permission[pk.strip()] = pv.strip()
    return permission


def _dedupe_names(names: List[str]) -> List[str]:
    """保持顺序去重。"""
    seen = set()
    out: List[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _parse_agent_md(text: str) -> Dict[str, Any]:
    """拆 agent.md 的 YAML 头与人设正文。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return _empty_agent_md(text)
    state: Dict[str, Any] = {
        "data": {},
        "perm_lines": [],
        "catalog": [],
        "in_perm": False,
        "in_catalog": False,
    }
    i = 1
    while i < len(lines):
        line = lines[i]
        if line.strip() == "---":
            i += 1
            break
        if _step_perm(state, line) or _step_catalog_item(state, line):
            i += 1
            continue
        if line.strip().startswith("permission:"):
            state["in_perm"] = True
            i += 1
            continue
        if _start_catalog(state, line):
            i += 1
            continue
        if ":" in line and not line.startswith(" "):
            k, _, v = line.partition(":")
            state["data"][k.strip()] = v.strip()
        i += 1
    data = state["data"]
    return {
        "description": data.get("description", ""),
        "title": data.get("title", ""),
        "mode": data.get("mode", "primary") or "primary",
        "permission": _parse_permission_lines(state["perm_lines"]),
        "prompt": "\n".join(lines[i:]).strip(),
        "catalog_skills": _dedupe_names(state["catalog"]),
    }


def _parse_skill_md(path: Path) -> Optional[Dict[str, Any]]:
    """读 SKILL.md；正文为空则返回 None（不嵌入 Card）。"""
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
    """扫描本包 skills/*/SKILL.md，带 content 嵌入 Card。"""
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
    """按 env 给 kb_search / emit_file 补 allow；默认 deny 基座 kb_lookup / save_output_file。"""
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
    """给 get_agent_card 用。改人设请改 agent.md，不要改这里。"""
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
