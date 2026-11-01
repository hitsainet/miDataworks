"""Running a code rule's cases through the runner: the calls, and what the answers mean.

Pure helpers shared by the save (async, ``repeat: 2`` at the floor), the dry run (async, the limits
in force) and the contract re-check (sync, ``repeat: 1`` at the floor). The runner only counts; the
API compares the answers with the expectations, and the two answers of a ``repeat: 2`` run with each
other (FR-018.20: a fixture host that varies its second answer is caught HERE, M16).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ...clients.runner_client import RunCall
from ...runner_contract.document import CodeRuleCase


def calls_for(cases: Sequence[CodeRuleCase], repeat: int) -> list[RunCall]:
    """One call per case (its function, its params, its one text)."""
    return [RunCall(c.function, c.params, [c.text], repeat) for c in cases]


def expected(case: CodeRuleCase) -> dict[str, Any]:
    return dict(case.expect.model_dump(mode="json"))


def matches(case: CodeRuleCase, result: Mapping[str, Any]) -> bool:
    expect = expected(case)
    if "count" in expect:
        return type(result.get("count")) is int and result["count"] == expect["count"]
    return bool(result.get("reason") == expect["reason"])


def outcome(result: Mapping[str, Any]) -> dict[str, Any]:
    """A result without the dry-run extras (what a stored record or a refusal may carry)."""
    return {k: result[k] for k in ("count", "reason") if k in result}


@dataclass(frozen=True)
class CaseVerdict:
    case: CodeRuleCase
    results: list[dict[str, Any]]

    @property
    def deterministic(self) -> bool:
        return all(outcome(r) == outcome(self.results[0]) for r in self.results[1:])

    @property
    def passed(self) -> bool:
        return self.deterministic and all(matches(self.case, r) for r in self.results)

    def out(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "function": self.case.function,
            "expected": expected(self.case),
            "result": self.results[0],
            "passed": self.passed,
        }


def verdicts(
    cases: Sequence[CodeRuleCase], results: Sequence[Sequence[Mapping[str, Any]]]
) -> list[CaseVerdict]:
    return [CaseVerdict(c, [dict(r) for r in rs]) for c, rs in zip(cases, results, strict=True)]
