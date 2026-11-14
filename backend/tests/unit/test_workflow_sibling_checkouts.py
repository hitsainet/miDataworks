"""No workflow checks out a sibling by its OLD repository name alone (feature 020; FR-020.9;
FTASKS 7.3). Green today: no miDataworks workflow checks out a sibling at all (verified 2026-11-08);
this pins it, so a checkout added later names the new repository (or both while P1 runs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
#: Built from parts: the suite's origin-header gate counts every tracked line that spells an old
#: private repository name (rebrand_checks.py origin-headers), and a test's data must not add one.
OLD_SLUGS = {
    f"{org}/{name}" for org in ("onegaishimas", "hitsainet") for name in ("mistudio", "millm")
}
PLANTED = "/".join(("Onegaishimas", "miStudio"))


def checkout_repositories(paths: list[Path]) -> list[tuple[str, str]]:
    found = []
    for path in paths:
        doc: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        for job in (doc.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                uses = str(step.get("uses", ""))
                if uses.startswith("actions/checkout"):
                    repo = str((step.get("with") or {}).get("repository", ""))
                    if repo:
                        found.append((path.name, repo))
    return found


def test_no_checkout_names_a_sibling_by_old_slug_alone() -> None:
    paths = sorted(WORKFLOWS.glob("*.yml"))
    assert len(paths) >= 3, "the workflows were not read"
    old = [(f, r) for f, r in checkout_repositories(paths) if r.lower() in OLD_SLUGS]
    assert old == [], old


def test_the_scanner_sees_a_planted_checkout(tmp_path: Path) -> None:
    planted = tmp_path / "x.yml"
    planted.write_text(
        "jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n"
        f"        with:\n          repository: {PLANTED}\n"
    )
    assert checkout_repositories([planted]) == [("x.yml", PLANTED)]
