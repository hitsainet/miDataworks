"""Old-name evidence is written by every process (feature 020 part B; FR-020.5; FTASKS 4.5, 4.7).

Each touch point is driven through its REAL trigger (the API lifespan, the live Celery signals, a
task applied through ``celery_app``), and the assertion is the row it wrote: payload and count. A
deleted ``connect`` line therefore turns a behaviour test red, not only a registry check (FTID §7.2;
controls M8a-M8j). Time is injected (a fake monotonic clock and a fake wall clock), never slept.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text

from src.core import env_names, legacy_names
from src.core.config import get_settings
from src.core.database import get_sync_engine
from tests.support.env_rows import in_transition

T0 = datetime(2026, 11, 20, 15, 0, tzinfo=UTC)


@dataclass
class Clocks:
    mono: float = 1000.0
    wall: datetime = T0
    waits: list[float] = field(default_factory=list)

    def advance(self, seconds: float) -> None:
        self.mono += seconds
        self.wall += timedelta(seconds=seconds)


def _set_env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for row in env_names.RENAMES:
        monkeypatch.delenv(row.new, raising=False)
        monkeypatch.delenv(row.old, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()  # type: ignore[attr-defined]
    get_settings()


@pytest.fixture
def clocks(monkeypatch: pytest.MonkeyPatch, clean_db: None) -> Iterator[Clocks]:
    in_transition(monkeypatch)  # the rows are retired since P7; these tests drive the evidence
    state = Clocks()
    monkeypatch.setattr(legacy_names, "_clock", lambda: state.mono)
    monkeypatch.setattr(legacy_names, "_now", lambda: state.wall)
    legacy_names.reset()
    yield state
    legacy_names.reset()
    get_settings.cache_clear()  # type: ignore[attr-defined]


@pytest.fixture
def old_names(monkeypatch: pytest.MonkeyPatch, clocks: Clocks) -> Clocks:
    _set_env(
        monkeypatch,
        MILLM_BASE_URL="http://old.test:8000",
        MIWORKBENCH_BASE_URL="http://new.test:8000",  # the NEW name for miWorkbench: not touched
    )
    return clocks


def uses() -> dict[str, dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT kind, old_name, new_name, first_seen_at, last_seen_at, count "
                "FROM dw_legacy_name_uses"
            )
        ).mappings()
        return {r["old_name"]: dict(r) for r in rows}


def write_errors() -> list[dict[str, Any]]:
    with get_sync_engine().connect() as conn:
        rows = conn.execute(
            text("SELECT reporter, error, failed_at, cleared_at FROM dw_legacy_name_write_errors")
        ).mappings()
        return [dict(r) for r in rows]


# --- 4.5: the store ---------------------------------------------------------------------------


def test_start_with_old_name_writes_a_row(old_names: Clocks) -> None:
    assert legacy_names.touch_sync(role="api") is True
    got = uses()
    assert set(got) == {"MILLM_BASE_URL"}, "only the old name actually read is recorded"
    row = got["MILLM_BASE_URL"]
    assert (row["kind"], row["new_name"], row["count"]) == ("env", "MISERVER_BASE_URL", 1)
    assert row["first_seen_at"] == T0 and row["last_seen_at"] == T0


def test_restart_with_new_name_keeps_the_earlier_last_seen(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy_names.touch_sync(role="api")
    _set_env(monkeypatch, MISERVER_BASE_URL="http://new.test:8000")  # restarted on the new name
    legacy_names.reset()
    old_names.advance(3600)
    assert legacy_names.touch_sync(role="api") is False
    row = uses()["MILLM_BASE_URL"]
    assert row["last_seen_at"] == T0 and row["count"] == 1


def test_a_task_after_a_minute_refreshes_last_seen(old_names: Clocks) -> None:
    from src.core.celery_app import celery_app

    legacy_names.touch_sync(role="api")
    old_names.advance(61)
    celery_app.tasks["celery.accumulate"].apply(args=(1,))
    row = uses()["MILLM_BASE_URL"]
    assert row["count"] == 2
    assert row["last_seen_at"] == T0 + timedelta(seconds=61)


def test_throttle_holds_within_a_minute(old_names: Clocks) -> None:
    assert legacy_names.touch_sync(role="worker") is True
    old_names.advance(30)
    assert legacy_names.touch_sync(role="worker") is False
    old_names.advance(30)
    assert legacy_names.touch_sync(role="worker") is True
    assert uses()["MILLM_BASE_URL"]["count"] == 2


def test_greatest_keeps_evidence_from_moving_back(old_names: Clocks) -> None:
    """A slow writer stamping an EARLIER time never moves ``last_seen_at`` back (control M8d)."""
    old_names.wall = T0 + timedelta(hours=2)
    legacy_names.touch_sync(role="worker")
    old_names.mono += 61
    old_names.wall = T0
    assert legacy_names.touch_sync(role="beat") is True
    row = uses()["MILLM_BASE_URL"]
    assert row["count"] == 2
    assert row["last_seen_at"] == T0 + timedelta(hours=2)


async def test_a_failed_worker_write_is_reported_and_cleared(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.database import async_session_factory

    real = legacy_names._upsert

    def failing(*_: Any, **__: Any) -> None:
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(legacy_names, "_upsert", failing)
    assert legacy_names.touch_sync(role="worker") is False
    who = f"worker@{socket.gethostname()}"
    [error] = write_errors()
    assert error == {"reporter": who, "error": "RuntimeError", "failed_at": T0, "cleared_at": None}
    assert uses() == {}
    async with async_session_factory()() as db:
        _, meta = await legacy_names.block(db)
    assert meta["error"] == f"write failed in {who}: RuntimeError"

    monkeypatch.setattr(legacy_names, "_upsert", real)
    old_names.advance(
        5
    )  # the stamp is set on ATTEMPT (review 2026-11-08): no retry inside the minute
    assert legacy_names.touch_sync(role="worker") is False
    old_names.advance(60)
    assert legacy_names.touch_sync(role="worker") is True
    [error] = write_errors()
    assert error["cleared_at"] == T0 + timedelta(seconds=65)
    assert uses()["MILLM_BASE_URL"]["count"] == 1
    async with async_session_factory()() as db:
        _, meta = await legacy_names.block(db)
    assert meta["error"] is None


def test_a_process_on_new_names_writes_nothing(
    clocks: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_env(
        monkeypatch,
        MISERVER_BASE_URL="http://new.test:8000",
        MIWORKBENCH_BASE_URL="http://bench.test:8000",
    )
    assert legacy_names.touch_sync(role="api") is False
    assert uses() == {} and write_errors() == []


# --- 4.7: each touch point through its real trigger --------------------------------------------


async def test_api_start_touches(old_names: Clocks) -> None:
    from src.main import fastapi_app, lifespan

    async with lifespan(fastapi_app):
        pass
    assert uses()["MILLM_BASE_URL"]["count"] == 1


def test_worker_process_init_touches(old_names: Clocks) -> None:
    from celery.signals import worker_process_init

    import src.core.celery_app  # noqa: F401 - the connects run at import

    worker_process_init.send(sender=None)
    assert uses()["MILLM_BASE_URL"]["count"] == 1


def test_task_prerun_touches_through_the_live_signal(old_names: Clocks) -> None:
    from src.core.celery_app import celery_app

    result = celery_app.tasks["celery.accumulate"].apply(args=(7,))
    assert result.get() == (7,)
    row = uses()["MILLM_BASE_URL"]
    assert row["count"] == 1 and row["last_seen_at"] == T0


def _fake_wait(clocks: Clocks, ticks: int) -> Any:
    def wait(seconds: float) -> bool:
        clocks.waits.append(seconds)
        clocks.advance(seconds)
        return len(clocks.waits) >= ticks

    return wait


def _join_heartbeat() -> None:
    thread = legacy_names._heartbeat
    assert thread is not None, "no heartbeat thread was started"
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_idle_worker_heartbeat_refreshes_once_per_minute(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    from celery.signals import worker_ready

    import src.core.celery_app  # noqa: F401

    monkeypatch.setattr(legacy_names, "_heartbeat_wait", _fake_wait(old_names, 3))
    worker_ready.send(sender=None)
    _join_heartbeat()
    assert old_names.waits == [60.0, 60.0, 60.0]
    row = uses()["MILLM_BASE_URL"]
    assert row["count"] == 3, "one write per injected minute"
    assert row["last_seen_at"] == T0 + timedelta(seconds=120)


def test_beat_heartbeat_refreshes_once_per_minute(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    from celery.signals import beat_init

    import src.core.celery_app  # noqa: F401

    monkeypatch.setattr(legacy_names, "_heartbeat_wait", _fake_wait(old_names, 3))
    beat_init.send(sender=None)
    _join_heartbeat()
    assert old_names.waits == [60.0, 60.0, 60.0]
    row = uses()["MILLM_BASE_URL"]
    assert row["count"] == 3  # the start touch, then one per minute (the first tick throttled)
    assert row["last_seen_at"] == T0 + timedelta(seconds=120)


def test_a_failed_attempt_holds_the_throttle(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2026-11-08: the throttle stamp is set on ATTEMPT, so a store that hangs or fails is
    tried at most once a minute per process, never once per health call (control M72)."""
    calls: list[int] = []

    def failing(*_: Any, **__: Any) -> None:
        calls.append(1)
        raise RuntimeError("store unreachable")

    monkeypatch.setattr(legacy_names, "_upsert", failing)
    for _ in range(5):
        legacy_names.touch_sync(role="api")
        old_names.advance(5)
    assert len(calls) == 1
    old_names.advance(60)
    legacy_names.touch_sync(role="api")
    assert len(calls) == 2


def test_the_store_engine_bounds_its_connect_and_statements(
    clean_db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung Postgres must not hold a touch thread forever: the touch engine connects and runs
    each statement under the health probe bound (review 2026-11-08). Read from the live session."""
    from sqlalchemy import text as sql

    from src.core.config import get_settings

    built: list[dict[str, Any]] = []
    real = legacy_names.create_engine

    def recording(*args: Any, **kwargs: Any) -> Any:
        built.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(legacy_names, "create_engine", recording)
    monkeypatch.setattr(legacy_names, "_engine", None)
    engine = legacy_names._own_engine()
    bound = max(1, int(round(get_settings().health_probe_timeout_seconds)))
    assert built[0]["connect_args"]["connect_timeout"] == bound
    with engine.connect() as conn:
        shown = conn.execute(sql("SHOW statement_timeout")).scalar()
    assert shown in (f"{bound}s", f"{bound * 1000}ms"), shown


def test_a_touch_against_a_server_that_never_answers_ends(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2: a database that accepts the connection and then says nothing (hung). The
    touch thread must end on its own (connect_timeout) while the server is STILL silent; no timing
    is asserted, only that it ends at all (the join's bound is a liveness limit, 30x the bound)."""
    import threading

    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    server.settimeout(0.1)
    held: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        while not stop.is_set():
            try:
                held.append(server.accept()[0])
            except OSError:
                pass

    acceptor = threading.Thread(target=accept, daemon=True)
    acceptor.start()
    port = server.getsockname()[1]
    monkeypatch.setattr(get_settings(), "health_probe_timeout_seconds", 1.0)
    monkeypatch.setattr(
        get_settings(), "database_url", f"postgresql+asyncpg://u:p@127.0.0.1:{port}/silent_test"
    )
    monkeypatch.setattr(legacy_names, "_engine", None)
    result: list[bool] = []
    touch = threading.Thread(
        target=lambda: result.append(legacy_names.touch_sync(role="api")), daemon=True
    )
    try:
        touch.start()
        touch.join(30)
        assert not touch.is_alive(), "the touch still waits on a server that never answers"
        assert held, "the touch never reached the silent server"
        assert result == [False]
    finally:
        stop.set()
        acceptor.join(5)
        for conn in held:
            conn.close()
        server.close()


def test_a_touch_behind_a_held_lock_ends_and_reports(
    old_names: Clocks, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review round 2: the store's table is locked by another session (a hung database as the
    touch sees it). The touch ends under statement_timeout while the lock is STILL held, and the
    failure is recorded, not lost."""
    import threading

    monkeypatch.setattr(get_settings(), "health_probe_timeout_seconds", 1.0)
    monkeypatch.setattr(legacy_names, "_engine", None)
    result: list[bool] = []
    touch = threading.Thread(
        target=lambda: result.append(legacy_names.touch_sync(role="api")), daemon=True
    )
    holder = get_sync_engine().connect()
    tx = holder.begin()
    try:
        holder.execute(text("LOCK TABLE dw_legacy_name_uses IN ACCESS EXCLUSIVE MODE"))
        touch.start()
        touch.join(30)
        assert not touch.is_alive(), "the touch still waits behind the lock"
        assert result == [False]
    finally:
        tx.rollback()
        holder.close()
    assert [e["error"] for e in write_errors()] == ["OperationalError"]
