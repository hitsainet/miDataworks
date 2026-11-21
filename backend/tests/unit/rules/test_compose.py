"""Signals, vetoes, expressions and outputs (FTASKS 2.4, 2.5, 2.8; FTDD 016 sections 5.3, 5.5, 10.3).

The contract's ``rule_kinds`` cases run in ``test_rule_signal_conformance.py``; this file adds the
fixture rule sets' own cases and the operation-count guard.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.rules import cases as rule_cases
from src.rules import compose, dialect
from src.rules.rule_set import RuleSetDocument, parse
from src.rules.validators import VALIDATORS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "behaviours"
DOCS = rule_cases.load_cases()["rule_documents"]


def _fixture(name: str) -> RuleSetDocument:
    return parse(json.loads((FIXTURES / name / "rule_set.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", ["fixture-alpha", "fixture-beta"])
def test_each_fixture_rule_set_passes_its_own_cases(name: str) -> None:
    outcomes = rule_cases.run_document_cases(_fixture(name))
    assert outcomes and all(o.passed for _, o in outcomes), [(c, o.mismatches) for c, o in outcomes]


def test_a_masked_value_is_vetoed_while_a_real_one_beside_it_fires() -> None:
    doc = parse(DOCS["veto_masked"])
    result = compose.evaluate(doc, "XXX-XX-1234 and 123-45-6789")
    assert result.vetoed and result.vetoes == {"masked_value": 1}
    assert result.outputs["spans"] == "[[16,27]]" and result.outputs["hit"] is True


def test_proximity_both_edges_in_both_units() -> None:
    cp = parse(DOCS["proximity_code_points"])
    by = parse(DOCS["proximity_utf8_bytes"])
    inside = "ssn ééé 123-45-6789"  # 5 code points, 8 bytes
    assert compose.evaluate(cp, inside).outputs["distance"] == 5
    assert compose.evaluate(by, inside).outputs["distance"] == 8
    assert compose.evaluate(cp, "ssn éééé 123-45-6789").outputs["near"] is False
    assert compose.evaluate(by, "ssn ééé  123-45-6789").outputs["near"] is False


def test_the_lexical_cut_is_inclusive() -> None:
    doc = parse(DOCS["lexical_presence"])
    assert compose.evaluate(doc, "zorbel quint").outputs["lx"] is True  # 2.5 >= 2.5
    assert compose.evaluate(doc, "zorbel").outputs["lx"] is False


def test_an_unknown_validator_version_is_refused_not_resolved() -> None:
    doc = json.loads(json.dumps(DOCS["validator_scan"]))
    doc["signals"][0]["validator"]["version"] = 7
    with pytest.raises(compose.RuleSetNotRunnable) as caught:
        compose.compile_rule_set(parse(doc))
    assert caught.value.code == "validator_unknown"


# --- 2.8 the operation-count guard ----------------------------------------------------------------


class _Counting:
    def __init__(self, inner: Any, counter: dict[str, int], key: str) -> None:
        self._inner, self._counter, self._key = inner, counter, key

    def search(self, text: str, pos: int = 0) -> Any:
        self._counter[self._key] = self._counter.get(self._key, 0) + 1
        return self._inner.search(text, pos)


def test_each_pattern_runs_once_per_row_and_each_candidate_is_checked_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document whose expressions name the pattern signal three times: the signal's matcher runs
    one pass per row (searches = matches + 1) and the validator checks each span once."""
    doc = json.loads(json.dumps(DOCS["veto_masked"]))
    doc["expressions"]["again"] = {"all": [{"signal": "num"}, {"any": [{"signal": "num"}]}]}
    doc["outputs"].append({"column": "again", "type": "bool", "value": {"fired": "again"}})
    compiled = compose.compile_rule_set(parse(doc))
    searches: dict[str, int] = {}
    compiled._compiled["num"] = _Counting(
        compiled._compiled["num"], searches, "num"
    )  # noqa: SLF001
    checks = {"n": 0}
    masked = VALIDATORS[("masked", 1)]
    original = masked.check

    def counting_check(*args: Any, **kwargs: Any) -> Any:
        checks["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(masked, "check", counting_check)
    compiled._validators["masked"] = masked  # noqa: SLF001
    text = "XXX-XX-1234 and 123-45-6789 and 987-65-4320"
    result = compiled.evaluate(text)
    assert result.outputs["hits"] == 2
    assert searches == {"num": 3 + 1}  # three matches, then the search that finds none
    assert checks["n"] == 3  # one check per candidate span


def test_finditer_is_the_only_engine_iteration() -> None:
    m = list(dialect.finditer(dialect.compile_pattern("a", False), "aXa"))
    assert [(x.start(), x.end()) for x in m] == [(0, 1), (2, 3)]


def test_the_case_comparator_keeps_json_types() -> None:
    """Review R1 (mutation R11): a case's ``true`` is not ``1``, ``1`` is not ``1.0`` and ``0`` is
    not ``false``; ``cases.same`` is what every conformance comparison goes through."""
    from src.rules.cases import same

    assert same(True, True) and same(1, 1) and same([1, "a"], [1, "a"])
    assert not same(True, 1)
    assert not same(1, 1.0)
    assert not same(False, 0)
    assert not same({"a": [True]}, {"a": [1]})
    assert not same(None, "")
