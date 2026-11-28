"""A calibration set's human labels against a version's label column (ENH-006 D116 G4; FTASKS 20.14).

D-7 reads a labeler's calibration record, and a behaviour's training label comes from its plan, so no
check measures that label against a person. In the dry run this read, by hand, found the largest label
fault of the run. It answers on the rows of the version the set labelled: how often the human and the
column agree, overall and per stratum, split by the column's own answer (a column can be right on its
yes rows and wrong on its no rows), each with its counts and a 95% Wilson interval, and how many
labelled rows the version does not hold.

A row is compared when it holds a value in the column and no ``exclude`` entry removes it. The human's
yes is the set's first label; the column's yes is ``positive_value``, named by the caller and compared
as a string the way a label mapping compares it. Reads the files as built, before any review decision,
so a set's answers never agree with themselves through ``label``. Nothing is written.

Runs through ``run_in_own_session``: it reads Parquet through DuckDB, off the event loop and off the
greenlet ``run_sync`` would use (``core.database``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.errors import AppError, NotFoundError
from ...core.storage import resolve_under_data_dir
from ...models.calibration import CalibrationSet, CalibrationSetLabel
from ...models.version import Version
from ...rules.next_step import next_for
from ..detector_sets.label_rules import value_key
from ..duck import arrow_table, connect, files_param, quote_ident, top_level_columns
from ..labeling_rules import wilson_interval
from ..version_crosstab import scoped_splits

#: The most strata one answer lists; the lowest agreement comes first.
MAX_STRATA = 500
NOTE = (
    "The human answered the set's question and the column answers its own, so a disagreement is "
    "where to read, not an error rate. Exclude a column value the two questions decide differently "
    "on purpose. Reads the version's files as built; a review decision is not applied here."
)


@dataclass
class _Tally:
    yes_rows: int = 0
    yes_agree: int = 0
    no_rows: int = 0
    no_agree: int = 0

    def add(self, column_yes: bool, human_yes: bool) -> None:
        if column_yes:
            self.yes_rows += 1
            self.yes_agree += int(human_yes)
        else:
            self.no_rows += 1
            self.no_agree += int(not human_yes)

    @property
    def rows(self) -> int:
        return self.yes_rows + self.no_rows

    @property
    def agree(self) -> int:
        return self.yes_agree + self.no_agree


def _figure(agree: int, rows: int) -> dict[str, Any]:
    if rows == 0:
        return {"rows": 0, "agree": 0, "agreement": None, "ci_low": None, "ci_high": None}
    share, low, high = wilson_interval(agree, rows)
    return {"rows": rows, "agree": agree, "agreement": share, "ci_low": low, "ci_high": high}


def _out(tally: _Tally) -> dict[str, Any]:
    return {
        **_figure(tally.agree, tally.rows),
        "column_yes": _figure(tally.yes_agree, tally.yes_rows),
        "column_no": _figure(tally.no_agree, tally.no_rows),
    }


def _labelled_rows(
    files: list[Any],
    keys: list[str],
    label_column: str,
    by: Sequence[str],
    exclude_columns: Sequence[str],
) -> list[dict[str, Any]]:
    """The version's rows whose key the set labelled, with only the columns this read needs."""
    con = connect()
    try:
        present: set[str] = set()
        for path in files:
            present.update(top_level_columns(con, [path]))
        wanted = list(dict.fromkeys(["_dw_row_key", label_column, *by, *exclude_columns]))
        select_list = ", ".join(quote_ident(c, present) for c in wanted)
        con.register("labelled", pa.table({"row_key": pa.array(keys, pa.string())}))
        table = arrow_table(
            con,
            f"SELECT {select_list} FROM read_parquet(?, union_by_name=true) "  # noqa: S608 - quoted
            "WHERE _dw_row_key IN (SELECT row_key FROM labelled)",
            [files_param(files)],
        )
    finally:
        con.close()
    rows: list[dict[str, Any]] = table.to_pylist()
    return rows


def agreement(
    session: Session,
    set_id: str,
    version_id: str,
    label_column: str,
    positive_value: str,
    *,
    split: str | None = None,
    by: Sequence[str] = (),
    exclude: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, Any]:
    from ..version_read_service import _uuid

    calibration = session.get(CalibrationSet, set_id)
    if calibration is None:
        raise NotFoundError(f"No calibration set {set_id}.", code="CALIBRATION_SET_NOT_FOUND")
    if len(calibration.label_set) != 2:
        raise AppError(
            f"Calibration set {set_id} has {len(calibration.label_set)} labels; agreement with a "
            "yes-or-no column needs two.",
            code="calibration_set_not_binary",
            status_code=422,
            details={"label_set": list(calibration.label_set)},
        )
    version = session.get(Version, _uuid(version_id))
    if version is None:
        raise NotFoundError(f"No version {version_id}.", code="version_not_found")
    splits = scoped_splits(version, split)
    human_yes = str(calibration.label_set[0])
    answers = dict(
        session.execute(
            select(CalibrationSetLabel.row_key, CalibrationSetLabel.human_label).where(
                CalibrationSetLabel.calibration_set_id == set_id,
                CalibrationSetLabel.human_label.is_not(None),
            )
        ).all()
    )
    leaving = {column: {str(v) for v in values} for column, values in (exclude or {}).items()}
    files = [resolve_under_data_dir(s["path"]) for s in splits]
    rows = _labelled_rows(files, sorted(answers), label_column, by, sorted(leaving))

    found: set[str] = set()
    overall = _Tally()
    strata: dict[tuple[str, ...], _Tally] = {}
    column_values: Counter[str] = Counter()
    excluded_rows = 0
    no_value_rows = 0
    for row in rows:
        key = row["_dw_row_key"]
        found.add(key)
        if row[label_column] is None:
            no_value_rows += 1
            continue
        value = value_key(row[label_column])
        column_values[value] += 1
        if any(value_key(row[c]) in values for c, values in leaving.items()):
            excluded_rows += 1
            continue
        column_yes = value == positive_value
        said_yes = str(answers[key]) == human_yes
        overall.add(column_yes, said_yes)
        if by:
            stratum = tuple(value_key(row[c]) for c in by)
            strata.setdefault(stratum, _Tally()).add(column_yes, said_yes)
    if column_values and positive_value not in column_values:
        raise AppError(
            f"No labelled row holds {positive_value!r} in {label_column!r}, so every row would count "
            f"as the column's no. It holds: {sorted(column_values)}.",
            code="positive_value_not_found",
            status_code=422,
            details={
                "label_column": label_column,
                "positive_value": positive_value,
                "column_values": dict(sorted(column_values.items())),
                "next": next_for(
                    "dataworks_get_version_crosstab",
                    {"version_id": str(version.id), "columns": [label_column]},
                    "read the values the column holds",
                ),
            },
        )
    listed = sorted(
        (
            {"values": dict(zip(by, stratum, strict=True)), **_out(tally)}
            for stratum, tally in strata.items()
        ),
        key=lambda s: (
            s["agreement"] if s["agreement"] is not None else 2.0,
            -s["rows"],
            str(sorted(s["values"].items())),
        ),
    )
    return {
        "calibration_set_id": set_id,
        "version_id": str(version.id),
        "split": split,
        "label_column": label_column,
        "positive_value": positive_value,
        "human_positive": human_yes,
        "by": list(by),
        "exclude": {c: sorted(v) for c, v in leaving.items()},
        "labelled_rows": len(answers),
        "labelled_rows_found": len(found),
        "labelled_rows_not_found": len(answers) - len(found),
        "rows_read": len(rows),
        "rows_excluded": excluded_rows,
        "rows_no_value": no_value_rows,
        "column_values": dict(sorted(column_values.items())),
        "overall": _out(overall),
        "strata": listed[:MAX_STRATA],
        "strata_total": len(listed),
        "strata_truncated": len(listed) > MAX_STRATA,
        "note": NOTE,
    }
