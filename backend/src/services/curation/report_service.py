"""Find-or-run for version reports: existing, inline or a job (FR-004.7; FTDD 004 §5.2, §7.1).

A report is identified by (version, kind, operator name, operator version, parameter hash, inputs
digest). Versions are immutable, so a completed report answers every later request forever.

- :func:`find_or_run_inline` — look up a completed report; otherwise compute it in THIS process
  under a PostgreSQL advisory lock on the identity (so two callers compute it once; the loser reads
  the winner's row) and store it completed. ``evaluate_warnings`` and ``check_leakage`` use it, so a
  publish check never sees "pending" (FTDD §2.2 step 5).
- :func:`start_job` — write a ``running`` row and a ``curation_report`` job; the worker task
  ``midataworks.curation.run_report`` computes and completes it (``workers/curation_tasks.py``).

Report persistence uses its OWN session, never the caller's: committing a caller's session would
commit whatever else the caller had pending (feature 008 passes its publish-check session).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.canonical_json import canonical_sha256
from ...core.clock import utc_now
from ...core.database import sync_session_factory
from ...core.ids import new_id
from ...models.curation import ReportState, VersionReport
from ...models.job import Job
from .codes import ReportInput

logger = logging.getLogger(__name__)

#: report kind -> (operator name, operator version, manifest hash), filled from the operators.
Identity = tuple[str, str, str]
Compute = Callable[[Session, list[ReportInput], dict[str, Any], int], "Computed"]


@dataclass
class Computed:
    result: dict[str, Any]
    artefacts: list[dict[str, Any]]
    rows: int


def params_hash(params: dict[str, Any]) -> str:
    return canonical_sha256(params)


def inputs_document(inputs: Sequence[ReportInput]) -> list[dict[str, Any]]:
    return [ri.as_dict() for ri in inputs]


def inputs_digest(inputs: Sequence[ReportInput]) -> str:
    return canonical_sha256(inputs_document(inputs))


def identity_of(kind: str) -> Identity:
    from ...operators.manifest import OperatorManifest, manifest_hash
    from ...operators.native.curation import REPORT_OPERATORS

    manifest: OperatorManifest = REPORT_OPERATORS[kind].manifest  # type: ignore[attr-defined]
    return manifest.name, manifest.version, manifest_hash(manifest)


def find_completed(
    session: Session, kind: str, inputs: Sequence[ReportInput], params: dict[str, Any]
) -> VersionReport | None:
    name, version, _ = identity_of(kind)
    return session.execute(
        select(VersionReport).where(
            VersionReport.version_id == inputs[0].version_id,
            VersionReport.kind == kind,
            VersionReport.operator_name == name,
            VersionReport.operator_version == version,
            VersionReport.params_hash == params_hash(params),
            VersionReport.inputs_digest == inputs_digest(inputs),
            VersionReport.state == ReportState.COMPLETED,
        )
    ).scalar_one_or_none()


def latest_completed(
    session: Session, version_id: str, kind: str, *, where: Callable[[Any], bool] | None = None
) -> VersionReport | None:
    """The newest completed report of ``kind`` for a version (the read routes)."""
    rows = session.execute(
        select(VersionReport)
        .where(
            VersionReport.version_id == version_id,
            VersionReport.kind == kind,
            VersionReport.state == ReportState.COMPLETED,
        )
        .order_by(VersionReport.completed_at.desc())
    ).scalars()
    for row in rows:
        if where is None or where(row):
            return row
    return None


def _lock_key(kind: str, inputs: Sequence[ReportInput], params: dict[str, Any]) -> int:
    digest = canonical_sha256([kind, inputs_digest(inputs), params_hash(params)])
    return int.from_bytes(bytes.fromhex(digest[:16]), "big", signed=True)


def find_or_run_inline(
    kind: str,
    inputs: Sequence[ReportInput],
    params: dict[str, Any],
    seed: int,
    compute: Compute,
    *,
    started_by: str,
    origin: str,
) -> tuple[str, VersionReport]:
    """``("existing" | "inline", report)``. Computes in this process when nothing is stored."""
    with sync_session_factory()() as session:
        found = find_completed(session, kind, inputs, params)
        if found is not None:
            return "existing", found
        session.execute(
            text("SELECT pg_advisory_xact_lock(:k)"), {"k": _lock_key(kind, inputs, params)}
        )
        found = find_completed(session, kind, inputs, params)
        if found is not None:
            session.commit()
            return "existing", found
        started = time.monotonic()
        logger.info(
            "report start kind=%s version=%s params=%s",
            kind,
            inputs[0].version_id,
            params_hash(params)[:12],
        )
        computed = compute(session, list(inputs), params, seed)
        row = _completed_row(kind, inputs, params, seed, computed, started_by, origin, None)
        session.add(row)
        try:
            session.commit()
        except IntegrityError:  # a racing writer without the lock (should not happen)
            session.rollback()
            found = find_completed(session, kind, inputs, params)
            if found is None:
                raise
            return "existing", found
        logger.info(
            "report end kind=%s version=%s rows=%d seconds=%.2f params=%s",
            kind,
            inputs[0].version_id,
            computed.rows,
            time.monotonic() - started,
            params_hash(params)[:12],
        )
        session.refresh(row)
        return "inline", row


def _completed_row(
    kind: str,
    inputs: Sequence[ReportInput],
    params: dict[str, Any],
    seed: int,
    computed: Computed,
    started_by: str,
    origin: str,
    job_id: str | None,
) -> VersionReport:
    name, version, digest = identity_of(kind)
    return VersionReport(
        id=str(uuid.uuid4()),
        version_id=inputs[0].version_id,
        kind=kind,
        operator_name=name,
        operator_version=version,
        manifest_hash=digest,
        params_hash=params_hash(params),
        params=params,
        inputs=inputs_document(inputs),
        inputs_digest=inputs_digest(inputs),
        seed=int(seed),
        state=ReportState.COMPLETED,
        result=computed.result,
        artefacts=computed.artefacts,
        job_id=job_id,
        started_by=started_by,
        started_by_origin=origin,
        completed_at=utc_now(),
    )


def start_job(
    session: Session,
    kind: str,
    inputs: Sequence[ReportInput],
    params: dict[str, Any],
    seed: int,
    *,
    started_by: str,
    origin: str,
) -> tuple[Job, VersionReport]:
    """A ``running`` report row and a queued ``curation_report`` job that will complete it."""
    name, version, digest = identity_of(kind)
    job = Job(
        id=new_id("job"),
        kind="curation_report",
        status="queued",
        progress=0.0,
        params={},
        started_by=started_by,
        started_by_origin=origin,
    )
    session.add(job)
    session.flush()
    report = VersionReport(
        id=str(uuid.uuid4()),
        version_id=inputs[0].version_id,
        kind=kind,
        operator_name=name,
        operator_version=version,
        manifest_hash=digest,
        params_hash=params_hash(params),
        params=params,
        inputs=inputs_document(inputs),
        inputs_digest=inputs_digest(inputs),
        seed=int(seed),
        state=ReportState.RUNNING,
        job_id=job.id,
        started_by=started_by,
        started_by_origin=origin,
    )
    session.add(report)
    job.params = {"report_id": report.id, "kind": kind, "version_id": inputs[0].version_id}
    session.commit()
    return job, report


def running_for(
    session: Session, kind: str, inputs: Sequence[ReportInput], params: dict[str, Any]
) -> VersionReport | None:
    """A report of this identity already running in a job (so a repeat run returns its job)."""
    name, version, _ = identity_of(kind)
    return session.execute(
        select(VersionReport).where(
            VersionReport.version_id == inputs[0].version_id,
            VersionReport.kind == kind,
            VersionReport.operator_name == name,
            VersionReport.operator_version == version,
            VersionReport.params_hash == params_hash(params),
            VersionReport.inputs_digest == inputs_digest(inputs),
            VersionReport.state == ReportState.RUNNING,
        )
    ).scalar_one_or_none()
