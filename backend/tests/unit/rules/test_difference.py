"""``rules/difference.py`` (FR-016.21; Q24 (a)): RFC 6901 pointers, lists of objects matched by id."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from src.rules.difference import apply_pointer, difference

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "behaviours"


def _doc(behaviour: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / behaviour / "rule_set.json").read_text())
    return data


@pytest.mark.parametrize("behaviour", ["fixture-alpha", "fixture-beta"])
def test_identical_documents_have_no_difference(behaviour: str) -> None:
    doc = _doc(behaviour)
    assert difference(doc, copy.deepcopy(doc)) == []


@pytest.mark.parametrize("behaviour", ["fixture-alpha", "fixture-beta"])
def test_one_substitution_is_exactly_that_path(behaviour: str) -> None:
    doc = _doc(behaviour)
    pointer = "/signals/0/pattern"
    changed = apply_pointer(doc, pointer, "renamed")
    assert difference(doc, changed) == [
        {"op": "replace", "path": pointer, "old": doc["signals"][0]["pattern"], "new": "renamed"}
    ]
    assert doc["signals"][0]["pattern"] != "renamed", "apply_pointer copies"


def test_lists_of_objects_with_an_id_are_matched_by_id() -> None:
    a = {"cases": [{"id": "x", "v": 1}, {"id": "y", "v": 2}]}
    b = {"cases": [{"id": "y", "v": 2}, {"id": "x", "v": 3}, {"id": "z", "v": 0}]}
    assert difference(a, b) == [
        {"op": "replace", "path": "/cases/0/v", "old": 1, "new": 3},
        {"op": "add", "path": "/cases/2", "new": {"id": "z", "v": 0}},
    ]


def test_values_compare_with_their_json_type_and_pointers_escape() -> None:
    assert difference({"a/b": 1}, {"a/b": True}) == [
        {"op": "replace", "path": "/a~1b", "old": 1, "new": True}
    ]
    assert difference({"k": [1, 2]}, {"k": [1]}) == [{"op": "remove", "path": "/k/1", "old": 2}]
    assert difference({}, {"t~": 1}) == [{"op": "add", "path": "/t~0", "new": 1}]


def test_apply_pointer_refuses_a_path_that_names_nothing() -> None:
    with pytest.raises(KeyError):
        apply_pointer({"a": 1}, "/b", 2)
    with pytest.raises(IndexError):
        apply_pointer({"a": [1]}, "/a/3", 2)
