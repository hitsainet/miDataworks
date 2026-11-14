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

from tests.support import git_snapshots
from tests.support.source_tree import (
    REPO,
    docs_kept,
    mirror_snapshot,
    source_only,
    stripped_by_mirror,
    sync_job,
)

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


#: Feature 020 (CF-2): the one docs/ file the mirror keeps that is not a manual page.
PUBLIC_EXPORT = "legacy-identifiers.public.md"


class TestOneAllowList:
    def test_docs_kept_is_exactly_schemas_plus_the_manual_pages(self) -> None:
        sources = [p["source"] for p in policy()["pages"]]
        assert len(sources) == len(set(sources)), "a page is listed twice"
        kept = docs_kept()
        assert len(kept) == len(set(kept)), "DOCS_KEPT names a page twice"
        assert set(kept) == {"schemas", *sources, PUBLIC_EXPORT}
        assert "legacy-identifiers.md" not in kept, "the full export never ships"

    def test_every_allow_listed_page_exists(self) -> None:
        for page in policy()["pages"]:
            assert (REPO / "docs" / page["source"]).is_file(), page["source"]

    def test_the_private_pages_stay_private(self) -> None:
        for private in ("docs/RUNBOOK.md", "docs/REUSE.md", "docs/a-new-page.md"):
            assert stripped_by_mirror(private), f"{private} would be published"

    def test_every_docs_page_not_listed_is_stripped(self) -> None:
        listed = {p["source"] for p in policy()["pages"]}
        for path in (REPO / "docs").glob("*.md"):
            shipped = path.name in listed or path.name == PUBLIC_EXPORT
            assert stripped_by_mirror(f"docs/{path.name}") == (not shipped), path.name

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

    def test_the_public_legacy_file_is_scanned_for_forbidden_content(self, tmp_path: Path) -> None:
        """Feature 020 (ID-3): the public export ships on the mirror, so the scan reads it; a
        forbidden line in it is a problem (control M49)."""
        tree = tmp_path / "repo"
        (tree / "manual").mkdir(parents=True)
        (tree / "docs").mkdir()
        shutil.copy2(POLICY_FILE, tree / "manual" / "publish-allowlist.json")
        for page in policy()["pages"]:
            (tree / "docs" / page["source"]).write_text("# A page\n")
        (tree / "docs" / PUBLIC_EXPORT).write_text("# export\n\nk8s-millm.hitsai.local\n")
        assert scanner().scan(tree) == [
            f"docs/{PUBLIC_EXPORT}:3: internal hostname must not be published"
        ]
        result = subprocess.run(
            ["python3", str(MANUAL / "scripts" / "check_published.py"), str(tree)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 1, result.stdout

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


# --- feature 020: the brand pass (FTID §7.5; FTASKS 8.7; control M16d) -------------------------


def _brand_tree(tmp_path: Path, mode: str, page_text: str, export: str = "full") -> Path:
    """A scratch repository root holding the manual's policy, every allow-listed page and one
    export: ``full`` (the private file; on the mirror snapshot the caller skips LOUDLY, anywhere
    else its absence fails) or ``public``."""
    tree = tmp_path / "repo"
    (tree / "manual" / "scripts").mkdir(parents=True)
    (tree / "docs").mkdir()
    shutil.copy2(POLICY_FILE, tree / "manual" / "publish-allowlist.json")
    if export == "full":
        full = source_only("docs/legacy-identifiers.md")
        shutil.copy2(full, tree / "docs" / "legacy-identifiers.md")
    else:
        shutil.copy2(REPO / "docs" / PUBLIC_EXPORT, tree / "docs" / PUBLIC_EXPORT)
    (tree / "brand-guard.json").write_text(json.dumps({"mode": mode, "deny": "(?i)mi(studio|llm)"}))
    for page in policy()["pages"]:
        (tree / "docs" / page["source"]).write_text("# A page\n")
    first = policy()["pages"][0]["source"]
    (tree / "docs" / first).write_text(f"# A page\n\n{page_text}\n")
    return tree


def _run_brand(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(MANUAL / "scripts" / "check_published.py"), str(tree), "--brand"],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_brand_pass_reports_in_report_mode(tmp_path: Path) -> None:
    result = _run_brand(_brand_tree(tmp_path, "report", "Open miStudio, then X-miLLM-Strict."))
    assert result.returncode == 0, result.stdout
    assert "(report): 1 unallowed old name(s)" in result.stdout, "the frozen header is allowed"


def test_brand_pass_fails_on_an_unallowed_hit(tmp_path: Path) -> None:
    result = _run_brand(_brand_tree(tmp_path, "enforce", "Open miStudio to train it."))
    assert result.returncode == 1 and "miStudio" in result.stdout
    clean = _run_brand(_brand_tree(tmp_path / "b", "enforce", "Nothing old here."))
    assert clean.returncode == 0, clean.stdout


@pytest.mark.parametrize("shape", sorted(git_snapshots.EXPECTED))
def test_the_mirror_predicates_agree(tmp_path: Path, shape: str) -> None:
    """check_published.py keeps a standard-library twin of source_tree.mirror_snapshot; both
    must give the same answer, and the right one, on every shape of tree (mirror CI, 2026-11-08)."""
    tree = git_snapshots.make_repo(tmp_path / "t", shape)
    expected = git_snapshots.EXPECTED[shape]
    assert scanner().mirror_snapshot(tree) is expected, shape
    assert mirror_snapshot(tree) is expected, shape


def test_brand_pass_reports_and_never_fails_on_the_mirror_snapshot(tmp_path: Path) -> None:
    """Suite decision R23 (2026-11-11): on the public mirror the brand pass runs in REPORT mode
    whatever ``brand-guard.json`` says, reading the public export (the only one the mirror
    carries; it withholds the old host names and the private repository names). It prints its
    count and the published-content scan still runs. Before R23 it skipped there (2026-11-08).
    """
    tree = _brand_tree(tmp_path, "enforce", "Open miStudio to train it.", export="public")
    git_snapshots.make_repo(tree, "mirror")
    result = _run_brand(tree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SKIPPED" not in result.stdout
    assert "(report on the public mirror, R23): 1 unallowed old name(s)" in result.stdout
    assert "miStudio" in result.stdout
    assert "published files are clean" in result.stdout


def test_brand_pass_enforces_in_a_private_tree_with_the_same_page(tmp_path: Path) -> None:
    """The other half of R23: the same page and the same ``enforce`` policy fail in a tree that
    is not the mirror snapshot (the private CI is the gate)."""
    tree = _brand_tree(tmp_path, "enforce", "Open miStudio to train it.")
    git_snapshots.make_repo(tree, "private")
    result = _run_brand(tree)
    assert result.returncode == 1, result.stdout
    assert "(enforce): 1 unallowed old name(s)" in result.stdout


def test_brand_pass_reports_an_identifier_in_a_code_span_and_enforces_prose(tmp_path: Path) -> None:
    """Scope (FPRD Q4): a code span naming an identifier is reported, prose is enforced."""
    page = "It refuses with `PROBE_ENDPOINT_NOT_MILLM`; read `dependencies.millm.ui_url`."
    result = _run_brand(_brand_tree(tmp_path, "enforce", page))
    assert result.returncode == 0, result.stdout
    assert "(enforce): 0 unallowed old name(s); 2 identifier hit(s), reported only" in result.stdout
    prose = _run_brand(_brand_tree(tmp_path / "b", "enforce", page + " Then open `miLLM`."))
    assert prose.returncode == 1 and "(enforce): 1 unallowed old name(s)" in prose.stdout


@pytest.mark.parametrize("shape", ["none", "private", "shallow-private", "orphan-plain"])
def test_brand_pass_without_the_full_export_fails_off_the_mirror(
    tmp_path: Path, shape: str
) -> None:
    """Anywhere but the mirror snapshot (this repository, the private CI) a missing full export
    is an error: the brand pass never skips there."""
    tree = _brand_tree(tmp_path, "report", "Nothing old here.", export="public")
    if shape == "shallow-private":
        git_snapshots.make_repo(tmp_path / "shallow", shape)
        shutil.copytree(tmp_path / "shallow" / ".git", tree / ".git")
    else:
        git_snapshots.make_repo(tree, shape)
    result = _run_brand(tree)
    assert result.returncode == 1, result.stdout
    assert "SKIPPED" not in result.stdout
    assert "is not the mirror snapshot" in result.stderr


def test_manual_ci_passes_brand() -> None:
    assert "check_published.py . --brand" in run_of(workflow("manual-ci.yml")["jobs"]["build"])


def test_no_link_to_the_old_pages_path() -> None:
    """Feature 020 (FR-020.19, ID-11): the manual links miStudio's pages by their new host once it
    answers. Strict: the day 9.2 lands this passes, and the marker must be removed."""
    found = [
        str(p.relative_to(REPO))
        for p in MANUAL.rglob("*")
        if p.is_file() and "node_modules" not in p.parts
        # Generated, git-ignored output (`manual/.gitignore`): a local build from before the links moved kept
        # the old path and turned this red on a workstation while CI, which has no build, stayed green
        # (2026-11-12). The sources are what the test owns.
        and not {"build", ".docusaurus"} & set(p.relative_to(MANUAL).parts)
        and p.suffix in {".md", ".mdx", ".ts", ".tsx", ".js"}
        and "hitsainet.github.io/miStudio" in p.read_text(errors="ignore")
    ]
    assert found == [], found
