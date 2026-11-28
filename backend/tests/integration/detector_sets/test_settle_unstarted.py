"""008's ``_settle_unstarted`` settles only a job that was cancelled before it started (control
C20; the defect 009 found: a second delivery of a publish another worker — or 009's send, running
it in process — had claimed would cancel that RUNNING publish's record)."""

from __future__ import annotations

from pathlib import Path

import httpx
from sqlalchemy import text

from src.core.database import get_sync_engine, sync_session_factory
from src.models import Publish
from src.workers import publish_tasks
from tests.support.send_fixtures import SendDriver, ready_set, start_send


async def test_a_second_delivery_never_cancels_a_running_publish(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path, sender: SendDriver
) -> None:
    set_id, _, _ = await ready_set(client, sender)
    started = await start_send(client, set_id)
    assert sender.run(started["job_id"])["status"] == "completed"
    from src.services.publishing import publish_service

    with sync_session_factory()() as db:
        done = db.query(Publish).filter(Publish.send_id == started["send_id"]).first()
        assert done is not None
        created = publish_service.request_publish(
            db,
            publish_service.PublishRequest(
                done.version_id, done.build_id, "mistudio/other", "private", ""
            ),
            publish_service.OperatorOrigin(),
            publish_service.Who("Test Operator", "operator"),
        )
        job_id, pub_id = created.job.id, created.publish.id
    # the record is "running" as far as a second delivery can tell
    with get_sync_engine().begin() as conn:
        conn.execute(text("UPDATE dw_jobs SET status='running' WHERE id=:i"), {"i": job_id})
        conn.execute(text("UPDATE dw_publishes SET status='uploading' WHERE id=:i"), {"i": pub_id})
    assert publish_tasks.run_publish_job(job_id)["status"] == "skipped"
    with sync_session_factory()() as db:
        assert db.get(Publish, pub_id).status == "uploading"  # type: ignore[union-attr]
    # a job really cancelled before it started IS settled
    with get_sync_engine().begin() as conn:
        conn.execute(text("UPDATE dw_jobs SET status='cancelled' WHERE id=:i"), {"i": job_id})
    publish_tasks.run_publish_job(job_id)
    with sync_session_factory()() as db:
        assert db.get(Publish, pub_id).status == "cancelled"  # type: ignore[union-attr]
