"""Archive and unarchive (operator decision 2026-10-20; migration ``0030``).

Archive hides a dataset, label run, generation run, approval, rule set (feature 016) or code rule
(feature 018) from the
default lists and the UI's pickers. It deletes nothing and writes only the four archive columns, so every detail read, every
version, label, publish and provenance record that names the item still works, and unarchive
restores it exactly. Each change appends a :class:`~src.models.archive.ArchiveEvent`.

Refused, with the reason named:

- a dataset while a job is working on it (``dataset_busy``): a build into it, a build reading one
  of its versions, a label or generation run over one of its versions, a publish, check, export or
  diversity job of one of its versions;
- a label or generation run that has not finished (``run_not_finished``);
- an approval that is not ``rejected`` or ``expired`` (``approval_not_archivable``): a pending
  one still needs the operator, and an executed or failed one is the record of what an agent did.

Archiving an archived item, or unarchiving a shown one, changes nothing and records nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import String, cast, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.agent_origin import Who
from ..core.clock import utc_now
from ..core.errors import ConflictError, UnprocessableError
from ..core.ids import new_id
from ..models.archive import ARCHIVE_KINDS, Archivable, ArchiveEvent
from ..models.generation import GenerationRun, GenerationRunJob
from ..models.job import Job
from ..models.label_run import LabelRun, LabelRunJob
from ..models.publish import Export, Publish, PublishBuild, PublishCheckRun
from ..models.version import Version, VersionBuild

logger = logging.getLogger(__name__)

LIVE_JOB = ("queued", "running", "cancelling")
#: Run states that may still change; such a run is not archived.
LABEL_RUN_UNFINISHED = frozenset({"awaiting_approval", "queued", "running"})
GENERATION_RUN_UNFINISHED = frozenset({"queued", "running"})
APPROVAL_ARCHIVABLE = frozenset({"rejected", "expired"})
REASON_MAX = 500


async def _load(db: AsyncSession, kind: str, entity_id: str) -> Any:
    if kind == "dataset":
        from . import dataset_service

        return await dataset_service.get_dataset_row(db, entity_id)
    if kind == "label_run":
        from . import label_run_service

        return await label_run_service.get_run(db, entity_id)
    if kind == "generation_run":
        from .generation import run_service

        return await run_service.get_run(db, entity_id)
    if kind == "approval":
        from .approval_service import ApprovalService

        return await ApprovalService.get(db, entity_id)
    if kind == "rule_set":
        from ..models.rule_set import RuleSet
        from .rules.errors import RuleSetError

        row = await db.get(RuleSet, entity_id)
        if row is None:
            raise RuleSetError(
                404,
                "rule_set_not_found",
                f"No stored rule set {entity_id}.",
                {"name": entity_id},
                next_tool="dataworks_list_rule_sets",
                why="find a stored rule set",
            )
        return row
    if kind == "code_rule":
        from ..models.code_rule import CodeRule
        from .code_rules.errors import not_found

        code_rule = await db.get(CodeRule, entity_id)
        if code_rule is None:
            raise not_found(entity_id)
        return code_rule
    raise UnprocessableError(f"{kind!r} cannot be archived.", code="not_archivable")


async def dataset_jobs(db: AsyncSession, dataset_id: str) -> list[str]:
    """Live jobs working on this dataset or one of its versions, oldest first."""
    versions = select(Version.id).where(Version.dataset_id == dataset_id)
    version_ids = [str(v) for v in (await db.execute(versions)).scalars()]
    reads_a_version = [
        VersionBuild.request["inputs"].contains(cast([{"kind": "version", "version_id": v}], JSONB))
        for v in version_ids
    ]
    owned = or_(
        Job.id.in_(select(VersionBuild.job_id).where(VersionBuild.dataset_id == dataset_id)),
        Job.id.in_(
            select(VersionBuild.job_id).where(
                or_(VersionBuild.verify_version_id.in_(versions), *reads_a_version)
            )
        ),
        Job.id.in_(
            select(LabelRunJob.job_id)
            .join(LabelRun, LabelRun.id == LabelRunJob.label_run_id)
            .where(LabelRun.input_version_id.in_(versions))
        ),
        Job.id.in_(
            select(GenerationRunJob.job_id)
            .join(GenerationRun, GenerationRun.id == GenerationRunJob.run_id)
            .where(GenerationRun.input_version_id.in_(versions))
        ),
        Job.id.in_(select(PublishBuild.job_id).where(PublishBuild.version_id.in_(versions))),
        Job.id.in_(select(PublishCheckRun.job_id).where(PublishCheckRun.version_id.in_(versions))),
        Job.id.in_(select(Publish.job_id).where(Publish.version_id.in_(versions))),
        Job.id.in_(select(Export.job_id).where(Export.version_id.in_(versions))),
        Job.params["version_id"].astext.in_(
            select(cast(Version.id, String)).where(Version.dataset_id == dataset_id)
        ),
    )
    rows = await db.execute(
        select(Job.id).where(Job.status.in_(LIVE_JOB), owned).order_by(Job.created_at, Job.id)
    )
    return [str(r) for r in rows.scalars()]


async def _refuse(db: AsyncSession, kind: str, row: Any) -> None:
    if kind == "dataset":
        jobs = await dataset_jobs(db, row.id)
        if jobs:
            raise ConflictError(
                f"Job {jobs[0]} is working on {row.name}. Wait for it to finish or cancel it, "
                "then archive the dataset.",
                code="dataset_busy",
                details={"jobs": jobs},
            )
    elif kind == "label_run" and row.state in LABEL_RUN_UNFINISHED:
        raise ConflictError(
            f"Label run {row.id} is {row.state.replace('_', ' ')}. Cancel it or let it finish, "
            "then archive it.",
            code="run_not_finished",
            details={"state": row.state},
        )
    elif kind == "generation_run" and row.state in GENERATION_RUN_UNFINISHED:
        raise ConflictError(
            f"Generation run {row.id} is {row.state}. Cancel it or let it finish, then archive it.",
            code="run_not_finished",
            details={"state": row.state},
        )
    elif kind == "approval" and row.status not in APPROVAL_ARCHIVABLE:
        raise ConflictError(
            f"Approval {row.id} is {row.status}; only a rejected or expired approval can be "
            "archived.",
            code="approval_not_archivable",
            details={"status": row.status},
        )


def _reason(reason: str | None) -> str | None:
    text = (reason or "").strip()
    return text[:REASON_MAX] or None


async def set_archived(
    db: AsyncSession,
    kind: str,
    entity_id: str,
    who: Who,
    *,
    archived: bool,
    reason: str | None = None,
) -> Any:
    """Archive (``archived=True``) or unarchive one item; returns the row."""
    if kind not in ARCHIVE_KINDS:
        raise UnprocessableError(f"{kind!r} cannot be archived.", code="not_archivable")
    row = await _load(db, kind, entity_id)
    ref = str(row.id)
    assert isinstance(row, Archivable)
    if (row.archived_at is not None) == archived:
        return row
    if archived:
        await _refuse(db, kind, row)
        row.archived_at = utc_now()
        row.archived_by = who.who
        row.archived_by_origin = who.origin
        row.archive_reason = _reason(reason)
    else:
        row.archived_at = None
        row.archived_by = None
        row.archived_by_origin = None
        row.archive_reason = None
    db.add(
        ArchiveEvent(
            id=new_id("arc"),
            entity_kind=kind,
            entity_id=ref,
            action="archive" if archived else "unarchive",
            reason=_reason(reason),
            actor=who.who,
            actor_origin=who.origin,
        )
    )
    await db.commit()
    await db.refresh(row)
    logger.info(
        "%s %s %s by %s (%s)",
        ARCHIVE_KINDS[kind],
        ref,
        "archived" if archived else "unarchived",
        who.who,
        who.origin,
    )
    return row


async def events(db: AsyncSession, kind: str, entity_id: str) -> list[ArchiveEvent]:
    rows = await db.execute(
        select(ArchiveEvent)
        .where(ArchiveEvent.entity_kind == kind, ArchiveEvent.entity_id == entity_id)
        .order_by(ArchiveEvent.created_at, ArchiveEvent.id)
    )
    return list(rows.scalars())


__all__ = ["dataset_jobs", "events", "set_archived"]
