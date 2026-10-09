"""The label-run worker loop (FR-005.24 – FR-005.48; FTDD 005 section 6.6; FTID 005 section 7.3).

```
plan     ← load run, re-resolve the endpoint, check it still matches the snapshot
ticket   ← lease_holder.join(...)          # miLLM; Unpinned → run.pinned = False (P-13)
recover  ← commit renamed chunk files that have no marker (no rescoring)
reuse    ← copy labels under the same fingerprint, outcomes recomputed with THIS run's thresholds
for each unrecorded row, in row-key order, one request per row (FR-005.34):
    cancellation (throttled read every 2 s); score or judge; outcome by the pure rules
    every chunk_size rows: stage → rename → commit; lease check; parse-failure guard; progress
finish   ← counts, actual keep share, length correlation, labels.parquet, leave the lease
```

Failure handling: backpressure waits inside the caller and never counts; a context overflow is
``skipped`` after ONE request; other row failures count toward ``LABEL_MAX_CONSECUTIVE_FAILURES``
and then fail the run, keeping every committed chunk; a lost lease, a refused model or a response
naming another model stops the run at once, after committing the rows already scored — resumable.
A strict-mode refusal is a defect in miDataworks and fails the run.

Every outcome comes from ONE function, :func:`decide_outcome`, which is the single call site of
``labeling_rules.decide_binary`` and ``decide_top_label`` (scoring, reuse and re-derive all use it).
"""

from __future__ import annotations

import logging
import time
from collections import Counter, deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from ..clients import millm_batch_client as batches
from ..clients.endpoint_caller import EndpointCaller
from ..clients.endpoint_errors import (
    ContextOverflow,
    EndpointCallError,
    LeaseLost,
    ModelNotResident,
    RowError,
    StrictRefusal,
)
from ..clients.labelers.base import ClassifierClient, JudgeClient, RenderedInput
from ..clients.labelers.factory import (
    ProtocolMismatch,
    TemplateModelMismatch,
    build_classifier,
    build_judge,
    parse_template,
)
from ..clients.labelers.openai_chat_judge import decide_structured_mode
from ..clients.labelers.openai_scoring import OpenAIScoringClient
from ..clients.labelers.plugins import PluginRefused
from ..clients.labelers.tei import read_identity
from ..core import clock
from ..core.cancellation import CancelCheck, OperatorCancelled, record_progress
from ..core.config import get_settings
from ..core.job_kinds import get_job_kind
from ..models.decision_template import DecisionTemplate
from ..models.job import Job
from ..models.label import Label
from ..models.label_run import LabelRun, LabelRunJob
from ..models.rubric import Rubric
from ..models.version import Version
from ..schemas.labeling import RubricBody
from . import label_inputs, label_store, labeling_rules
from .endpoint_resolver import resolve
from .label_run_service import NOT_REUSABLE, caller_for, set_state
from .label_store import LabelRecord
from .model_lease_holder import (
    HOLDER,
    LeaseHeldElsewhere,
    LeaseTicket,
    ModelLeaseHolder,
    ModelNotLoaded,
    Unpinned,
)

logger = logging.getLogger(__name__)

EMIT_INTERVAL_S = 2.0
RATE_WINDOW_S = 30.0
REUSE_BATCH = 5000


class RunStop(Exception):
    """Stop the run as ``failed`` with a reason; committed chunks stay; resumable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Requeue(Exception):
    """Another holder leases the model: back to ``queued``, retried later (FTDD 005 6.5)."""


# --- the outcome: the single call site of the threshold rules ---------------------------------


def positive_class_of(run: LabelRun, template_body: Mapping[str, Any] | None) -> str | None:
    if template_body is None:
        return None
    value = template_body.get("positive_class")
    return str(value) if value is not None else None


def decide_outcome(
    run: LabelRun, distribution: Mapping[str, float], positive_class: str | None
) -> tuple[str, float | None]:
    """(outcome, probability) for a classifier distribution under THIS run's thresholds."""
    labels = list(run.label_set or distribution)
    if len(labels) == 2:
        if (
            positive_class is None
            or run.threshold_positive is None
            or run.threshold_negative is None
        ):
            raise RunStop("THRESHOLDS_INVALID", "The run has no positive class or thresholds.")
        p = float(distribution[positive_class])
        return labeling_rules.decide_binary(p, run.threshold_positive, run.threshold_negative), p
    if run.min_top_probability is None:
        raise RunStop("THRESHOLDS_INVALID", "The run has no minimum top probability.")
    label = labeling_rules.decide_top_label(distribution, run.min_top_probability, labels)
    return label, float(max(distribution.values())) if distribution else None


# --- dependencies (injected by tests) ---------------------------------------------------------


Emit = Callable[[str, str, dict[str, Any]], bool]


def _default_emit(room: str, event: str, data: dict[str, Any]) -> bool:
    from ..workers.emit import emit

    return emit(room, event, data)


@dataclass
class EngineDeps:
    holder: ModelLeaseHolder = field(default_factory=lambda: HOLDER)
    emit: Emit = _default_emit
    #: None → a cooperative sleep in 1 s ticks that checks cancellation.
    sleep: Callable[[float], None] | None = None
    #: The crash test's pause point, between a chunk's rename and its commit.
    after_rename: Callable[[Path], None] | None = None


@dataclass
class _Loop:
    run: LabelRun
    job_id: str
    room: str
    counts: Counter[str]
    rows_done: int
    rows_goal: int
    consecutive: int = 0
    last_error: str = ""
    judged: int = 0
    parse_failures: int = 0
    first_fingerprint: str | None = None
    last_emit: float = 0.0
    samples: deque[tuple[float, int]] = field(default_factory=deque)


def _chars(fields: Mapping[str, Any]) -> int:
    return sum(len(str(v)) for v in fields.values() if v is not None)


# --- the engine -------------------------------------------------------------------------------


class LabelRunEngine:
    def __init__(self, deps: EngineDeps | None = None) -> None:
        self.deps = deps or EngineDeps()

    # --- entry --------------------------------------------------------------------------------

    def run(self, session: Session, job: Job) -> dict[str, Any]:
        link = session.execute(
            select(LabelRunJob).where(LabelRunJob.job_id == job.id)
        ).scalar_one_or_none()
        if link is None:
            record_progress(job.id, status="failed", error="This job names no label run.")
            return {"job_id": job.id, "outcome": "failed"}
        run = session.get(LabelRun, link.label_run_id, populate_existing=True)
        assert run is not None
        set_state(session, run, "running")
        record_progress(job.id, message=f"Label run {run.id} started.", force=True)
        try:
            if run.kind in ("classifier", "judge"):
                self._score_run(session, run, job)
            elif run.kind == "probe_verdict":
                self._probe_run(session, run, job)
            elif run.kind == "feature_tag":
                self._feature_run(session, run, job)
            elif run.kind == "rederived":
                self._rederive(session, run, job)
            elif run.kind == "aggregate":
                self._aggregate(session, run, job)
            else:
                # Never fall through to another kind's body: an aggregate over no parents
                # "completes" with zero labels, which reads as a finished run.
                raise RunStop(
                    "KIND_UNSUPPORTED",
                    f"This build's label-run engine has no body for kind {run.kind!r}.",
                )
        except OperatorCancelled:
            session.rollback()
            set_state(session, run, "cancelled")
            raise
        except Requeue as wait:
            session.rollback()
            self._requeue(session, run, job, str(wait))
            return {"job_id": job.id, "outcome": "queued"}
        except RunStop as stop:
            session.rollback()
            logger.warning("label_run.failed run=%s code=%s", run.id, stop.code)
            set_state(session, run, "failed", error={"code": stop.code, "message": stop.message})
            record_progress(
                job.id,
                status="failed",
                error=stop.message,
                result={"label_run_id": run.id, "error": {"code": stop.code}},
            )
            self._emit_now(run, "label_run:failed", {"code": stop.code, "message": stop.message})
            return {"job_id": job.id, "outcome": "failed"}
        return {"job_id": job.id, "outcome": "completed"}

    def _requeue(self, session: Session, run: LabelRun, job: Job, reason: str) -> None:
        from ..core.celery_app import celery_app

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

    # --- scoring ------------------------------------------------------------------------------

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

    def _score_run(self, session: Session, run: LabelRun, job: Job) -> None:
        settings = get_settings()
        snapshot = dict(run.endpoint_snapshot)
        try:
            resolved = resolve(str(snapshot["role"]), session)
        except Exception as exc:  # noqa: BLE001 - an unconfigured role stops the run
            raise RunStop("ROLE_UNCONFIGURED", str(exc)) from None
        if resolved.base_url != snapshot.get("base_url") or resolved.model_id != snapshot.get(
            "model_id"
        ):
            raise RunStop(
                "ENDPOINT_CHANGED",
                f"The {snapshot['role']} endpoint changed in Settings since this run started "
                f"(was {snapshot.get('model_id')} on {snapshot.get('base_url')}). Start a new run.",
            )
        version = session.get(Version, run.input_version_id)
        assert version is not None
        files = label_inputs.version_files(version.splits)
        cancel = CancelCheck(job.id)
        room = get_job_kind("label_run").room(run.id)

        def on_wait(seconds: float, reason: str) -> None:
            record_progress(
                job.id,
                message=f"Waiting for the endpoint: {reason} (retry in {seconds:.0f} s)",
                force=True,
            )

        caller = caller_for(
            resolved.base_url,
            resolved.api_key,
            sleep=self._sleeper(cancel),
            on_wait=on_wait,
        )
        ticket: LeaseTicket | None = None
        box: list[LeaseTicket | None] = [None]  # the batch transport may re-take the lease
        try:
            if snapshot.get("server_kind") == "millm":
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
            lease_id = ticket.lease_id if ticket is not None else None
            positive: str | None = None
            classifier: ClassifierClient | None = None
            judge: JudgeClient | None = None
            if run.kind == "classifier":
                template = session.get(DecisionTemplate, run.template_id)
                assert template is not None
                body = parse_template(template.body)
                positive = body.positive_class
                try:
                    classifier = build_classifier(
                        caller, resolved.protocol, body, resolved.model_id, lease_id=lease_id
                    )
                except (TemplateModelMismatch, ProtocolMismatch, PluginRefused) as exc:
                    raise RunStop(getattr(exc, "code", "TEMPLATE_INVALID"), str(exc)) from None
                if resolved.protocol == "tei_classification":
                    self._check_tei(caller, run)
            else:
                rubric_row = session.get(Rubric, run.rubric_id)
                assert rubric_row is not None
                rubric = RubricBody.model_validate(rubric_row.body)
                self._decide_structured(session, run, caller, rubric, files, lease_id)
                judge = build_judge(
                    caller,
                    rubric,
                    resolved.model_id,
                    sampling=run.sampling,
                    structured_output=run.structured_output,
                    lease_id=lease_id,
                )
            label_store.recover_uncommitted(session, run, job.id)
            self._copy_reused(session, run, files, positive)
            done = self._count(session, run.id)
            loop = _Loop(
                run=run,
                job_id=job.id,
                room=room,
                counts=self._counts(session, run.id),
                rows_done=done,
                rows_goal=run.rows_total,
                first_fingerprint=run.system_fingerprint,
            )
            if run.packing == "batch":
                assert isinstance(classifier, OpenAIScoringClient)
                box[0] = ticket
                self._batch_loop(session, loop, files, classifier, positive, caller, box, cancel)
                ticket = box[0]
            else:
                self._loop(
                    session,
                    loop,
                    files,
                    classifier,
                    judge,
                    positive,
                    caller,
                    ticket,
                    cancel,
                    settings,
                )
            self._finish(session, run, job)
        finally:
            ticket = box[0] if box[0] is not None else ticket
            if ticket is not None:
                try:
                    self.deps.holder.leave(caller, ticket, job.id)
                except Exception as exc:  # noqa: BLE001 - Beat's cleanup releases it later
                    logger.warning("Could not leave lease %s: %s", ticket.lease_row_id, exc)
            caller.close()

    # --- probe verdicts (009; operator decision 2026-10-07) -----------------------------------

    def _probe_run(self, session: Session, run: LabelRun, job: Job) -> None:
        """A probe-verdict run: miLLM ``POST /api/probes/score``, one input per request, under the
        shared lease, after the reproduction gate (FR-009.45 - FR-009.51, FR-009.77, FR-009.83)."""
        from ..clients.labelers.probe_score import ProbeScoreClient
        from .detector_sets import probe_protocol as pp
        from .detector_sets import reproduction

        settings = get_settings()
        snapshot = dict(run.endpoint_snapshot)
        probe = snapshot["probe"]
        try:
            base_url = pp.millm_base_url()
        except pp.ProbeRefused as refused:
            raise RunStop(refused.code, refused.message) from None
        if base_url != snapshot.get("base_url"):
            raise RunStop(
                "ENDPOINT_CHANGED",
                f"MILLM_BASE_URL changed since this run started (was {snapshot.get('base_url')}). "
                "Start a new run.",
            )
        version = session.get(Version, run.input_version_id)
        assert version is not None
        files = label_inputs.version_files(version.splits)
        cancel = CancelCheck(job.id)
        room = get_job_kind("label_run").room(run.id)

        def on_wait(seconds: float, reason: str) -> None:
            record_progress(
                job.id,
                message=f"Waiting for miLLM: {reason} (retry in {seconds:.0f} s)",
                force=True,
            )

        caller = caller_for(base_url, None, sleep=self._sleeper(cancel), on_wait=on_wait)
        ticket: LeaseTicket | None = None
        try:
            ticket = self._join_millm(session, run, caller, job)
            client = ProbeScoreClient(
                caller,
                [str(probe["probe_id"])],
                windows=[str(probe["window"])],
                lease_id=ticket.lease_id if ticket is not None else None,
            )
            seen_model: list[dict[str, Any]] = []

            def score(fields: Mapping[str, Any]) -> Any:
                try:
                    scored = client.score(fields)
                except RowError as exc:
                    refused = pp.refusal_for(exc)
                    if refused is not None:
                        raise RunStop(refused.code, refused.message) from None
                    raise
                except (ModelNotResident, LeaseLost, StrictRefusal) as exc:
                    raise RunStop(exc.code, exc.message) from None
                model = pp.reported_model(scored)
                if not seen_model:
                    seen_model.append(model)
                elif model != seen_model[0]:
                    raise RunStop(
                        "MODEL_CHANGED",
                        f"miLLM reported {model}, not {seen_model[0]}, mid-run; the run stopped "
                        "and can be resumed.",
                    )
                if scored.error is not None and scored.error.get("code") == "MODEL_CHANGED":
                    raise RunStop("MODEL_CHANGED", str(scored.error.get("message")))
                return scored

            self._probe_gate(session, run, job, cancel, score, pp, reproduction)
            label_store.recover_uncommitted(session, run, job.id)
            self._copy_reused(session, run, files, None)
            loop = _Loop(
                run=run,
                job_id=job.id,
                room=room,
                counts=self._counts(session, run.id),
                rows_done=self._count(session, run.id),
                rows_goal=run.rows_total,
            )

            def score_row(row_key: str, fields: dict[str, Any]) -> LabelRecord | None:
                try:
                    scored = score(fields)
                except ContextOverflow as exc:
                    loop.consecutive = 0
                    return LabelRecord(
                        row_key=row_key,
                        outcome="skipped",
                        parsed_value={"chars": _chars(fields)},
                        probability=None,
                        distribution=None,
                        raw_output={"error": exc.message},
                        rationale=None,
                        steering_state=pp.STEERING_STATE,
                        latency_ms=None,
                        skip_reason="context_overflow",
                        scored_at=clock.utc_now(),
                    )
                except EndpointCallError as exc:
                    self._fail_row(loop, exc)
                    return None
                loop.consecutive = 0
                try:
                    return pp.record_for(
                        row_key,
                        fields,
                        scored,
                        probe_id=str(probe["probe_id"]),
                        window=str(probe["window"]),
                        threshold_revision=int(run.labeler_identity["threshold_revision"]),
                    )
                except pp.ProbeRefused as refused:
                    raise RunStop(refused.code, refused.message) from None

            self._loop(
                session, loop, files, None, None, None, caller, ticket, cancel, settings,
                score_row=score_row,
            )  # fmt: skip
            if seen_model:
                run.endpoint_snapshot = {**run.endpoint_snapshot, "millm_model": seen_model[0]}
                session.commit()
            self._finish(session, run, job)
        finally:
            if ticket is not None:
                try:
                    self.deps.holder.leave(caller, ticket, job.id)
                except Exception as exc:  # noqa: BLE001 - Beat's cleanup releases it later
                    logger.warning("Could not leave lease %s: %s", ticket.lease_row_id, exc)
            caller.close()

    def _join_millm(
        self, session: Session, run: LabelRun, caller: EndpointCaller, job: Job
    ) -> LeaseTicket | None:
        """Take or join the shared lease without approval (P-05); no lease surface → unpinned,
        recorded with the reason (P-13)."""
        try:
            joined = self.deps.holder.join(caller, str(run.endpoint_snapshot["model_id"]), job.id)
        except ModelNotLoaded as exc:
            raise RunStop("MODEL_NOT_LOADED", str(exc)) from None
        except LeaseHeldElsewhere as exc:
            raise Requeue(str(exc)) from None
        except EndpointCallError as exc:
            raise RunStop(exc.code, exc.message) from None
        ticket = joined if isinstance(joined, LeaseTicket) else None
        run.pinned = ticket is not None
        run.endpoint_snapshot = {
            **run.endpoint_snapshot,
            "pinned": ticket is not None,
            "unpinned_reason": joined.reason if isinstance(joined, Unpinned) else None,
        }
        session.commit()
        return ticket

    def _feature_run(self, session: Session, run: LabelRun, job: Job) -> None:
        """A feature-tag run: one scoring-mode ``/v1/completions`` per row with
        ``return_sae_activations``, read unsteered, under the shared lease (FR-009.65 - 68)."""
        from ..clients.labelers.sae_activations import SaeActivationsClient
        from .detector_sets import feature_protocol as fp
        from .detector_sets import probe_protocol as pp

        settings = get_settings()
        snapshot = dict(run.endpoint_snapshot)
        spec = snapshot["features"]
        try:
            base_url = pp.millm_base_url()
        except pp.ProbeRefused as refused:
            raise RunStop(refused.code, refused.message) from None
        if base_url != snapshot.get("base_url"):
            raise RunStop(
                "ENDPOINT_CHANGED",
                f"MILLM_BASE_URL changed since this run started (was {snapshot.get('base_url')}).",
            )
        version = session.get(Version, run.input_version_id)
        assert version is not None
        files = label_inputs.version_files(version.splits)
        cancel = CancelCheck(job.id)
        caller = caller_for(base_url, None, sleep=self._sleeper(cancel))
        ticket: LeaseTicket | None = None
        try:
            ticket = self._join_millm(session, run, caller, job)
            client = SaeActivationsClient(
                caller,
                str(snapshot["model_id"]),
                sae_id=str(spec["sae_id"]),
                top_k=int(spec["top_k"]),
                positions=str(spec["positions"]),
                features=spec["features"],
                lease_id=ticket.lease_id if ticket is not None else None,
            )
            label_store.recover_uncommitted(session, run, job.id)
            self._copy_reused(session, run, files, None)
            loop = _Loop(
                run=run,
                job_id=job.id,
                room=get_job_kind("label_run").room(run.id),
                counts=self._counts(session, run.id),
                rows_done=self._count(session, run.id),
                rows_goal=run.rows_total,
            )

            def score_row(row_key: str, fields: dict[str, Any]) -> LabelRecord | None:
                try:
                    read = client.read(fields)
                except ContextOverflow as exc:
                    loop.consecutive = 0
                    return LabelRecord(
                        row_key=row_key,
                        outcome="skipped",
                        parsed_value={"chars": _chars(fields)},
                        probability=None,
                        distribution=None,
                        raw_output={"error": exc.message},
                        rationale=None,
                        steering_state=labeling_rules.NOT_REPORTED,
                        latency_ms=None,
                        skip_reason="context_overflow",
                        scored_at=clock.utc_now(),
                    )
                except RowError as exc:
                    refused = fp.refusal_for(exc)
                    if refused is not None:
                        raise RunStop(refused.code, refused.message) from None
                    self._fail_row(loop, exc)
                    return None
                except (ModelNotResident, LeaseLost, StrictRefusal) as exc:
                    raise RunStop(exc.code, exc.message) from None
                except EndpointCallError as exc:
                    self._fail_row(loop, exc)
                    return None
                loop.consecutive = 0
                self._check_response(loop, read.model, None)
                try:
                    return fp.record_for(row_key, fields, read, sae_id=str(spec["sae_id"]))
                except pp.ProbeRefused as refused:
                    raise RunStop(refused.code, refused.message) from None

            self._loop(
                session, loop, files, None, None, None, caller, ticket, cancel, settings,
                score_row=score_row,
            )  # fmt: skip
            self._finish(session, run, job)
        finally:
            if ticket is not None:
                try:
                    self.deps.holder.leave(caller, ticket, job.id)
                except Exception as exc:  # noqa: BLE001 - Beat's cleanup releases it later
                    logger.warning("Could not leave lease %s: %s", ticket.lease_row_id, exc)
            caller.close()

    def _probe_gate(
        self,
        session: Session,
        run: LabelRun,
        job: Job,
        cancel: CancelCheck,
        score: Callable[[Mapping[str, Any]], Any],
        pp: Any,
        reproduction: Any,
    ) -> None:
        """FR-009.77: reproduce miStudio's AUROC on one evaluated role before writing a verdict.
        A pass (this run's, or a previous run's for the same check) is recorded on the run; a
        failure stops it naming both figures."""
        recorded = run.endpoint_snapshot.get("reproduction") or {}
        if recorded.get("state") == "passed":
            return
        retry = run.endpoint_snapshot.get("reproduction_retry")
        if recorded.get("state") == "failed" and not retry:
            # 2026-10-08 finding 3: this run's own gate failed; running it again scores the same
            # rows the same way. The service refuses the resume; this is the worker's floor.
            raise RunStop("REPRODUCTION_FAILED_BEFORE", reproduction.failure_message(recorded))
        cached = reproduction.passed_before(session, run.labeler_identity, exclude_run_id=run.id)
        if cached is not None:
            run.endpoint_snapshot = {**run.endpoint_snapshot, "reproduction": cached}
            session.commit()
            return
        probe = run.endpoint_snapshot["probe"]
        source = probe["mistudio_probe_id"]
        try:
            target = reproduction.target_for(session, None if source == pp.NOT_REPORTED else source)
        except reproduction.ReproductionUnavailable as missing:
            raise RunStop("REPRODUCTION_UNAVAILABLE", missing.message) from None
        failed = reproduction.failed_before(
            session, run.labeler_identity, target, exclude_run_id=run.id
        )
        if failed is not None and not retry:
            raise RunStop(
                "REPRODUCTION_FAILED_BEFORE",
                f"The same reproduction check failed on run {failed['failed_run_id']}. "
                + reproduction.RETRY_GUIDANCE,
            )
        record_progress(
            job.id,
            message=f"Reproduction check: scoring {target.n_rows} rows of "
            f"{target.view_name or target.role} before labeling.",
            force=True,
        )

        def score_row(row_key: str, fields: dict[str, Any]) -> float | None:
            cancel.raise_if_cancelled(
                "Stopped during the reproduction check.", {"label_run_id": run.id}
            )
            try:
                scored = score(fields)
            except ContextOverflow:
                return None
            except EndpointCallError as exc:
                raise RunStop(
                    exc.code,
                    f"The reproduction check could not score a row ({exc.message}); nothing was "
                    "labeled. Resume when miLLM answers.",
                ) from None
            if scored.error is not None:
                return None
            verdict = pp.the_verdict(scored, str(probe["probe_id"]), str(probe["window"]))
            value = verdict["score"]
            return float(value) if value is not None else None

        try:
            result = reproduction.run_gate(session, target, score_row)
        except reproduction.ReproductionUnavailable as missing:
            raise RunStop("REPRODUCTION_UNAVAILABLE", missing.message) from None
        except pp.ProbeRefused as refused:
            raise RunStop(refused.code, refused.message) from None
        if retry:
            result = {**result, "retry_of": retry}
        run.endpoint_snapshot = {**run.endpoint_snapshot, "reproduction": result}
        session.commit()
        logger.info(
            "label_run.reproduction run=%s state=%s auroc=%s",
            run.id,
            result["state"],
            result["millm_auroc"],
        )
        if result["state"] != "passed":
            raise RunStop("REPRODUCTION_FAILED", reproduction.failure_message(result))

    def _check_tei(self, caller: EndpointCaller, run: LabelRun) -> None:
        try:
            identity = read_identity(caller)
        except EndpointCallError as exc:
            raise RunStop(exc.code, exc.message) from None
        snapshot = run.endpoint_snapshot
        if identity.model_id != snapshot.get("model_id") or (
            snapshot.get("model_revision") is not None
            and identity.model_sha != snapshot.get("model_revision")
        ):
            raise RunStop(
                "MODEL_CHANGED",
                f"The TEI server now serves {identity.model_id}@{identity.model_sha}; the run "
                f"started on {snapshot.get('model_id')}@{snapshot.get('model_revision')}.",
            )

    def _decide_structured(
        self,
        session: Session,
        run: LabelRun,
        caller: EndpointCaller,
        rubric: RubricBody,
        files: list[Path],
        lease_id: str | None,
    ) -> None:
        """One probe request at the FIRST start (FR-005.43); the fingerprint follows the mode."""
        if (
            run.endpoint_snapshot.get("structured_decided")
            or run.structured_output != "json_schema"
        ):
            return
        if self._count(session, run.id) > 0:
            return
        sample = label_inputs.sample_rows(files, run.field_map, run.row_filter, 1, 0)
        mode = "strict_parse"
        if sample:
            try:
                mode = decide_structured_mode(
                    caller,
                    rubric,
                    str(run.endpoint_snapshot.get("model_id")),
                    sample[0][1],
                    run.question,
                    lease_id=lease_id,
                )
            except (ModelNotResident, LeaseLost) as exc:
                raise RunStop(exc.code, exc.message) from None
        run.structured_output = mode
        run.labeler_fingerprint = labeling_rules.fingerprint(
            run.labeler_identity, run.sampling, mode, run.packing
        )
        run.endpoint_snapshot = {**run.endpoint_snapshot, "structured_decided": True}
        session.commit()

    def _loop(
        self,
        session: Session,
        loop: _Loop,
        files: list[Path],
        classifier: ClassifierClient | None,
        judge: JudgeClient | None,
        positive: str | None,
        caller: EndpointCaller,
        ticket: LeaseTicket | None,
        cancel: CancelCheck,
        settings: Any,
        *,
        score_row: Callable[[str, dict[str, Any]], LabelRecord | None] | None = None,
    ) -> None:
        run = loop.run
        recorded = label_store.recorded_keys(session, run.id)
        index = label_store.next_chunk_index(session, run.id)
        buffer: list[LabelRecord] = []
        millm = run.endpoint_snapshot.get("server_kind") == "millm"
        rows: Iterator[tuple[str, dict[str, Any]]] = label_inputs.iterate_rows(
            files, run.field_map, run.row_filter, recorded
        )

        def flush() -> None:
            nonlocal index, buffer
            if not buffer:
                return
            path, sha = label_store.stage_chunk(
                run.id, index, buffer, after_rename=self.deps.after_rename
            )
            label_store.commit_chunk(session, run, index, loop.job_id, path, sha, buffer)
            logger.info(
                "label_run.chunk_committed run=%s chunk=%d rows=%d", run.id, index, len(buffer)
            )
            index += 1
            buffer = []

        try:
            for row_key, fields in rows:
                if cancel():
                    flush()
                    raise OperatorCancelled(
                        loop.job_id,
                        "cancelled",
                        "Stopped at a row boundary; every completed row is kept.",
                        {"label_run_id": run.id},
                    )
                if score_row is not None:
                    record = score_row(row_key, fields)
                elif classifier is not None:
                    record = self._score_one(
                        run, loop, classifier, row_key, fields, positive, millm
                    )
                else:
                    assert judge is not None
                    record = self._judge_one(run, loop, judge, row_key, fields)
                if record is not None:
                    buffer.append(record)
                    loop.counts[record.outcome] += 1
                    loop.rows_done += 1
                if len(buffer) >= run.chunk_size:
                    flush()
                    self._boundary(session, loop, caller, ticket, settings)
                self._progress(loop)
        except RunStop:
            if loop.first_fingerprint is not None:
                run.system_fingerprint = loop.first_fingerprint
            flush()
            session.commit()
            raise
        flush()
        if run.system_fingerprint != loop.first_fingerprint:
            run.system_fingerprint = loop.first_fingerprint
            session.commit()

    # --- the batch transport (FR-005.35, FR-005.55) ------------------------------------------

    def _save_batches(self, session: Session, run: LabelRun, items: list[dict[str, Any]]) -> None:
        # Fresh dicts: JSONB change detection compares values, and an item mutated in place
        # would compare equal to itself and never be written.
        run.endpoint_snapshot = {
            **run.endpoint_snapshot,
            "batches": [dict(b) for b in items],
            "pack": False,
        }
        run.batch_id = items[-1]["id"] if items else run.batch_id
        session.commit()

    def _batch_loop(
        self,
        session: Session,
        loop: _Loop,
        files: list[Path],
        client: OpenAIScoringClient,
        positive: str | None,
        caller: EndpointCaller,
        box: list[LeaseTicket | None],
        cancel: CancelCheck,
    ) -> None:
        """Rows go to miLLM as JSONL batches, unpacked, under the shared lease. Each batch is
        RECORDED on the run before it is polled, so a resume polls it again and never submits a
        duplicate. After a miLLM restart (every lease ends) the holder re-takes the lease and
        re-attaches each running batch (``ModelLeaseHolder.rejoin``)."""
        settings = get_settings()
        run = loop.run
        items: list[dict[str, Any]] = list(run.endpoint_snapshot.get("batches") or [])
        pending = [b for b in items if b.get("state") == "submitted"]
        if not pending:
            recorded = label_store.recorded_keys(session, run.id)
            rows = list(label_inputs.iterate_rows(files, run.field_map, run.row_filter, recorded))
            for start in range(0, len(rows), settings.label_batch_max_lines):
                group = rows[start : start + settings.label_batch_max_lines]
                lines = [
                    {
                        "custom_id": key,
                        "method": "POST",
                        "url": client.path,
                        "body": client.request_body(RenderedInput(key, fields, run.question)),
                    }
                    for key, fields in group
                ]
                ticket = box[0]
                try:
                    created = batches.submit(
                        caller,
                        lines,
                        endpoint=client.path,
                        lease_id=ticket.lease_id if ticket else None,
                        metadata={"label_run_id": run.id},
                    )
                except EndpointCallError as exc:
                    raise RunStop(exc.code, exc.message) from None
                items.append({"id": created["id"], "rows": len(lines), "state": "submitted"})
                self._save_batches(session, run, items)
            pending = [b for b in items if b.get("state") == "submitted"]
        for item in pending:
            batch = self._poll_batch(session, loop, caller, box, cancel, item, items)
            if batch.get("status") != "completed":
                item["state"] = batch.get("status", "failed")
                self._save_batches(session, run, items)
                raise RunStop(
                    "BATCH_FAILED",
                    f"miLLM batch {item['id']} ended {batch.get('status')}; rows it labeled before "
                    "are kept and a resume submits the rest.",
                )
            records = []
            for line in batches.output_lines(caller, batch.get("output_file_id")):
                record = self._batch_record(run, loop, client, line, positive)
                if record is not None:
                    records.append(record)
            for line in batches.output_lines(caller, batch.get("error_file_id")):
                record = self._batch_record(run, loop, client, line, positive)
                if record is not None:
                    records.append(record)
            index = label_store.next_chunk_index(session, run.id)
            for start in range(0, len(records), run.chunk_size):
                chunk = records[start : start + run.chunk_size]
                path, sha = label_store.stage_chunk(
                    run.id, index, chunk, after_rename=self.deps.after_rename
                )
                label_store.commit_chunk(session, run, index, loop.job_id, path, sha, chunk)
                for record in chunk:
                    loop.counts[record.outcome] += 1
                loop.rows_done += len(chunk)
                index += 1
            item["state"] = "committed"
            self._save_batches(session, run, items)
            self._progress(loop)
        if loop.first_fingerprint is not None:
            run.system_fingerprint = loop.first_fingerprint
            session.commit()

    def _poll_batch(
        self,
        session: Session,
        loop: _Loop,
        caller: EndpointCaller,
        box: list[LeaseTicket | None],
        cancel: CancelCheck,
        item: dict[str, Any],
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        settings = get_settings()
        sleep = self._sleeper(cancel)
        while True:
            if cancel():
                batches.cancel(caller, item["id"])
                raise OperatorCancelled(
                    loop.job_id,
                    "cancelled",
                    "Stopped; the miLLM batch was cancelled.",
                    {"label_run_id": loop.run.id},
                )
            try:
                batch = batches.get(caller, item["id"])
            except EndpointCallError as exc:
                raise RunStop(exc.code, exc.message) from None
            if batch.get("status") in batches.TERMINAL:
                return batch
            waiting = (batch.get("millm") or {}).get("waiting_reason")
            ticket = box[0]
            lost = waiting == "lease_unavailable"
            if ticket is not None and not lost:
                try:
                    self.deps.holder.check(caller, ticket)
                except LeaseLost:
                    lost = True
            if lost and ticket is not None:
                running = [b["id"] for b in items if b.get("state") == "submitted"]
                try:
                    # Our lease may be alive and the batch merely detached from it (a resume
                    # after a restart): hand it the lease we hold. Only if that is refused is the
                    # lease re-taken.
                    self.deps.holder.reattach(caller, ticket, running)
                except LeaseLost:
                    try:
                        box[0] = self.deps.holder.rejoin(caller, ticket, loop.job_id, running)
                    except (LeaseLost, LeaseHeldElsewhere, ModelNotLoaded) as exc:
                        raise RunStop(
                            "LEASE_LOST",
                            f"miLLM restarted and the lease could not be re-taken ({exc}); the "
                            "run stopped and can be resumed. No batch was resubmitted.",
                        ) from None
                logger.info("label_run.lease_rejoined run=%s batches=%d", loop.run.id, len(running))
                continue
            record_progress(loop.job_id, message=f"Waiting for miLLM batch {item['id']}.")
            sleep(settings.label_batch_poll_seconds)

    def _batch_record(
        self,
        run: LabelRun,
        loop: _Loop,
        client: OpenAIScoringClient,
        line: dict[str, Any],
        positive: str | None,
    ) -> LabelRecord | None:
        key = str(line.get("custom_id"))
        now = clock.utc_now()
        response = line.get("response") or {}
        error = line.get("error")
        status = response.get("status_code") if isinstance(response, dict) else None
        if error or status != 200:
            body = response.get("body") if isinstance(response, dict) else None
            code = (error or {}).get("code") if isinstance(error, dict) else None
            if code is None and isinstance(body, dict) and isinstance(body.get("error"), dict):
                code = body["error"].get("code")
            if code == "context_length_exceeded":
                return LabelRecord(
                    row_key=key,
                    outcome="skipped",
                    parsed_value={},
                    probability=None,
                    distribution=None,
                    raw_output={"error": error or body},
                    rationale=None,
                    steering_state=labeling_rules.NOT_REPORTED,
                    latency_ms=None,
                    skip_reason="context_overflow",
                    scored_at=now,
                )
            return None  # unrecorded: a resume scores it again
        headers = (response.get("millm") or {}).get("headers") or {}
        result = client.result_from(
            response.get("body"),
            latency_ms=0,
            steering_header=headers.get("X-miLLM-Steering"),
            request_body={},
        )
        self._check_response(loop, result.response_model, result.system_fingerprint)
        outcome, probability = decide_outcome(run, result.distribution, positive)
        top = max(result.distribution.items(), key=lambda kv: kv[1])[0]
        return LabelRecord(
            row_key=key,
            outcome=outcome,
            parsed_value={"top": top, "prompt_tokens": result.prompt_tokens, "batch": True},
            probability=probability,
            distribution=dict(result.distribution),
            raw_output=result.raw_output,
            rationale=None,
            steering_state=result.steering_header or labeling_rules.UNSTEERED_SCORING,
            latency_ms=None,
            skip_reason=None,
            scored_at=now,
        )

    def _boundary(
        self,
        session: Session,
        loop: _Loop,
        caller: EndpointCaller,
        ticket: LeaseTicket | None,
        settings: Any,
    ) -> None:
        run = loop.run
        if loop.first_fingerprint is not None and run.system_fingerprint != loop.first_fingerprint:
            run.system_fingerprint = loop.first_fingerprint
            session.commit()
        if ticket is not None:
            try:
                self.deps.holder.check(caller, ticket)
            except LeaseLost as lost:
                raise RunStop(
                    "LEASE_LOST",
                    f"The miLLM lease was lost ({lost.reason}); the run stopped at a chunk "
                    "boundary and can be resumed.",
                ) from None
        if run.kind == "judge" and labeling_rules.parse_failure_exceeded(
            loop.parse_failures,
            loop.judged,
            settings.judge_parse_failure_stop_share,
            settings.judge_parse_failure_min_rows,
        ):
            raise RunStop(
                "PARSE_FAILURES",
                f"{loop.parse_failures} of {loop.judged} judge answers did not parse, above "
                f"{settings.judge_parse_failure_stop_share:.0%}. Rows so far are kept.",
            )
        if run.endpoint_snapshot.get("protocol") == "tei_classification":
            self._check_tei(caller, run)
        record_progress(
            loop.job_id,
            progress=100.0 * loop.rows_done / max(1, loop.rows_goal),
            message=f"{loop.rows_done} of {loop.rows_goal} rows labeled.",
        )

    def _fail_row(self, loop: _Loop, exc: EndpointCallError) -> None:
        settings = get_settings()
        loop.consecutive += 1
        loop.last_error = f"{exc.code}: {exc.message}"
        if loop.consecutive >= settings.label_max_consecutive_failures:
            raise RunStop(
                "ENDPOINT_FAILING",
                f"{loop.consecutive} consecutive rows failed; the last error was "
                f"{loop.last_error}. Completed rows are kept; resume when the endpoint is fixed.",
            )

    def _check_response(self, loop: _Loop, model: str | None, fingerprint: str | None) -> None:
        run = loop.run
        expected = run.endpoint_snapshot.get("model_id")
        if model is not None and model != expected:
            raise RunStop(
                "MODEL_CHANGED",
                f"A response named {model}, not {expected}; the run stopped and can be resumed.",
            )
        if fingerprint is not None:
            if loop.first_fingerprint is None:
                loop.first_fingerprint = fingerprint
            elif fingerprint != loop.first_fingerprint:
                raise RunStop(
                    "MODEL_CHANGED",
                    "miLLM's system_fingerprint changed mid-run (the model, its revision or its "
                    "precision changed); the run stopped and can be resumed.",
                )

    def _score_one(
        self,
        run: LabelRun,
        loop: _Loop,
        client: ClassifierClient,
        row_key: str,
        fields: dict[str, Any],
        positive: str | None,
        millm: bool,
    ) -> LabelRecord | None:
        now = clock.utc_now()
        try:
            result = client.score(RenderedInput(row_key, fields, run.question))
        except ContextOverflow as exc:
            logger.info("label_run.row_skipped run=%s reason=context_overflow", run.id)
            loop.consecutive = 0
            return LabelRecord(
                row_key=row_key,
                outcome="skipped",
                parsed_value={"chars": _chars(fields)},
                probability=None,
                distribution=None,
                raw_output={"error": exc.message},
                rationale=None,
                steering_state=labeling_rules.NOT_REPORTED,
                latency_ms=None,
                skip_reason="context_overflow",
                scored_at=now,
            )
        except StrictRefusal as exc:
            raise RunStop("STRICT_REFUSAL", exc.message) from None
        except (ModelNotResident, LeaseLost) as exc:
            raise RunStop(exc.code, exc.message) from None
        except EndpointCallError as exc:
            self._fail_row(loop, exc)
            return None
        loop.consecutive = 0
        self._check_response(loop, result.response_model, result.system_fingerprint)
        outcome, probability = decide_outcome(run, result.distribution, positive)
        steering = result.steering_header or (
            labeling_rules.UNSTEERED_SCORING if millm else labeling_rules.NOT_REPORTED
        )
        top = max(result.distribution.items(), key=lambda kv: kv[1])[0]
        return LabelRecord(
            row_key=row_key,
            outcome=outcome,
            parsed_value={
                "top": top,
                "chars": _chars(fields),
                "prompt_tokens": result.prompt_tokens,
            },
            probability=probability,
            distribution=dict(result.distribution),
            raw_output=result.raw_output,
            rationale=None,
            steering_state=steering,
            latency_ms=result.latency_ms,
            skip_reason=None,
            scored_at=now,
        )

    def _judge_one(
        self,
        run: LabelRun,
        loop: _Loop,
        client: JudgeClient,
        row_key: str,
        fields: dict[str, Any],
    ) -> LabelRecord | None:
        now = clock.utc_now()
        try:
            result = client.judge(RenderedInput(row_key, fields, run.question))
        except ContextOverflow as exc:
            loop.consecutive = 0
            return LabelRecord(
                row_key=row_key,
                outcome="skipped",
                parsed_value={"chars": _chars(fields)},
                probability=None,
                distribution=None,
                raw_output={"error": exc.message},
                rationale=None,
                steering_state=labeling_rules.NOT_REPORTED,
                latency_ms=None,
                skip_reason="context_overflow",
                scored_at=now,
            )
        except StrictRefusal as exc:
            raise RunStop("STRICT_REFUSAL", exc.message) from None
        except (ModelNotResident, LeaseLost) as exc:
            raise RunStop(exc.code, exc.message) from None
        except EndpointCallError as exc:
            self._fail_row(loop, exc)
            return None
        loop.consecutive = 0
        self._check_response(loop, result.response_model, result.system_fingerprint)
        if result.pairwise:
            outcome = labeling_rules.swap_and_agree(result.verdict, result.swapped_verdict)
        else:
            outcome = result.verdict if result.verdict is not None else "parse_failure"
        loop.judged += 1
        if outcome == "parse_failure":
            loop.parse_failures += 1
        return LabelRecord(
            row_key=row_key,
            outcome=outcome,
            parsed_value={
                "verdict": result.verdict,
                "swapped_verdict": result.swapped_verdict,
                "score": result.score,
                "chars": _chars(fields),
                "seed": result.seed_echo or labeling_rules.SEED_NOT_CONFIRMED,
            },
            probability=None,
            distribution=None,
            raw_output=result.raw_output,
            rationale=result.rationale,
            steering_state=result.steering_header or labeling_rules.NOT_REPORTED,
            latency_ms=result.latency_ms,
            skip_reason=None,
            scored_at=now,
        )

    # --- progress ---------------------------------------------------------------------------

    def _progress(self, loop: _Loop) -> None:
        now = clock.monotonic()
        loop.samples.append((now, loop.rows_done))
        while loop.samples and now - loop.samples[0][0] > RATE_WINDOW_S:
            loop.samples.popleft()
        if now - loop.last_emit < EMIT_INTERVAL_S:
            return
        loop.last_emit = now
        first_t, first_n = loop.samples[0]
        rate = (loop.rows_done - first_n) / (now - first_t) if now > first_t else None
        remaining = max(0, loop.rows_goal - loop.rows_done)
        self.deps.emit(
            loop.room,
            "label_run:progress",
            {
                "label_run_id": loop.run.id,
                "job_id": loop.job_id,
                "rows_done": loop.rows_done,
                "rows_total": loop.rows_goal,
                "rows_per_second": rate,
                "eta_seconds": (remaining / rate) if rate else None,
                "counts": dict(loop.counts),
            },
        )

    def _emit_now(self, run: LabelRun, event: str, data: dict[str, Any]) -> None:
        self.deps.emit(
            get_job_kind("label_run").room(run.id), event, {"label_run_id": run.id, **data}
        )

    # --- reuse, counts, finish ----------------------------------------------------------------

    @staticmethod
    def _count(session: Session, run_id: str) -> int:
        return int(
            session.execute(
                select(func.count()).select_from(Label).where(Label.label_run_id == run_id)
            ).scalar_one()
        )

    @staticmethod
    def _counts(session: Session, run_id: str) -> Counter[str]:
        rows = session.execute(
            select(Label.outcome, func.count())
            .where(Label.label_run_id == run_id)
            .group_by(Label.outcome)
        ).all()
        return Counter({str(outcome): int(n) for outcome, n in rows})

    def _copy_reused(
        self, session: Session, run: LabelRun, files: list[Path], positive: str | None
    ) -> None:
        """Copy labels this run's fingerprint already produced (FR-005.29). Only when the
        revision was reported: without one, sameness of model cannot be shown."""
        revision = run.endpoint_snapshot.get("model_revision")
        if not labeling_rules.reuse_allowed(revision, revision) or not run.revision_reported:
            return
        keys = label_inputs.row_keys(files, run.row_filter).column("row_key").to_pylist()
        copied = 0
        for start in range(0, len(keys), REUSE_BATCH):
            batch = [str(k) for k in keys[start : start + REUSE_BATCH]]
            source = session.execute(
                select(Label)
                .join(LabelRun, LabelRun.id == Label.label_run_id)
                .where(
                    Label.labeler_fingerprint == run.labeler_fingerprint,
                    LabelRun.revision_reported.is_(True),
                    Label.outcome.not_in(NOT_REUSABLE),
                    Label.label_run_id != run.id,
                    Label.row_key.in_(batch),
                )
                .order_by(Label.row_key, Label.scored_at)
                .ext(distinct_on(Label.row_key))
            ).scalars()
            records = []
            for label in source:
                if run.kind == "classifier" and label.distribution:
                    outcome, probability = decide_outcome(run, label.distribution, positive)
                else:
                    outcome, probability = label.outcome, label.probability
                records.append(
                    LabelRecord(
                        row_key=label.row_key,
                        outcome=outcome,
                        parsed_value=label.parsed_value,
                        probability=probability,
                        distribution=label.distribution,
                        raw_output=label.raw_output,
                        rationale=label.rationale,
                        steering_state=label.steering_state,
                        latency_ms=label.latency_ms,
                        skip_reason=None,
                        scored_at=clock.utc_now(),
                        reused_from_run_id=label.label_run_id,
                    )
                )
            copied += label_store.insert_records(session, run, records, None)
            session.commit()
        if copied:
            run.rows_reused = int(run.rows_reused) + copied
            session.commit()

    def _finish(self, session: Session, run: LabelRun, job: Job) -> None:
        counts = self._counts(session, run.id)
        run.counts = dict(counts)
        if run.kind in ("classifier", "rederived"):
            scored = sum(n for o, n in counts.items() if o not in ("skipped",))
            kept = scored - counts.get("excluded", 0)
            run.keep_share_actual = (kept / scored) if scored else None
        run.length_correlation = self._length_correlation(session, run)
        session.commit()
        label_store.export_labels(session, run)
        set_state(session, run, "completed")
        record_progress(
            job.id,
            status="completed",
            progress=100.0,
            message=f"{sum(counts.values())} rows labeled.",
            result={"label_run_id": run.id, "counts": dict(counts)},
        )
        logger.info("label_run.completed run=%s rows=%d", run.id, sum(counts.values()))
        self._emit_now(run, "label_run:completed", {"counts": dict(counts)})

    @staticmethod
    def _length_correlation(session: Session, run: LabelRun) -> dict[str, Any] | None:
        """Spearman's rho between the numeric score and row length (FR-005.44)."""
        rows = session.execute(
            select(Label.probability, Label.parsed_value).where(Label.label_run_id == run.id)
        ).all()
        chars_pairs: list[tuple[float, float]] = []
        token_pairs: list[tuple[float, float]] = []
        for probability, parsed in rows:
            score = probability
            if (
                score is None
                and isinstance(parsed, dict)
                and isinstance(parsed.get("score"), int | float)
            ):
                score = float(parsed["score"])
            if score is None or not isinstance(parsed, dict):
                continue
            if isinstance(parsed.get("chars"), int):
                chars_pairs.append((float(score), float(parsed["chars"])))
            if isinstance(parsed.get("prompt_tokens"), int):
                token_pairs.append((float(score), float(parsed["prompt_tokens"])))
        if not chars_pairs:
            return None
        return {
            "chars": spearman(chars_pairs),
            "tokens": spearman(token_pairs) if token_pairs else None,
        }

    # --- re-derive and aggregate --------------------------------------------------------------

    def _rederive(self, session: Session, run: LabelRun, job: Job) -> None:
        """New thresholds over the parent's stored probabilities; NO endpoint call (FR-005.21)."""
        parent_id = run.parent_run_ids[0]
        template = session.get(DecisionTemplate, run.template_id) if run.template_id else None
        positive = positive_class_of(run, template.body if template else None)
        cancel = CancelCheck(job.id)
        last = ""
        while True:
            cancel.raise_if_cancelled("Stopped while re-deriving.", {"label_run_id": run.id})
            parents = list(
                session.execute(
                    select(Label)
                    .where(Label.label_run_id == parent_id, Label.row_key > last)
                    .order_by(Label.row_key)
                    .limit(REUSE_BATCH)
                ).scalars()
            )
            if not parents:
                break
            records = []
            for label in parents:
                if label.distribution:
                    outcome, probability = decide_outcome(run, label.distribution, positive)
                else:
                    outcome, probability = label.outcome, label.probability
                records.append(
                    LabelRecord(
                        row_key=label.row_key,
                        outcome=outcome,
                        parsed_value=label.parsed_value,
                        probability=probability,
                        distribution=label.distribution,
                        raw_output=None,
                        rationale=None,
                        steering_state=label.steering_state,
                        latency_ms=None,
                        skip_reason=label.skip_reason,
                        scored_at=clock.utc_now(),
                    )
                )
            label_store.insert_records(session, run, records, None)
            session.commit()
            last = parents[-1].row_key
        self._finish(session, run, job)

    def _aggregate(self, session: Session, run: LabelRun, job: Job) -> None:
        """Unanimous → verdict; majority → verdict, flagged; tie → excluded, flagged (FR-005.45)."""
        parents = list(run.parent_run_ids)
        verdicts: dict[str, dict[str, str | None]] = {}
        for label in session.execute(
            select(Label.label_run_id, Label.row_key, Label.outcome).where(
                Label.label_run_id.in_(parents)
            )
        ).all():
            run_id, row_key, outcome = label
            value = (
                None
                if outcome in ("parse_failure", "position_inconsistent", "skipped")
                else outcome
            )
            verdicts.setdefault(str(row_key), {})[str(run_id)] = value
        records = []
        for row_key in sorted(verdicts):
            per_run = {p: verdicts[row_key].get(p) for p in parents}
            outcome, flag = labeling_rules.aggregate_verdict(list(per_run.values()))
            records.append(
                LabelRecord(
                    row_key=row_key,
                    outcome=outcome,
                    parsed_value={"verdicts": per_run, "flag": flag},
                    probability=None,
                    distribution=None,
                    raw_output=None,
                    rationale=None,
                    steering_state="not applicable (aggregate of judge runs)",
                    latency_ms=None,
                    skip_reason=None,
                    scored_at=clock.utc_now(),
                )
            )
            if len(records) >= REUSE_BATCH:
                label_store.insert_records(session, run, records, None)
                session.commit()
                records = []
        label_store.insert_records(session, run, records, None)
        session.commit()
        self._finish(session, run, job)


def spearman(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    """Spearman's rho with n; None (with the reason) when it is undefined."""
    if len(pairs) < 3:
        return {"rho": None, "n": len(pairs), "reason": "fewer than 3 rows"}
    from scipy.stats import spearmanr

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return {"rho": None, "n": len(pairs), "reason": "a constant column"}
    rho = float(spearmanr(xs, ys).statistic)
    return {"rho": rho, "n": len(pairs), "reason": None}


# --- "Try it on a sample" and the keep-share preview (FR-005.19, FR-005.20) -------------------


@dataclass(frozen=True)
class SampleSpec:
    """What a sample or a preview scores: never written as labels, never under a lease."""

    role: str
    protocol: str
    base_url: str
    model_id: str
    api_key: str | None = field(repr=False)
    template_body: dict[str, Any] | None
    rubric_body: dict[str, Any] | None
    question: str | None
    threshold_positive: float | None
    threshold_negative: float | None
    min_top_probability: float | None
    label_set: list[str]
    server_kind: str


def _transient_run(spec: SampleSpec) -> LabelRun:
    """An unsaved run carrying the thresholds, so the sample decides through decide_outcome."""
    return LabelRun(
        label_set=spec.label_set,
        threshold_positive=spec.threshold_positive,
        threshold_negative=spec.threshold_negative,
        min_top_probability=spec.min_top_probability,
    )


def _has_thresholds(spec: SampleSpec) -> bool:
    if len(spec.label_set) == 2:
        return spec.threshold_positive is not None and spec.threshold_negative is not None
    return spec.min_top_probability is not None


def score_sample(
    spec: SampleSpec, rows: list[tuple[str, dict[str, Any]]], *, deadline_s: float
) -> list[dict[str, Any]]:
    """Score ≤ 20 rows synchronously with refuse-load and NO lease (FR-005.20)."""
    started = clock.monotonic()

    def on_wait(seconds: float, reason: str) -> None:
        if clock.monotonic() + seconds - started > deadline_s:
            raise RunStop("SAMPLE_TIMEOUT", f"The endpoint is busy ({reason}); try again shortly.")

    out: list[dict[str, Any]] = []
    run = _transient_run(spec)
    positive = positive_class_of(run, spec.template_body)
    with caller_for(spec.base_url, spec.api_key, on_wait=on_wait, transient_retries=0) as caller:
        classifier: ClassifierClient | None = None
        judge: JudgeClient | None = None
        if spec.template_body is not None:
            classifier = build_classifier(
                caller, spec.protocol, parse_template(spec.template_body), spec.model_id
            )
        else:
            assert spec.rubric_body is not None
            judge = build_judge(caller, RubricBody.model_validate(spec.rubric_body), spec.model_id)
        for row_key, fields in rows:
            text = " | ".join(str(v) for v in fields.values() if v is not None)
            item: dict[str, Any] = {
                "row_key": row_key,
                "text": text[:2000],
                "probability": None,
                "distribution": None,
                "outcome": None,
                "verdict": None,
                "rationale": None,
                "latency_ms": None,
                "error": None,
            }
            try:
                if classifier is not None:
                    result = classifier.score(RenderedInput(row_key, fields, spec.question))
                    item["distribution"] = dict(result.distribution)
                    item["latency_ms"] = result.latency_ms
                    if _has_thresholds(spec):
                        item["outcome"], item["probability"] = decide_outcome(
                            run, result.distribution, positive
                        )
                    elif positive is not None:
                        item["probability"] = float(result.distribution[positive])
                else:
                    assert judge is not None
                    verdict = judge.judge(RenderedInput(row_key, fields, spec.question))
                    item["verdict"] = verdict.verdict
                    item["rationale"] = verdict.rationale
                    item["latency_ms"] = verdict.latency_ms
                    item["outcome"] = (
                        labeling_rules.swap_and_agree(verdict.verdict, verdict.swapped_verdict)
                        if verdict.pairwise
                        else (verdict.verdict or "parse_failure")
                    )
            except ContextOverflow as exc:
                item["outcome"], item["error"] = "skipped", exc.message
            except (ModelNotResident, LeaseLost, StrictRefusal) as exc:
                raise RunStop(exc.code, exc.message) from None
            except EndpointCallError as exc:
                item["error"] = f"{exc.code}: {exc.message}"
            out.append(item)
            if clock.monotonic() - started > deadline_s:
                break
    return out


def keep_share(
    spec: SampleSpec, rows: list[tuple[str, dict[str, Any]]], cancel: Callable[[], None]
) -> dict[str, Any]:
    """Score a random sample and report the share the band keeps, with its Wilson interval."""
    run = _transient_run(spec)
    positive = positive_class_of(run, spec.template_body)
    kept = n = 0
    probabilities: list[float] = []
    with caller_for(spec.base_url, spec.api_key) as caller:
        assert spec.template_body is not None
        client = build_classifier(
            caller, spec.protocol, parse_template(spec.template_body), spec.model_id
        )
        for row_key, fields in rows:
            cancel()
            try:
                result = client.score(RenderedInput(row_key, fields, spec.question))
            except ContextOverflow:
                continue
            except (ModelNotResident, LeaseLost, StrictRefusal) as exc:
                raise RunStop(exc.code, exc.message) from None
            outcome, probability = decide_outcome(run, result.distribution, positive)
            n += 1
            kept += outcome != "excluded"
            if probability is not None:
                probabilities.append(probability)
    if n == 0:
        raise RunStop("KEEP_SHARE_EMPTY", "No sampled row could be scored.")
    share, lo, hi = labeling_rules.wilson_interval(kept, n)
    return {"share": share, "lo": lo, "hi": hi, "n": n, "probabilities": probabilities}
