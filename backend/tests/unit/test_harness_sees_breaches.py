"""The vector harness can SEE a breach (feature 018; FTDD §11.4; G3 (a); FTASKS 6.8; M9).

Every hostile vector of a breach class must FAIL when run through ``InProcessExecRunner`` (the
host's own ``exec``: no fresh instance, no reply grammar, no count cap). If a vector passed there,
it would prove nothing about the real runner: it would pass whatever the barrier did. This is the
permanent negative control for the guest barrier; the same vectors pass through the real runner in
``runner/tests/test_vectors.py``.

Breach classes (the contract's ``hostile_classes``): 1-4 and 9 (files, environment, sockets,
processes, the real clock; the operator's pending escape-form vectors, ENH-006 D48), 8 (return the
matched text), 10 (carry state between calls), 13 (malformed return) and 14 (count above the cap).
Classes whose breach in-process would hang or exhaust THIS process (5, 6, 11, 12, 16) are not run
here; they are bounded only by the runner. A pending class with no vector is named, not skipped
silently; when the operator adds its vectors they are run here without a change to this file.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.runner_contract.cases import load_cases
from src.runner_contract.coverage import PENDING_HOSTILE_CLASSES
from src.runner_contract.text import replace_lone_surrogates
from tests.support.in_process_exec_runner import InProcessExecRunner

CASES = load_cases()
DOCUMENTS = CASES["documents"]
BREACH_CLASSES = frozenset({1, 2, 3, 4, 8, 9, 10, 13, 14})


def matches(expect: dict[str, Any], got: dict[str, Any]) -> bool:
    """The harness's verdict, the rule ``runner/tests/test_vectors.py`` applies."""
    if "counts" in expect:
        return bool(got.get("counts") == expect["counts"])
    if "count" in expect:
        return bool(got.get("count") == expect["count"]) and type(got.get("count")) is int
    return bool(got.get("reason") == expect["reason"])


def run_case(runner: InProcessExecRunner, case: dict[str, Any]) -> dict[str, Any]:
    document = DOCUMENTS[case["doc"]]
    text = replace_lone_surrogates(case["text"])
    if "calls" in case:
        counts = [
            runner.run(document, case["function"], case["params"], text).get("count")
            for _ in range(case["calls"])
        ]
        return {"counts": counts}
    got: dict[str, Any] = runner.run(document, case["function"], case["params"], text)
    return got


def breach_cases() -> list[dict[str, Any]]:
    hostile = [c for c in CASES["hostile"] if c["number"] in BREACH_CLASSES]
    # Class 10's conformance form (three calls, counts [1, 1, 1]) runs first, on a fresh runner.
    fresh = [c for c in CASES["conformance"] if c["id"] == "conf.fresh_instance"]
    return fresh + hostile


def test_every_breach_vector_fails_without_the_barrier() -> None:
    runner = InProcessExecRunner()
    seen_failing = []
    for case in breach_cases():
        got = run_case(runner, case)
        assert not matches(
            case["expect"], got
        ), f"{case['id']} PASSED with no barrier at all ({got}); it cannot detect a breach"
        seen_failing.append(case["id"])
    assert "conf.fresh_instance" in seen_failing and "hostile.state" in seen_failing


def test_every_breach_class_is_exercised_or_named_pending() -> None:
    present = {c["number"] for c in CASES["hostile"]}
    for number in sorted(BREACH_CLASSES):
        if number in present:
            continue
        assert number in PENDING_HOSTILE_CLASSES, f"breach class {number} has no vector"
    pending_breaches = sorted(BREACH_CLASSES - present)
    assert pending_breaches == [1, 2, 3, 4, 9], pending_breaches


@pytest.mark.parametrize("case_id", ["conf.return_zero", "conf.count_tokens", "conf.params"])
def test_the_harness_passes_an_honest_rule_in_process(case_id: str) -> None:
    """Positive control: a vector that needs no barrier passes in-process, so a failure above is
    the missing barrier, not a broken harness."""
    case = next(c for c in CASES["conformance"] if c["id"] == case_id)
    assert matches(case["expect"], run_case(InProcessExecRunner(), case))
