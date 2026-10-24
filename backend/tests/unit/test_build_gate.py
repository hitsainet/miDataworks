"""The image build trusts the private CI verdict and never double-builds (ADR-024; 15.2, 15.5, 15.6).

Reads ``.github/workflows/docker-images.yml`` and RUNS its shell, not just its text:

- **The test gate** moved upstream on 2026-10-20 (operator directive: the mirror trusts the private
  repo's CI). The mirror-side ``gate`` job that polled THIS mirror's Backend Tests is gone; the
  private sync's gate, the snapshot trailers and the mirror's ``private-ci`` check are pinned - and
  run - in ``test_private_ci_trust.py``. What stays here is the wiring that check needs.
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
        # The only way a third-party operator package reaches the image (T-10); until 2026-10-22
        # a change to it rebuilt nothing.
        ("backend/requirements-operators.txt", ["backend"]),
        ("backend/constraints.txt", ["backend"]),
        # Test tooling only; the image never installs it.
        ("backend/requirements-dev.txt", []),
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
        ("designer/constraints.txt", ["designer"]),
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


class TestTheWiring:
    def test_the_build_waits_for_the_private_ci_verdict(self) -> None:
        # Every image, frontend included: the private gate covers Frontend CI too. No
        # `always()`, so a refused verdict skips the whole matrix rather than one leg.
        assert job("build")["needs"] == ["detect", "private-ci"]
        assert "always()" not in job("build")["if"]

    def test_no_job_waits_on_the_mirrors_own_test_runs(self) -> None:
        assert "gate" not in load()["jobs"]
        for name, body in load()["jobs"].items():
            assert "actions" not in (body.get("permissions") or {}), name

    def test_the_image_records_the_commit_detect_diffs_from(self) -> None:
        build = step("build", "Build and push ${{ matrix.name }} image")
        assert "org.opencontainers.image.revision=${{ github.sha }}" in build["with"]["labels"]
        assert build["with"]["provenance"] == "mode=max"

    def test_it_runs_only_on_the_public_mirror(self) -> None:
        for name in ("detect", "private-ci", "build"):
            assert "hitsainet/miDataworks" in job(name)["if"]
            assert "Onegaishimas" not in job(name)["if"]
