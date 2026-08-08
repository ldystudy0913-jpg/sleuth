"""从本包 agent.md + skills 组装 Agent Card JSON。

Sleuth 在 SLEUTH_MCP_SERVERS 配 agent:true 时会调 MCP 工具 get_agent_card，
用本卡注册人格/权限/技能（无需再拷 agent.md 到 .opencode）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

_PACK_ROOT = Path(__file__).resolve().parents[1]


def _parse_agent_md(text: str) -> Dict[str, Any]:
    """解析 agent.md 的 YAML frontmatter + 正文 prompt（不依赖 sleuth）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {"prompt": text.strip(), "permission": {}, "description": "", "mode": "primary"}
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
        "mode": data.get("mode", "primary") or "primary",
        "permission": permission,
        "prompt": prompt,
    }


def load_agent_card(*, server_name: str = "ddcheck") -> Dict[str, Any]:
    """产出 get_agent_card 返回体：name/prompt/permission/skills。"""
    agent_path = _PACK_ROOT / "agent.md"
    skill_path = _PACK_ROOT / "skills" / "dd-report-check" / "SKILL.md"
    parsed = _parse_agent_md(agent_path.read_text(encoding="utf-8"))
    skills: List[Dict[str, Any]] = []
    if skill_path.is_file():
        skill_text = skill_path.read_text(encoding="utf-8")
        # description from skill frontmatter if present
        skill_desc = ""
        skill_name = "dd-report-check"
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
                # multi-line description folded as single line start; keep simple
                if not skill_desc:
                    skill_desc = "尽调报告检查 SOP"
        skills.append(
            {
                "name": skill_name,
                "description": skill_desc or "尽调报告检查 SOP",
                "content": skill_body if skill_body.strip() else skill_text,
                "mcp": [server_name],
            }
        )
    # Remap bare tool names in permission to qualified names if needed
    permission = dict(parsed.get("permission") or {})
    # agent.md already uses ddcheck_* qualified names
    return {
        "name": "dd_analyst",
        "description": parsed.get("description") or "尽调报告检查分析师",
        "mode": parsed.get("mode") or "primary",
        "prompt": parsed.get("prompt") or "",
        "permission": permission,
        "skills": skills,
        "mcp_server": server_name,
        "version": "1",
    }
