"""Cases are append-only per published validator version (FR-016.2; FTASKS 3.5).

Diffs ``docs/schemas/rule-signal-cases-v1.json`` against every BASELINE git can show: ``HEAD`` (an
uncommitted edit) and ``origin/main`` (what is published; Review R1: against ``HEAD`` alone a
COMMITTED edit was invisible, so the guard passed every commit that changed a case). A case of a
validator version that is still registered, or of any rule kind, that changed or disappeared is
red; added cases pass. A baseline without the file (nothing published yet) is skipped; with no
baseline at all (an archive, a shallow checkout) the test skips loudly. Debt: CI's shallow
checkout of a pull request has no ``origin/main``, so there the check is local-only.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.rules.validators import VALIDATORS
from tests.support.source_tree import REPO

PATH = "docs/schemas/rule-signal-cases-v1.json"


#: The refs a published case is read from: the last commit, and the published main.
BASELINE_REFS = ("HEAD", "origin/main")


def _show(repo: Path, ref: str) -> dict[str, Any] | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "show", f"{ref}:{PATH}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return json.loads(out)


def baselines(repo: Path = REPO) -> list[tuple[str, dict[str, Any]]]:
    """``(ref, contract file at ref)`` for every baseline ref that holds the file."""
    found = [(ref, _show(repo, ref)) for ref in BASELINE_REFS]
    return [(ref, data) for ref, data in found if data is not None]


def index(data: dict[str, Any]) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for ref, sec in data["validators"].items():
        for c in sec["cases"]:
            out[(f"validators/{ref}", c["id"])] = c
    for kind, cases in data["rule_kinds"].items():
        for c in cases:
            out[(f"rule_kinds/{kind}", c["id"])] = c
    for c in data["rule_set_identity"]:
        out[("rule_set_identity", c["id"])] = c
    return out


def violations(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    before, after = index(old), index(new)
    found = []
    for key, case in before.items():
        section = key[0]
        if section.startswith("validators/"):
            vid, version = section.split("/", 1)[1].rsplit("@", 1)
            if (vid, int(version)) not in VALIDATORS:
                continue  # a retired version's cases may go with it
        if key not in after:
            found.append(f"{key}: removed")
        elif after[key] != case:
            found.append(f"{key}: changed")
    for key, doc in old.get("rule_documents", {}).items():
        if new.get("rule_documents", {}).get(key) != doc:
            found.append(f"rule_documents/{key}: changed or removed")
    return found


def test_no_published_case_changed_or_disappeared() -> None:
    found = baselines()
    if not found:
        pytest.skip(f"NO HISTORY: no baseline ref ({BASELINE_REFS}) holds {PATH}; not checked")
    new = json.loads((REPO / PATH).read_text(encoding="utf-8"))
    assert {ref: violations(old, new) for ref, old in found} == {ref: [] for ref, _ in found}


def test_a_committed_edit_of_a_published_case_is_red(tmp_path: Path) -> None:
    """Review R1: a case edited AND committed on a branch is caught against origin/main."""
    published = (REPO / PATH).read_text(encoding="utf-8")

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True,
            capture_output=True,
        )

    target = tmp_path / PATH
    target.parent.mkdir(parents=True)
    target.write_text(published, encoding="utf-8")
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "published")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    edited = json.loads(published)
    edited["validators"]["luhn@1"]["cases"][0]["expect"]["reason"] = "changed"
    target.write_text(json.dumps(edited), encoding="utf-8")
    git("commit", "-qam", "edit a published case")
    by_ref = {ref: violations(old, edited) for ref, old in baselines(tmp_path)}
    assert by_ref["HEAD"] == []  # the committed edit is invisible to HEAD
    assert by_ref["origin/main"] == ["('validators/luhn@1', 'luhn.card_test'): changed"]


def test_an_edited_expectation_is_red_and_an_added_case_is_not() -> None:
    new = json.loads((REPO / PATH).read_text(encoding="utf-8"))
    edited = json.loads(json.dumps(new))
    edited["validators"]["luhn@1"]["cases"][0]["expect"]["reason"] = "changed"
    assert violations(new, edited) == [
        ("validators/luhn@1", "luhn.card_test").__str__() + ": changed"
    ]
    added = json.loads(json.dumps(new))
    added["rule_kinds"]["pattern"].append(
        {**added["rule_kinds"]["pattern"][0], "id": "pattern.new"}
    )
    assert violations(new, added) == []
