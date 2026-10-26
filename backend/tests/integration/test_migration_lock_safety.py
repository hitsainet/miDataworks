# Origin: miStudio (Onegaishimas/miStudio) backend/tests/unit/test_migration_lock_safety.py @ 67b9ab1f
# Mode: adapt (docs/REUSE.md). The incident replay is the same; the migration replayed is this
# repo's own 0031 (ALTER TABLE dw_endpoint_roles ADD COLUMN), the budget reads this repo's api
# container and its ConfigMap, and the runner is `python -m src.core.migrate`.
"""A migration blocked on a lock fails fast, names its blocker, and leaves nothing queued.

miStudio's INCIDENT of 2026-10-21 (05:05-05:16 ET), whose exposure miDataworks shared: the
entrypoint's bare ``alembic upgrade`` reached an ``ALTER TABLE … ADD COLUMN`` while a hung,
two-day-old COPY held ACCESS SHARE on that table. With no ``lock_timeout`` the ALTER waited
forever; the startup probe SIGKILLed the container twice, and each killed attempt's ALTER stayed
queued in Postgres behind the COPY — and in front of every later reader of the table.

These tests replay that against the REAL PostgreSQL 15 of the suite (a scratch database per test,
dropped WITH (FORCE) afterwards), through the real ``alembic/env.py`` and the real
``python -m src.core.migrate`` the entrypoint runs. The migration is this repo's 0031, which opens
with ``ALTER TABLE dw_endpoint_roles ADD COLUMN api_key_origin`` (ACCESS EXCLUSIVE); the blocker is
a second session holding ACCESS SHARE on that table in an open transaction, exactly the COPY's lock.

MUTATION CONTROLS (2026-10-21; each alone, restored from saved bytes, sha256-checked) — all red;
the record is 0xcc/reviews/migrate_safely_2026-10-21.md:
  M1 lock_timeout set to "0" (disabled) in apply_session_guards
  M2 client_connection_check_interval not set
  M3 env.py installs no guards (reads its pid and commits only)
  M4 a lock timeout is not retryable
  M5 the watch never records blockers
  M6 the deadline cancel removed from the watch
  M7 startupProbe failureThreshold lowered to 20 (200 s)
  M8 statement_timeout not set
  M9 idle_in_transaction_session_timeout not set
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy.engine import make_url

from src.core import migration_safety as ms

BACKEND = Path(__file__).resolve().parents[2]
MANIFESTS = BACKEND.parent / "k8s" / "base"
#: The revision before the replayed migration, and the replayed migration.
BEFORE = "0030"
BLOCKED = "0031"
BLOCKED_TABLE = "dw_endpoint_roles"
BLOCKED_COLUMN = "api_key_origin"


def _server_url() -> Any:
    return make_url(os.environ["DATABASE_URL"])


def _connect(dbname: str) -> Any:
    import psycopg2

    u = _server_url()
    conn = psycopg2.connect(
        host=u.host or "localhost",
        port=u.port or 5432,
        user=u.username,
        password=u.password,
        dbname=dbname,
        connect_timeout=5,
    )
    conn.autocommit = True
    return conn


@pytest.fixture
def scratch_db() -> Iterator[str]:
    name = f"mig_lock_test_{uuid.uuid4().hex[:12]}"
    admin = _connect(_server_url().database)
    with admin.cursor() as cur:
        cur.execute(f'CREATE DATABASE "{name}"')
    try:
        yield name
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()


def _env(dbname: str, **extra: object) -> dict[str, str]:
    env = dict(os.environ)
    env["DATABASE_URL"] = _server_url().set(database=dbname).render_as_string(hide_password=False)
    env["PYTHONUNBUFFERED"] = "1"
    for name in (*ms.DEFAULTS, "MIGRATION_DEADLINE_AT"):
        env.pop(name, None)
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _alembic(dbname: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=_env(dbname),
        capture_output=True,
        text=True,
        timeout=240,
    )


def _migrate(
    dbname: str, timeout: float, **extra: object
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-m", "src.core.migrate", "upgrade"],
        cwd=BACKEND,
        env=_env(dbname, **extra),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result, time.monotonic() - started


def _version(conn: Any) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        return {row[0] for row in cur.fetchall()}


def _migration_sessions(conn: Any, dbname: str) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pid, state, wait_event_type, query FROM pg_stat_activity "
            "WHERE datname = %s AND application_name = %s",
            (dbname, ms.APPLICATION_NAME),
        )
        return list(cur.fetchall())


def _head() -> str:
    from alembic.script import ScriptDirectory

    from src.core.migrate import DEFAULT_INI, _config

    (head,) = ScriptDirectory.from_config(_config(DEFAULT_INI)).get_heads()
    return str(head)


@pytest.fixture
def at_the_blocked_migration(scratch_db: str) -> Iterator[tuple[str, Any, int, Any]]:
    """A scratch database one revision before 0031's ALTER, and a session holding ACCESS SHARE on
    the table that ALTER needs, as miStudio's hung COPY did."""
    result = _alembic(scratch_db, "upgrade", BEFORE)
    assert result.returncode == 0, result.stdout + result.stderr
    blocker = _connect(scratch_db)
    blocker.autocommit = False
    with blocker.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {BLOCKED_TABLE}")  # noqa: S608 — a constant
        cur.execute("SELECT pg_backend_pid()")
        pid = cur.fetchone()[0]
    observer = _connect(scratch_db)
    try:
        yield scratch_db, blocker, pid, observer
    finally:
        try:
            blocker.rollback()
            blocker.close()
        finally:
            observer.close()


class TestTheIncidentReplayed:
    def test_a_blocked_migration_gives_up_names_the_blocker_and_then_succeeds(
        self, at_the_blocked_migration: tuple[str, Any, int, Any]
    ) -> None:
        dbname, blocker, blocker_pid, observer = at_the_blocked_migration
        # Budget: 2 attempts x 2 s lock_timeout + 1 s backoff. Without lock_timeout this run
        # would wait until the 150 s statement_timeout; the 60 s subprocess timeout turns that
        # into a failure rather than a hung suite.
        result, elapsed = _migrate(
            dbname,
            timeout=60,
            MIGRATION_LOCK_TIMEOUT="2s",
            MIGRATION_ATTEMPTS=2,
            MIGRATION_RETRY_BACKOFF="1s",
        )
        out = result.stdout + result.stderr
        assert result.returncode == 3, out
        assert elapsed < 30, f"took {elapsed:.1f}s for a ~5s budget"
        assert out.count("gave up waiting for a lock") >= 2, "the lock timeout was not retried"
        assert f"pid={blocker_pid} " in out, f"the blocker (pid {blocker_pid}) is not named:\n{out}"
        assert "WAITING FOR A LOCK" in out, "the blocker was not logged during the wait"
        # The values the SERVER accepted for the migration session (set_config's own result).
        for applied in (
            "lock_timeout=2s",
            "statement_timeout=150s",
            "idle_in_transaction_session_timeout=5min",
            "client_connection_check_interval=2s",
        ):
            assert applied in out, f"{applied} was not set on the migration session:\n{out}"
        assert "pg_terminate_backend" in out and "GIVING UP" in out
        assert _version(observer) == {BEFORE}, "a blocked run must apply nothing"
        assert _migration_sessions(observer, dbname) == [], "a migration statement was left queued"

        blocker.rollback()  # the COPY finishes (or the operator terminates it)
        result, _ = _migrate(dbname, timeout=120, MIGRATION_LOCK_TIMEOUT="2s")
        assert result.returncode == 0, result.stdout + result.stderr
        assert _version(observer) == {_head()}
        with observer.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (BLOCKED_TABLE, BLOCKED_COLUMN),
            )
            assert cur.fetchone(), "0031's column was not added once the lock was free"

    def test_a_killed_migration_leaves_no_waiter_behind(
        self, at_the_blocked_migration: tuple[str, Any, int, Any]
    ) -> None:
        """The startup probe's SIGKILL must not leave the ALTER in the lock queue (miStudio,
        2026-10-21: it did, twice). client_connection_check_interval reaps it long before
        lock_timeout would."""
        dbname, _blocker, _pid, observer = at_the_blocked_migration
        proc = subprocess.Popen(
            [sys.executable, "-m", "src.core.migrate", "upgrade"],
            cwd=BACKEND,
            env=_env(dbname, MIGRATION_LOCK_TIMEOUT="120s", MIGRATION_ATTEMPTS=1),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 60
            while not any(row[2] == "Lock" for row in _migration_sessions(observer, dbname)):
                assert proc.poll() is None, "the migration exited instead of waiting on the lock"
                assert time.monotonic() < deadline, "the migration never queued on the lock"
                time.sleep(0.2)
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)
            killed = time.monotonic()
            while _migration_sessions(observer, dbname):
                assert time.monotonic() - killed < 15, (
                    "the killed migration's statement is still queued: "
                    f"{_migration_sessions(observer, dbname)}"
                )
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                proc.kill()

    def test_the_deadline_cancels_a_run_server_side(
        self, at_the_blocked_migration: tuple[str, Any, int, Any]
    ) -> None:
        dbname, _blocker, _pid, observer = at_the_blocked_migration
        result, elapsed = _migrate(
            dbname,
            timeout=60,
            MIGRATION_LOCK_TIMEOUT="120s",
            MIGRATION_ATTEMPTS=1,
            MIGRATION_DEADLINE="3s",
        )
        out = result.stdout + result.stderr
        assert result.returncode == 4, out
        assert "passed its deadline" in out and elapsed < 30, out
        assert _migration_sessions(observer, dbname) == []

    def test_a_long_statement_is_cancelled_and_reported(
        self, at_the_blocked_migration: tuple[str, Any, int, Any]
    ) -> None:
        dbname, _blocker, _pid, _observer = at_the_blocked_migration
        result, _ = _migrate(
            dbname,
            timeout=60,
            MIGRATION_LOCK_TIMEOUT="120s",
            MIGRATION_STATEMENT_TIMEOUT="1s",
            MIGRATION_ATTEMPTS=3,
        )
        out = result.stdout + result.stderr
        assert result.returncode == 4, out
        assert "statement_timeout=1s" in out and "MIGRATION_STATEMENT_TIMEOUT=0" in out
        assert out.count("Running upgrade") == 1, "a statement timeout must not be retried"


class TestTheSessionGuards:
    def test_the_guards_hold_on_a_real_session(self, scratch_db: str) -> None:
        """Each guard is a real server setting on the session, and the idle one ends a transaction
        that stops talking (a migration process that hangs mid-transaction cannot keep its
        locks)."""
        from sqlalchemy import create_engine, text
        from sqlalchemy.exc import DBAPIError

        url = _server_url().set(drivername="postgresql+psycopg2", database=scratch_db)
        engine = create_engine(url)
        settings = ms.MigrationSettings(
            lock_timeout="3s",
            statement_timeout="7s",
            idle_timeout="1s",
            client_check_interval="2s",
            attempts=1,
            retry_backoff_s=0,
            deadline_s=10,
        )
        try:
            with engine.connect() as conn:
                guards = ms.apply_session_guards(conn, settings)
                shown = {
                    name: conn.execute(text(f"SHOW {name}")).scalar_one()
                    for name in (
                        "application_name",
                        "lock_timeout",
                        "statement_timeout",
                        "idle_in_transaction_session_timeout",
                        "client_connection_check_interval",
                    )
                }
                assert shown == {
                    "application_name": ms.APPLICATION_NAME,
                    "lock_timeout": "3s",
                    "statement_timeout": "7s",
                    "idle_in_transaction_session_timeout": "1s",
                    "client_connection_check_interval": "2s",
                }, shown
                assert guards.backend_pid > 0
                conn.commit()
                conn.execute(text("SELECT 1"))  # opens a transaction, then goes quiet
                time.sleep(2.5)
                with pytest.raises(DBAPIError):
                    conn.execute(text("SELECT 1"))
        finally:
            engine.dispose()


class TestTheBudget:
    def _docs(self, name: str) -> list[dict[str, Any]]:
        return [d for d in yaml.safe_load_all((MANIFESTS / name).read_text()) if d]

    def _api_container(self) -> dict[str, Any]:
        (deployment,) = [
            d
            for d in self._docs("backend.yaml")
            if d.get("kind") == "Deployment" and d["metadata"]["name"] == "midataworks-backend"
        ]
        (api,) = [
            c
            for c in deployment["spec"]["template"]["spec"]["containers"]
            if {e["name"]: e.get("value") for e in c.get("env", [])}.get("SERVICE_TYPE") == "api"
        ]
        return dict(api)

    def test_the_startup_probe_outlasts_the_whole_migration_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The migration must report and exit before the kubelet SIGKILLs it: the deadline (which
        bounds everything, retries included) plus room for imports and uvicorn's boot must fit in
        the startup probe's window. Read with the manifests' own MIGRATION_* values applied — the
        api container's ConfigMap first, its own env over it, as Kubernetes resolves them."""
        api = self._api_container()
        for name in ms.DEFAULTS:
            monkeypatch.delenv(name, raising=False)
        maps = {
            d["metadata"]["name"]: d.get("data") or {}
            for d in self._docs("config.yaml")
            if d.get("kind") == "ConfigMap"
        }
        for source in api.get("envFrom", []):
            for key, value in maps[source["configMapRef"]["name"]].items():
                if key.startswith("MIGRATION_"):
                    monkeypatch.setenv(key, str(value))
        for env in api.get("env", []):
            if env["name"].startswith("MIGRATION_") and "value" in env:
                monkeypatch.setenv(env["name"], env["value"])
        settings = ms.settings_from_env()
        probe = api["startupProbe"]
        window = probe.get("periodSeconds", 10) * probe.get("failureThreshold", 3) + probe.get(
            "initialDelaySeconds", 0
        )
        assert window >= settings.deadline_s + 90, (window, settings.deadline_s)
        assert settings.statement_timeout_s < settings.deadline_s
        assert (
            settings.lock_retry_budget_s() <= settings.deadline_s
        ), "the retries cannot all run inside the deadline"
        assert settings.attempts >= 2, "a single attempt cannot ride out a short-lived lock"


class TestParsing:
    @pytest.mark.parametrize(
        "value, seconds", [("15s", 15), ("500ms", 0.5), ("4min", 240), ("2000", 2), ("0", 0)]
    )
    def test_postgres_durations(self, value: str, seconds: float) -> None:
        assert ms.parse_duration(value) == pytest.approx(seconds)

    def test_a_malformed_setting_is_refused_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIGRATION_LOCK_TIMEOUT", "15 seconds; DROP TABLE x")
        with pytest.raises(ValueError, match="not a duration"):
            ms.settings_from_env()

    def test_the_sqlstate_is_found_through_the_wrapping(self) -> None:
        class Inner(Exception):
            pgcode = "55P03"

        class Wrapped(Exception):
            def __init__(self) -> None:
                super().__init__("wrapped")
                self.orig = Exception("adapter")
                self.orig.__cause__ = Inner()

        assert ms.sqlstate_of(Wrapped()) == ms.LOCK_NOT_AVAILABLE
        assert ms.sqlstate_of(ValueError("no sql")) is None
