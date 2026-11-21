"""``RuleSetDocument`` (FR-016.10; FTASKS 2.6; FTDD 016 section 4.10)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from src.rules import cases as rule_cases
from src.rules.rule_set import (
    SAFE_INTEGER,
    RuleSetDocument,
    canonical_bytes,
    digest,
    first_non_nfc,
    parse,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "behaviours"
BASE: dict[str, Any] = rule_cases.load_cases()["rule_documents"]["pattern_ssn_shape"]


def _doc(**change: Any) -> dict[str, Any]:
    doc = copy.deepcopy(BASE)
    doc.update(change)
    return doc


def _errors(doc: dict[str, Any]) -> list[dict[str, Any]]:
    with pytest.raises(ValidationError) as caught:
        RuleSetDocument.model_validate(doc)
    return list(caught.value.errors())


@pytest.mark.parametrize("name", ["fixture-alpha", "fixture-beta"])
def test_both_fixture_documents_validate(name: str) -> None:
    parse(json.loads((FIXTURES / name / "rule_set.json").read_text(encoding="utf-8")))


def test_every_contract_document_validates() -> None:
    for doc in rule_cases.load_cases()["rule_documents"].values():
        parse(doc)


def test_a_nested_rule_set_fails_the_schema() -> None:
    doc = _doc(expressions={"hit": {"rule_set": {"name": "other", "version": 1}}})
    assert _errors(doc)
    signal = {"id": "inner", "kind": "rule_set", "name": "other", "version": 1}
    assert _errors(_doc(signals=[*BASE["signals"], signal]))


def test_code_rule_needs_function_and_params() -> None:
    rule = {
        "id": "c",
        "kind": "code_rule",
        "code_sha256": "0" * 64,
        "runner_contract": "x/v1",
        "function": "detect",
        "params": {},
        "outputs": [{"name": "c_hit", "type": "bool"}],
    }
    parse(_doc(signals=[*BASE["signals"], rule]))
    for missing in ("function", "params"):
        broken = {k: v for k, v in rule.items() if k != missing}
        assert _errors(_doc(signals=[*BASE["signals"], broken]))


def test_a_non_nfc_string_is_refused_naming_its_pointer() -> None:
    doc = _doc(description="Café rule.")
    errors = _errors(doc)
    assert errors[0]["type"] == "not_nfc" and errors[0]["ctx"]["pointer"] == "/description"
    deep = _doc()
    deep["signals"][0]["pattern"] = "é"
    assert first_non_nfc(deep) == "/signals/0/pattern"


def test_key_order_does_not_change_the_digest_but_content_does() -> None:
    reordered = json.loads(json.dumps(BASE, sort_keys=True))
    reversed_keys = dict(reversed(list(BASE.items())))
    assert digest(BASE) == digest(reordered) == digest(reversed_keys)
    assert digest(_doc(vetoes=[])) != digest(BASE)  # absent and empty differ


def test_the_identity_is_rfc_8785() -> None:
    """D38-4: numbers are values (1 == 1.0), ECMAScript number forms, keys by UTF-16 code units."""
    lexical = copy.deepcopy(rule_cases.load_cases()["rule_set_identity"][3]["document"])
    as_float = copy.deepcopy(lexical)
    as_float["signals"][0]["terms"][1]["weight"] = 1.0
    assert lexical["signals"][0]["terms"][1]["weight"] == 1
    assert digest(lexical) == digest(as_float)
    as_float["signals"][0]["cut"] = 1e-7
    assert b'"cut":1e-7' in canonical_bytes(as_float)
    keyed = _doc(
        renderings=[
            {
                "rendering": "m",
                "render": {"mapping": {"\ufb01": "1", "\U0001f600": "2"}},
                "normaliser": {"mapping_inverse": True},
                "invisible_to": [],
            }
        ]
    )
    raw = canonical_bytes(keyed).decode("utf-8")
    assert raw.index("\U0001f600") < raw.index("\ufb01")  # D83D sorts before FB01


def test_an_unsafe_integer_is_refused_naming_its_pointer() -> None:
    doc = _doc()
    doc["signals"] = [
        *BASE["signals"],
        {
            "id": "near",
            "kind": "proximity",
            "anchor": "p",
            "phrases": ["y"],
            "max_distance": SAFE_INTEGER + 1,
            "side": "either",
            "unit": "code_points",
            "word_boundary": False,
            "ignore_case": False,
            "fold_fullwidth_ascii": False,
        },
    ]
    errors = _errors(doc)
    assert errors[0]["type"] == "unsafe_integer"
    assert errors[0]["ctx"]["pointer"] == "/signals/1/max_distance"
    doc["signals"][1]["max_distance"] = SAFE_INTEGER
    parse(doc)


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"name": "Bad Name"}, "name slug"),
        ({"name": "x" * 65}, "name over 64 (FTID I4)"),
        ({"behaviour_key": "Pii"}, "behaviour key slug"),
        ({"format": "midataworks.rule-set/v2"}, "format"),
        ({"surprise": 1}, "extra key"),
        ({"signals": []}, "no signals"),
    ],
)
def test_structural_refusals(change: dict[str, Any], why: str) -> None:
    assert _errors(_doc(**change)), why


def test_semantic_options_have_no_default() -> None:
    doc = _doc()
    del doc["signals"][0]["ignore_case"]
    assert any(e["loc"][-1] == "ignore_case" for e in _errors(doc))


def test_references_must_resolve() -> None:
    doc = _doc()
    doc["expressions"]["hit"] = {"signal": "missing"}
    assert _errors(doc)
    doc = _doc()
    doc["outputs"][0]["type"] = "int"  # fired is a bool
    assert _errors(doc)


def test_expression_depth_is_at_most_eight() -> None:
    expr: dict[str, Any] = {"signal": "p"}
    for _ in range(7):
        expr = {"not": expr}
    parse(_doc(expressions={"hit": expr}))
    assert _errors(_doc(expressions={"hit": {"not": expr}}))
