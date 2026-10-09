"""The public mirror never receives a private path (ADR-023; Foundation tasks 14.2, 14.3, 14.5).

Reads ``.github/workflows/sync-to-clean.yml`` and checks it three ways:

1. **The list is pinned.** ``EXCLUDED_PATHS`` and ``DOCS_KEPT`` equal the decided list (14.2). A
   path dropped from the workflow fails here before it ever reaches the mirror.
2. **The removal step WORKS.** Its shell is run, as written, against a fixture tree holding every
   excluded path plus look-alikes that must survive (a nested ``backend/scripts/``, a nested
   ``PLAN-*.md``), and against a copy of this repository's tracked files. A list that is right
   while the script ignores it would pass a text check and fail here.
3. **Publication is a parentless snapshot,** verified after the push against every commit.

``tests/support/source_tree.py`` derives "skip on the mirror" from the same workflow keys.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.support.source_tree import (
    REPO,
    docs_kept,
    excluded_paths,
    stripped_by_mirror,
    sync_job,
)

#: The decided exclusion list (14.2, operator 2026-10-06), plus the venv paths and the
#: tooling cache miStudio and miForge also strip. Change it only together with the workflow.
EXPECTED_EXCLUDED = (
    "0xcc",
    ".claude",
    "AGENT.md",
    "CLAUDE.md",
    "GEMINI.md",
    "PLAN-*.md",
    "scripts",
    "records",
    "dataset",
    "backups",
    "venv",
    "backend/.python",
    ".understand-anything",
)
EXPECTED_DOCS_KEPT = ("schemas", "mcp-contract.md")

#: One fixture file per excluded entry (what the removal step must delete).
EXCLUDED_FIXTURES = {
    "0xcc": "0xcc/prds/000_PPRD.md",
    ".claude": ".claude/agents/x.md",
    "AGENT.md": "AGENT.md",
    "CLAUDE.md": "CLAUDE.md",
    "GEMINI.md": "GEMINI.md",
    "PLAN-*.md": "PLAN-humor-labeling.md",
    "scripts": "scripts/label.py",
    "records": "records/run.jsonl",
    "dataset": "dataset/rows.parquet",
    "backups": "backups/db.dump",
    "venv": "venv/bin/python",
    "backend/.python": "backend/.python/bin/python3.11",
    ".understand-anything": ".understand-anything/graph.json",
}
#: Removed by the docs rule, not by the list.
DOCS_STRIPPED = ("docs/RUNBOOK.md", "docs/REUSE.md", "docs/notes/plan.md")
#: Must survive: shipped code and contracts, and look-alikes of excluded names.
SURVIVORS = (
    "README.md",
    "docs/schemas/midataworks-dataset-version-v1.json",
    "docs/mcp-contract.md",
    "backend/src/main.py",
    "backend/scripts/operate.sh",
    "frontend/src/records.ts",
    "frontend/src/PLAN-notes.md",
    ".github/workflows/backend-tests.yml",
    "datajuicer/Dockerfile",
)
#: Tracked but matching .gitignore: force-added, never meant to ship.
IGNORED_BUT_TRACKED = "notes/secret.log"


def step(name: str) -> dict[str, Any]:
    for s in sync_job()["steps"]:
        if s.get("name") == name:
            return dict(s)
    raise AssertionError(f"sync-to-clean.yml has no step named {name!r}")


def run_removal(tree: Path) -> None:
    env = {
        **os.environ,
        "EXCLUDED_PATHS": str(sync_job()["env"]["EXCLUDED_PATHS"]),
        "DOCS_KEPT": str(sync_job()["env"]["DOCS_KEPT"]),
    }
    script = step("Remove excluded files and folders")["run"]
    result = subprocess.run(
        ["bash", "-e", "-c", script], cwd=tree, env=env, capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stdout + result.stderr


def git(tree: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=tree,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def fixture_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    for rel in (*EXCLUDED_FIXTURES.values(), *DOCS_STRIPPED, *SURVIVORS, IGNORED_BUT_TRACKED):
        path = tree / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture {rel}\n")
    (tree / ".gitignore").write_text("*.log\n")
    git(tree, "init", "-q")
    git(tree, "add", "-A")
    git(tree, "add", "-f", IGNORED_BUT_TRACKED)
    git(tree, "commit", "-q", "-m", "fixture")
    return tree


class TestTheListIsPinned:
    def test_excluded_paths_match_the_decision(self) -> None:
        assert excluded_paths() == list(EXPECTED_EXCLUDED)

    def test_docs_kept_match_the_decision(self) -> None:
        assert docs_kept() == list(EXPECTED_DOCS_KEPT)

    def test_both_steps_read_the_one_list(self) -> None:
        # The list is job-level env; neither step may shadow it with its own copy.
        for name in ("Remove excluded files and folders", "Verify the published mirror"):
            s = step(name)
            assert "EXCLUDED_PATHS" not in s.get("env", {}), f"{name} shadows the list"
            assert "$EXCLUDED_PATHS" in s["run"], f"{name} does not read EXCLUDED_PATHS"
        assert "$DOCS_KEPT" in step("Verify the published mirror")["run"]

    def test_runs_only_in_the_private_repository(self) -> None:
        condition = sync_job()["if"]
        assert "Onegaishimas/miDataworks" in condition
        assert "hitsainet" not in condition


class TestTheRemovalStepWorks:
    @pytest.mark.parametrize("entry", EXPECTED_EXCLUDED)
    def test_each_excluded_path_is_removed(self, fixture_tree: Path, entry: str) -> None:
        run_removal(fixture_tree)
        rel = EXCLUDED_FIXTURES[entry]
        assert not (fixture_tree / rel).exists(), f"{rel} survived the sync ({entry})"

    @pytest.mark.parametrize("rel", DOCS_STRIPPED)
    def test_docs_other_than_the_contracts_are_removed(self, fixture_tree: Path, rel: str) -> None:
        run_removal(fixture_tree)
        assert not (fixture_tree / rel).exists(), f"{rel} survived the sync"

    @pytest.mark.parametrize("rel", SURVIVORS)
    def test_shipped_files_survive(self, fixture_tree: Path, rel: str) -> None:
        run_removal(fixture_tree)
        assert (fixture_tree / rel).exists(), f"{rel} was removed but must ship"

    def test_a_tracked_file_that_matches_gitignore_is_removed(self, fixture_tree: Path) -> None:
        run_removal(fixture_tree)
        assert not (fixture_tree / IGNORED_BUT_TRACKED).exists()

    def test_on_a_copy_of_this_repository(self, tmp_path: Path) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True
        ).stdout.split(b"\0")
        tree = tmp_path / "repo"
        for raw in filter(None, tracked):
            rel = raw.decode()
            source = REPO / rel
            if not source.is_file():
                continue  # deleted in the working tree
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, tree / rel)
        git(tree, "init", "-q")
        git(tree, "add", "-A")
        run_removal(tree)
        remaining = [
            p.relative_to(tree).as_posix()
            for p in tree.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(tree).parts
        ]
        leaked = [rel for rel in remaining if stripped_by_mirror(rel)]
        assert not leaked, f"paths the mirror must not receive survived: {leaked[:10]}"
        for rel in ("README.md", "backend/src/main.py", "frontend/package.json", "docs/schemas"):
            assert (tree / rel).exists(), f"{rel} must ship"
        assert not [
            r
            for r in remaining
            if r.startswith("docs/") and r.split("/")[1] not in EXPECTED_DOCS_KEPT
        ]


class TestPublicationIsASnapshot:
    def test_the_checkout_fetches_no_history(self) -> None:
        checkout = step("Checkout source repository")
        assert checkout["with"]["fetch-depth"] == 1

    def test_the_commit_is_an_orphan_and_refuses_parents(self) -> None:
        run = step("Build the orphan snapshot")["run"]
        assert "git checkout --orphan clean" in run
        assert "rev-list --parents -n1 HEAD" in run and "exit 1" in run

    def test_tags_point_at_the_orphan_and_stale_tags_are_retargeted(self) -> None:
        run = step("Push to clean repository")["run"]
        assert 'git tag -f "$TAG" HEAD' in run
        assert "git ls-remote --tags" in run and 'git tag -f "$T" HEAD' in run
        assert "|| true" in run.split("git ls-remote --tags", 1)[1].splitlines()[0]

    def test_the_verify_step_checks_every_commit_and_has_a_positive_control(self) -> None:
        run = step("Verify the published mirror")["run"]
        assert "git rev-list --all --count" in run
        assert "git log --all --oneline" in run
        assert "git ls-files -ci --exclude-standard" in run
        assert "README.md" in run and "docs/schemas" in run


class TestSourceOnlySkip:
    def test_a_stripped_path_is_recognised(self) -> None:
        assert stripped_by_mirror("docs/REUSE.md")
        assert stripped_by_mirror("0xcc/tasks/000_FTASKS|Project_Foundation.md")
        assert stripped_by_mirror("PLAN-humor-labeling.md")

    def test_a_shipping_path_is_not(self) -> None:
        assert not stripped_by_mirror("docs/schemas/README.md")
        assert not stripped_by_mirror("backend/scripts/x.sh")
        assert not stripped_by_mirror("frontend/src/PLAN-notes.md")

    def test_a_missing_shipping_file_fails_rather_than_skips(self) -> None:
        from tests.support.source_tree import source_only

        # A skip here would read as a pass, so catch it explicitly (control M05).
        try:
            source_only("backend/src/does_not_exist.py")
        except AssertionError as exc:
            assert "not strip it" in str(exc)
        except pytest.skip.Exception:
            pytest.fail("a missing SHIPPING file was skipped instead of failing")
        else:
            pytest.fail("source_only returned a path for a missing file")

    def test_a_missing_stripped_file_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.support import source_tree

        monkeypatch.setattr(source_tree, "REPO", Path("/nonexistent-checkout"))
        with pytest.raises(pytest.skip.Exception, match="SOURCE-ONLY"):
            source_tree.source_only("docs/REUSE.md")
