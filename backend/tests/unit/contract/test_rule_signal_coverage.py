"""Coverage is derived from the live registry and the rule-set schema (FR-016.7; FTASKS 3.3).

Every item is computed - from ``VALIDATORS``, the rule-set model's enums and union members, and
``dialect``'s rule tuples - never from a list in this test. An empty registry is a gap (fail closed).
Valid-path reasons of a ``not_publicly_vectored`` validator are covered by the private cases; on the
public mirror (where that directory is stripped) their check skips loudly.
"""

from __future__ import annotations

import copy
import json
import typing
from pathlib import Path
from typing import Any

import pytest

from src.rules import cases as rule_cases
from src.rules import dialect
from src.rules.rule_set import (
    Expr,
    LexicalWeightsSignal,
    PatternSignal,
    ProximitySignal,
    Rendering,
    Signal,
)
from src.rules.validators import VALIDATORS, Validator

PRIVATE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "private" / "rule-signal-private-cases.json"
)
EXTERNAL = {"published_test_value", "published_reference_value"}


def _private() -> dict[str, Any] | None:
    return json.loads(PRIVATE.read_text(encoding="utf-8")) if PRIVATE.exists() else None


def _reasons(case: dict[str, Any]) -> list[str]:
    expect = case["expect"]
    return (
        [c["reason"] for c in expect["candidates"]]
        if "candidates" in expect
        else [expect["reason"]]
    )


def _valid(case: dict[str, Any]) -> bool:
    expect = case["expect"]
    return (
        any(c["valid"] for c in expect["candidates"]) if "candidates" in expect else expect["valid"]
    )


def validator_gaps(
    validators: dict[tuple[str, int], Validator],
    data: dict[str, Any],
    private: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """(gaps, private-only items that could not be checked)."""
    gaps: list[str] = []
    unchecked: list[str] = []
    if not validators:
        return ["the validator registry is empty"], unchecked
    sections = data["validators"]
    for (vid, version), validator in sorted(validators.items()):
        ref = f"{vid}@{version}"
        if ref not in sections:
            gaps.append(f"{ref}: registered with no cases")
            continue
        public = sections[ref]["cases"]
        if not public:
            gaps.append(f"{ref}: an empty case list")
        hidden = (private or {}).get("validators", {}).get(ref, {}).get("cases", [])
        seen = {r for c in public for r in _reasons(c)}
        seen_private = {r for c in hidden for r in _reasons(c)}
        path = sections[ref]["valid_path"]
        for reason in validator.decl.reasons:
            if reason in seen:
                continue
            # Only a not_publicly_vectored validator may cover a reason privately; any other
            # validator's uncovered reason is a gap on the mirror too (Review R1: the mirror had
            # turned every such gap into a skip).
            if private is None and path == "not_publicly_vectored":
                unchecked.append(f"{ref}: reason {reason!r} (private cases absent)")
            elif reason not in seen_private:
                gaps.append(f"{ref}: no case for declared reason {reason!r}")
        if not validator.decl.value_bearing:
            if path != "no_identifier":
                gaps.append(f"{ref}: bears no identifier, valid_path must be no_identifier")
        elif path == "publicly_vectored":
            if not any(_valid(c) and c["source"]["kind"] in EXTERNAL for c in public):
                gaps.append(f"{ref}: publicly_vectored without an externally sourced valid case")
        elif path == "not_publicly_vectored":
            if private is None:
                unchecked.append(f"{ref}: private valid path")
            elif not any(_valid(c) for c in hidden):
                gaps.append(f"{ref}: not_publicly_vectored with no private valid case")
        else:
            gaps.append(f"{ref}: unknown valid_path {path!r}")
    for ref in sections:
        vid, version = ref.rsplit("@", 1)
        if (vid, int(version)) not in validators:
            gaps.append(f"{ref}: cases for a validator that is not registered")
    return gaps, unchecked


def _tags(data: dict[str, Any]) -> set[str]:
    return {t for cases in data["rule_kinds"].values() for c in cases for t in c.get("covers", [])}


def _literal(field: Any) -> tuple[str, ...]:
    return tuple(typing.get_args(field.annotation))


def _expr_operators() -> list[str]:
    return sorted(
        next(iter(m.model_fields.values())).alias or next(iter(m.model_fields))
        for m in typing.get_args(Expr)
        if next(iter(m.model_fields)) != "signal"
    )


def rule_gaps(data: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    kinds = data["rule_kinds"]
    tags = _tags(data)
    for op in _expr_operators():
        section = kinds.get(f"composition.{op}", [])
        if not section:
            gaps.append(f"composition.{op}: no cases")
        elif not any(
            f'"{op}"' in json.dumps(data["rule_documents"][c["document"]]["expressions"])
            for c in section
        ):
            gaps.append(f"composition.{op}: no case's document uses {op}")
    for unit in _literal(ProximitySignal.model_fields["unit"]):
        for edge in ("inside", "outside"):
            if f"proximity.{unit}.{edge}" not in tags:
                gaps.append(f"proximity edge {unit} {edge}")
    for mode in _literal(LexicalWeightsSignal.model_fields["counting"]):
        if f"lexical.repeated.{mode}" not in tags:
            gaps.append(f"lexical repeated terms under {mode}")
    for item in ("lexical.cut_equal", "lexical.cut_below", "lexical.negative_weight"):
        if item not in tags:
            gaps.append(item)
    for rule in dialect.DIALECT_RULES:
        if f"dialect.{rule}" not in tags:
            gaps.append(f"dialect rule {rule}")
    refused = {c["rule"] for c in kinds.get("refusals", [])}
    for rule in dialect.REFUSAL_RULES:
        if rule not in refused:
            gaps.append(f"refusal rule {rule}")
    options = [n for n, f in PatternSignal.model_fields.items() if f.annotation is bool]
    for option in options:
        for state in ("on", "off"):
            if f"normalisation.{option}.{state}" not in tags:
                gaps.append(f"normalisation {option} {state}")
        if not _differs_only_in(data, option):
            gaps.append(f"normalisation {option}: no two cases differ only in it")
    # D38-3: every render and every normaliser form has a normalise(render(v)) == v case, derived
    # from the model's own unions and literals (a builtin added to v1 needs its case).
    renders = [r["rendering"] for r in kinds.get("renderings", [])]
    for field, members in (
        ("render", typing.get_args(Rendering.model_fields["render"].annotation)),
        ("normaliser", typing.get_args(Rendering.model_fields["normaliser"].annotation)),
    ):
        for member in members:
            key = next(iter(member.model_fields))
            if key == "builtin":
                for builtin in _literal(member.model_fields["builtin"]):
                    if not any(r[field].get("builtin") == builtin for r in renders):
                        gaps.append(f"rendering {field} builtin {builtin}: no case")
            elif key not in ("validator",) and not any(
                (member.model_fields[key].alias or key) in r[field] for r in renders
            ):
                gaps.append(f"rendering {field} {key}: no case")
    used = {s["kind"] for d in data["rule_documents"].values() for s in d["signals"]}
    for member in typing.get_args(typing.get_args(Signal)[0]):
        kind = _literal(member.model_fields["kind"])[0]
        if kind == "code_rule":
            if not any(c["rule"] == "code_rule_not_available" for c in kinds.get("refusals", [])):
                gaps.append("code_rule: no refusal case")
        elif kind not in used:
            gaps.append(f"signal kind {kind}: no case document")
    return gaps


def _differs_only_in(data: dict[str, Any], option: str) -> bool:
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for c in data["rule_kinds"]["pattern"]:
        by_doc.setdefault(c["document"], []).append(c)
    docs = data["rule_documents"]
    for a, cases_a in by_doc.items():
        for b, cases_b in by_doc.items():
            da, db = json.loads(json.dumps(docs[a])), json.loads(json.dumps(docs[b]))
            if a == b or da["signals"][0].get(option) == db["signals"][0].get(option):
                continue
            da["signals"][0][option] = db["signals"][0][option]
            if da != db:
                continue
            for ca in cases_a:
                for cb in cases_b:
                    if (
                        ca["input"] == cb["input"]
                        and ca["expect"]["outputs"] != cb["expect"]["outputs"]
                    ):
                        return True
    return False


def test_every_validator_item_is_covered() -> None:
    gaps, unchecked = validator_gaps(VALIDATORS, rule_cases.load_cases(), _private())
    assert gaps == []
    if unchecked:
        pytest.skip("MIRROR: private cases absent; unchecked: " + "; ".join(unchecked))


def test_every_rule_item_is_covered() -> None:
    assert rule_gaps(rule_cases.load_cases()) == []


# --- red-capable --------------------------------------------------------------------------------


def test_an_empty_registry_is_a_gap() -> None:
    assert validator_gaps({}, rule_cases.load_cases(), _private())[0] == [
        "the validator registry is empty"
    ]


def test_a_registered_validator_without_cases_is_a_gap() -> None:
    extra = dict(VALIDATORS)
    extra[("luhn", 2)] = VALIDATORS[("luhn", 1)]
    gaps, _ = validator_gaps(extra, rule_cases.load_cases(), _private())
    assert "luhn@2: registered with no cases" in gaps


def test_removing_the_only_case_of_a_reason_is_a_gap() -> None:
    data = json.loads(json.dumps(rule_cases.load_cases()))
    cases = data["validators"]["iban@1"]["cases"]
    data["validators"]["iban@1"]["cases"] = [c for c in cases if c["id"] != "iban.bad_bban_format"]
    gaps, _ = validator_gaps(VALIDATORS, data, _private())
    assert "iban@1: no case for declared reason 'bad_bban_format'" in gaps


def test_dropping_a_rule_tag_is_a_gap() -> None:
    data = json.loads(json.dumps(rule_cases.load_cases()))
    for c in data["rule_kinds"]["proximity"]:
        c.get("covers", []).clear()
    assert "proximity edge utf8_bytes inside" in rule_gaps(data)


def test_on_the_mirror_a_public_reason_gap_is_still_a_gap() -> None:
    """Review R1: with the private cases stripped (the mirror), only a not_publicly_vectored
    validator may defer a reason; a publicly vectored validator's missing reason stays red."""
    data = json.loads(json.dumps(rule_cases.load_cases()))
    cases = data["validators"]["iban@1"]["cases"]
    data["validators"]["iban@1"]["cases"] = [c for c in cases if c["id"] != "iban.bad_bban_format"]
    gaps, unchecked = validator_gaps(VALIDATORS, data, None)
    assert "iban@1: no case for declared reason 'bad_bban_format'" in gaps
    assert not any(u.startswith("iban@1") for u in unchecked)
    # and the full file, as the mirror sees it, has no gap at all
    assert validator_gaps(VALIDATORS, rule_cases.load_cases(), None)[0] == []


def test_removing_the_only_case_of_a_normaliser_builtin_is_a_gap() -> None:
    """D38-3: the rendering items are derived from the model; without the cases that use
    ``spelled_out_digits`` / ``words_to_digits`` (three since Review R1 of slices 2-4), the gap is
    named."""
    data = copy.deepcopy(rule_cases.load_cases())
    data["rule_kinds"]["renderings"] = [
        c
        for c in data["rule_kinds"]["renderings"]
        if not c["id"].startswith("rendering.spelled_out_digits")
    ]
    gaps = rule_gaps(data)
    assert "rendering render builtin spelled_out_digits: no case" in gaps
    assert "rendering normaliser builtin words_to_digits: no case" in gaps
