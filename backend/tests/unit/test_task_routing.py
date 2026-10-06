"""Every registered task reaches its intended queue (ADR-006 v1.1; Foundation task 5.6).

Driven by the LIVE registry: the modules a worker imports (``include=``) are imported by Celery
itself, and every task found is checked. A task name without the ``midataworks.`` prefix fails,
because ``task_routes`` globs match the task NAME and a short name silently lands on ``default``.
"""

from __future__ import annotations

import pytest

from src.core.celery_app import QUEUES, celery_app, route_for

#: The queue each task must reach. A registered task missing here fails the suite.
INTENDED: dict[str, str] = {
    "midataworks.selftest.run": "default",
    "midataworks.system.janitor": "default",
    "midataworks.system.dispatch_queued": "default",
    "midataworks.system.expire_approvals": "default",
}

#: Names sent by the backend to tasks that live in other images or later features.
SENT_BY_NAME: dict[str, str] = {
    "midataworks.datajuicer.ping": "datajuicer",
    "midataworks.datajuicer.preview": "datajuicer_preview",
    "midataworks.datajuicer.step": "datajuicer",
    "midataworks.preview.run": "preview",
    "midataworks.curation.profile": "curation",
    "midataworks.operators.step.curation": "curation",
    "midataworks.labeling.run": "labeling",
    "midataworks.publish.hub_push": "publish",
}


def _registered() -> set[str]:
    celery_app.loader.import_default_modules()
    return {name for name in celery_app.tasks if not name.startswith("celery.")}


def test_every_registered_task_has_the_single_prefix() -> None:
    bad = sorted(n for n in _registered() if not n.startswith("midataworks."))
    assert not bad, f"task names without the midataworks. prefix land on default: {bad}"


def test_every_registered_task_routes_to_its_intended_queue() -> None:
    registered = _registered()
    unlisted = registered - set(INTENDED)
    assert not unlisted, f"registered tasks with no intended queue here: {sorted(unlisted)}"
    for name in registered:
        assert route_for(name) == INTENDED[name], name


@pytest.mark.parametrize(("name", "queue"), sorted(SENT_BY_NAME.items()))
def test_names_sent_to_other_workers_route_correctly(name: str, queue: str) -> None:
    assert route_for(name) == queue


def test_preview_exact_names_beat_the_globs() -> None:
    assert route_for("midataworks.datajuicer.preview") == "datajuicer_preview"
    assert route_for("midataworks.datajuicer.anything_else") == "datajuicer"


def test_a_short_name_would_land_on_default() -> None:
    """The failure mode the prefix rule guards against, demonstrated."""
    assert route_for("label") == "default"


def test_the_seven_queues_are_declared() -> None:
    assert set(QUEUES) == {
        "default",
        "curation",
        "labeling",
        "datajuicer",
        "publish",
        "preview",
        "datajuicer_preview",
    }
    declared = {q.name for q in celery_app.conf.task_queues}
    assert declared == set(QUEUES)
