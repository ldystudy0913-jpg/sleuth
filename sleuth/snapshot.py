"""Checkpoint snapshots — git tree-hash of the working directory.

Port of opencode's snapshot service (packages/opencode/src/snapshot/index.ts
+ packages/core/src/snapshot.ts). A snapshot is a git tree SHA-1 capturing
the working-directory state; it is taken before/after each assistant step and
stored on the message so a step can be reverted (`git checkout` the tree).

opencode uses a *separate bare git repo* under ~/.local/share/opencode/snapshot
so the source repo's index is never touched. To keep this MVP dependency-free
and simple we use a **temporary index file** (`GIT_INDEX_FILE`) seeded from
HEAD, which achieves the same "capture working tree without polluting the
user's index" property without the bare-repo plumbing. This is a faithful
simplification of opencode's mechanism, not new behaviour.

If the workdir is not a git repo, snapshots degrade to None (the loop skips
snapshot capture/restore).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


class SnapshotError(Exception):
    pass


def is_git_repo(workdir: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=str(workdir), capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def capture(workdir: Path) -> Optional[str]:
    """Capture the working tree as a git tree hash.

    Returns the 40-char SHA-1, or None if workdir is not a git repo.
    """
    if not is_git_repo(workdir):
        return None

    # Use a throwaway index so we never mutate the source repo's real index.
    # Seed it from HEAD (so deletions are recorded) then `add -A` the working
    # tree. Mirrors opencode's "seed from source index" cold-start.
    with tempfile.NamedTemporaryFile(prefix="sleuth-snap-", suffix=".idx", delete=False) as tf:
        tmp_index = tf.name
    env = dict(os.environ, GIT_INDEX_FILE=tmp_index)
    try:
        # seed from HEAD if a commit exists (ignore failure on empty repos)
        subprocess.run(["git", "read-tree", "HEAD"], cwd=str(workdir),
                        env=env, capture_output=True, text=True, timeout=30)
        r = subprocess.run(["git", "add", "-A"], cwd=str(workdir),
                           env=env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise SnapshotError(f"git add -A failed: {r.stderr.strip()}")
        r = subprocess.run(["git", "write-tree"], cwd=str(workdir),
                           env=env, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise SnapshotError(f"git write-tree failed: {r.stderr.strip()}")
        return r.stdout.strip() or None
    except SnapshotError:
        raise
    except Exception as exc:
        raise SnapshotError(f"snapshot capture failed: {exc}")
    finally:
        try:
            os.unlink(tmp_index)
        except OSError:
            pass


def files_between(workdir: Path, from_tree: Optional[str], to_tree: Optional[str]) -> List[str]:
    """List files changed between two snapshots (opencode `patch`)."""
    if not from_tree or not to_tree:
        return []
    r = subprocess.run(
        ["git", "diff", "--name-only", from_tree, to_tree],
        cwd=str(workdir), capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def restore(workdir: Path, tree: Optional[str]) -> bool:
    """Restore the working tree + index to a captured snapshot tree.

    Uses `git read-tree -u --reset <tree>` (equivalent to a hard reset to a
    tree). Returns False if not a git repo or no tree. Untracked files that
    post-date the snapshot are left in place (a deliberate simplification vs
    opencode's per-file patch revert).
    """
    if not tree or not is_git_repo(workdir):
        return False
    r = subprocess.run(
        ["git", "read-tree", "-u", "--reset", tree],
        cwd=str(workdir), capture_output=True, text=True, timeout=120,
    )
    return r.returncode == 0


def revert_to(workdir: Path, tree: Optional[str]) -> bool:
    """Convenience alias matching opencode's `snap.restore(snapshot)`."""
    return restore(workdir, tree)
