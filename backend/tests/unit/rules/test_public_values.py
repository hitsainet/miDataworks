"""Public test values only (FR-016.8; D28, D30, D38-2; FTASKS 3.6, 3.10, 14.2).

The rule in ``src/rules/public_values.py`` is run over the contract file, the fixture rule sets and
any behaviour pack under ``behaviours/``: per case (what a case's own validators read valid) and,
since D38-2, over EVERY string of the file with every identifier reader. The negative controls
prove both are red-capable.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.rules import cases as rule_cases
from src.rules import public_values
from src.rules.rule_set import parse
from tests.support.source_tree import REPO


def _packs() -> list[Any]:
    paths = sorted((REPO / "backend" / "tests" / "fixtures" / "behaviours").glob("*/rule_set.json"))
    paths += sorted((REPO / "behaviours").glob("*/rule_set.json"))
    return paths


def test_the_contract_file_carries_only_public_values() -> None:
    assert public_values.contract_violations(rule_cases.load_cases()) == []


def test_every_rule_set_in_the_tree_carries_only_public_values() -> None:
    paths = _packs()
    assert len(paths) >= 2
    for path in paths:
        doc = parse(json.loads(path.read_text(encoding="utf-8")))
        assert public_values.document_violations(doc) == [], path


def _with_source(kind: str, citation: str | None = None) -> dict[str, Any]:
    data = json.loads(json.dumps(rule_cases.load_cases()))
    case = next(
        c for c in data["validators"]["card_pan@1"]["cases"] if c["id"] == "card_pan.visa_test"
    )
    case["source"] = {"kind": kind, **({"citation": citation} if citation else {})}
    return data


def test_a_valid_case_with_a_constructed_source_is_a_violation() -> None:
    found = public_values.contract_violations(_with_source("constructed"))
    assert found and "card_pan.visa_test" in found[0]


def test_a_false_unissuable_claim_is_a_violation() -> None:
    assert public_values.contract_violations(_with_source("unissuable"))


def test_a_reference_value_is_only_for_institution_identifiers() -> None:
    assert public_values.contract_violations(_with_source("published_reference_value"))


def test_a_test_value_needs_a_citation() -> None:
    assert public_values.contract_violations(_with_source("published_test_value"))
    assert not public_values.contract_violations(_with_source("published_test_value", "a list"))


def test_an_invalid_value_needs_no_public_source() -> None:
    data = json.loads(json.dumps(rule_cases.load_cases()))
    case = next(
        c for c in data["validators"]["card_pan@1"]["cases"] if c["id"] == "card_pan.bad_prefix"
    )
    case["source"] = {"kind": "constructed"}
    assert public_values.contract_violations(data) == []


def test_a_fixture_rule_set_with_a_constructed_valid_case_is_a_violation() -> None:
    path = (
        REPO / "backend" / "tests" / "fixtures" / "behaviours" / "fixture-alpha" / "rule_set.json"
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["cases"][0]["source"] = {"kind": "constructed"}
    assert public_values.document_violations(parse(raw))


def test_a_reference_value_read_valid_by_a_non_institution_validator_too_is_a_violation() -> None:
    """Review R1 (mutation R9): ``published_reference_value`` needs EVERY validator that reads the
    value valid to be an institution identifier; one ABA plus one SSN reading is a violation."""
    from src.rules.validators import resolve

    aba, ssn = resolve("aba_routing", 1).decl, resolve("ssn_us", 1).decl
    source = {"kind": "published_reference_value"}
    assert public_values.judge("c", source, [aba]) is None
    assert public_values.judge("c", source, [aba, ssn]) is not None


# --- every string (D38-2) -------------------------------------------------------------------------


def _contract() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(json.dumps(rule_cases.load_cases()))
    return data


def test_every_string_of_the_contract_file_is_public() -> None:
    assert public_values.string_violations(rule_cases.load_cases()) == []


def test_every_string_of_every_rule_set_in_the_tree_is_public() -> None:
    for path in _packs():
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert public_values.string_violations(raw) == [], path


def test_the_readers_are_the_identifier_formats_of_the_live_registry() -> None:
    refs = {v.decl.ref for v in public_values.identifier_readers()}
    # iban@2 (D38-1) reads with its own scan, beside iban@1: every value-bearing version reads.
    assert refs == {
        "card_pan@1",
        "ssn_us@1",
        "aba_routing@1",
        "iban@1",
        "iban@2",
        "nhs@1",
        "aadhaar@1",
    }


@pytest.mark.parametrize(
    ("where", "value", "reader"),
    [
        ("note", "an aside: 123-45-6789", "ssn_us@1"),
        ("input", "a 4012888888881881 b", "card_pan@1"),
        ("key", "123456789", "ssn_us@1"),
        ("spelled", "1 2 3 4 5 6 7 8 9", "ssn_us@1"),
    ],
)
def test_an_undocumented_valid_looking_value_anywhere_is_a_violation(
    where: str, value: str, reader: str
) -> None:
    """A constructed case's text, a note, a dict key: any string a format reads valid."""
    data = _contract()
    case = data["rule_kinds"]["pattern"][0]
    if where == "note":
        case["note"] = value
    elif where == "input":
        case["input"]["text"] = value
    elif where == "spelled":
        data["rule_kinds"]["renderings"][0]["expect"]["rendered"] = value
    else:
        data["rule_documents"]["veto_masked"]["x_key"] = {value: 1}
    found = public_values.string_violations(data)
    assert found and all(reader in f for f in found), found


def test_a_documented_public_value_may_appear_anywhere() -> None:
    data = _contract()
    data["rule_kinds"]["pattern"][0][
        "note"
    ] = "the voided 078-05-1120 and the card 4111111111111111"
    assert public_values.string_violations(data) == []


def test_a_value_is_documented_only_by_a_cited_public_source() -> None:
    """Without citations nothing is documented: the published SSN and card values become
    violations wherever they appear."""
    data = _contract()
    for case in public_values._cases(data):
        if case["source"].get("kind") == "published_test_value":
            case["source"] = {"kind": "published_test_value"}
    found = public_values.string_violations(data)
    assert any("'078-05-1120' reads valid as ssn_us@1" in f for f in found), found
    assert any("card_pan@1" in f for f in found), found


def _aba_and_ssn_valid() -> str:
    """A nine-digit value that reads valid as BOTH an ABA routing number and an SSN."""
    from src.rules.validators import resolve

    aba, ssn = resolve("aba_routing", 1), resolve("ssn_us", 1)
    for n in range(11_000_000, 11_100_000):
        value = f"{n:09d}"
        if aba.check(value, "", "", {}).valid and ssn.check(value, "", "", {}).valid:
            return value
    raise AssertionError("no value found")


def test_a_reference_value_is_documented_for_institution_readers_only() -> None:
    """D30: a published ABA routing number is public as an ABA number, never as an SSN."""
    value = _aba_and_ssn_valid()
    data = {
        "cases": [{"source": {"kind": "published_reference_value"}, "input": {"value": value}}],
        "note": value,
    }
    found = public_values.string_violations(data)
    assert found and all("ssn_us@1" in f for f in found), found
    data["cases"][0]["source"] = {"kind": "published_test_value", "citation": "a list"}
    assert public_values.string_violations(data) == []


def test_only_a_whole_sha256_digest_is_not_read() -> None:
    """A 64-hex digest is a hash output; the same digit run anywhere else is still read."""
    digest = "eb9b609a185271072ec87" + "0" * 43
    assert public_values.string_violations({"sha256": digest}) == []
    assert public_values.string_violations({"note": digest + " "}) != []
    assert public_values.string_violations({"note": "a185271072e"}) != []
