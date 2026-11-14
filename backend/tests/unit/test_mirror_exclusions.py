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

from tests.support import git_snapshots, source_tree
from tests.support.source_tree import (
    REPO,
    docs_kept,
    excluded_paths,
    stripped_by_mirror,
    sync_job,
)

#: The decided exclusion list (14.2, operator 2026-10-06), plus the venv paths and the
#: tooling cache miWorkbench and miForge also strip. Change it only together with the workflow.
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
    # 016 FTASKS 3.9: valid-path cases of formats with no published value never ship.
    "backend/tests/fixtures/private",
    # Dependabot runs on the private repository only: its branches on the mirror anchored old
    # snapshots and failed the one-commit check (2026-11-01).
    ".github/dependabot.yml",
)
#: The vendored contracts, and the docs/ pages the public manual is built from (operator decision
#: 2026-10-20; equal to manual/publish-allowlist.json, pinned by test_manual_publish.py).
EXPECTED_DOCS_KEPT = (
    "schemas",
    "import.md",
    "versions.md",
    "curation.md",
    "labeling.md",
    "calibration.md",
    "generation.md",
    "publishing.md",
    "detector-sets.md",
    "rule-signals.md",  # feature 016 (FR-016.40)
    "code-rules.md",  # feature 018 (X7)
    "operators.md",
    "data-model-keying.md",
    "mcp-contract.md",
    # Feature 020 (CF-2): the PUBLIC variant of the rebrand export; the full file stays private.
    "legacy-identifiers.public.md",
)

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
    ".github/dependabot.yml": ".github/dependabot.yml",
    "backend/tests/fixtures/private": "backend/tests/fixtures/private/rule-signal-private-cases.json",
}
#: Removed by the docs rule, not by the list.
DOCS_STRIPPED = ("docs/RUNBOOK.md", "docs/REUSE.md", "docs/notes/plan.md")
#: Must survive: shipped code and contracts, and look-alikes of excluded names.
SURVIVORS = (
    "README.md",
    ".github/workflows/frontend-ci.yml",
    "docs/schemas/midataworks-dataset-version-v1.json",
    "docs/schemas/midataworks-rubric-v1.json",
    "docs/schemas/rule-signal-cases-v1.json",
    "docs/schemas/midataworks-rule-set-v1.json",
    "backend/tests/fixtures/behaviours/fixture-alpha/rule_set.json",
    "docs/mcp-contract.md",
    "docs/labeling.md",
    "manual/docusaurus.config.ts",
    "manual/docs/intro.md",
    "backend/src/main.py",
    "backend/scripts/operate.sh",
    "frontend/src/records.ts",
    "frontend/src/PLAN-notes.md",
    ".github/workflows/backend-tests.yml",
    "datajuicer/Dockerfile",
    # Feature 018 (FTDD §12.2, H5 (a)): the runner image's tree and both runner contract files.
    "runner/Dockerfile",
    "docs/schemas/runner-cases-v1.json",
    "docs/schemas/midataworks-runner-v1.json",
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
        for rel in (
            "README.md",
            "backend/src/main.py",
            "frontend/package.json",
            "docs/schemas",
            "docs/labeling.md",
            "manual/docusaurus.config.ts",
            "manual/publish-allowlist.json",
            # Feature 018: the mirror builds hitsai/midataworks-runner from these.
            "runner/Dockerfile",
            "runner/Dockerfile.dockerignore",
            "runner/requirements.txt",
            "runner/constraints.txt",
            "runner/scripts/build_bundle.py",
            "backend/src/runner/server.py",
            "backend/src/runner_contract/constants.py",
            "docs/schemas/midataworks-runner-v1.json",
            "docs/schemas/runner-cases-v1.json",
        ):
            assert (tree / rel).exists(), f"{rel} must ship"
        assert not [
            r
            for r in remaining
            if r.startswith("docs/") and r.split("/")[1] not in EXPECTED_DOCS_KEPT
        ]


class TestTheRebrandExport:
    """Feature 020 (CF-2): the mirror keeps ``docs/legacy-identifiers.public.md`` and never the full
    ``docs/legacy-identifiers.md`` (which names internal hosts and the private repositories)."""

    def test_public_variant_survives_and_full_file_is_stripped(self, tmp_path: Path) -> None:
        tree = tmp_path / "tree"
        for rel in (
            "docs/legacy-identifiers.md",
            "docs/legacy-identifiers.public.md",
            "README.md",
        ):
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            (tree / rel).write_text(f"fixture {rel}\n")
        git(tree, "init", "-q")
        git(tree, "add", "-A")
        git(tree, "commit", "-q", "-m", "fixture")
        run_removal(tree)
        assert (tree / "docs" / "legacy-identifiers.public.md").exists()
        assert not (tree / "docs" / "legacy-identifiers.md").exists()
        assert stripped_by_mirror("docs/legacy-identifiers.md")
        assert not stripped_by_mirror("docs/legacy-identifiers.public.md")


class TestTheGateRunsEveryRequiredWorkflowOnEveryPush:
    """miServer's P0 review (2026-11-08): a path-filtered workflow plus a gate that passes a commit
    which touched no filtered path lets a docs-, k8s-, workflow- or manual-only push skip the very
    tests that read those files (the 020 export guards, the backup manifest, the sync workflow,
    the manual). Here every workflow the mirror gate requires runs on every push: none may carry a
    path filter. The list is read from the gate itself, so a workflow added to it is covered."""

    def test_no_required_workflow_has_a_path_filter(self) -> None:
        import yaml

        required = sync_job_gate_workflows()
        assert {"backend-tests.yml", "frontend-ci.yml", "manual-ci.yml"} <= set(required)
        for name in required:
            doc = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text())
            on = doc.get("on", doc.get(True)) or {}
            for event in ("push", "pull_request"):
                assert event in on, f"{name} does not run on {event}"
                config = on[event] or {}
                for key in ("paths", "paths-ignore"):
                    assert key not in config, f"{name} {event} has a {key} filter: {config[key]}"


def sync_job_gate_workflows() -> list[str]:
    import yaml

    doc = yaml.safe_load((REPO / ".github" / "workflows" / "sync-to-clean.yml").read_text())
    return str(doc["jobs"]["gate"]["env"]["REQUIRED_WORKFLOWS"]).split()


class TestTheVerifyStepRefusesTheFullExport:
    """The mirror's own leak check (the verify step, run on the PUBLISHED clone) refuses the
    private full export even if the removal step were wrong: under docs/ only DOCS_KEPT may exist.
    The step's own pattern line is executed here on both export names."""

    def _stray(self, paths: list[str]) -> list[str]:
        run = step("Verify the published mirror")["run"]
        [pattern_line] = [line for line in run.splitlines() if line.strip().startswith("pattern=")]
        script = pattern_line.strip() + '\nprintf "%s\\n" "$@" | grep -vE "$pattern" || true\n'
        env = {**os.environ, "DOCS_KEPT": str(sync_job()["env"]["DOCS_KEPT"])}
        out = subprocess.run(
            ["bash", "-c", script, "verify", *paths],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return out.split()

    def test_the_full_export_is_a_leak_and_the_public_one_is_not(self) -> None:
        assert self._stray(
            ["docs/legacy-identifiers.md", "docs/legacy-identifiers.public.md", "docs/labeling.md"]
        ) == ["docs/legacy-identifiers.md"]

    def _verdict(self, tmp_path: Path, paths: list[str]) -> subprocess.CompletedProcess[str]:
        """The step's OWN docs block, from ``pattern=`` through the ``fi`` that closes its LEAK
        branch, run with ``git log`` answering ``paths`` (review round 2, survivors V01 and V02:
        the pattern line alone was executed, so the line that USES it could be broken freely)."""
        lines = step("Verify the published mirror")["run"].splitlines()
        start = next(i for i, line in enumerate(lines) if line.strip().startswith("pattern="))
        end = next(i for i in range(start, len(lines)) if lines[i].strip() == "fi")
        block = "\n".join(lines[start : end + 1])
        assert "stray=" in block and "LEAK" in block, block
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        (bin_dir / "git").write_text('#!/bin/sh\nprintf "%s\\n" $STUB_PATHS\n')
        (bin_dir / "git").chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "DOCS_KEPT": str(sync_job()["env"]["DOCS_KEPT"]),
            "STUB_PATHS": " ".join(paths),
        }
        script = f'set -eo pipefail\nFAILED=0\n{block}\necho "FAILED=$FAILED"\n'
        return subprocess.run(
            ["bash", "-c", script], env=env, capture_output=True, text=True, check=True
        )

    def test_the_step_itself_fails_on_the_full_export(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        leak = self._verdict(tmp_path / "a", ["docs/legacy-identifiers.md", "docs/labeling.md"])
        assert "FAILED=1" in leak.stdout and "docs/legacy-identifiers.md" in leak.stdout
        clean = self._verdict(
            tmp_path / "b", ["docs/legacy-identifiers.public.md", "docs/labeling.md"]
        )
        assert "FAILED=0" in clean.stdout, clean.stdout


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

    def test_a_missing_stripped_file_skips_on_the_mirror_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from tests.support import source_tree
        from tests.support.git_snapshots import make_repo

        monkeypatch.setattr(source_tree, "REPO", make_repo(tmp_path / "m", "mirror"))
        with pytest.raises(pytest.skip.Exception, match="SOURCE-ONLY"):
            source_tree.source_only("docs/REUSE.md")

    @pytest.mark.parametrize("shape", ["none", "private", "shallow-private", "orphan-plain"])
    def test_a_missing_stripped_file_fails_off_the_mirror_snapshot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shape: str
    ) -> None:
        """The private CI never skips for want of a file the mirror lacks: on any tree that is
        not the mirror snapshot a missing stripped file is a FAILURE (mirror CI, 2026-11-08)."""
        from tests.support import source_tree
        from tests.support.git_snapshots import make_repo

        monkeypatch.setattr(source_tree, "REPO", make_repo(tmp_path / "t", shape))
        try:
            source_tree.source_only("docs/REUSE.md")
        except pytest.skip.Exception:
            pytest.fail(f"a missing stripped file was skipped on a {shape!r} tree")
        except AssertionError as exc:
            assert "not the mirror snapshot" in str(exc)
        else:
            pytest.fail("source_only returned a path for a missing file")


class TestMirrorSnapshot:
    """The predicate every mirror-only skip rests on (mirror CI, 2026-11-08)."""

    @pytest.mark.parametrize("shape", sorted(git_snapshots.EXPECTED))
    def test_only_the_synced_orphan_is_the_mirror(self, tmp_path: Path, shape: str) -> None:
        tree = git_snapshots.make_repo(tmp_path / "t", shape)
        assert source_tree.mirror_snapshot(tree) is git_snapshots.EXPECTED[shape], shape

    def test_the_sync_writes_the_trailers_the_predicate_reads(self) -> None:
        """The marker comes from the workflow: if the sync stopped writing either trailer, the
        mirror would stop being recognised and its runs would fail loudly, not skip."""
        steps = source_tree.sync_job()["steps"]
        build = next(s["run"] for s in steps if s.get("name") == "Build the orphan snapshot")
        assert "git checkout --orphan clean" in build
        assert '--trailer "Source-Commit: ${SOURCE_SHA}"' in build
        assert '--trailer "Private-CI: passed"' in build
        assert git_snapshots.SYNC_TRAILERS[3] == "Private-CI: passed"

    def test_this_checkout_is_the_mirror_only_without_the_full_export(self) -> None:
        """Live: the tree under test is the mirror snapshot exactly when the private-only full
        export is absent. On the private CI this says the predicate is False, so no mirror-only
        skip can fire there; on the mirror it says the predicate recognised the snapshot."""
        full = (source_tree.REPO / "docs" / "legacy-identifiers.md").is_file()
        assert source_tree.mirror_snapshot() is (not full)
