"""Where a sibling suite repository (miWorkbench, miServer, miForge) is checked out.

The suite repositories are checked out side by side, so a sibling is found BESIDE this checkout.
During the suite rebrand (ENH-009, feature 020, FR-020.8) miStudio becomes miWorkbench and miLLM
becomes miServer, and both the folders and the override variables change name. A caller names the
sibling by its NEW name only; the lookup order is:

1. the new override variable (``MIWORKBENCH_REPO``, ``MISERVER_REPO``, ``MIFORGE_REPO``);
2. the old one (``MISTUDIO_REPO``, ``MILLM_REPO``), read until P7;
3. the new folder beside this checkout (``REPO.parent / "miWorkbench"``);
4. the old folder (``REPO.parent / "miStudio"``);
5. else the NEW folder, absent (so a skip names where the checkout belongs).

There is deliberately no absolute default (the suite moved once already, 2026-10-16). Callers keep
their own absent-checkout handling (a loud ``CROSS-REPO`` skip, or a failure under
``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1``); this module only answers "where".
"""

from __future__ import annotations

import os
from pathlib import Path

#: This repository's root (``backend/tests/support/siblings.py`` is three levels below it).
REPO = Path(__file__).resolve().parents[3]

#: New name -> (old folder name, new override variable, old override variable).
SIBLINGS: dict[str, tuple[str, str, str]] = {
    "miWorkbench": ("miStudio", "MIWORKBENCH_REPO", "MISTUDIO_REPO"),
    "miServer": ("miLLM", "MISERVER_REPO", "MILLM_REPO"),
    "miForge": ("miForge", "MIFORGE_REPO", "MIFORGE_REPO"),
}


def sibling_repo(name: str) -> Path:
    """The checkout of sibling ``name`` (its NEW name); see the module docstring for the order."""
    old, new_env, old_env = SIBLINGS[name]
    for var in (new_env, old_env):
        override = os.environ.get(var)
        if override:
            return Path(override).expanduser()
    for folder in (name, old):
        candidate = REPO.parent / folder
        if candidate.is_dir():
            return candidate
    return REPO.parent / name
