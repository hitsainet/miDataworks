"""The public manual publishes exactly its allow-list, and nothing internal (operator 2026-10-20).

The manual (``manual/``, Docusaurus) is built from docs/ pages and published from the public mirror
to GitHub Pages. Three things decide what becomes public, and this file pins all three together:

1. **One allow-list.** ``manual/publish-allowlist.json`` names every docs/ page the manual is built
   from; ``sync-to-clean.yml``'s ``DOCS_KEPT`` must be exactly ``schemas`` plus those files. A page
   added to one and not the other fails here (on the mirror the build would 404 it, or a private
   page would ship).
2. **Nothing internal in it.** ``manual/scripts/check_published.py`` scans the published pages for
   the policy's forbidden patterns. It runs here, in Manual CI and in the mirror's verify step;
   each rule is proved to BITE on a sample, so a rule that stops matching fails.
3. **The wiring.** The verify step, Manual CI and the deploy workflow run the scanner and the
   build; the build runs ``sync-docs.mjs`` (prebuild), which refuses a missing page, a forbidden
   pattern, a table row split by ``|`` in code and a link to an unpublished page. The deploy runs
   only on the mirror, and the sync dispatches it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from tests.support.source_tree import REPO, docs_kept, stripped_by_mirror, sync_job

MANUAL = REPO / "manual"
POLICY_FILE = MANUAL / "publish-allowlist.json"
WORKFLOWS = REPO / ".github" / "workflows"


def policy() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(POLICY_FILE.read_text())
    return data


def scanner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_published", MANUAL / "scripts" / "check_published.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    if True in data:  # YAML 1.1 reads the bare key `on` as True
        data["on"] = data.pop(True)
    return dict(data)


def run_of(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


#: One line per forbidden rule that the rule must catch. A rule renamed, removed or weakened so it
#: no longer matches its sample fails ``test_every_rule_bites``.
SAMPLES = {
    "internal hostname": "open http://k8s-midataworks.hitsai.local/api/",
    "private IPv4 address": "the node at 192.168.244.52",
    "internal host name": "pinned to mcs-lnxhost02",
    "private planning tree": "recorded in 0xcc/reviews/x.md",
    "internal task or design document": "see 009 FTASKS 8.2",
    "private repository": "push to Onegaishimas/miDataworks",
    "local home or scratch path": "the log in /home/someone/app",
    "ssh login": "ssh sean@node 'ls'",
    "Hugging Face token": "the token hf_" + "a" * 34,
    "GitHub token": "token ghp_" + "b" * 36,
    "private key": "-----BEGIN OPENSSH PRIVATE KEY-----",
    "private runbook": "as docs/RUNBOOK.md says",
}


class TestOneAllowList:
    def test_docs_kept_is_exactly_schemas_plus_the_manual_pages(self) -> None:
        sources = [p["source"] for p in policy()["pages"]]
        assert len(sources) == len(set(sources)), "a page is listed twice"
        kept = docs_kept()
        assert len(kept) == len(set(kept)), "DOCS_KEPT names a page twice"
        assert set(kept) == {"schemas", *sources}

    def test_every_allow_listed_page_exists(self) -> None:
        for page in policy()["pages"]:
            assert (REPO / "docs" / page["source"]).is_file(), page["source"]

    def test_the_private_pages_stay_private(self) -> None:
        for private in ("docs/RUNBOOK.md", "docs/REUSE.md", "docs/a-new-page.md"):
            assert stripped_by_mirror(private), f"{private} would be published"

    def test_every_docs_page_not_listed_is_stripped(self) -> None:
        listed = {p["source"] for p in policy()["pages"]}
        for path in (REPO / "docs").glob("*.md"):
            assert stripped_by_mirror(f"docs/{path.name}") == (path.name not in listed), path.name

    def test_the_manual_itself_ships(self) -> None:
        for rel in ("manual/docusaurus.config.ts", "manual/publish-allowlist.json", "manual/docs"):
            assert not stripped_by_mirror(rel), rel
        assert "manual" not in sync_job()["env"]["EXCLUDED_PATHS"].split()


class TestNothingInternalIsPublished:
    def test_the_published_set_is_clean(self) -> None:
        assert scanner().scan(REPO) == []

    def test_the_published_set_covers_the_pages_and_the_manual(self) -> None:
        files = {p.as_posix() for p in scanner().published_files(REPO)}
        for page in policy()["pages"]:
            assert f"docs/{page['source']}" in files
        assert "manual/docs/intro.md" in files
        assert "manual/docusaurus.config.ts" in files
        assert "docs/RUNBOOK.md" not in files

    def test_every_rule_has_a_sample(self) -> None:
        assert {f["name"] for f in policy()["forbidden"]} == set(SAMPLES)

    @pytest.mark.parametrize("name", sorted(SAMPLES))
    def test_every_rule_bites(self, tmp_path: Path, name: str) -> None:
        tree = tmp_path / "repo"
        (tree / "manual" / "scripts").mkdir(parents=True)
        (tree / "docs").mkdir()
        shutil.copy2(POLICY_FILE, tree / "manual" / "publish-allowlist.json")
        for page in policy()["pages"]:
            (tree / "docs" / page["source"]).write_text("# A page\n\nNothing to see.\n")
        target = policy()["pages"][0]["source"]
        (tree / "docs" / target).write_text(f"# A page\n\n{SAMPLES[name]}\n")
        problems = scanner().scan(tree)
        assert problems == [f"docs/{target}:3: {name} must not be published"]

    def test_a_hand_written_page_is_scanned_too(self, tmp_path: Path) -> None:
        tree = tmp_path / "repo"
        (tree / "manual" / "docs").mkdir(parents=True)
        (tree / "docs").mkdir()
        shutil.copy2(POLICY_FILE, tree / "manual" / "publish-allowlist.json")
        for page in policy()["pages"]:
            (tree / "docs" / page["source"]).write_text("# A page\n")
        (tree / "manual" / "docs" / "intro.md").write_text("Reach it at 10.0.12.7.\n")
        assert scanner().scan(tree) == [
            "manual/docs/intro.md:1: private IPv4 address must not be published"
        ]

    def test_a_missing_page_is_a_problem_not_a_pass(self, tmp_path: Path) -> None:
        tree = tmp_path / "repo"
        (tree / "manual").mkdir(parents=True)
        shutil.copy2(POLICY_FILE, tree / "manual" / "publish-allowlist.json")
        problems = scanner().scan(tree)
        assert len(problems) == len(policy()["pages"])
        assert all("missing" in p for p in problems)


@pytest.fixture
def manual_tree(tmp_path: Path) -> Path:
    """A copy of manual/ (policy, script, hand-written pages) and the allow-listed docs."""
    tree = tmp_path / "repo"
    (tree / "manual" / "scripts").mkdir(parents=True)
    shutil.copy2(POLICY_FILE, tree / "manual" / "publish-allowlist.json")
    shutil.copy2(
        MANUAL / "scripts" / "sync-docs.mjs", tree / "manual" / "scripts" / "sync-docs.mjs"
    )
    shutil.copytree(
        MANUAL / "docs",
        tree / "manual" / "docs",
        ignore=shutil.ignore_patterns("guide", "reference"),
    )
    (tree / "docs").mkdir()
    for page in policy()["pages"]:
        shutil.copy2(REPO / "docs" / page["source"], tree / "docs" / page["source"])
    return tree


def sync_docs(tree: Path) -> subprocess.CompletedProcess[str]:
    node = shutil.which("node")
    assert node, "node is needed to run manual/scripts/sync-docs.mjs (CI runners have it)"
    return subprocess.run(
        [node, str(tree / "manual" / "scripts" / "sync-docs.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestTheBuildRefusesABrokenPage:
    def test_the_real_pages_generate(self, manual_tree: Path) -> None:
        result = sync_docs(manual_tree)
        assert result.returncode == 0, result.stderr
        for page in policy()["pages"]:
            out = manual_tree / "manual" / "docs" / page["section"] / f"{page['id']}.md"
            assert out.is_file(), out
            assert out.read_text().startswith("---\nid: ")

    def test_a_pipe_inside_code_in_a_table_fails(self, manual_tree: Path) -> None:
        page = manual_tree / "docs" / "import.md"
        page.write_text(page.read_text() + "\n| A | B |\n|---|---|\n| `x | y` | z |\n")
        result = sync_docs(manual_tree)
        assert result.returncode == 1
        assert "docs/import.md" in result.stderr and "cells" in result.stderr

    def test_an_escaped_pipe_passes(self, manual_tree: Path) -> None:
        page = manual_tree / "docs" / "import.md"
        page.write_text(page.read_text() + "\n| A | B |\n|---|---|\n| `x \\| y` | z |\n")
        assert sync_docs(manual_tree).returncode == 0

    def test_a_link_to_an_unpublished_page_fails(self, manual_tree: Path) -> None:
        page = manual_tree / "docs" / "import.md"
        page.write_text(page.read_text() + "\nSee [the runbook](REUSE.md).\n")
        result = sync_docs(manual_tree)
        assert result.returncode == 1 and "not a published page" in result.stderr

    def test_a_forbidden_pattern_fails_the_build(self, manual_tree: Path) -> None:
        page = manual_tree / "docs" / "labeling.md"
        page.write_text(page.read_text() + "\nOpen k8s-midataworks.hitsai.local.\n")
        result = sync_docs(manual_tree)
        assert result.returncode == 1 and "internal hostname" in result.stderr

    def test_a_missing_page_fails_the_build(self, manual_tree: Path) -> None:
        (manual_tree / "docs" / "calibration.md").unlink()
        result = sync_docs(manual_tree)
        assert result.returncode == 1 and "docs/calibration.md" in result.stderr


class TestTheWiring:
    def test_the_build_runs_sync_docs_first(self) -> None:
        scripts = json.loads((MANUAL / "package.json").read_text())["scripts"]
        assert scripts["prebuild"] == "node scripts/sync-docs.mjs"
        assert scripts["build"] == "docusaurus build"

    def test_the_site_fails_on_broken_links(self) -> None:
        config = (MANUAL / "docusaurus.config.ts").read_text()
        for setting in (
            "onBrokenLinks: 'throw'",
            "onBrokenAnchors: 'throw'",
            "onBrokenMarkdownLinks: 'throw'",
        ):
            assert setting in config, setting
        assert "url: 'https://hitsainet.github.io'" in config
        assert "baseUrl: '/miDataworks/'" in config

    def test_the_generated_pages_are_never_committed(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "manual/docs"], cwd=REPO, capture_output=True, text=True, check=True
        ).stdout.split()
        assert tracked, "no hand-written manual page is tracked"
        assert not [
            t for t in tracked if t.startswith(("manual/docs/guide/", "manual/docs/reference/"))
        ]

    def test_the_mirror_verify_step_scans_and_requires_the_manual(self) -> None:
        verify = next(
            s for s in sync_job()["steps"] if s.get("name") == "Verify the published mirror"
        )
        run = verify["run"]
        assert "python3 manual/scripts/check_published.py ." in run
        assert "manual/docusaurus.config.ts" in run and "manual/publish-allowlist.json" in run
        assert "for k in $DOCS_KEPT" in run

    def test_the_sync_dispatches_the_manual_deploy(self) -> None:
        push = next(s for s in sync_job()["steps"] if s.get("name") == "Push to clean repository")
        assert "/actions/workflows/deploy-manual.yml/dispatches" in push["run"]
        assert '"ref":"main"' in push["run"]

    def test_manual_ci_scans_and_builds_on_every_push_and_pull_request(self) -> None:
        wf = workflow("manual-ci.yml")
        assert "paths" not in (wf["on"].get("push") or {})
        assert "pull_request" in wf["on"]
        run = run_of(wf["jobs"]["build"])
        assert "python3 manual/scripts/check_published.py ." in run
        assert "npm ci" in run and "npm run build" in run
        dirs = {s.get("working-directory") for s in wf["jobs"]["build"]["steps"] if "run" in s}
        assert "manual" in dirs

    def test_the_deploy_runs_only_on_the_mirror_and_publishes_the_build(self) -> None:
        wf = workflow("deploy-manual.yml")
        assert "workflow_dispatch" in wf["on"]
        for name, job in wf["jobs"].items():
            assert "hitsainet/miDataworks" in job["if"], name
            assert "Onegaishimas" not in job["if"], name
        build = wf["jobs"]["build"]
        run = run_of(build)
        assert "python3 manual/scripts/check_published.py ." in run and "npm run build" in run
        upload = next(
            s
            for s in build["steps"]
            if str(s.get("uses", "")).startswith("actions/upload-pages-artifact@")
        )
        assert upload["with"]["path"] == "manual/build"
        deploy = wf["jobs"]["deploy"]["steps"]
        assert any(str(s.get("uses", "")).startswith("actions/deploy-pages@") for s in deploy)
        assert wf["permissions"] == {"contents": "read", "pages": "write", "id-token": "write"}
