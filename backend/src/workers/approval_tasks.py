"""Beat task: expire pending approvals after ``APPROVAL_TTL_HOURS`` (P-08)."""

from __future__ import annotations

from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..services.approval_service import expire_due


@celery_app.task(name="midataworks.system.expire_approvals")
def expire_approvals_task() -> dict[str, int]:
    with get_sync_db() as db:
        return {"expired": expire_due(db)}
