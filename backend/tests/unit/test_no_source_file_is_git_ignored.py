"""No file under the application source trees may be git-ignored.

Why: `.gitignore` carried a bare `data/` (meant for the root data directory), which also matched
every package directory named `data`. Feature 008's `licence-table-v1.json` and the backend's copy
of the handoff schema lived in two such directories, so they existed on the developer's disk and
in no commit. Every local suite passed; a clean checkout could not import the backend
(2026-10-07, found by the 005 agent after rebasing onto `main` at 51fbbe4). This test asks git
directly, so a future pattern that swallows source fails here instead of in production.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SOURCE_TREES = ("backend/src", "frontend/src", "datajuicer", "designer")
#: Build and cache output that legitimately sits inside a source tree.
ALLOWED_PARTS = {"__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _ignored_source_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "--", *SOURCE_TREES],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [p for p in out if not (ALLOWED_PARTS & set(Path(p).parts)) and not p.endswith(".pyc")]


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(),
    reason="not a git checkout: the ignore rules cannot be evaluated here",
)
def test_no_source_file_is_ignored():
    ignored = _ignored_source_files()
    assert not ignored, (
        f"these source files exist on disk but are git-ignored, so no commit or image contains "
        f"them: {ignored}. Narrow the .gitignore pattern that matches them (`git check-ignore -v`)."
    )


def test_the_two_files_the_bare_data_pattern_hid_are_tracked():
    """The concrete regression, independent of git being present: both files ship."""
    assert (ROOT / "backend/src/services/publishing/data/licence-table-v1.json").is_file()
    assert (ROOT / "backend/src/schemas/data/midataworks-dataset-version-v1.json").is_file()
