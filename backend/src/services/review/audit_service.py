"""The audit before export (FR-006.27 – FR-006.29; T-26, P-02).

Guarantees:
- a draw takes 50 to 100 rows (``AUDIT_SIZE_OUT_OF_RANGE`` otherwise), default 100, stratified by
  effective label x probability band unless the caller names stratum columns (007 FR-007.13);
  seed, strata and size are recorded; a new draw SUPERSEDES an in-progress audit;
- an audit is complete when every sampled row has an OPERATOR-origin decision made in that
  version for the audit's question. Earlier operator decisions on the same question count; agent
  decisions never count (FR-006.24). Completion is evaluated inside the decision's transaction;
- the result counts accept, override and flag (the latest operator decision per row) and the
  agreement share, with the sample size. Completion alone satisfies R-03.40 (P-02).

:func:`status` is synchronous: 008's check C-5 calls it from a worker
(``feature_seams.audit_status``); the route runs it through ``run_sync``.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.agent_origin import Who
from ...core.canonical_json import canonical_json
from ...core.clock import utc_now
from ...core.errors import AppError
from ...core.ids import new_id
from ...models.label import Label
from ...models.label_run import LabelRun
from ...models.review import Audit, ReviewDecision, ReviewItem, ReviewQueue
from ...models.version import Version
from ...schemas.review import AuditDraw, AuditStatusOut
from ..calibration.constants import AUDIT_DEFAULT, AUDIT_MAX, AUDIT_MIN
from ..calibration.mapping import question_hash
from .sampling import stratified_sample


def size_refusal(size: int) -> AppError:
    return AppError(
        f"An audit takes {AUDIT_MIN} to {AUDIT_MAX} rows (R-03.40); {size} is outside that. "
        f"Choose a size in the range; {AUDIT_DEFAULT} is the default.",
        code="AUDIT_SIZE_OUT_OF_RANGE",
        status_code=422,
        details={"size": size, "min": AUDIT_MIN, "max": AUDIT_MAX},
    )


def summarise(latest: dict[str, str], sample: Sequence[str]) -> dict[str, Any]:
    """Counts of the latest operator decision per sampled row, and the agreement share."""
    decided = [latest[k] for k in sample if k in latest]
    counts = {kind: decided.count(kind) for kind in ("accept", "override", "flag")}
    size = len(sample)
    return {
        **counts,
        "decided": len(decided),
        "size": size,
        "agreement_share": counts["accept"] / size if size else None,
    }


def _latest_operator(
    rows: Sequence[tuple[str | None, str]],
) -> dict[str, str]:
    latest: dict[str, str] = {}
    for row_key, decision in rows:  # ordered oldest first
        if row_key is not None:
            latest[row_key] = decision
    return latest


def _decisions_query(audit: Audit, qh: str, keys: Sequence[str]) -> Any:
    return (
        select(ReviewDecision.row_key, ReviewDecision.decision)
        .where(
            ReviewDecision.version_id == audit.version_id,
            ReviewDecision.question_hash == qh,
            ReviewDecision.decided_by_origin == "operator",
            ReviewDecision.row_key.in_(list(keys)),
        )
        .order_by(ReviewDecision.created_at, ReviewDecision.id)
    )


async def evaluate_for_queue(db: AsyncSession, queue_id: str) -> None:
    """Re-evaluate the in-progress audit that owns this queue (inside the decision transaction)."""
    audit = (
        await db.execute(
            select(Audit).where(Audit.queue_id == queue_id, Audit.state == "in_progress")
        )
    ).scalar_one_or_none()
    if audit is None:
        return
    queue = await db.get(ReviewQueue, queue_id)
    assert queue is not None
    keys = [
        k
        for k in (
            await db.execute(select(ReviewItem.row_key).where(ReviewItem.queue_id == queue_id))
        ).scalars()
        if k is not None
    ]
    rows = (await db.execute(_decisions_query(audit, queue.question_hash, keys))).all()
    result = summarise(_latest_operator([(r[0], r[1]) for r in rows]), keys)
    if result["decided"] == len(keys):
        audit.state = "complete"
        audit.result = result
        audit.completed_at = utc_now()
        queue.state = "closed"
    await db.flush()


def status(version_id: str, *, session: Session) -> AuditStatusOut:
    from .queue_service import version_uuid

    version_id = version_uuid(version_id)
    audit = session.execute(
        select(Audit)
        .where(Audit.version_id == version_id, Audit.state != "superseded")
        .order_by(Audit.created_at.desc(), Audit.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if audit is None:
        return AuditStatusOut(
            version_id=version_id,
            state="none",
            audit_id=None,
            queue_id=None,
            size=None,
            decided=0,
            strata=None,
            result=None,
        )
    queue = session.get(ReviewQueue, audit.queue_id)
    assert queue is not None
    keys = [
        k
        for k in session.execute(
            select(ReviewItem.row_key).where(ReviewItem.queue_id == audit.queue_id)
        ).scalars()
        if k is not None
    ]
    rows = session.execute(_decisions_query(audit, queue.question_hash, keys)).all()
    live = summarise(_latest_operator([(r[0], r[1]) for r in rows]), keys)
    return AuditStatusOut(
        version_id=version_id,
        state="complete" if audit.state == "complete" else "in_progress",
        audit_id=audit.id,
        queue_id=audit.queue_id,
        size=audit.size,
        decided=int(live["decided"]),
        strata=audit.strata,
        result=audit.result if audit.state == "complete" else None,
    )


def _strata(
    session: Session,
    version: Version,
    keys: Sequence[str],
    run: LabelRun | None,
    columns: Sequence[str] | None,
    qh: str,
) -> dict[str, str]:
    if columns:
        from ..calibration.mapping import read_rows

        _, raw = read_rows(version.splits, list(columns))
        out: dict[str, str] = {}
        for record in raw:
            key = str(record["_dw_row_key"])
            out.setdefault(key, canonical_json({c: record.get(c) for c in columns}).decode("utf-8"))
        return out
    if run is None:
        return dict.fromkeys(keys, "unlabeled")
    from .effective_label import resolve_with
    from .queue_service import band_of

    probabilities = dict(
        session.execute(
            select(Label.row_key, Label.probability).where(Label.label_run_id == run.id)
        ).all()
    )
    effective = resolve_with(session, str(version.id), qh, run.id, keys)
    return {
        k: f"{effective[k].label or effective[k].state}|{band_of(probabilities.get(k), run)}"
        for k in keys
    }


async def draw(db: AsyncSession, version_id: str, body: AuditDraw, who: Who) -> Audit:
    if not AUDIT_MIN <= body.size <= AUDIT_MAX:
        raise size_refusal(body.size)
    from .queue_service import get_version

    version = await get_version(db, version_id)
    run: LabelRun | None = None
    run_id = body.label_run_id
    if run_id is None:
        bound = [str(b["id"]) for b in version.bindings if b.get("kind") == "label_run"]
        run_id = bound[0] if bound else None
    if run_id is not None:
        run = await db.get(LabelRun, run_id)
        if run is None:
            raise AppError(f"No label run {run_id}.", code="LABEL_RUN_NOT_FOUND", status_code=404)
    question = body.question or (run.question if run is not None else None)
    if not question:
        raise AppError(
            "Name the question this audit checks, or pass a label run of the version.",
            code="AUDIT_NEEDS_QUESTION",
            status_code=422,
        )
    from .queue_service import run_label_set, snapshots, version_row_keys

    qh = question_hash(question)
    keys = version_row_keys(version)
    seed = (
        body.seed if body.seed is not None else random.SystemRandom().randrange(2**31)
    )  # noqa: S311
    strata_map = await db.run_sync(
        lambda s: _strata(s, version, keys, run, body.strata_columns, qh)
    )
    sample = stratified_sample([(k, strata_map.get(k, "unlabeled")) for k in keys], body.size, seed)
    snaps = await snapshots(db, run, [k for k, _ in sample]) if run is not None else {}
    labels = run_label_set(run) if run is not None else ["positive", "negative"]
    # Supersede the in-progress audit (FR-006.27) before the partial unique index sees a second.
    previous = list(
        (
            await db.execute(
                select(Audit).where(Audit.version_id == version.id, Audit.state == "in_progress")
            )
        ).scalars()
    )
    for old in previous:
        old.state = "superseded"
        await db.execute(
            update(ReviewQueue).where(ReviewQueue.id == old.queue_id).values(state="closed")
        )
    await db.flush()
    strata_spec = {
        "kind": "columns" if body.strata_columns else "effective_label_x_probability_band",
        "columns": list(body.strata_columns or []),
        "label_run_id": run.id if run is not None else None,
    }
    queue = ReviewQueue(
        id=new_id("rq"),
        kind="audit",
        version_id=version.id,
        label_run_id=run.id if run is not None else None,
        question=question,
        question_hash=qh,
        label_set=labels,
        show_model_output=True,
        sample_spec={"source": "audit", "size": body.size, "seed": seed, "strata": strata_spec},
        state="open",
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(queue)
    await db.flush()
    db.add_all(
        ReviewItem(
            id=new_id("ri"),
            queue_id=queue.id,
            position=i,
            row_key=k,
            model_snapshot=snaps.get(k),
            stratum=s,
        )
        for i, (k, s) in enumerate(sample)
    )
    audit = Audit(
        id=new_id("au"),
        version_id=version.id,
        queue_id=queue.id,
        question_hash=qh,
        size=body.size,
        strata=strata_spec,
        seed=seed,
        state="in_progress",
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(audit)
    await db.flush()
    # Rows the operator already decided for this question count at once (FR-006.28).
    await evaluate_for_queue(db, queue.id)
    await db.commit()
    await db.refresh(audit)
    return audit
