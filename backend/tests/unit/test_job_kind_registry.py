"""Every job kind declares a cancel scope, a janitor limit and a room (Foundation 5.2, 5.2a)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.core.cancellation import SCOPES
from src.core.job_kinds import JOB_KINDS, JobKind, JobKindError, register_job_kind


@pytest.mark.parametrize("name", sorted(JOB_KINDS))
def test_every_registered_kind_is_complete(name: str) -> None:
    kind = JOB_KINDS[name]
    assert kind.cancel_scope in SCOPES, f"{name}: cancel scope"
    assert kind.janitor_heartbeat_limit > timedelta(0), f"{name}: janitor limit"
    assert kind.room("job_x").startswith("dataworks/") and kind.room("job_x").endswith("/job_x")


def test_label_run_and_lease_are_in_the_live_registry() -> None:
    assert JOB_KINDS["label_run"].janitor_heartbeat_limit == timedelta(minutes=15)
    assert JOB_KINDS["label_run"].room("abc") == "dataworks/label-runs/abc"
    assert JOB_KINDS["lease"].janitor_heartbeat_limit == timedelta(minutes=10)
    assert JOB_KINDS["lease"].room("abc") == "dataworks/leases/abc"


def test_the_janitor_limit_exceeds_the_heartbeat_throttle() -> None:
    """A limit at or under the 60 s throttle would reap a live job between two beats."""
    for kind in JOB_KINDS.values():
        assert kind.janitor_heartbeat_limit > timedelta(seconds=120), kind.name


@pytest.mark.parametrize(
    "broken",
    [
        JobKind("x_no_scope", "nowhere", timedelta(minutes=1), "dataworks/x/{id}"),
        JobKind("x_no_limit", "dw_jobs", timedelta(0), "dataworks/x/{id}"),
        JobKind("x_no_room", "dw_jobs", timedelta(minutes=1), ""),
        JobKind("x_bad_room", "dw_jobs", timedelta(minutes=1), "jobs/{id}"),
        JobKind("x_short_task", "dw_jobs", timedelta(minutes=1), "dataworks/x/{id}", "run"),
    ],
    ids=lambda k: k.name,
)
def test_an_incomplete_kind_is_refused(broken: JobKind) -> None:
    with pytest.raises(JobKindError):
        register_job_kind(broken)
    assert broken.name not in JOB_KINDS
