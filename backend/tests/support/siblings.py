"""Where a sibling suite repository (miWorkbench, miServer, miForge) is checked out, and what an
absent one means.

The suite repositories are checked out side by side, so a sibling is found BESIDE this checkout.
A caller names the sibling by its name; the lookup is:

1. its override variable (``MIWORKBENCH_REPO``, ``MISERVER_REPO``, ``MIFORGE_REPO``), when set,
   even if that path does not exist (the operator said where it is: a wrong path reads as absent);
2. else the folder of that name beside this checkout (``REPO.parent / "miWorkbench"``), whether or
   not it exists, so an absent sibling's message names where the checkout belongs.

**Retired spellings (``RETIRED``).** During the suite rebrand (ENH-009, feature 020, FR-020.8)
miStudio became miWorkbench and miLLM became miServer, and from P0 this lookup also read the old
variables (``MISTUDIO_REPO``, ``MILLM_REPO``) and the old folders (``../miStudio``, ``../miLLM``).
P7 retired them (suite R31, 2026-11-12; miServer FR-040.40, miForge ``RETIRED_ENV`` and miWorkbench
did the same); this repository followed on 2026-11-21. Nothing here reads them any more. They are
kept in ``RETIRED`` only so ``test_siblings_lookup.py`` proves each one is ignored. The old folder
name ``miStudio`` is since P5 the SUITE's own folder, so a fallback to it would compare against the
suite repository rather than miWorkbench.

**Absent checkouts (``absent``).** A missing sibling skips loudly (``CROSS-REPO:``), which is what
the public mirror and CI see (they check out this repository alone). Either require switch turns it
into a failure: this repository's ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` or the suite-wide
``MISTUDIO_REQUIRE_CROSS_REPO_CHECKS=1`` (the suite repository's current name, not a retired one;
suite ``CLAUDE.md``, "Cross-repo tests"). Before 2026-11-21 each caller read only the first of the
two, inline. There is deliberately no absolute default (the suite moved once already, 2026-10-16).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import NoReturn

import pytest

#: This repository's root (``backend/tests/support/siblings.py`` is three levels below it).
REPO = Path(__file__).resolve().parents[3]

#: Sibling name (also its folder beside this checkout) -> its override variable.
SIBLINGS: dict[str, str] = {
    "miWorkbench": "MIWORKBENCH_REPO",
    "miServer": "MISERVER_REPO",
    "miForge": "MIFORGE_REPO",
}

#: Retired by suite P7: (sibling, old override variable, old folder name). Read by nothing here.
RETIRED: tuple[tuple[str, str, str], ...] = (
    ("miWorkbench", "MISTUDIO_REPO", "miStudio"),
    ("miServer", "MILLM_REPO", "miLLM"),
)

#: Any of these set to "1" turns an absent sibling into a failure.
REQUIRE_SWITCHES: tuple[str, ...] = (
    "MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS",
    "MISTUDIO_REQUIRE_CROSS_REPO_CHECKS",
)


def sibling_repo(name: str) -> Path:
    """The checkout of sibling ``name``: its override variable, else ``REPO.parent / name``.

    Not checked for existence: the caller decides what an absent checkout means (``absent``).
    """
    override = os.environ.get(SIBLINGS[name])
    if override:
        return Path(override).expanduser()
    return REPO.parent / name


def required() -> bool:
    """True when any of ``REQUIRE_SWITCHES`` is "1"."""
    return any(os.environ.get(switch) == "1" for switch in REQUIRE_SWITCHES)


def absent(message: str) -> NoReturn:
    """Skip loudly (``CROSS-REPO: <message>``), or fail instead when ``required()``."""
    if required():
        pytest.fail(f"CROSS-REPO (required: {' or '.join(REQUIRE_SWITCHES)}=1): {message}")
    pytest.skip(f"CROSS-REPO: {message}")
