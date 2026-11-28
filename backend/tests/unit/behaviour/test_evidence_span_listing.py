"""Which rule-set outputs are listed as evidence spans (ENH-006 G39).

The real chain is ``tests/integration/behaviour/test_evidence_spans_travel.py``; this pins the two
exclusions that fixture cannot reach: a rule set whose signals read the element, and a column the build
did not publish. The recipe and the carried rule sets are given; nothing else is stubbed.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.services.behaviour import evidence_spans
from src.services.rules import carry


def _rule_set(name: str, *, element: str | None = None) -> dict[str, Any]:
    signals: list[dict[str, Any]] = [{"id": "num", "kind": "pattern", "reads": "text"}]
    if element == "reads":
        signals.append({"id": "el", "kind": "pattern", "reads": "element"})
    if element == "candidates":
        signals.append({"id": "el", "kind": "validator", "candidates": {"element": True}})
    return {
        "name": name,
        "signals": signals,
        "outputs": [
            {"column": "hit", "type": "bool", "value": {"fired": "x"}},
            {"column": "spans", "type": "string", "value": {"spans": "x"}},
        ],
    }


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:
    documents = {
        ("plain", 1): _rule_set("plain"),
        ("elem", 1): _rule_set("elem", element="reads"),
        ("cand", 1): _rule_set("cand", element="candidates"),
    }
    steps = [
        {
            "operator": "rule_set",
            "params": {
                "rule_set_name": name,
                "rule_set_version": 1,
                "output_prefix": f"{name}_",
                "text_column": "body" if name == "plain" else "text",
            },
        }
        for name in ("plain", "elem", "cand")
    ] + [{"operator": "derive_labels", "params": {}}]
    monkeypatch.setattr(
        carry,
        "carried",
        lambda session, version: [
            SimpleNamespace(name=n, version=v, body_canonical=json.dumps(d))
            for (n, v), d in documents.items()
        ],
    )
    session = SimpleNamespace(get=lambda model, key: SimpleNamespace(body={"steps": steps}))
    version = SimpleNamespace(
        recipe_hash="h", behaviour_definition={"key": "k"}, behaviour_configuration=None
    )
    return session, version


def test_only_span_outputs_are_listed_with_the_text_their_step_read(world: Any) -> None:
    session, version = world
    entries = evidence_spans.span_outputs(session, version)
    assert [e["column"] for e in entries] == ["plain_spans", "elem_spans", "cand_spans"]
    assert entries[0] == {
        "column": "plain_spans",
        "text_column": "body",
        "unit": "code_points",
        "rule_set": "plain@1",
    }


def test_a_rule_set_reading_the_element_and_an_unpublished_column_are_left_out(world: Any) -> None:
    session, version = world
    published = ["plain_spans", "elem_spans", "cand_spans", "plain_hit"]
    listed = evidence_spans.evidence_span_columns(session, version, published)
    assert listed is not None and [e["column"] for e in listed] == ["plain_spans"]
    assert (
        evidence_spans.evidence_span_columns(session, version, ["elem_spans", "plain_hit"]) is None
    )
