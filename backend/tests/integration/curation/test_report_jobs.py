"""Report jobs, the post-build audit and the sweeper with real PostgreSQL (FTASKS 11.1–11.5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from src.core.database import get_sync_engine, sync_session_factory
from src.models import Job, VersionReport
from src.services.curation import api, label_columns, report_service
from src.services.curation.codes import ReportInput
from src.workers import curation_tasks
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import StepFixture, StubRegistry, labeler_info, make_version

LABELER = labeler_info("threshold_labeler", {"label": "metadata", "label_probability": "metadata"})


@pytest.fixture
def stub_labeler(monkeypatch: pytest.MonkeyPatch) -> StubRegistry:
    reg = StubRegistry({"threshold_labeler": LABELER})
    monkeypatch.setattr(label_columns, "_registry", lambda: reg)
    return reg


@pytest.fixture
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = []
    monkeypatch.setattr(
        curation_tasks, "emit", lambda room, event, data: out.append((room, event, data)) or True
    )
    monkeypatch.setattr(curation_tasks, "_next_jobs", lambda: None)
    return out


def _labelled_version() -> str:
    with sync_session_factory()() as db:
        v = make_version(db, hp.candidates(), hp.ROLES, steps=[StepFixture("threshold_labeler")])
        return str(v.id)


def _start(version_id: str) -> tuple[str, str]:
    params = api.audit_params("label")
    with sync_session_factory()() as db:
        job, report = report_service.start_job(
            db,
            "shortcut_audit",
            [ReportInput(version_id)],
            params,
            7,
            started_by="op",
            origin="operator",
        )
        return job.id, report.id


def test_run_report_completes_the_row_heartbeats_and_emits(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry, emitted: list
) -> None:
    vid = _labelled_version()
    job_id, report_id = _start(vid)
    curation_tasks.run_report(job_id)
    with sync_session_factory()() as db:
        report = db.get(VersionReport, report_id)
        job = db.get(Job, job_id)
        assert report.state == "completed" and report.result["label_column"] == "label"
        assert job.status == "completed" and job.heartbeat_at is not None
    room = f"dataworks/curation-reports/{job_id}"
    assert (room, "completed") in [(r, e) for r, e, _ in emitted]
    assert emitted[-1][2]["report_id"] == report_id


def test_cancel_during_a_report_stores_no_completed_row(
    clean_db: None,
    data_dir: Path,
    stub_labeler: StubRegistry,
    emitted: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vid = _labelled_version()
    job_id, report_id = _start(vid)
    from src.services.curation import kinds

    real = kinds.compute_for("shortcut_audit")

    def cancelling(db: Any, *args: Any) -> Any:
        with get_sync_engine().begin() as conn:
            conn.execute(
                text(
                    "UPDATE dw_jobs SET status='cancelling', cancel_requested_at=now() WHERE id=:id"
                ),
                {"id": job_id},
            )
        return real(db, *args)

    monkeypatch.setattr(curation_tasks, "compute_for", lambda kind: cancelling)
    from src.core import cancellation

    monkeypatch.setattr(cancellation, "DEFAULT_POLL_INTERVAL_S", 0.0)
    curation_tasks.run_report(job_id)
    with sync_session_factory()() as db:
        assert db.get(VersionReport, report_id).state == "cancelled"
        assert (
            db.execute(select(VersionReport).where(VersionReport.state == "completed")).first()
            is None
        )


def test_worker_death_leaves_running_which_the_sweeper_fails(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry
) -> None:
    vid = _labelled_version()
    job_id, report_id = _start(vid)
    with get_sync_engine().begin() as conn:  # the janitor failed the dead worker's job
        conn.execute(
            text("UPDATE dw_jobs SET status='failed', completed_at=now() WHERE id=:id"),
            {"id": job_id},
        )
    with sync_session_factory()() as db:
        assert curation_tasks.fail_orphaned_reports(db) == 1
        assert db.get(VersionReport, report_id).state == "failed"


def test_janitor_leaves_a_live_report_job_alone_and_reaps_a_dead_one(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry
) -> None:
    from datetime import timedelta

    from src.core.clock import utc_now
    from src.workers import janitor

    vid = _labelled_version()
    job_id, report_id = _start(vid)
    with get_sync_engine().begin() as conn:
        conn.execute(
            text(
                "UPDATE dw_jobs SET status='running', started_at=now(), heartbeat_at=now() WHERE id=:id"
            ),
            {"id": job_id},
        )
    with sync_session_factory()() as db:
        assert janitor.sweep(db, active_ids=set()) == []  # heartbeat is recent: alive
        assert db.get(VersionReport, report_id).state == "running"
    with sync_session_factory()() as db:
        reaped = janitor.sweep(db, active_ids=set(), now=utc_now() + timedelta(hours=1))
        assert [r.job_id for r in reaped] == [job_id]
    with sync_session_factory()() as db:
        row = db.get(VersionReport, report_id)
        assert row.state == "failed" and row.error["code"] == "job_reaped"


def test_post_version_audits_a_labelled_version(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry
) -> None:
    vid = _labelled_version()
    out = curation_tasks.post_version(vid)
    assert out["audit"]["outcome"] == "inline"
    with sync_session_factory()() as db:
        rows = (
            db.execute(select(VersionReport).where(VersionReport.version_id == vid)).scalars().all()
        )
    assert [r.kind for r in rows] == ["shortcut_audit"] and rows[0].state == "completed"


def test_post_version_records_a_refusal_so_the_sweeper_stops(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry
) -> None:
    with sync_session_factory()() as db:
        v = make_version(
            db, hp.candidates().slice(0, 6), hp.ROLES, steps=[StepFixture("threshold_labeler")]
        )
    out = curation_tasks.post_version(str(v.id))
    assert out["audit"] == {"refused": "insufficient_rows"}
    with sync_session_factory()() as db:
        assert curation_tasks.unaudited(db, registry=stub_labeler) == []


def test_sweeper_finds_an_unaudited_version_and_produces_its_report(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    vid = _labelled_version()
    with sync_session_factory()() as db:
        make_version(db, hp.candidates().slice(0, 50), hp.ROLES)  # no labeler: not wanted
    sent: list[tuple[str, list[Any]]] = []
    monkeypatch.setattr(curation_tasks, "_send_task", lambda name, args: sent.append((name, args)))
    from src.operators import registry as registry_module

    monkeypatch.setattr(registry_module, "current", lambda: stub_labeler)
    result = curation_tasks.sweep_unaudited()
    assert sent == [("midataworks.curation.post_version", [vid])] and result["enqueued"] == [vid]
    for _, args in sent:  # the worker runs what the sweeper sent: the report exists
        curation_tasks.post_version(*args)
    with sync_session_factory()() as db:
        assert (
            db.execute(
                select(VersionReport.state).where(VersionReport.version_id == vid)
            ).scalar_one()
            == "completed"
        )


def test_a_long_compute_heartbeats_on_time(
    clean_db: None,
    data_dir: Path,
    stub_labeler: StubRegistry,
    emitted: list,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time as _time

    from src.core.config import get_settings

    vid = _labelled_version()
    job_id, report_id = _start(vid)
    monkeypatch.setattr(get_settings(), "progress_heartbeat_seconds", 0.05)
    beats: list[str] = []
    real = curation_tasks.record_progress

    def recording(job: str, **kw: Any) -> bool:
        if kw.get("force") and kw.get("status") is None:
            beats.append(job)
        return real(job, **kw)

    monkeypatch.setattr(curation_tasks, "record_progress", recording)
    from src.services.curation import kinds

    compute = kinds.compute_for("shortcut_audit")

    def slow(*args: Any) -> Any:
        _time.sleep(0.4)
        return compute(*args)

    monkeypatch.setattr(curation_tasks, "compute_for", lambda kind: slow)
    curation_tasks.run_report(job_id)
    assert beats.count(job_id) >= 4  # the start mark plus at least three beats in 0.4 s


def test_sweeper_stamps_its_run_and_health_shows_it(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    import redis

    from src.core.config import get_settings
    from src.services import health_service

    redis.Redis.from_url(get_settings().redis_url).delete(health_service.CURATION_SWEEPER_KEY)
    assert asyncio.run(health_service.curation_sweeper()) == {"ok": True, "last_run": None}
    monkeypatch.setattr(curation_tasks, "_send_task", lambda name, args: None)
    curation_tasks.sweep_unaudited()
    stamped = asyncio.run(health_service.curation_sweeper())
    assert stamped["ok"] is True and stamped["last_run"] is not None


def test_report_lines_carry_kind_version_rows_duration_and_no_secret(
    clean_db: None, data_dir: Path, stub_labeler: StubRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    vid = _labelled_version()
    with caplog.at_level(logging.INFO, logger="src.services.curation.report_service"):
        report_service.find_or_run_inline(
            "shortcut_audit",
            [ReportInput(vid)],
            api.audit_params("label"),
            7,
            compute_for_audit(),
            started_by="op",
            origin="operator",
        )
    end = [r.getMessage() for r in caplog.records if r.getMessage().startswith("report end")]
    assert len(end) == 1
    assert "kind=shortcut_audit" in end[0] and f"version={vid}" in end[0]
    assert "rows=10914" in end[0] and "seconds=" in end[0] and "params=" in end[0]
    assert "test-only" not in caplog.text and "hf_" not in caplog.text


def compute_for_audit() -> Any:
    from src.services.curation.audit_service import compute_audit

    return compute_audit
