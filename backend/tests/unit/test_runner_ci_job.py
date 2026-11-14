"""The ``runner-tests`` CI job exists, runs the whole runner suite and gates the image (FTASKS 4.10).

FR-018.23: the runner tests are part of "Backend Tests", so sync-to-clean's gate (which requires the
whole workflow to conclude success) covers them, gating the runner image like datajuicer-tests gates
the Data-Juicer image. It installs the runner image's pins, builds the guest bundle, sets
``MIDATAWORKS_REQUIRE_RUNNER=1`` and runs ``runner/tests``.
"""

from __future__ import annotations

from tests.unit.test_ci_runs_whole_tree import NARROWING, load, pytest_argv, pytest_steps


def test_the_runner_tests_job_exists_and_runs_the_whole_directory() -> None:
    workflow = load("backend-tests.yml")
    assert "runner-tests" in workflow["jobs"]
    ((job, step),) = pytest_steps(workflow, "runner-tests")
    argv = pytest_argv(step["run"])
    assert [t for t in argv if not t.startswith("-")] == ["runner/tests"]
    for token in argv:
        for option in NARROWING:
            assert not (token == option or token.startswith(option + "=")), token
    assert not step.get("continue-on-error") and "if" not in job


def test_it_installs_the_runner_pins_builds_the_bundle_and_requires_it() -> None:
    workflow = load("backend-tests.yml")
    job = workflow["jobs"]["runner-tests"]
    runs = " ".join(s.get("run", "") for s in job["steps"])
    assert "-r runner/requirements.txt -c runner/constraints.txt" in runs
    assert "build_bundle.py" in runs
    assert job["env"]["MIDATAWORKS_REQUIRE_RUNNER"] == "1"


def test_the_job_is_in_the_workflow_the_image_gate_reads() -> None:
    gate = load("sync-to-clean.yml")["jobs"]["gate"]
    assert "backend-tests.yml" in gate["env"]["REQUIRED_WORKFLOWS"].split()
    job = load("backend-tests.yml")["jobs"]["runner-tests"]
    assert not job.get("continue-on-error") and "if" not in job
