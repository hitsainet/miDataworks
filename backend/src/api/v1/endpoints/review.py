"""Review routes (FTDD 006 section 5.1): queues, items, decisions, and the miForge API.

- The decision route calls ``decision_rules.allowed`` (inside ``decision_service.record``) before
  anything is written: agents may accept and flag only (P-10), and ``reject`` exists on external
  queues only (T-29). ``tests/unit/review/test_review_reachability.py`` asserts the call by AST.
- ``POST /review-queues/{id}/candidates`` needs the agent-origin header (the calling application);
  ``GET /review-queues/{id}/decisions`` (read-back) does not. Both are exempt from MCP tools: they
  are the application API for miForge (R-03.41), not UI actions (FTDD 006 section 5.6).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....core.websocket import emit_to_room
from ....schemas.review import (
    CalibrationLabelingQueueCreate,
    CandidatesIn,
    CandidatesOut,
    DecisionIn,
    DecisionRead,
    ExternalDecisions,
    ExternalQueueCreate,
    LabelReviewQueueCreate,
    ReviewItemPage,
    ReviewQueueCreate,
    ReviewQueueList,
    ReviewQueueOut,
)
from ....services.review import decision_service, external_service, queue_service
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1", tags=["review"])


def queue_room(queue_id: str) -> str:
    return f"dataworks/review-queues/{queue_id}"


@router.get("/review-queues", response_model=ReviewQueueList)
async def list_review_queues(
    kind: str | None = Query(None, max_length=24),
    state: str | None = Query(None, max_length=16),
    version_id: str | None = Query(None, max_length=64),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> ReviewQueueList:
    items, total = await queue_service.list_queues(
        db, kind=kind, state=state, version_id=version_id, offset=page.offset, limit=page.limit
    )
    return ReviewQueueList(items=items, total=total)


@router.post("/review-queues", response_model=ReviewQueueOut, status_code=201)
async def create_review_queue(
    body: ReviewQueueCreate = Body(...),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> ReviewQueueOut:
    """Create a label-review, calibration-labeling or external queue with all its items."""
    who = await resolve_who(actor, db)
    if isinstance(body, LabelReviewQueueCreate):
        queue = await queue_service.create_label_review(db, body, who)
    elif isinstance(body, CalibrationLabelingQueueCreate):
        queue = await queue_service.create_calibration_labeling(db, body, who)
    else:
        assert isinstance(body, ExternalQueueCreate)
        queue = await queue_service.create_external(
            db, body, who, external_service.app_of(actor.agent)
        )
    counts = await queue_service.progress(db, [queue.id])
    return queue_service.queue_out(queue, counts[queue.id])


@router.get("/review-queues/{queue_id}", response_model=ReviewQueueOut)
async def get_review_queue(queue_id: str, db: AsyncSession = Depends(get_db)) -> ReviewQueueOut:
    queue = await queue_service.get_queue(db, queue_id)
    counts = await queue_service.progress(db, [queue.id])
    return queue_service.queue_out(queue, counts[queue.id])


@router.get("/review-queues/{queue_id}/items", response_model=ReviewItemPage)
async def get_review_items(
    queue_id: str,
    decided: bool | None = Query(None),
    stratum: str | None = Query(None, max_length=512),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> ReviewItemPage:
    """A page of items (``limit`` at most 200), with row text and the latest decision."""
    items, total = await queue_service.items_page(
        db, queue_id, decided=decided, stratum=stratum, offset=page.offset, limit=page.limit
    )
    return ReviewItemPage(items=items, total=total)


@router.post("/review-items/{item_id}/decisions", response_model=DecisionRead, status_code=201)
async def record_review_decision(
    item_id: str,
    body: DecisionIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> DecisionRead:
    """Record accept, override, flag or (external queues only) reject. Agents: accept and flag."""
    who = await resolve_who(actor, db)
    decision = await decision_service.record(db, item_id, body, who)
    out = queue_service.decision_read(decision)
    await emit_to_room(
        queue_room(decision.queue_id), "review:decision", out.model_dump(mode="json")
    )
    return out


@router.get("/review-items/{item_id}/decisions", response_model=list[DecisionRead])
async def get_review_decisions(
    item_id: str, db: AsyncSession = Depends(get_db)
) -> list[DecisionRead]:
    """The item's decision history, oldest first (decisions are never edited)."""
    return [queue_service.decision_read(d) for d in await decision_service.history(db, item_id)]


@router.post("/review-queues/{queue_id}/candidates", response_model=CandidatesOut, status_code=201)
async def post_review_candidates(
    queue_id: str,
    body: CandidatesIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CandidatesOut:
    """miForge's candidates (batch at most 500); a repeated external ID returns its item."""
    items = await external_service.post_candidates(db, queue_id, body.candidates, actor.agent)
    return CandidatesOut(items=items)


@router.get("/review-queues/{queue_id}/decisions", response_model=ExternalDecisions)
async def read_back_review_decisions(
    queue_id: str,
    external_ids: list[str] | None = Query(None, max_length=500),
    since: datetime | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> ExternalDecisions:
    """Decisions on an external queue, by candidate or since a time, with stable IDs."""
    items = await external_service.read_back(db, queue_id, external_ids=external_ids, since=since)
    return ExternalDecisions(items=items)
