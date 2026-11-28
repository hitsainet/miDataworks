"""Second-opinion disagreements go to review (017 FR-017.44; FTID §3.8, I17; FTDD question D4 (a)).

``POST /api/v1/label-runs/handoffs/{handoff_id}/review`` opens ``label_review`` queues over EXACTLY
the rows where a run of the hand-off disagrees with the column it was compared with (its
``target_column``, else the column named): a row the run labelled positive whose column value is
not the positive value, or the reverse. Rows the run skipped, excluded or failed are not
disagreements, and neither are rows with no value in the column.

- At most ``QUEUE_ROWS`` (5,000, the label-review queue's own limit) keys per queue; as many
  queues as needed per run, refused before anything is written when the hand-off would need more
  than ``REVIEW_MAX_QUEUES_PER_HANDOFF`` (``cap_exceeded``, naming the setting).
- Keys are ordered round-robin over the strata (``by``, default genre, generator, kind and form
  where the version has them), so every stratum with a disagreement appears in the FIRST queue.
- One transaction for every queue. Queues carry the hand-off id and the behaviour key.
- An agent may open the queues; only the operator decides rows (P-10): an operator override
  reaches the published label through 006's effective-label resolver, an agent decision does not.

Nothing here reads or returns row text: keys, counts and strata only.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.config import get_settings
from ...core.errors import AppError, NotFoundError
from ...models.label import Label
from ...models.label_run import LabelRun
from ...schemas.review import LabelReviewQueueCreate
from .. import label_agreement, label_inputs
from ..duck import connect, files_param, quote_ident, top_level_columns
from ..labeling.stratified import stratum_of
from . import queue_service

#: The most row keys one label-review queue holds (``schemas/review.py``).
QUEUE_ROWS = 5000
NEXT_SETTING = {
    "tool": "dataworks_get_environment",
    "args": {},
    "why": "the operator raises REVIEW_MAX_QUEUES_PER_HANDOFF in Settings, or review the "
    "largest strata first",
}


def interleave(keys_by_stratum: dict[str, list[str]]) -> list[str]:
    """Round-robin over the strata in name order: the first ``len(strata)`` keys hold one key of
    every stratum, so any queue holding at least that many starts with all of them."""
    queues = [list(keys_by_stratum[s]) for s in sorted(keys_by_stratum)]
    out: list[str] = []
    for i in range(max((len(q) for q in queues), default=0)):
        out.extend(q[i] for q in queues if i < len(q))
    return out


def _disagreeing(
    files: list[Path],
    column: str,
    positive: str,
    labels: dict[str, str],
    strata: Sequence[str],
) -> dict[str, list[str]]:
    """Row keys whose run outcome disagrees with the column, per stratum (key order)."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident(label_inputs.ROW_KEY, columns)
        col = quote_ident(column, columns)
        picked = "".join(
            f", CAST({quote_ident(c, columns)} AS VARCHAR) AS s{i}" for i, c in enumerate(strata)
        )
        sql = (
            f"SELECT {key} AS row_key, CAST({col} AS VARCHAR) AS v{picked} FROM read_parquet(?) "  # noqa: S608 - identifiers quoted
            f'WHERE {key} IS NOT NULL QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY "_dw_occurrence") = 1 '
            "ORDER BY row_key"
        )
        data = con.execute(sql, [files_param(files)]).fetchall()
    finally:
        con.close()
    out: dict[str, list[str]] = {}
    for row_key, value, *values in data:
        outcome = labels.get(str(row_key))
        if value is None or outcome not in ("positive", "negative"):
            continue
        if (outcome == "positive") == (str(value) == positive):
            continue
        cell = stratum_of(dict(zip(strata, values, strict=True)), strata) if strata else "all"
        out.setdefault(cell, []).append(str(row_key))
    return out


async def _run_disagreements(
    db: AsyncSession,
    run: LabelRun,
    column: str | None,
    positive_value: str | None,
    by: list[str] | None,
) -> tuple[str, str, list[str], dict[str, list[str]]]:
    from ..label_run_service import _version_files

    files = list(await _version_files(db, str(run.input_version_id)))
    target = column or run.target_column
    if not target:
        raise AppError(
            f"Label run {run.id} names no target column; give the column to compare with.",
            code="target_column_invalid",
            status_code=422,
            details={
                "label_run_id": run.id,
                "next": {
                    "tool": "dataworks_get_label_run_agreement",
                    "args": {"run_id": run.id},
                    "why": "see the columns the run can be compared with",
                },
            },
        )
    values = await run_in_threadpool(label_agreement._all_values, files, target)
    positive = (
        positive_value if positive_value is not None else label_agreement.positive_of(values, run)
    )
    if len(values) != 2 or positive is None or positive not in values:
        raise AppError(
            f"Column {target!r} of run {run.id} holds {values}; say which value is positive "
            "(exactly two values are compared).",
            code="agreement_value_invalid",
            status_code=422,
            details={
                "label_run_id": run.id,
                "column": target,
                "values": values,
                "next": {
                    "tool": "dataworks_get_label_run_agreement",
                    "args": {"run_id": run.id, "column": target},
                    "why": "see the column's values",
                },
            },
        )
    strata = await run_in_threadpool(label_agreement._strata_columns, files, by)
    rows = (
        await db.execute(select(Label.row_key, Label.outcome).where(Label.label_run_id == run.id))
    ).all()
    labels = {str(k): str(o) for k, o in rows}
    found = await run_in_threadpool(_disagreeing, files, target, positive, labels, strata)
    return target, positive, strata, found


async def hand_off(
    db: AsyncSession,
    handoff_id: str,
    who: Who,
    *,
    column: str | None = None,
    positive_value: str | None = None,
    by: list[str] | None = None,
) -> dict[str, Any]:
    runs = list(
        (
            await db.execute(
                select(LabelRun)
                .where(LabelRun.handoff_id == handoff_id)
                .order_by(LabelRun.created_at, LabelRun.id)
            )
        ).scalars()
    )
    if not runs:
        raise NotFoundError(
            f"No label run carries hand-off {handoff_id!r}.",
            code="handoff_not_found",
            details={
                "handoff_id": handoff_id,
                "next": {
                    "tool": "dataworks_start_label_run",
                    "args": {"handoff_id": handoff_id},
                    "why": "start the second-opinion runs with this hand-off id",
                },
            },
        )
    planned: list[dict[str, Any]] = []
    for run in runs:
        target, positive, strata, found = await _run_disagreements(
            db, run, column, positive_value, by
        )
        ordered = interleave(found)
        chunks = [ordered[i : i + QUEUE_ROWS] for i in range(0, len(ordered), QUEUE_ROWS)]
        planned.append(
            {
                "run": run,
                "target_column": target,
                "positive_value": positive,
                "by": strata,
                "strata": {s: len(k) for s, k in sorted(found.items())},
                "chunks": chunks,
            }
        )
    needed = sum(len(p["chunks"]) for p in planned)
    cap = int(get_settings().review_max_queues_per_handoff)
    total = sum(sum(p["strata"].values()) for p in planned)
    if needed > cap:
        raise AppError(
            f"{total:,} disagreements need {needed} review queues of at most {QUEUE_ROWS:,} rows; "
            f"REVIEW_MAX_QUEUES_PER_HANDOFF allows {cap}.",
            code="cap_exceeded",
            status_code=422,
            details={
                "setting": "REVIEW_MAX_QUEUES_PER_HANDOFF",
                "limit": cap,
                "actual": needed,
                "disagreements": total,
                "next": NEXT_SETTING,
            },
        )
    out_runs: list[dict[str, Any]] = []
    for p in planned:
        run = p["run"]
        queues = []
        for chunk in p["chunks"]:
            queue = await queue_service.create_label_review(
                db,
                LabelReviewQueueCreate(
                    kind="label_review",
                    label_run_id=run.id,
                    row_keys=chunk,
                    behaviour_key=run.behaviour_key,
                ),
                who,
                handoff_id=handoff_id,
                commit=False,
            )
            queues.append({"queue_id": queue.id, "rows": len(chunk)})
        out_runs.append(
            {
                "label_run_id": run.id,
                "target_column": p["target_column"],
                "positive_value": p["positive_value"],
                "by": p["by"],
                "disagreements": sum(p["strata"].values()),
                "strata": p["strata"],
                "queues": queues,
            }
        )
    await db.commit()
    return {
        "handoff_id": handoff_id,
        "disagreements": total,
        "queues": needed,
        "runs": out_runs,
        "note": "Only the operator decides these rows; an operator override changes the "
        "published label, an agent decision does not (P-10).",
    }
