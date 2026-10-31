"""Where a sibling suite repository (miStudio, miLLM, miForge) is checked out.

The suite repositories are always checked out side by side, so a sibling is found BESIDE this
checkout: ``REPO.parent / name``. An environment variable overrides it (``MISTUDIO_REPO``,
``MIFORGE_REPO``), exactly as before. There is deliberately no absolute default: the suite moved
from ``/home/x-sean/app/<repo>`` to ``/home/x-sean/app/miSuite/<repo>`` on 2026-10-16 and a
hard-coded path would have pointed every cross-repo check at a directory that no longer exists,
which those checks report as a SKIP, so the pair would quietly stop being proved.

The override variables are ``MISTUDIO_REPO``, ``MILLM_REPO`` and ``MIFORGE_REPO`` (the rule-signal
and runner contracts are vendored by miStudio and miLLM, read through ``MILLM_REPO``).

Callers keep their own absent-checkout handling (a loud ``CROSS-REPO`` skip, or a failure under
``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1``); this module only answers "where".
"""

from __future__ import annotations

import os
from pathlib import Path

#: This repository's root (``backend/tests/support/siblings.py`` is three levels below it).
REPO = Path(__file__).resolve().parents[3]


def sibling_repo(name: str, env: str) -> Path:
    """The checkout of sibling ``name``: ``$env`` when set and non-empty, else ``REPO.parent / name``."""
    override = os.environ.get(env)
    if override:
        return Path(override).expanduser()
    return REPO.parent / name
