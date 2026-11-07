"""What the public mirror strips, read from the sync workflow, and a loud skip for it (task 14.3).

The mirror (`hitsainet/miDataworks`) is an orphan snapshot with `0xcc/`, `AGENT.md`, most of
`docs/` and the prototype removed, and it runs the backend suite too, because its image build is
gated on that run. A test that reads a stripped file must therefore SKIP there — and must FAIL
anywhere else the file is missing. miLLM found the failure mode the hard way: a guard that passed
locally failed the mirror's Backend Tests over a file the sync deletes, and the build refused to
publish.

The split is derived from ``.github/workflows/sync-to-clean.yml`` (the job's ``EXCLUDED_PATHS`` and
``DOCS_KEPT``), never from a second hand-kept list, so a test can only skip for a path the mirror
really strips. ``tests/unit/test_mirror_exclusions.py`` pins the workflow's list.
"""

from __future__ import annotations

import fnmatch
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


def source_only(rel: str) -> Path:
    """The path to a file the mirror strips; skip LOUDLY when it is absent, fail otherwise.

    A shipping file that is missing is a real failure, never a skip: otherwise a deleted file
    would let its guard skip its way to green.
    """
    path = REPO / rel
    if path.exists():
        return path
    assert stripped_by_mirror(rel), (
        f"{rel} is missing, and sync-to-clean.yml does not strip it from the mirror. A shipping "
        "file must exist; this is a failure, not a skip."
    )
    pytest.skip(
        f"SOURCE-ONLY: {rel} is stripped from the public mirror by sync-to-clean.yml, so this "
        "check runs only in the private repository."
    )
