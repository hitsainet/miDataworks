"""The ``next_steps`` guard is wired into the shared API test client, and it bites (022 FR-022.8).

The guard (``tests/support/next_steps_guard.py``) checks every JSON answer the API tests produce. A
hook that is not installed would pass every test silently, so this drives one refusal carrying
``details.next`` through the shared ``client`` and asserts the guard counted it.
"""

from __future__ import annotations

import httpx
import pytest

from tests.support import next_steps_guard
from tests.support.result_language import validate


async def test_the_shared_client_runs_the_guard(client: httpx.AsyncClient) -> None:
    before = len(next_steps_guard.INSPECTED)
    response = await client.get("/api/v1/rule-sets/no-such-rule-set")
    assert response.status_code == 404, response.text
    details = response.json()["error"]["details"]
    assert details["next"]["tool"] == "dataworks_list_rule_sets", "the legacy next is unchanged"
    assert details["next_steps"] == [details["next"]]
    validate("next_step", details["next"])
    assert len(next_steps_guard.INSPECTED) == before + 1, "the guard did not see the answer"


def test_the_guard_refuses_a_next_without_next_steps() -> None:
    step = {"tool": "dataworks_list_rule_sets", "args": {}, "why": "find one"}
    with pytest.raises(AssertionError, match="next_steps"):
        next_steps_guard.check_body({"next": step}, "GET /x")
    with pytest.raises(AssertionError, match="next_steps"):
        next_steps_guard.check_body({"error": {"details": {"next": step}}}, "GET /x")
    assert next_steps_guard.check_body({"next": step, "next_steps": [step]}, "GET /x") == 1
    assert next_steps_guard.check_body({"items": []}, "GET /x") == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, []),
        (
            {"tool": "dataworks_a", "args": {"x": 1}, "why": "w"},
            [{"tool": "dataworks_a", "args": {"x": 1}, "why": "w"}],
        ),
        (
            {"step": 3, "tool": "dataworks_b", "args": {}, "why": "w"},
            [{"tool": "dataworks_b", "args": {}, "why": "w"}],
        ),
        (
            [{"tool": "dataworks_a", "args": {}, "why": "1"}, "not a step"],
            [{"tool": "dataworks_a", "args": {}, "why": "1"}],
        ),
    ],
)
def test_add_next_steps_lists_and_leaves_next(value: object, expected: object) -> None:
    import copy

    from src.rules.next_step import add_next_steps

    before = copy.deepcopy(value)
    body = {"next": value}
    out = add_next_steps(body)
    assert out is body and body["next"] == before, "next is never touched"
    assert body["next_steps"] == expected
    for step in body["next_steps"]:
        validate("next_step", step)
    assert add_next_steps({"other": 1}) == {"other": 1}


# -- review R2 (findings 5, 9): contract limits, pinned with values written out here ------------


def test_a_long_why_is_cut_to_the_contracts_300() -> None:
    from src.rules.next_step import as_next_step

    step = as_next_step({"tool": "dataworks_a", "args": {}, "why": "w" * 400})
    assert step is not None and step["why"] == "w" * 300
    validate("next_step", step)


def test_a_blank_why_becomes_call_the_tool() -> None:
    from src.rules.next_step import as_next_step

    for why in ("", "   ", None):
        step = as_next_step({"tool": "dataworks_a", "args": {}, "why": why})
        assert step == {"tool": "dataworks_a", "args": {}, "why": "call dataworks_a"}
        validate("next_step", step)


def test_next_steps_holds_at_most_five() -> None:
    from src.rules.next_step import add_next_steps

    body = {"next": [{"tool": f"dataworks_t{i}", "args": {}, "why": str(i)} for i in range(7)]}
    add_next_steps(body)
    assert [s["tool"] for s in body["next_steps"]] == [f"dataworks_t{i}" for i in range(5)]
    assert len(body["next"]) == 7, "next is never touched"
    validate("result_fields", {"next_steps": body["next_steps"]})


def test_the_guard_looks_into_nested_results() -> None:
    step = {"tool": "dataworks_list_rule_sets", "args": {}, "why": "find one"}
    with pytest.raises(AssertionError, match="body.result has next"):
        next_steps_guard.check_body({"result": {"next": step}}, "POST /x")


def test_the_guard_refuses_a_next_steps_outside_the_contract() -> None:
    """Review R3: a next_steps of prose strings (the leakage plan's, before 022) fails."""
    import jsonschema

    with pytest.raises(jsonschema.ValidationError):
        next_steps_guard.check_body({"next_steps": ["dataworks_save_recipe(name)"]}, "POST /x")
    step = {"tool": "dataworks_a", "args": {}, "why": "w"}
    with pytest.raises(jsonschema.ValidationError):
        next_steps_guard.check_body({"next": step, "next_steps": [{**step, "extra": 1}]}, "GET /x")


def test_a_recorded_refusal_nested_in_a_result_is_carried_verbatim() -> None:
    step = {"tool": "dataworks_list_rule_sets", "args": {}, "why": "find one"}
    body = {"first_refusal": {"code": "x", "details": {"next": step}}}
    assert next_steps_guard.check_body(body, "POST /x") == 0
    with pytest.raises(AssertionError, match="body.error.details has next"):
        next_steps_guard.check_body({"error": {"details": {"next": step}}}, "POST /x")
