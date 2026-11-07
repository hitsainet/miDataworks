"""Renderings through the build operator (017 FTASKS 6.2; FR-017.23).

For every rendering the pinned rule set declares and every element value its cases carry,
``insert_element@1`` inserts the RENDERED element and records ``normalise(render(v)) == v`` as
``element_recovered`` (the normaliser recorded as the label's rule provenance); the patterns the
rule set declares a rendering invisible to do not match it in the built text. Controls: M4 (skip
the normaliser) and "break one mapping" (a normaliser that does not invert the rendering).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.rules.compose import CompiledRuleSet
from src.rules.rule_set import parse
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator
from tests.support.label_fixtures import SLOT, pin, rule_set_document, vocabulary

ALPHA = "fixture-alpha"


def _values(document: dict[str, Any]) -> list[str]:
    """Every element value the rule set's own cases carry."""
    return [c["input"]["value"] for c in document["cases"] if "value" in c["input"]]


def _carriers(document: dict[str, Any], form: str) -> Any:
    rows = [
        {
            "text": f"Reference 参照 {SLOT} 🙂 end {i}",
            "slot": SLOT,
            "kind": "valid",
            "form": form,
            "value": value,
        }
        for i, value in enumerate(_values(document))
    ]
    return hp.with_system_columns(rows, source_id="src-r", content=("text",))


def _run(document: dict[str, Any], form: str) -> list[dict[str, Any]]:
    params = {
        "mode": "slot",
        "element_column": "value",
        "vocabulary": vocabulary(ALPHA),
        **pin(ALPHA, document),
    }
    roles = {"text": "content", "slot": "metadata", "kind": "metadata", "form": "metadata",
             "value": "metadata"}  # fmt: skip
    return list(run_operator("insert_element", params, _carriers(document, form), roles)
                .output.to_pylist())  # fmt: skip


def test_the_fixture_declares_a_rendering_and_values_to_render() -> None:
    document = rule_set_document(ALPHA)
    assert [r["rendering"] for r in document["renderings"]] == ["spelled"]
    assert len(_values(document)) >= 2


@pytest.mark.parametrize("rendering", ["spelled"])
def test_every_declared_rendering_normalises_back_through_the_build(rendering: str) -> None:
    document = rule_set_document(ALPHA)
    rows = _run(document, rendering)
    assert len(rows) == len(_values(document))
    for row, value in zip(rows, _values(document), strict=True):
        assert row["element_rendering"] == rendering
        assert row["element_normaliser"] == "builtin:words_to_digits"
        assert row["element_inserted"] != value  # it was rendered
        assert row["element_recovered"] is True, (row["element_inserted"], value)
        assert row["text"][row["ins_start"] : row["ins_end"]] == row["element_inserted"]


def test_a_form_that_is_no_rendering_is_inserted_as_given() -> None:
    document = rule_set_document(ALPHA)
    for row, value in zip(_run(document, "plain"), _values(document), strict=True):
        assert row["element_inserted"] == value
        assert row["element_rendering"] is None and row["element_normaliser"] is None
        assert row["element_recovered"] is True


def test_the_patterns_do_not_see_the_forms_declared_invisible() -> None:
    document = rule_set_document(ALPHA)
    compiled = CompiledRuleSet(parse(document))
    (spelled,) = document["renderings"]
    for row in _run(document, "spelled"):
        signals = compiled.evaluate(row["text"]).signals
        for signal in spelled["invisible_to"]:
            inside = [
                s
                for s in signals[signal]["spans"]
                if s[0] < row["ins_end"] and row["ins_start"] < s[1]
            ]
            assert inside == [], (signal, row["element_inserted"])
    # and the same values inserted plain ARE seen (the invisibility is the rendering's doing)
    seen = [compiled.evaluate(r["text"]).signals["num"]["spans"] for r in _run(document, "plain")]
    assert all(seen)


def test_a_normaliser_that_does_not_invert_its_rendering_is_caught() -> None:
    """Breaking one mapping: the rendering's normaliser no longer recovers the element."""
    document = rule_set_document(ALPHA)
    broken = {
        **document,
        "renderings": [{**document["renderings"][0], "normaliser": {"builtin": "digits_only"}}],
    }
    rows = _run(broken, "spelled")
    assert rows and all(r["element_recovered"] is False for r in rows)
