"""Calibration routes (FTDD 006 section 5.1): sets, records, status and targets.

AUROC is the area under the receiver operating characteristic curve; CI a confidence interval.

- Every write resolves who through ``resolve_who`` (C5); a who in a body is never read.
- ``POST /calibration-records`` starts a job and answers ``202`` with its room; calibration never
  runs inside a request.
- ``PUT /calibration-targets`` is gated for agents as ``gate_target_write`` (S3-08): an agent's
  request answers ``202`` with an approval; the stored request carries the card facts and the ID of
  the target the agent saw, inside the digest, and a stale approval writes nothing.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, requires_approval_when_agent, resolve_who
from ....core.database import get_db
from ....core.errors import UnprocessableError
from ....schemas.calibration import (
    CalibrationRecordList,
    CalibrationRecordOut,
    CalibrationRecordStart,
    CalibrationSetFromReview,
    CalibrationSetImport,
    CalibrationSetList,
    CalibrationSetOut,
    CalibrationSetPreview,
    CalibrationStatus,
    JobStarted,
    TargetIn,
    TargetOut,
    TargetsOut,
)
from ....services.calibration import record_service, set_service, status_service, target_service
from ....services.calibration.mapping import question_hash
from ._paging import Page, paging

router = APIRouter(prefix="/api/v1", tags=["calibration"])

HEX64 = r"^[0-9a-f]{64}$"


# --- sets -------------------------------------------------------------------------------------


@router.post("/calibration-sets/preview", response_model=CalibrationSetPreview)
async def preview_calibration_set(
    body: CalibrationSetImport, db: AsyncSession = Depends(get_db)
) -> CalibrationSetPreview:
    """Validate a mapping against the version: counts, sortedness, sample rows. Writes nothing."""
    return await set_service.preview(db, body)


@router.post("/calibration-sets/import", response_model=CalibrationSetOut, status_code=201)
async def import_calibration_set(
    body: CalibrationSetImport,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CalibrationSetOut:
    """Create a calibration set from an imported version's human-label column (FR-006.2 b)."""
    who = await resolve_who(actor, db)
    return set_service.set_out(await set_service.import_set(db, body, who))


@router.post("/calibration-sets/from-review", response_model=CalibrationSetOut, status_code=201)
async def build_calibration_set_from_review(
    body: CalibrationSetFromReview,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> CalibrationSetOut:
    """Create a calibration set from a calibration-labeling queue's OPERATOR decisions only."""
    who = await resolve_who(actor, db)
    return set_service.set_out(await set_service.from_review(db, body.queue_id, who))


@router.get("/calibration-sets", response_model=CalibrationSetList)
async def list_calibration_sets(
    version_id: str | None = Query(None, max_length=64),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> CalibrationSetList:
    rows, total = await set_service.list_sets(
        db, version_id=version_id, offset=page.offset, limit=page.limit
    )
    return CalibrationSetList(items=[set_service.set_out(r) for r in rows], total=total)


@router.get("/calibration-sets/{set_id}", response_model=CalibrationSetOut)
async def get_calibration_set(set_id: str, db: AsyncSession = Depends(get_db)) -> CalibrationSetOut:
    return set_service.set_out(await set_service.get_set(db, set_id))


# --- records ----------------------------------------------------------------------------------


@router.post("/calibration-records", response_model=JobStarted, status_code=202)
async def compute_calibration_record(
    body: CalibrationRecordStart,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> JobStarted:
    """Start a calibration job for (label run, calibration set); reads labels, calls no model."""
    who = await resolve_who(actor, db)
    return await record_service.start(db, body, who)


@router.get("/calibration-records", response_model=CalibrationRecordList)
async def list_calibration_records(
    labeler: str | None = Query(None, pattern=HEX64, description="Labeler identity hash"),
    question_hash_: str | None = Query(None, alias="question_hash", pattern=HEX64),
    page: Page = Depends(paging),
    db: AsyncSession = Depends(get_db),
) -> CalibrationRecordList:
    items, total = await record_service.list_records(
        db, labeler=labeler, question_hash=question_hash_, offset=page.offset, limit=page.limit
    )
    return CalibrationRecordList(items=items, total=total)


@router.get("/calibration-records/{record_id}", response_model=CalibrationRecordOut)
async def get_calibration_record(
    record_id: str, db: AsyncSession = Depends(get_db)
) -> CalibrationRecordOut:
    """A record with its checks (failed first) and its verdict."""
    return await record_service.get_record(db, record_id)


@router.get("/calibration-status", response_model=CalibrationStatus)
async def get_calibration_status(
    labeler: str | None = Query(None, pattern=HEX64, description="Labeler identity hash"),
    fingerprint: str | None = Query(None, pattern=HEX64, description="Labeler fingerprint"),
    db: AsyncSession = Depends(get_db),
) -> CalibrationStatus:
    """The latest record and verdict for a labeler, or ``none_recorded`` (FR-006.20)."""
    if (labeler is None) == (fingerprint is None):
        raise UnprocessableError(
            "Pass exactly one of labeler (an identity hash) or fingerprint.",
            code="VALIDATION_ERROR",
        )
    return await db.run_sync(
        lambda s: status_service.latest(identity_hash=labeler, fingerprint=fingerprint, session=s)
    )


# --- targets ----------------------------------------------------------------------------------


@router.get("/calibration-targets", response_model=TargetsOut)
async def get_calibration_targets(
    question_hash_: str | None = Query(None, alias="question_hash", pattern=HEX64),
    question: str | None = Query(None, max_length=4096),
    db: AsyncSession = Depends(get_db),
) -> TargetsOut:
    """The current target for a question (or ``null`` = default C3) and its history."""
    if (question_hash_ is None) == (question is None):
        raise UnprocessableError(
            "Pass exactly one of question_hash or question.", code="VALIDATION_ERROR"
        )
    qh = question_hash_ if question_hash_ is not None else question_hash(question or "")
    return await target_service.targets_out(db, qh)


async def _target_facts(values: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    body: TargetIn = values["body"]
    facts = await target_service.card_facts(db, body.question, body.target)
    return {"facts": facts, "expected_current_target_id": facts["expected_current_target_id"]}


def _target_summary(payload: dict[str, Any]) -> str:
    facts = payload["facts"]
    changes = len(facts["verdict_changes"])
    return (
        f"Change the gate target for {facts['question']!r} from {facts['old_display']} to "
        f"{facts['new']:.3f}; {changes} labeler verdict(s) would change."
    )


@router.put(
    "/calibration-targets",
    response_model=TargetOut,
    status_code=201,
    responses={202: {"description": "An agent's request waits for approval (gate_target_write)"}},
)
@requires_approval_when_agent("gate_target_write", enrich=_target_facts, summary=_target_summary)
async def set_calibration_target(
    body: TargetIn,
    # Set only by the stored approval (enrich above): the target the agent saw. Hidden from the
    # schema and compared only while an approval executes.
    expected_current_target_id: str | None = Query(None, max_length=64, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> Any:
    """Set the AUROC CI lower bound a labeler must reach for this question (FR-006.19)."""
    who = await resolve_who(actor, db)
    approval = await target_service.approval_ref(db, actor.approval_id)
    row = await target_service.set_target(
        db,
        body.question,
        body.target,
        who,
        approval=approval,
        expected_current_target_id=expected_current_target_id,
        check_current=actor.approval_id is not None,
    )
    return target_service.target_out(row)
