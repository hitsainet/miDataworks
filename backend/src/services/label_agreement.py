"""A label run against a label column the data already has (novice re-walk 2026-10-19, finding 6).

The walk labeled ColBERT's 25,000 rows with a humor probe: 11.9 % positive, on data whose own
``humor`` column is about half positive, and nothing on screen said so. This compares a run's
verdicts with a two-valued column of its input version:

- **Candidates** are the version's own columns (never ``_dw_*`` bookkeeping, never a column the
  labeler read) that hold exactly two values. They are listed so the reader chooses one; the
  first is preselected (booleans first, then names that say "label").
- **Which value is positive** is decided only when it is unambiguous: a boolean's ``true``, a
  ``0``/``1`` column's ``1``, yes/no, positive/negative, or the run's own positive and negative
  label names. Otherwise the answer asks for ``positive_value`` and computes nothing.
- **Compared rows** are the run's ``positive`` and ``negative`` labels whose row has a value in
  the column (a row key's lowest occurrence, as the run read it). Skipped, excluded and failed
  rows are counted as not compared.
- **AUROC** is computed from the run's own score (the classifier's probability, or the probe's
  score) only when every compared row has one and both classes are present.

The column is the dataset's own label, not a checked ground truth; the note says so. Nothing is
written: this is a read.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.errors import AppError
from ..models.label import Label
from ..models.label_run import LabelRun
from . import label_inputs
from .duck import connect, files_param, quote_ident, top_level_columns
from .labeling.stratified import DEFAULT_STRATA

#: How many rows a column is sampled over to decide whether it is a two-valued candidate.
CANDIDATE_SAMPLE_ROWS = 5000
POSITIVE_WORDS = frozenset({"true", "1", "1.0", "yes", "y", "positive", "pos"})
NEGATIVE_WORDS = frozenset({"false", "0", "0.0", "no", "n", "negative", "neg"})
#: Column types a label column can have (DuckDB's names, prefix-matched).
_LABEL_TYPES = ("BOOLEAN", "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "UTINYINT", "USMALLINT",
                "UINTEGER", "UBIGINT", "VARCHAR", "DOUBLE", "FLOAT")  # fmt: skip
CLOSE, PARTIAL = 0.9, 0.75


@dataclass(frozen=True)
class Candidate:
    name: str
    type: str
    values: list[str]


def _candidates(files: list[Path], exclude: set[str]) -> list[Candidate]:
    con = connect()
    try:
        columns = top_level_columns(con, files)
        described = con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [files_param(files)]
        ).fetchall()
        types = {str(row[0]): str(row[1]).upper() for row in described}
        found: list[Candidate] = []
        for name in columns:
            if name.startswith("_dw_") or name in exclude:
                continue
            if not types.get(name, "").startswith(_LABEL_TYPES):
                continue
            col = quote_ident(name, columns)
            sql = (
                f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM (SELECT {col} FROM read_parquet(?) "  # noqa: S608 - identifiers quoted
                f"WHERE {col} IS NOT NULL LIMIT {CANDIDATE_SAMPLE_ROWS}) LIMIT 3"
            )
            values = sorted(str(v[0]) for v in con.execute(sql, [files_param(files)]).fetchall())
            if len(values) == 2:
                found.append(Candidate(name, types[name], values))
    finally:
        con.close()

    def rank(c: Candidate) -> tuple[int, int, str]:
        return (0 if c.type == "BOOLEAN" else 1, 0 if "label" in c.name.lower() else 1, c.name)

    return sorted(found, key=rank)


def _all_values(files: list[Path], column: str) -> list[str]:
    con = connect()
    try:
        columns = top_level_columns(con, files)
        col = quote_ident(column, columns)
        sql = f"SELECT DISTINCT CAST({col} AS VARCHAR) FROM read_parquet(?) WHERE {col} IS NOT NULL LIMIT 3"  # noqa: S608, E501 - identifiers quoted
        return sorted(str(v[0]) for v in con.execute(sql, [files_param(files)]).fetchall())
    finally:
        con.close()


def positive_of(values: list[str], run: LabelRun) -> str | None:
    """The value that means positive, when the column says so unambiguously; else None."""
    lowered = {v.lower(): v for v in values}
    pos = [v for k, v in lowered.items() if k in POSITIVE_WORDS]
    neg = [v for k, v in lowered.items() if k in NEGATIVE_WORDS]
    if len(pos) == 1 and len(neg) == 1:
        return pos[0]
    names = {
        (run.positive_label or "").strip().lower(): "pos",
        (run.negative_label or "").strip().lower(): "neg",
    }
    named = {names.get(k): v for k, v in lowered.items() if names.get(k)}
    if set(named) == {"pos", "neg"}:
        return named["pos"]
    return None


def _joined(files: list[Path], column: str, labels: pa.Table) -> list[tuple[str, Any, str]]:
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident(label_inputs.ROW_KEY, columns)
        col = quote_ident(column, columns)
        con.register("run_labels", labels)
        sql = (
            f"WITH d AS (SELECT {key} AS row_key, CAST({col} AS VARCHAR) AS v FROM read_parquet(?) "  # noqa: S608 - identifiers quoted
            f'WHERE {key} IS NOT NULL QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY "_dw_occurrence") = 1) '
            "SELECT l.outcome, l.score, d.v FROM run_labels l JOIN d ON d.row_key = l.row_key "
            "WHERE d.v IS NOT NULL"
        )
        return [
            (str(o), s, str(v)) for o, s, v in con.execute(sql, [files_param(files)]).fetchall()
        ]
    finally:
        con.close()


def _score(probability: float | None, parsed: Any) -> float | None:
    if probability is not None:
        return float(probability)
    if isinstance(parsed, dict):
        value = parsed.get("score")
        if isinstance(value, int | float) and not isinstance(value, bool):
            return float(value)
    return None


def note_for(
    *, column: str, n: int, accuracy: float, run_share: float, column_share: float, partial: bool
) -> str:
    so_far = " so far" if partial else ""
    parts = [
        f"The run marked {run_share:.1%} of the {n:,} compared rows positive{so_far}; the column "
        f"{column!r} marks {column_share:.1%} of them positive. They agree on {accuracy:.1%}."
    ]
    if accuracy >= CLOSE:
        parts.append("That is close agreement.")
    elif accuracy >= PARTIAL:
        parts.append(
            "That is partial agreement: look at rows where they differ before relying on these labels."
        )  # noqa: E501
    else:
        parts.append(
            "That is weak agreement: the labeler and the column disagree on many rows. Check the "
            "labeler on the Calibration screen, or the column, before relying on these labels."
        )
    if run_share + 0.15 < column_share:
        parts.append("The labeler calls far fewer rows positive than the column does.")
    elif column_share + 0.15 < run_share:
        parts.append("The labeler calls far more rows positive than the column does.")
    parts.append("The column is the dataset's own label; miDataworks has not checked it.")
    return " ".join(parts)


# --- 017 FR-017.43: agreement by stratum ------------------------------------------------------

#: The cells one answer carries (worst first); the rest are reached by ``page_token``.
DEFAULT_CELL_LIMIT = 50
MAX_CELL_LIMIT = 500
NOT_MEASURED = "not measured"


def _partial(run: LabelRun) -> bool:
    """A run that stopped part way (a stop reason, or failed or cancelled): its figures are
    over the rows scored so far (FR-017.41)."""
    return run.stop_reason is not None or run.state in ("failed", "cancelled")


async def _handoff(
    db: AsyncSession, run: LabelRun, handoff_id: str | None
) -> dict[str, Any] | None:
    """The runs of one hand-off (one per question over one selection, TD-017.23)."""
    if handoff_id is None:
        return None
    if run.handoff_id != handoff_id:
        raise AppError(
            f"Label run {run.id} is not part of hand-off {handoff_id!r}"
            + (f" (it is part of {run.handoff_id!r})." if run.handoff_id else "."),
            code="handoff_mismatch",
            status_code=422,
            details={
                "handoff_id": handoff_id,
                "run_handoff_id": run.handoff_id,
                "next": {
                    "tool": "dataworks_list_label_runs",
                    "args": {},
                    "why": "find the runs of the hand-off",
                },
            },
        )
    runs = (
        await db.execute(
            select(LabelRun.id, LabelRun.target_column, LabelRun.state, LabelRun.stop_reason)
            .where(LabelRun.handoff_id == handoff_id)
            .order_by(LabelRun.created_at, LabelRun.id)
        )
    ).all()
    return {
        "handoff_id": handoff_id,
        "runs": [
            {"run_id": r[0], "target_column": r[1], "state": r[2], "stop_reason": r[3]}
            for r in runs
        ],
    }


def _strata_columns(files: list[Path], by: list[str] | None) -> list[str]:
    """``by`` as asked (each must be a column), else FR-017.43's default strata the version has."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
    finally:
        con.close()
    if by is None:
        return [c for c in DEFAULT_STRATA if c in columns]
    missing = [c for c in by if c not in columns or c.startswith("_dw_")]
    if missing:
        raise AppError(
            f"The input version has no column(s) {missing} to group the agreement by.",
            code="agreement_column_invalid",
            status_code=422,
            details={
                "by": list(by),
                "missing": missing,
                "next": {
                    "tool": "dataworks_get_version",
                    "args": {},
                    "why": "read the version's columns",
                },
            },
        )
    return list(by)


def kappa(tp: int, fp: int, fn: int, tn: int) -> float | None:
    """Cohen's kappa of two binary raters; None when chance agreement is total (one class only on
    both sides), where kappa is undefined."""
    n = tp + fp + fn + tn
    if n == 0:
        return None
    observed = (tp + tn) / n
    run_pos, col_pos = (tp + fp) / n, (tp + fn) / n
    chance = run_pos * col_pos + (1 - run_pos) * (1 - col_pos)
    if chance >= 1.0:
        return None
    return (observed - chance) / (1 - chance)


def _cell(rows: list[tuple[str | None, float | None, str]], positive: str) -> dict[str, Any]:
    """One stratum: the selected rows, how many the run scored, and (over the rows it labelled
    positive or negative) the confusion counts, agreement, kappa and AUROC. An empty cell reads
    "not measured", never 1.0 or 0.0."""
    selected = len(rows)
    scored = sum(1 for o, _, _ in rows if o is not None)
    compared = [(o, sc, v) for o, sc, v in rows if o in ("positive", "negative")]
    out: dict[str, Any] = {
        "rows": selected,
        "scored": scored,
        "coverage": scored / selected if selected else None,
        "compared": len(compared),
    }
    if not compared:
        return {
            **out,
            "counts": None,
            "agreement": NOT_MEASURED,
            "kappa": NOT_MEASURED,
            "auroc": NOT_MEASURED,
        }
    predicted = np.array([o == "positive" for o, _, _ in compared])
    actual = np.array([v == positive for _, _, v in compared])
    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    tn = int(np.sum(~predicted & ~actual))
    k = kappa(tp, fp, fn, tn)
    scores = [sc for _, sc, _ in compared]
    auroc: float | str = NOT_MEASURED
    if all(sc is not None for sc in scores) and 0 < int(actual.sum()) < len(compared):
        from .calibration.metrics import auroc as auroc_of

        auroc = float(auroc_of(actual.astype(int), np.array(scores, dtype=float)))
    return {
        **out,
        "counts": {
            "both_positive": tp,
            "run_only_positive": fp,
            "column_only_positive": fn,
            "both_negative": tn,
        },
        "agreement": (tp + tn) / len(compared),
        "kappa": NOT_MEASURED if k is None else k,
        "auroc": auroc,
    }


def _by_stratum(
    files: list[Path],
    column: str,
    positive: str,
    labels: pa.Table,
    strata: list[str],
    limit: int,
    page_token: str | None,
) -> dict[str, Any]:
    """Every stratum of the version's rows (a row key's lowest occurrence), worst agreement first,
    then the cells with nothing measured; ``limit`` cells from ``page_token`` on."""
    con = connect()
    try:
        columns = top_level_columns(con, files)
        key = quote_ident(label_inputs.ROW_KEY, columns)
        col = quote_ident(column, columns)
        picked = ", ".join(
            f"CAST({quote_ident(c, columns)} AS VARCHAR) AS s{i}" for i, c in enumerate(strata)
        )
        sql = (
            f"SELECT {key} AS row_key, CAST({col} AS VARCHAR) AS v, {picked} FROM read_parquet(?) "  # noqa: S608 - identifiers quoted
            f'WHERE {key} IS NOT NULL QUALIFY row_number() OVER (PARTITION BY {key} ORDER BY "_dw_occurrence") = 1'
        )
        data = con.execute(sql, [files_param(files)]).fetchall()
    finally:
        con.close()
    by_key = {
        str(k): (str(o), sc)
        for k, o, sc in zip(
            labels.column("row_key").to_pylist(),
            labels.column("outcome").to_pylist(),
            labels.column("score").to_pylist(),
            strict=True,
        )
    }
    cells: dict[tuple[str, ...], list[tuple[str | None, float | None, str]]] = {}
    for row in data:
        row_key, value, *values = row
        if value is None:
            continue  # no value in the compared column: not part of the comparison
        outcome, score = by_key.get(str(row_key), (None, None))
        cells.setdefault(tuple(str(v) for v in values), []).append((outcome, score, str(value)))
    figured = [
        {"stratum": dict(zip(strata, values, strict=True)), **_cell(rows, positive)}
        for values, rows in cells.items()
    ]

    def order(cell: dict[str, Any]) -> tuple[int, float, str]:
        measured = isinstance(cell["agreement"], float)
        return (0 if measured else 1, cell["agreement"] if measured else 0.0, str(cell["stratum"]))

    figured.sort(key=order)
    limit = max(1, min(int(limit), MAX_CELL_LIMIT))
    start = _offset(page_token)
    page = figured[start : start + limit]
    after = start + len(page)
    return {
        "by": strata,
        "cells_total": len(figured),
        "cells_not_measured": sum(1 for c in figured if c["agreement"] == NOT_MEASURED),
        "cells": page,
        "next_page_token": f"o:{after}" if after < len(figured) else None,
        "coverage": {
            "rows": sum(c["rows"] for c in figured),
            "scored": sum(c["scored"] for c in figured),
        },
    }


def _offset(token: str | None) -> int:
    if token is None:
        return 0
    if not token.startswith("o:") or not token[2:].isdigit():
        raise AppError(
            f"page_token {token!r} is not one this answer gave.",
            code="page_token_invalid",
            status_code=422,
            details={"page_token": token},
        )
    return int(token[2:])


async def agreement(
    db: AsyncSession,
    run: LabelRun,
    column: str | None,
    positive_value: str | None,
    *,
    by: list[str] | None = None,
    limit: int = DEFAULT_CELL_LIMIT,
    page_token: str | None = None,
    handoff_id: str | None = None,
) -> dict[str, Any]:
    from .label_run_service import _version_files

    files = list(await _version_files(db, str(run.input_version_id)))
    if column is None and run.target_column:
        column = run.target_column  # 017 TD-017.25: the run's declared comparison
    handoff = await _handoff(db, run, handoff_id)
    strata = await run_in_threadpool(_strata_columns, files, by)
    exclude = {str(v) for v in (run.field_map or {}).values()}
    candidates = await run_in_threadpool(_candidates, files, exclude)
    base: dict[str, Any] = {
        "label_run_id": run.id,
        "run_state": run.state,
        "columns": [{"name": c.name, "values": c.values} for c in candidates],
        "column": None,
        "values": [],
        "positive_value": None,
        "needs_positive_value": False,
        "rows_compared": 0,
        "rows_not_compared": 0,
        "counts": None,
        "accuracy": None,
        "run_positive_share": None,
        "column_positive_share": None,
        "auroc": None,
        "auroc_from": None,
        "note": None,
        "partial": _partial(run),
        "stop_reason": run.stop_reason,
        "by": strata,
        "strata": None,
        "handoff": handoff,
    }
    if column is None:
        if not candidates:
            return {
                **base,
                "applicable": False,
                "reason": "The version has no column with exactly two values to compare the run with.",
            }
        column = candidates[0].name
    elif column.startswith("_dw_") or column in exclude:
        raise AppError(
            f"Column {column!r} cannot be compared: it is bookkeeping or the text the labeler read.",
            code="agreement_column_invalid",
            status_code=422,
            details={"column": column},
        )
    values = await run_in_threadpool(_all_values, files, column)
    base = {**base, "column": column, "values": values}
    if len(values) != 2:
        return {
            **base,
            "applicable": False,
            "reason": f"Column {column!r} holds {'more than two' if len(values) > 2 else len(values)} "
            "values; agreement needs exactly two.",
        }
    positive = positive_value if positive_value is not None else positive_of(values, run)
    if positive_value is not None and positive_value not in values:
        raise AppError(
            f"{positive_value!r} is not one of column {column!r}'s values ({', '.join(values)}).",
            code="agreement_value_invalid",
            status_code=422,
            details={"values": values},
        )
    if positive is None:
        return {
            **base,
            "applicable": False,
            "needs_positive_value": True,
            "reason": f"Say which of column {column!r}'s values ({' or '.join(values)}) means positive.",
        }
    rows = (
        await db.execute(
            select(Label.row_key, Label.outcome, Label.probability, Label.parsed_value).where(
                Label.label_run_id == run.id
            )
        )
    ).all()
    total_labels = len(rows)
    labels = pa.table(
        {
            "row_key": [r[0] for r in rows],
            "outcome": [r[1] for r in rows],
            "score": pa.array([_score(r[2], r[3]) for r in rows], type=pa.float64()),
        }
    )
    joined = await run_in_threadpool(_joined, files, column, labels)
    compared = [(o, s, v) for o, s, v in joined if o in ("positive", "negative")]
    base = {**base, "positive_value": positive, "rows_not_compared": total_labels - len(compared)}
    if not compared:
        return {
            **base,
            "applicable": False,
            "reason": "No row has both a positive or negative label from this run and a value in "
            f"column {column!r}.",
        }
    predicted = np.array([o == "positive" for o, _, _ in compared])
    actual = np.array([v == positive for _, _, v in compared])
    tp = int(np.sum(predicted & actual))
    fp = int(np.sum(predicted & ~actual))
    fn = int(np.sum(~predicted & actual))
    tn = int(np.sum(~predicted & ~actual))
    n = len(compared)
    accuracy = (tp + tn) / n
    run_share, column_share = (tp + fp) / n, (tp + fn) / n
    scores = [s for _, s, _ in compared]
    auroc: float | None = None
    if all(s is not None for s in scores) and 0 < int(actual.sum()) < n:
        from .calibration.metrics import auroc as auroc_of

        auroc = auroc_of(actual.astype(int), np.array(scores, dtype=float))
    if strata:
        base["strata"] = await run_in_threadpool(
            _by_stratum, files, column, positive, labels, strata, limit, page_token
        )
    return {
        **base,
        "applicable": True,
        "reason": None,
        "rows_compared": n,
        "counts": {
            "both_positive": tp,
            "run_only_positive": fp,
            "column_only_positive": fn,
            "both_negative": tn,
        },
        "accuracy": accuracy,
        "run_positive_share": run_share,
        "column_positive_share": column_share,
        "auroc": auroc,
        "auroc_from": (
            None
            if auroc is None
            else ("probe score" if run.kind == "probe_verdict" else "probability")
        ),
        "note": note_for(
            column=column,
            n=n,
            accuracy=accuracy,
            run_share=run_share,
            column_share=column_share,
            partial=run.state != "completed",
        ),
    }
