"""Beat task: start queued jobs the model rule allows (R-03.64; Foundation task 7.1)."""

from __future__ import annotations

from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..services.job_service import dispatch_queued


@celery_app.task(name="midataworks.system.dispatch_queued")
def dispatch_queued_task() -> dict[str, object]:
    with get_sync_db() as db:
        return {"sent": dispatch_queued(db)}
