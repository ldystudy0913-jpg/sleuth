"""Copy the Sleuth agent template and fill placeholders. No extra dependencies."""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TEMPLATE = _HERE / "template"
_PLACEHOLDER_DIRS = ("__PKG_NAME__", "__PRIVATE_SKILL__", "__COS_SKILL__")
_SKIP_DIR_NAMES = {".git", "__pycache__", ".venv", "venv", "node_modules"}

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SERVER_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_SKILL_MODES = ("private", "cos", "both", "none")


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
) -> dict[str, str]:
    return {
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
    needles = (
        "__AGENT_NAME__",
        "__PKG_NAME__",
        "__DIST_NAME__",
        "__SERVER_NAME__",
        "__MCP_PORT__",
        "__SKILL_MODE__",
        "__TITLE__",
        "__ENV_PREFIX__",
        "__PRIVATE_SKILL__",
        "__COS_SKILL__",
        "__AGENT_TRUE__",
    )
    for path in root.rglob("*"):
        if path.is_dir() and path.name in needles:
            hits.append(str(path.relative_to(root)))
            continue
        if not path.is_file() or not _is_text(path):
            continue
        if any(part in _SKIP_DIR_NAMES for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8")
        if any(n in text for n in needles):
            hits.append(str(path.relative_to(root)))
    return hits


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
    )
    shutil.copytree(
        src,
        dest,
        ignore=lambda _dir, names: [n for n in names if n in _SKIP_DIR_NAMES],
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
        )
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {dest}")
    print(f"next: cd {dest} && python -m pip install -e \".[mcp]\" && python -m {dest.name}.mcp_server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
