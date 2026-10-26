"""Every file under ``docs/schemas/`` has a row in its README (FR-016.15; FTASKS 3.8).

Derived from the directory listing, so a new contract file without a row is red.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.support.source_tree import REPO

SCHEMAS = REPO / "docs" / "schemas"


def missing_rows(directory: Path) -> list[str]:
    readme = (directory / "README.md").read_text(encoding="utf-8")
    rows = set(re.findall(r"^\| `([^`]+)` \|", readme, flags=re.MULTILINE))
    files = sorted(p.name for p in directory.iterdir() if p.is_file() and p.name != "README.md")
    assert files, "no contract files found"
    return [f for f in files if f not in rows]


def test_every_contract_file_has_a_readme_row() -> None:
    assert missing_rows(SCHEMAS) == []


def test_an_unlisted_file_is_red(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text((SCHEMAS / "README.md").read_text(encoding="utf-8"))
    for p in SCHEMAS.iterdir():
        (tmp_path / p.name).write_bytes(p.read_bytes())
    (tmp_path / "new-contract-v1.json").write_text("{}")
    assert missing_rows(tmp_path) == ["new-contract-v1.json"]


def test_the_process_rule_is_recorded() -> None:
    assert "The vector is the truth" in (SCHEMAS / "README.md").read_text(encoding="utf-8")
