"""Where a sibling checkout is found during the rename (feature 020; FR-020.8; TPD-19; FTASKS 7.1).

Order: the new override variable, the old one, the new folder beside this checkout, the old folder;
absent, the NEW folder is named (so a skip message points where the checkout belongs). Both
folders are present in the fixture, so the order is visible (fixtures never agree by construction).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import siblings


@pytest.fixture
def parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "miDataworks").mkdir()
    monkeypatch.setattr(siblings, "REPO", tmp_path / "miDataworks")
    for var in ("MIWORKBENCH_REPO", "MISTUDIO_REPO", "MISERVER_REPO", "MILLM_REPO", "MIFORGE_REPO"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_new_env_wins_over_both_folders(parent: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (parent / "miWorkbench").mkdir()
    (parent / "miStudio").mkdir()
    monkeypatch.setenv("MIWORKBENCH_REPO", str(parent / "elsewhere-new"))
    monkeypatch.setenv("MISTUDIO_REPO", str(parent / "elsewhere-old"))
    assert siblings.sibling_repo("miWorkbench") == parent / "elsewhere-new"


def test_old_env_used_when_new_unset(parent: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (parent / "miServer").mkdir()
    monkeypatch.setenv("MILLM_REPO", str(parent / "elsewhere-old"))
    assert siblings.sibling_repo("miServer") == parent / "elsewhere-old"


def test_new_folder_wins_over_old_folder(parent: Path) -> None:
    (parent / "miWorkbench").mkdir()
    (parent / "miStudio").mkdir()
    assert siblings.sibling_repo("miWorkbench") == parent / "miWorkbench"


def test_old_folder_alone_is_found(parent: Path) -> None:
    (parent / "miLLM").mkdir()
    assert siblings.sibling_repo("miServer") == parent / "miLLM"


def test_absent_sibling_points_at_the_new_folder(parent: Path) -> None:
    assert siblings.sibling_repo("miWorkbench") == parent / "miWorkbench"
    assert siblings.sibling_repo("miForge") == parent / "miForge"


def test_an_unknown_sibling_is_refused() -> None:
    with pytest.raises(KeyError):
        siblings.sibling_repo("miStudio")
