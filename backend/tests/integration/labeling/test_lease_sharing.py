"""The shared miLLM lease (005 FTASKS 10.x, 18.3; X-08, P-05; criterion 6)."""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, text

from src.clients.endpoint_caller import EndpointCaller
from src.clients.endpoint_errors import LeaseLost
from src.core.clock import utc_now
from src.core.database import sync_session_factory
from src.models.approval import Approval
from src.models.model_lease import ModelLease, ModelLeaseMember
from src.services.model_lease_holder import (
    LeaseHeldElsewhere,
    LeaseTicket,
    ModelLeaseHolder,
    ModelNotLoaded,
    Unpinned,
)
from tests.integration.labeling.helpers import AGENT, setup_classifier, start_body
from tests.support.db_factories import job
from tests.support.fake_millm import ORIGIN
from tests.support.labeling_fixtures import Labeling


def caller() -> EndpointCaller:
    return EndpointCaller(ORIGIN + "/v1", None, sleep=lambda s: None)


def new_job(status: str = "running") -> str:
    with sync_session_factory()() as db:
        row = job(db, kind="label_run", status=status)
        db.commit()
        return row.id


def test_two_jobs_on_one_model_share_one_lease(labeling: Labeling) -> None:
    holder = ModelLeaseHolder()
    a, b = new_job(), new_job()
    with caller() as c:
        ta = holder.join(c, "JEV-9B-decision", a)
        tb = holder.join(c, "JEV-9B-decision", b)
        assert isinstance(ta, LeaseTicket) and isinstance(tb, LeaseTicket)
        assert ta.lease_id == tb.lease_id
        assert len(labeling.millm.calls("/api/models/7/lease", "POST")) == 1
        holder.leave(c, ta, a)
        assert labeling.millm.calls("/api/models/7/lease", "DELETE") == []
        holder.leave(c, tb, b)
    deletes = labeling.millm.calls("/api/models/7/lease", "DELETE")
    assert len(deletes) == 1 and deletes[0].headers["x-millm-lease"] == ta.lease_id
    acquire = labeling.millm.calls("/api/models/7/lease", "POST")[0]
    assert acquire.body["holder"] == "midataworks" and acquire.body["ttl_seconds"] == 1800
    with sync_session_factory()() as db:
        (row,) = db.execute(select(ModelLease)).scalars().all()
    assert row.state == "released" and ta.lease_id not in row.lease_id_ciphertext


def test_a_different_model_resident_is_refused_with_no_load(labeling: Labeling) -> None:
    holder = ModelLeaseHolder()
    labeling.millm.resident = {
        "id": 99,
        "name": "Qwen2.5-7B",
        "repo_id": None,
        "revision": None,
        "quantization": "Q4",
    }
    with caller() as c, pytest.raises(ModelNotLoaded) as exc:
        holder.join(c, "JEV-9B-decision", new_job())
    assert exc.value.resident == "Qwen2.5-7B"
    assert [r for r in labeling.millm.requests if r.method == "POST"] == []


def test_a_job_on_a_second_model_while_we_hold_the_first_is_refused(labeling: Labeling) -> None:
    holder = ModelLeaseHolder()
    with caller() as c:
        holder.join(c, "JEV-9B-decision", new_job())
        with pytest.raises(ModelNotLoaded):
            holder.join(c, "Qwen2.5-7B", new_job())
    assert len(labeling.millm.calls("/api/models/7/lease", "POST")) == 1


def test_another_holder_is_never_taken_or_broken(labeling: Labeling) -> None:
    labeling.millm.foreign_lease = {"holder": "miforge", "expires_at": "2026-10-07T12:00:00Z"}
    with caller() as c, pytest.raises(LeaseHeldElsewhere) as exc:
        ModelLeaseHolder().join(c, "JEV-9B-decision", new_job())
    assert (exc.value.holder, exc.value.expires_at) == ("miforge", "2026-10-07T12:00:00Z")
    assert labeling.millm.calls("/api/models/7/lease", "DELETE") == []


def test_no_lease_surface_is_unpinned(labeling: Labeling) -> None:
    labeling.millm.lease_supported = False
    with caller() as c:
        assert isinstance(ModelLeaseHolder().join(c, "JEV-9B-decision", new_job()), Unpinned)


def test_renewal_below_two_thirds_and_loss_on_404(labeling: Labeling) -> None:
    holder = ModelLeaseHolder()
    member = new_job()
    with caller() as c:
        ticket = holder.join(c, "JEV-9B-decision", member)
        assert isinstance(ticket, LeaseTicket)
        holder.check(c, ticket)
        assert labeling.millm.calls("/api/models/7/lease/renew") == []  # plenty of TTL left
        with sync_session_factory()() as db:
            db.execute(
                text("UPDATE dw_model_leases SET expires_at = :t"),
                {"t": utc_now() + timedelta(seconds=1100)},
            )
            db.commit()
        holder.check(c, ticket)
        (renew,) = labeling.millm.calls("/api/models/7/lease/renew")
        assert renew.headers["x-millm-lease"] == ticket.lease_id and renew.body == {
            "ttl_seconds": 1800
        }
        labeling.millm.restart()
        with sync_session_factory()() as db:
            db.execute(
                text("UPDATE dw_model_leases SET expires_at = :t"),
                {"t": utc_now() + timedelta(seconds=60)},
            )
            db.commit()
        with pytest.raises(LeaseLost):
            holder.check(c, ticket)
    with sync_session_factory()() as db:
        (row,) = db.execute(select(ModelLease)).scalars().all()
    assert row.state == "lost"


async def test_a_lost_lease_stops_the_run_at_a_chunk_boundary_resumably(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=450)
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()

    def restart_at(n: int) -> None:
        if n == 150:
            labeling.millm.restart()
            with sync_session_factory()() as db:
                db.execute(text("UPDATE dw_model_leases SET expires_at = now()"))
                db.commit()

    labeling.millm.on_score = restart_at
    final = labeling.run_until_done(run["id"])
    assert final.state == "failed" and final.error["code"] == "LEASE_LOST"
    from tests.integration.labeling.test_label_run_worker import label_count

    assert label_count(run["id"]) == 200  # stopped at the first chunk boundary after the loss
    labeling.millm.on_score = None
    assert (await client.post(f"/api/v1/label-runs/{run['id']}/resume")).status_code == 200
    assert labeling.run_until_done(run["id"]).state == "completed"
    assert label_count(run["id"]) == 450
    assert len(labeling.millm.calls("/api/models/7/lease", "POST")) == 2  # re-taken on resume


async def test_another_holder_requeues_the_job(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    version_id, template_id = await setup_classifier(client, labeling, n=5)
    run = (await client.post("/api/v1/label-runs", json=start_body(version_id, template_id))).json()
    labeling.millm.foreign_lease = {"holder": "miforge", "expires_at": "2026-10-07T12:00:00Z"}
    job_id = labeling.job_for(run["id"])
    assert labeling.run_job(job_id)["outcome"] == "queued"
    assert labeling.run(run["id"]).state == "queued"
    job_row = labeling.job(job_id)
    assert job_row.status == "queued" and "miforge" in (job_row.queue_reason or "")
    retry = [
        s
        for s in labeling.sent
        if s[0] == "midataworks.labeling.run_label_run" and s[2].get("countdown")
    ]
    assert retry and retry[-1][2]["countdown"] == 60.0


def test_beat_releases_a_lease_whose_members_are_terminal(labeling: Labeling) -> None:
    holder = ModelLeaseHolder()
    dead = new_job(status="running")
    with caller() as c:
        holder.join(c, "JEV-9B-decision", dead)
    with sync_session_factory()() as db:
        db.execute(text("UPDATE dw_jobs SET status='failed' WHERE id=:j"), {"j": dead})
        db.commit()
    outcome = holder.renew_all(lambda base: caller())
    assert outcome["released"] == 1
    assert len(labeling.millm.calls("/api/models/7/lease", "DELETE")) == 1
    with sync_session_factory()() as db:
        member = db.execute(select(ModelLeaseMember)).scalars().one()
    assert member.left_at is not None


def test_the_beat_task_is_wired(labeling: Labeling, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.workers import lease_tasks

    seen: list[object] = []
    monkeypatch.setattr(
        lease_tasks.HOLDER, "renew_all", lambda f: seen.append(f) or {"released": 0}
    )
    assert lease_tasks.renew_model_leases() == {"released": 0}
    assert len(seen) == 1


def test_the_lease_id_is_never_logged(labeling: Labeling, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    holder = ModelLeaseHolder()
    member = new_job()
    with caller() as c:
        ticket = holder.join(c, "JEV-9B-decision", member)
        assert isinstance(ticket, LeaseTicket)
        logging.getLogger("x").info("debugging %s", ticket.lease_id)
        holder.leave(c, ticket, member)
    assert ticket.lease_id not in caplog.text
    assert ticket.lease_id not in repr(ticket)


async def test_an_agent_takes_the_lease_without_an_approval(
    client: httpx.AsyncClient, labeling: Labeling
) -> None:
    """P-05: taking a lease loads nothing; only the P-07 row count is gated."""
    version_id, template_id = await setup_classifier(client, labeling, n=10)
    run = (
        await client.post(
            "/api/v1/label-runs", json=start_body(version_id, template_id), headers=AGENT
        )
    ).json()
    assert labeling.run_until_done(run["id"]).pinned is True
    with sync_session_factory()() as db:
        assert db.execute(select(Approval)).first() is None


def test_rejoin_after_a_restart_reattaches_each_running_batch(labeling: Labeling) -> None:
    """FR-005.55: re-take the lease, then ONE re-attach call per running batch with the NEW lease;
    no batch is resubmitted."""
    holder = ModelLeaseHolder()
    member = new_job()
    with caller() as c:
        old = holder.join(c, "JEV-9B-decision", member)
        assert isinstance(old, LeaseTicket)
        labeling.millm.restart()
        new = holder.rejoin(c, old, member, ["batch_a", "batch_b"])
    assert new.lease_id != old.lease_id
    reattach = [
        r
        for r in labeling.millm.requests
        if r.path.endswith("/lease") and r.path.startswith("/v1/batches/")
    ]
    assert [r.path for r in reattach] == ["/v1/batches/batch_a/lease", "/v1/batches/batch_b/lease"]
    assert all(r.headers["x-millm-lease"] == new.lease_id for r in reattach)
    assert not [r for r in labeling.millm.requests if r.path == "/v1/batches"]


def test_rejoin_refused_stops(labeling: Labeling) -> None:
    holder = ModelLeaseHolder()
    member = new_job()
    with caller() as c:
        old = holder.join(c, "JEV-9B-decision", member)
        assert isinstance(old, LeaseTicket)
        labeling.millm.restart()
        labeling.millm.batches["done"] = {"id": "done", "status": "completed"}
        with pytest.raises(LeaseLost) as exc:
            holder.rejoin(c, old, member, ["done"])
        assert exc.value.reason == "reattach_refused"
        labeling.millm.restart()
        labeling.millm.foreign_lease = {"holder": "miforge", "expires_at": "x"}
        with pytest.raises(LeaseHeldElsewhere):
            holder.rejoin(c, old, member, ["batch_a"])
