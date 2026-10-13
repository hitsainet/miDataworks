"""Operator gate targets per question (FR-006.19, FR-006.43; S3-08; FTDD 006 section 5.7).

Guarantees:
- a target row is never updated; the latest per question hash wins and the history is kept;
- an agent-origin write needs an approval reference (``APPROVAL_REQUIRED`` otherwise): a second
  layer behind the route's ``gate_target_write`` gate, so a future caller that skips the decorator
  still cannot write;
- while an approval executes, the target the agent saw (``expected_current_target_id``) must still
  be current, or the write fails ``409 APPROVAL_STALE`` and writes nothing;
- :func:`verdict_changes` re-decides the latest record of every labeler for the question with the
  old and the new target through the SAME ``verdict.decide``, and writes nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.errors import AppError, ConflictError
from ...core.ids import new_id
from ...models.calibration import (
    CalibrationCheck,
    CalibrationRecord,
    CalibrationSet,
    CalibrationTarget,
    CalibrationVerdict,
)
from ...models.label_run import LabelRun
from ...schemas.calibration import TargetOut, TargetsOut
from ..sources.source_service import ApprovalRef, approval_ref
from .constants import C3_DEFAULT_LOWER_BOUND
from .mapping import question_hash
from .record_service import checks_of, figures_of
from .verdict import decide

__all__ = ["ApprovalRef", "approval_ref", "card_facts", "current", "history", "set_target"]


async def history(db: AsyncSession, qh: str) -> list[CalibrationTarget]:
    rows = await db.execute(
        select(CalibrationTarget)
        .where(CalibrationTarget.question_hash == qh)
        .order_by(CalibrationTarget.created_at.desc(), CalibrationTarget.id.desc())
    )
    return list(rows.scalars())


async def current(db: AsyncSession, qh: str) -> CalibrationTarget | None:
    rows = await history(db, qh)
    return rows[0] if rows else None


def target_out(row: CalibrationTarget) -> TargetOut:
    return TargetOut(
        id=row.id,
        question_hash=row.question_hash,
        question=row.question,
        target=row.target,
        set_by=row.set_by,
        set_by_origin=row.set_by_origin,
        approval_id=row.approval_id,
        approved_by=row.approved_by,
        created_at=row.created_at,
    )


async def targets_out(db: AsyncSession, qh: str) -> TargetsOut:
    rows = await history(db, qh)
    return TargetsOut(
        question_hash=qh,
        current=target_out(rows[0]) if rows else None,
        default_lower_bound=C3_DEFAULT_LOWER_BOUND,
        history=[target_out(r) for r in rows],
    )


async def verdict_changes(
    db: AsyncSession, qh: str, question: str, old: float | None, new: float
) -> list[dict[str, Any]]:
    """Each labeler whose latest record's verdict would differ under ``new``. Writes nothing."""
    records = (
        await db.execute(
            select(CalibrationRecord)
            .join(CalibrationSet, CalibrationSet.id == CalibrationRecord.calibration_set_id)
            .where(CalibrationSet.question_hash == qh)
            .order_by(CalibrationRecord.created_at.desc(), CalibrationRecord.id.desc())
        )
    ).scalars()
    latest: dict[str, CalibrationRecord] = {}
    for record in records:
        latest.setdefault(record.labeler_identity_hash, record)
    out: list[dict[str, Any]] = []
    for identity, record in sorted(latest.items()):
        checks = checks_of(
            list(
                (
                    await db.execute(
                        select(CalibrationCheck).where(CalibrationCheck.record_id == record.id)
                    )
                ).scalars()
            )
        )
        figs = figures_of(record)
        before = decide(figs, checks, old)
        after = decide(figs, checks, new)
        if before.verdict == after.verdict:
            continue
        version_ids = (
            await db.execute(
                select(LabelRun.input_version_id)
                .where(LabelRun.labeler_identity_hash == identity, LabelRun.question == question)
                .distinct()
            )
        ).scalars()
        out.append(
            {
                "record_id": record.id,
                "labeler": {
                    "identity_hash": identity,
                    "fingerprint": record.labeler_fingerprint,
                    "model_id": (record.labeler_identity or {}).get("model_id"),
                },
                "version_ids": sorted(str(v) for v in version_ids),
                "old_verdict": before.verdict,
                "new_verdict": after.verdict,
            }
        )
    return out


async def card_facts(db: AsyncSession, question: str, new: float) -> dict[str, Any]:
    """What the operator approves (FR-006.43): question, old target, new target, verdict changes."""
    qh = question_hash(question)
    row = await current(db, qh)
    old = row.target if row is not None else None
    changes = await verdict_changes(db, qh, question, old, new)
    return {
        "question": question,
        "question_hash": qh,
        "old": old,
        "old_display": "default (C3)" if old is None else f"{old:.3f}",
        "new": new,
        "verdict_changes": changes,
        "verdict_changes_note": (
            "No verdict would change."
            if not changes
            else "Stored verdicts are never rewritten; this is the verdict each record would get "
            "when next decided."
        ),
        "expected_current_target_id": row.id if row is not None else None,
    }


async def set_target(
    db: AsyncSession,
    question: str,
    target: float,
    who: Who,
    *,
    approval: ApprovalRef | None,
    expected_current_target_id: str | None = None,
    check_current: bool = False,
) -> CalibrationTarget:
    if who.origin == "agent" and approval is None:
        raise AppError(
            "An agent's target change needs the operator's approval (gate_target_write).",
            code="APPROVAL_REQUIRED",
            status_code=403,
        )
    qh = question_hash(question)
    if check_current:
        row = await current(db, qh)
        actual = row.id if row is not None else None
        if actual != expected_current_target_id:
            raise ConflictError(
                "The target changed after the agent asked; this approval described a different "
                "state and was not applied. Ask again.",
                code="APPROVAL_STALE",
                details={
                    "expected_current_target_id": expected_current_target_id,
                    "current": actual,
                },
            )
    created = CalibrationTarget(
        id=new_id("ct"),
        question_hash=qh,
        question=question,
        target=target,
        set_by=who.who,
        set_by_origin=who.origin,
        approval_id=approval.approval_id if approval else None,
        approved_by=approval.approved_by if approval else None,
    )
    db.add(created)
    await db.commit()
    await db.refresh(created)
    return created


async def verdict_target(db: AsyncSession, record_id: str) -> CalibrationTarget | None:
    verdict = await db.get(CalibrationVerdict, record_id)
    if verdict is None or verdict.target_id is None:
        return None
    return await db.get(CalibrationTarget, verdict.target_id)
