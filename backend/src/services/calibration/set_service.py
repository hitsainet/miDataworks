"""Calibration sets: preview, import from a version, build from a review queue (FR-006.1 –
FR-006.5, FR-006.24, FR-006.26, FR-006.37; FTDD 006 section 6.3).

Guarantees:
- preview writes nothing; import and from-review create the set COMPLETE in one transaction;
- from-review uses OPERATOR-origin decisions only (FR-006.24, P-10): the query carries
  ``decided_by_origin = 'operator'`` and an agent accept never becomes a human label;
- the licence class comes from 008's licence table at creation (P-14), worst class across the
  version's sources, and a version with no recorded source is ``private_only`` — never "permits";
- provenance records the source revision or queue, the deciders and how many decisions were made
  with model output visible (FR-006.5).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.agent_origin import Who
from ...core.canonical_json import canonical_sha256
from ...core.config import get_settings
from ...core.errors import AppError, ConflictError, NotFoundError
from ...core.ids import new_id
from ...models.calibration import CalibrationSet, CalibrationSetLabel
from ...models.review import ReviewDecision, ReviewItem, ReviewQueue
from ...models.version import Version
from ...schemas.calibration import (
    CalibrationMapping,
    CalibrationSetImport,
    CalibrationSetOut,
    CalibrationSetPreview,
)
from ..publishing.licence_table import LicenceClass
from .constants import MAPPING_SCHEMA
from .mapping import MappingResult, evaluate, question_hash

_WORST = [LicenceClass.FORBIDS, LicenceClass.PRIVATE_ONLY, LicenceClass.PERMITS]


async def get_version(db: AsyncSession, version_id: str) -> Version:
    from ..review.queue_service import version_uuid

    version = await db.get(Version, version_uuid(version_id))
    if version is None or version.state != "completed":
        raise NotFoundError(
            f"No completed version {version_id}. Import or build one first.",
            code="VERSION_NOT_FOUND",
        )
    return version


def licence_of(session: Session, version: Version) -> tuple[str, list[dict[str, Any]]]:
    """The version's licence class from 008's table: the WORST class across its sources."""
    from ..publishing.check_inputs import source_facts

    try:
        sources = json.loads(version.manifest).get("sources")
    except (TypeError, ValueError):
        sources = None
    if not sources:
        return LicenceClass.PRIVATE_ONLY.value, []
    facts = source_facts(session, version)
    classes = [f.current.licence_class for f in facts]
    worst = next(c for c in _WORST if c in classes) if classes else LicenceClass.PRIVATE_ONLY
    detail = [
        {
            "source_id": f.source.id,
            "licence_class": f.current.licence_class.value,
            "decided_by": f.current.decided_by,
            "table_version": f.current.table_version,
        }
        for f in facts
    ]
    return worst.value, detail


def _sample(result: MappingResult, n: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "row_key": r.row_key,
            "position": r.position,
            "human_label": r.human_label,
            "group_key": r.group_key,
            "is_reference": r.is_reference,
            "ratings": r.ratings,
        }
        for r in result.rows[:n]
    ]


async def _evaluate(db: AsyncSession, body: CalibrationSetImport) -> tuple[Version, MappingResult]:
    version = await get_version(db, body.version_id)
    result = evaluate(version.splits, version.column_roles, body.mapping, body.label_set)
    limit = get_settings().calibration_max_per_rater_rows
    if body.mapping.ratings is not None and result.counts["rows_with_ratings"] > limit:
        raise AppError(
            f"{result.counts['rows_with_ratings']} rows carry per-rater ratings; sets above {limit} "
            "are refused until the ceiling's cost is measured at that size. Use a subset version.",
            code="PER_RATER_TOO_LARGE",
            status_code=422,
            details={"rows": result.counts["rows_with_ratings"], "limit": limit},
        )
    return version, result


async def preview(db: AsyncSession, body: CalibrationSetImport) -> CalibrationSetPreview:
    _, result = await _evaluate(db, body)
    return CalibrationSetPreview(
        counts=result.counts,
        ratings_sorted=result.ratings_sorted,
        warnings=result.warnings,
        sample=_sample(result),
        mapping_hash=result.mapping_hash,
    )


async def _refuse_duplicate(db: AsyncSession, version_id: str, qh: str, mh: str) -> None:
    existing = (
        await db.execute(
            select(CalibrationSet.id).where(
                CalibrationSet.version_id == version_id,
                CalibrationSet.question_hash == qh,
                CalibrationSet.mapping_hash == mh,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(
            f"Calibration set {existing} already has this version, question and mapping. Use it, "
            "or change the mapping to make a new set.",
            code="CALIBRATION_SET_EXISTS",
            details={"calibration_set_id": existing},
        )


async def import_set(db: AsyncSession, body: CalibrationSetImport, who: Who) -> CalibrationSet:
    version, result = await _evaluate(db, body)
    qh = question_hash(body.question)
    await _refuse_duplicate(db, version.id, qh, result.mapping_hash)
    licence, licence_detail = await db.run_sync(lambda s: licence_of(s, version))
    manifest = json.loads(version.manifest) if version.manifest else {}
    row = CalibrationSet(
        id=new_id("cs"),
        version_id=version.id,
        question=body.question,
        question_hash=qh,
        label_set=list(body.label_set),
        source_kind="imported",
        source_queue_id=None,
        mapping=body.mapping.document(),
        mapping_hash=result.mapping_hash,
        ratings_sorted=result.ratings_sorted,
        licence_class=licence,
        counts=result.counts,
        provenance={
            "kind": "imported",
            "version_id": version.id,
            "sources": [
                {
                    k: s.get(k)
                    for k in ("source_id", "repo_id", "revision", "resolved_commit")
                    if k in s
                }
                for s in (manifest.get("sources") or [])
                if isinstance(s, dict)
            ],
            "licence": licence_detail,
            "warnings": result.warnings,
        },
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    db.add_all(
        CalibrationSetLabel(
            calibration_set_id=row.id,
            row_key=r.row_key,
            position=r.position,
            human_label=r.human_label,
            group_key=r.group_key,
            strata_key=r.strata_key,
            is_reference=r.is_reference,
            ratings=r.ratings,
        )
        for r in result.rows
    )
    await db.commit()
    await db.refresh(row)
    return row


async def from_review(db: AsyncSession, queue_id: str, who: Who) -> CalibrationSet:
    queue = await db.get(ReviewQueue, queue_id)
    if queue is None:
        raise NotFoundError(f"No review queue {queue_id}.", code="QUEUE_NOT_FOUND")
    if queue.kind != "calibration_labeling" or queue.version_id is None:
        raise AppError(
            "Only a calibration-labeling queue can become a calibration set.",
            code="QUEUE_KIND_INVALID",
            status_code=422,
            details={"kind": queue.kind},
        )
    # The latest OPERATOR decision per item. Agent decisions never become human labels (P-10).
    latest = (
        select(
            ReviewDecision.item_id,
            ReviewDecision.id,
            ReviewDecision.decision,
            ReviewDecision.override_label,
            ReviewDecision.decided_by,
            ReviewDecision.model_output_visible,
            func.row_number()
            .over(
                partition_by=ReviewDecision.item_id,
                order_by=(ReviewDecision.created_at.desc(), ReviewDecision.id.desc()),
            )
            .label("rn"),
        )
        .where(ReviewDecision.queue_id == queue.id, ReviewDecision.decided_by_origin == "operator")
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                ReviewItem,
                latest.c.id,
                latest.c.decision,
                latest.c.override_label,
                latest.c.decided_by,
                latest.c.model_output_visible,
            )
            .join(latest, latest.c.item_id == ReviewItem.id)
            .where(latest.c.rn == 1)
            .order_by(ReviewItem.position)
        )
    ).all()
    labels: list[CalibrationSetLabel] = []
    deciders: set[str] = set()
    visible = 0
    for item, decision_id, decision, override_label, decided_by, model_visible in rows:
        if decision == "override":
            label = override_label
        elif decision == "accept":
            snapshot = item.model_snapshot or {}
            label = snapshot.get("label")
        else:
            label = None  # a flag is not a label
        if label is None or item.row_key is None:
            continue
        deciders.add(decided_by)
        visible += 1 if model_visible else 0
        labels.append(
            CalibrationSetLabel(
                calibration_set_id="",
                row_key=item.row_key,
                position=len(labels),
                human_label=label,
                decision_id=decision_id,
            )
        )
    label_set = list(queue.label_set)
    positives = sum(1 for x in labels if x.human_label == label_set[0])
    if positives == 0 or positives == len(labels):
        missing = "positives" if positives == 0 else "negatives"
        raise AppError(
            f"The queue's operator decisions give 0 {missing}; AUROC needs both classes. Label "
            "more rows in the queue first.",
            code="MAPPING_INVALID",
            status_code=422,
            details={"labeled": len(labels), "positives": positives},
        )
    mapping = {"schema": MAPPING_SCHEMA, "source": "review", "queue_id": queue.id}
    mh = canonical_sha256({**mapping, "decisions": sorted(x.decision_id or "" for x in labels)})
    await _refuse_duplicate(db, queue.version_id, queue.question_hash, mh)
    version = await get_version(db, queue.version_id)
    licence, licence_detail = await db.run_sync(lambda s: licence_of(s, version))
    row = CalibrationSet(
        id=new_id("cs"),
        version_id=queue.version_id,
        question=queue.question,
        question_hash=queue.question_hash,
        label_set=label_set,
        source_kind="review",
        source_queue_id=queue.id,
        mapping=mapping,
        mapping_hash=mh,
        ratings_sorted=None,
        licence_class=licence,
        counts={
            "rows": len(labels),
            "labeled": len(labels),
            "positives": positives,
            "negatives": len(labels) - positives,
            "excluded": 0,
            "references": 0,
            "groups": 0,
            "rows_with_ratings": 0,
        },
        provenance={
            "kind": "review",
            "queue_id": queue.id,
            "deciders": sorted(deciders),
            "decisions_with_model_output_visible": visible,
            "decisions_with_model_output_hidden": len(labels) - visible,
            "licence": licence_detail,
        },
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    for x in labels:
        x.calibration_set_id = row.id
    db.add_all(labels)
    await db.commit()
    await db.refresh(row)
    return row


async def get_set(db: AsyncSession, set_id: str) -> CalibrationSet:
    row = await db.get(CalibrationSet, set_id)
    if row is None:
        raise NotFoundError(f"No calibration set {set_id}.", code="CALIBRATION_SET_NOT_FOUND")
    return row


async def list_sets(
    db: AsyncSession, *, version_id: str | None, offset: int, limit: int
) -> tuple[list[CalibrationSet], int]:
    query = select(CalibrationSet)
    if version_id:
        query = query.where(CalibrationSet.version_id == version_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = (
        await db.execute(
            query.order_by(CalibrationSet.created_at.desc()).offset(offset).limit(limit)
        )
    ).scalars()
    return list(rows), int(total)


def set_out(row: CalibrationSet) -> CalibrationSetOut:
    return CalibrationSetOut(
        id=row.id,
        version_id=str(row.version_id),
        question=row.question,
        question_hash=row.question_hash,
        label_set=list(row.label_set),
        source_kind=row.source_kind,
        source_queue_id=row.source_queue_id,
        mapping=row.mapping,
        mapping_hash=row.mapping_hash,
        ratings_sorted=row.ratings_sorted,
        licence_class=row.licence_class,
        counts=row.counts,
        provenance=row.provenance,
        created_by=row.created_by,
        created_by_origin=row.created_by_origin,
        created_at=row.created_at,
    )


__all__ = [
    "CalibrationMapping",
    "from_review",
    "get_set",
    "import_set",
    "licence_of",
    "list_sets",
    "preview",
    "set_out",
]
