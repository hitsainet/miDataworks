"""Cases are append-only within the contract major (FR-018.9; FTASKS 3.8).

Diffs ``docs/schemas/runner-cases-v1.json`` against every baseline git can show (``HEAD`` and
``origin/main``): a changed or removed case under the same major is red, an added case passes, no
history skips loudly. A document with a new major (``midataworks.runner/v2``) is refused by the model.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from src.runner_contract.cases import CASES_FILE_NAME
from tests.support.source_tree import REPO

PATH = f"docs/schemas/{CASES_FILE_NAME}"
BASELINE_REFS = ("HEAD", "origin/main")
SECTIONS = ("conformance", "hostile", "hash", "imports", "limits")


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
    found = [(ref, _show(repo, ref)) for ref in BASELINE_REFS]
    return [(ref, data) for ref, data in found if data is not None]


def index(data: dict[str, Any]) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for section in SECTIONS:
        for case in data.get(section, []):
            out[(section, case["id"])] = case
    for doc_id, doc in data.get("documents", {}).items():
        out[("documents", doc_id)] = doc
    return out


def violations(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    before, after = index(old), index(new)
    found = []
    for key, case in before.items():
        if key not in after:
            found.append(f"{key[0]}/{key[1]}: removed")
        elif after[key] != case:
            found.append(f"{key[0]}/{key[1]}: changed")
    return found


def test_no_published_case_changed_or_disappeared() -> None:
    found = baselines()
    if not found:
        pytest.skip(f"NO HISTORY: no baseline ref ({BASELINE_REFS}) holds {PATH}; not checked")
    new = json.loads((REPO / PATH).read_text(encoding="utf-8"))
    assert {ref: violations(old, new) for ref, old in found} == {ref: [] for ref, _ in found}


def test_an_edited_case_is_red_and_an_added_case_is_not() -> None:
    new = json.loads((REPO / PATH).read_text(encoding="utf-8"))
    edited = json.loads(json.dumps(new))
    edited["conformance"][0]["expect"] = {"count": 99}
    assert violations(new, edited), "an edited expectation must be a violation"
    added = json.loads(json.dumps(new))
    added["hostile"].append({**added["hostile"][0], "id": "hostile.added"})
    assert violations(new, added) == []


def test_a_v2_document_is_refused_by_the_model() -> None:
    from pydantic import ValidationError

    from src.runner_contract.document import CodeRuleDocument

    doc = {
        "runner_contract": "midataworks.runner/v2",
        "source": "def f(t, p):\n    return 0\n",
        "functions": [{"name": "f", "output": {"type": "count"}}],
        "params_schema": {"type": "object"},
        "derived_from": {"sources": [], "statement": "x"},
    }
    with pytest.raises(ValidationError):
        CodeRuleDocument.model_validate(doc)
