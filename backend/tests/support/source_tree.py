"""What the public mirror strips, read from the sync workflow, and a loud skip for it (task 14.3).

The mirror (`hitsainet/miDataworks`) is an orphan snapshot with `0xcc/`, `AGENT.md`, most of
`docs/` and the prototype removed, and it runs the backend suite too, because its image build is
gated on that run. A test that reads a stripped file must therefore SKIP there — and must FAIL
anywhere else the file is missing. miServer found the failure mode the hard way: a guard that passed
locally failed the mirror's Backend Tests over a file the sync deletes, and the build refused to
publish.

The split is derived from ``.github/workflows/sync-to-clean.yml`` (the job's ``EXCLUDED_PATHS`` and
``DOCS_KEPT``), never from a second hand-kept list, so a test can only skip for a path the mirror
really strips. ``tests/unit/test_mirror_exclusions.py`` pins the workflow's list.

A skip also requires the tree to BE the mirror snapshot (:func:`mirror_snapshot`), so a stripped
file deleted from this repository fails here instead of skipping its guard to green: the private
CI is the gate, and nothing may skip there for want of a file the mirror lacks.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
SYNC_WORKFLOW = REPO / ".github" / "workflows" / "sync-to-clean.yml"


def sync_job() -> dict[str, Any]:
    data = yaml.safe_load(SYNC_WORKFLOW.read_text())
    if True in data:  # YAML 1.1 reads the bare key `on` as True
        data["on"] = data.pop(True)
    job: dict[str, Any] = data["jobs"]["sync"]
    return job


def excluded_paths() -> list[str]:
    return str(sync_job()["env"]["EXCLUDED_PATHS"]).split()


def docs_kept() -> list[str]:
    return str(sync_job()["env"]["DOCS_KEPT"]).split()


def stripped_by_mirror(rel: str) -> bool:
    """True when the sync workflow removes ``rel`` (a repo-relative POSIX path) from the mirror."""
    rel = rel.strip("/")
    for pattern in excluded_paths():
        if fnmatch.fnmatchcase(rel, pattern) or rel.startswith(pattern.rstrip("/") + "/"):
            return True
    parts = rel.split("/")
    if parts[0] == "docs" and len(parts) > 1:
        return parts[1] not in docs_kept()
    return False


_SOURCE_COMMIT = re.compile(r"^Source-Commit: [0-9a-f]{40}$", re.M)


def mirror_snapshot(root: Path | None = None) -> bool:
    """True only when ``root``'s HEAD is the snapshot sync-to-clean.yml publishes: a commit with
    NO parent carrying the ``Source-Commit: <sha>`` and ``Private-CI: passed`` trailers that the
    sync's "Build the orphan snapshot" step writes and the mirror's image build requires
    (docker-images.yml, job ``private-ci``). A commit of this repository has a parent and no such
    trailers; a shallow clone of it still lacks the trailers. No git, or no repository: False.

    ``manual/scripts/check_published.py`` keeps a standard-library twin of this predicate;
    ``test_manual_publish.py`` holds the two to the same answers on the same repositories.
    """
    root = REPO if root is None else root
    try:
        parents = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
        )
        trailers = subprocess.run(
            ["git", "log", "-1", "--format=%(trailers:only,unfold)", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, NotADirectoryError):
        return False
    if parents.returncode != 0 or trailers.returncode != 0:
        return False
    if len(parents.stdout.split()) != 1:
        return False
    lines = trailers.stdout.splitlines()
    return "Private-CI: passed" in lines and bool(_SOURCE_COMMIT.search(trailers.stdout))


def source_only(rel: str) -> Path:
    """The path to a file the mirror strips; skip LOUDLY when it is absent ON THE MIRROR SNAPSHOT,
    fail otherwise.

    A shipping file that is missing is a real failure, never a skip: otherwise a deleted file
    would let its guard skip its way to green. So is a stripped file missing from any tree that
    is not the mirror snapshot (this repository, a worktree, the private CI).
    """
    path = REPO / rel
    if path.exists():
        return path
    assert stripped_by_mirror(rel), (
        f"{rel} is missing, and sync-to-clean.yml does not strip it from the mirror. A shipping "
        "file must exist; this is a failure, not a skip."
    )
    assert mirror_snapshot(REPO), (
        f"{rel} is missing from a tree that is not the mirror snapshot (HEAD is not a parentless "
        "commit with 'Source-Commit' / 'Private-CI: passed' trailers). Only the mirror may lack "
        "it; this is a failure, not a skip."
    )
    pytest.skip(
        f"SOURCE-ONLY: {rel} is stripped from the public mirror by sync-to-clean.yml, so this "
        "check runs only in the private repository."
    )
