"""Contract re-checks (FR-018.31; FTASKS 10.13): a new contract minor that breaks a stored rule's
case marks it failing, by the Beat task's own code path, with no new approval."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.runner_contract.hashing import code_sha256
from src.services.code_rules import limits
from tests.support import code_rule_fixtures as fx
from tests.support.fake_runner import FakeRunner

from .conftest import CR, count, save

pytestmark = pytest.mark.usefixtures("operator_name")


def _recheck() -> dict[str, Any]:
    from src.workers.code_rule_tasks import recheck_contract_task

    out: dict[str, Any] = recheck_contract_task.run()
    return out


async def test_a_minor_that_breaks_a_case_marks_the_rule_failing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    for behaviour in ("fixture-gamma", "fixture-delta"):
        assert (await save(client, fx.save_body(behaviour))).status_code == 201
    gamma = code_sha256(fx.fixture("fixture-gamma")["document"])
    assert _recheck() == {"checked": 0, "failing": 0, "contract_version": "1.0"}

    fake.contract_version = "1.1"
    fake.twins[gamma] = {"digit_sum": lambda text, params: 0}  # the new minor breaks "fires"
    calls_before = len(fake.runs())
    assert _recheck() == {"checked": 2, "failing": 1, "contract_version": "1.1"}
    new_runs = fake.runs()[calls_before:]
    assert [(r["mode"], r["limits"]) for r in new_runs] == [("save_check", limits.floor())] * 2
    assert count("dw_code_rule_contract_runs", "contract_version = '1.1'") == 2
    status = (await client.get(f"{CR}/gamma-digit-sum/versions/1")).json()["contract_status"]
    assert status == {**status, "contract_version": "1.1", "passed": False, "failing_case": "fires"}
    assert count("dw_approvals") == 0, "a minor needs no new approval"
    # A second pass under the same minor checks nothing again.
    assert _recheck()["checked"] == 0


def test_an_unreachable_or_unconfigured_runner_checks_nothing(fake: FakeRunner) -> None:
    fake.down = True
    assert _recheck() == {"checked": 0, "failing": 0, "runner": "unavailable"}


def test_an_unconfigured_runner_checks_nothing() -> None:
    """No fake installed and RUNNER_URL unset (the default here): the task says why."""
    assert _recheck() == {"checked": 0, "failing": 0, "runner": "runner_unavailable"}


# --- 018 Review R2: a runner fault is never recorded as the rule's failure -------------------------


async def _saved_and_bumped(client: httpx.AsyncClient, fake: FakeRunner) -> None:
    assert (await save(client, fx.save_body("fixture-gamma"))).status_code == 201
    fake.contract_version = "1.1"


async def test_a_runner_without_a_free_slot_marks_nothing_failing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    """A row is final for its contract version (a second pass skips the hash), so a lane-backstop
    tail recorded as "failing" would refuse a sound rule for good. Nothing is written; the next
    pass checks it."""
    await _saved_and_bumped(client, fake)
    fake.starve_after = 1  # 2 cases x repeat 1: the second finds no slot
    out = _recheck()
    assert out == {
        "checked": 0,
        "failing": 0,
        "contract_version": "1.1",
        "stopped": "rule_worker_unavailable",
    }
    assert count("dw_code_rule_contract_runs", "contract_version = '1.1'") == 0
    fake.starve_after = None
    assert _recheck() == {"checked": 1, "failing": 0, "contract_version": "1.1"}
    assert count("dw_code_rule_contract_runs", "contract_version = '1.1' AND passed") == 1


async def test_a_runner_gone_mid_pass_marks_nothing_failing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    await _saved_and_bumped(client, fake)
    fake.runs_down = True  # /health answered; /v1/run is refused through the retry budget
    assert _recheck()["stopped"] == "rule_worker_unavailable"
    assert count("dw_code_rule_contract_runs", "contract_version = '1.1'") == 0


@pytest.mark.parametrize(
    ("code", "recorded"),
    [
        ("limit_above_ceiling", False),
        ("request_invalid", False),
        ("runner_contract_unknown", False),
        ("code_rule_invalid", True),
    ],
)
async def test_only_a_refusal_of_the_document_is_the_rules_failure(
    client: httpx.AsyncClient, fake: FakeRunner, code: str, recorded: bool
) -> None:
    await _saved_and_bumped(client, fake)
    fake.refuse_run = (422, code, "limits.wall_ms is above this runner's ceiling")
    out = _recheck()
    rows = count("dw_code_rule_contract_runs", "contract_version = '1.1' AND NOT passed")
    if recorded:
        assert (out["checked"], out["failing"], rows) == (1, 1, 1)
        assert "stopped" not in out
    else:
        assert (out["checked"], out["failing"], rows) == (0, 0, 0)
        assert out["stopped"] == code


async def test_a_runner_whose_bundle_is_refused_checks_nothing(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    await _saved_and_bumped(client, fake)
    fake.health_status = "bundle_mismatch"
    assert _recheck() == {"checked": 0, "failing": 0, "runner": "unavailable"}
    assert fake.runs("save_check")[1:] == [], "no case ran on a runner refusing its bundle"


async def test_an_archived_code_rule_is_not_rechecked(
    client: httpx.AsyncClient, fake: FakeRunner
) -> None:
    await _saved_and_bumped(client, fake)
    assert (await client.post(f"{CR}/gamma-digit-sum/archive", json={})).status_code == 200
    assert _recheck() == {"checked": 0, "failing": 0, "contract_version": "1.1"}
