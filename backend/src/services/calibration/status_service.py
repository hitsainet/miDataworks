"""The calibration status lookup (FR-006.20): the latest record and verdict for a labeler.

Consumers: 005's Label-step callout (REST), 008's checks C-6 and card field M-8 (in-process, through
``services/publishing/feature_seams.calibration_status``), 009's check D-7.

``latest(identity_hash=…)`` keys on 005's labeler identity hash. ``latest(fingerprint=…)``
RESOLVES the fingerprint to its identity — through a record carrying it, else through the label
run that produced it — and then returns the newest record for that identity, so a fingerprint never
matches a different labeler's record. The result is field for field 008's ``CalibrationRef`` minus
``labeler_fingerprint``, with ``rows_shipped`` always false (P-14).

Synchronous (008 calls it from a worker with a sync session); the route runs it through
``AsyncSession.run_sync``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.calibration import CalibrationRecord, CalibrationSet, CalibrationVerdict
from ...models.label_run import LabelRun
from ...schemas.calibration import AurocRef, CalibrationSetRef, CalibrationStatus

NONE_RECORDED = CalibrationStatus(
    status="none_recorded",
    record_id=None,
    verdict=None,
    rule=None,
    auroc=None,
    calibration_set=None,
)


def identity_for_fingerprint(session: Session, fingerprint: str) -> str | None:
    from_record = session.execute(
        select(CalibrationRecord.labeler_identity_hash)
        .where(CalibrationRecord.labeler_fingerprint == fingerprint)
        .limit(1)
    ).scalar_one_or_none()
    if from_record is not None:
        return str(from_record)
    from_run = session.execute(
        select(LabelRun.labeler_identity_hash)
        .where(LabelRun.labeler_fingerprint == fingerprint)
        .limit(1)
    ).scalar_one_or_none()
    return str(from_run) if from_run is not None else None


def latest(
    *, identity_hash: str | None = None, fingerprint: str | None = None, session: Session
) -> CalibrationStatus:
    if (identity_hash is None) == (fingerprint is None):
        raise ValueError("pass exactly one of identity_hash and fingerprint")
    if fingerprint is not None:
        identity_hash = identity_for_fingerprint(session, fingerprint)
        if identity_hash is None:
            return NONE_RECORDED
    record = session.execute(
        select(CalibrationRecord)
        .where(CalibrationRecord.labeler_identity_hash == identity_hash)
        .order_by(CalibrationRecord.created_at.desc(), CalibrationRecord.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if record is None:
        return NONE_RECORDED
    verdict = session.get(CalibrationVerdict, record.id)
    cal = session.get(CalibrationSet, record.calibration_set_id)
    assert verdict is not None and cal is not None
    auroc = record.metrics.get("auroc")
    return CalibrationStatus(
        status="recorded",
        record_id=record.id,
        verdict=verdict.verdict,
        rule=verdict.rule,
        auroc=(
            AurocRef(
                value=float(auroc["value"]),
                ci_low=float(auroc["ci_low"]),
                ci_high=float(auroc["ci_high"]),
                n=int(auroc["n"]),
            )
            if auroc
            else None
        ),
        calibration_set=CalibrationSetRef(
            id=cal.id,
            licence_class=cal.licence_class,
            rows_shipped=False,
        ),
    )
