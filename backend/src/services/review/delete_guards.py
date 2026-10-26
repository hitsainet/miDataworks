"""Feature 006's answers to 002's version delete (002 FR-002.37; ``REFERENCE_CHECKERS``).

A version delete is a tombstone and 006's rows that reference it stay as evidence (P-15):
calibration sets, review queues and decisions are never a reason to keep a version. The one thing
that refuses is an audit still IN PROGRESS for the version, because the operator is deciding its
rows for a push the delete would make meaningless; finish or supersede it first.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.review import Audit
from ..version_delete_service import Reference, ReferenceChecker, register_reference_checker


async def _audits(db: AsyncSession, version_id: str) -> Reference | None:
    found = (
        await db.execute(
            select(Audit.id).where(Audit.version_id == version_id, Audit.state == "in_progress")
        )
    ).scalar_one_or_none()
    if found is None:
        return None
    return Reference(
        "version_in_use",
        f"Audit {found} of this version is in progress; finish it in Review, then delete.",
        {"audit_id": found},
    )


async def _never(db: AsyncSession, version_id: str) -> Reference | None:
    """Kept with the tombstone (P-15): evidence, never a reason to refuse."""
    return None


for _table, _check in (
    ("dw_audits", _audits),
    ("dw_calibration_sets", _never),
    ("dw_review_queues", _never),
    ("dw_review_decisions", _never),
):
    register_reference_checker(ReferenceChecker("006", _table, _check))
