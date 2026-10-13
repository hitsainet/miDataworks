"""miStudio's and miForge's vendored copies equal ours byte for byte (FR-008.39; FTASKS 2.6).

The consumers own their copies and their own guard (miStudio 034 FR-24/25; miForge R-02.37);
this pins the other direction, as miStudio's ``test_probe_definition.py`` docstring urges, so a
drift is caught on whichever side runs first.

Skipped when a sibling repository is not checked out — always the case on the public mirror and
in CI, which clone this repository alone. ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` turns a
missing sibling into a failure for runs that must prove the pair.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.schemas.dataset_version import SCHEMA_FILE_NAME, render_schema_file

DEFAULTS = {
    "MISTUDIO_REPO": "/home/x-sean/app/miStudio",
    "MIFORGE_REPO": "/home/x-sean/app/miForge",
}


def _sibling_copy(var: str) -> Path | None:
    root = Path(os.environ.get(var, DEFAULTS[var]))
    copy = root / "docs" / "schemas" / SCHEMA_FILE_NAME
    if copy.exists():
        return copy
    message = (
        f"{var}: no vendored {SCHEMA_FILE_NAME} under {root}. Copy miDataworks' "
        f"docs/schemas/{SCHEMA_FILE_NAME} into that repository's docs/schemas/."
    )
    if os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1":
        pytest.fail(message)
    # The public mirror and CI check out this repository alone, so the sibling is absent there.
    pytest.skip("CROSS-REPO: " + message)
    return None


@pytest.mark.parametrize("var", sorted(DEFAULTS))
def test_the_vendored_copy_is_byte_identical(var: str) -> None:
    copy = _sibling_copy(var)
    assert copy is not None
    assert copy.read_bytes() == render_schema_file(), (
        f"{copy} differs from miDataworks' schema. Copy miDataworks' docs/schemas/"
        f"{SCHEMA_FILE_NAME} over it; never edit the vendored copy by hand."
    )


def test_a_required_check_fails_rather_than_skips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTUDIO_REPO", str(tmp_path))
    monkeypatch.setenv("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS", "1")
    with pytest.raises(pytest.fail.Exception, match="Copy miDataworks"):
        _sibling_copy("MISTUDIO_REPO")
