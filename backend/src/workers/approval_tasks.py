"""Beat tasks: expire pending approvals (P-08) and prune old agent activity (010 FTDD 4.2)."""

from __future__ import annotations

from ..core.celery_app import celery_app
from ..core.database import get_sync_db
from ..services.agent_activity_service import prune_agent_requests
from ..services.approval_service import expire_due


@celery_app.task(name="midataworks.system.expire_approvals")
def expire_approvals_task() -> dict[str, int]:
    with get_sync_db() as db:
        return {"expired": expire_due(db)}


@celery_app.task(name="midataworks.system.prune_agent_requests")
def prune_agent_requests_task() -> dict[str, int]:
    with get_sync_db() as db:
        return {"pruned": prune_agent_requests(db)}
