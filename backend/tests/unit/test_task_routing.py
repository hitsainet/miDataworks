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
    "midataworks.calibration.compute_record": "default",
    "midataworks.detector_sets.run_mistudio_send": "publish",
    "midataworks.system.janitor": "default",
    "midataworks.system.dispatch_queued": "default",
    "midataworks.system.expire_approvals": "default",
    "midataworks.system.prune_agent_requests": "default",
    "midataworks.versions.advance_build": "curation",
    "midataworks.versions.verify_rebuild": "curation",
    "midataworks.versions.version_orphan_sweeper": "default",
    "midataworks.sources.preview_hf": "default",
    "midataworks.sources.import_source": "curation",
    "midataworks.operators.step.curation": "curation",
    "midataworks.operators.step.labeling": "labeling",
    "midataworks.operators.step.finalize_datajuicer": "curation",
    "midataworks.operators.step.finalize_designer": "labeling",
    "midataworks.operators.preview": "preview",
    "midataworks.publish.build": "publish",
    "midataworks.publish.check": "publish",
    "midataworks.publish.publish": "publish",
    "midataworks.publish.reverify": "publish",
    "midataworks.publish.export": "publish",
    # feature 005 (FTDD 005 section 11)
    "midataworks.labeling.run_label_run": "labeling",
    # feature 007 (FTDD 007 section 3): endpoint calls on labeling; the Beat sweep on default
    "midataworks.generation.run": "labeling",
    "midataworks.generation.diversity_report": "labeling",
    "midataworks.generation.diversity_sweep": "default",
    "midataworks.detector_sets.advance_minimal_pair_chains": "default",
    "midataworks.labeling.run_label_preview": "labeling",
    "midataworks.labeling.run_rederive": "labeling",
    "midataworks.labeling.run_aggregate": "labeling",
    "midataworks.labeling.renew_model_leases": "default",
    # feature 004
    "midataworks.curation.run_report": "curation",
    "midataworks.curation.post_version": "curation",
    "midataworks.curation.sweep_unaudited": "curation",
}

#: Names sent by the backend to tasks that live in other images or later features.
SENT_BY_NAME: dict[str, str] = {
    "midataworks.datajuicer.ping": "datajuicer",
    "midataworks.datajuicer.preview": "datajuicer_preview",
    "midataworks.datajuicer.step": "datajuicer",
    "midataworks.designer.step": "designer",
    "midataworks.designer.preview": "designer",
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
        "designer",
    }
    declared = {q.name for q in celery_app.conf.task_queues}
    assert declared == set(QUEUES)


def test_task_arguments_are_never_stored_with_results() -> None:
    """001 FTDD section 8: result_extended would store arguments beside each result."""
    assert celery_app.conf.result_extended is False
