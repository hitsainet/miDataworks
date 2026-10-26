"""Feature 018 slice 3 wiring read from the LIVE registries (FTASKS 10.13): the contract re-check is
a task a worker registers AND a Beat entry the live Celery configuration schedules every 10 min."""

from __future__ import annotations

from datetime import timedelta

from src.core.celery_app import celery_app, route_for

TASK = "midataworks.code_rules.recheck_contract"


def test_the_recheck_is_registered_scheduled_and_routed() -> None:
    celery_app.loader.import_default_modules()
    assert TASK in celery_app.tasks
    scheduled = {
        name: entry
        for name, entry in celery_app.conf.beat_schedule.items()
        if entry["task"] == TASK
    }
    assert len(scheduled) == 1, scheduled
    (entry,) = scheduled.values()
    assert entry["schedule"] == timedelta(minutes=10)
    assert route_for(TASK) == "default"
