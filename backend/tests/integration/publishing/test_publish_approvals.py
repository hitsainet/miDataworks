"""The hub_push approval, at REST and in the service (FR-008.50, 008.51, 008.66, 008.67; AC-US6;
EC-15, EC-16; 008 FTASKS 9.3, 9.4, 9.6).

Two gates must agree: the REST decorator stores an agent request and answers 202, and the service
(``request_publish``) refuses any agent-originated publish whose digest no approval covers — the
gate 009's send worker cannot step around.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update

from src.core.clock import utc_now
from src.core.database import sync_session_factory
from src.core.errors import AppError
from src.models import Approval, Publish
from src.services.job_service import dispatch_queued
from src.services.publishing import publish_service
from tests.support.publish_fixtures import (
    REPO,
    PublishDriver,
    built_version,
    completed_build,
    publish,
    publisher,
    store_token,
)
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def _ready(client, driver, publisher, data_dir) -> tuple[str, dict]:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    return version_id, await completed_build(client, publisher, version_id)


async def test_an_agent_publish_waits_and_nothing_reaches_the_hub(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    response = await publish(client, publisher, version_id, build["id"], headers=AGENT)
    assert response.status_code == 202, response.text
    assert response.json()["action"] == "hub_push"
    publisher.run_publish_tasks()
    assert REPO not in publisher.hub.repos
    with sync_session_factory()() as db:
        assert db.execute(select(Publish)).first() is None
        approval = db.get(Approval, response.json()["approval_id"])
        assert approval is not None
        assert len(approval.payload["publish_digest"]) == 64
        assert approval.payload["facts"]["repo_id"] == REPO


async def test_approving_pushes_once_and_records_who_and_the_approver(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    response = await publish(client, publisher, version_id, build["id"], headers=AGENT)
    approval_id = response.json()["approval_id"]
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed", approved.json()
    again = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert again.status_code == 409
    publisher.run_publish_tasks()
    assert len([c for c in publisher.hub.calls if c[0] == "create_commit"]) == 1
    with sync_session_factory()() as db:
        rows = list(db.execute(select(Publish)).scalars())
        assert len(rows) == 1
        pub = rows[0]
        assert pub.status == "published"
        assert pub.started_by == "agent:dataworks-mcp" and pub.started_by_origin == "agent"
        assert pub.approval_id == approval_id and pub.approved_by == operator_name


async def test_the_card_route_is_gated_too(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    first = await publish(client, publisher, version_id, build["id"])
    publisher.run_publish_tasks()
    commits = publisher.hub.commit_count(REPO)
    response = await client.post(
        f"/api/v1/publishes/{first.json()['publish_id']}/card",
        json={"card_prose": "agent words"},
        headers=AGENT,
    )
    assert response.status_code == 202
    publisher.run_publish_tasks()
    assert publisher.hub.commit_count(REPO) == commits


async def test_a_started_by_in_the_body_is_refused(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    response = await client.post(
        "/api/v1/publishes",
        json={"version_id": "v", "build_id": "b", "repo_id": "a/b", "started_by": "someone"},
    )
    assert response.status_code == 422


def _who_agent() -> publish_service.Who:
    return publish_service.Who("agent:dataworks-mcp", "agent")


async def test_the_service_refuses_an_agent_publish_without_an_approval(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """P-06: the gate is in the service, so 009's worker cannot reach the Hub around REST."""
    version_id, build = await _ready(client, driver, publisher, data_dir)
    req = publish_service.PublishRequest(version_id, build["id"], REPO, "private", "")
    with sync_session_factory()() as db, pytest.raises(AppError) as info:
        publish_service.request_publish(db, req, publish_service.OperatorOrigin(), _who_agent())
    assert info.value.code == "approval_mismatch" and info.value.status_code == 403


def _approval(db, *, status: str, payload: dict, expires_in_h: float = 24) -> Approval:
    row = Approval(
        id=f"apr_{utc_now().timestamp():.0f}{len(payload)}{status[:3]}",
        action="hub_push",
        target="test",
        summary="test",
        payload=payload,
        request_digest="0" * 64,
        requested_by="agent:dataworks-mcp",
        status=status,
        expires_at=utc_now() + timedelta(hours=expires_in_h),
        decided_by="Test Operator" if status != "pending" else None,
        decided_at=utc_now() if status != "pending" else None,
    )
    db.add(row)
    db.commit()
    return row


async def test_ec15_an_approval_for_a_different_visibility_no_longer_matches(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    private = publish_service.PublishRequest(version_id, build["id"], REPO, "private", "")
    public = publish_service.PublishRequest(version_id, build["id"], REPO, "public", "")
    with sync_session_factory()() as db:
        digest = publish_service.request_digest(db, private)
        approval = _approval(db, status="executing", payload={"publish_digest": digest})
        with pytest.raises(AppError) as info:
            publish_service.request_publish(
                db, public, publish_service.PublishApproval(approval.id), _who_agent()
            )
        assert info.value.code == "approval_mismatch"
        created = publish_service.request_publish(
            db, private, publish_service.PublishApproval(approval.id), _who_agent()
        )
        assert created.publish.approval_id == approval.id
        with pytest.raises(AppError) as again:  # consumed once
            publish_service.request_publish(
                db, private, publish_service.PublishApproval(approval.id), _who_agent()
            )
        assert again.value.code in ("approval_mismatch", "repo_busy")


async def test_ec16_a_pending_or_expired_approval_never_runs(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    version_id, build = await _ready(client, driver, publisher, data_dir)
    response = await publish(client, publisher, version_id, build["id"], headers=AGENT)
    approval_id = response.json()["approval_id"]
    with sync_session_factory()() as db:
        db.execute(
            update(Approval)
            .where(Approval.id == approval_id)
            .values(expires_at=utc_now() - timedelta(minutes=1))
        )
        db.commit()
        req = publish_service.PublishRequest(
            version_id, build["id"], REPO, "private", "# Humor\n\nShort texts."
        )
        with pytest.raises(AppError) as info:
            publish_service.request_publish(
                db, req, publish_service.PublishApproval(approval_id), _who_agent()
            )
        assert info.value.code == "approval_mismatch"
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 409
    publisher.run_publish_tasks()
    assert REPO not in publisher.hub.repos


async def test_one_send_approval_covers_two_role_publishes(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """FR-008.66 / P-06: 009's send lists every publish digest; each is accepted once."""
    version_id, build = await _ready(client, driver, publisher, data_dir)
    train = publish_service.PublishRequest(
        version_id, build["id"], "mistudio/role-train", "private", ""
    )
    test = publish_service.PublishRequest(
        version_id, build["id"], "mistudio/role-test", "private", ""
    )
    other = publish_service.PublishRequest(
        version_id, build["id"], "mistudio/role-other", "private", ""
    )
    with sync_session_factory()() as db:
        _send_rows(db, "send_1", "send_2")
        digests = [publish_service.request_digest(db, r) for r in (train, test)]
        approval = _approval(
            db, status="executed", payload={"send_id": "send_1", "publish_digests": digests}
        )
        auth = publish_service.SendApproval(approval.id, "send_1")
        for req in (train, test):
            created = publish_service.request_publish(db, req, auth, _who_agent())
            assert created.publish.send_id == "send_1"
        with pytest.raises(AppError) as info:
            publish_service.request_publish(db, other, auth, _who_agent())
        assert info.value.code == "approval_mismatch"
        with pytest.raises(AppError):
            publish_service.request_publish(
                db, train, publish_service.SendApproval(approval.id, "send_2"), _who_agent()
            )
        dispatch_queued(db)
    publisher.run_publish_tasks()
    assert {"mistudio/role-train", "mistudio/role-test"} <= set(publisher.hub.repos)
    with sync_session_factory()() as db:
        manifests = [
            publish_service.published_manifest_for(db, p.id)
            for p in db.execute(select(Publish)).scalars()
        ]
        assert all(m is not None and m["publication"]["state"] == "published" for m in manifests)


async def test_an_approval_is_consumed_once_even_after_its_publish_finished(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """Control C31 survived: the repository lock hid a missing consumption check. With the first
    publish finished, only consumption can refuse the replay."""
    version_id, build = await _ready(client, driver, publisher, data_dir)
    req = publish_service.PublishRequest(version_id, build["id"], REPO, "private", "")
    with sync_session_factory()() as db:
        approval = _approval(
            db,
            status="executing",
            payload={"publish_digest": publish_service.request_digest(db, req)},
        )
        publish_service.request_publish(
            db, req, publish_service.PublishApproval(approval.id), _who_agent()
        )
        dispatch_queued(db)
    publisher.run_publish_tasks()
    with sync_session_factory()() as db:
        assert publish_service.active_publish(db, REPO) is None
        with pytest.raises(AppError) as info:
            publish_service.request_publish(
                db, req, publish_service.PublishApproval(approval.id), _who_agent()
            )
        assert info.value.code == "approval_mismatch"
        assert "already been used" in info.value.message


async def test_a_send_approval_belongs_to_its_own_send(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    """Control C37 survived: the wrong-send case was only tried on an already-consumed digest."""
    version_id, build = await _ready(client, driver, publisher, data_dir)
    req = publish_service.PublishRequest(version_id, build["id"], "mistudio/role-x", "private", "")
    with sync_session_factory()() as db:
        digest = publish_service.request_digest(db, req)
        approval = _approval(
            db, status="executed", payload={"send_id": "send_1", "publish_digests": [digest]}
        )
        with pytest.raises(AppError) as info:
            publish_service.request_publish(
                db, req, publish_service.SendApproval(approval.id, "send_2"), _who_agent()
            )
        assert info.value.code == "approval_mismatch"
        assert "another send" in info.value.message


def _send_rows(db: Any, *send_ids: str) -> None:
    """009's sends the approvals name: ``dw_publishes.send_id`` is a foreign key since 009."""
    from src.core.ids import new_id
    from src.models import DetectorSend, DetectorSet
    from tests.support import db_factories

    s = DetectorSet(
        id=new_id("dts"), name=f"s-{new_id('x')[-8:]}", created_by="T", created_by_origin="operator"
    )
    db.add(s)
    db.flush()
    for send_id in send_ids:
        job = db_factories.job(db)
        db.add(
            DetectorSend(
                id=send_id,
                set_id=s.id,
                job_id=job.id,
                job_ids=[job.id],
                snapshot={},
                snapshot_sha256="0" * 64,
                checks=[],
                plan={},
                mistudio_base_url="http://m",
                approval_digest="0" * 64,
                state="queued",
                started_by="T",
                started_by_origin="operator",
            )
        )
    db.commit()
