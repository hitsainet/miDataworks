"""The miForge review API: candidates in, decisions back (FR-006.30 – FR-006.32; R-03.41;
BRD-02 R-02.36; FTDD 006 section 5.4).

Guarantees:
- candidates are posted only by an application that identifies itself with the agent-origin
  header (``ORIGIN_APP_REQUIRED`` otherwise); the queue's ``origin_app`` must match it;
- a batch holds at most 500 candidates and each payload at most ``REVIEW_CANDIDATE_MAX_BYTES``
  (``PAYLOAD_TOO_LARGE``, 413) — external text is untrusted, stored as data, rendered as text;
- a repeated external ID returns the existing item (idempotent, no duplicate);
- decisions read back carry stable decision IDs; read-back needs no header.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.canonical_json import canonical_json
from ...core.config import get_settings
from ...core.errors import AppError, ForbiddenError
from ...core.ids import new_id
from ...models.review import ReviewDecision, ReviewItem, ReviewQueue
from ...schemas.review import Candidate, CandidateAccepted, ExternalDecision
from ..calibration.constants import CANDIDATE_BATCH_MAX
from .queue_service import get_queue


def app_of(agent: str | None) -> str | None:
    return agent.removeprefix("agent:") if agent else None


async def _external_queue(db: AsyncSession, queue_id: str) -> ReviewQueue:
    queue = await get_queue(db, queue_id)
    if queue.kind != "external":
        raise AppError(
            "Candidates go to an external queue only.",
            code="QUEUE_KIND_INVALID",
            status_code=422,
            details={"kind": queue.kind},
        )
    return queue


async def post_candidates(
    db: AsyncSession, queue_id: str, candidates: Sequence[Candidate], agent: str | None
) -> list[CandidateAccepted]:
    app = app_of(agent)
    if app is None:
        raise AppError(
            "External candidates come from an application, which identifies itself with "
            "X-Dataworks-Agent (miForge sends agent:miforge).",
            code="ORIGIN_APP_REQUIRED",
            status_code=400,
        )
    if len(candidates) > CANDIDATE_BATCH_MAX:
        raise AppError(
            f"A batch holds at most {CANDIDATE_BATCH_MAX} candidates; split it.",
            code="PAYLOAD_TOO_LARGE",
            status_code=413,
            details={"count": len(candidates), "max": CANDIDATE_BATCH_MAX},
        )
    limit = get_settings().review_candidate_max_bytes
    for c in candidates:
        size = len(canonical_json(c.model_dump(mode="json")))
        if size > limit:
            raise AppError(
                f"Candidate {c.external_id!r} is {size} bytes; the limit is {limit}.",
                code="PAYLOAD_TOO_LARGE",
                status_code=413,
                details={"external_id": c.external_id, "bytes": size, "max": limit},
            )
    queue = await _external_queue(db, queue_id)
    if queue.origin_app != app:
        raise ForbiddenError(
            f"This queue belongs to {queue.origin_app}; {app} cannot post to it.",
            code="ORIGIN_APP_MISMATCH",
        )
    existing = {
        i.external_id: i
        for i in (
            await db.execute(
                select(ReviewItem).where(
                    ReviewItem.queue_id == queue.id,
                    ReviewItem.external_id.in_([c.external_id for c in candidates]),
                )
            )
        ).scalars()
    }
    position = int(
        (
            await db.execute(
                select(func.coalesce(func.max(ReviewItem.position), -1)).where(
                    ReviewItem.queue_id == queue.id
                )
            )
        ).scalar_one()
    )
    out: list[CandidateAccepted] = []
    for c in candidates:
        found = existing.get(c.external_id)
        if found is not None:
            out.append(
                CandidateAccepted(external_id=c.external_id, item_id=found.id, created=False)
            )
            continue
        position += 1
        item = ReviewItem(
            id=new_id("ri"),
            queue_id=queue.id,
            position=position,
            external_id=c.external_id,
            payload={
                "prompt": c.prompt,
                "completion": c.completion,
                "model_output": c.model_output,
                "provenance": c.provenance,
            },
            model_snapshot=c.model_output,
        )
        db.add(item)
        existing[c.external_id] = item
        out.append(CandidateAccepted(external_id=c.external_id, item_id=item.id, created=True))
    await db.commit()
    return out


async def read_back(
    db: AsyncSession,
    queue_id: str,
    *,
    external_ids: Sequence[str] | None,
    since: datetime | None,
) -> list[ExternalDecision]:
    queue = await _external_queue(db, queue_id)
    query = (
        select(ReviewDecision, ReviewItem.external_id)
        .join(ReviewItem, ReviewItem.id == ReviewDecision.item_id)
        .where(ReviewDecision.queue_id == queue.id)
    )
    if external_ids:
        query = query.where(ReviewItem.external_id.in_(list(external_ids)))
    if since is not None:
        query = query.where(ReviewDecision.created_at > since)
    rows = (await db.execute(query.order_by(ReviewDecision.created_at, ReviewDecision.id))).all()
    return [
        ExternalDecision(
            decision_id=d.id,
            external_id=str(ext),
            item_id=d.item_id,
            decision=d.decision,
            override_label=d.override_label,
            reason=d.reason,
            decided_by=d.decided_by,
            decided_by_origin=d.decided_by_origin,
            created_at=d.created_at,
        )
        for d, ext in rows
    ]
