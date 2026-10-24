"""One version of each Python package across the backend and both side images.

Operator request 2026-10-21 (suite dependency baseline): each package gets ONE version across
the backend, the Data Designer image and the Data-Juicer image, unless a side image's own library
forbids it upstream. Until 2026-10-22 the backend pinned only its direct dependencies, so its
transitive set floated per build and could not be held to anything; ``backend/constraints.txt``
now pins the whole set, and every install (the images, CI, the dev venv) goes through it.

The pins are found by scanning every tracked requirements and constraints file, never from a
hand-kept list of files. The exceptions below are the only places versions may differ. Each one
quotes the upstream requirement that forces it and names the library release that carries it,
and each one EXPIRES:

* if the image no longer pins that library release (the library moved: re-check the bound);
* if the image no longer holds the excepted version (someone changed it: re-review it);
* if the image's version agrees with the rest again (the exception is no longer needed).

The resolver output proving each refusal is in ``0xcc/reviews/deps_sync_2026-10-21.md``.
"""

from __future__ import annotations

import importlib.metadata
import re
import subprocess
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
IMAGES = ("backend", "designer", "datajuicer")
#: Every image with its own constraints file; the runner's (feature 018) is too small for the scan
#: threshold below, so it is not in IMAGES, but its files are its own image, never the backend's.
ALL_IMAGES = (*IMAGES, "runner")
REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*([<>=!~].*)?$")


@dataclass(frozen=True)
class UpstreamBound:
    held: str  # the version the side image pins
    library: str  # name==version of the release carrying the bound, as the image pins it
    upstream: str  # the requirement that release declares, quoted from its metadata


#: (image, package) -> why that image may hold a different version from the rest.
EXCEPTIONS: dict[tuple[str, str], UpstreamBound] = {
    ("designer", "huggingface-hub"): UpstreamBound(
        "1.33.0", "data-designer==0.9.4", "huggingface-hub<2,>=1.0.1"
    ),
    ("designer", "pyarrow"): UpstreamBound("24.0.0", "data-designer==0.9.4", "pyarrow<25,>=24"),
    ("designer", "mcp"): UpstreamBound("1.30.0", "data-designer-engine==0.9.4", "mcp<2,>=1.29.0"),
    ("designer", "fsspec"): UpstreamBound(
        "2025.12.0", "data-designer-engine==0.9.4", "fsspec<2026,>=2025.3.0"
    ),
    ("designer", "opentelemetry-api"): UpstreamBound(
        "1.43.0", "data-designer==0.9.4", "opentelemetry-api<1.44,>=1.43"
    ),
    # data-designer-engine 0.9.4 requires sqlfluff<5,>=4.1.0, and every sqlfluff release through
    # 4.4.0 (the newest, 2026-10-22) caps click below 8.5.
    ("designer", "click"): UpstreamBound("8.4.2", "sqlfluff==4.4.0", "click<8.5.0"),
    ("designer", "duckdb"): UpstreamBound(
        "1.5.6", "data-designer-engine==0.9.4", "duckdb<2,>=1.5.0"
    ),
    ("designer", "pillow"): UpstreamBound(
        "12.3.0", "data-designer-config==0.9.4", "pillow<13,>=12.3.0"
    ),
    ("datajuicer", "dill"): UpstreamBound("0.3.8", "py-data-juicer==1.6.0", "dill==0.3.8"),
    ("datajuicer", "multiprocess"): UpstreamBound(
        "0.70.16", "py-data-juicer==1.6.0", "multiprocess==0.70.16"
    ),
}


def _tracked(*patterns: str) -> list[Path]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--", *patterns],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [REPO / line for line in out.splitlines() if line]


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def image_of(rel: str) -> str:
    """designer/…, datajuicer/… and runner/… are the side images; everything else (backend/…,
    and the prototype's root requirements.txt) is held to the backend's versions."""
    top = rel.split("/", 1)[0]
    return top if top in ALL_IMAGES[1:] else "backend"


def parse_text(text: str, where: str = "<text>") -> list[tuple[str, str]]:
    """(package, specifier) for each requirement line; options and comments are skipped."""
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = REQ_LINE.match(line)
        assert m, f"{where}: unparseable requirement {raw!r}"
        out.append((norm(m.group(1)), (m.group(2) or "").replace(" ", "")))
    return out


def parse(path: Path) -> list[tuple[str, str]]:
    return parse_text(path.read_text(), str(path.relative_to(REPO)))


def pin_files() -> list[Path]:
    return _tracked("*requirements*.txt", "**/*requirements*.txt", "**/constraints*.txt")


def pins() -> list[tuple[str, str, str, str]]:
    """(image, file, package, version) for every exact pin; the local tag (+cpu) is dropped."""
    out = []
    for path in pin_files():
        rel = str(path.relative_to(REPO))
        for package, spec in parse(path):
            if spec.startswith("=="):
                out.append((image_of(rel), rel, package, spec[2:].split("+", 1)[0]))
    return out


def constraints(image: str) -> dict[str, str]:
    return {p: v for img, f, p, v in pins() if img == image and f == f"{image}/constraints.txt"}


# --- the scan --------------------------------------------------------------------------------


def test_the_scan_finds_all_three_constraint_sets() -> None:
    # An empty or partial scan would make every other test here vacuous.
    files = {str(p.relative_to(REPO)) for p in pin_files()}
    for image in IMAGES:
        assert f"{image}/constraints.txt" in files, files
        assert len(constraints(image)) > 80, (image, len(constraints(image)))


def test_every_pin_in_a_constraints_file_is_exact() -> None:
    loose = [
        (str(path.relative_to(REPO)), p, s)
        for path in pin_files()
        if path.name == "constraints.txt"
        for p, s in parse(path)
        if not re.fullmatch(r"==[0-9][^,;]*", s)
    ]
    assert not loose, f"constraints must pin with ==: {loose}"


def test_each_direct_pin_is_in_its_images_constraints() -> None:
    """A requirement the constraints file does not name would float; one it names at another
    version makes pip refuse the install."""
    missing = []
    for img, f, p, _v in pins():
        if f.endswith("requirements.txt") and f != "requirements.txt":
            if p not in constraints(img):
                missing.append((f, p))
    assert not missing, f"direct pins absent from their image's constraints.txt: {missing}"


# --- one version -----------------------------------------------------------------------------


def test_every_package_has_one_version() -> None:
    versions: dict[str, dict[str, set[str]]] = {}
    for img, f, p, v in pins():
        if (img, p) in EXCEPTIONS:
            continue
        versions.setdefault(p, {}).setdefault(v, set()).add(f)
    split = {p: v for p, v in versions.items() if len(v) > 1}
    assert (
        not split
    ), f"pins disagree across files (add an exception only with an upstream bound): {split}"


def test_each_exception_still_holds_its_version_under_its_library() -> None:
    for (image, package), exc in EXCEPTIONS.items():
        held = {v for img, _f, p, v in pins() if img == image and p == package}
        assert held == {
            exc.held
        }, f"{image} pins {package} at {held}, not the excepted {exc.held}: re-review the exception"
        lib, lib_version = exc.library.split("==")
        assert constraints(image).get(norm(lib)) == lib_version, (
            f"{image} no longer pins {exc.library}: re-check whether it still requires "
            f"{exc.upstream!r}, then update or drop the exception"
        )


def test_every_exception_is_still_needed() -> None:
    for (image, package), exc in EXCEPTIONS.items():
        others = {v for img, _f, p, v in pins() if img != image and p == package}
        assert others, f"{package} is pinned only in {image}: the exception guards nothing"
        assert (
            exc.held not in others
        ), f"{image}'s {package} {exc.held} agrees with the rest again: drop the exception"


# --- every install goes through its constraints ----------------------------------------------


def _workflow(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text())
    return data


def _install_runs(job: str) -> list[str]:
    steps = _workflow("backend-tests.yml")["jobs"][job]["steps"]
    return [s["run"] for s in steps if "pip install -r" in s.get("run", "")]


def test_ci_installs_every_image_set_with_its_constraints() -> None:
    for job, constraint in (
        ("test", "-c constraints.txt"),  # working-directory: backend
        ("designer-tests", "-c designer/constraints.txt"),
        ("datajuicer-tests", "-c datajuicer/constraints.txt"),
    ):
        runs = _install_runs(job)
        assert runs, f"{job} installs nothing"
        for run in runs:
            assert constraint in run, f"{job}: {run!r} does not install with {constraint}"


def test_every_image_installs_with_its_constraints() -> None:
    for image in ALL_IMAGES:
        text = (REPO / image / "Dockerfile").read_text()
        installs = re.findall(r"pip install(?: --no-cache-dir)? -r (\S+)([^\n&]*)", text)
        assert installs, f"{image}/Dockerfile installs nothing"
        for req, rest in installs:
            assert (
                "-c /app/constraints.txt" in rest
            ), f"{image}: {req} installed without constraints"


# --- the interpreter running this suite ------------------------------------------------------


def test_the_running_environment_matches_the_backend_constraints() -> None:
    """The dev venv and CI must run what the image runs (suite memory
    ``dev-venv-vs-image-version-drift``): every installed package the backend constraints name is
    at the pinned version."""
    pinned = constraints("backend")
    drift = []
    # site-packages only, as pip freeze reads it: a vendored copy another package puts on
    # sys.path (ray does this in the Data-Juicer image) is not an installed distribution.
    roots = sorted({sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]})
    seen = 0
    for dist in importlib.metadata.distributions(path=roots):
        name = norm(dist.metadata["Name"])
        seen += name in pinned
        if name in pinned and dist.version.split("+", 1)[0] != pinned[name]:
            drift.append(f"{name} {dist.version} (pinned {pinned[name]})")
    # A scan that finds nothing reports no drift: the runtime set alone is 96 packages.
    assert seen > 80, f"only {seen} pinned packages found installed: wrong environment or scan"
    assert not drift, (
        "this environment differs from backend/constraints.txt; reinstall with "
        f"`pip install -r requirements-dev.txt -c constraints.txt`: {sorted(drift)}"
    )


# --- the parser itself -----------------------------------------------------------------------


def test_the_parser_reads_extras_local_tags_and_names_alike() -> None:
    parsed = dict(
        parse_text(
            "# comment\n--extra-index-url https://x\nsqlalchemy[asyncio]==2.1.5\n"
            "torch==2.14.1+cpu  # cpu wheel\nPyYAML==6.0.3\ntyping_extensions==4.16.0\n"
        )
    )
    assert parsed == {
        "sqlalchemy": "==2.1.5",
        "torch": "==2.14.1+cpu",
        "pyyaml": "==6.0.3",
        "typing-extensions": "==4.16.0",
    }
    assert image_of("designer/constraints.txt") == "designer"
    assert image_of("datajuicer/requirements.txt") == "datajuicer"
    assert image_of("backend/requirements-dev.txt") == "backend"
    assert image_of("requirements.txt") == "backend"
