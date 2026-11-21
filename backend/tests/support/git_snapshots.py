"""Scratch git repositories shaped like the trees the mirror predicate must tell apart.

``source_tree.mirror_snapshot`` (and its twin in ``manual/scripts/check_published.py``) says a tree
is the public mirror only when HEAD is a parentless commit carrying the trailers that
``sync-to-clean.yml``'s "Build the orphan snapshot" step writes. These builders make each shape
from a real ``git`` so the predicate is tested against commits, not stubs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SOURCE_SHA = "6193435066ff3a9ac7c387832953ec4273cdfda4"
#: What the sync's "Build the orphan snapshot" step passes to ``git commit``.
SYNC_TRAILERS = ("--trailer", f"Source-Commit: {SOURCE_SHA}", "--trailer", "Private-CI: passed")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def make_repo(root: Path, shape: str) -> Path:
    """``shape``: ``none`` (no repository), ``private`` (two plain commits), ``mirror`` (one
    parentless commit with both sync trailers), ``orphan-plain`` (parentless, no trailers),
    ``orphan-source-only`` (parentless, ``Source-Commit`` only), ``trailers-with-parent`` (both
    trailers on a commit that has a parent), ``shallow-private`` (a depth-1 clone of ``private``,
    whose HEAD shows no parent)."""
    root.mkdir(parents=True, exist_ok=True)
    if shape == "none":
        return root
    if shape == "shallow-private":
        source = make_repo(root.parent / f"{root.name}-source", "private")
        subprocess.run(
            ["git", "clone", "-q", "--depth", "1", source.as_uri(), str(root)],
            check=True,
            capture_output=True,
        )
        return root
    git(root, "init", "-q")
    (root / "README.md").write_text("x\n")
    git(root, "add", "-A")
    if shape in ("private", "trailers-with-parent"):
        git(root, "commit", "-qm", "first")
        (root / "README.md").write_text("y\n")
        git(root, "add", "-A")
    trailers: tuple[str, ...] = ()
    if shape in ("mirror", "trailers-with-parent"):
        trailers = SYNC_TRAILERS
    elif shape == "orphan-source-only":
        trailers = SYNC_TRAILERS[:2]
    elif shape not in ("private", "orphan-plain"):
        raise ValueError(shape)
    git(root, "commit", "-qm", "miDataworks - filtered snapshot", *trailers)
    return root


#: shape -> whether the predicate must call it the mirror snapshot
EXPECTED = {
    "none": False,
    "private": False,
    "shallow-private": False,
    "orphan-plain": False,
    "orphan-source-only": False,
    "trailers-with-parent": False,
    "mirror": True,
}
