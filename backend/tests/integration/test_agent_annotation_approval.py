"""Every agent annotation waits for ``source_annotate`` (001 FTASKS 10.8–10.10; FR-001.38, S3-01).

An annotation can change a source's licence class and so unlock a public push (008 C-1). An agent's
request of any kind answers 202 and writes nothing; the stored approval carries the card facts and
the ID of the annotation the agent saw (``expected_current_id``, inside the digest). Approving writes
exactly one row recording the agent, its origin, the approval and the deciding operator. An operator
annotation landing in between makes the approval fail ``APPROVAL_STALE`` and write nothing.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from src.core.agent_origin import Who
from src.core.canonical_json import canonical_sha256
from src.core.clock import utc_now
from src.core.database import async_session_factory, sync_session_factory
from src.core.errors import AppError
from src.models.approval import Approval
from src.models.source import SourceAnnotation
from src.services.approval_service import expire_due
from src.services.sources import source_service
from tests.support import db_factories

AGENT = {"X-Dataworks-Agent": "agent:mcp"}
DETECTION = {
    "detector_version": "dw.detect/v1",
    "trl_type": "none",
    "chat_format": "plain_text",
    "text_columns": ["text"],
    "label_columns": ["humor"],
    "suggested_target": "detector",
    "reasons": [],
}
BODIES: dict[str, dict[str, Any]] = {
    "terms": {"kind": "terms", "redistribution": "permits", "reason": "terms read on the Hub"},
    "licence": {"kind": "licence", "redistribution": "private_only", "reason": "card says NC"},
    "detection_override": {
        "kind": "detection_override",
        "value": {"text_columns": ["text"], "label_columns": ["humor"]},
        "reason": "humor is the label",
    },
}


def make_source(state: str = "ready") -> str:
    with sync_session_factory()() as s:
        row = db_factories.source(s, state=state, detection=DETECTION)
        s.commit()
        return row.id


def annotation_count(source_id: str) -> int:
    with sync_session_factory()() as s:
        return int(
            s.execute(
                select(func.count())
                .select_from(SourceAnnotation)
                .where(SourceAnnotation.source_id == source_id)
            ).scalar_one()
        )


def annotations(source_id: str) -> list[SourceAnnotation]:
    with sync_session_factory()() as s:
        rows = list(
            s.execute(
                select(SourceAnnotation)
                .where(SourceAnnotation.source_id == source_id)
                .order_by(SourceAnnotation.created_at)
            ).scalars()
        )
        for r in rows:
            s.expunge(r)
        return rows


def approval(approval_id: str) -> Approval:
    with sync_session_factory()() as s:
        row = s.execute(select(Approval).where(Approval.id == approval_id)).scalar_one()
        s.expunge(row)
        return row


async def ask(client: httpx.AsyncClient, source_id: str, body: dict[str, Any]) -> httpx.Response:
    return await client.post(f"/api/v1/sources/{source_id}/annotations", json=body, headers=AGENT)


# --------------------------------------------------------------------------------------------
# Refusal (10.9)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(BODIES))
async def test_an_agent_annotation_of_every_kind_waits_and_writes_nothing(
    client: httpx.AsyncClient, operator_name: str, kind: str
) -> None:
    source_id = make_source()
    before = (await client.get(f"/api/v1/sources/{source_id}")).json()
    response = await ask(client, source_id, BODIES[kind])
    assert response.status_code == 202, response.text
    assert response.json()["action"] == "source_annotate"
    assert response.json()["status"] == "pending"
    assert annotation_count(source_id) == 0
    after = (await client.get(f"/api/v1/sources/{source_id}")).json()
    assert after["licence"] == before["licence"]
    assert after["licence"]["terms_status"] == "not recorded"
    assert after["detection"] == before["detection"]


async def test_the_service_refuses_an_agent_annotation_without_an_approval(
    clean_db: None,
) -> None:
    source_id = make_source()
    async with async_session_factory()() as db:
        with pytest.raises(AppError) as info:
            await source_service.annotate(
                db, source_id, BODIES["terms"], Who("agent:mcp", "agent"), approval=None
            )
    assert info.value.code == "APPROVAL_REQUIRED" and info.value.status_code == 403
    assert annotation_count(source_id) == 0


async def test_reject_writes_nothing(client: httpx.AsyncClient, operator_name: str) -> None:
    source_id = make_source()
    approval_id = (await ask(client, source_id, BODIES["licence"])).json()["approval_id"]
    rejected = await client.post(
        f"/api/v1/approvals/{approval_id}/reject", json={"reason": "not what the card says"}
    )
    assert rejected.status_code == 200
    assert approval(approval_id).status == "rejected" and annotation_count(source_id) == 0


async def test_expiry_writes_nothing(client: httpx.AsyncClient, operator_name: str) -> None:
    source_id = make_source()
    approval_id = (await ask(client, source_id, BODIES["terms"])).json()["approval_id"]
    with sync_session_factory()() as s:
        s.execute(
            update(Approval)
            .where(Approval.id == approval_id)
            .values(expires_at=utc_now() - timedelta(seconds=1))
        )
        s.commit()
        assert expire_due(s) == 1
    late = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert late.status_code == 409
    assert annotation_count(source_id) == 0


async def test_an_operator_annotation_is_not_gated(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    source_id = make_source()
    response = await client.post(f"/api/v1/sources/{source_id}/annotations", json=BODIES["terms"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created_by"] == "Test Operator" and body["created_by_origin"] == "operator"
    assert body["approval_id"] is None
    with sync_session_factory()() as s:
        assert s.execute(select(Approval)).first() is None


async def test_a_request_that_cannot_land_is_refused_before_an_approval_is_stored(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    deleted = make_source(state="deleted")
    gone = await ask(client, deleted, BODIES["terms"])
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "source_deleted"
    live = make_source()
    bare = await ask(client, live, {"kind": "licence", "reason": "no class given"})
    assert bare.status_code == 422
    assert bare.json()["error"]["code"] == "redistribution_required"
    bad = await ask(client, live, {"kind": "detection_override", "value": {"x": 1}, "reason": "?"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "override_invalid"
    with sync_session_factory()() as s:
        assert s.execute(select(Approval)).first() is None


# --------------------------------------------------------------------------------------------
# The approval payload, execution and staleness (10.10)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(BODIES))
async def test_the_card_facts_describe_the_source_the_prior_and_the_proposal(
    client: httpx.AsyncClient, operator_name: str, kind: str
) -> None:
    source_id = make_source()
    approval_id = (await ask(client, source_id, BODIES[kind])).json()["approval_id"]
    row = approval(approval_id)
    facts = row.payload["facts"]
    assert facts["source_id"] == source_id
    assert facts["display_name"] == "org/data" and facts["repo_id"] == "org/data"
    assert facts["content_hash"] is None
    assert facts["current"] is None and row.payload["expected_current_id"] is None
    body = BODIES[kind]
    assert facts["proposed"] == {
        "kind": kind,
        "redistribution": body.get("redistribution"),
        "value": body.get("value", {}),
        "reason": body["reason"],
    }
    if kind in ("terms", "licence"):
        assert facts["warning"] == "An annotation can unlock a public push."
        assert "An annotation can unlock a public push." in row.summary
    else:
        assert "warning" not in facts
    # The facts and the bound state are INSIDE the digest, so tampering with either is caught.
    assert canonical_sha256(row.payload) == row.request_digest


async def test_the_card_names_the_current_annotation_and_binds_to_it(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    source_id = make_source()
    prior = await client.post(
        f"/api/v1/sources/{source_id}/annotations",
        json={"kind": "terms", "redistribution": "private_only", "reason": "first reading"},
    )
    prior_id = prior.json()["id"]
    approval_id = (await ask(client, source_id, BODIES["terms"])).json()["approval_id"]
    row = approval(approval_id)
    assert row.payload["facts"]["current"]["id"] == prior_id
    assert row.payload["facts"]["current"]["redistribution"] == "private_only"
    assert row.payload["expected_current_id"] == prior_id


async def test_an_agent_cannot_choose_the_state_it_binds_to(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    source_id = make_source()
    response = await client.post(
        f"/api/v1/sources/{source_id}/annotations?expected_current_id=forged",
        json=BODIES["terms"],
        headers=AGENT,
    )
    assert approval(response.json()["approval_id"]).payload["expected_current_id"] is None


@pytest.mark.parametrize("kind", sorted(BODIES))
async def test_approve_writes_exactly_one_row_recording_agent_and_operator(
    client: httpx.AsyncClient, operator_name: str, kind: str
) -> None:
    source_id = make_source()
    approval_id = (await ask(client, source_id, BODIES[kind])).json()["approval_id"]
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.status_code == 200 and approved.json()["status"] == "executed", approved.text
    [row] = annotations(source_id)
    assert row.kind == kind
    assert row.created_by == "agent:mcp" and row.created_by_origin == "agent"
    assert row.approval_id == approval_id and row.approved_by == "Test Operator"
    again = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert again.status_code == 409 and annotation_count(source_id) == 1


async def test_an_operator_annotation_in_between_makes_the_approval_stale(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    source_id = make_source()
    approval_id = (await ask(client, source_id, BODIES["terms"])).json()["approval_id"]
    landed = await client.post(
        f"/api/v1/sources/{source_id}/annotations",
        json={"kind": "terms", "redistribution": "forbids", "reason": "terms forbid it"},
    )
    assert landed.status_code == 201
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.json()["status"] == "failed"
    assert approved.json()["error"]["code"] == "APPROVAL_STALE"
    assert [a.created_by_origin for a in annotations(source_id)] == ["operator"]
    view = (await client.get(f"/api/v1/sources/{source_id}")).json()["licence"]
    assert view["terms_status"] == "forbids"


async def test_another_kinds_annotation_in_between_does_not_make_it_stale(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    source_id = make_source()
    approval_id = (await ask(client, source_id, BODIES["terms"])).json()["approval_id"]
    await client.post(f"/api/v1/sources/{source_id}/annotations", json=BODIES["licence"])
    approved = await client.post(f"/api/v1/approvals/{approval_id}/approve")
    assert approved.json()["status"] == "executed", approved.text
    assert annotation_count(source_id) == 2
