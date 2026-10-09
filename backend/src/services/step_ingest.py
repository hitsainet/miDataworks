"""Step ingestion: accounting, key verification and event storage (FR-002.24, 002.25, 002.47).

FTID 002 section 3.6. When a step's output appears, before anything downstream reads it:

1. ``meta.json`` must carry every count (``step_meta_incomplete``) or report the executor's error.
2. Accounting, in DuckDB, over ``(row key, occurrence)`` pairs — never bare keys, because duplicate
   keys are allowed (FR-002.21, FR-002.47):
   - every input pair is exactly one of: unchanged in the output, the ``from`` of a ``changed``
     event, or ``dropped``;
   - every output pair is exactly one of: an unchanged input, the ``to`` of a ``changed`` event,
     or ``added``;
   - every event carries a reason code and a reason; every drop and change carries a statistic;
   - the counts in ``meta.json`` equal the counts measured here.
   A violation fails the step with the counts and up to 20 sample pairs. No drop is silent.
3. A sample of changed and added rows (1%, at least 100, all when fewer) is re-keyed with
   :func:`compute_row_key`; a disagreement is ``row_key_mismatch``.
4. Events are copied into ``dw_row_events`` with ``COPY`` in chunks, and the execution is marked
   completed, in ONE transaction: a failure part-way leaves no events and no completed step.
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
from sqlalchemy.orm import Session

from ..core.clock import utc_now
from ..core.config import get_settings
from ..core.errors import AppError
from ..models.enums import ColumnRole, StepState
from ..models.step_execution import StepExecution
from .duck import connect, files_param
from .identity import logical_digest
from .row_keys import RowKeyError, compute_row_key
from .step_contract import EVENTS_FILE, StepMetaIncomplete, part_files, read_meta

logger = logging.getLogger(__name__)

SAMPLE_LIMIT = 20
EVENT_COLUMNS = (
    "step_execution_id",
    "seq",
    "kind",
    "row_key",
    "occurrence",
    "new_row_key",
    "new_occurrence",
    "related_row_key",
    "parent_keys",
    "split",
    "reason_code",
    "reason",
    "statistic_name",
    "statistic_value",
    "statistic_text",
    "threshold",
)


class StepFailed(AppError):
    """A step's output failed a check; the build fails with this envelope."""

    status_code = 409


def _pairs(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [{"row_key": r[0], "occurrence": r[1]} for r in rows[:SAMPLE_LIMIT]]


@dataclass(frozen=True)
class Accounting:
    rows_in: int
    rows_kept: int
    rows_changed: int
    rows_dropped: int
    rows_added: int
    rows_split_assigned: int
    reason_counts: list[dict[str, Any]]


def _views(
    con: duckdb.DuckDBPyConnection, inputs: list[Path], outputs: list[Path], events: Path
) -> None:
    con.execute(
        "CREATE TEMP TABLE i AS SELECT _dw_row_key AS k, _dw_occurrence AS o FROM read_parquet(?)",
        [files_param(inputs)],
    )
    con.execute(
        "CREATE TEMP TABLE o AS SELECT _dw_row_key AS k, _dw_occurrence AS o FROM read_parquet(?)",
        [files_param(outputs)],
    )
    con.execute("CREATE TEMP TABLE ev AS SELECT * FROM read_parquet(?)", [str(events)])
    con.execute(
        "CREATE TEMP VIEW d AS SELECT row_key AS k, occurrence AS o FROM ev WHERE kind = 'dropped'"
    )
    con.execute(
        "CREATE TEMP VIEW cf AS SELECT row_key AS k, occurrence AS o FROM ev WHERE kind = 'changed'"
    )
    con.execute(
        "CREATE TEMP VIEW ct AS SELECT new_row_key AS k, new_occurrence AS o FROM ev "
        "WHERE kind = 'changed'"
    )
    con.execute(
        "CREATE TEMP VIEW a AS SELECT row_key AS k, occurrence AS o FROM ev WHERE kind = 'added'"
    )
    con.execute(
        "CREATE TEMP VIEW s AS SELECT row_key AS k, occurrence AS o FROM ev "
        "WHERE kind = 'split_assigned'"
    )
    con.execute(
        "CREATE TEMP VIEW unchanged AS (SELECT k, o FROM i EXCEPT SELECT k, o FROM d) "
        "EXCEPT SELECT k, o FROM cf"
    )


def _fail(code: str, message: str, **details: Any) -> StepFailed:
    return StepFailed(message, code=code, details=details)


def check_accounting(
    inputs: list[Path], outputs: list[Path], events: Path, meta: dict[str, Any], label: str
) -> Accounting:
    """The four-way accounting of FTID 002 section 3.6, raising on the first violation."""
    con = connect()
    try:
        _views(con, inputs, outputs, events)

        def rows(sql: str) -> list[tuple[Any, ...]]:
            return con.execute(sql).fetchall()

        def count(view: str) -> int:
            # ``view`` is one of this function's own view names, never input.
            sql = f"SELECT count(*) FROM {view}"  # noqa: S608
            return int(con.execute(sql).fetchone()[0])  # type: ignore[index]

        unreasoned = rows(
            "SELECT row_key, occurrence, kind FROM ev WHERE coalesce(reason_code, '') = '' "
            "OR coalesce(reason, '') = '' OR (kind IN ('dropped', 'changed') AND "
            "(statistic_name IS NULL OR (statistic_value IS NULL AND statistic_text IS NULL)))"
        )
        if unreasoned:
            raise _fail(
                "unreasoned_event",
                f"{label} {unreasoned[0][2]} {len(unreasoned)} rows with no reason or statistic. "
                "Fix the operator, or remove the step and build again.",
                count=len(unreasoned),
                sample=_pairs(unreasoned),
            )
        counts = {
            "in": count("i"),
            "dropped": count("d"),
            "changed": count("cf"),
            "added": count("a"),
            "split_assigned": count("s"),
            "out": count("o"),
        }
        counts["kept"] = count("unchanged")
        checks: list[tuple[str, str, str]] = [
            (
                "output_duplicate_pair",
                "SELECT k, o FROM o GROUP BY k, o HAVING count(*) > 1",
                "the output names one (row key, occurrence) more than once",
            ),
            (
                "duplicate_event",
                "SELECT row_key, occurrence FROM ev WHERE kind IN ('dropped', 'changed') "
                "GROUP BY row_key, occurrence HAVING count(*) > 1",
                "one input row has more than one drop or change event",
            ),
            (
                "event_for_missing_input",
                "SELECT k, o FROM (SELECT k, o FROM d UNION SELECT k, o FROM cf) "
                "EXCEPT SELECT k, o FROM i",
                "events name input rows that do not exist",
            ),
            (
                "input_row_lost",
                "SELECT k, o FROM unchanged EXCEPT SELECT k, o FROM o",
                "input rows are missing from the output with no event",
            ),
            (
                "output_row_unaccounted",
                "((SELECT k, o FROM o EXCEPT SELECT k, o FROM unchanged) EXCEPT "
                "SELECT k, o FROM ct) EXCEPT SELECT k, o FROM a",
                "output rows appeared with no added or changed event",
            ),
            (
                "event_target_missing",
                "(SELECT k, o FROM ct UNION SELECT k, o FROM a) EXCEPT SELECT k, o FROM o",
                "changed or added events name rows the output does not have",
            ),
            (
                "output_row_counted_twice",
                "SELECT k, o FROM unchanged INTERSECT (SELECT k, o FROM ct UNION ALL "
                "SELECT k, o FROM a)",
                "an output row is both an unchanged input and a changed or added row",
            ),
            (
                "split_event_for_missing_row",
                "SELECT k, o FROM s EXCEPT SELECT k, o FROM o",
                "split_assigned events name rows the output does not have",
            ),
        ]
        for direction, sql, what in checks:
            found = rows(sql)
            if found:
                raise _fail(
                    "accounting_mismatch",
                    f"{label}: accounting does not close — {what} ({len(found)} rows). In "
                    f"{counts['in']}, kept {counts['kept']}, changed {counts['changed']}, dropped "
                    f"{counts['dropped']}, added {counts['added']}.",
                    direction=direction,
                    counts=counts,
                    sample=_pairs(found),
                )
        reported = {
            "in": meta["rows_in"],
            "kept": meta["rows_kept"],
            "changed": meta["rows_changed"],
            "dropped": meta["rows_dropped"],
            "added": meta["rows_added"],
            "split_assigned": meta["rows_split_assigned"],
        }
        measured = {k: counts[k] for k in reported}
        if (
            reported != measured
            or counts["in"] != counts["kept"] + counts["changed"] + counts["dropped"]
        ):
            raise _fail(
                "accounting_mismatch",
                f"{label}: the step reported {reported} but its rows and events show {measured}.",
                direction="counts_disagree",
                counts=counts,
                reported=reported,
            )
        reasons = [
            {"kind": kind, "reason_code": code, "count": int(n), "example": example}
            for kind, code, n, example in rows(
                "SELECT kind, reason_code, count(*), min(reason) FROM ev "
                "WHERE kind IN ('dropped', 'changed') GROUP BY kind, reason_code "
                "ORDER BY kind, count(*) DESC, reason_code"
            )
        ]
        return Accounting(
            rows_in=counts["in"],
            rows_kept=counts["kept"],
            rows_changed=counts["changed"],
            rows_dropped=counts["dropped"],
            rows_added=counts["added"],
            rows_split_assigned=counts["split_assigned"],
            reason_counts=reasons,
        )
    finally:
        con.close()


def verify_keys(
    outputs: list[Path], events: Path, roles: dict[str, str], scheme: str, label: str
) -> int:
    """Re-key a deterministic sample of changed and added rows; returns how many were checked."""
    content = sorted(c for c, r in roles.items() if r == ColumnRole.CONTENT)
    con = connect()
    try:
        total = int(
            con.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE kind IN ('changed', 'added')",
                [str(events)],
            ).fetchone()[
                0
            ]  # type: ignore[index]
        )
        if total == 0:
            return 0
        limit = max(100, total // 100)
        table = con.execute(
            "WITH targets AS (SELECT coalesce(new_row_key, row_key) AS k, "
            "coalesce(new_occurrence, occurrence) AS o FROM read_parquet(?) "
            "WHERE kind IN ('changed', 'added') ORDER BY k, o LIMIT ?) "
            "SELECT p.* FROM read_parquet(?) p JOIN targets t "
            "ON p._dw_row_key = t.k AND p._dw_occurrence = t.o",
            [str(events), limit, files_param(outputs)],
        ).to_arrow_table()
    finally:
        con.close()
    for row in table.to_pylist():
        try:
            actual = compute_row_key(row, content, scheme)
        except RowKeyError as exc:
            raise _fail(exc.code, f"{label}: {exc.message}", **exc.details) from None
        if actual != row["_dw_row_key"]:
            raise _fail(
                "row_key_mismatch",
                f"{label} reported row key {row['_dw_row_key'][:12]}… but the row's content "
                f"hashes to {actual[:12]}…. The operator must key rows with compute_row_key.",
                reported=row["_dw_row_key"],
                actual=actual,
            )
    return int(table.num_rows)


def _bytea(hex_key: str | None) -> str | None:
    return None if hex_key is None else "\\x" + hex_key


def _array(keys: list[str] | None) -> str | None:
    if keys is None:
        return None
    return "{" + ",".join(f'"\\\\x{k}"' for k in keys) + "}"


def copy_events(session: Session, execution_id: str, events: Path) -> int:
    """``COPY`` events into ``dw_row_events`` in chunks, inside the caller's transaction."""
    chunk = get_settings().row_event_copy_chunk
    raw = session.connection().connection.driver_connection
    assert raw is not None
    cursor = raw.cursor()
    sql = f"COPY dw_row_events ({', '.join(EVENT_COLUMNS)}) FROM STDIN WITH (FORMAT csv)"
    seq = 0
    handle = pq.ParquetFile(events)
    for batch in handle.iter_batches(batch_size=chunk):
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        for event in batch.to_pylist():
            threshold = event.get("threshold")
            writer.writerow(
                [
                    execution_id,
                    seq,
                    event["kind"],
                    _bytea(event["row_key"]),
                    event["occurrence"],
                    _bytea(event.get("new_row_key")),
                    event.get("new_occurrence"),
                    _bytea(event.get("related_row_key")),
                    _array(event.get("parent_keys")),
                    event.get("split"),
                    event["reason_code"],
                    event["reason"],
                    event.get("statistic_name"),
                    event.get("statistic_value"),
                    event.get("statistic_text"),
                    json.dumps(json.loads(threshold)) if threshold else None,
                ]
            )
            seq += 1
        buffer.seek(0)
        cursor.copy_expert(sql, buffer)
    return seq


def ingest(
    session: Session,
    execution: StepExecution,
    input_dir: Path,
    output_dir: Path,
    scheme: str,
    label: str,
) -> Accounting:
    """Check, verify and store one finished step, then mark it completed. Commits."""
    try:
        meta = read_meta(output_dir)
    except StepMetaIncomplete as exc:
        raise _fail(StepMetaIncomplete.code, f"{label}: {exc}") from None
    if meta.get("error"):
        error = meta["error"]
        raise _fail(
            str(error.get("code", "step_failed")),
            f"{label} failed: {error.get('message', 'the executor reported an error')}",
            executor_error=error,
        )
    inputs, outputs = part_files(input_dir), part_files(output_dir)
    events = output_dir / EVENTS_FILE
    if not events.is_file():
        raise _fail(StepMetaIncomplete.code, f"{label}: the step wrote no {EVENTS_FILE}.")
    accounting = check_accounting(inputs, outputs, events, meta, label)
    roles = dict(meta["output_column_roles"])
    verify_keys(outputs, events, roles, scheme, label)
    copied = copy_events(session, execution.id, events)
    execution.rows_in = accounting.rows_in
    execution.rows_kept = accounting.rows_kept
    execution.rows_changed = accounting.rows_changed
    execution.rows_dropped = accounting.rows_dropped
    execution.rows_added = accounting.rows_added
    execution.rows_split_assigned = accounting.rows_split_assigned
    execution.reason_counts = accounting.reason_counts
    execution.output_column_roles = roles
    execution.split_roles = meta.get("split_roles")
    execution.output_logical_digest = logical_digest(outputs)
    execution.state = StepState.COMPLETED
    execution.completed_at = utc_now()
    execution.error = None
    session.commit()
    logger.info(
        "ingested step execution %s: in %d kept %d changed %d dropped %d added %d events %d",
        execution.id,
        accounting.rows_in,
        accounting.rows_kept,
        accounting.rows_changed,
        accounting.rows_dropped,
        accounting.rows_added,
        copied,
    )
    return accounting
