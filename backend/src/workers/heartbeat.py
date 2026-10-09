# Origin: miStudio (Onegaishimas/miStudio) backend/src/workers/task_heartbeat.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). miStudio stamps a heartbeat into Celery's result meta; miDataworks
# stamps `dw_jobs.heartbeat_at` through record_progress (ADR-007), so what remains here is the
# staleness rule and the "is Celery running it" question the janitor asks.
"""Liveness: when is a job stale, and does Celery report it active (ADR-007; task 5.5)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def last_sign_of_life(
    heartbeat_at: datetime | None, started_at: datetime | None, created_at: datetime
) -> datetime:
    """The newest timestamp the job wrote. A job that never beat is judged from its start."""
    return heartbeat_at or started_at or created_at


def is_stale(last: datetime, limit: timedelta, now: datetime) -> bool:
    """Older than the kind's limit. The limit must exceed the slowest gap between beats."""
    return now - last > limit


def active_task_ids(timeout_s: float = 2.0) -> set[str] | None:
    """Task ids Celery workers report running, or None when the question could not be asked.

    None means UNKNOWN (the broker is unreachable), and the janitor then reaps nothing. An empty
    set means the broker answered and no worker is running anything.
    """
    from ..core.celery_app import celery_app

    try:
        replies = celery_app.control.inspect(timeout=timeout_s).active()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not ask Celery which tasks are active: %s", exc)
        return None
    ids: set[str] = set()
    for tasks in (replies or {}).values():
        for task in tasks or []:
            task_id = task.get("id")
            if isinstance(task_id, str):
                ids.add(task_id)
    return ids
