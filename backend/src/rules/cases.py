"""The conformance cases: the packaged ``rule-signal-cases-v1.json`` and how one case runs.

The contract file (``docs/schemas/rule-signal-cases-v1.json``, hand-written; FR-016.6) is packaged at
``src/schemas/data/`` because the image has no ``docs/`` (``test_rule_signal_cases_file.py`` pins the
two equal). A case is the truth (FR-016.9): when the library disagrees with an externally sourced
case, the library is fixed. Outputs compare with their JSON type (``true`` is not ``1``).
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import dialect, renderings, rule_set
from .compose import CompiledRuleSet, RuleSetNotRunnable
from .rule_set import Case, RuleSetDocument
from .validators import Validator

CASES_FILE_NAME = "rule-signal-cases-v1.json"
CASES_FORMAT = "midataworks.rule-signal-cases/v1"
PACKAGED_CASES_PATH = Path(__file__).resolve().parents[1] / "schemas" / "data" / CASES_FILE_NAME


@functools.lru_cache(maxsize=1)
def load_cases() -> dict[str, Any]:
    """The packaged contract file (read once per process)."""
    data: dict[str, Any] = json.loads(PACKAGED_CASES_PATH.read_text(encoding="utf-8"))
    return data


def same(a: Any, b: Any) -> bool:
    """JSON equality with types: ``True`` is not ``1``, ``1`` is not ``1.0``."""
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    return bool(a == b)


@dataclass
class Outcome:
    passed: bool
    actual: dict[str, Any]
    mismatches: list[str] = field(default_factory=list)


def _compare(expected: dict[str, Any], actual: dict[str, Any], prefix: str = "") -> list[str]:
    return [
        f"{prefix}{k}: expected {expected[k]!r}, got {actual.get(k)!r}"
        for k in expected
        if not same(expected[k], actual.get(k))
    ]


# --- validators -----------------------------------------------------------------------------------


def run_validator_case(validator: Validator, case: dict[str, Any]) -> Outcome:
    """A value case (``input.value``) through ``check``, a text case (``input.text``) through
    ``scan``: every candidate, in order, with its span."""
    params = case.get("params", {})
    inp, expect = case["input"], case["expect"]
    if "text" in inp:
        candidates = [
            {
                "start": c.start,
                "end": c.end,
                "valid": c.verdict.valid,
                "reason": c.verdict.reason,
                "tier": c.verdict.tier,
            }
            for c in validator.scan(inp["text"], params)
        ]
        want = expect["candidates"]
        got = [{k: c[k] for k in w} for c, w in zip(candidates, want, strict=False)]
        mismatches = []
        if len(candidates) != len(want):
            mismatches.append(
                f"candidates: expected {len(want)}, got {len(candidates)}: {candidates}"
            )
        mismatches += [
            m
            for i, (w, g) in enumerate(zip(want, got, strict=False))
            for m in _compare(w, g, f"[{i}].")
        ]
        return Outcome(not mismatches, {"candidates": candidates}, mismatches)
    verdict = validator.check(inp["value"], inp.get("before", ""), inp.get("after", ""), params)
    actual = {n: getattr(verdict, n) for n, _ in validator.decl.outputs}
    wanted = {"valid": expect["valid"], "reason": expect["reason"], **expect.get("outputs", {})}
    mismatches = _compare(wanted, actual)
    return Outcome(not mismatches, actual, mismatches)


# --- rule sets ------------------------------------------------------------------------------------


def case_input(case: dict[str, Any] | Case) -> tuple[str, str | None]:
    """``(text, element)``: ``{"text"}`` has no element; ``{"value", "before", "after"}`` is the
    element inserted between ``before`` and ``after``."""
    inp = case["input"] if isinstance(case, dict) else case.input.model_dump()
    if "text" in inp:
        return inp["text"], None
    value = inp["value"]
    return inp.get("before", "") + value + inp.get("after", ""), value


def run_rule_case(compiled: CompiledRuleSet, case: dict[str, Any] | Case) -> Outcome:
    data = case if isinstance(case, dict) else case.model_dump(mode="json", exclude_none=True)
    text, element = case_input(data)
    result = compiled.evaluate(text, element)
    expect = data["expect"]
    mismatches = _compare(expect["outputs"], result.outputs, "outputs.")
    if not same(expect["vetoed"], result.vetoed):
        mismatches.append(f"vetoed: expected {expect['vetoed']}, got {result.vetoed}")
    for name, spans in (expect.get("spans") or {}).items():
        got = result.expressions[name]["spans"]
        if not same([list(s) for s in spans], got):
            mismatches.append(f"spans.{name}: expected {spans}, got {got}")
    actual = {
        "outputs": result.outputs,
        "vetoed": result.vetoed,
        "expressions": result.expressions,
        "signals": result.signals,
    }
    return Outcome(not mismatches, actual, mismatches)


def run_document_cases(document: RuleSetDocument) -> list[tuple[str, Outcome]]:
    """Every embedded case of a rule set (FR-016.13: a rule set passes its own cases)."""
    compiled = CompiledRuleSet(document)
    return [(c.id, run_rule_case(compiled, c)) for c in document.cases]


# --- refusals, identity, renderings ---------------------------------------------------------------


def refusal_of(refuse: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """``(rule, details)`` an app must cite for a refused pattern or document; ``(None, {})``
    when it is accepted."""
    if "pattern" in refuse:
        found = dialect.scan(refuse["pattern"])
        if found is None:
            try:
                dialect.compile_pattern(refuse["pattern"], bool(refuse.get("ignore_case", False)))
            except dialect.PatternRefused as exc:
                return exc.rule, {"at": exc.position}
            return None, {}
        return found.rule, {"at": found.position}
    try:
        document = RuleSetDocument.model_validate(refuse["document"])
    except ValidationError as exc:
        for error in exc.errors():
            if error["type"] in ("not_nfc", "unsafe_integer"):
                return str(error["type"]), {"pointer": error["ctx"]["pointer"]}
        return "rule_set_invalid", {}
    try:
        CompiledRuleSet(document)
    except RuleSetNotRunnable as exc:
        return exc.code, dict(exc.details)
    return None, {}


def run_refusal_case(case: dict[str, Any]) -> Outcome:
    rule, details = refusal_of(case["refuse"])
    actual = {"rule": rule, **details}
    wanted = {"rule": case["rule"]}
    if "at" in case:
        wanted["at"] = case["at"]
    if "pointer" in case:
        wanted["pointer"] = case["pointer"]
    mismatches = _compare(wanted, actual)
    return Outcome(not mismatches, actual, mismatches)


def run_identity_case(case: dict[str, Any]) -> Outcome:
    """The bytes through ``rule_set.canonical_bytes`` and the hash through ``rule_set.digest``: the
    two production functions, each checked on its own (D38-4; Review R1 D7)."""
    raw = rule_set.canonical_bytes(case["document"])
    actual = {"canonical_utf8": raw.decode("utf-8"), "sha256": rule_set.digest(case["document"])}
    mismatches = _compare(
        {"canonical_utf8": case["canonical_utf8"], "sha256": case["sha256"]}, actual
    )
    return Outcome(not mismatches, actual, mismatches)


def run_rendering_case(case: dict[str, Any]) -> Outcome:
    rendering = case["rendering"]
    rendered = renderings.render(rendering, case["input"]["value"])
    back = renderings.normalise(rendering, rendered)
    actual = {"rendered": rendered, "normalised": back}
    mismatches = _compare(case["expect"], actual)
    return Outcome(not mismatches, actual, mismatches)


def rule_document(cases_file: dict[str, Any], case: dict[str, Any]) -> RuleSetDocument:
    """The rule-set document a ``rule_kinds`` case runs (by id from ``rule_documents``)."""
    return RuleSetDocument.model_validate(cases_file["rule_documents"][case["document"]])


# --- the minimum a saved rule set carries (FR-016.17; Q10 (a)) -------------------------------------

#: An output value that reads as "did not fire": false, zero, null, or an empty span list.
NOT_FIRING: tuple[Any, ...] = (False, 0, None, "[]")


def fires(value: Any) -> bool:
    """Whether an output value reads as fired (typed: ``0`` and ``False`` are not firing, ``"0"``
    is)."""
    return not any(same(value, quiet) for quiet in NOT_FIRING)


def minimum_cases(compiled: CompiledRuleSet) -> list[dict[str, Any]]:
    """What the document's cases lack: per output column, one case whose expected value fires and
    one whose does not; per veto, one case in which that veto removes a span (read by evaluating
    the case's input, since a case states only ``vetoed``). Empty when the minimum is met."""
    document = compiled.document
    missing: list[dict[str, Any]] = []
    for out in document.outputs:
        values = [case.expect.outputs.get(out.column) for case in document.cases]
        if not any(fires(v) for v in values):
            missing.append({"output": out.column, "needs": "a case where it fires"})
        if all(fires(v) for v in values):
            missing.append({"output": out.column, "needs": "a case where it does not fire"})
    if document.vetoes:
        hit: set[str] = set()
        for case in document.cases:
            text, element = case_input(case)
            removed = compiled.evaluate(text, element).vetoes
            hit |= {veto for veto, n in removed.items() if n}
        missing += [
            {"veto": v.id, "needs": "a case in which the veto removes a span"}
            for v in document.vetoes
            if v.id not in hit
        ]
    return missing
