# Origin: miStudio (Onegaishimas/miStudio) backend/src/core/celery_app.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). Kept: explicit routes keyed on the full task name, because
# task_routes globs match the TASK NAME and a short name silently lands on the default queue;
# acks_late with prefetch 1. Changed: seven queues (ADR-006 v1.1), the single
# `midataworks.<area>.<task>` prefix, exact names listed before globs, Beat runs the janitor.
"""Celery: seven queues, fully qualified task names, explicit routes (ADR-006; task 5.6).

The routing contract, enforced by ``tests/unit/test_task_routing.py`` against the LIVE registry:
- every task name starts with ``midataworks.``;
- every task resolves to the queue its area names;
- exact names (the two preview tasks) are matched before the globs, because
  ``midataworks.datajuicer.preview`` must reach ``datajuicer_preview`` and not ``datajuicer``.

The Data-Juicer tasks live in the Data-Juicer image (``src/operators/datajuicer/runner.py``) and
are not registered in this app; the backend sends them by name and this table routes them.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from celery import Celery
from kombu import Queue

from .config import get_settings

QUEUES: tuple[str, ...] = (
    "default",
    "curation",
    "labeling",
    "datajuicer",
    "publish",
    "preview",
    "datajuicer_preview",
)

#: Ordered: Celery takes the first matching entry, so exact names come before globs.
TASK_ROUTES: dict[str, dict[str, str]] = {
    # exact names first
    "midataworks.datajuicer.preview": {"queue": "datajuicer_preview"},
    "midataworks.preview.run": {"queue": "preview"},
    # area globs
    "midataworks.datajuicer.*": {"queue": "datajuicer"},
    "midataworks.preview.*": {"queue": "preview"},
    "midataworks.curation.*": {"queue": "curation"},
    "midataworks.operators.step.curation": {"queue": "curation"},
    "midataworks.labeling.*": {"queue": "labeling"},
    "midataworks.publish.*": {"queue": "publish"},
    "midataworks.system.*": {"queue": "default"},
    "midataworks.selftest.*": {"queue": "default"},
}

#: Modules whose tasks the app registers. Celery imports these itself (``include=``), so a test
#: that reads the registry sees exactly what a worker would.
TASK_MODULES: tuple[str, ...] = (
    "src.workers.selftest_tasks",
    "src.workers.janitor",
    "src.workers.dispatch",
    "src.workers.approval_tasks",
    "src.workers.startup",
)

BEAT_SCHEDULE: dict[str, dict[str, Any]] = {
    "job-janitor": {
        "task": "midataworks.system.janitor",
        "schedule": timedelta(seconds=120),
    },
    "dispatch-queued-jobs": {
        "task": "midataworks.system.dispatch_queued",
        "schedule": timedelta(seconds=30),
    },
    "expire-approvals": {
        "task": "midataworks.system.expire_approvals",
        "schedule": timedelta(minutes=5),
    },
}


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("midataworks", broker=settings.redis_url, backend=settings.redis_url)
    beat = dict(BEAT_SCHEDULE)
    beat["job-janitor"] = {
        **beat["job-janitor"],
        "schedule": timedelta(seconds=settings.janitor_interval_seconds),
    }
    app.conf.update(
        task_default_queue="default",
        task_queues=[Queue(name) for name in QUEUES],
        task_routes=TASK_ROUTES,
        task_acks_late=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        beat_schedule=beat,
        task_always_eager=settings.celery_task_always_eager,
        include=list(TASK_MODULES),
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery_app()


def route_for(task_name: str) -> str:
    """The queue a task name resolves to, through Celery's own router."""
    router = celery_app.amqp.router
    route = router.route({}, task_name)
    queue = route.get("queue")
    name = getattr(queue, "name", queue)
    return str(name) if name else celery_app.conf.task_default_queue
