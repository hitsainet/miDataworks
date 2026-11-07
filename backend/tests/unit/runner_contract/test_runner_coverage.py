"""Coverage derived from the contract, and the pending escape-form set (FR-018.12; FTASKS 3.7)."""

from __future__ import annotations

import json

import pytest

from src.runner_contract import cases as cases_mod
from src.runner_contract import coverage
from src.runner_contract.document import PACKAGED_CONTRACT_PATH

CONTRACT = json.loads(PACKAGED_CONTRACT_PATH.read_text(encoding="utf-8"))
CASES = cases_mod.load_cases()


def test_the_cases_file_fully_covers_the_contract() -> None:
    assert coverage.missing_coverage(CONTRACT, CASES) == {}


def test_the_pending_escape_form_classes_are_named_and_not_empty() -> None:
    """ENH-006 D41 handoff: the escape-form hostile vectors are open for the operator. If this set
    is emptied, the covered classes must include them or coverage goes red (the next test)."""
    assert coverage.PENDING_HOSTILE_CLASSES == {1, 2, 3, 4, 9, 17, 18, 19}
    present = {h["number"] for h in CASES["hostile"]}
    # None of the pending classes has a vector yet (they are the operator's to add).
    assert present.isdisjoint(coverage.PENDING_HOSTILE_CLASSES)


def test_emptying_the_pending_set_without_vectors_turns_coverage_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(coverage, "PENDING_HOSTILE_CLASSES", frozenset())
    missing = coverage.missing_coverage(CONTRACT, CASES)
    assert set(missing.get("hostile", [])) == {1, 2, 3, 4, 9, 17, 18, 19}


def test_removing_the_only_vector_of_one_reason_turns_coverage_red() -> None:
    """Control M28 analogue for reasons: drop every rule_timeout vector."""
    stripped = json.loads(json.dumps(CASES))
    stripped["limits"] = [
        c for c in stripped["limits"] if c["expect"].get("reason") != "rule_timeout"
    ]
    missing = coverage.missing_coverage(CONTRACT, stripped)
    assert "rule_timeout" in missing.get("reasons", [])


def test_removing_the_only_vector_of_one_module_turns_coverage_red() -> None:
    stripped = json.loads(json.dumps(CASES))
    stripped["imports"] = [c for c in stripped["imports"] if c["module"] != "re"]
    assert "re" in coverage.missing_coverage(CONTRACT, stripped).get("modules", [])


def test_removing_a_non_pending_hostile_class_turns_coverage_red() -> None:
    stripped = json.loads(json.dumps(CASES))
    stripped["hostile"] = [c for c in stripped["hostile"] if c["number"] != 5]
    assert 5 in coverage.missing_coverage(CONTRACT, stripped).get("hostile", [])


def test_an_empty_contract_reasons_list_fails_closed() -> None:
    missing = coverage.missing_coverage(
        {"reasons": [], "stdlib_allowed": [], "limits_floor": {}, "hostile_classes": []}, CASES
    )
    assert missing["reasons"] and missing["hostile"]


def test_reasons_without_a_vector_are_only_the_two_host_and_db_faults() -> None:
    assert coverage.REASONS_WITHOUT_VECTOR == {"rule_worker_unavailable", "rule_not_approved"}
