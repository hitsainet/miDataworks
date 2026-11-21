"""Each old environment name follows its record in the export (feature 020; FR-020.25; CF-11;
FTASKS 19.2, controls M46 and M46b).

Per name, driven by the export's ``env`` record (census owns the status; this repository keeps a
literal table, ``core/env_names.RENAMES``):

- ``transition``: the row is live (read as a fallback), ``.env.example`` keeps the old name as an
  old-name comment, and the brand guard allows the old name.
- ``retired`` (P7, on that name's own evidence), or the record dropped after P7 closes: the row is
  ``retired=True`` (never read, reported when set), ``.env.example`` names the old variable nowhere,
  not even in a comment, and the guard no longer allows it.

Either way the previous-address settings are untouched (FR-020.27): they are not renamed variables,
carry no old name, and stay assigned in ``.env.example``.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.core import env_names
from src.core.config import Settings
from tests.support import legacy_identifiers as li
from tests.unit.brand import scan

REPO = Path(__file__).resolve().parents[3]
EXAMPLE = REPO / ".env.example"
DENY = scan.policy()["deny"]
PREVIOUS = {
    "miserver_previous_base_urls": "MISERVER_PREVIOUS_BASE_URLS",
    "miworkbench_previous_base_urls": "MIWORKBENCH_PREVIOUS_BASE_URLS",
}


def _status() -> dict[str, str]:
    """old name -> the export's status for it ("dropped" when the export no longer lists it)."""
    export = li.load()
    listed = {r["old"]: r["status"] for r in export.section("env")}
    return {row.old: listed.get(row.old, "dropped") for row in env_names.RENAMES}


def test_each_rename_follows_its_export_status() -> None:
    export = li.load()
    example = EXAMPLE.read_text(encoding="utf-8")
    status = _status()
    assert len(status) == len(env_names.RENAMES) >= 7, "the rename table was not read"
    problems: list[str] = []
    for row in env_names.RENAMES:
        state = status[row.old]
        named = re.findall(rf"(?m)^.*\b{re.escape(row.old)}\b.*$", example)
        allowed = li.unallowed_hits(f"Set {row.old} to the address.", export, DENY) == []
        if state == "transition":
            if row.retired:
                problems.append(f"{row.old}: the export says transition, the row is retired")
            if named != [f"# {row.old}= (old name, read until P7)"]:
                problems.append(f"{row.old}: .env.example lines {named}, not one old-name comment")
            if not allowed:
                problems.append(f"{row.old}: in transition but the brand guard denies it")
        else:
            if not row.retired:
                problems.append(f"{row.old}: the export says {state}, the row is still read")
            if named:
                problems.append(f"{row.old}: {state} but .env.example still names it: {named}")
            if allowed:
                problems.append(f"{row.old}: {state} but the brand guard still allows it")
    # FR-020.27: retiring the aliases leaves the previous-address settings alone.
    renamed = {row.new for row in env_names.RENAMES} | {row.old for row in env_names.RENAMES}
    for field, variable in PREVIOUS.items():
        if field not in Settings.model_fields:
            problems.append(f"the Settings field {field} is gone")
        if variable in renamed or env_names.old_name_for(field) is not None:
            problems.append(f"{variable} became a renamed variable")
        if not re.search(rf"(?m)^{variable}=", example):
            problems.append(f".env.example no longer assigns {variable}")
    assert not problems, problems


def test_the_rule_reads_every_status_the_export_can_carry() -> None:
    """Fail closed: the statuses this repository's export holds are ones the rule above judges
    (a new status would otherwise fall into the retired branch unexamined)."""
    assert set(_status().values()) <= {"transition", "retired", "dropped"}
    # Each env record of the export names a row here (CF-3 checks the pairs both ways).
    rows = {row.old for row in env_names.RENAMES}
    assert {r["old"] for r in li.load().section("env")} <= rows


#: Where an operator reads which variable to set: the README, the docs pages (the published ones and
#: the private runbook), the manual's own pages and the example environment. The exports are the
#: register's record, not instructions, and are excluded by ``scan.tracked``.
#: A git pathspec's ``*`` crosses ``/``: ``manual/docs/*.md`` is every manual page at any depth (the
#: first form here, ``manual/docs/**/*.md``, skipped the pages directly under ``manual/docs``: control
#: M67 survived it).
OPERATOR_TEXT = ("README.md", "docs/*.md", "manual/docs/*.md", ".env.example")


def test_a_retired_name_is_set_by_no_operator_document() -> None:
    """A retired old name in a page that tells the operator what to set is an instruction that is
    silently ignored since P7 (FR-020.26). Found at P7: the getting-started table, the README and
    four docs pages still named the old variables (FTASKS 19.3)."""
    files = scan.tracked(*OPERATOR_TEXT)
    read = {str(p.relative_to(REPO)) for p in files}
    expected = {"README.md", ".env.example", "docs/labeling.md", "manual/docs/getting-started.md"}
    assert expected <= read, f"pages not read: {sorted(expected - read)}"
    retired = [row.old for row in env_names.RENAMES if row.retired]
    found = [
        f"{path.relative_to(REPO)}:{number}: {name}"
        for path in files
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for name in retired
        if re.search(rf"\b{name}\b", line)
    ]
    assert found == [], found
