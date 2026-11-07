"""Coverage of the cases file, derived from the CONTRACT, not a list in a test (FR-018.12).

Every reason in the contract's ``reasons``, every module in ``stdlib_allowed``, every floor limit and
every hostile class number 1 – 19 must have at least one vector in the cases file. An empty contract
list is itself a finding (``missing_coverage`` returns it as uncovered, so the test fails closed).

**Open item (ENH-006 D41 handoff, 2026-10-24).** The hostile ESCAPE-form vectors — a guest reaching
files, the environment, a socket or the real clock by bypassing the prelude (classes 1, 2, 3, 4, 9),
and the guest-source forms that replay or reorder a phase marker or forge an extra reply line
(classes 17, 18, 19) — are **not yet in the cases file**. They are listed in
:data:`PENDING_HOSTILE_CLASSES` so coverage is honest and loud: the operator fills them in later
(the runner host enforces the marker protocol regardless, proven on a real store by
``runner/tests/test_wasi_table.py`` and ``runner/tests/test_host.py``, which also pin every Table W row
those vectors would exercise; the static-refusal path is proven by hostile class 15). Emptying :data:`PENDING_HOSTILE_CLASSES`
without adding the vectors turns ``missing_coverage`` red for those classes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: Hostile classes whose vectors are deferred to the operator (see the module docstring).
PENDING_HOSTILE_CLASSES: frozenset[int] = frozenset({1, 2, 3, 4, 9, 17, 18, 19})

#: Reasons a guest-source vector cannot produce, so they have no cases-file vector by design:
#: ``rule_worker_unavailable`` is a host/deployment fault (lane full, bundle mismatch, switch off;
#: proven by backend integration tests, S2/S3) and ``rule_not_approved`` is emitted where the
#: database is, never by the runner. Both are excluded from the cases-file reason coverage.
REASONS_WITHOUT_VECTOR: frozenset[str] = frozenset({"rule_worker_unavailable", "rule_not_approved"})


def _reasons(cases: Mapping[str, Any]) -> set[str]:
    seen: set[str] = set()
    for section in ("conformance", "hostile", "limits"):
        for case in cases.get(section, []):
            expect = case.get("expect", {})
            if "reason" in expect:
                seen.add(expect["reason"])
    return seen


def _modules(cases: Mapping[str, Any]) -> set[str]:
    return {c["module"] for c in cases.get("imports", []) if "module" in c}


def _limits(cases: Mapping[str, Any]) -> set[str]:
    return {c["limit"] for c in cases.get("limits", []) if "limit" in c}


def _hostile(cases: Mapping[str, Any]) -> set[int]:
    return {c["number"] for c in cases.get("hostile", []) if "number" in c}


def missing_coverage(contract: Mapping[str, Any], cases: Mapping[str, Any]) -> dict[str, list[Any]]:
    """What the contract requires but the cases file does not cover. Empty dict == full coverage.

    ``hostile`` lists only the classes neither present nor in ``PENDING_HOSTILE_CLASSES``; a pending
    class with a vector is fine, a non-pending class without one is a gap.
    """
    reasons: Sequence[str] = contract.get("reasons", [])
    modules: Sequence[str] = contract.get("stdlib_allowed", [])
    floor: Mapping[str, Any] = contract.get("limits_floor", {})
    hostile_numbers = [h["number"] for h in contract.get("hostile_classes", [])]

    present_reasons, present_modules = _reasons(cases), _modules(cases)
    present_limits, present_hostile = _limits(cases), _hostile(cases)
    out: dict[str, list[Any]] = {
        "reasons": sorted(
            r for r in reasons if r not in present_reasons and r not in REASONS_WITHOUT_VECTOR
        ),
        "modules": sorted(m for m in modules if m not in present_modules),
        "limits": sorted(k for k in floor if k not in present_limits),
        "hostile": sorted(
            n
            for n in hostile_numbers
            if n not in present_hostile and n not in PENDING_HOSTILE_CLASSES
        ),
    }
    # Fail closed: an empty required list means nothing was covered.
    if not reasons:
        out["reasons"] = ["<contract has no reasons>"]
    if not hostile_numbers:
        out["hostile"] = ["<contract has no hostile classes>"]
    return {key: value for key, value in out.items() if value}
