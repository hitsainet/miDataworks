"""An agent's target change waits for the operator (006 FTASKS 7.9 – 7.12; FR-006.43, S3-08).

The verdict-change fixture puts records on both sides of the move: two labelers whose AUROC CI
lower bound sits BETWEEN the old and the new target (their verdict flips) and one that does not.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select, update

from src.core.agent_origin import Who
from src.core.clock import utc_now
from src.core.database import async_session_factory, sync_session_factory
from src.core.errors import AppError
from src.core.ids import new_id
from src.models.approval import Approval
from src.models.calibration import (
    CalibrationRecord,
    CalibrationSet,
    CalibrationTarget,
    CalibrationVerdict,
)
from src.services.approval_service import expire_due
from src.services.calibration import target_service
from src.services.calibration.mapping import question_hash
from tests.support import db_factories
from tests.support.calibration_fixtures import QUESTION, make_run

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


def target_rows() -> list[CalibrationTarget]:
    with sync_session_factory()() as s:
        rows = list(
            s.execute(select(CalibrationTarget).order_by(CalibrationTarget.created_at)).scalars()
        )
        for r in rows:
            s.expunge(r)
        return rows


def approval(approval_id: str) -> Approval:
    with sync_session_factory()() as s:
        row = s.execute(select(Approval).where(Approval.id == approval_id)).scalar_one()
        s.expunge(row)
        return row


def seed_record(version_id: str, ci_low: float, model_id: str) -> tuple[str, str]:
    """A stored record (no job run needed) with an AUROC lower bound of ``ci_low``."""
    run = make_run(version_id, {}, model_id=model_id)
    with sync_session_factory()() as s:
        cal = s.execute(
            select(CalibrationSet).where(CalibrationSet.version_id == version_id)
        ).scalar_one_or_none()
        if cal is None:
            cal = CalibrationSet(
                id=new_id("cs"),
                version_id=version_id,
                question=QUESTION,
                question_hash=question_hash(QUESTION),
                label_set=["humorous", "not_humorous"],
                source_kind="imported",
                mapping={"schema": "dw.calibration-mapping/v1"},
                mapping_hash="d" * 64,
                licence_class="private_only",
                counts={},
                provenance={},
                created_by="Test Operator",
                created_by_origin="operator",
            )
            s.add(cal)
            s.flush()
        job = db_factories.job(s, kind="calibration_compute")
        record = CalibrationRecord(
            id=new_id("cr"),
            calibration_set_id=cal.id,
            label_run_id=run.id,
            labeler_identity=run.labeler_identity,
            labeler_identity_hash=run.labeler_identity_hash,
            labeler_fingerprint=run.labeler_fingerprint,
            score_kind="probability",
            metrics={
                "auroc": {
                    "value": ci_low + 0.02,
                    "ci_low": ci_low,
                    "ci_high": ci_low + 0.04,
                    "n": 500,
                    "resamples": 2000,
                    "dropped": 0,
                },
                "ceiling": None,
                "comparison_auroc": None,
            },
            metrics_sha256="e" * 64,
            settings={},
            warnings=[],
            job_id=job.id,
            created_by="Test Operator",
            created_by_origin="operator",
        )
        s.add(record)
        s.flush()
        s.add(
            CalibrationVerdict(record_id=record.id, verdict="passes", rule="default_c3", numbers={})
        )
        s.commit()
        return record.id, run.labeler_identity_hash


@pytest.fixture
def labelers(client: httpx.AsyncClient, data_dir: Any) -> dict[str, tuple[str, str]]:
    with sync_session_factory()() as s:
        version = db_factories.version(s)
        s.commit()
        version_id = version.id
    return {
        "flips_a": seed_record(version_id, 0.72, "model-a"),
        "flips_b": seed_record(version_id, 0.74, "model-b"),
        "stays": seed_record(version_id, 0.90, "model-c"),
    }


async def ask(client: httpx.AsyncClient, target: float) -> httpx.Response:
    return await client.put(
        "/api/v1/calibration-targets", json={"question": QUESTION, "target": target}, headers=AGENT
    )


# --- 7.10 approval required -------------------------------------------------------------------


async def test_an_agent_write_waits_and_writes_nothing(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    response = await ask(client, 0.8)
    assert response.status_code == 202, response.text
    assert response.json()["action"] == "gate_target_write"
    assert target_rows() == []
    current = (
        await client.get("/api/v1/calibration-targets", params={"question": QUESTION})
    ).json()
    assert current["current"] is None


async def test_the_service_refuses_an_agent_write_without_an_approval(clean_db: None) -> None:
    async with async_session_factory()() as db:
        with pytest.raises(AppError) as info:
            await target_service.set_target(
                db, QUESTION, 0.8, Who("agent:dataworks-mcp", "agent"), approval=None
            )
    assert info.value.code == "APPROVAL_REQUIRED" and info.value.status_code == 403
    assert target_rows() == []


async def test_reject_and_expiry_write_nothing(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    a = (await ask(client, 0.8)).json()["approval_id"]
    assert (
        await client.post(f"/api/v1/approvals/{a}/reject", json={"reason": "no"})
    ).status_code == 200
    b = (await ask(client, 0.81)).json()["approval_id"]
    with sync_session_factory()() as s:
        s.execute(
            update(Approval)
            .where(Approval.id == b)
            .values(expires_at=utc_now() - timedelta(seconds=1))
        )
        s.commit()
        assert expire_due(s) == 1
    late = await client.post(f"/api/v1/approvals/{b}/approve")
    assert late.status_code == 409
    assert target_rows() == []


async def test_an_operator_write_is_not_gated(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    r = await client.put("/api/v1/calibration-targets", json={"question": QUESTION, "target": 0.8})
    assert r.status_code == 201
    assert len(target_rows()) == 1


# --- 7.11 the approval payload ----------------------------------------------------------------


async def test_the_card_names_exactly_the_verdicts_that_would_change(
    client: httpx.AsyncClient, operator_name: str, labelers: dict[str, tuple[str, str]]
) -> None:
    approval_id = (await ask(client, 0.80)).json()["approval_id"]
    facts = approval(approval_id).payload["facts"]
    assert facts["question"] == QUESTION
    assert facts["old"] is None and facts["old_display"] == "default (C3)"
    assert facts["new"] == 0.80
    changed = {c["record_id"]: c for c in facts["verdict_changes"]}
    assert set(changed) == {labelers["flips_a"][0], labelers["flips_b"][0]}
    for c in changed.values():
        assert c["old_verdict"] == "passes" and c["new_verdict"] == "fails"
        assert len(c["version_ids"]) == 1
        assert c["labeler"]["identity_hash"]


async def test_a_target_that_changes_no_verdict_yields_an_empty_list(
    client: httpx.AsyncClient, operator_name: str, labelers: dict[str, tuple[str, str]]
) -> None:
    approval_id = (await ask(client, 0.70)).json()["approval_id"]
    facts = approval(approval_id).payload["facts"]
    assert facts["verdict_changes"] == []
    assert facts["verdict_changes_note"] == "No verdict would change."


async def test_an_operator_change_in_between_makes_the_approval_stale(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    approval_id = (await ask(client, 0.80)).json()["approval_id"]
    await client.put("/api/v1/calibration-targets", json={"question": QUESTION, "target": 0.6})
    result = (await client.post(f"/api/v1/approvals/{approval_id}/approve")).json()
    assert result["status"] == "failed" and result["error"]["code"] == "APPROVAL_STALE"
    assert [t.target for t in target_rows()] == [0.6]


# --- 7.12 the audit row -----------------------------------------------------------------------


async def test_approval_writes_one_row_with_both_names(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    approval_id = (await ask(client, 0.82)).json()["approval_id"]
    result = (await client.post(f"/api/v1/approvals/{approval_id}/approve")).json()
    assert result["status"] == "executed", result
    rows = target_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.set_by == "agent:dataworks-mcp" and row.set_by_origin == "agent"
    assert row.approval_id == approval_id and row.approved_by == operator_name
    history = (
        await client.get("/api/v1/calibration-targets", params={"question": QUESTION})
    ).json()
    assert history["current"]["set_by"] == "agent:dataworks-mcp"
    assert history["current"]["approved_by"] == operator_name
    with sync_session_factory()() as s:
        assert s.execute(select(func.count()).select_from(CalibrationTarget)).scalar_one() == 1
