"""A label run whose worker died, and the structured-output probe on a long row (005 FTASKS 9.5,
9.7; both found live on the deployed stack 2026-10-18, ``0xcc/reviews/live2_2026-10-17_controls.md``
"005 17.4 on the deployed stack, the GPU half").

9.5: a SIGKILLed labeling worker left ``lr_195f533e…`` ``running`` for good. The janitor reaped its
job, but no ``label_run`` reap hook existed, so the run was neither cancellable nor resumable and
its miLLM lease stayed held until Beat's cleanup. These tests run the REAL janitor sweep (the live
registry), not a fixture that writes the run's state itself.

9.7: the FR-005.43 probe drew a row longer than the context; ``ContextOverflow`` escaped and the
run failed ``INTERNAL_ERROR`` before scoring anything, and a resume drew the same row again.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.clock import utc_now
from src.core.database import sync_session_factory
from src.services.label_run_service import orphaned_runs
from src.services.model_lease_holder import HOLDER, LeaseTicket
from src.workers import janitor
from tests.integration.labeling.test_judge_and_rederive import RUBRIC, judge_body
from tests.integration.labeling.test_label_run_worker import (
    SimulatedCrash,
    keys_labeled,
    label_count,
    request_cancel,
    start,
)
from tests.support.labeling_fixtures import Labeling, make_version, set_operator, set_role

LIMIT = {"label_run": timedelta(seconds=1)}


def age_job(job_id: str, minutes: int = 30) -> None:
    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_jobs SET heartbeat_at = now() - make_interval(mins => :m), "
                "started_at = now() - make_interval(mins => :m) WHERE id = :j"
            ),
            {"j": job_id, "m": minutes},
        )
        db.commit()


def sweep() -> list[str]:
    with sync_session_factory()() as db:
        return [r.job_id for r in janitor.sweep(db, active_ids=set(), limits=LIMIT)]


def crash_like_sigkill(
    labeling: Labeling, monkeypatch: pytest.MonkeyPatch, job_id: str, at_call: int
) -> None:
    """Kill the job mid-chunk the way SIGKILL does: no ``except``, no ``finally`` — so the
    engine's ``holder.leave`` on the way out does not run either (that is what left the lease
    held live for 18 minutes)."""
    labeling.millm.on_score = lambda n: (
        (_ for _ in ()).throw(SimulatedCrash()) if n == at_call else None
    )
    with monkeypatch.context() as m:
        m.setattr(HOLDER, "leave", lambda *_a, **_k: None)
        with pytest.raises(SimulatedCrash):
            labeling.run_job(job_id)
    labeling.millm.on_score = None


def test_the_label_job_reap_hook_and_reconciler_are_in_the_live_registry() -> None:
    """Read after importing exactly the modules the Celery app includes (the janitor runs in a
    worker that imports them all), so a hook defined but never registered fails here."""
    from src.core.celery_app import TASK_MODULES

    for module in TASK_MODULES:
        importlib.import_module(module)
    from src.workers import label_run_tasks

    for kind in ("label_run", "label_rederive", "label_aggregate"):
        assert janitor.ON_REAP[kind] is label_run_tasks.reap_label_run, kind
    assert janitor.RECONCILERS["label_runs"] is label_run_tasks.reconcile_label_runs


@pytest.mark.parametrize(
    ("cancel_first", "expected_state"), [(False, "failed"), (True, "cancelled")]
)
async def test_a_killed_worker_is_reaped_into_a_resumable_run_and_its_lease_released(
    client: httpx.AsyncClient,
    labeling: Labeling,
    monkeypatch: pytest.MonkeyPatch,
    cancel_first: bool,
    expected_state: str,
) -> None:
    """9.5 as seen live: killed mid-chunk (one committed chunk, half the next scored), optionally
    after an operator cancel nobody was left to see. The janitor's reap moves the run to a state
    the resume path accepts, keeps the committed labels, releases the lease at once (one DELETE,
    carrying the lease the job took), and a resume finishes every row exactly once."""
    run = await start(client, labeling, 500)
    job_id = labeling.job_for(run["id"])
    crash_like_sigkill(labeling, monkeypatch, job_id, at_call=250)
    assert labeling.run(run["id"]).state == "running"
    assert label_count(run["id"]) == 200
    (lease_id,) = list(labeling.millm.leases)  # held: no finally ran
    assert labeling.millm.calls("/api/models/7/lease", "DELETE") == []
    # the live symptom, before the reap: a cancel only asks a dead worker
    if cancel_first:
        request_cancel(job_id)

    age_job(job_id)
    assert sweep() == [job_id]

    stuck = labeling.run(run["id"])
    assert stuck.state == expected_state
    assert stuck.error is not None and stuck.error["code"] == "WORKER_LOST"
    assert "no heartbeat" in stuck.error["message"]
    assert label_count(run["id"]) == 200  # committed labels kept
    deletes = labeling.millm.calls("/api/models/7/lease", "DELETE")
    assert len(deletes) == 1 and deletes[0].headers["x-millm-lease"] == lease_id
    assert labeling.millm.leases == {}
    detail = (await client.get(f"/api/v1/label-runs/{run['id']}")).json()
    assert detail["resumable"] is True

    calls_before = labeling.millm.scoring_calls
    response = await client.post(f"/api/v1/label-runs/{run['id']}/resume")
    assert response.status_code == 200, response.text
    final = labeling.run_until_done(run["id"])
    assert final.state == "completed"
    keys = keys_labeled(run["id"])
    assert len(keys) == len(set(keys)) == 500
    assert labeling.millm.scoring_calls - calls_before == 300  # the half chunk is rescored
    # a second sweep finds nothing more to do
    assert sweep() == []
    assert labeling.run(run["id"]).state == "completed"


async def test_reaping_one_member_of_a_shared_lease_does_not_release_it(
    labeling: Labeling,
) -> None:
    """FR-005.37: the lease is shared by every job on the model. A reaped member leaves; only the
    last member's leave releases (one DELETE, after the second)."""
    from src.services.label_run_service import caller_for
    from tests.support.fake_millm import ORIGIN

    set_operator()
    with caller_for(ORIGIN + "/v1", None) as caller:
        first = HOLDER.join(caller, "JEV-9B-decision", "job_a")
        HOLDER.join(caller, "JEV-9B-decision", "job_b")
    assert len(labeling.millm.calls("/api/models/7/lease", "POST")) == 1

    def lease_caller(base_url: str) -> Any:
        return caller_for(base_url, None)

    assert HOLDER.leave_for("job_a", lease_caller) == 1
    assert labeling.millm.calls("/api/models/7/lease", "DELETE") == []
    assert HOLDER.leave_for("job_a", lease_caller) == 0  # already left
    assert HOLDER.leave_for("job_b", lease_caller) == 1
    deletes = labeling.millm.calls("/api/models/7/lease", "DELETE")
    assert len(deletes) == 1
    assert isinstance(first, LeaseTicket)
    assert deletes[0].headers["x-millm-lease"] == first.lease_id


def stick(run_id: str, job_id: str, job_status: str, ended_minutes_ago: int) -> None:
    """The production end state (``lr_47191807…``, ``lr_195f533e…``): the job already reaped by a
    janitor that had no hook, the run still ``running``."""
    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_jobs SET status = :s, error = 'The worker stopped reporting: no "
                "heartbeat', completed_at = now() - make_interval(mins => :m) WHERE id = :j"
            ),
            {"j": job_id, "s": job_status, "m": ended_minutes_ago},
        )
        db.execute(text("UPDATE dw_label_runs SET state = 'running' WHERE id = :r"), {"r": run_id})
        db.commit()


async def test_runs_stuck_before_the_hook_existed_are_reconciled_by_the_sweep(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """After deploy the hook never fires again for runs whose job was reaped earlier; the sweep's
    reconciliation applies the same transition. Left alone: a run whose job ended within the
    grace (the API's own two-commit cancel), and a run with a live job."""
    old_cancelled = await start(client, labeling, 5)
    old_failed = await start(client, labeling, 5)
    recent = await start(client, labeling, 5)
    live = await start(client, labeling, 5)
    stick(old_cancelled["id"], labeling.job_for(old_cancelled["id"]), "cancelled", 30)
    stick(old_failed["id"], labeling.job_for(old_failed["id"]), "failed", 11 * 24 * 60)
    stick(recent["id"], labeling.job_for(recent["id"]), "cancelled", 0)
    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_label_runs SET state = 'running' WHERE id = :r"), {"r": live["id"]}
        )
        db.execute(
            text(
                "UPDATE dw_jobs SET status = 'running', heartbeat_at = now() + interval '1 hour' "
                "WHERE id = :j"
            ),
            {"j": labeling.job_for(live["id"])},
        )
        db.commit()

    # the live 409s, before the sweep
    for run in (old_cancelled, old_failed):
        refused = await client.post(f"/api/v1/label-runs/{run['id']}/resume")
        assert refused.json()["error"]["code"] == "RUN_NOT_RESUMABLE"

    assert sweep() == []  # nothing reaped: this is the reconciliation, not the hook
    assert labeling.run(old_cancelled["id"]).state == "cancelled"
    assert labeling.run(old_failed["id"]).state == "failed"
    for run in (old_cancelled, old_failed):
        moved = labeling.run(run["id"])
        assert moved.error is not None and moved.error["code"] == "WORKER_LOST"
        assert "without updating the run" in moved.error["message"]
    assert labeling.run(recent["id"]).state == "running"
    assert labeling.run(live["id"]).state == "running"
    # past the grace, the recent run is orphaned; the run with a live job never is (the grace
    # must not be what protects it: its job's updated_at is recent too)
    with sync_session_factory()() as db:
        later = {r for r, _ in orphaned_runs(db, now=utc_now() + timedelta(days=1))}
    assert later == {recent["id"]}

    response = await client.post(f"/api/v1/label-runs/{old_failed['id']}/resume")
    assert response.status_code == 200, response.text
    assert labeling.run_until_done(old_failed["id"]).state == "completed"
    assert label_count(old_failed["id"]) == 5


async def test_a_resumed_run_is_not_moved_by_its_old_jobs_reap(
    client: httpx.AsyncClient, labeling: Labeling, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hook moves the run only when the reaped job is its LAST job: once a resume added a
    newer job, the run belongs to that one."""
    from src.services.label_run_service import end_after_worker_lost

    run = await start(client, labeling, 5)
    first_job = labeling.job_for(run["id"])
    stick(run["id"], first_job, "failed", 30)
    with sync_session_factory()() as db:
        db.execute(text("UPDATE dw_label_runs SET state='failed' WHERE id=:r"), {"r": run["id"]})
        db.commit()
    assert (await client.post(f"/api/v1/label-runs/{run['id']}/resume")).status_code == 200
    with sync_session_factory()() as db:
        assert end_after_worker_lost(db, first_job, "failed", "late reap") is None
    assert labeling.run(run["id"]).state == "queued"


# --- 9.7: the structured-output probe and a row longer than the context ---------------------

SCHEMA_RUBRIC = {
    **RUBRIC,
    "parser": "json_v1",
    "json_schema": {
        "type": "object",
        "required": ["verdict"],
        "properties": {"verdict": {"type": "string"}},
    },
}


def _key(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def overflow_row_first(others: list[str]) -> str:
    """A context-overflowing row whose row key sorts before ``others``, so it is the FIRST row the
    probe draws (the sample is ordered by row key), as the live 3-row version's was."""
    for n in range(1, 500):
        candidate = "OVERFLOW " * n
        if all(_key(candidate) < _key(o) for o in others):
            return candidate
    raise AssertionError("no candidate sorts first")


async def judge_run(client: httpx.AsyncClient, labeling: Labeling, rows: list[str]) -> str:
    set_operator()
    set_role("judge", model="JEV-9B-decision", protocol="openai_chat")
    rubric = (
        await client.post("/api/v1/rubrics", json={"name": "j/overflow", "body": SCHEMA_RUBRIC})
    ).json()
    version_id = make_version(labeling.data_dir, rows)
    response = await client.post("/api/v1/label-runs", json=judge_body(version_id, rubric["id"]))
    assert response.status_code == 201, response.text
    assert response.json()["structured_output"] == "json_schema"
    return str(response.json()["id"])


def chat_calls(labeling: Labeling, pred: Callable[[str], bool]) -> int:
    return sum(
        1
        for c in labeling.millm.calls("/v1/chat/completions")
        if pred(" ".join(str(m.get("content", "")) for m in c.body.get("messages") or []))
    )


async def test_a_probe_row_longer_than_the_context_is_passed_over_and_the_run_completes(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """The live 3-row case: one row of 160k tokens, drawn first by the probe. The probe moves to
    the next sampled row; the run completes with the long row skipped as context_overflow."""
    labeling.millm.judge_answer = lambda msgs: '{"verdict": "yes"}'
    fine = ["a short joke", "another short joke"]
    long_row = overflow_row_first(fine)
    run_id = await judge_run(client, labeling, [long_row, *fine])
    final = labeling.run_until_done(run_id)
    assert final.state == "completed", final.error
    assert final.counts == {"yes": 2, "skipped": 1}
    assert final.structured_output == "json_schema"  # decided on the row that fit
    assert final.endpoint_snapshot["structured_probe"] == {"rows_overflowed": 1, "reason": None}
    # the long row: once for the probe, once in the row loop (skipped there, not retried)
    assert chat_calls(labeling, lambda t: "OVERFLOW" in t) == 2
    assert chat_calls(labeling, lambda t: "OVERFLOW" not in t) == 3  # 1 probe + 2 rows
    labels = (await client.get(f"/api/v1/label-runs/{run_id}/labels?outcome=skipped")).json()
    assert [i["skip_reason"] for i in labels["items"]] == ["context_overflow"]
    assert labels["items"][0]["row_key"] == _key(long_row)


async def test_when_every_probe_row_overflows_the_run_uses_strict_parse_with_the_reason(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    labeling.millm.judge_answer = lambda msgs: '{"verdict": "yes"}'
    run_id = await judge_run(client, labeling, ["OVERFLOW one", "OVERFLOW two"])
    final = labeling.run_until_done(run_id)
    assert final.state == "completed", final.error
    assert final.counts == {"skipped": 2}
    assert final.structured_output == "strict_parse"
    probe = final.endpoint_snapshot["structured_probe"]
    assert probe["rows_overflowed"] == 2 and "exceeded the model's context" in probe["reason"]


async def test_another_endpoint_error_on_the_probe_stops_the_run_resumably_with_its_code(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """Any other endpoint error on the probe used to escape the same way (INTERNAL_ERROR). It now
    stops the run with the endpoint's own code, and a resume after the fix completes it."""
    labeling.millm.judge_answer = lambda msgs: '{"verdict": "yes"}'
    labeling.millm.fail_status = 401
    run_id = await judge_run(client, labeling, ["one", "two", "three"])
    stopped = labeling.run_until_done(run_id)
    assert stopped.state == "failed"
    assert stopped.error is not None and stopped.error["code"] == "ENDPOINT_UNAUTHORIZED"
    labeling.millm.fail_status = None
    assert (await client.post(f"/api/v1/label-runs/{run_id}/resume")).status_code == 200
    final = labeling.run_until_done(run_id)
    assert final.state == "completed" and final.counts == {"yes": 3}
