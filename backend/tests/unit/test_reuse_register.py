"""Every copied file names its origin, and docs/REUSE.md lists it (ADR-001, R-03.61; task 2.5).

A file whose header says it came from miStudio or miLLM must carry the repository, the path and
the commit, and must have a row in ``docs/REUSE.md``; every row must point at a file that exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.support.source_tree import source_only

REPO = Path(__file__).resolve().parents[3]
SCANNED = [REPO / "backend" / "src", REPO / "backend" / "tests", REPO / "frontend" / "src"]
HEADER = re.compile(r"Origin[^:]*:\s*(miStudio|miLLM)\s*\(([^)]+)\)\s*(\S+)")
COMMIT = re.compile(r"@\s*([0-9a-f]{7,40})")
ROW = re.compile(
    r"^\|\s*(miStudio|miLLM)\s*\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{7,40})`\s*\|\s*`([^`]+)`\s*\|\s*(copy|adapt)",
    re.M,
)


def copied_files() -> dict[str, tuple[str, str, str]]:
    found: dict[str, tuple[str, str, str]] = {}
    for root in SCANNED:
        for path in root.rglob("*"):
            if path.suffix not in {".py", ".ts", ".tsx"} or "node_modules" in path.parts:
                continue
            head = "\n".join(path.read_text(errors="ignore").splitlines()[:6])
            match = HEADER.search(head)
            if match:
                commit = COMMIT.search(head)
                assert commit, f"{path.relative_to(REPO)} names an origin but no commit"
                found[str(path.relative_to(REPO))] = (
                    match.group(1),
                    match.group(3),
                    commit.group(1),
                )
    return found


def register() -> dict[str, tuple[str, str, str]]:
    # docs/REUSE.md is stripped from the public mirror: skip loudly there, fail if missing here.
    text = source_only("docs/REUSE.md").read_text()
    return {m.group(4): (m.group(1), m.group(2), m.group(3)) for m in ROW.finditer(text)}


def test_every_copied_file_is_registered() -> None:
    rows = register()
    missing = sorted(set(copied_files()) - set(rows))
    assert not missing, f"copied files with no docs/REUSE.md row: {missing}"


def test_every_row_points_at_an_existing_file_with_a_matching_header() -> None:
    files = copied_files()
    for destination, (repo, origin, commit) in register().items():
        assert (REPO / destination).exists(), f"REUSE.md lists {destination}, which does not exist"
        assert destination in files, f"{destination} has no origin header"
        assert files[destination] == (repo, origin, commit), destination


def test_the_scan_finds_copied_code() -> None:
    assert len(copied_files()) >= 10
