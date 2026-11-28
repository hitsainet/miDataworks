"""Where a sibling checkout is found, and what an absent one means (feature 020; FR-020.8; TPD-19;
FTASKS 7.1; the old spellings retired after suite P7, 2026-11-21).

Order: the override variable, else the folder of the sibling's name beside this checkout (named
even when absent, so a skip message points where the checkout belongs). The retired spellings
(``MISTUDIO_REPO``, ``MILLM_REPO``, ``../miStudio``, ``../miLLM``) are planted beside the new ones,
each a DIFFERENT directory, so a lookup that still read one cannot pass by agreement.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from tests.support import siblings
from tests.support.source_tree import REPO

#: Every variable the lookup or the switch could read, new and retired.
ALL_VARIABLES = (
    *siblings.SIBLINGS.values(),
    *(old_env for _, old_env, _ in siblings.RETIRED),
    *siblings.REQUIRE_SWITCHES,
    "MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS",
    "MISTUDIO_REQUIRE_CROSS_REPO_CHECKS",
)


@pytest.fixture
def parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "miDataworks").mkdir()
    monkeypatch.setattr(siblings, "REPO", tmp_path / "miDataworks")
    for var in ALL_VARIABLES:
        monkeypatch.delenv(var, raising=False)
    return tmp_path


def test_the_retired_spellings_are_the_four_p7_retired() -> None:
    """The table names exactly the rebrand's two old variables and two old folders, each against
    the sibling it used to find, and no current spelling."""
    assert siblings.RETIRED == (
        ("miWorkbench", "MISTUDIO_REPO", "miStudio"),
        ("miServer", "MILLM_REPO", "miLLM"),
    )
    assert siblings.SIBLINGS == {
        "miWorkbench": "MIWORKBENCH_REPO",
        "miServer": "MISERVER_REPO",
        "miForge": "MIFORGE_REPO",
        "miAssist": "MIASSIST_REPO",
    }


@pytest.mark.parametrize("name", sorted(siblings.SIBLINGS))
def test_the_override_variable_wins_over_the_folder(
    parent: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    (parent / name).mkdir()
    monkeypatch.setenv(siblings.SIBLINGS[name], str(parent / "elsewhere"))
    assert siblings.sibling_repo(name) == parent / "elsewhere"


def test_a_set_variable_pointing_nowhere_is_used(
    parent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A set variable says where the sibling is: a missing path there reads as absent, never as
    whatever happens to sit beside this checkout."""
    (parent / "miServer").mkdir()
    monkeypatch.setenv("MISERVER_REPO", str(parent / "does-not-exist"))
    assert siblings.sibling_repo("miServer") == parent / "does-not-exist"


@pytest.mark.parametrize("name", sorted(siblings.SIBLINGS))
def test_the_folder_beside_this_checkout_is_found(parent: Path, name: str) -> None:
    (parent / name).mkdir()
    assert siblings.sibling_repo(name) == parent / name


@pytest.mark.parametrize(("name", "old_env", "old_folder"), siblings.RETIRED)
def test_a_retired_variable_alone_finds_nothing(
    parent: Path, monkeypatch: pytest.MonkeyPatch, name: str, old_env: str, old_folder: str
) -> None:
    """P7: only the old variable set, pointing at a real checkout, and no new folder: the sibling
    is ABSENT, reported at its new folder."""
    (parent / "old-checkout").mkdir()
    monkeypatch.setenv(old_env, str(parent / "old-checkout"))
    found = siblings.sibling_repo(name)
    assert found == parent / name
    assert not found.exists()


@pytest.mark.parametrize(("name", "old_env", "old_folder"), siblings.RETIRED)
def test_a_retired_variable_never_overrides_the_new_folder(
    parent: Path, monkeypatch: pytest.MonkeyPatch, name: str, old_env: str, old_folder: str
) -> None:
    (parent / name).mkdir()
    (parent / "old-checkout").mkdir()
    monkeypatch.setenv(old_env, str(parent / "old-checkout"))
    assert siblings.sibling_repo(name) == parent / name


@pytest.mark.parametrize(("name", "old_env", "old_folder"), siblings.RETIRED)
def test_a_retired_folder_alone_finds_nothing(
    parent: Path, name: str, old_env: str, old_folder: str
) -> None:
    """P7: only the old folder beside this checkout: the sibling is ABSENT. ``../miStudio`` is
    since P5 the suite's own folder, so reading it would compare against the wrong repository."""
    (parent / old_folder).mkdir()
    found = siblings.sibling_repo(name)
    assert found == parent / name
    assert not found.exists()


def test_the_retired_spellings_together_still_find_nothing(
    parent: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _, old_env, old_folder in siblings.RETIRED:
        (parent / old_folder).mkdir()
        monkeypatch.setenv(old_env, str(parent / old_folder))
    for name, _, _ in siblings.RETIRED:
        assert not siblings.sibling_repo(name).exists(), name


def test_an_old_name_is_not_a_sibling() -> None:
    for _, _, old_folder in siblings.RETIRED:
        with pytest.raises(KeyError):
            siblings.sibling_repo(old_folder)


def _outcome(call: Callable[[], object]) -> tuple[str, str]:
    """Run ``call`` and name its pytest outcome. Caught explicitly: a skip escaping a
    ``pytest.raises(pytest.fail.Exception)`` block would mark THIS test skipped, which exits 0."""
    try:
        call()
    except pytest.skip.Exception as exc:
        return "skip", str(exc)
    except pytest.fail.Exception as exc:
        return "fail", str(exc)
    return "none", ""


def test_absent_skips_loudly_without_a_switch(parent: Path) -> None:
    assert siblings.required() is False
    assert _outcome(lambda: siblings.absent("no miServer")) == ("skip", "CROSS-REPO: no miServer")


@pytest.mark.parametrize(
    "switch", ["MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS", "MISTUDIO_REQUIRE_CROSS_REPO_CHECKS"]
)
def test_each_require_switch_alone_turns_absent_into_a_failure(
    parent: Path, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    """This repository's switch and the suite-wide one (``MISTUDIO_``: the suite repository's
    current name, which stays after P7) each make an absent sibling fail."""
    monkeypatch.setenv(switch, "1")
    assert siblings.required() is True
    kind, message = _outcome(lambda: siblings.absent("no miServer"))
    assert kind == "fail", f"{switch}=1 must turn an absent sibling into a failure"
    assert message.startswith("CROSS-REPO (required") and message.endswith("no miServer")


def test_the_switches_are_this_repos_and_the_suites() -> None:
    assert siblings.REQUIRE_SWITCHES == (
        "MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS",
        "MISTUDIO_REQUIRE_CROSS_REPO_CHECKS",
    )


@pytest.mark.parametrize("value", ["0", "", "true"])
def test_a_switch_counts_only_at_one(
    parent: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    for switch in siblings.REQUIRE_SWITCHES:
        monkeypatch.setenv(switch, value)
    assert siblings.required() is False


#: Where a retired spelling may still be named: the table itself and this test. Dated records
#: (``0xcc/``) keep the names of their date and are not scanned.
ALLOWED = {"backend/tests/support/siblings.py", "backend/tests/unit/test_siblings_lookup.py"}
SCANNED = ("backend/", ".github/", "docs/", "manual/", "README.md", "AGENT.md", ".env.example")


def _tracked() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *SCANNED],
        cwd=REPO,
        capture_output=True,
        check=True,
    )
    return [p for p in result.stdout.decode().split("\0") if p]


def retired_variable_hits(paths: list[str], root: Path) -> list[tuple[str, str]]:
    hits = []
    for rel in paths:
        if rel in ALLOWED:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue
        for _, old_env, _ in siblings.RETIRED:
            if old_env in text:
                hits.append((rel, old_env))
    return hits


def test_no_tracked_file_names_a_retired_variable() -> None:
    """A test, workflow or document naming ``MISTUDIO_REPO`` / ``MILLM_REPO`` sends someone to set
    a variable that finds nothing."""
    paths = _tracked()
    assert len(paths) > 500, f"the scan read only {len(paths)} files"
    assert "backend/tests/support/siblings.py" in paths
    assert retired_variable_hits(paths, REPO) == []


def test_the_scan_sees_a_planted_retired_variable(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text('os.environ.get("MILLM_REPO")\n')
    (tmp_path / "b.md").write_text("set MISTUDIO_REPO to compare\n")
    (tmp_path / "c.md").write_text("set MIWORKBENCH_REPO to compare\n")
    assert retired_variable_hits(["a.py", "b.md", "c.md"], tmp_path) == [
        ("a.py", "MILLM_REPO"),
        ("b.md", "MISTUDIO_REPO"),
    ]
