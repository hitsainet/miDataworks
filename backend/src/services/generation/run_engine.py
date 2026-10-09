"""The generation worker loop (FTDD 007 section 6.3; FTID 007 section 7.3; FR-007.2 – 007.34).

```
load     ← run, snapshots; re-resolve the generation role (must match the run's endpoint)
guards   ← held_out_guard + seed_split_guard again (the worker call sites, FR-007.35/36)
ticket   ← lease_holder.join(model)        # miLLM; Unpinned → pinned = false (P-13)
seeds    ← seeds.parquet, or select with the run seed (FR-002.30) and write it; EVERY row
           re-checked: a source row from a seed split, never a held-out one (FR-007.36)
recover  ← commit renamed chunk files with no marker (no regeneration)
expand   ← one request per seed (generator side)                          # when the run has it
respond  ← N responses per prompt; steered: side A then side B, same template, same seed
           each response: model check, steering check, seed echo → generated | discarded | skipped
           steered: a pair only when BOTH sides match (else both discarded, with reasons)
           every chunk: profile_guard (re-read updated_at) → stage → rename → commit → lease check
finish   ← counts, committed.json (what versions may read), leave the lease
```

A discarded response never becomes a ``generated`` record: :func:`decide` is the single place an
outcome is chosen, and a database constraint refuses ``generated`` with a failed check. A state
miLLM did not report is recorded verbatim (``reported_steering`` NULL) and decided ``unreported``,
never "unsteered". Waits on 503 happen inside the caller and are never failures.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ...clients.endpoint_caller import EndpointCaller
from ...clients.endpoint_errors import EndpointCallError, LeaseLost
from ...core import clock
from ...core.cancellation import CancelCheck, OperatorCancelled, record_progress
from ...core.canonical_json import canonical_json
from ...core.config import get_settings
from ...core.job_kinds import get_job_kind
from ...core.storage import atomic_write_bytes, resolve_under_data_dir
from ...models.generation import (
    GenerationChunk,
    GenerationPair,
    GenerationRecord,
    GenerationRun,
    GenerationRunJob,
    GenerationTemplate,
    SteeringSnapshot,
)
from ...models.job import Job
from ...models.version import Version
from ..endpoint_resolver import resolve
from ..identity import file_sha256
from ..model_lease_holder import (
    HOLDER,
    LeaseHeldElsewhere,
    LeaseTicket,
    ModelLeaseHolder,
    ModelNotLoaded,
)
from ..row_keys import compute_row_key
from . import record_store, rules, seed_selection, settings_client, steering
from .generation_call import (
    CallContext,
    CallStop,
    GenerationCall,
    GenerationRequest,
    GenerationResult,
)
from .record_store import GenRecord
from .run_service import call_for, caller_for, expected_for, set_state

logger = logging.getLogger(__name__)

EMIT_INTERVAL_S = 2.0
JOB_KIND = "generation_run"
PROMPT_KEY_COLUMNS = ["prompt"]


class RunStop(Exception):
    """Stop the run as ``failed``; committed chunks stay. ``failure_reason`` set → not resumable."""

    def __init__(self, code: str, message: str, failure_reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.failure_reason = failure_reason


class Requeue(Exception):
    """Another holder leases the model: back to ``queued``, retried later (005's rule)."""


Emit = Callable[[str, str, dict[str, Any]], bool]


def _default_emit(room: str, event: str, data: dict[str, Any]) -> bool:
    from ...workers.emit import emit

    return emit(room, event, data)


@dataclass
class EngineDeps:
    holder: ModelLeaseHolder = field(default_factory=lambda: HOLDER)
    emit: Emit = _default_emit
    #: None → a cooperative sleep that checks cancellation every second.
    sleep: Callable[[float], None] | None = None
    #: The crash test's pause point, between a chunk's rename and its commit.
    after_rename: Callable[[Path], None] | None = None
    call_for: Callable[[str], GenerationCall] = call_for
    #: Called with the job id at every unit boundary (tests cancel or edit a profile here).
    on_unit: Callable[[int], None] | None = None


@dataclass
class Seed:
    position: int
    row_key: str
    split: str
    origin: str
    prompt: str
    values: dict[str, Any]


@dataclass
class Unit:
    """One prompt: every response (and side) for it lives in one chunk."""

    position: int
    seed_row_key: str
    prompt_row_key: str
    prompt: str
    values: dict[str, Any]


@dataclass
class _Loop:
    run: GenerationRun
    job_id: str
    room: str
    counts: Counter[str]
    is_millm: bool
    model: str
    caller: EndpointCaller
    ticket: LeaseTicket | None
    snapshots: dict[str, SteeringSnapshot]
    consecutive: int = 0
    independence_checked: bool = False
    stop_after_chunk: RunStop | None = None
    last_emit: float = 0.0
    units_done: int = 0
    units_goal: int = 0


# --- the single decision ---------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    outcome: str
    reason_code: str | None
    check: steering.Check


def decide(
    result: GenerationResult,
    expected: steering.Expected | None,
    *,
    accept_unreported: bool,
) -> Decision:
    """The outcome of one response: the ONLY place a record's outcome is chosen.

    ``accept_unreported`` is true only for an unsteered side while miLLM does not yet report
    steering (FR-007.21: the record then says "not reported" and the run carries a warning).
    """
    if result.outcome == "context_overflow":
        return Decision("skipped", "context_overflow", steering.Check("not_applicable"))
    if result.outcome == "error":
        return Decision("skipped", "row_error", steering.Check("not_applicable"))
    check = steering.check_reported_state(
        expected, steering.parse_steering_header(result.steering_header)
    )
    passed = check.result in ("match", "not_applicable") or (
        check.result == "unreported" and accept_unreported
    )
    if not passed:
        return Decision("discarded", check.reason_code(), check)
    if result.outcome == "parse_failure":
        return Decision("discarded", "parse_failure", check)
    if result.text is None or not str(result.text).strip():
        return Decision("discarded", "empty_response", check)
    return Decision("generated", None, check)


# --- the engine ------------------------------------------------------------------------------


class GenerationEngine:
    def __init__(self, deps: EngineDeps | None = None) -> None:
        self.deps = deps or EngineDeps()

    def run(self, session: Session, job: Job) -> dict[str, Any]:
        link = session.execute(
            select(GenerationRunJob).where(GenerationRunJob.job_id == job.id)
        ).scalar_one_or_none()
        if link is None:
            record_progress(job.id, status="failed", error="This job names no generation run.")
            return {"job_id": job.id, "outcome": "failed"}
        run = session.get(GenerationRun, link.run_id, populate_existing=True)
        assert run is not None
        set_state(session, run, "running")
        record_progress(job.id, message=f"Generation run {run.id} started.", force=True)
        try:
            self._run(session, run, job)
        except OperatorCancelled:
            session.rollback()
            self._store_counts(session, run)
            set_state(session, run, "cancelled")
            raise
        except Requeue as wait:
            session.rollback()
            self._requeue(session, run, job, str(wait))
            return {"job_id": job.id, "outcome": "queued"}
        except RunStop as stop:
            session.rollback()
            logger.warning(
                "generation_run.failed run=%s code=%s reason=%s",
                run.id,
                stop.code,
                stop.failure_reason,
            )
            self._store_counts(session, run)
            set_state(
                session,
                run,
                "failed",
                error={"code": stop.code, "message": stop.message},
                failure_reason=stop.failure_reason,
            )
            record_progress(
                job.id,
                status="failed",
                error=stop.message,
                result={"generation_run_id": run.id, "error": {"code": stop.code}},
            )
            self._emit_now(
                run, "generation_run:failed", {"code": stop.code, "message": stop.message}
            )
            return {"job_id": job.id, "outcome": "failed"}
        return {"job_id": job.id, "outcome": "completed"}

    def _requeue(self, session: Session, run: GenerationRun, job: Job, reason: str) -> None:
        from ...core.celery_app import celery_app

        set_state(session, run, "queued")
        task_id = f"{job.id}-retry-{int(time.time())}"
        session.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(status="queued", celery_task_id=task_id, queue_reason=reason)
        )
        session.commit()
        kind = get_job_kind(job.kind)
        assert kind.task_name is not None
        try:
            celery_app.send_task(
                kind.task_name,
                args=[job.id],
                task_id=task_id,
                countdown=get_settings().label_lease_retry_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - Beat's dispatcher picks it up again
            logger.warning("Could not re-send job %s: %s", job.id, exc)
            session.execute(update(Job).where(Job.id == job.id).values(celery_task_id=None))
            session.commit()

    def _sleeper(self, cancel: CancelCheck) -> Callable[[float], None]:
        if self.deps.sleep is not None:
            injected = self.deps.sleep

            def sleep_and_check(seconds: float) -> None:
                injected(seconds)
                cancel.raise_if_cancelled("Stopped while waiting for the endpoint.")

            return sleep_and_check

        def cooperative(seconds: float) -> None:
            end = clock.monotonic() + seconds
            while True:
                cancel.raise_if_cancelled("Stopped while waiting for the endpoint.")
                remaining = end - clock.monotonic()
                if remaining <= 0:
                    return
                time.sleep(min(1.0, remaining))

        return cooperative

    # --- the run ------------------------------------------------------------------------------

    def _run(self, session: Session, run: GenerationRun, job: Job) -> None:
        endpoint = dict(run.generation_endpoint)
        try:
            resolved = resolve("generation", session)
        except Exception as exc:  # noqa: BLE001 - an unconfigured role stops the run
            raise RunStop("ROLE_UNCONFIGURED", str(exc)) from None
        if resolved.base_url != endpoint.get("base_url") or resolved.model_id != endpoint.get(
            "model_id"
        ):
            raise RunStop(
                "ENDPOINT_CHANGED",
                f"The generation endpoint changed in Settings since this run started (was "
                f"{endpoint.get('model_id')} on {endpoint.get('base_url')}). Start a new run.",
            )
        version = session.get(Version, run.input_version_id)
        assert version is not None
        # The worker call sites of the held-out guards (FR-007.35, FR-007.36).
        try:
            held_out = rules.held_out_guard(version.splits, version.held_out_origin_version_id)
            rules.seed_split_guard(run.seed_splits, held_out)
        except rules.GenerationRuleError as exc:
            raise RunStop(exc.code, exc.message) from None
        snapshots = {
            s.side: s
            for s in session.execute(
                select(SteeringSnapshot).where(SteeringSnapshot.run_id == run.id)
            ).scalars()
        }
        cancel = CancelCheck(job.id)

        def on_wait(seconds: float, reason: str) -> None:
            record_progress(
                job.id,
                message=f"Waiting for the endpoint: {reason} (retry in {seconds:.0f} s)",
                force=True,
            )

        sleep = self._sleeper(cancel)
        caller = caller_for(resolved.base_url, resolved.api_key, sleep=sleep, on_wait=on_wait)
        ticket: LeaseTicket | None = None
        try:
            is_millm = run.server_kind == "millm"
            if is_millm:
                try:
                    joined = self.deps.holder.join(caller, resolved.model_id, job.id)
                except ModelNotLoaded as exc:
                    raise RunStop("MODEL_NOT_LOADED", str(exc)) from None
                except LeaseHeldElsewhere as exc:
                    raise Requeue(str(exc)) from None
                except EndpointCallError as exc:
                    raise RunStop(exc.code, exc.message) from None
                ticket = joined if isinstance(joined, LeaseTicket) else None
            run.pinned = ticket is not None
            session.commit()
            seeds = self._seeds(session, run, version, held_out)
            record_store.recover_uncommitted(session, run.id, job.id, run.chosen_side)
            loop = _Loop(
                run=run,
                job_id=job.id,
                room=get_job_kind(JOB_KIND).room(run.id),
                counts=self._counts(session, run.id),
                is_millm=is_millm,
                model=resolved.model_id,
                caller=caller,
                ticket=ticket,
                snapshots=snapshots,
            )
            call = self.deps.call_for(run.engine_path)
            base = CallContext(
                base_url=resolved.base_url,
                model=resolved.model_id,
                api_key=resolved.api_key,
                lease_id=ticket.lease_id if ticket else None,
                is_millm=is_millm,
                sleep=sleep,
                on_wait=on_wait,
                timeout_s=get_settings().endpoint_http_timeout_seconds,
                job_id=job.id,
            )
            if "expand" in run.stages:
                template = session.get(GenerationTemplate, run.expand_template_id)
                assert template is not None
                units = [Unit(s.position, s.row_key, s.row_key, s.prompt, s.values) for s in seeds]
                self._stage(session, loop, "expand", template, units, call, base, cancel)
            if "respond" in run.stages:
                template = session.get(GenerationTemplate, run.respond_template_id)
                assert template is not None
                units = self._respond_units(session, run, seeds)
                self._stage(session, loop, "respond", template, units, call, base, cancel)
            self._finish(session, run, job)
        finally:
            if ticket is not None:
                try:
                    self.deps.holder.leave(caller, ticket, job.id)
                except Exception as exc:  # noqa: BLE001 - Beat's cleanup releases it later
                    logger.warning("Could not leave lease %s: %s", ticket.lease_row_id, exc)
            caller.close()

    # --- seeds --------------------------------------------------------------------------------

    def _needed_columns(self, session: Session, run: GenerationRun) -> list[str]:
        bodies = []
        for template_id in (run.expand_template_id, run.respond_template_id):
            if template_id is None:
                continue
            template = session.get(GenerationTemplate, template_id)
            assert template is not None
            bodies.append(template.body)
        return seed_selection.needed_columns(run.prompt_column, bodies)

    def _seeds(
        self, session: Session, run: GenerationRun, version: Version, held_out: list[str]
    ) -> list[Seed]:
        stored = record_store.read_seeds(run.id)
        if stored is None:
            needed = self._needed_columns(session, run)
            candidates = seed_selection.seed_candidates(version.splits, run.seed_splits, needed)
            rows = seed_selection.select_seed_rows(
                candidates, needed, run.prompt_column, run.sample_size, int(run.seed)
            )
            self._check_seed_rows(run, rows, held_out)
            record_store.write_seeds(
                run.id,
                pa.Table.from_pylist(
                    rows,
                    schema=pa.schema(
                        [
                            ("position", pa.int32()),
                            ("row_key", pa.string()),
                            ("split", pa.string()),
                            ("origin", pa.string()),
                            ("prompt", pa.string()),
                            ("values", pa.string()),
                        ]
                    ),
                ),
            )
        else:
            rows = stored.to_pylist()
            self._check_seed_rows(run, rows, held_out)
        return [
            Seed(
                int(r["position"]),
                str(r["row_key"]),
                str(r["split"]),
                str(r["origin"]),
                str(r["prompt"] or ""),
                json.loads(r["values"] or "{}"),
            )
            for r in rows
        ]

    @staticmethod
    def _check_seed_rows(
        run: GenerationRun, rows: Sequence[dict[str, Any]], held_out: list[str]
    ) -> None:
        """The worker's per-row re-check (FR-007.36), shared with the preview."""
        try:
            seed_selection.check_seed_rows(rows, run.seed_splits, held_out)
        except rules.GenerationRuleError as exc:
            raise RunStop(exc.code, exc.message) from None

    def _respond_units(self, session: Session, run: GenerationRun, seeds: list[Seed]) -> list[Unit]:
        if "expand" not in run.stages:
            return [Unit(s.position, s.row_key, s.row_key, s.prompt, s.values) for s in seeds]
        by_position = {s.position: s for s in seeds}
        units = []
        paths = record_store.committed_chunk_paths(session, run.id, "expand")
        for record in record_store.iter_committed(paths):
            if record.outcome != "generated" or record.text is None or record.row_key is None:
                continue
            seed = by_position[record.seed_position]
            units.append(
                Unit(record.seed_position, seed.row_key, record.row_key, record.text, seed.values)
            )
        units.sort(key=lambda u: u.position)
        return units

    # --- one stage ------------------------------------------------------------------------

    def _sides(self, run: GenerationRun, stage: str) -> list[str]:
        if stage == "respond" and run.mode == "steered_pairs":
            return ["a", "b"]
        return ["generator"]

    def _messages(self, template: GenerationTemplate, unit: Unit) -> list[dict[str, Any]]:
        return seed_selection.render_messages(template.body, {**unit.values, "prompt": unit.prompt})

    def _response_format(self, template: GenerationTemplate) -> dict[str, Any] | None:
        if template.body.get("structured_output") != "json_schema":
            return None
        return {
            "type": "json_schema",
            "json_schema": {"name": "generation", "schema": template.body.get("json_schema")},
        }

    def _stage(
        self,
        session: Session,
        loop: _Loop,
        stage: str,
        template: GenerationTemplate,
        units: list[Unit],
        call: GenerationCall,
        base: CallContext,
        cancel: CancelCheck,
    ) -> None:
        run = loop.run
        settings = get_settings()
        done = record_store.done_positions(session, run.id, stage)
        pending = [u for u in units if u.position not in done]
        loop.units_goal = len(units)
        loop.units_done = len(units) - len(pending)
        index = record_store.next_chunk_index(session, run.id, stage)
        sides = self._sides(run, stage)
        n = run.n_responses if stage == "respond" else 1
        sampling = dict(template.body.get("sampling") or {})
        response_format = self._response_format(template)
        for start in range(0, len(pending), settings.generation_chunk_size):
            chunk_units = pending[start : start + settings.generation_chunk_size]
            self._profile_guard(loop)
            buffer: list[GenRecord] = []
            try:
                for unit in chunk_units:
                    if cancel():
                        raise OperatorCancelled(
                            loop.job_id,
                            "cancelled",
                            "Stopped at a record boundary; every committed chunk is kept.",
                            {"generation_run_id": run.id},
                        )
                    if self.deps.on_unit is not None:
                        self.deps.on_unit(unit.position)
                    records = self._unit(
                        loop, stage, template, unit, sides, n, sampling, response_format, call, base
                    )
                    if records is None:  # a response named another model: stop at the boundary
                        break
                    buffer.extend(records)
                    loop.units_done += 1
                    self._progress(loop)
            except OperatorCancelled:
                index = self._flush(session, loop, stage, index, buffer)
                raise
            except RunStop:
                index = self._flush(session, loop, stage, index, buffer)
                raise
            index = self._flush(session, loop, stage, index, buffer)
            self._boundary(loop)
            if loop.stop_after_chunk is not None:
                raise loop.stop_after_chunk

    def _flush(
        self, session: Session, loop: _Loop, stage: str, index: int, buffer: list[GenRecord]
    ) -> int:
        if not buffer:
            return index
        run = loop.run
        path, sha = record_store.stage_chunk(
            run.id, stage, index, buffer, after_rename=self.deps.after_rename
        )
        record_store.commit_chunk(
            session, run.id, stage, index, loop.job_id, path, sha, buffer, run.chosen_side
        )
        for record in buffer:
            loop.counts[f"{stage}:{record.outcome}"] += 1
            if record.reason_code:
                loop.counts[f"reason:{record.reason_code}"] += 1
        logger.info(
            "generation_run.chunk_committed run=%s stage=%s chunk=%d records=%d discards=%s",
            run.id,
            stage,
            index,
            len(buffer),
            dict(Counter(r.reason_code for r in buffer if r.reason_code)),
        )
        return index + 1

    def _unit(
        self,
        loop: _Loop,
        stage: str,
        template: GenerationTemplate,
        unit: Unit,
        sides: list[str],
        n: int,
        sampling: dict[str, Any],
        response_format: dict[str, Any] | None,
        call: GenerationCall,
        base: CallContext,
    ) -> list[GenRecord] | None:
        run = loop.run
        messages = self._messages(template, unit)
        by_side: dict[str, list[tuple[int, GenerationRequest, GenerationResult]]] = {}
        for side in sides:
            snapshot = loop.snapshots[side]
            requests: list[GenerationRequest] = []
            for r in range(n):
                seed = rules.response_seed(int(run.seed), unit.prompt_row_key, r)
                requests.append(
                    GenerationRequest(
                        record_index=rules.record_index(
                            unit.position, r, n, side if side in ("a", "b") else None
                        ),
                        row_key=unit.prompt_row_key,
                        messages=messages,
                        sampling=sampling,
                        seed=seed,
                        response_format=response_format,
                    )
                )
            ctx = CallContext(**{**base.__dict__, "body_overrides": dict(snapshot.body_overrides)})
            try:
                results = call.run(requests, ctx)
            except CallStop as stop:
                raise RunStop(stop.code, stop.message) from None
            by_side[side] = [
                (r, req, res) for r, (req, res) in enumerate(zip(requests, results, strict=True))
            ]
        records: list[GenRecord] = []
        for side, pairs in by_side.items():
            snapshot = loop.snapshots[side]
            expected = expected_for(snapshot, loop.is_millm)
            accept_unreported = (not run.steering_supported) and snapshot.kind == "none"
            for response_index, req, result in pairs:
                if (
                    result.outcome == "ok"
                    and result.model is not None
                    and result.model != loop.model
                ):
                    loop.stop_after_chunk = RunStop(
                        "MODEL_CHANGED",
                        f"A response named {result.model}, not {loop.model}; the run stopped at the "
                        "chunk boundary and can be resumed once the model is back.",
                    )
                    return None
                if result.outcome == "error":
                    self._fail_row(loop, result.error or "error")
                else:
                    loop.consecutive = 0
                if result.outcome == "ok" and not loop.independence_checked:
                    self._worker_independence(loop, result, snapshot)
                decision = decide(result, expected, accept_unreported=accept_unreported)
                text = result.text if result.outcome == "ok" else None
                row_key = (
                    compute_row_key({"prompt": text}, PROMPT_KEY_COLUMNS)
                    if stage == "expand" and decision.outcome == "generated" and text
                    else None
                )
                records.append(
                    GenRecord(
                        record_index=req.record_index,
                        stage=stage,
                        seed_position=unit.position,
                        row_key=row_key,
                        seed_row_key=unit.seed_row_key,
                        prompt_row_key=unit.prompt_row_key,
                        response_index=response_index,
                        side=side if side in ("a", "b") else None,
                        template_id=template.id,
                        model_id=result.model,
                        model_revision=run.model_revision,
                        requested_set_hash=snapshot.set_hash,
                        reported_steering=result.steering_header,
                        steering_check=decision.check.result,
                        check_reasons=tuple(decision.check.reasons),
                        seed_sent=req.seed,
                        seed_confirmed=(
                            None if result.seed_echo is None else result.seed_echo == req.seed
                        ),
                        latency_ms=result.latency_ms,
                        finish_reason=result.finish_reason,
                        outcome=decision.outcome,
                        reason_code=decision.reason_code,
                        prompt=unit.prompt,
                        text=text,
                    )
                )
        if run.mode == "steered_pairs" and stage == "respond":
            records = self._void_partners(records)
        return records

    @staticmethod
    def _void_partners(records: list[GenRecord]) -> list[GenRecord]:
        """One side discarded voids the pair: its partner is discarded too, with its reason."""
        from dataclasses import replace

        by_response: dict[int, list[GenRecord]] = {}
        for record in records:
            by_response.setdefault(record.response_index, []).append(record)
        out: list[GenRecord] = []
        for group in by_response.values():
            ok = all(r.outcome == "generated" for r in group) and len(group) == 2
            for record in group:
                if ok or record.outcome != "generated":
                    out.append(record)
                else:
                    out.append(
                        replace(record, outcome="discarded", reason_code="pair_partner_discarded")
                    )
        out.sort(key=lambda r: r.record_index)
        return out

    def _fail_row(self, loop: _Loop, error: str) -> None:
        settings = get_settings()
        loop.consecutive += 1
        if loop.consecutive >= settings.label_max_consecutive_failures:
            raise RunStop(
                "ENDPOINT_FAILING",
                f"{loop.consecutive} consecutive requests failed; the last error was {error}. "
                "Committed chunks are kept; resume when the endpoint is fixed.",
            )

    def _worker_independence(
        self, loop: _Loop, result: GenerationResult, snapshot: SteeringSnapshot
    ) -> None:
        """The worker call site of judge independence, with the SERVED model (FR-007.25)."""
        loop.independence_checked = True
        run = loop.run
        try:
            judge = resolve("judge")
        except Exception:  # noqa: BLE001 - no judge configured: checked again at label time
            return
        served = rules.generator_identity(
            str(result.model or loop.model), run.model_revision, snapshot.set_hash
        )
        judge_revision = run.model_revision if judge.model_id == loop.model else None
        if run.judge_identity and run.judge_identity.get("model_id") == judge.model_id:
            reported = run.judge_identity.get("revision")
            judge_revision = None if reported == rules.NOT_REPORTED else reported
        judge_id = rules.generator_identity(judge.model_id, judge_revision, None)
        conflicts = rules.judge_conflicts(judge_id, [served])
        if conflicts:
            error = rules.conflict_error(judge_id, conflicts, inherited_from=None)
            raise RunStop(error.code, error.message)

    def _profile_guard(self, loop: _Loop) -> None:
        """Re-read every profile side's ``updated_at`` before each chunk (FR-007.17). A change
        fails the run with ``profile_changed``, which is NOT resumable."""
        for side, snapshot in sorted(loop.snapshots.items()):
            if snapshot.kind != "profile" or snapshot.profile_id is None:
                continue
            try:
                current = settings_client.get_profile(loop.caller, snapshot.profile_id)
            except (settings_client.SettingsReadError, EndpointCallError) as exc:
                raise RunStop(
                    "PROFILES_UNREADABLE",
                    f"Could not re-read profile {snapshot.profile_name}: {exc}",
                ) from None
            updated = None if current is None else str(current.get("updated_at"))
            if updated != snapshot.profile_updated_at:
                raise RunStop(
                    "PROFILE_CHANGED",
                    f"Profile {snapshot.profile_name!r} (side {side}) changed at {updated or 'deletion'} "
                    f"after this run snapshotted it at {snapshot.profile_updated_at}. Start a new run.",
                    failure_reason="profile_changed",
                )

    def _boundary(self, loop: _Loop) -> None:
        if loop.ticket is not None:
            try:
                self.deps.holder.check(loop.caller, loop.ticket)
            except LeaseLost as lost:
                raise RunStop(
                    "LEASE_LOST",
                    f"The miLLM lease was lost ({lost.reason}); the run stopped at a chunk "
                    "boundary and can be resumed.",
                ) from None
        record_progress(
            loop.job_id,
            progress=100.0 * loop.units_done / max(1, loop.units_goal),
            message=f"{loop.units_done} of {loop.units_goal} prompts done.",
        )

    def _progress(self, loop: _Loop) -> None:
        now = clock.monotonic()
        if now - loop.last_emit < EMIT_INTERVAL_S:
            return
        loop.last_emit = now
        self.deps.emit(
            loop.room,
            "generation_run:progress",
            {
                "generation_run_id": loop.run.id,
                "job_id": loop.job_id,
                "units_done": loop.units_done,
                "units_total": loop.units_goal,
                "counts": dict(loop.counts),
            },
        )

    def _emit_now(self, run: GenerationRun, event: str, data: dict[str, Any]) -> None:
        self.deps.emit(
            get_job_kind(JOB_KIND).room(run.id), event, {"generation_run_id": run.id, **data}
        )

    # --- counts and finish --------------------------------------------------------------------

    @staticmethod
    def _counts(session: Session, run_id: str) -> Counter[str]:
        counts: Counter[str] = Counter()
        for stage, outcome, reason, n in session.execute(
            select(
                GenerationRecord.stage,
                GenerationRecord.outcome,
                GenerationRecord.reason_code,
                func.count(),
            )
            .where(GenerationRecord.run_id == run_id)
            .group_by(
                GenerationRecord.stage, GenerationRecord.outcome, GenerationRecord.reason_code
            )
        ).all():
            counts[f"{stage}:{outcome}"] += int(n)
            if reason:
                counts[f"reason:{reason}"] += int(n)
        return counts

    def _store_counts(self, session: Session, run: GenerationRun) -> None:
        counts = dict(self._counts(session, run.id))
        counts["pairs"] = int(
            session.execute(
                select(func.count())
                .select_from(GenerationPair)
                .where(GenerationPair.run_id == run.id)
            ).scalar_one()
        )
        run.counts = counts
        session.commit()

    def _finish(self, session: Session, run: GenerationRun, job: Job) -> None:
        self._store_counts(session, run)
        write_committed_manifest(session, run)
        set_state(session, run, "completed")
        record_progress(
            job.id,
            status="completed",
            progress=100.0,
            message="Generation finished.",
            result={"generation_run_id": run.id, "counts": dict(run.counts)},
        )
        logger.info("generation_run.completed run=%s counts=%s", run.id, dict(run.counts))
        self._emit_now(run, "generation_run:completed", {"counts": dict(run.counts)})


def write_committed_manifest(session: Session, run: GenerationRun) -> Path:
    """``runs/<run_id>/gen/committed.json``: exactly the chunks the database committed, with their
    SHA-256, and the seeds file. ``dw_generated_rows`` reads ONLY what this lists."""
    chunks = [
        {"stage": c.stage, "chunk_index": c.chunk_index, "path": c.path, "sha256": c.file_sha256}
        for c in session.execute(
            select(GenerationChunk)
            .where(GenerationChunk.run_id == run.id)
            .order_by(GenerationChunk.stage, GenerationChunk.chunk_index)
        ).scalars()
    ]
    seeds = record_store.seeds_path(run.id)
    document = {
        "run_id": run.id,
        "state": "completed",
        "mode": run.mode,
        "target_type": run.target_type,
        # The column a minimal-pair run's counterparts are written into (009 FR-009.60).
        "prompt_column": run.prompt_column,
        "chosen_side": run.chosen_side,
        "held_out_splits": list(run.held_out_splits),
        "chunks": chunks,
        "seeds": (
            {
                "path": str(resolve_under_data_dir(seeds).relative_to(resolve_under_data_dir())),
                "sha256": file_sha256(seeds),
            }
            if seeds.is_file()
            else None
        ),
    }
    destination = record_store.gen_dir(run.id) / "committed.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    return atomic_write_bytes(destination, canonical_json(document))
