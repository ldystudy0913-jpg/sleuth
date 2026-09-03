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
_PLACEHOLDER_DIRS = ("__PKG_NAME__", "__SKILL_SLUG__")
_SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SERVER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
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


def _skill_slug(pkg: str) -> str:
    return pkg.replace("_", "-") + "-sop"


def _title_from_pkg(pkg: str) -> str:
    return pkg.replace("_", " ").title()


def _replacements(
    *,
    agent_name: str,
    pkg_name: str,
    server: str,
    port: int,
    title: str,
    skill_slug: str,
    tools_only: bool,
) -> dict[str, str]:
    return {
        "__AGENT_NAME__": agent_name,
        "__PKG_NAME__": pkg_name,
        "__DIST_NAME__": pkg_name.replace("_", "-") + "-capability",
        "__SERVER_NAME__": server,
        "__MCP_PORT__": str(port),
        "__TITLE__": title,
        "__ENV_PREFIX__": pkg_name.upper(),
        "__SKILL_SLUG__": skill_slug,
        "__AGENT_TRUE__": "false" if tools_only else "true",
    }


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


def _copy_capability_modules(dest: Path) -> None:
    pkg_dir = dest / "__PKG_NAME__"
    if not pkg_dir.is_dir():
        raise FileNotFoundError(f"package dir missing after copy: {pkg_dir}")
    pairs = (
        (_OPTIONAL / "attachments" / "attachments.py", pkg_dir / "attachments.py"),
        (_OPTIONAL / "kb" / "kb.py", pkg_dir / "kb.py"),
        (_OPTIONAL / "output" / "output.py", pkg_dir / "output.py"),
    )
    for src, target in pairs:
        if not src.is_file():
            raise FileNotFoundError(f"capability module missing: {src}")
        shutil.copy2(src, target)


def generate(
    *,
    name: str,
    server: str | None = None,
    port: int = 8799,
    out: Path | None = None,
    title: str | None = None,
    force: bool = False,
    template: Path | None = None,
    tools_only: bool = False,
) -> Path:
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
        title=(title or "").strip() or _title_from_pkg(pkg),
        skill_slug=_skill_slug(pkg),
        tools_only=bool(tools_only),
    )
    shutil.copytree(
        src,
        dest,
        ignore=lambda _dir, names: [n for n in names if n in _SKIP_DIR_NAMES],
    )
    skills_cos = dest / "skills_cos"
    if skills_cos.is_dir():
        shutil.rmtree(skills_cos)
    _copy_capability_modules(dest)
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
        "--tools-only",
        action="store_true",
        help="Snippet uses agent:false (tools on every session, no dedicated persona)",
    )
    parser.add_argument("--out", default=None, help="Output directory (default agents/<name>)")
    parser.add_argument("--title", default=None, help="Display title for Agent Card")
    parser.add_argument("--force", action="store_true", help="Replace an existing --out directory")
    args = parser.parse_args(argv)
    try:
        dest = generate(
            name=args.name,
            server=args.server,
            port=args.port,
            out=Path(args.out) if args.out else None,
            title=args.title,
            force=args.force,
            tools_only=args.tools_only,
        )
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {dest}")
    print(f"next: cd {dest} && python -m pip install -e \".[mcp]\" && python -m {dest.name}.mcp_server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
