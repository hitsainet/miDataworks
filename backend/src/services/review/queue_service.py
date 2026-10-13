"""Review queues and their items (FR-006.22, FR-006.26, FR-006.40; FTDD 006 section 6.3).

Guarantees:
- a queue is created WITH all its items in one transaction; items are fixed afterwards;
- a label-review queue from an explicit row-key list validates every key against the version
  (``ROW_KEY_UNKNOWN``, 404, naming the key) (FR-006.40);
- each item carries a snapshot of the model's output at queueing time; a calibration-labeling
  queue hides it by default (T-25) and the item page returns ``model_snapshot = null`` with
  ``model_output_hidden = true`` while it does;
- item text is read with ONE DuckDB query per page (``WHERE _dw_row_key IN (…)``), as data.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.errors import AppError, NotFoundError
from ...core.ids import new_id
from ...models.label import Label
from ...models.label_run import LabelRun
from ...models.review import ReviewDecision, ReviewItem, ReviewQueue
from ...models.version import Version
from ...schemas.review import (
    CalibrationLabelingQueueCreate,
    DecisionRead,
    ExternalQueueCreate,
    LabelReviewQueueCreate,
    ReviewItemOut,
    ReviewQueueOut,
)
from ..calibration.mapping import question_hash
from ..duck import connect, files_param, quote_ident, top_level_columns
from ..label_inputs import ROW_KEY, version_files
from .sampling import stratified_sample


def run_label_set(run: LabelRun) -> list[str]:
    """The labels a run's outcomes take: its label set, else its two outcome names."""
    if run.label_set:
        return [str(x) for x in run.label_set]
    return [run.positive_label or "positive", run.negative_label or "negative"]


def label_name(outcome: str | None, labels: Sequence[str]) -> str | None:
    if outcome == "positive":
        return labels[0]
    if outcome == "negative":
        return labels[1]
    if outcome in labels:
        return outcome
    return None


def band_of(probability: float | None, run: LabelRun | None) -> str:
    if run is None or probability is None:
        return "no_score"
    if run.threshold_positive is not None and probability >= run.threshold_positive:
        return "at_or_above"
    if run.threshold_negative is not None and probability <= run.threshold_negative:
        return "at_or_below"
    return "excluded"


def version_uuid(value: str) -> str:
    """A version id as a canonical UUID, or 404 (a malformed id must not reach the database)."""
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(f"{value!r} is not a version id.", code="VERSION_NOT_FOUND") from None


async def get_version(db: AsyncSession, version_id: str) -> Version:
    version = await db.get(Version, version_uuid(version_id))
    if version is None or version.state != "completed":
        raise NotFoundError(f"No completed version {version_id}.", code="VERSION_NOT_FOUND")
    return version


async def get_run(db: AsyncSession, run_id: str) -> LabelRun:
    run = await db.get(LabelRun, run_id)
    if run is None:
        raise NotFoundError(f"No label run {run_id}.", code="LABEL_RUN_NOT_FOUND")
    return run


def version_row_keys(version: Version) -> list[str]:
    """Distinct row keys in version order."""
    files = version_files(version.splits)
    if not files:
        return []
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident(ROW_KEY, columns)
        rows = con.execute(
            f"SELECT {key} FROM read_parquet(?)", [files_param(files)]  # noqa: S608 - quoted
        ).fetchall()
    finally:
        con.close()
    return list(dict.fromkeys(str(r[0]) for r in rows))


def version_text(version: Version, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Content columns for these row keys, one query (first occurrence of each key)."""
    if not keys:
        return {}
    files = version_files(version.splits)
    if not files:
        return {}
    con = connect()
    try:
        columns = top_level_columns(con, files)
        content = [c for c, r in version.column_roles.items() if r == "content" and c in columns]
        if not content:
            return {}
        select_cols = ", ".join(quote_ident(c, columns) for c in [ROW_KEY, *content])
        key = quote_ident(ROW_KEY, columns)
        placeholders = ", ".join("?" for _ in keys)
        cursor = con.execute(
            f"SELECT {select_cols} FROM read_parquet(?) WHERE {key} IN ({placeholders})",  # noqa: S608
            [files_param(files), *keys],
        )
        names = [d[0] for d in cursor.description or []]
        out: dict[str, dict[str, Any]] = {}
        for row in cursor.fetchall():
            record = dict(zip(names, row, strict=True))
            k = str(record.pop(ROW_KEY))
            out.setdefault(k, {c: (str(v) if v is not None else None) for c, v in record.items()})
        return out
    finally:
        con.close()


async def snapshots(
    db: AsyncSession, run: LabelRun, keys: Sequence[str]
) -> dict[str, dict[str, Any]]:
    labels = run_label_set(run)
    rows = (
        await db.execute(
            select(Label).where(Label.label_run_id == run.id, Label.row_key.in_(list(keys)))
        )
    ).scalars()
    return {
        r.row_key: {
            "label_run_id": run.id,
            "outcome": r.outcome,
            "label": label_name(r.outcome, labels),
            "probability": r.probability,
            "rationale": r.rationale,
        }
        for r in rows
    }


def _unknown(keys: Sequence[str], known: set[str]) -> None:
    unknown = [k for k in keys if k not in known]
    if unknown:
        raise AppError(
            f"Row key {unknown[0]} is not in this version. Check the list you sent.",
            code="ROW_KEY_UNKNOWN",
            status_code=404,
            details={"row_keys": unknown[:20], "count": len(unknown)},
        )


async def _add_items(
    db: AsyncSession,
    queue: ReviewQueue,
    picked: Sequence[tuple[str, str | None]],
    snaps: Mapping[str, Any],
) -> None:
    db.add_all(
        ReviewItem(
            id=new_id("ri"),
            queue_id=queue.id,
            position=i,
            row_key=key,
            model_snapshot=snaps.get(key),
            stratum=stratum,
        )
        for i, (key, stratum) in enumerate(picked)
    )


async def create_label_review(
    db: AsyncSession, body: LabelReviewQueueCreate, who: Who
) -> ReviewQueue:
    run = await get_run(db, body.label_run_id)
    version = await get_version(db, str(run.input_version_id))
    labels = run_label_set(run)
    question = run.question or f"rubric:{run.rubric_id}"
    seed = (
        body.seed if body.seed is not None else random.SystemRandom().randrange(2**31)
    )  # noqa: S311
    if body.row_keys is not None:
        keys = list(dict.fromkeys(body.row_keys))
        _unknown(keys, set(version_row_keys(version)))
        snaps = await snapshots(db, run, keys)
        picked: list[tuple[str, str | None]] = [
            (k, (snaps.get(k) or {}).get("label")) for k in keys
        ]
        spec: dict[str, Any] = {"source": "row_keys", "count": len(keys)}
    else:
        rows = (
            await db.execute(
                select(Label.row_key, Label.outcome).where(Label.label_run_id == run.id)
            )
        ).all()
        sample = stratified_sample([(r.row_key, r.outcome) for r in rows], body.size, seed)
        snaps = await snapshots(db, run, [k for k, _ in sample])
        picked = [(k, s) for k, s in sample]
        spec = {"source": "sample", "size": body.size, "seed": seed, "strata": "outcome"}
    queue = ReviewQueue(
        id=new_id("rq"),
        kind="label_review",
        version_id=version.id,
        label_run_id=run.id,
        question=question,
        question_hash=question_hash(question),
        label_set=labels,
        show_model_output=True,
        sample_spec=spec,
        state="open",
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(queue)
    await db.flush()
    await _add_items(db, queue, picked, snaps)
    await db.commit()
    await db.refresh(queue)
    return queue


async def create_calibration_labeling(
    db: AsyncSession, body: CalibrationLabelingQueueCreate, who: Who
) -> ReviewQueue:
    version = await get_version(db, body.version_id)
    seed = (
        body.seed if body.seed is not None else random.SystemRandom().randrange(2**31)
    )  # noqa: S311
    keys = version_row_keys(version)
    run: LabelRun | None = None
    strata: dict[str, str] = {}
    if body.label_run_id is not None:
        run = await get_run(db, body.label_run_id)
        if str(run.input_version_id) != str(version.id):
            raise AppError(
                "The label run used for strata labeled a different version.",
                code="RUN_VERSION_MISMATCH",
                status_code=409,
            )
        rows = (
            await db.execute(
                select(Label.row_key, Label.probability).where(Label.label_run_id == run.id)
            )
        ).all()
        strata = {
            r.row_key: (
                f"bin_{min(int(r.probability * 10), 9)}"
                if r.probability is not None
                else "no_score"
            )
            for r in rows
        }
    sample = stratified_sample([(k, strata.get(k, "all")) for k in keys], body.size, seed)
    snaps = await snapshots(db, run, [k for k, _ in sample]) if run is not None else {}
    queue = ReviewQueue(
        id=new_id("rq"),
        kind="calibration_labeling",
        version_id=version.id,
        label_run_id=run.id if run is not None else None,
        question=body.question,
        question_hash=question_hash(body.question),
        label_set=list(body.label_set),
        show_model_output=body.show_model_output,
        sample_spec={
            "source": "sample",
            "size": body.size,
            "seed": seed,
            "strata": "probability_bin" if run is not None else "none",
        },
        state="open",
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(queue)
    await db.flush()
    await _add_items(db, queue, [(k, s) for k, s in sample], snaps)
    await db.commit()
    await db.refresh(queue)
    return queue


async def create_external(
    db: AsyncSession, body: ExternalQueueCreate, who: Who, origin_app: str | None
) -> ReviewQueue:
    if origin_app is None:
        raise AppError(
            "External queues are created by applications, which identify themselves with "
            "X-Dataworks-Agent (for miForge, agent:miforge).",
            code="ORIGIN_APP_REQUIRED",
            status_code=400,
        )
    queue = ReviewQueue(
        id=new_id("rq"),
        kind="external",
        version_id=None,
        label_run_id=None,
        question=body.question,
        question_hash=question_hash(body.question),
        label_set=list(body.label_set),
        show_model_output=True,
        origin_app=origin_app,
        external_ref=body.external_ref,
        state="open",
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(queue)
    await db.commit()
    await db.refresh(queue)
    return queue


async def get_queue(db: AsyncSession, queue_id: str) -> ReviewQueue:
    queue = await db.get(ReviewQueue, queue_id)
    if queue is None:
        raise NotFoundError(f"No review queue {queue_id}.", code="QUEUE_NOT_FOUND")
    return queue


async def progress(db: AsyncSession, queue_ids: Sequence[str]) -> dict[str, tuple[int, int]]:
    if not queue_ids:
        return {}
    items = dict(
        (
            await db.execute(
                select(ReviewItem.queue_id, func.count())
                .where(ReviewItem.queue_id.in_(list(queue_ids)))
                .group_by(ReviewItem.queue_id)
            )
        ).all()
    )
    decided = dict(
        (
            await db.execute(
                select(ReviewDecision.queue_id, func.count(func.distinct(ReviewDecision.item_id)))
                .where(ReviewDecision.queue_id.in_(list(queue_ids)))
                .group_by(ReviewDecision.queue_id)
            )
        ).all()
    )
    return {q: (int(items.get(q, 0)), int(decided.get(q, 0))) for q in queue_ids}


def queue_out(queue: ReviewQueue, counts: tuple[int, int]) -> ReviewQueueOut:
    return ReviewQueueOut(
        id=queue.id,
        kind=queue.kind,
        version_id=str(queue.version_id) if queue.version_id else None,
        label_run_id=queue.label_run_id,
        question=queue.question,
        question_hash=queue.question_hash,
        label_set=list(queue.label_set),
        show_model_output=queue.show_model_output,
        sample_spec=queue.sample_spec,
        origin_app=queue.origin_app,
        external_ref=queue.external_ref,
        state=queue.state,
        created_by=queue.created_by,
        created_by_origin=queue.created_by_origin,
        created_at=queue.created_at,
        items=counts[0],
        decided=counts[1],
    )


async def list_queues(
    db: AsyncSession,
    *,
    kind: str | None,
    state: str | None,
    version_id: str | None,
    offset: int,
    limit: int,
) -> tuple[list[ReviewQueueOut], int]:
    query = select(ReviewQueue)
    if kind:
        query = query.where(ReviewQueue.kind == kind)
    if state:
        query = query.where(ReviewQueue.state == state)
    if version_id:
        query = query.where(ReviewQueue.version_id == version_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    rows = list(
        (
            await db.execute(
                query.order_by(ReviewQueue.created_at.desc(), ReviewQueue.id.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars()
    )
    counts = await progress(db, [q.id for q in rows])
    return [queue_out(q, counts[q.id]) for q in rows], int(total)


def decision_read(d: ReviewDecision) -> DecisionRead:
    return DecisionRead(
        id=d.id,
        item_id=d.item_id,
        queue_id=d.queue_id,
        row_key=d.row_key,
        decision=d.decision,
        override_label=d.override_label,
        reason=d.reason,
        decided_by=d.decided_by,
        decided_by_origin=d.decided_by_origin,
        model_output_visible=d.model_output_visible,
        version_id=str(d.version_id) if d.version_id else None,
        created_at=d.created_at,
    )


async def items_page(
    db: AsyncSession,
    queue_id: str,
    *,
    decided: bool | None,
    stratum: str | None,
    offset: int,
    limit: int,
) -> tuple[list[ReviewItemOut], int]:
    queue = await get_queue(db, queue_id)
    query = select(ReviewItem).where(ReviewItem.queue_id == queue.id)
    if stratum is not None:
        query = query.where(ReviewItem.stratum == stratum)
    decided_ids = select(ReviewDecision.item_id).where(ReviewDecision.queue_id == queue.id)
    if decided is True:
        query = query.where(ReviewItem.id.in_(decided_ids))
    elif decided is False:
        query = query.where(ReviewItem.id.not_in(decided_ids))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    items = list(
        (
            await db.execute(query.order_by(ReviewItem.position).offset(offset).limit(limit))
        ).scalars()
    )
    latest: dict[str, ReviewDecision] = {}
    if items:
        for d in (
            await db.execute(
                select(ReviewDecision)
                .where(ReviewDecision.item_id.in_([i.id for i in items]))
                .order_by(ReviewDecision.created_at, ReviewDecision.id)
            )
        ).scalars():
            latest[d.item_id] = d
    text: dict[str, dict[str, Any]] = {}
    if queue.version_id is not None:
        version = await db.get(Version, queue.version_id)
        if version is not None:
            text = version_text(version, [i.row_key for i in items if i.row_key])
    hidden = not queue.show_model_output
    return [
        ReviewItemOut(
            id=i.id,
            queue_id=i.queue_id,
            position=i.position,
            row_key=i.row_key,
            external_id=i.external_id,
            payload=i.payload,
            model_snapshot=None if hidden else i.model_snapshot,
            model_output_hidden=hidden,
            stratum=i.stratum,
            text=text.get(i.row_key) if i.row_key else None,
            latest_decision=decision_read(latest[i.id]) if i.id in latest else None,
        )
        for i in items
    ], int(total)
