"""The Data Designer environment is exactly designer/constraints.txt (2026-10-22).

The image and CI job ``designer-tests`` both install ``requirements.txt -c constraints.txt``, and this
suite runs inside that environment, so it can check the result rather than the recipe: every
installed distribution is pinned at the installed version, and every pin is installed. A package
that floats (as setuptools did when python:3.13-slim stopped shipping it) or a pin the resolver no
longer needs fails here. Only the test tooling CI adds on top is allowed unpinned.
"""

from __future__ import annotations

import importlib.metadata
import re
import sysconfig
from pathlib import Path

CONSTRAINTS = Path(__file__).resolve().parents[1] / "constraints.txt"

#: Installed by the test job, not the image; allowed only while the constraints do not name them.
TEST_TOOLING = {"pip", "pytest", "iniconfig", "pluggy", "pygments", "packaging"}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pinned() -> dict[str, str]:
    out = {}
    for raw in CONSTRAINTS.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            m = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
            assert m, f"constraints.txt: not an exact pin: {raw!r}"
            out[norm(m.group(1))] = m.group(2)
    return out


def installed() -> dict[str, str]:
    """What pip installed: site-packages only, as ``pip freeze`` reads it. Not all of sys.path:
    ray puts its vendored ``ray/thirdparty_files`` (colorama and others) on sys.path once
    imported, and those are part of ray, not distributions the image installs."""
    roots = sorted({sysconfig.get_paths()["purelib"], sysconfig.get_paths()["platlib"]})
    return {
        norm(d.metadata["Name"]): d.version for d in importlib.metadata.distributions(path=roots)
    }


def test_the_library_is_pinned_and_installed() -> None:
    # A vacuous comparison (wrong file, empty environment) would pass everything below.
    assert "data-designer" in pinned() and "data-designer" in installed()
    assert len(pinned()) > 80


def test_every_installed_distribution_is_pinned_at_its_version() -> None:
    pins = pinned()
    wrong = {
        name: (version, pins.get(name))
        for name, version in installed().items()
        if pins.get(name) != version and not (name in TEST_TOOLING and name not in pins)
    }
    assert not wrong, f"installed (version, pinned) that differ from constraints.txt: {wrong}"


def test_every_pin_is_installed() -> None:
    missing = sorted(set(pinned()) - set(installed()))
    assert not missing, f"pinned in constraints.txt but not installed: {missing}"
