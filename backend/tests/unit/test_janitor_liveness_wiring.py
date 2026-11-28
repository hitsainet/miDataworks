"""002 mutation round (2026-10-17), survivor V13: the janitor's liveness probes for build AND verify
jobs are registered by the worker's own task modules. Without the probe a verify job (which runs
steps whose heartbeats live on step executions, not the job row) is judged by the job row's
heartbeat alone and can be reaped while its steps are alive. Read from the live registry after
importing exactly the modules the Celery app includes."""

from __future__ import annotations

import importlib

from src.core.celery_app import TASK_MODULES


def test_build_and_verify_jobs_have_their_liveness_probe() -> None:
    for module in TASK_MODULES:
        importlib.import_module(module)
    from src.workers import janitor, version_build_tasks

    assert "src.workers.version_build_tasks" in TASK_MODULES
    assert janitor.LIVENESS["version_build"] is version_build_tasks.build_liveness
    assert janitor.LIVENESS["version_verify"] is version_build_tasks.build_liveness
