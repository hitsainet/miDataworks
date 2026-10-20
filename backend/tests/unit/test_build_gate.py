"""The image build is gated on the backend suite and never double-builds (ADR-024; 15.2, 15.5, 15.6).

Reads ``.github/workflows/docker-images.yml`` and RUNS its shell, not just its text:

- **The test gate** (copied from miLLM, where three commits once deployed over a red suite): the
  gate step is executed with a stub ``gh`` that answers what GitHub's API would, and a stub
  ``sleep``. A passing run publishes; a failed or cancelled one refuses; no run at all falls back
  to the last completed run only when it passed AND no backend source changed since.
- **The wiring**: the build waits for the gate, and the backend and Data-Juicer legs refuse as
  their first step when the gate did not succeed, while the frontend leg is free to publish.
- **The double-build guard** (miStudio commit 7f27b520): a tag push builds only when the tag is
  NEW. The sync retargets every existing mirror tag on each snapshot; without this guard each sync
  built and rolled the backend twice, and the second rollout killed a running job.
- **Change detection** maps each changed path to the images it belongs to.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "docker-images.yml"
ALL_IMAGES = ["backend", "frontend", "datajuicer", "designer"]


def load(path: Path = WORKFLOW) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if True in data:  # YAML 1.1 reads the bare key `on` as True
        data["on"] = data.pop(True)
    return data


def job(name: str) -> dict[str, Any]:
    return dict(load()["jobs"][name])


def step(job_name: str, step_name: str) -> dict[str, Any]:
    for s in job(job_name)["steps"]:
        if s.get("name") == step_name:
            return dict(s)
    raise AssertionError(f"{job_name} has no step {step_name!r}")


# --- the double-build guard -------------------------------------------------------------------


def run_detect(tmp_path: Path, **env: str) -> list[str]:
    output = tmp_path / "github_output"
    output.write_text("")
    script = step("detect", "Detect changed images")["run"]
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        env={**os.environ, "GITHUB_OUTPUT": str(output), **env},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (line,) = [x for x in output.read_text().splitlines() if x.startswith("images=")]
    images: list[str] = json.loads(line.removeprefix("images="))
    return images


class TestTheDoubleBuildGuard:
    def test_a_retargeted_existing_tag_builds_nothing(self, tmp_path: Path) -> None:
        assert run_detect(tmp_path, REF="refs/tags/v0.1.0", CREATED="false") == []

    def test_a_tag_push_without_created_builds_nothing(self, tmp_path: Path) -> None:
        assert run_detect(tmp_path, REF="refs/tags/v0.1.0", CREATED="") == []

    def test_a_new_tag_builds_every_image(self, tmp_path: Path) -> None:
        assert run_detect(tmp_path, REF="refs/tags/v0.2.0", CREATED="true") == ALL_IMAGES

    def test_detect_reads_the_created_flag_from_the_event(self) -> None:
        assert step("detect", "Detect changed images")["env"]["CREATED"] == (
            "${{ github.event.created }}"
        )

    def test_the_sync_still_retargets_tags_so_the_guard_is_needed(self) -> None:
        # If the sync stopped retargeting, this guard's reason would be gone; if the guard is
        # removed while the sync retargets, every sync builds twice. Keep the two together.
        sync = (REPO / ".github" / "workflows" / "sync-to-clean.yml").read_text()
        assert "Retargeted stale tag" in sync


# --- change detection -------------------------------------------------------------------------


def paths_for_function() -> str:
    script = step("detect", "Detect changed images")["run"]
    match = re.search(r"(?ms)^paths_for\(\) \{.*?^\}", script)
    assert match, "detect has no paths_for function"
    return match.group(0)


def images_for(path: str) -> list[str]:
    function = paths_for_function()
    out = []
    for image in ALL_IMAGES:
        result = subprocess.run(
            ["bash", "-c", f'{function}\necho "$1" | grep -qE "$(paths_for {image})"', "_", path],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0:
            out.append(image)
    return out


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("backend/src/main.py", ["backend"]),
        ("backend/alembic/versions/0004_x.py", ["backend"]),
        ("backend/requirements.txt", ["backend"]),
        ("backend/Dockerfile", ["backend"]),
        ("backend/docker-entrypoint.sh", ["backend"]),
        ("backend/src/operators/datajuicer/runner.py", ["backend", "datajuicer"]),
        ("datajuicer/requirements.txt", ["datajuicer"]),
        ("datajuicer/constraints.txt", ["datajuicer"]),
        ("backend/tests/fixtures/operators/contract_fixture.parquet", ["datajuicer"]),
        ("backend/src/operators/data_designer/runner.py", ["backend", "designer"]),
        ("backend/src/operators/data_designer/relay.py", ["backend", "designer"]),
        ("backend/src/operators/data_designer/finalize.py", ["backend"]),
        ("designer/requirements.txt", ["designer"]),
        ("designer/Dockerfile", ["designer"]),
        ("frontend/src/App.tsx", ["frontend"]),
        ("backend/tests/unit/test_x.py", []),
        ("docs/schemas/x.json", []),
        ("k8s/base/backend.yaml", []),
        ("README.md", []),
    ],
)
def test_each_path_rebuilds_the_images_it_is_part_of(path: str, expected: list[str]) -> None:
    assert images_for(path) == expected


def test_each_image_has_a_registry_repository() -> None:
    script = step("detect", "Detect changed images")["run"]
    for image in ALL_IMAGES:
        assert f"{image})" in script
        assert f"hitsai/midataworks-{image}" in script
    assert "ALL=(backend frontend datajuicer designer)" in script


# --- the test gate ----------------------------------------------------------------------------


@pytest.fixture
def gate_env(tmp_path: Path) -> dict[str, Any]:
    """A git repo with two commits, and stub gh/sleep on PATH."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    (repo / "README.md").write_text("one\n")
    (repo / "backend").mkdir()
    (repo / "backend" / "app.py").write_text("x = 1\n")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "tested")
    tested = git("rev-parse", "HEAD")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh_calls"
    (bin_dir / "gh").write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'case "$*" in *head_sha=*) printf "%s" "$STUB_RUN" ;; *) printf "%s" "$STUB_PREV" ;; esac\n'
    )
    (bin_dir / "sleep").write_text("#!/bin/sh\nexit 0\n")
    for stub in bin_dir.iterdir():
        stub.chmod(0o755)
    return {"repo": repo, "git": git, "tested": tested, "bin": bin_dir, "calls": calls}


def run_gate(
    gate_env: dict[str, Any], *, run: str, prev: str = ""
) -> subprocess.CompletedProcess[str]:
    script = step("gate", "Require Backend Tests to have passed for this commit")["run"]
    env = {
        **os.environ,
        "PATH": f"{gate_env['bin']}:{os.environ['PATH']}",
        "SHA": gate_env["git"]("rev-parse", "HEAD"),
        "GITHUB_REPOSITORY": "hitsainet/miDataworks",
        "GH_TOKEN": "stub",
        "STUB_RUN": run,
        "STUB_PREV": prev,
    }
    return subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=gate_env["repo"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def commit_change(gate_env: dict[str, Any], rel: str) -> None:
    path = gate_env["repo"] / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n")
    gate_env["git"]("add", "-A")
    gate_env["git"]("commit", "-q", "-m", f"change {rel}")


class TestTheGateScript:
    def test_a_passing_suite_publishes_at_once(self, gate_env: dict[str, Any]) -> None:
        result = run_gate(gate_env, run="completed success")
        assert result.returncode == 0, result.stdout + result.stderr
        calls = [x for x in gate_env["calls"].read_text().splitlines() if x.startswith("api ")]
        assert len(calls) == 1 and "head_sha=" in calls[0]

    @pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
    def test_a_red_suite_refuses(self, gate_env: dict[str, Any], conclusion: str) -> None:
        result = run_gate(gate_env, run=f"completed {conclusion}")
        assert result.returncode == 1
        assert "Refusing to publish" in result.stdout

    def test_a_suite_that_never_finishes_times_out_and_refuses(
        self, gate_env: dict[str, Any]
    ) -> None:
        result = run_gate(gate_env, run="in_progress ")
        assert result.returncode == 1 and "Timed out" in result.stdout

    def test_no_run_publishes_only_when_backend_is_unchanged_since_a_pass(
        self, gate_env: dict[str, Any]
    ) -> None:
        commit_change(gate_env, "README.md")
        result = run_gate(gate_env, run="", prev=f"{gate_env['tested']} success")
        assert result.returncode == 0, result.stdout + result.stderr

    @pytest.mark.parametrize("rel", ["backend/app.py", "datajuicer/requirements.txt"])
    def test_no_run_refuses_when_backend_source_changed(
        self, gate_env: dict[str, Any], rel: str
    ) -> None:
        commit_change(gate_env, rel)
        result = run_gate(gate_env, run="", prev=f"{gate_env['tested']} success")
        assert result.returncode == 1 and "Refusing to publish untested" in result.stdout

    def test_no_run_refuses_when_the_last_run_failed(self, gate_env: dict[str, Any]) -> None:
        result = run_gate(gate_env, run="", prev=f"{gate_env['tested']} failure")
        assert result.returncode == 1

    def test_no_run_and_no_history_refuses(self, gate_env: dict[str, Any]) -> None:
        result = run_gate(gate_env, run="", prev="")
        assert result.returncode == 1

    def test_the_gate_reads_the_workflow_that_exists(self) -> None:
        backend_tests = load(REPO / ".github" / "workflows" / "backend-tests.yml")
        script = step("gate", "Require Backend Tests to have passed for this commit")["run"]
        assert f'select(.name=="{backend_tests["name"]}")' in script


class TestTheWiring:
    def test_the_build_waits_for_the_gate(self) -> None:
        assert job("build")["needs"] == ["detect", "gate"]

    def test_the_gate_runs_for_the_backend_and_datajuicer_images(self) -> None:
        condition = job("gate")["if"]
        assert "contains(needs.detect.outputs.images, 'backend')" in condition
        assert "contains(needs.detect.outputs.images, 'datajuicer')" in condition

    def test_the_gated_legs_refuse_first(self) -> None:
        first = job("build")["steps"][0]
        assert first["if"] == (
            "(matrix.name == 'backend' || matrix.name == 'datajuicer' || matrix.name == 'designer') "
            "&& needs.gate.result != 'success'"
        )
        assert "exit 1" in first["run"]

    def test_a_failed_gate_does_not_skip_the_frontend_leg(self) -> None:
        # A job-level gate condition would skip every matrix leg (miLLM's stranded admin-ui).
        condition = job("build")["if"]
        assert "always()" in condition
        assert "needs.gate.result != 'failure'" not in condition
        assert "needs.gate.result == 'success'" not in condition

    def test_the_image_records_the_commit_detect_diffs_from(self) -> None:
        build = step("build", "Build and push ${{ matrix.name }} image")
        assert "org.opencontainers.image.revision=${{ github.sha }}" in build["with"]["labels"]
        assert build["with"]["provenance"] == "mode=max"

    def test_it_runs_only_on_the_public_mirror(self) -> None:
        for name in ("detect", "gate", "build"):
            assert "hitsainet/miDataworks" in job(name)["if"]
            assert "Onegaishimas" not in job(name)["if"]
