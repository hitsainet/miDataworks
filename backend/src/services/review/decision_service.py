"""Recording a review decision (FR-006.23, FR-006.42; FTDD 006 section 2.3).

Order, in one request: load item and queue -> ``decision_rules.allowed`` (P-10, T-29) ->
``decision_rules.validate`` -> insert ONE ``dw_review_decisions`` row -> if the queue is an audit,
re-evaluate completion in the SAME transaction -> commit -> emit ``review:decision`` from the API
process. A refused decision writes nothing. Logs carry the decision ID, origin and kind, never the
row text or the reason.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.errors import AppError, ConflictError, NotFoundError
from ...core.ids import new_id
from ...models.review import ReviewDecision, ReviewItem, ReviewQueue
from ...schemas.review import DecisionIn
from . import audit_service
from .decision_rules import DecisionRefused, allowed, validate

logger = logging.getLogger(__name__)


def refused(exc: DecisionRefused) -> AppError:
    return AppError(exc.message, code=exc.code, status_code=exc.status, details=exc.details)


async def load(db: AsyncSession, item_id: str) -> tuple[ReviewItem, ReviewQueue]:
    item = await db.get(ReviewItem, item_id)
    if item is None:
        raise NotFoundError(f"No review item {item_id}.", code="ITEM_NOT_FOUND")
    queue = await db.get(ReviewQueue, item.queue_id)
    assert queue is not None
    return item, queue


async def record(db: AsyncSession, item_id: str, body: DecisionIn, who: Who) -> ReviewDecision:
    item, queue = await load(db, item_id)
    if queue.state != "open":
        raise ConflictError(
            "This queue is closed; decisions are recorded on open queues only.",
            code="QUEUE_CLOSED",
            details={"queue_id": queue.id},
        )
    try:
        allowed(body.decision, who.origin, queue.kind)
        reason = validate(
            body.decision,
            override_label=body.override_label,
            reason=body.reason,
            label_set=list(queue.label_set),
            queue_kind=queue.kind,
            model_output_visible=queue.show_model_output,
        )
    except DecisionRefused as exc:
        raise refused(exc) from None
    decision = ReviewDecision(
        id=new_id("rd"),
        item_id=item.id,
        queue_id=queue.id,
        row_key=item.row_key,
        question_hash=queue.question_hash,
        version_id=queue.version_id,
        decision=body.decision,
        override_label=body.override_label,
        reason=reason,
        model_output_visible=queue.show_model_output,
        decided_by=who.who,
        decided_by_origin=who.origin,
    )
    db.add(decision)
    await db.flush()
    if queue.kind == "audit":
        await audit_service.evaluate_for_queue(db, queue.id)
    await db.commit()
    await db.refresh(decision)
    logger.info(
        "review.decision.recorded id=%s origin=%s kind=%s queue=%s",
        decision.id,
        decision.decided_by_origin,
        decision.decision,
        queue.id,
    )
    return decision


async def history(db: AsyncSession, item_id: str) -> list[ReviewDecision]:
    item, _ = await load(db, item_id)
    rows = await db.execute(
        select(ReviewDecision)
        .where(ReviewDecision.item_id == item.id)
        .order_by(ReviewDecision.created_at, ReviewDecision.id)
    )
    return list(rows.scalars())
