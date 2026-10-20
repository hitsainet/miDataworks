"""CI runs the WHOLE backend test tree (R-03.66, ADR-022; Foundation task 13.3).

miStudio's backend workflow ran ``pytest tests/ --ignore=tests/integration -m "not slow"`` and
its CI stayed red for a week over a stale test outside the directory it ran; miLLM's still carries
eight ``--ignore=`` flags. This reads ``.github/workflows/backend-tests.yml`` and fails if the
command narrows the tree in any way — a path below ``tests``, an ``--ignore``/``--deselect``, a
marker or keyword filter — or if the workflow stops running for every push and pull request.

It also closes the two side doors a narrowing could move to: ``addopts`` in ``pyproject.toml`` and
``collect_ignore`` in a conftest.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
BACKEND = REPO / "backend"
WORKFLOWS = REPO / ".github" / "workflows"

#: Options that narrow what pytest collects or runs. Any of them on the CI command is a failure.
NARROWING = ("--ignore", "--ignore-glob", "--deselect", "-m", "-k", "--lf", "--last-failed")


def load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    # YAML 1.1 reads the bare key `on` as the boolean True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    out = []
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            out.append((job_name, job, step))
    return out


def pytest_steps(
    workflow: dict[str, Any], job_name: str = "test"
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pytest steps of one job (the backend job by default; ``datajuicer-tests`` is checked
    separately below)."""
    return [
        (job, step)
        for name, job, step in steps(workflow)
        if name == job_name
        and "pytest" in step.get("run", "")
        and "pip install" not in step.get("run", "")
    ]


def working_directory(job: dict[str, Any], step: dict[str, Any]) -> str:
    return str(
        step.get("working-directory")
        or job.get("defaults", {}).get("run", {}).get("working-directory")
        or "."
    )


def pytest_argv(run: str) -> list[str]:
    """The tokens after ``pytest`` on the one line that invokes it."""
    lines = [line for line in run.replace("\\\n", " ").splitlines() if "pytest" in line]
    assert len(lines) == 1, f"expected one pytest command line, got {lines}"
    tokens = shlex.split(lines[0])
    start = next(i for i, t in enumerate(tokens) if t.endswith("pytest"))
    return tokens[start + 1 :]


class TestBackendWorkflow:
    def test_the_workflow_is_named_for_the_image_gate(self) -> None:
        # docker-images.yml's gate selects runs by this exact name.
        assert load("backend-tests.yml")["name"] == "Backend Tests"

    def test_it_runs_on_every_push_and_pull_request_with_no_path_filter(self) -> None:
        triggers = load("backend-tests.yml")["on"]
        for event in ("push", "pull_request"):
            assert event in triggers, f"backend tests no longer run on {event}"
            config = triggers[event] or {}
            for key in ("paths", "paths-ignore"):
                assert key not in config, f"{event} has a {key} filter: {config[key]}"

    def test_exactly_one_step_runs_pytest(self) -> None:
        assert len(pytest_steps(load("backend-tests.yml"))) == 1

    def test_the_command_collects_the_whole_tree(self) -> None:
        ((job, step),) = pytest_steps(load("backend-tests.yml"))
        argv = pytest_argv(step["run"])
        positional = [t for t in argv if not t.startswith("-") and not t.isdigit()]
        # `-n 4 --dist loadfile` take values; drop those values from the positional list.
        values = {argv[i + 1] for i, t in enumerate(argv[:-1]) if t in ("-n", "--dist")}
        paths = [p for p in positional if p not in values]
        cwd = working_directory(job, step).rstrip("/")
        if cwd == "backend":
            assert paths == ["tests"], f"CI collects {paths}, not the whole tree"
        else:
            assert paths == ["backend/tests"], f"CI collects {paths}, not the whole tree"

    @pytest.mark.parametrize("option", NARROWING)
    def test_no_narrowing_option(self, option: str) -> None:
        ((_, step),) = pytest_steps(load("backend-tests.yml"))
        for token in pytest_argv(step["run"]):
            assert not (
                token == option or token.startswith(option + "=")
            ), f"the backend CI command carries {token!r}; R-03.66 forbids narrowing the tree"

    def test_a_failure_cannot_be_swallowed(self) -> None:
        ((_, step),) = pytest_steps(load("backend-tests.yml"))
        assert not step.get("continue-on-error"), "continue-on-error makes a red suite green"
        assert "|| true" not in step["run"] and "|| exit 0" not in step["run"]

    def test_real_postgres_15_and_redis_7(self) -> None:
        workflow = load("backend-tests.yml")
        ((job, _),) = pytest_steps(workflow)
        images = {name: svc["image"] for name, svc in job.get("services", {}).items()}
        assert images.get("postgres") == "postgres:15", images
        assert images.get("redis") == "redis:7", images


class TestDatajuicerJob:
    """Feature 003 (FTASKS 13.4): the Data-Juicer tests run in their own job, whole directory."""

    def test_the_job_runs_the_whole_datajuicer_test_directory(self) -> None:
        workflow = load("backend-tests.yml")
        assert "datajuicer-tests" in workflow["jobs"]
        ((job, step),) = pytest_steps(workflow, "datajuicer-tests")
        argv = pytest_argv(step["run"])
        assert working_directory(job, step) in {".", ""}
        assert [t for t in argv if not t.startswith("-")] == ["datajuicer/tests"]
        for token in argv:
            for option in NARROWING:
                assert not (token == option or token.startswith(option + "=")), token
        assert not step.get("continue-on-error")

    def test_it_installs_the_image_pins(self) -> None:
        workflow = load("backend-tests.yml")
        runs = " ".join(s.get("run", "") for s in workflow["jobs"]["datajuicer-tests"]["steps"])
        assert "-r datajuicer/requirements.txt -c datajuicer/constraints.txt" in runs

    def test_the_job_is_in_the_workflow_the_image_gate_reads(self) -> None:
        """docker-images.yml's gate requires the "Backend Tests" run to succeed; the Data-Juicer
        job is in that workflow, so it gates the Data-Juicer image."""
        workflow = load("backend-tests.yml")
        assert workflow["name"] == "Backend Tests" and "datajuicer-tests" in workflow["jobs"]
        gate = (WORKFLOWS / "docker-images.yml").read_text()
        assert 'select(.name=="Backend Tests")' in gate
        job = workflow["jobs"]["datajuicer-tests"]
        assert not job.get("continue-on-error") and "if" not in job


class TestDesignerJob:
    """ADR-010 amendment (2026-10-07): the Data Designer tests run in their own job, whole directory,
    in the workflow the image gate reads."""

    def test_the_job_runs_the_whole_designer_test_directory(self) -> None:
        workflow = load("backend-tests.yml")
        ((job, step),) = pytest_steps(workflow, "designer-tests")
        argv = pytest_argv(step["run"])
        assert [t for t in argv if not t.startswith("-")] == ["designer/tests"]
        for token in argv:
            for option in NARROWING:
                assert not (token == option or token.startswith(option + "=")), token
        assert not step.get("continue-on-error") and "if" not in job
        runs = " ".join(s.get("run", "") for s in job["steps"])
        assert "-r designer/requirements.txt -c designer/constraints.txt" in runs


class TestSideDoors:
    def test_addopts_does_not_narrow(self) -> None:
        import tomllib

        config = tomllib.loads((BACKEND / "pyproject.toml").read_text())
        addopts = config["tool"]["pytest"]["ini_options"].get("addopts", "")
        # Compare by prefix: `--ignore=tests/integration` is one token, not two (control C05).
        for token in shlex.split(addopts):
            for option in NARROWING:
                assert not (
                    token == option or token.startswith(option + "=")
                ), f"addopts carries {token!r}"
        assert config["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]

    def test_no_conftest_ignores_collection(self) -> None:
        for conftest in (BACKEND / "tests").rglob("conftest.py"):
            text = conftest.read_text()
            assert "collect_ignore" not in text, f"{conftest} hides tests from collection"


class TestFrontendWorkflow:
    def test_type_check_lint_unit_tests_and_build(self) -> None:
        workflow = load("frontend-ci.yml")
        runs = " ; ".join(step.get("run", "") for _, _, step in steps(workflow))
        for command in (
            "npm ci",
            "npm run type-check",
            "npm run lint",
            "vitest run",
            "npm run build",
        ):
            assert command in runs, f"frontend CI no longer runs {command!r}"

    def test_it_runs_on_every_push_and_pull_request(self) -> None:
        triggers = load("frontend-ci.yml")["on"]
        for event in ("push", "pull_request"):
            assert event in triggers
            assert "paths" not in (triggers[event] or {})
