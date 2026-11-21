"""The template check (017 FTASKS 5.3; FR-017.16; FTID §7.5).

On BOTH fixture behaviours, with their own rule sets (016's fixtures): a template holding a string
the behaviour's rule set fires on is refused naming the rule set and the signal; a clean one is
accepted; swapping the two rule sets swaps which template is refused; the system (instruction)
text is checked as well as the prompt; a declared phrase is refused case-folded.
"""

from __future__ import annotations

import json

import pytest

from src.core.errors import AppError
from src.rules import rule_set
from src.services.generation import template_check
from tests.support.behaviour_fixtures import FIXTURES

#: A string each fixture's rule set fires on (from its own cases), and one neither fires on.
ELEMENT = {
    "fixture-alpha": "Vexilloquant: 4111 1111 1111 1111.",
    "fixture-beta": "a quorbish and a zantimere",
}
CLEAN = "Write a short {genre} message in a {framing} tone; put {slot} where the value goes."


def document(key: str) -> rule_set.RuleSetDocument:
    return rule_set.parse(json.loads((FIXTURES / key / "rule_set.json").read_text()))


@pytest.mark.parametrize("key", ["fixture-alpha", "fixture-beta"])
def test_a_template_showing_the_element_is_refused_naming_rule_and_signal(key: str) -> None:
    doc = document(key)
    found = template_check.hits({"t@1": {"prompt": f"{CLEAN} Example: {ELEMENT[key]}"}}, [doc], [])
    assert found and {h.rule_set for h in found} == {doc.name}
    with pytest.raises(AppError) as refused:
        template_check.refuse(found)
    error = refused.value
    assert error.code == "slot_template_has_element"
    assert error.details["rule_set"] == doc.name and error.details["signal"] == found[0].signal
    assert doc.name in error.message and repr(found[0].signal) in error.message
    assert error.details["next"]["tool"] == "dataworks_clone_generation_template"
    assert template_check.hits({"t@1": {"prompt": CLEAN}}, [doc], []) == []


def test_swapping_the_rule_sets_swaps_the_refusal() -> None:
    alpha, beta = document("fixture-alpha"), document("fixture-beta")
    templates = {
        "a@1": {"prompt": f"{CLEAN} {ELEMENT['fixture-alpha']}"},
        "b@1": {"prompt": f"{CLEAN} {ELEMENT['fixture-beta']}"},
    }
    assert {h.template for h in template_check.hits(templates, [alpha], [])} == {"a@1"}
    assert {h.template for h in template_check.hits(templates, [beta], [])} == {"b@1"}


def test_the_instruction_text_is_checked_too() -> None:
    beta = document("fixture-beta")
    instruction = {"system": f"Never write {ELEMENT['fixture-beta']}.", "prompt": CLEAN}
    first, *_ = template_check.hits({"i@1": instruction}, [beta], [])
    assert first.template == "i@1" and first.rule_set == beta.name


def test_a_declared_phrase_is_refused_case_folded() -> None:
    found = template_check.hits(
        {"t@1": {"prompt": "Note: FOR REFERENCE only."}}, [], ["for reference"]
    )
    assert [(h.phrase, h.signal) for h in found] == [("for reference", None)]
    with pytest.raises(AppError) as refused:
        template_check.refuse(found)
    assert refused.value.details["phrase"] == "for reference"
    assert template_check.hits({"t@1": {"prompt": CLEAN}}, [], ["for reference"]) == []
