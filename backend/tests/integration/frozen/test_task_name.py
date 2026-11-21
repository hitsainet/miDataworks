"""DR-38: the queued-message identifier of the detector-set send stays (feature 020; FR-020.2).

A message queued under the old task name before a deploy must still be consumed after it: the name
is registered in the LIVE Celery app and routes to the ``publish`` queue (S001 §11.4)."""

from __future__ import annotations

from src.core.celery_app import celery_app, route_for
from src.core.job_kinds import get_job_kind
from tests.support.frozen import exercises

TASK = "midataworks.detector_sets.run_mistudio_send"


@exercises("DR-38", literals=(TASK,))
def test_dr38_task_name_registered_and_routed_to_publish(literal: str) -> None:
    celery_app.loader.import_default_modules()
    assert literal in celery_app.tasks, f"{literal} is not registered"
    assert route_for(literal) == "publish"
    assert get_job_kind("mistudio_send").task_name == literal
