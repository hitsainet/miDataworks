"""Evidence of old sibling environment names in use (feature 020, part B; FTID §3.3, FTDD §6.2).

P7 retires an old name (``MILLM_BASE_URL`` …) only once no miDataworks process has read it since
the suite's P7 sweep (R31, 2026-11-12; it was 7 quiet days). A process reports only on itself, and the API, the workers and beat are separate
processes, so each one WRITES its own uses to ``dw_legacy_name_uses`` and health READS the table:

- **Touch points** (each a wiring line with its own negative control, FTID §7.2): T1 the API start
  (``main.lifespan``), T2 every ``GET /api/health``, T3 a worker child's start
  (``worker_process_init``), T4 every task (``task_prerun``), T5 an idle worker's heartbeat
  (``worker_ready``), T6 beat's start and heartbeat (``beat_init``).
- **What is written:** one upsert per old name this process read (``env_names.current_report()``),
  ``last_seen_at = GREATEST(stored, now)`` so a slow writer never moves the evidence back, ``count``
  incremented. Throttled in memory to once a minute per name; the stamp is set on the ATTEMPT, so a
  store that hangs or fails is tried at most once a minute (the heartbeat retries), and the touch
  engine connects and runs each statement under the health probe bound.
- **Names only, never a value.** Nothing here reads a setting's value.
- **A failed write is not silence** (CF-25 m2): it is logged (exception TYPE only) and recorded in
  ``dw_legacy_name_write_errors`` under ``<role>@<host>``; health then sets
  ``legacy_names_meta.error``, so S001's sampler treats the day as UNMEASURED. The next success
  clears the row. A process that can reach no store at all only logs (tracked debt, FTDD §6.2).
- **Never raises.** Evidence never fails a task, the API start or health.
- **Own engine with ``NullPool``** (ID-16): the heartbeat thread lives in the prefork parent, and a
  child forked later must not inherit a pooled connection.

``block()`` is the read side for health: ``legacy_names`` with exactly BR-023's four keys and
``legacy_names_meta`` with everything else (CF-4).
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Engine, Table, create_engine, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.pool import NullPool

from . import env_names
from .config import get_settings

logger = logging.getLogger(__name__)

THROTTLE_S = 60.0
HEARTBEAT_S = 60.0
IN_USE_WINDOW = timedelta(hours=24)
WRITE_ERROR_WINDOW = timedelta(hours=24)
KIND = "env"

#: Test seams (monkeypatched, never reassigned in production code).
_clock: Callable[[], float] = time.monotonic
_now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731
#: The heartbeat's wait between ticks: returns True to stop. None → a fresh Event's ``wait``.
_heartbeat_wait: Callable[[float], bool] | None = None

_last_touch: dict[str, float] = {}
_heartbeat: threading.Thread | None = None
_heartbeat_lock = threading.Lock()
_engine: tuple[int, Engine] | None = None
_process_started_at: datetime = datetime.now(UTC)


def _own_engine() -> Engine:
    """This process's NullPool engine (rebuilt after a fork: the pid is part of the key)."""
    global _engine
    pid = os.getpid()
    if _engine is None or _engine[0] != pid:
        # Bounded like every other health probe: a hung Postgres cannot hold a touch thread
        # (review 2026-11-08). connect_timeout is whole seconds; statement_timeout milliseconds.
        bound = max(1, int(round(get_settings().health_probe_timeout_seconds)))
        _engine = (
            pid,
            create_engine(
                get_settings().database_url_sync,
                poolclass=NullPool,
                connect_args={
                    "connect_timeout": bound,
                    "options": f"-c statement_timeout={bound * 1000}",
                },
            ),
        )
    return _engine[1]


def reporter(role: str) -> str:
    return f"{role}@{socket.gethostname()}"[:128]


def names_in_use_here() -> list[env_names.EnvRename]:
    """The renames whose OLD name supplied a value in this process's last settings load."""
    read = env_names.current_report().read
    return [row for row in env_names.RENAMES if read.get(row.field) == row.old]


def _upsert(engine: Engine, rows: list[env_names.EnvRename], now: datetime) -> None:
    from ..models.legacy_names import LegacyNameUse

    table = cast(Table, LegacyNameUse.__table__)
    with engine.begin() as conn:
        for row in rows:
            statement = insert(table).values(
                kind=KIND,
                old_name=row.old,
                new_name=row.new,
                first_seen_at=now,
                last_seen_at=now,
                count=1,
            )
            statement = statement.on_conflict_do_update(
                index_elements=[table.c.kind, table.c.old_name],
                set_={
                    "last_seen_at": func.greatest(
                        table.c.last_seen_at, statement.excluded.last_seen_at
                    ),
                    "count": table.c.count + 1,
                },
            )
            conn.execute(statement)


def _clear_error(engine: Engine, who: str, now: datetime) -> None:
    from ..models.legacy_names import LegacyNameWriteError

    table = cast(Table, LegacyNameWriteError.__table__)
    with engine.begin() as conn:
        conn.execute(
            update(table)
            .where(table.c.reporter == who, table.c.cleared_at.is_(None))
            .values(cleared_at=now)
        )


def _record_error(engine: Engine, who: str, error: str, now: datetime) -> None:
    from ..models.legacy_names import LegacyNameWriteError

    table = cast(Table, LegacyNameWriteError.__table__)
    statement = insert(table).values(reporter=who, error=error, failed_at=now, cleared_at=None)
    statement = statement.on_conflict_do_update(
        index_elements=[table.c.reporter],
        set_={"error": error, "failed_at": now, "cleared_at": None},
    )
    with engine.begin() as conn:
        conn.execute(statement)


def touch_sync(*, role: str, engine_factory: Callable[[], Engine] | None = None) -> bool:
    """Record this process's old-name uses. True when a row was written. Never raises."""
    try:
        due = [
            row
            for row in names_in_use_here()
            if _clock() - _last_touch.get(row.old, float("-inf")) >= THROTTLE_S
        ]
        if not due:
            return False
        # The stamp is set on ATTEMPT (review 2026-11-08): a store that hangs or fails is tried at
        # most once a minute per name, never once per health call; the heartbeat retries.
        attempt = _clock()
        for row in due:
            _last_touch[row.old] = attempt
        engine = (engine_factory or _own_engine)()
        now = _now()
        who = reporter(role)
        try:
            _upsert(engine, due, now)
        except Exception as exc:  # noqa: BLE001 - evidence never fails its caller
            kind = type(exc).__name__
            logger.warning("code=legacy_names_unwritable reporter=%s error=%s", who, kind)
            try:
                _record_error(engine, who, kind, now)
            except Exception as again:  # noqa: BLE001
                logger.warning(
                    "code=legacy_names_error_unwritable reporter=%s error=%s",
                    who,
                    type(again).__name__,
                )
            return False
        try:
            _clear_error(engine, who, now)
        except Exception as exc:  # noqa: BLE001
            logger.warning("code=legacy_names_clear_failed error=%s", type(exc).__name__)
        return True
    except Exception as exc:  # noqa: BLE001 - never raises, whatever failed
        logger.warning("code=legacy_names_touch_failed error=%s", type(exc).__name__)
        return False


# --- Celery signal handlers (connected in core/celery_app.py; weak=False) --------------------


def on_worker_process_init(**_: Any) -> None:
    """T3: a worker child starts."""
    touch_sync(role="worker")


def on_task_prerun(**_: Any) -> None:
    """T4: every task."""
    touch_sync(role="worker")


def on_worker_ready(**_: Any) -> None:
    """T5: the worker (prefork parent) is ready: refresh once a minute while it idles."""
    try:
        start_heartbeat(role="worker")
    except Exception as exc:  # noqa: BLE001
        logger.warning("code=legacy_names_heartbeat_failed error=%s", type(exc).__name__)


def on_beat_init(**_: Any) -> None:
    """T6: beat starts: touch now, then once a minute."""
    try:
        touch_sync(role="beat")
        start_heartbeat(role="beat")
    except Exception as exc:  # noqa: BLE001
        logger.warning("code=legacy_names_heartbeat_failed error=%s", type(exc).__name__)


def _loop(role: str, interval: float, wait: Callable[[float], bool]) -> None:
    while True:
        touch_sync(role=role)
        if wait(interval):
            return


def start_heartbeat(*, role: str, interval: float | None = None) -> threading.Thread:
    """Start this process's one heartbeat thread (``legacy-names-heartbeat``); at most one."""
    global _heartbeat
    with _heartbeat_lock:
        if _heartbeat is not None and _heartbeat.is_alive():
            return _heartbeat
        wait = _heartbeat_wait or threading.Event().wait
        thread = threading.Thread(
            target=_loop,
            args=(role, HEARTBEAT_S if interval is None else interval, wait),
            name="legacy-names-heartbeat",
            daemon=True,
        )
        thread.start()
        _heartbeat = thread
        return thread


def reset() -> None:
    """Forget the in-memory throttle and heartbeat (tests: a "restart")."""
    global _heartbeat
    _last_touch.clear()
    _heartbeat = None


# --- the read side, for GET /api/health ---------------------------------------------------------


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _pairs(rows: tuple[env_names.EnvRename, ...]) -> list[dict[str, str]]:
    return [{"old": row.old, "new": row.new} for row in rows]


async def block(db: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(legacy_names, legacy_names_meta)`` for health. Never raises.

    ``legacy_names`` has exactly ``in_use``, ``also_set``, ``conflicts``, ``retired_but_set``
    (BR-023, CF-4). ``in_use`` is every row seen in the last 24 hours plus every old name this
    process reads now, newest first, each entry exactly ``kind, old, new, last_seen``. A failed
    read leaves ``in_use`` with this process's own reads and sets ``legacy_names_meta.error``.
    """
    from ..models.legacy_names import LegacyNameUse, LegacyNameWriteError

    now = _now()
    report = env_names.current_report()
    entries: dict[str, dict[str, Any]] = {}
    error: str | None = None
    observing_since: datetime | None = None
    try:
        rows = (
            await db.execute(
                select(
                    LegacyNameUse.kind,
                    LegacyNameUse.old_name,
                    LegacyNameUse.new_name,
                    LegacyNameUse.last_seen_at,
                ).where(LegacyNameUse.last_seen_at > now - IN_USE_WINDOW)
            )
        ).all()
        for kind, old, new, last_seen in rows:
            entries[old] = {"kind": kind, "old": old, "new": new, "last_seen": last_seen}
        observing_since = (await db.execute(select(func.min(LegacyNameUse.first_seen_at)))).scalar()
        failures = (
            await db.execute(
                select(LegacyNameWriteError.reporter, LegacyNameWriteError.error)
                .where(
                    LegacyNameWriteError.cleared_at.is_(None),
                    LegacyNameWriteError.failed_at > now - WRITE_ERROR_WINDOW,
                )
                .order_by(LegacyNameWriteError.reporter)
            )
        ).all()
        if failures:
            error = "; ".join(f"write failed in {who}: {kind}" for who, kind in failures)
    except Exception as exc:  # noqa: BLE001 - health never raises
        error = f"{type(exc).__name__}: cannot read dw_legacy_name_uses"
        entries = {}
    for row in names_in_use_here():
        entries[row.old] = {"kind": KIND, "old": row.old, "new": row.new, "last_seen": now}
    in_use = sorted(entries.values(), key=lambda e: (e["last_seen"], e["old"]), reverse=True)
    names = {
        "in_use": [
            {"kind": e["kind"], "old": e["old"], "new": e["new"], "last_seen": _iso(e["last_seen"])}
            for e in in_use
        ],
        "also_set": _pairs(report.also_set),
        "conflicts": _pairs(report.conflicts),
        "retired_but_set": list(report.retired_but_set),
    }
    meta = {
        "error": error,
        "observing_since": _iso(observing_since or _process_started_at),
        "in_use_window_hours": int(IN_USE_WINDOW.total_seconds() // 3600),
        "watching": {"env": sum(1 for row in env_names.RENAMES if not row.retired)},
    }
    return names, meta


def unreadable(reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The block when the read could not even start (a timeout): own reads, error set."""
    report = env_names.current_report()
    now = _now()
    names = {
        "in_use": [
            {"kind": KIND, "old": row.old, "new": row.new, "last_seen": _iso(now)}
            for row in names_in_use_here()
        ],
        "also_set": _pairs(report.also_set),
        "conflicts": _pairs(report.conflicts),
        "retired_but_set": list(report.retired_but_set),
    }
    meta = {
        "error": reason,
        "observing_since": _iso(_process_started_at),
        "in_use_window_hours": int(IN_USE_WINDOW.total_seconds() // 3600),
        "watching": {"env": sum(1 for row in env_names.RENAMES if not row.retired)},
    }
    return names, meta
