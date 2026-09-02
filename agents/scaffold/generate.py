"""Copy the Sleuth agent template and fill placeholders. No extra dependencies."""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "template"
_OPTIONAL = _HERE / "optional"
_PLACEHOLDER_DIRS = ("__PKG_NAME__", "__PRIVATE_SKILL__", "__COS_SKILL__")
_SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SERVER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SKILL_MODES = ("private", "cos", "both", "none")
# Uppercase dunders only — skip __main__ / __init__ / __file__.
_LEFTOVER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")


def _pkg_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", raw.strip().lower())
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or not name[0].isalpha():
        raise ValueError("agent name must start with a letter (a-z)")
    if not _NAME_RE.fullmatch(name):
        raise ValueError(f"invalid package name: {name!r}")
    return name


def _server_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9_-]", "", raw.strip().lower())
    if not name or not name[0].isalpha():
        raise ValueError("server name must start with a letter (a-z)")
    if not _SERVER_RE.fullmatch(name):
        raise ValueError(f"invalid server name: {name!r}")
    return name


def _skill_slug(pkg: str, suffix: str) -> str:
    return pkg.replace("_", "-") + suffix


def _title_from_pkg(pkg: str) -> str:
    return pkg.replace("_", " ").title()


def _indent_block(text: str, spaces: int) -> str:
    pad = " " * spaces
    lines = text.splitlines()
    if not lines:
        return ""
    out = [lines[0]]
    out.extend(pad + line if line else line for line in lines[1:])
    return "\n".join(out)


def _optional_replacements(
    *,
    attachments: bool,
    kb: bool,
    output: bool,
    server: str,
    env_prefix: str,
) -> dict[str, str]:
    if attachments:
        pipeline_imports = (
            "from typing import List, Optional\n"
            "\n"
            "from .attachments import summarize_refs"
        )
        pipeline_ping = '''def ping(message: str, attachment_refs: Optional[List[dict]] = None) -> Dict[str, Any]:
    summary = summarize_refs(attachment_refs or [])
    return {
        "ok": True,
        "echo": message,
        **summary,
        "sources": [],
    }
'''
        ping_sig = "message: str = \"pong\", attachment_refs_json: str = \"[]\""
        ping_desc = (
            "Optional attachment_refs_json is injected by Sleuth when the schema "
            "declares it (session-file excerpts). Prefer excerpt; do not decrypt SM4."
        )
        ping_body = _indent_block(
            """try:
    refs = json.loads(attachment_refs_json) if attachment_refs_json else []
except json.JSONDecodeError:
    refs = []
if not isinstance(refs, list):
    refs = []
result = run_ping(
    message,
    attachment_refs=[r for r in refs if isinstance(r, dict)],
)""",
            8,
        )
    else:
        pipeline_imports = ""
        pipeline_ping = '''def ping(message: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "echo": message,
        "sources": [],
    }
'''
        ping_sig = "message: str = \"pong\""
        ping_desc = "Returns JSON with echo + optional sources[]."
        ping_body = "result = run_ping(message)"

    mcp_imports: list[str] = []
    registers: list[str] = []
    perm_lines = []
    howto_notes: list[str] = []
    readme_extras: list[str] = []
    agent_duties: list[str] = []
    env_lines: list[str] = []
    settings_init = ""

    if kb:
        mcp_imports.append("from .kb import register as register_kb")
        registers.append("register_kb(server, settings)")
        perm_lines.append(f"{server}_kb_search: allow")
        settings_init = (
            'self.kb_api_url: str = str('
            f'overrides.get("kb_api_url", _env("{env_prefix}_KB_API_URL", "") or "")'
            ")"
        )
        env_lines.append(f"# {env_prefix}_KB_API_URL=")
        howto_notes.append(
            "- 本包已生成 `kb.py` / 工具 `kb_search`。命中时顶层 `sources[]`（`title` + http(s) `url`）。"
            "Card 对本包 `kb_search` 为 allow，对 Sleuth `kb_lookup` 为 deny。"
            "改用基座检索时删掉 deny 并去掉本包工具。"
        )
        readme_extras.append(
            "- `--kb`：本包 `kb_search`（自己的 HTTP，不是复制 Sleuth `kb_lookup`）。"
        )
        agent_duties.append(
            f"需要知识库时调用 `{server}_kb_search`；不要调用基座 `kb_lookup`。"
            "工具 JSON 的 `sources[]` 会由 Sleuth 附在答复末尾。"
        )
    else:
        howto_notes.append(
            "- 未打开 `--kb`：无本包知识库模块。Card 对 `kb_lookup` 为 deny（专用人格默认不碰 build 的知识库）。"
        )

    if output:
        mcp_imports.append("from .output import register as register_output")
        registers.append("register_output(server, settings)")
        perm_lines.append(f"{server}_emit_file: allow")
        howto_notes.append(
            "- 本包已生成 `output.py` / 工具 `emit_file`，返回 `files[]`（`filename` + https `url` 或 `object_key`）。"
            "也可走 Sleuth `save_output_file` 写会话邮箱。不要把字节或 data-URL 写进答复。"
        )
        readme_extras.append(
            "- `--output`：`emit_file` 回传桩（`files[]` 约定）。"
        )
        agent_duties.append(
            f"回传文件用 MCP `files[]`（`{server}_emit_file`）或 Sleuth `save_output_file`；"
            "不要把字节/data-URL 写进答复。"
        )
        save_output = "allow"
    else:
        howto_notes.append(
            "- 未打开 `--output`：无本包回传模块。Card 对 `save_output_file` 为 deny。"
        )
        save_output = "deny"

    perm_lines.append("kb_lookup: deny")
    perm_lines.append(f"save_output_file: {save_output}")

    if attachments:
        howto_notes.insert(
            0,
            "- 本包已生成 `attachments.py`：优先用 Sleuth `excerpt`；无 excerpt 且未加密才允许 http(s) 下载；"
            "跳过 data:/file: 与「有密文无 excerpt」。`ping` 声明了 `attachment_refs_json`。",
        )
        readme_extras.insert(0, "- `--attachments`：会话摘录 helper；`ping` 带 `attachment_refs_json`。")
        agent_duties.insert(
            0,
            "会话附件优先读注入的 `excerpt`，不要自行解密 SM4。",
        )
    else:
        howto_notes.insert(
            0,
            "- 未打开 `--attachments`：`ping` 不声明 `attachment_refs_json`，基座不注入会话文件。",
        )

    perm_block = "\n  ".join(perm_lines)
    register_block = "\n    ".join(registers) if registers else "pass  # optional extras off"
    import_block = "\n".join(mcp_imports)
    howto_block = "\n".join(howto_notes)
    readme_block = "\n".join(readme_extras) if readme_extras else "- （本次未打开可选开关）"
    duties_block = (
        "\n".join(f"{i}. {d}" for i, d in enumerate(agent_duties, start=4))
        if agent_duties
        else ""
    )
    env_block = "\n".join(env_lines)

    return {
        "__PIPELINE_IMPORTS__": pipeline_imports,
        "__PIPELINE_PING__": pipeline_ping,
        "__PING_MCP_SIGNATURE__": ping_sig,
        "__PING_MCP_DESCRIPTION__": ping_desc,
        "__PING_MCP_BODY__": ping_body,
        "__OPTIONAL_MCP_IMPORTS__": import_block,
        "__OPTIONAL_REGISTER__": register_block,
        "__OPTIONAL_PERMISSION_LINES__": perm_block,
        "__OPTIONAL_SETTINGS_INIT__": settings_init,
        "__OPTIONAL_HOWTO_FLAG_NOTES__": howto_block,
        "__OPTIONAL_README_EXTRAS__": readme_block,
        "__OPTIONAL_AGENT_DUTIES__": duties_block,
        "__OPTIONAL_ENV__": env_block,
    }


def _replacements(
    *,
    agent_name: str,
    pkg_name: str,
    server: str,
    port: int,
    skill: str,
    title: str,
    private_skill: str,
    cos_skill: str,
    attachments: bool,
    kb: bool,
    output: bool,
) -> dict[str, str]:
    mapping = {
        "__AGENT_NAME__": agent_name,
        "__PKG_NAME__": pkg_name,
        "__DIST_NAME__": pkg_name.replace("_", "-") + "-capability",
        "__SERVER_NAME__": server,
        "__MCP_PORT__": str(port),
        "__SKILL_MODE__": skill,
        "__TITLE__": title,
        "__ENV_PREFIX__": pkg_name.upper(),
        "__PRIVATE_SKILL__": private_skill,
        "__COS_SKILL__": cos_skill,
        "__AGENT_TRUE__": "false" if skill == "none" else "true",
    }
    mapping.update(
        _optional_replacements(
            attachments=attachments,
            kb=kb,
            output=output,
            server=server,
            env_prefix=pkg_name.upper(),
        )
    )
    return mapping


def _is_text(path: Path) -> bool:
    try:
        path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    return True


def _replace_in_tree(root: Path, mapping: dict[str, str]) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        if not _is_text(path):
            continue
        text = path.read_text(encoding="utf-8")
        new = text
        for old, val in mapping.items():
            new = new.replace(old, val)
        if new != text:
            path.write_text(new, encoding="utf-8")


def _rename_placeholder_dirs(root: Path, mapping: dict[str, str]) -> None:
    dirs = sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    for path in dirs:
        if path.name not in _PLACEHOLDER_DIRS:
            continue
        dest = path.with_name(mapping[path.name])
        if dest.exists():
            raise FileExistsError(f"cannot rename {path} -> {dest}: target exists")
        path.rename(dest)


def leftover_placeholders(root: Path) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if path.is_dir() and _LEFTOVER_RE.fullmatch(path.name):
            hits.append(str(path.relative_to(root)))
            continue
        if not path.is_file() or not _is_text(path):
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if _LEFTOVER_RE.search(text):
            hits.append(str(path.relative_to(root)))
    return hits


def _copy_optional_modules(
    dest: Path,
    *,
    attachments: bool,
    kb: bool,
    output: bool,
) -> None:
    pkg_dir = dest / "__PKG_NAME__"
    if not pkg_dir.is_dir():
        raise FileNotFoundError(f"package dir missing after copy: {pkg_dir}")
    pairs = (
        (attachments, _OPTIONAL / "attachments" / "attachments.py", pkg_dir / "attachments.py"),
        (kb, _OPTIONAL / "kb" / "kb.py", pkg_dir / "kb.py"),
        (output, _OPTIONAL / "output" / "output.py", pkg_dir / "output.py"),
    )
    for enabled, src, target in pairs:
        if not enabled:
            continue
        if not src.is_file():
            raise FileNotFoundError(f"optional module missing: {src}")
        shutil.copy2(src, target)


def generate(
    *,
    name: str,
    server: str | None = None,
    port: int = 8799,
    skill: str = "private",
    out: Path | None = None,
    title: str | None = None,
    force: bool = False,
    template: Path | None = None,
    attachments: bool = False,
    kb: bool = False,
    output: bool = False,
) -> Path:
    skill = (skill or "private").strip().lower()
    if skill not in _SKILL_MODES:
        raise ValueError(f"skill must be one of {', '.join(_SKILL_MODES)}")
    pkg = _pkg_name(name)
    agent_name = pkg
    srv = _server_name(server or pkg.replace("_", ""))
    dest = Path(out) if out is not None else (_HERE.parent / pkg)
    dest = dest.resolve()
    src = (template or _TEMPLATE).resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"template not found: {src}")
    if dest.exists():
        if any(dest.iterdir()) and not force:
            raise FileExistsError(f"{dest} already exists (pass --force to replace)")
        shutil.rmtree(dest)
    mapping = _replacements(
        agent_name=agent_name,
        pkg_name=pkg,
        server=srv,
        port=int(port),
        skill=skill,
        title=(title or "").strip() or _title_from_pkg(pkg),
        private_skill=_skill_slug(pkg, "-sop"),
        cos_skill=_skill_slug(pkg, "-shared"),
        attachments=bool(attachments),
        kb=bool(kb),
        output=bool(output),
    )
    shutil.copytree(
        src,
        dest,
        ignore=lambda _dir, names: [n for n in names if n in _SKIP_DIR_NAMES],
    )
    _copy_optional_modules(
        dest,
        attachments=bool(attachments),
        kb=bool(kb),
        output=bool(output),
    )
    _replace_in_tree(dest, mapping)
    _rename_placeholder_dirs(dest, mapping)
    leftover = leftover_placeholders(dest)
    if leftover:
        raise RuntimeError("unreplaced placeholders in: " + ", ".join(leftover[:12]))
    return dest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="generate.py",
        description="Create a Sleuth MCP agent project from agents/scaffold/template.",
    )
    parser.add_argument("--name", required=True, help="Agent id, e.g. demo_ops")
    parser.add_argument("--server", default=None, help="MCP config key (qualified-name prefix)")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument(
        "--skill",
        default="private",
        choices=_SKILL_MODES,
        help="private | cos | both | none",
    )
    parser.add_argument("--out", default=None, help="Output directory (default agents/<name>)")
    parser.add_argument("--title", default=None, help="Display title for Agent Card")
    parser.add_argument("--force", action="store_true", help="Replace an existing --out directory")
    parser.add_argument(
        "--attachments",
        action="store_true",
        help="Generate session-excerpt helper and declare attachment_refs_json on ping",
    )
    parser.add_argument(
        "--kb",
        action="store_true",
        help="Generate this-package kb_search stub (sources[]); deny Sleuth kb_lookup",
    )
    parser.add_argument(
        "--output",
        action="store_true",
        help="Generate emit_file stub returning files[]; allow save_output_file",
    )
    args = parser.parse_args(argv)
    try:
        dest = generate(
            name=args.name,
            server=args.server,
            port=args.port,
            skill=args.skill,
            out=Path(args.out) if args.out else None,
            title=args.title,
            force=args.force,
            attachments=args.attachments,
            kb=args.kb,
            output=args.output,
        )
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {dest}")
    extras = [
        flag
        for flag, on in (
            ("attachments", args.attachments),
            ("kb", args.kb),
            ("output", args.output),
        )
        if on
    ]
    if extras:
        print("optional modules: " + ", ".join(extras))
    print(f"next: cd {dest} && python -m pip install -e \".[mcp]\" && python -m {dest.name}.mcp_server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
