"""Annotations and overrides (001 FTASKS 9.1–9.3; FR-001.20, FR-001.28, FR-001.29).

Annotations append; the Hub's raw value is unchanged; the terms status follows the latest value;
an override changes the detection defaults and keeps the original suggestion; a deleted source
cannot be annotated; redistribution is required for terms and licence; an agent-origin call
without an approval reference is refused by the service itself (S3-01, behind the route's gate).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.agent_origin import Who
from src.core.database import async_session_factory, dispose_engines, sync_session_factory
from src.core.errors import AppError
from src.models.source import SourceAnnotation
from src.services.sources import source_service
from tests.support import db_factories

OPERATOR = Who("Test Operator", "operator")
DETECTION = {
    "detector_version": "dw.detect/v1",
    "trl_type": "none",
    "trl_format": None,
    "chat_format": "plain_text",
    "text_columns": ["text"],
    "label_columns": ["humor"],
    "suggested_target": "detector",
    "reasons": [],
}


@pytest.fixture
async def db(clean_db: None) -> AsyncIterator[AsyncSession]:
    async with async_session_factory()() as session:
        yield session
    await dispose_engines()


def make(state: str = "ready", detection: dict[str, Any] | None = None) -> str:
    with sync_session_factory()() as s:
        row = db_factories.source(s, state=state, detection=detection)
        s.commit()
        return row.id


def terms(redistribution: str, reason: str = "read the terms") -> dict[str, Any]:
    return {"kind": "terms", "redistribution": redistribution, "value": {}, "reason": reason}


async def test_annotations_append_and_the_terms_status_follows_the_latest(
    db: AsyncSession,
) -> None:
    source_id = make()
    await source_service.annotate(db, source_id, terms("private_only"), OPERATOR, approval=None)
    await source_service.annotate(db, source_id, terms("permits"), OPERATOR, approval=None)
    source = await source_service.get_source_row(db, source_id)
    view = await source_service.current_licence_view(db, source)
    assert view["terms_status"] == "permits" and view["redistribution"] == "permits"
    assert [a["redistribution"] for a in view["history"]] == ["private_only", "permits"]
    assert (view["raw"], view["display"], view["origin"]) == ("cc-by-2.0", "cc-by-2.0", "card_data")
    assert view["history"][0]["created_by"] == "Test Operator"


async def test_a_licence_annotation_sets_the_asserted_class_but_not_the_terms_status(
    db: AsyncSession,
) -> None:
    source_id = make()
    body = {"kind": "licence", "redistribution": "forbids", "value": {"text": "NC"}, "reason": "x"}
    await source_service.annotate(db, source_id, body, OPERATOR, approval=None)
    view = await source_service.current_licence_view(
        db, await source_service.get_source_row(db, source_id)
    )
    assert view["redistribution"] == "forbids" and view["terms_status"] == "not recorded"
    assert view["raw"] == "cc-by-2.0", "the Hub's raw value is never rewritten"


@pytest.mark.parametrize("kind", ["terms", "licence"])
async def test_redistribution_is_required_for_terms_and_licence(
    db: AsyncSession, kind: str
) -> None:
    with pytest.raises(AppError) as info:
        await source_service.annotate(
            db, make(), {"kind": kind, "value": {}, "reason": "r"}, OPERATOR, approval=None
        )
    assert info.value.code == "redistribution_required"


async def test_an_override_changes_the_defaults_and_keeps_the_suggestion(db: AsyncSession) -> None:
    source_id = make(detection=DETECTION)
    body = {
        "kind": "detection_override",
        "value": {"text_columns": ["headline"], "suggested_target": "sft"},
        "reason": "the headline is the text",
    }
    await source_service.annotate(db, source_id, body, OPERATOR, approval=None)
    source = await source_service.get_source_row(db, source_id)
    effective = await source_service.effective_detection(db, source)
    assert effective is not None
    assert effective["text_columns"] == ["headline"] and effective["suggested_target"] == "sft"
    assert effective["overridden"] is True and effective["suggested"] == DETECTION
    assert source.detection == DETECTION, "the stored detection is never rewritten"


@pytest.mark.parametrize("value", [{}, {"colour": "blue"}, {"text_columns": "text"}])
async def test_an_override_names_only_detection_outputs(
    db: AsyncSession, value: dict[str, Any]
) -> None:
    body = {"kind": "detection_override", "value": value, "reason": "r"}
    with pytest.raises(AppError) as info:
        await source_service.annotate(db, make(detection=DETECTION), body, OPERATOR, approval=None)
    assert info.value.code == "override_invalid"


async def test_a_deleted_source_cannot_be_annotated(db: AsyncSession) -> None:
    source_id = make()
    await source_service.delete(db, source_id, "gone", OPERATOR)
    with pytest.raises(AppError) as info:
        await source_service.annotate(db, source_id, terms("permits"), OPERATOR, approval=None)
    assert info.value.code == "source_deleted"


async def test_an_agent_annotation_without_an_approval_is_refused_by_the_service(
    db: AsyncSession,
) -> None:
    source_id = make()
    with pytest.raises(AppError) as info:
        await source_service.annotate(
            db, source_id, terms("permits"), Who("agent:mcp", "agent"), approval=None
        )
    assert info.value.code == "APPROVAL_REQUIRED"
    count = await db.scalar(select(func.count()).select_from(SourceAnnotation))
    assert count == 0


async def test_annotations_are_append_only_in_the_database(db: AsyncSession) -> None:
    source_id = make()
    row = await source_service.annotate(db, source_id, terms("permits"), OPERATOR, approval=None)
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    with pytest.raises(DBAPIError):
        await db.execute(
            text("UPDATE dw_source_annotations SET reason = 'x' WHERE id = :i"), {"i": row.id}
        )
    await db.rollback()
