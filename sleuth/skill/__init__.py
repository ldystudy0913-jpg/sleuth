"""Skill discovery — local paths, HTTP urls, and S3 (boto3).

Supports multiple skills per source (zip with many SKILL.md, S3 prefix listing).
Server-side refresh uses ETag / LastModified and atomic catalog swap.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import shutil
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, TYPE_CHECKING

from ..util.markdown_fm import parse_file

if TYPE_CHECKING:
    from ..config import Config, SkillS3Entry, SkillsConfig
    from ..mcp.manager import McpManager

log = logging.getLogger("sleuth.skill")


@dataclass
class SkillInfo:
    name: str
    description: str
    location: Path
    content: str
    required_mcp: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    owner_agent: Optional[str] = None


@dataclass
class _CacheMeta:
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    path: Optional[str] = None


# Process-wide catalog + refresh state
_SKILLS: Dict[str, SkillInfo] = {}
_LOCK = threading.Lock()
_LAST_REFRESH: float = 0.0
_ETAGS: Dict[str, _CacheMeta] = {}
_CONFIG_REF: Optional["Config"] = None
_CWD: Optional[Path] = None

# Single-flight refresh gate (separate from _LOCK so catalog reads stay short)
_REFRESH_GATE = threading.Lock()
_REFRESH_EVENT: Optional[threading.Event] = None


def _expand(path: str, cwd: Path) -> Path:
    expanded = Path(os.path.expanduser(path))
    if not expanded.is_absolute():
        expanded = cwd / expanded
    return expanded.resolve()


def _scan_dir(root: Path, pattern: str = "**/SKILL.md") -> List[Path]:
    if not root.is_dir():
        return []
    return sorted(root.glob(pattern))


def _global_skill_roots() -> List[Path]:
    home = Path.home()
    return [
        home / ".config" / "sleuth" / "skills",
        home / ".config" / "sleuth" / "skill",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
    ]


def _cache_dir() -> Path:
    base = os.environ.get("SLEUTH_DATA_DIR")
    if base:
        p = Path(base) / "skills-cache"
    elif os.name == "nt":
        p = Path.home() / "AppData" / "Local" / "sleuth" / "skills-cache"
    else:
        p = Path.home() / ".local" / "share" / "sleuth" / "skills-cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


class _CacheFileLock:
    """Cross-process exclusive lock for one cache_key (stdlib only)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None

    def __enter__(self) -> "_CacheFileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+b")
        if os.name == "nt":
            import msvcrt

            self._fh.seek(0)
            if self._fh.read(1) == b"":
                self._fh.write(b"0")
                self._fh.flush()
            self._fh.seek(0)
            while True:
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._fh.seek(0)
                try:
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None


def _safe_slug(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    cleaned = "".join(c if c.isalnum() else "_" for c in key)[-48:]
    return f"{cleaned}_{digest}" or digest


def _url_cache_key(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"url:{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _meta_path(dest: Path) -> Path:
    return dest.parent / (dest.name + ".meta.json")


def _read_meta(dest: Path) -> _CacheMeta:
    mp = _meta_path(dest)
    if not mp.is_file():
        return _CacheMeta()
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
        return _CacheMeta(
            etag=data.get("etag"),
            last_modified=data.get("last_modified"),
            path=data.get("path"),
        )
    except Exception:
        return _CacheMeta()


def _write_meta(dest: Path, meta: _CacheMeta) -> None:
    mp = _meta_path(dest)
    mp.write_text(
        json.dumps(
            {"etag": meta.etag, "last_modified": meta.last_modified, "path": str(dest)},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _dir_looks_materialized(dest: Path) -> bool:
    if not dest.is_dir():
        return False
    try:
        next(dest.rglob("SKILL.md"))
        return True
    except StopIteration:
        return False


def _atomic_swap_dir(tmp: Path, dest: Path) -> None:
    """Replace dest with tmp atomically on the same volume; remove previous dest."""
    token = f"{os.getpid()}-{time.time_ns()}"
    old = dest.parent / f".{dest.name}.old-{token}"
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        try:
            dest.rename(old)
        except OSError:
            shutil.move(str(dest), str(old))
        try:
            tmp.rename(dest)
        except OSError:
            if not dest.exists() and old.exists():
                try:
                    old.rename(dest)
                except OSError:
                    pass
            raise
        shutil.rmtree(old, ignore_errors=True)
    else:
        tmp.rename(dest)


def _materialize_bytes(data: bytes, cache_key: str) -> Optional[Path]:
    """Write zip or SKILL.md bytes into cache; return extracted root.

    Uses a per-cache_key file lock and extracts into a temp dir then swaps,
    so concurrent workers never expose a half-written tree.
    """
    is_zip = data[:2] == b"PK"
    is_md = b"---" in data[:200] or data.lstrip().startswith(b"#")
    if not is_zip and not is_md:
        # manifest JSON listing relative skill roots is handled elsewhere
        log.warning("skill materialize: unsupported payload for %s", cache_key)
        return None

    cache = _cache_dir()
    slug = _safe_slug(cache_key)
    dest = cache / slug
    lock_path = cache / f"{slug}.lock"
    token = f"{os.getpid()}-{time.time_ns()}"
    tmp = cache / f".{slug}.tmp-{token}"
    had_dest = _dir_looks_materialized(dest)

    with _CacheFileLock(lock_path):
        # First-publish race: peer finished while we waited — reuse their tree.
        if not had_dest and _dir_looks_materialized(dest):
            return dest

        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            if is_zip:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    zf.extractall(tmp)
            else:
                (tmp / "SKILL.md").write_bytes(data)
            _atomic_swap_dir(tmp, dest)
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        return dest


def _collect_from_root(root: Path, found: Dict[str, SkillInfo]) -> None:
    for path in _scan_dir(root):
        info = _load_skill_file(path)
        if info:
            found[info.name] = info


def _pull_url(url: str, *, force: bool = False) -> Optional[Path]:
    cache_key = _url_cache_key(url)
    dest = _cache_dir() / _safe_slug(cache_key)
    meta = _read_meta(dest)

    headers = {"User-Agent": "sleuth"}
    if not force and meta.etag:
        headers["If-None-Match"] = meta.etag
    if not force and meta.last_modified:
        headers["If-Modified-Since"] = meta.last_modified

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = getattr(resp, "status", 200)
            if status == 304 and dest.is_dir():
                return dest
            data = resp.read()
            etag = resp.headers.get("ETag")
            last_mod = resp.headers.get("Last-Modified")
    except Exception as exc:
        log.warning("skill url fetch failed %s: %s", url, exc)
        if dest.is_dir():
            return dest
        return None

    pulled = _materialize_bytes(data, cache_key)
    if pulled is None:
        return dest if dest.is_dir() else None
    _write_meta(pulled, _CacheMeta(etag=etag, last_modified=last_mod, path=str(pulled)))
    _ETAGS[cache_key] = _CacheMeta(etag=etag, last_modified=last_mod, path=str(pulled))
    return pulled


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    # s3://bucket/key
    raw = uri[5:] if uri.startswith("s3://") else uri
    bucket, _, key = raw.partition("/")
    return bucket, key


def _s3_client(region: Optional[str] = None, endpoint_url: Optional[str] = None):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "skills.s3 configured but boto3 is not installed; "
            'pip install "sleuth[s3]" or pip install boto3'
        ) from exc
    kwargs: Dict[str, Any] = {}
    if region:
        kwargs["region_name"] = region
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("s3", **kwargs)


def _is_prefix_entry(entry: "SkillS3Entry") -> bool:
    if entry.prefix:
        return True
    if entry.uri and entry.uri.rstrip().endswith("/"):
        return True
    key = entry.key or ""
    if entry.uri and not entry.key:
        _, key = _parse_s3_uri(entry.uri)
    return bool(key.endswith("/"))


def _entry_bucket_key(entry: "SkillS3Entry") -> tuple[str, str, Optional[str]]:
    region = entry.region
    if entry.uri:
        bucket, key = _parse_s3_uri(entry.uri)
        return bucket, key, region
    if not entry.bucket:
        raise ValueError("skills.s3 entry needs uri or bucket")
    key = entry.prefix or entry.key or ""
    return entry.bucket, key, region


def _pull_s3_object(
    client: Any,
    bucket: str,
    key: str,
    *,
    force: bool = False,
) -> Optional[Path]:
    cache_key = f"s3:{bucket}/{key}"
    dest = _cache_dir() / _safe_slug(cache_key)
    meta = _read_meta(dest)

    try:
        head = client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        log.warning("skill s3 head failed s3://%s/%s: %s", bucket, key, exc)
        return dest if dest.is_dir() else None

    etag = (head.get("ETag") or "").strip('"')
    last_mod = head.get("LastModified")
    last_mod_s = last_mod.isoformat() if hasattr(last_mod, "isoformat") else str(last_mod or "")

    if (
        not force
        and dest.is_dir()
        and meta.etag
        and meta.etag == etag
    ):
        return dest

    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
    except Exception as exc:
        log.warning("skill s3 get failed s3://%s/%s: %s", bucket, key, exc)
        return dest if dest.is_dir() else None

    # manifest JSON
    if key.endswith(".json") or (data[:1] == b"{" and b"keys" in data[:200]):
        try:
            manifest = json.loads(data.decode("utf-8"))
        except Exception:
            manifest = None
        if isinstance(manifest, dict) and isinstance(manifest.get("keys"), list):
            # materialize nothing; caller expands keys
            return None

    pulled = _materialize_bytes(data, cache_key)
    if pulled is None:
        return dest if dest.is_dir() else None
    _write_meta(pulled, _CacheMeta(etag=etag, last_modified=last_mod_s, path=str(pulled)))
    _ETAGS[cache_key] = _CacheMeta(etag=etag, last_modified=last_mod_s, path=str(pulled))
    return pulled


def _load_manifest_keys(client: Any, bucket: str, key: str) -> List[str]:
    try:
        obj = client.get_object(Bucket=bucket, Key=key)
        data = obj["Body"].read()
        manifest = json.loads(data.decode("utf-8"))
    except Exception as exc:
        log.warning("skill s3 manifest failed s3://%s/%s: %s", bucket, key, exc)
        return []
    keys = manifest.get("keys") if isinstance(manifest, dict) else None
    if not isinstance(keys, list):
        return []
    return [str(k) for k in keys if k]


def _expand_s3_entry(entry: "SkillS3Entry", *, force: bool = False) -> List[Path]:
    endpoint = os.environ.get("AWS_ENDPOINT_URL") or os.environ.get("SLEUTH_S3_ENDPOINT")
    bucket, key, region = _entry_bucket_key(entry)
    client = _s3_client(region=region, endpoint_url=endpoint)
    roots: List[Path] = []

    if entry.manifest or key.endswith(".json"):
        for child_key in _load_manifest_keys(client, bucket, key):
            if child_key.startswith("s3://"):
                b2, k2 = _parse_s3_uri(child_key)
                pulled = _pull_s3_object(client, b2, k2, force=force)
            else:
                pulled = _pull_s3_object(client, bucket, child_key, force=force)
            if pulled is not None:
                roots.append(pulled)
        return roots

    if _is_prefix_entry(entry):
        prefix = entry.prefix or key
        try:
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents") or []:
                    obj_key = obj.get("Key") or ""
                    if obj_key.endswith("/"):
                        continue
                    if not (
                        obj_key.endswith(".zip")
                        or obj_key.endswith("SKILL.md")
                        or obj_key.endswith(".md")
                    ):
                        continue
                    pulled = _pull_s3_object(client, bucket, obj_key, force=force)
                    if pulled is not None:
                        roots.append(pulled)
        except Exception as exc:
            log.warning("skill s3 list failed s3://%s/%s: %s", bucket, prefix, exc)
        return roots

    pulled = _pull_s3_object(client, bucket, key, force=force)
    if pulled is not None:
        roots.append(pulled)
    return roots


def _load_skill_file(path: Path) -> Optional[SkillInfo]:
    try:
        md = parse_file(path)
    except OSError:
        return None
    name = md.data.get("name")
    if not name:
        name = path.parent.name
    if not isinstance(name, str) or not name.strip():
        return None
    description = md.data.get("description") or ""
    if not isinstance(description, str):
        description = str(description)

    mcp_req = md.data.get("mcp") or []
    if isinstance(mcp_req, str):
        mcp_req = [mcp_req]
    if not isinstance(mcp_req, list):
        mcp_req = []

    tools_req = md.data.get("tools") or []
    if isinstance(tools_req, str):
        tools_req = [tools_req]
    if not isinstance(tools_req, list):
        tools_req = []

    return SkillInfo(
        name=name.strip(),
        description=description.strip(),
        location=path,
        content=md.content,
        required_mcp=[str(x) for x in mcp_req],
        required_tools=[str(x) for x in tools_req],
    )


def discover_skills(
    config: "Config",
    cwd: Optional[Path] = None,
    *,
    force: bool = False,
) -> Dict[str, SkillInfo]:
    """Discover skills from global dirs, paths, urls, and s3."""
    cwd = cwd or Path.cwd()
    found: Dict[str, SkillInfo] = {}

    roots: List[Path] = []
    for r in _global_skill_roots():
        roots.append(r)

    for name in ("skill", "skills"):
        p = cwd / ".sleuth" / name
        if p.is_dir():
            roots.append(p)

    for raw in config.skills.paths:
        roots.append(_expand(raw, cwd))

    for root in roots:
        _collect_from_root(root, found)

    for url in config.skills.urls:
        pulled = _pull_url(url, force=force)
        if pulled is None:
            log.warning("skill url skipped: %s", url)
            continue
        _collect_from_root(pulled, found)

    for entry in config.skills.s3:
        try:
            for pulled in _expand_s3_entry(entry, force=force):
                _collect_from_root(pulled, found)
        except Exception as exc:
            log.warning("skill s3 entry failed: %s", exc)

    return found


def refresh_skills(
    config: Optional["Config"] = None,
    cwd: Optional[Path] = None,
    *,
    force: bool = False,
) -> Dict[str, SkillInfo]:
    """Re-discover and atomically swap the process catalog (single-flight)."""
    return _refresh_single_flight(config, cwd, force=force)


def _refresh_single_flight(
    config: Optional["Config"],
    cwd: Optional[Path],
    *,
    force: bool,
) -> Dict[str, SkillInfo]:
    """Only one discover runs at a time; concurrent callers wait and share the result."""
    global _LAST_REFRESH, _CONFIG_REF, _CWD, _REFRESH_EVENT

    wait_event: Optional[threading.Event] = None
    is_leader = False
    with _REFRESH_GATE:
        if _REFRESH_EVENT is not None:
            wait_event = _REFRESH_EVENT
        else:
            is_leader = True
            _REFRESH_EVENT = threading.Event()

    if wait_event is not None:
        wait_event.wait()
        return get_skills()

    cfg = config or _CONFIG_REF
    if cfg is None:
        from ..config import load

        cfg = load(cwd)
    work = cwd or _CWD or Path.cwd()

    try:
        skills = discover_skills(cfg, work, force=force)
        set_skills(skills)
        try:
            from ..catalog import merge_live_mcp_skills

            merge_live_mcp_skills(cfg)
        except Exception as exc:
            log.debug("mcp card skill merge skipped: %s", exc)
        _CONFIG_REF = cfg
        _CWD = work
        _LAST_REFRESH = time.time()
        return get_skills()
    finally:
        with _REFRESH_GATE:
            ev = _REFRESH_EVENT
            _REFRESH_EVENT = None
            if ev is not None:
                ev.set()


def ensure_skills_fresh(config: "Config", cwd: Optional[Path] = None) -> Dict[str, SkillInfo]:
    """Refresh if TTL elapsed; otherwise return current catalog."""
    global _CONFIG_REF, _CWD
    ttl = int(getattr(config.skills, "refresh_seconds", 0) or 0)
    with _LOCK:
        empty = not _SKILLS
        stale = ttl > 0 and (time.time() - _LAST_REFRESH) >= ttl
        if not empty and not stale:
            _CONFIG_REF = config
            _CWD = cwd or Path.cwd()
            return dict(_SKILLS)
    try:
        return _refresh_single_flight(config, cwd, force=False)
    except Exception as exc:
        log.error("skill refresh failed; keeping previous catalog: %s", exc)
        return get_skills()


def check_skill_deps(
    skill: SkillInfo,
    *,
    tool_names: Sequence[str],
    mcp_manager: Optional["McpManager"] = None,
) -> List[str]:
    """Return human-readable warnings for unmet mcp/tools frontmatter deps."""
    warnings: List[str] = []
    available = set(tool_names)
    if mcp_manager is not None:
        connected = set(mcp_manager._sessions.keys())  # noqa: SLF001
    else:
        connected = set()

    for name in skill.required_mcp:
        if name not in connected:
            warnings.append(f"required MCP server not connected: {name}")

    for name in skill.required_tools:
        if name in available:
            continue
        if any(t == name or t.startswith(name + "_") or t.startswith(name) for t in available):
            continue
        if name.endswith("*"):
            prefix = name[:-1]
            if any(t.startswith(prefix) for t in available):
                continue
        warnings.append(f"required tool not available: {name}")
    return warnings


def set_skills(skills: Dict[str, SkillInfo]) -> None:
    global _SKILLS
    with _LOCK:
        _SKILLS = dict(skills)


def get_skills() -> Dict[str, SkillInfo]:
    with _LOCK:
        return dict(_SKILLS)


def get_skill(name: str) -> Optional[SkillInfo]:
    with _LOCK:
        return _SKILLS.get(name)
