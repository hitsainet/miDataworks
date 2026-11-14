"""The export never counts against the guard (feature 020; CF-20; FTASKS 8.4; control M16f)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.unit.brand import scan


def _git(tree: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=tree,
        check=True,
        capture_output=True,
    )


def test_export_files_are_never_a_surface(tmp_path: Path) -> None:
    for rel in (
        "docs/legacy-identifiers.md",
        "docs/legacy-identifiers.public.md",
        "nested/docs/legacy-identifiers.md",
        "docs/other.md",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("Open miStudio\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    found = scan.tracked("*.md", root=tmp_path)
    assert [p.relative_to(tmp_path).as_posix() for p in found] == ["docs/other.md"]
