"""Resume after a failure at each step, cancel, and the agent approval path (FTASKS 6.4, 6.5;
FR-009.27, FR-009.73, FR-009.82; P-06, P-08)."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.models import Approval, Publish
from tests.support.publish_fixtures import store_token
from tests.support.send_fixtures import API, NAMESPACE, SendDriver, ready_set, start_send

AGENT = {"X-Dataworks-Agent": "agent:test-agent"}


def _publish_counts(send_id: str) -> Counter[str]:
    with sync_session_factory()() as db:
        return Counter(p.repo_id for p in db.query(Publish).filter(Publish.send_id == send_id))


async def test_resume_after_a_failed_publish_repeats_no_completed_step(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, versions, repos = await ready_set(client, sender)
    started = await start_send(client, set_id)
    # the token is gone after the first publish: the second publish is refused (008 C-2)
    from src.workers import publish_tasks

    original = publish_tasks.run_publish_job
    calls = {"n": 0}

    def flaky(job_id: str) -> dict[str, object]:
        calls["n"] += 1
        if calls["n"] == 2:
            with get_sync_engine().begin() as conn:
                conn.execute(text("DELETE FROM dw_app_settings WHERE key = 'hf_token'"))
        return original(job_id)

    publish_tasks.run_publish_job = flaky  # type: ignore[assignment]
    try:
        assert sender.run(started["job_id"])["status"] == "failed"
    finally:
        publish_tasks.run_publish_job = original  # type: ignore[assignment]
    send = sender.send(started["send_id"])
    assert send.error["step"] == "publish" and send.error["code"] == "publish_failed"
    assert sender.mistudio.requests == [] or all(
        r.path == "/api/openapi.json" for r in sender.mistudio.requests
    )
    await store_token(client)
    resumed = await client.post(f"{API}/detector-sends/{send.id}/resume")
    assert resumed.status_code == 202, resumed.text
    assert sender.run(resumed.json()["job_id"])["status"] == "completed"
    counts = _publish_counts(send.id)
    # the version published before the failure was not published again; the failed one twice
    assert sorted(counts.values()) == [1, 1, 2], counts
    assert sender.send(send.id).job_ids == [started["job_id"], resumed.json()["job_id"]]


@pytest.mark.parametrize(
    ("key", "route", "prefix"),
    [
        ("POST /datasets/download", "POST", "/api/v1/datasets/download"),
        ("POST /probe-monitors/datasets", "POST", "/api/v1/probe-monitors/datasets"),
    ],
)
async def test_resume_after_a_failed_mistudio_step_repeats_no_completed_call(
    client: httpx.AsyncClient,
    operator_name: str,
    data_dir: Path,
    sender: SendDriver,
    key: str,
    route: str,
    prefix: str,
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    # the first call of this kind succeeds, the second fails
    original = sender.mistudio.handle
    seen = {"n": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == route and request.url.path == prefix:
            seen["n"] += 1
            if seen["n"] == 2:
                sender.mistudio.requests.append(
                    type(sender.mistudio.requests[0])(route, prefix, {}, {}, {"failed": True})
                )
                return httpx.Response(500, json={"detail": "scripted failure"})
        return original(request)

    sender.mistudio.handle = handle  # type: ignore[method-assign]
    from src.clients import mistudio_client

    mistudio_client.TRANSPORT = httpx.MockTransport(handle)
    assert sender.run(started["job_id"])["status"] == "failed"
    before = [c.body for c in sender.mistudio.calls(route, prefix) if c.body != {"failed": True}]
    assert len(before) == 1
    sender.mistudio.handle = original  # type: ignore[method-assign]
    mistudio_client.TRANSPORT = sender.mistudio.transport()
    resumed = await client.post(f"{API}/detector-sends/{started['send_id']}/resume")
    assert sender.run(resumed.json()["job_id"])["status"] == "completed"
    # completed steps are skipped, not re-run as "reused" (control C22)
    after = sender.steps(started["send_id"])
    assert sum(1 for s in after.values() if s.state == "reused") == 0
    bodies = [c.body for c in sender.mistudio.calls(route, prefix) if c.body != {"failed": True}]
    # the call that succeeded before the failure was never sent again
    assert len(bodies) == 4
    assert bodies.count(before[0]) == 1
    # publishes were all done before the failure: none repeated
    assert sorted(_publish_counts(started["send_id"]).values()) == [1, 1, 1]


async def test_cancel_between_steps_keeps_completed_steps_and_resumes(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    from src.workers import publish_tasks

    original = publish_tasks.run_publish_job

    def then_cancel(job_id: str) -> dict[str, object]:
        out = original(job_id)
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:i"
                ),
                {"i": started["job_id"]},
            )
        return out

    publish_tasks.run_publish_job = then_cancel  # type: ignore[assignment]
    try:
        sender.run(started["job_id"])
    finally:
        publish_tasks.run_publish_job = original  # type: ignore[assignment]
    send = sender.send(started["send_id"])
    assert send.state == "cancelled"
    assert sender.job(started["job_id"]).status == "cancelled"
    done = [s for s in sender.steps(send.id).values() if s.state == "done"]
    assert len(done) == 1 and done[0].step == "publish"
    resumed = await client.post(f"{API}/detector-sends/{send.id}/resume")
    assert sender.run(resumed.json()["job_id"])["status"] == "completed"
    assert sorted(_publish_counts(send.id).values()) == [1, 1, 1]


async def test_an_agent_send_waits_for_approval_and_runs_once(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    pending = await client.post(
        f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE}, headers=AGENT
    )
    assert pending.status_code == 202, pending.text
    approval_id = pending.json()["approval_id"]
    assert pending.json()["action"] == "hub_push"
    assert sender.send_jobs() == []  # nothing runs before the operator approves
    with sync_session_factory()() as db:
        approval = db.get(Approval, approval_id)
        assert approval is not None
        send_id = approval.payload["send_id"]
        assert len(approval.payload["publish_digests"]) == 3
    approved = await client.post(f"{API}/approvals/{approval_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed", approved.json()
    [job_id] = sender.send_jobs()
    assert sender.run(job_id)["status"] == "completed"
    send = sender.send(send_id)
    assert send.approval_id == approval_id and send.started_by == "agent:test-agent"
    assert send.approved_by == operator_name
    with sync_session_factory()() as db:
        pubs = db.query(Publish).filter(Publish.send_id == send_id).all()
        assert len(pubs) == 3 and {p.approval_id for p in pubs} == {approval_id}
    again = await client.post(f"{API}/approvals/{approval_id}/approve")
    assert again.status_code == 409  # executed once


async def test_a_plan_changed_after_approval_is_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    pending = await client.post(
        f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE}, headers=AGENT
    )
    approval_id = pending.json()["approval_id"]
    # the operator edits the set after the agent asked
    set_row = (await client.get(f"{API}/detector-sets/{set_id}")).json()
    roles = [
        {
            k: r[k]
            for k in (
                "role",
                "version_id",
                "split",
                "input_column",
                "label_column",
                "label_mapping",
                "pair_column",
                "negatives_basis",
            )
        }
        for r in set_row["roles"]
    ]
    roles[0]["display_name"] = "renamed training view"
    patched = await client.patch(f"{API}/detector-sets/{set_id}", json={"roles": roles})
    assert patched.status_code == 200, patched.text
    approved = await client.post(f"{API}/approvals/{approval_id}/approve")
    assert approved.json()["status"] == "failed"
    assert approved.json()["error"]["code"] == "approval_mismatch"
    assert sender.send_jobs() == []


async def test_an_expired_approval_never_runs(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    pending = await client.post(
        f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE}, headers=AGENT
    )
    approval_id = pending.json()["approval_id"]
    with get_sync_engine().begin() as conn:
        conn.execute(
            text("UPDATE dw_approvals SET expires_at = now() - :d WHERE id = :i"),
            {"d": timedelta(hours=1), "i": approval_id},
        )
    approved = await client.post(f"{API}/approvals/{approval_id}/approve")
    assert approved.status_code == 409
    assert sender.send_jobs() == []


async def test_008_refuses_a_publish_the_send_approval_does_not_list(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    """008's own check, reached through the send: a digest missing from the approval refuses."""
    set_id, _, _ = await ready_set(client, sender)
    pending = await client.post(
        f"{API}/detector-sets/{set_id}/send", json={"namespace": NAMESPACE}, headers=AGENT
    )
    approval_id = pending.json()["approval_id"]
    await client.post(f"{API}/approvals/{approval_id}/approve")
    [job_id] = sender.send_jobs()
    with get_sync_engine().begin() as conn:
        conn.execute(text("ALTER TABLE dw_approvals DISABLE TRIGGER USER"))
        conn.execute(
            text(
                "UPDATE dw_approvals SET payload = jsonb_set(payload, '{publish_digests}', '[]') "
                "WHERE id = :i"
            ),
            {"i": approval_id},
        )
        conn.execute(text("ALTER TABLE dw_approvals ENABLE TRIGGER USER"))
    assert sender.run(job_id)["status"] == "failed"
    with sync_session_factory()() as db:
        from src.models import DetectorSend

        send = db.query(DetectorSend).filter(DetectorSend.approval_id == approval_id).one()
        assert send.error["code"] == "approval_mismatch"
        assert db.query(Publish).filter(Publish.send_id == send.id).count() == 0
