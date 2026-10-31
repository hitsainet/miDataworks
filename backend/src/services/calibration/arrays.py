"""The only bridge from SQL to the pure layer (FTID 006 section 11).

:func:`load_arrays` reads a calibration set's human labels joined with one label run's scores in
ONE statement, ordered by the set's ``position``, so results never depend on a query plan.
:func:`scores_by_key` reads the same run's scores a SECOND time, by row key, for the
row-alignment check: two independent loads must agree.

A row's score is its probability; without one, a discrete outcome maps to 1 (positive, or the set's
first label), 0 (negative, or the second label) and 0.5 (excluded). Skipped and parse-failure rows
have no score.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import text
from sqlalchemy.orm import Session

LOAD_SQL = text("""
    SELECT sl.position, sl.row_key, sl.human_label, sl.group_key, sl.strata_key,
           sl.is_reference, sl.ratings, l.probability, l.outcome, l.distribution
    FROM dw_calibration_set_labels sl
    LEFT JOIN dw_labels l ON l.row_key = sl.row_key AND l.label_run_id = :run_id
    WHERE sl.calibration_set_id = :set_id
    ORDER BY sl.position
    """)


@dataclass(frozen=True)
class CalibrationArrays:
    positions: NDArray[np.int64]
    row_keys: list[str]
    human: list[str | None]
    groups: list[str | None]
    strata: list[str | None]
    is_reference: list[bool]
    ratings: list[list[int] | None]
    #: NaN where the run has no usable score for the row.
    scores: NDArray[np.float64]
    probabilities: list[float | None]
    outcomes: list[str | None]
    distributions: list[dict[str, float] | None]

    def __len__(self) -> int:
        return len(self.row_keys)


def discrete_score(outcome: str | None, label_set: Sequence[str]) -> float | None:
    if outcome is None:
        return None
    if outcome == "positive" or outcome == label_set[0]:
        return 1.0
    if outcome == "negative" or (len(label_set) > 1 and outcome == label_set[1]):
        return 0.0
    if outcome == "excluded":
        return 0.5
    return None


def score_of(
    probability: float | None, outcome: str | None, label_set: Sequence[str]
) -> float | None:
    if probability is not None:
        return float(probability)
    return discrete_score(outcome, label_set)


def load_arrays(
    session: Session, set_id: str, run_id: str, label_set: Sequence[str]
) -> CalibrationArrays:
    rows = session.execute(LOAD_SQL, {"set_id": set_id, "run_id": run_id}).all()
    scores = [score_of(r.probability, r.outcome, label_set) for r in rows]
    return CalibrationArrays(
        positions=np.array([r.position for r in rows], dtype=np.int64),
        row_keys=[r.row_key for r in rows],
        human=[r.human_label for r in rows],
        groups=[r.group_key for r in rows],
        strata=[r.strata_key for r in rows],
        is_reference=[bool(r.is_reference) for r in rows],
        ratings=[list(r.ratings) if r.ratings is not None else None for r in rows],
        scores=np.array([np.nan if s is None else s for s in scores], dtype=np.float64),
        probabilities=[r.probability for r in rows],
        outcomes=[r.outcome for r in rows],
        distributions=[r.distribution for r in rows],
    )


def scores_by_key(session: Session, run_id: str, label_set: Sequence[str]) -> dict[str, float]:
    """The run's usable scores keyed by row key: the second, independent load."""
    rows = session.execute(
        text("SELECT row_key, probability, outcome FROM dw_labels WHERE label_run_id = :run_id"),
        {"run_id": run_id},
    ).all()
    out: dict[str, float] = {}
    for r in rows:
        s = score_of(r.probability, r.outcome, label_set)
        if s is not None:
            out[r.row_key] = s
    return out


def unscored_labeled(arrays: CalibrationArrays) -> int:
    """Human-labeled, non-reference rows the run has no usable score for."""
    return int(
        sum(
            1
            for h, ref, s in zip(arrays.human, arrays.is_reference, arrays.scores, strict=True)
            if h is not None and not ref and np.isnan(s)
        )
    )


def unscored_count_sql(session: Session, set_id: str, run_id: str, label_set: Sequence[str]) -> int:
    """The same count as :func:`unscored_labeled`, in SQL, for the request-time refusal."""
    value: Any = session.execute(
        text("""
            SELECT count(*) FROM dw_calibration_set_labels sl
            LEFT JOIN dw_labels l ON l.row_key = sl.row_key AND l.label_run_id = :run_id
            WHERE sl.calibration_set_id = :set_id AND sl.human_label IS NOT NULL
              AND NOT sl.is_reference
              AND (l.row_key IS NULL OR (l.probability IS NULL
                   AND l.outcome NOT IN ('positive', 'negative', 'excluded')
                   AND NOT (l.outcome = ANY(:labels))))
            """),
        {"set_id": set_id, "run_id": run_id, "labels": list(label_set[:2])},
    ).scalar_one()
    return int(value)
