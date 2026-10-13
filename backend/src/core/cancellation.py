# Origin: miStudio (Onegaishimas/miStudio) backend/src/core/cancellation.py @ c829a2cc
# Mode: adapt (docs/REUSE.md). miStudio's module serves nineteen tables with four terminal
# vocabularies; miDataworks has one job table (ADR-007), so the scope registry shrinks to one
# scope and the vocabulary is written once in models/job.py. Kept from the origin, with their
# reasons: OperatorCancelled derives from BaseException; the checker polls on TIME with the first
# call always polling; the terminal guard; cooperative_cancel returning (which acks acks_late).
# Added: the 60 s time throttle on record_progress itself, the heartbeat column it writes, and a
# pure plan_cancel() decision shared by the async API path and the sync worker path.
"""Cooperative cancellation and the progress heartbeat for ``dw_jobs`` (ADR-007; tasks 5.3, 5.4).

WHY NOT ``revoke(terminate=True)``. miStudio verified on hardware that ``terminate`` signals a
pool child a solo worker does not have, and that a busy solo worker never reads the control
queue, so the revoke returns cleanly and changes nothing. SIGKILL is not the fallback either: it
stranded an ``acks_late`` message for the full visibility timeout. So the API writes the request
to the row and the task polls the row at a checkpoint it chooses.

THE GUARD IS NOT OPTIONAL. Between the API writing the request and the task noticing it, the task
is still reporting progress. Without :func:`record_progress`'s terminal guard its next write
would overwrite the cancellation, and the operator would be told it worked.

THE HEARTBEAT IS IN THE DATABASE, ON A TIME THROTTLE. miStudio's janitor reaped a live 5.8-hour
job whose phase reported only over WebSocket. :func:`record_progress` writes ``heartbeat_at`` at
most once per ``PROGRESS_HEARTBEAT_SECONDS`` (60 s) for progress-only calls; status changes always
write. A throttle on a call count is wrong in both directions — milliseconds for a fast loop,
twenty minutes for a slow one — so it is time.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock

logger = logging.getLogger(__name__)

#: The one cancel scope: a row in ``dw_jobs``. Job kinds name it (core/job_kinds.py).
JOB_SCOPE = "dw_jobs"
SCOPES: frozenset[str] = frozenset({JOB_SCOPE})

#: The checker reads the database at most this often.
DEFAULT_POLL_INTERVAL_S = 2.0

LIVE = frozenset({"queued", "running"})
CANCELLING = "cancelling"
TERMINAL = frozenset({"cancelled", "completed", "failed"})


class OperatorCancelled(BaseException):  # noqa: N818 - the name states what happened, not an error
    """The operator asked this job to stop. Not an error.

    Derives from ``BaseException`` deliberately: a broad ``except Exception`` in task code, a
    progress callback or a library cannot swallow it and record the job as failed. Cleanup that
    must run on cancellation belongs in ``finally``.
    """

    def __init__(
        self,
        job_id: str,
        reason: str = "cancelled",
        detail: str = "",
        result: dict[str, Any] | None = None,
    ) -> None:
        self.job_id = job_id
        self.reason = reason
        self.detail = detail
        #: What the job kept, recorded on the row with the cancelled status.
        self.result = result
        super().__init__(f"{job_id} {reason}" + (f": {detail}" if detail else ""))


# --------------------------------------------------------------------------------------------
# The rules, as pure functions
# --------------------------------------------------------------------------------------------


def guard_allows(current: str | None, incoming: str | None, *, writes_progress: bool) -> bool:
    """Would a write of this shape be accepted onto a row in state ``current``?

    - live row (queued, running): anything;
    - cancelling: a terminal status or progress, never a step back to queued or running;
    - terminal: nothing but a message (where it stopped). Terminal states are never left except
      by an explicit retry, which creates a new job (ADR-007).
    """
    if current in LIVE or current is None:
        return True
    if current == CANCELLING:
        return incoming is None or incoming in TERMINAL
    # terminal
    return incoming is None and not writes_progress


@dataclass(frozen=True)
class CancelPlan:
    """What a cancel request does to a row in a given state."""

    allowed: bool
    new_status: str | None
    set_requested: bool
    detail: str


def plan_cancel(status: str) -> CancelPlan:
    """The cancel decision, shared by the API (async) and worker-side callers (sync)."""
    if status == "queued":
        return CancelPlan(True, "cancelled", True, "The job had not started; it will not run.")
    if status == "running":
        return CancelPlan(
            True,
            CANCELLING,
            True,
            "Cancellation requested. The job stops at its next checkpoint and keeps what it "
            "has written.",
        )
    if status == CANCELLING:
        return CancelPlan(True, None, False, "Cancellation was already requested.")
    return CancelPlan(
        False, None, False, f"The job is already {status}; there is nothing to cancel."
    )


def row_requests_cancel(status: str | None, cancel_requested_at: Any) -> bool:
    """Does this row tell its task to stop?"""
    return cancel_requested_at is not None or status in {CANCELLING, "cancelled"}


# --------------------------------------------------------------------------------------------
# Reading: the checker
# --------------------------------------------------------------------------------------------


def _fetch(session: Session, job_id: str) -> Any:
    from ..models.job import Job

    # populate_existing(): a long-lived task session must see the API's write, not the row as
    # it looked when the task started (miStudio MIS-E2E-057).
    return session.execute(
        select(Job).where(Job.id == job_id).execution_options(populate_existing=True)
    ).scalar_one_or_none()


class CancelCheck:
    """Poll the job row, throttled on time. Call it at the finest boundary you can abandon."""

    def __init__(
        self, job_id: str, *, db: Session | None = None, min_interval_s: float | None = None
    ) -> None:
        self.job_id = job_id
        self._db = db
        self._interval = DEFAULT_POLL_INTERVAL_S if min_interval_s is None else min_interval_s
        self._calls = 0
        self._last_poll = clock.monotonic()
        self._cancelled = False
        self.reason: str | None = None

    def _poll(self) -> bool:
        self._last_poll = clock.monotonic()
        try:
            if self._db is not None:
                row = _fetch(self._db, self.job_id)
            else:
                from .database import get_sync_db

                with get_sync_db() as session:
                    row = _fetch(session, self.job_id)
        except Exception as exc:  # noqa: BLE001 - a failed poll must not kill the work
            logger.warning("Cancel poll failed for job %s: %s", self.job_id, exc)
            return False
        if row is None:
            self._cancelled, self.reason = True, "deleted"
        elif row_requests_cancel(row.status, row.cancel_requested_at):
            self._cancelled, self.reason = True, "cancelled"
        return self._cancelled

    def __call__(self) -> bool:
        if self._cancelled:
            return True
        first = self._calls == 0
        self._calls += 1
        # The first call always polls: a job cancelled before it started must not run to its
        # Nth checkpoint before noticing.
        if not first and clock.monotonic() - self._last_poll < self._interval:
            return False
        return self._poll()

    def poll_now(self) -> bool:
        """Ignore the throttle, before an expensive step that cannot be abandoned midway."""
        return self._cancelled or self._poll()

    def raise_if_cancelled(self, detail: str = "", result: dict[str, Any] | None = None) -> None:
        if self():
            raise OperatorCancelled(self.job_id, self.reason or "cancelled", detail, result)


# --------------------------------------------------------------------------------------------
# Writing: record_progress
# --------------------------------------------------------------------------------------------

#: job id -> monotonic time of its last written heartbeat, in this process.
_last_write: dict[str, float] = {}


def _interval_s() -> float:
    from .config import get_settings

    return get_settings().progress_heartbeat_seconds


def reset_throttle(job_id: str | None = None) -> None:
    """Forget throttle state (tests, and a task that restarts a job in the same process)."""
    if job_id is None:
        _last_write.clear()
    else:
        _last_write.pop(job_id, None)


def record_progress(
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    error: str | None = None,
    result: dict[str, Any] | None = None,
    force: bool = False,
    db: Session | None = None,
) -> bool:
    """Write progress and ``heartbeat_at``, refusing to move a terminal row. Returns "written".

    Progress-only calls are throttled on elapsed time (``PROGRESS_HEARTBEAT_SECONDS``): within the
    interval since this job's last write they write nothing. A status change, an error, a result
    or ``force=True`` always writes. The first call for a job always writes.
    """
    from ..models.job import Job

    always = force or status is not None or error is not None or result is not None
    if not always:
        last = _last_write.get(job_id)
        if last is not None and clock.monotonic() - last < _interval_s():
            return False

    def _apply(session: Session) -> bool:
        row: Job | None = _fetch(session, job_id)
        if row is None:
            logger.warning("Job %s not found for a progress write", job_id)
            return False
        writes_progress = progress is not None or result is not None
        if not guard_allows(row.status, status, writes_progress=writes_progress):
            logger.info(
                "Ignoring %s write for job %s: the row is already %s",
                status or "progress",
                job_id,
                row.status,
            )
            return False
        now = clock.utc_now()
        if status is not None:
            if status == "running" and row.started_at is None:
                row.started_at = now
            if status in TERMINAL and row.completed_at is None:
                row.completed_at = now
            row.status = status
        if progress is not None:
            row.progress = max(0.0, min(100.0, float(progress)))
        if message is not None:
            row.message = message[:2000]
        if error is not None:
            row.error = error[:4000]
        if result is not None:
            row.result = result
        if row.status not in TERMINAL or status is not None:
            row.heartbeat_at = now
        session.commit()
        _last_write[job_id] = clock.monotonic()
        return True

    try:
        if db is not None:
            return _apply(db)
        from .database import get_sync_db

        with get_sync_db() as session:
            return _apply(session)
    except Exception as exc:  # noqa: BLE001 - narration must not break the work
        logger.warning("Could not record progress for job %s: %s", job_id, exc)
        return False


# --------------------------------------------------------------------------------------------
# The task boundary
# --------------------------------------------------------------------------------------------

P = ParamSpec("P")
R = TypeVar("R")


def cooperative_cancel(fn: Callable[P, R]) -> Callable[P, R | dict[str, Any]]:
    """Turn :class:`OperatorCancelled` into a returned result and mark the job cancelled.

    Returning (not raising) acks the ``acks_late`` message. The worker's own write moves
    ``cancelling`` to ``cancelled``; :func:`record_progress` refuses to touch a row that is
    already terminal.
    """

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | dict[str, Any]:
        try:
            return fn(*args, **kwargs)
        except OperatorCancelled as cancelled:
            logger.info("Job %s stopped by the operator (%s)", cancelled.job_id, cancelled.reason)
            record_progress(
                cancelled.job_id,
                status="cancelled",
                message=cancelled.detail or "Cancelled by the operator.",
                result=cancelled.result,
            )
            return {"status": "cancelled", "job_id": cancelled.job_id, "reason": cancelled.reason}

    wrapper.__cooperative_cancel__ = True  # type: ignore[attr-defined]
    return wrapper


# --------------------------------------------------------------------------------------------
# Inside code with no callback: the watchdog (001 FTASKS 3.5)
# --------------------------------------------------------------------------------------------
# Origin: miStudio (Onegaishimas/miStudio) backend/src/core/cancellation.py @ c829a2cc
# (class CancelWatchdog). Mode: adapt (docs/REUSE.md, the module's existing row). Kept: a daemon
# thread polls and raises OperatorCancelled in the calling thread through
# PyThreadState_SetAsyncExc; never injected after the block exits. Added: ``on_tick``, called each
# interval before the poll (the import's cache-size heartbeat); a failing on_tick is logged and
# never stops the watchdog.


class CancelWatchdog:
    """Stop the enclosed block when its job is cancelled — inside code that offers no callback.

    ``datasets.load_dataset`` is one blocking call; miStudio found its tqdm hook never reached the
    bars that tick, and a cancelled 313.7 GB download ran on for 30 minutes. The exception surfaces
    at the next Python bytecode, so ``finally`` blocks run and the worker survives.
    """

    def __init__(
        self,
        job_id: str,
        *,
        interval_s: float = DEFAULT_POLL_INTERVAL_S,
        on_tick: Callable[[], None] | None = None,
        checker: CancelCheck | None = None,
    ) -> None:
        self._job_id = job_id
        self._interval_s = interval_s
        self._on_tick = on_tick
        self._check = checker if checker is not None else CancelCheck(job_id, min_interval_s=0.0)
        self._armed = False
        self._lock: Any = None
        self._stop: Any = None
        self._thread: Any = None

    def __enter__(self) -> CancelWatchdog:
        import threading

        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._armed = True
        self._thread = threading.Thread(
            target=self._watch,
            args=(threading.get_ident(),),
            name=f"cancel-watchdog:{self._job_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _watch(self, target_thread: int) -> None:
        import ctypes

        while not self._stop.wait(self._interval_s):
            if self._on_tick is not None:
                try:
                    self._on_tick()
                except Exception as exc:  # noqa: BLE001 - a heartbeat must not stop the watchdog
                    logger.warning("cancel watchdog tick failed for job %s: %s", self._job_id, exc)
            if not self._check.poll_now():
                continue
            with self._lock:
                if not self._armed:
                    return
                self._armed = False
                job_id, reason = self._job_id, self._check.reason or "cancelled"

                def _init(
                    exc: OperatorCancelled, job_id: str = job_id, reason: str = reason
                ) -> None:
                    OperatorCancelled.__init__(
                        exc, job_id, reason, "stopped by the cancel watchdog"
                    )

                # CPython instantiates the class with no arguments at delivery, so the job and
                # reason are bound into a subclass here.
                exc_type = type("CancelledByWatchdog", (OperatorCancelled,), {"__init__": _init})
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(target_thread), ctypes.py_object(exc_type)
                )
            return

    def __exit__(self, *exc_info: Any) -> None:
        with self._lock:
            self._armed = False
        self._stop.set()
