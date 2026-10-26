"""miDataworks runs on ONE Python version, and every place that names it agrees.

Operator decision 2026-10-21 (suite memory ``python-313-upgrade-decision``): the suite moves
from 3.11 to 3.13, one app at a time; miDataworks is the last. miDataworks ships THREE Python
images, and the version is written in places that nothing tied together:

* ``backend/Dockerfile``'s ``FROM python:X.Y-slim`` - what the API, every worker, Beat and the
  MCP server run. This is THE version; everything else is compared with it;
* ``designer/Dockerfile`` and ``datajuicer/Dockerfile`` - the Data Designer and Data-Juicer side
  workers, each with its own pins (PADR ADR-010 and its 2026-10-07 amendment);
* every ``actions/setup-python`` step in ``.github/workflows/`` - what CI tests on. The
  ``datajuicer-tests`` and ``designer-tests`` jobs test the side images' pins, so each must run
  its own image's Python; every other step runs the backend's;
* the side images' ``constraints.txt`` headers - the Python the frozen set was resolved on;
* ``requires-python``'s floor, Black's and Ruff's ``target-version`` and MyPy's
  ``python_version`` - what an install promises and what the source is checked as.

A side image may stay on another Python ONLY when its library refuses the backend's, and only
as a recorded exception in ``SIDE_IMAGE_EXCEPTIONS`` below (with the refusal it rests on). The
table is self-expiring: an entry for an image that is back on the backend's version fails, so
the exception cannot outlive its reason.

Drift between these is the failure suite memory ``dev-venv-vs-image-version-drift`` records: a
suite green on one version, an image shipping another. Every value is read from the file it
governs; nothing here restates "3.13" except the parser self-checks, so the next upgrade edits
the files and this test stays as is.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
PYPROJECT = REPO / "backend" / "pyproject.toml"
WORKFLOWS = REPO / ".github" / "workflows"
BACKEND_TESTS = "backend-tests.yml"

#: Image name -> its Dockerfile. "backend" is the authority.
DOCKERFILES = {
    "backend": REPO / "backend" / "Dockerfile",
    "designer": REPO / "designer" / "Dockerfile",
    "datajuicer": REPO / "datajuicer" / "Dockerfile",
}
#: Side image -> the pinned set frozen for it (its header names the Python it was frozen on).
CONSTRAINTS = {
    "designer": REPO / "designer" / "constraints.txt",
    "datajuicer": REPO / "datajuicer" / "constraints.txt",
}
#: (workflow file, job id) -> the image whose pins that job tests. Any other setup-python
#: step tests (or gates) the backend.
SIDE_IMAGE_JOBS = {
    (BACKEND_TESTS, "datajuicer-tests"): "datajuicer",
    (BACKEND_TESTS, "designer-tests"): "designer",
}
#: Side image -> (its own MAJOR.MINOR, the quoted refusal that keeps it there, the review
#: record). EMPTY since 2026-10-21: Data Designer 0.9.4 and Data-Juicer 1.6.0 both install and
#: run on 3.13 (0xcc/reviews/python_313_2026-10-21.md).
SIDE_IMAGE_EXCEPTIONS: dict[str, tuple[tuple[int, int], str, str]] = {}

_FROM_PYTHON = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?python:(\d+)\.(\d+)[^\s]*", re.M | re.I)
#: The header phrase that names the frozen-on Python; anchored, so a later mention of another
#: version in the same comment (e.g. "torch requires it from 3.12") is not read as the claim.
_FROZEN_ON = re.compile(r"^#.*\bpip freeze in a clean Python (\d+)\.(\d+) env\b", re.M)


def _dockerfile_versions(text: str) -> list[tuple[int, int]]:
    return [(int(a), int(b)) for a, b in _FROM_PYTHON.findall(text)]


def _setup_python_steps(workflow: dict[str, Any]) -> list[tuple[str, Any]]:
    """Every ``actions/setup-python`` step, as (job id, its ``python-version`` or None)."""
    found: list[tuple[str, Any]] = []
    for job_id, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if str(step.get("uses", "")).startswith("actions/setup-python"):
                found.append((job_id, (step.get("with") or {}).get("python-version")))
    return found


def _mm(text: Any) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)", str(text).strip())
    assert m, f"not a MAJOR.MINOR version: {text!r}"
    return int(m.group(1)), int(m.group(2))


def _image_version(image: str) -> tuple[int, int]:
    path = DOCKERFILES[image]
    versions = _dockerfile_versions(path.read_text())
    assert versions, f"{path.relative_to(REPO)} has no `FROM python:X.Y` line - the parser is blind"
    assert len(set(versions)) == 1, f"{path.relative_to(REPO)} names several Pythons: {versions}"
    return versions[0]


def _the_shipped_version() -> tuple[int, int]:
    return _image_version("backend")


def _expected_for(image: str) -> tuple[int, int]:
    """The backend's version, or the side image's recorded exception."""
    if image in SIDE_IMAGE_EXCEPTIONS:
        return SIDE_IMAGE_EXCEPTIONS[image][0]
    return _the_shipped_version()


def _all_setup_python_steps() -> list[tuple[str, str, Any]]:
    steps: list[tuple[str, str, Any]] = []
    for path in sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")]):
        for job, version in _setup_python_steps(yaml.safe_load(path.read_text())):
            steps.append((path.name, job, version))
    return steps


# ── the parsers, against inputs whose answers are known ───────────────────────────────


class TestTheParsersSeeWhatTheyClaimTo:
    def test_a_from_line_is_read_and_a_comment_is_not(self) -> None:
        text = "# FROM python:3.9-slim\nFROM python:3.13-slim AS base\nfrom base\n"
        assert _dockerfile_versions(text) == [(3, 13)]

    def test_a_lowercase_from_is_read(self) -> None:
        assert _dockerfile_versions("from python:3.12-slim as runtime\n") == [(3, 12)]

    def test_a_setup_python_step_without_a_version_is_reported_not_skipped(self) -> None:
        wf = {"jobs": {"a": {"steps": [{"uses": "actions/setup-python@v7"}]}}}
        assert _setup_python_steps(wf) == [("a", None)]

    def test_a_quoted_and_an_unquoted_version_read_the_same(self) -> None:
        # Unquoted `3.10` is the YAML float 3.1 - the reason workflows quote it.
        wf = yaml.safe_load(
            "jobs:\n a:\n  steps:\n  - uses: actions/setup-python@v7\n"
            "    with:\n     python-version: '3.13'\n"
        )
        assert [_mm(v) for _, v in _setup_python_steps(wf)] == [(3, 13)]

    def test_the_constraints_header_parser_reads_the_frozen_version(self) -> None:
        text = (
            "# The resolved set (pip freeze in a clean Python 3.13 env, 2026-10-21).\n"
            "# torch requires setuptools on Python 3.12 and later.\nx==1\n"
        )
        assert [(int(a), int(b)) for a, b in _FROZEN_ON.findall(text)] == [(3, 13)]


# ── every place agrees ─────────────────────────────────────────────────────────────────


class TestEveryPlaceNamesTheShippedVersion:
    def test_each_side_image_runs_the_backend_python_or_a_recorded_exception(self) -> None:
        shipped = _the_shipped_version()
        for image in ("designer", "datajuicer"):
            assert _image_version(image) == _expected_for(image), (
                f"{image}/Dockerfile runs {_image_version(image)}, the backend {shipped}; a side "
                "image may differ only with a SIDE_IMAGE_EXCEPTIONS entry quoting the refusal"
            )

    def test_an_exception_expires_when_its_image_is_back_on_the_backend_python(self) -> None:
        shipped = _the_shipped_version()
        for image, (version, refusal, record) in SIDE_IMAGE_EXCEPTIONS.items():
            assert image in CONSTRAINTS, f"exception for an unknown side image {image!r}"
            assert version != shipped, f"{image} is back on {shipped}: delete its exception"
            assert refusal.strip() and record.strip(), f"{image}'s exception quotes no refusal"

    def test_every_ci_python_is_its_image_python(self) -> None:
        steps = _all_setup_python_steps()
        # backend-tests.yml has one in the backend `test` job and one per side-image job, and
        # sync-to-clean.yml one in its gate. Missing any means the walk stopped seeing them,
        # and an empty comparison below would pass.
        jobs = {(wf, job) for wf, job, _ in steps}
        for needed in [(BACKEND_TESTS, "test"), *SIDE_IMAGE_JOBS, ("sync-to-clean.yml", "gate")]:
            assert needed in jobs, f"no setup-python step found in {needed}: {steps}"
        wrong = []
        for wf, job, version in steps:
            image = SIDE_IMAGE_JOBS.get((wf, job), "backend")
            if version is None or _mm(version) != _image_version(image):
                wrong.append((wf, job, version, f"{image} image runs {_image_version(image)}"))
        assert not wrong, f"these CI steps do not run their image's Python: {wrong}"

    def test_each_constraints_file_was_frozen_on_its_image_python(self) -> None:
        for image, path in CONSTRAINTS.items():
            found = [(int(a), int(b)) for a, b in _FROZEN_ON.findall(path.read_text())]
            assert len(found) == 1, (
                f"{path.relative_to(REPO)}'s header must name ONE Python it was frozen on "
                f"('pip freeze in a clean Python X.Y env'); found {found}"
            )
            assert found[0] == _image_version(image), (
                f"{path.relative_to(REPO)} says it was frozen on {found}; "
                f"{image}/Dockerfile runs {_image_version(image)}"
            )

    def test_requires_python_floor_is_the_image_python(self) -> None:
        spec = tomllib.loads(PYPROJECT.read_text())["project"]["requires-python"]
        m = re.fullmatch(r">=\s*(\d+\.\d+)", spec.strip())
        assert m, f"requires-python should be a bare floor, got {spec!r}"
        assert _mm(m.group(1)) == _the_shipped_version()

    def test_black_ruff_and_mypy_target_the_image_python(self) -> None:
        tools = tomllib.loads(PYPROJECT.read_text())["tool"]
        major, minor = _the_shipped_version()
        assert tools["black"]["target-version"] == [f"py{major}{minor}"]
        assert tools["ruff"]["target-version"] == f"py{major}{minor}"
        assert _mm(tools["mypy"]["python_version"]) == (major, minor)

    def test_the_interpreter_running_this_suite_meets_the_floor(self) -> None:
        """CI installs the shipped version, so this is equality there; a newer local venv
        is allowed, an older one is not a miDataworks environment."""
        assert sys.version_info[:2] >= _the_shipped_version()
