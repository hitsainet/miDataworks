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
    # ADR-010 amendment (2026-10-07): Data Designer's own image and queue, like Data-Juicer.
    "designer",
)

#: Ordered: Celery takes the first matching entry, so exact names come before globs.
TASK_ROUTES: dict[str, dict[str, str]] = {
    # exact names first
    "midataworks.datajuicer.preview": {"queue": "datajuicer_preview"},
    "midataworks.preview.run": {"queue": "preview"},
    # feature 003 (FTASKS 12.1): named exactly, so no glob decides where a step runs
    "midataworks.operators.preview": {"queue": "preview"},
    "midataworks.operators.step.curation": {"queue": "curation"},
    "midataworks.operators.step.labeling": {"queue": "labeling"},
    "midataworks.operators.step.finalize_datajuicer": {"queue": "curation"},
    "midataworks.operators.step.finalize_designer": {"queue": "labeling"},
    # feature 005 (FTDD 005 section 11): the lease Beat task is not labeling work
    "midataworks.labeling.renew_model_leases": {"queue": "default"},
    # feature 007 (FTDD 007 section 3): the Beat sweep is not model work
    "midataworks.generation.diversity_sweep": {"queue": "default"},
    # area globs
    "midataworks.datajuicer.*": {"queue": "datajuicer"},
    "midataworks.designer.*": {"queue": "designer"},
    "midataworks.preview.*": {"queue": "preview"},
    "midataworks.versions.version_orphan_sweeper": {"queue": "default"},
    "midataworks.sources.preview_hf": {"queue": "default"},
    "midataworks.sources.import_source": {"queue": "curation"},
    "midataworks.versions.advance_build": {"queue": "curation"},
    "midataworks.versions.verify_rebuild": {"queue": "curation"},
    "midataworks.curation.*": {"queue": "curation"},
    "midataworks.labeling.*": {"queue": "labeling"},
    # feature 007: generation runs and diversity reports call endpoints (ADR-006)
    "midataworks.generation.*": {"queue": "labeling"},
    "midataworks.publish.*": {"queue": "publish"},
    # feature 009 (FR-009.17): a send runs on the publish queue, beside 008's publishes
    "midataworks.detector_sets.run_mistudio_send": {"queue": "publish"},
    # feature 009 (decision 2026-10-07): Beat advances minimal-pair chains; it starts work only
    "midataworks.detector_sets.advance_minimal_pair_chains": {"queue": "default"},
    "midataworks.system.*": {"queue": "default"},
    "midataworks.selftest.*": {"queue": "default"},
    # feature 006 (FTDD 006 section 11): no model calls, short jobs
    "midataworks.calibration.compute_record": {"queue": "default"},
}

#: Modules whose tasks the app registers. Celery imports these itself (``include=``), so a test
#: that reads the registry sees exactly what a worker would.
TASK_MODULES: tuple[str, ...] = (
    "src.workers.selftest_tasks",
    "src.workers.janitor",
    "src.workers.dispatch",
    "src.workers.approval_tasks",
    "src.workers.startup",
    "src.workers.version_build_tasks",
    "src.workers.source_tasks",
    "src.workers.operator_tasks",
    "src.workers.curation_tasks",
    "src.workers.publish_tasks",
    "src.workers.label_run_tasks",
    "src.workers.lease_tasks",
    "src.workers.calibration_tasks",
    "src.workers.detector_send_tasks",
    "src.workers.generation_tasks",
    "src.workers.diversity_tasks",
    "src.workers.minimal_pair_tasks",
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
    "version-orphan-sweeper": {
        "task": "midataworks.versions.version_orphan_sweeper",
        "schedule": timedelta(hours=1),
    },
    # Feature 004: the backstop for a lost post_version message (FTDD 004 section 2.2).
    "curation-sweep-unaudited": {
        "task": "midataworks.curation.sweep_unaudited",
        "schedule": timedelta(seconds=60),
    },
    "expire-approvals": {
        "task": "midataworks.system.expire_approvals",
        "schedule": timedelta(minutes=5),
    },
    # feature 005 (FTDD 005 section 6.5): renew shared miLLM leases, release memberless ones
    "renew-model-leases": {
        "task": "midataworks.labeling.renew_model_leases",
        "schedule": timedelta(minutes=5),
    },
    # feature 007: queue a diversity report for every version with generated rows and none
    "generation-diversity-sweep": {
        "task": "midataworks.generation.diversity_sweep",
        "schedule": timedelta(seconds=300),
    },
    # feature 009: each running minimal-pair chain moves to its next stage when one finishes
    "advance-minimal-pair-chains": {
        "task": "midataworks.detector_sets.advance_minimal_pair_chains",
        "schedule": timedelta(seconds=30),
    },
    "prune-agent-requests": {
        "task": "midataworks.system.prune_agent_requests",
        "schedule": timedelta(days=1),
    },
}


def create_celery_app() -> Celery:
    settings = get_settings()
    app = Celery("midataworks", broker=settings.redis_url, backend=settings.redis_url)
    beat = dict(BEAT_SCHEDULE)
    beat["curation-sweep-unaudited"] = {
        **beat["curation-sweep-unaudited"],
        "schedule": timedelta(seconds=settings.curation_sweep_interval_s),
    }
    beat["generation-diversity-sweep"] = {
        **beat["generation-diversity-sweep"],
        "schedule": timedelta(seconds=settings.diversity_sweep_seconds),
    }
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
        # Never store task arguments with results: an argument could one day carry a secret
        # (001 FTDD section 8; asserted by tests/unit/test_task_routing.py).
        result_extended=False,
        result_expires=300,
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


def linked_signature(name: str, **options: Any) -> Any:
    """A callback signature whose queue is PINNED to this app's route for ``name``.

    A ``link`` or ``link_error`` is sent by the worker that ran the parent task, using THAT
    worker's Celery app. The Data-Juicer and Data Designer runners define their own apps, whose
    default queues are ``datajuicer`` and ``designer`` and which have no route for the backend's
    finalize and preview tasks — so an unpinned callback landed on the runner's own queue, where
    the task is unregistered, and was discarded. Every version build with a Data-Juicer step hung
    at ``running`` in production (2026-10-07, found by the first live Humicroedit build).
    """
    return celery_app.signature(name, queue=route_for(name), **options)
