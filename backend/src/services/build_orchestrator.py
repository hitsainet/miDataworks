"""The build orchestrator: ``advance_build``'s body (FR-002.6, 002.28, 002.35, 002.36; FTID 002 §3.5).

``advance(session, job_id)`` does as much synchronous work as it can and returns when it has
dispatched a step to feature 003 or is waiting on one. It is IDEMPOTENT: every call re-reads the
job, the build and the step executions, so a redelivered Celery message, a link callback and a
janitor re-kick all do the right thing.

One pass:
1. claim the job (queued -> running) or honour a cancel request;
2. verify every input file against its recorded hash, once (FR-002.7);
3. for each step index from the last linked one:
   - index 0 is ``assemble``, run inline;
   - compute the step's identity; a completed execution with that identity is REUSED;
   - an execution another build is computing is WAITED on (that build re-kicks this one);
   - this build's own finished execution is INGESTED (accounting, key check, events);
   - otherwise the step is DISPATCHED to 003 with ``advance_build`` as its link, and we return;
4. finalize: files, digests, invariants, manifest, rename, commit, post-commit enqueue.

A verify-rebuild job runs the same loop with reuse disabled (its step identities are salted with
the job id, so it can never reuse, nor be reused) and compares logical digests instead of
committing a version (FTID 002 section 7.4).
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..core.cancellation import OperatorCancelled, record_progress, row_requests_cancel
from ..core.clock import utc_now
from ..core.errors import AppError
from ..core.job_kinds import get_job_kind
from ..core.storage import resolve_under_data_dir, run_dir, staging_dir
from ..models.enums import InputKind, StepKind, StepState, VerificationResult
from ..models.job import TERMINAL_STATUSES, Job
from ..models.recipe import RecipeBody
from ..models.source import SourceFile
from ..models.step_execution import StepExecution
from ..models.version import Version, VersionBuild, VersionVerification
from ..services.job_service import claim_job
from . import identity
from .assembly import InputFile, assemble, verify_files
from .operator_port import OperatorRefusal, StepSpec, registry
from .step_contract import is_finished, part_files
from .step_ingest import ingest
from .step_params import input_records, resolve_inputs, resolve_step, sync_lookup
from .version_finalize import LinkedStep, finalize, write_split_files

logger = logging.getLogger(__name__)

ADVANCE_TASK = "midataworks.versions.advance_build"
LIVE_STEP_STATES = (StepState.QUEUED, StepState.RUNNING, StepState.COMPLETED)

Emit = Callable[[str, str, dict[str, Any]], Any]
SendTask = Callable[..., Any]


@dataclass
class Context:
    session: Session
    job: Job
    build: VersionBuild
    room: str
    emit: Emit
    send_task: SendTask

    @property
    def request(self) -> dict[str, Any]:
        return self.build.request


def progress_message(phase: str, fields: dict[str, Any]) -> str:
    """What the job record says: the phase AND, for a step, which one, and whether it is queued
    for a worker, running, reused or waiting (e2e run G10: a 10-minute build read "ingest" and a
    queued step read as running)."""
    index, count, operator = (
        fields.get("step_index"),
        fields.get("step_count"),
        fields.get("operator"),
    )
    if index is None:
        return phase
    name = f"step {index} of {count}" if count is not None else f"step {index}"
    if operator:
        name += f" ({operator})"
    if phase == "dispatched":
        return f"{name}: queued for a {fields.get('queue', 'worker')} worker; not started yet"
    if phase == "waiting":
        return f"{name}: waiting on build {fields.get('waiting_on_job')}, which computes it"
    if phase == "ingest":
        return f"{name}: recording its output"
    if fields.get("reused"):
        return f"{name}: reused a completed run"
    return f"{name}: done" if "rows_out" in fields else name


def _progress(
    ctx: Context, phase: str, percent: float, *, force: bool = False, **fields: Any
) -> None:
    record_progress(
        ctx.job.id, progress=percent, message=progress_message(phase, fields), force=force
    )
    ctx.emit(
        ctx.room,
        f"{_event_prefix(ctx)}:progress",
        {"job_id": ctx.job.id, "phase": phase, "percent": percent, **fields},
    )


def _event_prefix(ctx: Context) -> str:
    return "version_verify" if ctx.build.verify_version_id else "version_build"


# --------------------------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------------------------


def input_files(session: Session, inputs: list[dict[str, Any]]) -> list[InputFile]:
    files: list[InputFile] = []
    for item in inputs:
        if item["kind"] == InputKind.SOURCE:
            rows = session.execute(
                select(SourceFile)
                .where(SourceFile.source_id == item["source_id"])
                .order_by(SourceFile.split)
            ).scalars()
            for f in rows:
                files.append(
                    InputFile(
                        path=resolve_under_data_dir(f.path),
                        sha256=f.sha256,
                        split=f.split,
                        kind="source",
                        source_id=str(item["source_id"]),
                        label=f"source {item['source_id']} split {f.split!r}",
                    )
                )
        else:
            version = session.get(Version, item["version_id"])
            assert version is not None
            for split in version.splits:
                files.append(
                    InputFile(
                        path=resolve_under_data_dir(split["path"]),
                        sha256=split["file_sha256"],
                        split=split["name"],
                        kind="version",
                        source_id=None,
                        label=f"version {version.id} split {split['name']!r}",
                    )
                )
    return files


# --------------------------------------------------------------------------------------------
# Step identity and executions
# --------------------------------------------------------------------------------------------


def _salt(ctx: Context, digest: str) -> str:
    if ctx.build.reuse_enabled:
        return digest
    return identity.digest_text({"verify_job": ctx.job.id, "identity": digest})


def step_identity_for(
    ctx: Context, index: int, step: dict[str, Any] | None, previous: StepExecution | None
) -> tuple[str, dict[str, Any]]:
    """The identity digest of step ``index`` and the fields an execution row records."""
    req = ctx.request
    if index == 0:
        digest = identity.assemble_identity(
            req["inputs"], req["initial_roles"], req["rowkey_scheme"]
        )
        return _salt(ctx, digest), {"kind": StepKind.ASSEMBLE}
    assert step is not None and previous is not None
    info = registry().get(step["operator"], step["version"])
    ref = identity.operator_ref(info.name, info.version)
    seed = identity.step_seed(int(req["seed"]), index, ref)
    consumed = [b for b in req["bindings"] if b["kind"] in info.binding_kinds]
    bindings = identity.bindings_digest(consumed)
    params = identity.params_hash(step.get("params", {}))
    digest = identity.step_identity(
        input_digest=previous.identity_digest,
        kind=StepKind.OPERATOR,
        ref=ref,
        manifest_hash=info.manifest_hash,
        params_digest=params,
        seed=seed,
        bindings=bindings,
        rowkey_scheme=req["rowkey_scheme"],
    )
    return _salt(ctx, digest), {
        "kind": StepKind.OPERATOR,
        "operator_name": info.name,
        "operator_version": info.version,
        "manifest_hash": info.manifest_hash,
        "params_hash": params,
        "step_seed": seed,
        "bindings_digest": bindings,
        "input_execution_digest": previous.identity_digest,
        "input_logical_digest": previous.output_logical_digest,
    }


def find_or_create_execution(
    ctx: Context, digest: str, fields: dict[str, Any]
) -> tuple[StepExecution, bool]:
    """The live-or-completed execution with this identity, creating it if none exists.

    ``INSERT … ON CONFLICT DO NOTHING`` on the partial unique index, then a read: two builds racing
    to the same identity get one execution, and the loser waits for the winner (FTASKS 8.2).
    Returns (execution, found_completed).
    """
    session = ctx.session
    existing = session.execute(
        select(StepExecution)
        .where(StepExecution.identity_digest == digest, StepExecution.state.in_(LIVE_STEP_STATES))
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if existing is not None:
        return existing, existing.state == StepState.COMPLETED
    new_id = str(uuid.uuid4())
    session.execute(
        pg_insert(StepExecution)
        .values(
            id=new_id,
            identity_digest=digest,
            state=StepState.QUEUED,
            output_dir=f"runs/{ctx.job.id}/steps/{new_id}",
            job_id=ctx.job.id,
            **fields,
        )
        .on_conflict_do_nothing(
            index_elements=["identity_digest"],
            index_where=StepExecution.state.in_(LIVE_STEP_STATES),
        )
    )
    session.commit()
    row = session.execute(
        select(StepExecution)
        .where(StepExecution.identity_digest == digest, StepExecution.state.in_(LIVE_STEP_STATES))
        .execution_options(populate_existing=True)
    ).scalar_one()
    return row, row.state == StepState.COMPLETED and row.id != new_id


def _link(ctx: Context, index: int, execution: StepExecution, reused: bool) -> None:
    steps = list(ctx.build.steps)
    steps.append({"index": index, "execution_id": execution.id, "reused": reused})
    ctx.build.steps = steps
    ctx.build.current_step_index = index + 1
    ctx.build.waiting_execution_id = None
    ctx.session.commit()


def _rekick_waiters(ctx: Context, execution_id: str) -> None:
    rekick_waiters(ctx.session, execution_id, ctx.job.id, ctx.send_task)


def rekick_waiters(
    session: Session, execution_id: str, owner_job_id: str, send_task: SendTask
) -> None:
    """Advance every other live build waiting on ``execution_id`` (it ended, one way or another)."""
    waiting = session.execute(
        select(VersionBuild.job_id)
        .join(Job, Job.id == VersionBuild.job_id)
        .where(
            VersionBuild.waiting_execution_id == execution_id,
            VersionBuild.job_id != owner_job_id,
            Job.status.in_(("running", "cancelling")),
        )
    ).scalars()
    for job_id in list(waiting):
        try:
            send_task(ADVANCE_TASK, args=[job_id])
        except Exception as exc:  # noqa: BLE001 - the janitor re-finds a stalled build
            logger.warning("could not re-kick build %s: %s", job_id, exc)


def settle_after_worker_lost(
    session: Session, job_id: str, job_status: str, reason: str, send_task: SendTask
) -> list[str]:
    """A build or verify job ended without its worker settling its steps (the step's operator
    worker was SIGKILLed; the janitor reaped the build on the step's stale heartbeat). Its
    ``queued``/``running`` executions kept their identity's live slot
    (``uq_dw_step_executions_identity_live``), so building the same request again waited on them
    for good (2026-10-18 audit). Mark them ``cancelled`` (the job was being cancelled) or
    ``failed`` with ``WORKER_LOST``, remove their partial output, and advance any other build
    waiting on them. Completed executions are kept for reuse. Returns the executions settled."""
    state = StepState.CANCELLED if job_status == "cancelled" else StepState.FAILED
    rows = list(
        session.execute(
            select(StepExecution).where(
                StepExecution.job_id == job_id,
                StepExecution.state.in_((StepState.QUEUED, StepState.RUNNING)),
            )
        ).scalars()
    )
    for row in rows:
        session.execute(
            update(StepExecution)
            .where(
                StepExecution.id == row.id,
                StepExecution.state.in_((StepState.QUEUED, StepState.RUNNING)),
            )
            .values(
                state=state,
                error={"code": "WORKER_LOST", "message": reason, "details": {}},
                completed_at=utc_now(),
            )
        )
        shutil.rmtree(resolve_under_data_dir(row.output_dir), ignore_errors=True)
    session.commit()
    for row in rows:
        logger.warning("build.step_worker_lost job=%s execution=%s -> %s", job_id, row.id, state)
        rekick_waiters(session, row.id, job_id, send_task)
    return [row.id for row in rows]


#: An execution whose job ended this long ago while it still reads ``queued``/``running`` is
#: stranded (the reconciliation below). The grace covers a build's own failure path, which writes
#: the job before it settles its steps.
STRANDED_GRACE = timedelta(minutes=2)


def stranded_executions(session: Session, *, now: datetime | None = None) -> list[tuple[str, str]]:
    """``(execution id, job id)`` for ``queued``/``running`` executions whose job is terminal and
    ended before the grace: steps stranded before the reap hook existed, or whose hook failed."""
    cutoff = (now or utc_now()) - STRANDED_GRACE
    rows = session.execute(
        select(StepExecution.id, StepExecution.job_id, Job.completed_at, Job.updated_at)
        .join(Job, Job.id == StepExecution.job_id)
        .where(
            StepExecution.state.in_((StepState.QUEUED, StepState.RUNNING)),
            Job.status.in_(tuple(TERMINAL_STATUSES)),
        )
    ).all()
    out: list[tuple[str, str]] = []
    for execution_id, job_id, completed_at, updated_at in rows:
        ended = completed_at or updated_at
        if ended is not None and ended > cutoff:
            continue
        out.append((str(execution_id), str(job_id)))
    return out


# --------------------------------------------------------------------------------------------
# One pass
# --------------------------------------------------------------------------------------------


def _assemble(ctx: Context, execution: StepExecution) -> None:
    req = ctx.request
    files = input_files(ctx.session, req["inputs"])
    staged = staging_dir() / f"assemble-{execution.id}"
    shutil.rmtree(staged, ignore_errors=True)
    checker = _cancel_checker(ctx)
    try:
        result = assemble(
            files,
            req["initial_roles"],
            req["rowkey_scheme"],
            staged,
            check_cancel=checker,
            progress=lambda n: _progress(ctx, "assemble", 5.0, rows_out=n),
        )
        destination = resolve_under_data_dir(execution.output_dir)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        staged.rename(destination)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    execution.rows_in = result.rows
    execution.rows_kept = result.rows
    execution.rows_changed = execution.rows_dropped = execution.rows_added = 0
    execution.rows_split_assigned = 0
    execution.output_column_roles = result.column_roles
    execution.output_logical_digest = result.logical_digest
    execution.state = StepState.COMPLETED
    execution.started_at = execution.started_at or utc_now()
    execution.completed_at = utc_now()
    ctx.session.commit()


def _cancel_checker(ctx: Context) -> Callable[[], None]:
    from ..core.cancellation import CancelCheck

    check = CancelCheck(ctx.job.id)
    return lambda: check.raise_if_cancelled()


def _dispatch(
    ctx: Context,
    index: int,
    step: dict[str, Any],
    execution: StepExecution,
    previous: StepExecution,
) -> None:
    spec = StepSpec(
        step_execution_id=execution.id,
        operator=step["operator"],
        version=step["version"],
        params=dict(step.get("params", {})),
        input_dir=previous.output_dir,
        output_dir=execution.output_dir,
        step_seed=int(execution.step_seed or 0),
        job_id=ctx.job.id,
        bindings=list(ctx.request["bindings"]),
        column_roles=dict(previous.output_column_roles or {}),
        rowkey_scheme=ctx.request["rowkey_scheme"],
        expected_manifest_hash=str(execution.manifest_hash),
    )
    execution.state = StepState.RUNNING
    execution.started_at = utc_now()
    execution.heartbeat_at = utc_now()
    ctx.session.commit()
    registry().dispatch_step(spec, ADVANCE_TASK, [ctx.job.id])


def _recipe_body(ctx: Context) -> dict[str, Any]:
    row = ctx.session.get(RecipeBody, ctx.request["recipe_hash"])
    assert row is not None
    parsed: dict[str, Any] = json.loads(row.canonical)
    return parsed


def _fail_execution(ctx: Context, execution: StepExecution, error: AppError) -> None:
    execution.state = StepState.FAILED
    execution.error = {"code": error.code, "message": error.message, "details": error.details}
    execution.completed_at = utc_now()
    ctx.session.commit()
    _rekick_waiters(ctx, execution.id)


def advance(session: Session, job_id: str, *, emit: Emit, send_task: SendTask) -> str:
    """One idempotent pass. Returns what happened: skipped, waiting, dispatched, completed."""
    job = session.get(Job, job_id, populate_existing=True)
    if job is None:
        return "skipped"
    if job.status == "queued":
        claimed = claim_job(session, job_id)
        if claimed is None:
            return "skipped"
        job = claimed
    if job.is_terminal:
        return "skipped"
    build = session.get(VersionBuild, job_id, populate_existing=True)
    assert build is not None, f"job {job_id} has no build record"
    ctx = Context(session, job, build, get_job_kind(job.kind).room(job.id), emit, send_task)
    try:
        if row_requests_cancel(job.status, job.cancel_requested_at):
            cancel(ctx)
        return _advance(ctx)
    except OperatorCancelled:
        session.rollback()
        _cleanup_unfinished(ctx, StepState.CANCELLED)
        raise
    except AppError as error:
        session.rollback()
        fail(ctx, error)
        return "failed"


def _advance(ctx: Context) -> str:
    session, build = ctx.session, ctx.build
    req = ctx.request
    if not build.sources_verified:
        files = input_files(session, req["inputs"])
        verify_files(
            files, progress=lambda d, n: _progress(ctx, "verify_sources", 2.0 * d / max(n, 1))
        )
        build.sources_verified = True
        session.commit()
    body = _recipe_body(ctx)
    steps = body["steps"]
    total = len(steps) + 1
    executions: dict[int, StepExecution] = {}
    for entry in build.steps:
        row = session.get(StepExecution, entry["execution_id"], populate_existing=True)
        assert row is not None
        executions[entry["index"]] = row
    index = build.current_step_index
    checker = _cancel_checker(ctx)
    lookup = sync_lookup(session)
    records = input_records(session, req["inputs"])
    while index < total:
        checker()
        pinned = steps[index - 1] if index > 0 else None
        # Feature 016: a step whose operator fills parameters from a stored record (rule_set@1)
        # is resolved ONCE here and the resolved step feeds both its identity and its dispatch;
        # the stored recipe body keeps the pin (FTID 016 section 3.6, I2). Feature 017: an
        # "@input" parameter reads the input version's recorded configuration first (FTID §7.10).
        step = resolve_step(resolve_inputs(pinned, records), lookup) if pinned is not None else None
        previous = executions.get(index - 1)
        try:
            digest, fields = step_identity_for(ctx, index, step, previous)
        except OperatorRefusal as refusal:
            raise AppError(refusal.message, code=refusal.code, status_code=409) from None
        execution, found_completed = find_or_create_execution(ctx, digest, fields)
        label = (
            "Step 0 (assemble)"
            if index == 0
            else f"Step {index} ({execution.operator_name} {execution.operator_version})"
        )
        if found_completed:
            _link(ctx, index, execution, reused=True)
            executions[index] = execution
            _progress(
                ctx,
                "step",
                10 + 80 * index / total,
                step_index=index,
                step_count=total - 1,
                operator=execution.operator_name,
                reused=True,
                rows_in=execution.rows_in,
                rows_out=_rows_out(execution),
            )
            index += 1
            continue
        if execution.job_id != ctx.job.id:
            build.waiting_execution_id = execution.id
            session.commit()
            _progress(
                ctx,
                "waiting",
                10 + 80 * max(index - 1, 0) / total,
                step_index=index,
                step_count=total - 1,
                operator=execution.operator_name,
                waiting_on_job=execution.job_id,
                force=True,
            )
            return "waiting"
        if index == 0:
            _assemble(ctx, execution)
            # A sibling build reading the same inputs found this assemble live and is waiting on
            # it. Every other step's ingest re-kicks its waiters; assemble did not, so the sibling
            # waited at "verify_sources 2%" for good (e2e run F3, 2026-10-21).
            _rekick_waiters(ctx, execution.id)
        elif execution.state == StepState.QUEUED:
            assert step is not None and previous is not None
            _dispatch(ctx, index, step, execution, previous)
            _progress(
                ctx,
                "step",
                10 + 80 * (index - 1) / total,
                step_index=index,
                step_count=total - 1,
                operator=execution.operator_name,
                reused=False,
            )
            record_progress(
                ctx.job.id,
                message=progress_message(
                    "dispatched",
                    {
                        "step_index": index,
                        "step_count": total - 1,
                        "operator": execution.operator_name,
                        "queue": registry().get(step["operator"], step["version"]).queue,
                    },
                ),
                force=True,
            )
            return "dispatched"
        elif not is_finished(resolve_under_data_dir(execution.output_dir)):
            return "waiting"
        else:
            assert previous is not None
            _progress(
                ctx,
                "ingest",
                10 + 80 * index / total,
                step_index=index,
                step_count=total - 1,
                operator=execution.operator_name,
            )
            try:
                ingest(
                    session,
                    execution,
                    resolve_under_data_dir(previous.output_dir),
                    resolve_under_data_dir(execution.output_dir),
                    req["rowkey_scheme"],
                    label,
                    step_params=dict(pinned["params"]) if pinned is not None else None,
                )
            except AppError as error:
                session.rollback()
                _fail_execution(ctx, execution, error)
                raise
            _rekick_waiters(ctx, execution.id)
        _link(ctx, index, execution, reused=False)
        executions[index] = execution
        _progress(
            ctx,
            "step",
            10 + 80 * index / total,
            step_index=index,
            step_count=total - 1,
            operator=execution.operator_name,
            reused=False,
            rows_in=execution.rows_in,
            rows_out=_rows_out(execution),
        )
        index += 1
    ordered = [
        LinkedStep(e["index"], executions[e["index"]], bool(e["reused"])) for e in build.steps
    ]
    checker()
    _progress(ctx, "finalize", 95.0)
    if build.verify_version_id:
        return _finish_verify(ctx, ordered)
    version_id = finalize(session, ctx.job, build, ordered, body, send_task=ctx.send_task)
    version = session.get(Version, version_id)
    assert version is not None
    record_progress(
        ctx.job.id,
        status="completed",
        progress=100.0,
        message=f"Built version {version.number}.",
        result={"version_id": version_id, "number": version.number},
    )
    ctx.emit(
        ctx.room,
        "version_build:completed",
        {
            "job_id": ctx.job.id,
            "version_id": version_id,
            "dataset_id": version.dataset_id,
            "number": version.number,
        },
    )
    return "completed"


def _rows_out(execution: StepExecution) -> int:
    return (
        int(execution.rows_in or 0)
        - int(execution.rows_dropped or 0)
        + int(execution.rows_added or 0)
    )


# --------------------------------------------------------------------------------------------
# Terminal paths
# --------------------------------------------------------------------------------------------


def fail(ctx: Context, error: AppError) -> None:
    envelope = {"code": error.code, "message": error.message, "details": error.details}
    logger.warning("build %s failed: %s", ctx.job.id, error.code)
    record_progress(ctx.job.id, status="failed", error=error.message, result={"error": envelope})
    ctx.emit(ctx.room, f"{_event_prefix(ctx)}:failed", {"job_id": ctx.job.id, "error": envelope})
    _cleanup_unfinished(ctx, StepState.FAILED)
    if ctx.build.verify_version_id:
        _record_verification(ctx, VerificationResult.FAILED, [], {"error": envelope})


def cancel(ctx: Context) -> None:
    """Stop: no version, no visible files; completed executions are kept for a resume.
    (``advance`` marks this build's unfinished executions cancelled on the way out.)"""
    raise OperatorCancelled(
        ctx.job.id,
        "cancelled",
        "Cancelled. Completed steps were kept; building the same request again resumes from them.",
        result={"completed_steps": len(ctx.build.steps)},
    )


def _cleanup_unfinished(ctx: Context, state: StepState) -> None:
    rows = list(
        ctx.session.execute(
            select(StepExecution).where(
                StepExecution.job_id == ctx.job.id,
                StepExecution.state.in_((StepState.QUEUED, StepState.RUNNING)),
            )
        ).scalars()
    )
    for row in rows:
        ctx.session.execute(
            update(StepExecution)
            .where(StepExecution.id == row.id)
            .values(state=state, completed_at=utc_now())
        )
        shutil.rmtree(resolve_under_data_dir(row.output_dir), ignore_errors=True)
    ctx.session.commit()
    for row in rows:
        _rekick_waiters(ctx, row.id)


# --------------------------------------------------------------------------------------------
# Verify rebuild (FR-002.6)
# --------------------------------------------------------------------------------------------


def _split_pairs(path: Path) -> list[tuple[str, int]]:
    table = pq.read_table(path, columns=["_dw_row_key", "_dw_occurrence"])
    return list(zip(table.column(0).to_pylist(), table.column(1).to_pylist(), strict=True))


def _finish_verify(ctx: Context, steps: list[LinkedStep]) -> str:
    version = ctx.session.get(Version, ctx.build.verify_version_id)
    assert version is not None
    last = steps[-1].execution
    scratch = staging_dir() / f"verify-{ctx.job.id}"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        rebuilt = dict(
            write_split_files(part_files(resolve_under_data_dir(last.output_dir)), scratch)
        )
        report: list[dict[str, Any]] = []
        first_mismatch: dict[str, Any] | None = None
        names = [s["name"] for s in version.splits] + [
            n for n in rebuilt if n not in {s["name"] for s in version.splits}
        ]
        for name in names:
            expected = next(
                (s["logical_digest"] for s in version.splits if s["name"] == name), None
            )
            path = rebuilt.get(name)
            actual = identity.logical_digest(path) if path else None
            report.append({"split": name, "expected": expected, "actual": actual})
            if expected != actual and first_mismatch is None:
                first_mismatch = _first_difference(version, name, path)
        result = VerificationResult.MATCH if first_mismatch is None else VerificationResult.MISMATCH
        _record_verification(ctx, result, report, first_mismatch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        for linked in steps:
            if linked.execution.job_id == ctx.job.id:
                shutil.rmtree(
                    resolve_under_data_dir(linked.execution.output_dir), ignore_errors=True
                )
        shutil.rmtree(run_dir(ctx.job.id), ignore_errors=True)
    record_progress(
        ctx.job.id,
        status="completed",
        progress=100.0,
        message=f"Verify rebuild: {result.value}.",
        result={"version_id": version.id, "result": result.value, "first_mismatch": first_mismatch},
    )
    ctx.emit(
        ctx.room,
        "version_verify:completed",
        {"job_id": ctx.job.id, "version_id": version.id, "result": result.value},
    )
    return "completed"


def _first_difference(version: Version, split: str, rebuilt: Path | None) -> dict[str, Any]:
    stored = next((s for s in version.splits if s["name"] == split), None)
    expected = _split_pairs(resolve_under_data_dir(stored["path"])) if stored else []
    actual = _split_pairs(rebuilt) if rebuilt else []
    for position, (a, b) in enumerate(zip(expected, actual, strict=False)):
        if a != b:
            return {"split": split, "position": position, "expected": list(a), "actual": list(b)}
    position = min(len(expected), len(actual))
    return {
        "split": split,
        "position": position,
        "expected": list(expected[position]) if position < len(expected) else None,
        "actual": list(actual[position]) if position < len(actual) else None,
    }


def _record_verification(
    ctx: Context,
    result: VerificationResult,
    splits: list[dict[str, Any]],
    first: dict[str, Any] | None,
) -> None:
    assert ctx.build.verify_version_id is not None
    ctx.session.add(
        VersionVerification(
            id=str(uuid.uuid4()),
            version_id=ctx.build.verify_version_id,
            job_id=ctx.job.id,
            result=result.value,
            splits=splits,
            first_mismatch=first,
            created_by=ctx.job.started_by,
            created_by_origin=ctx.job.started_by_origin,
        )
    )
    ctx.session.commit()


# --------------------------------------------------------------------------------------------
# Waiting builds: cancel and the janitor's reconciliation (e2e run F3, 2026-10-21)
# --------------------------------------------------------------------------------------------


def idle_build(session: Session, job_id: str) -> bool:
    """True when no step of this build is running in a worker: its task has returned (it is
    waiting on another build, or between passes), so nothing will advance it unless kicked."""
    running = session.execute(
        select(StepExecution.id).where(
            StepExecution.job_id == job_id, StepExecution.state == StepState.RUNNING
        )
    ).first()
    return running is None


#: The reconciler leaves a wait that ended this recently alone: the step's own path (ingest,
#: assemble, a reap hook) re-kicks its waiters at once, and a second kick would run the build twice.
WAKE_GRACE = timedelta(minutes=2)


def stalled_waiting_builds(session: Session, *, now: datetime | None = None) -> list[str]:
    """Live builds (running or cancelling) that wait on an execution which can no longer wake
    them: it ended (completed, failed or cancelled) more than :data:`WAKE_GRACE` ago, or is gone.
    Advancing them is safe (a pass is idempotent): a running build links the finished step and
    goes on; a cancelling one stops."""
    cutoff = (now or utc_now()) - WAKE_GRACE
    rows = session.execute(
        select(VersionBuild.job_id, StepExecution.state, StepExecution.completed_at)
        .join(Job, Job.id == VersionBuild.job_id)
        .outerjoin(StepExecution, StepExecution.id == VersionBuild.waiting_execution_id)
        .where(
            VersionBuild.waiting_execution_id.is_not(None),
            Job.status.in_(("running", "cancelling")),
        )
    ).all()
    return [
        str(job)
        for job, state, ended in rows
        if state not in (StepState.QUEUED, StepState.RUNNING) and (ended is None or ended <= cutoff)
    ]


def stuck_cancelling_builds(session: Session, *, now: datetime | None = None) -> list[str]:
    """Builds whose cancel was asked more than :data:`WAKE_GRACE` ago but which have no running
    step to notice it (the cancel route wakes an idle build at once; this catches a lost wake)."""
    cutoff = (now or utc_now()) - WAKE_GRACE
    jobs = session.execute(
        select(Job.id).where(
            Job.status == "cancelling",
            Job.kind.in_(("version_build", "version_verify")),
            (Job.cancel_requested_at.is_(None)) | (Job.cancel_requested_at <= cutoff),
        )
    ).scalars()
    return [str(j) for j in jobs if idle_build(session, str(j))]
