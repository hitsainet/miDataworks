"""The shortcut audit and its cross-tab (FR-004.26–004.32; FTDD 004 §6.3, §6.4).

One implementation, two entry points: :func:`audit_table` scores an in-memory table (the cell
balancer's re-audit and the ``shortcut_audit`` report operator), and :func:`run_audit` loads a
version's files and calls it (post-build audits, on-demand runs, ``evaluate_warnings``).

Audited: every ``metadata``-role column, the derived ``length_band`` (deciles of total content
characters), ``_dw_source_id``, ``_dw_origin``, and ``role`` in a cross-role audit (FR-004.27).
Excluded, and listed with the reason: content columns, the other system columns, the label, and
every label-derived column a labeler wrote (FR-004.28). Rows whose label is null are not scored and
are counted as ``unlabelled_rows``.

The audit stores FIGURES. Whether a figure warns depends on the level in force when it is read, so
warnings are evaluated elsewhere (``api.evaluate_warnings``) and never stored.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.storage import resolve_under_data_dir
from ...models.enums import ColumnRole
from ...models.version import Version
from ..duck import connect, files_param
from ..step_contract import EVENTS_FILE, part_files
from . import label_columns
from .binning import NULL_LABEL, content_length, quantile_edges, top_values
from .codes import (
    ORDER_COLUMNS,
    ROLE_COLUMN,
    Encoded,
    InputFiles,
    ReportInput,
    as_inputs,
    encode,
    files_for,
    label_codes,
    load_table,
    quote,
    schema_names,
)
from .label_columns import LabelColumns
from .shortcut_rules import fold_ids, permutations, score_column

logger = logging.getLogger(__name__)

#: System columns the audit scores; every other ``_dw_`` column is excluded.
AUDITED_SYSTEM = ("_dw_source_id", "_dw_origin")
LENGTH_BAND = "length_band"


class AuditRefusal(Exception):
    """An input the audit cannot score; ``code`` is the stable code (FTID 004 §12)."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class AuditSettings:
    folds: int = 5
    control_runs: int = 5
    tolerance_pp: float = 2.0


def audited_and_excluded(
    columns: Sequence[str],
    roles: Mapping[str, str],
    label_column: str,
    derived: Mapping[str, str],
    *,
    cross_role: bool = False,
    label_sources: Mapping[str, str] | None = None,
    construction: Mapping[str, str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Split the table's columns into audited ones and excluded ones with reasons.

    ``label_sources`` maps a column the label was COMPUTED FROM to who declared it (feature 009's
    ``label_source_columns``): such a column predicts the label by definition, and the probe never
    reads it, so it is excluded and reported as ``label_source``, never audited and never dropped
    silently.
    """
    sources = dict(label_sources or {})
    built = dict(construction or {})
    audited: list[str] = []
    excluded: list[dict[str, Any]] = []
    for column in columns:
        if column == label_column:
            excluded.append({"column": column, "reason": "label"})
        elif column in built:
            # the label is a row's role in a constructed pair: these columns record the
            # construction, so they predict it by definition (``label_columns.pair_construction``)
            excluded.append(
                {"column": column, "reason": "pair_construction", "source_operator": built[column]}
            )
        elif column in derived:
            excluded.append(
                {"column": column, "reason": "label_derived", "source_operator": derived[column]}
            )
        elif column in sources:
            excluded.append(
                {"column": column, "reason": "label_source", "declared_by": sources[column]}
            )
        elif column in AUDITED_SYSTEM:
            audited.append(column)
        elif column.startswith("_dw_"):
            excluded.append({"column": column, "reason": "system"})
        elif cross_role and column == ROLE_COLUMN:
            audited.append(column)
        elif roles.get(column) == ColumnRole.CONTENT:
            excluded.append({"column": column, "reason": "content"})
        elif roles.get(column, ColumnRole.METADATA) == ColumnRole.METADATA:
            audited.append(column)
        else:
            excluded.append({"column": column, "reason": "system"})
    return audited, excluded


def _length_band(table: pa.Table, content: Sequence[str]) -> pa.Array:
    totals = np.zeros(table.num_rows, dtype=np.float64)
    for column in content:
        if column in table.schema.names:
            totals += np.fromiter(
                (content_length(v) for v in table.column(column).to_pylist()),
                dtype=np.float64,
                count=table.num_rows,
            )
    return pa.array(totals, pa.float64())


def _per_value(enc: Encoded, y: np.ndarray, k: int, labels: Sequence[str]) -> dict[str, Any]:
    n_values = len(enc.names)
    counts = np.bincount(enc.codes * k + y, minlength=n_values * k).reshape(n_values, k)
    per_value = {
        enc.names[v]: {labels[c]: int(counts[v, c]) for c in range(k) if counts[v, c]}
        for v in range(n_values)
        if counts[v].sum()
    }
    return per_value


def audit_table(
    table: pa.Table,
    roles: Mapping[str, str],
    label_column: str,
    *,
    derived: Mapping[str, str] | None = None,
    seed: int,
    settings: AuditSettings | None = None,
    cross_role: bool = False,
    label_source: str = "chosen",
    label_sources: Mapping[str, str] | None = None,
    construction: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Score every audited column of ``table`` (FR-004.29–004.31). Returns the result document."""
    cfg = settings or AuditSettings()
    derived = dict(derived or {})
    if label_column not in table.schema.names:
        raise AuditRefusal(
            "no_label_column",
            f"This version has no column {label_column!r} to audit against. Add a labeling step "
            "(feature 005) or choose a categorical label column.",
            {"label_column": label_column},
        )
    if any(c in table.schema.names for c in ORDER_COLUMNS):
        order = pc.sort_indices(
            table, sort_keys=[(c, "ascending") for c in ORDER_COLUMNS if c in table.schema.names]
        )
        table = table.take(order)
    y_all, classes, present = label_codes(table.column(label_column))
    unlabelled = int((~present).sum())
    if unlabelled:
        table = table.filter(pa.array(present))
        y_all = y_all[present]
    k = len(classes)
    if k < 2:
        raise AuditRefusal(
            "single_class_label",
            f"{label_column!r} has one class only ({classes[0] if classes else 'none'}): nothing "
            "to predict, so no column can be a shortcut.",
            {"label_column": label_column, "classes": classes},
        )
    smallest = int(np.bincount(y_all, minlength=k).min())
    if smallest < cfg.folds:
        raise AuditRefusal(
            "insufficient_rows",
            f"Every class needs at least {cfg.folds} rows for {cfg.folds}-fold held-out scoring; "
            f"the smallest class has {smallest}. Add rows before auditing.",
            {"minimum_per_class": cfg.folds, "smallest_class": smallest},
        )
    content = sorted(c for c, r in roles.items() if r == ColumnRole.CONTENT)
    audited, excluded = audited_and_excluded(
        table.schema.names,
        roles,
        label_column,
        derived,
        cross_role=cross_role,
        label_sources=label_sources,
        construction=construction,
    )
    chance = 1.0 / k
    fold_index = fold_ids(y_all, cfg.folds, seed)
    permuted = permutations(y_all, folds=cfg.folds, seed=seed, runs=cfg.control_runs)
    columns_out: list[dict[str, Any]] = []
    encoded: dict[str, Encoded] = {}
    for column in [*audited, LENGTH_BAND]:
        if column == LENGTH_BAND:
            if not content:
                continue
            enc = encode(_length_band(table, content), binned=True)
            kind = "derived"
        else:
            enc = encode(table.column(column))
            kind = "derived" if column in (*AUDITED_SYSTEM, ROLE_COLUMN) else "metadata"
        encoded[column] = enc
        score = score_column(
            enc.codes,
            y_all,
            k,
            folds=cfg.folds,
            seed=seed,
            tolerance_pp=cfg.tolerance_pp,
            fold_index=fold_index,
            permuted=permuted,
        )
        per_value = _per_value(enc, y_all, k, classes)
        columns_out.append(
            {
                "column": column,
                "kind": kind,
                "n_rows": int(table.num_rows),
                "n_values": len(per_value),
                "classes": classes,
                "chance": chance,
                "figure": score.figure,
                "control_mean": score.control,
                "control_runs": cfg.control_runs,
                "folds": cfg.folds,
                "valid": score.valid,
                "invalid_reason": (
                    None
                    if score.valid
                    else (
                        f"The permuted-label control read {score.control:.1%}, more than "
                        f"{cfg.tolerance_pp:g} points above chance ({chance:.1%}); this column's "
                        "figure cannot be trusted."
                    )
                ),
                "per_value": top_values(per_value),
                "bins": enc.edges,
            }
        )
    return {
        "label_column": label_column,
        "label_source": label_source,
        "classes": classes,
        "class_counts": {classes[c]: int(n) for c, n in enumerate(np.bincount(y_all, minlength=k))},
        "n_rows": int(table.num_rows),
        "unlabelled_rows": unlabelled,
        "chance": chance,
        "columns": columns_out,
        "excluded_columns": excluded,
        "excluded_by_band": {},
        "sample_seed": int(seed),
        "settings": {
            "folds": cfg.folds,
            "control_runs": cfg.control_runs,
            "tolerance_pp": cfg.tolerance_pp,
        },
        "edges": {c: e.edges for c, e in encoded.items() if e.edges is not None},
    }


def length_band_edges(table: pa.Table, content: Sequence[str]) -> list[float]:
    """The length band's edges for ``table`` (used to find a band's rows again)."""
    values = np.asarray(_length_band(table, content).to_numpy(zero_copy_only=False))
    return quantile_edges(values)


# --------------------------------------------------------------------------------------------
# Versions: load, resolve the label, audit, cross-tab excluded counts, cell samples
# --------------------------------------------------------------------------------------------


def _version(session: Session, version_id: str) -> Version:
    from .api import require_version

    return require_version(session, version_id)


def resolve_for_inputs(
    session: Session, inputs: Sequence[ReportInput], chosen: str | None
) -> tuple[LabelColumns, dict[str, str], list[InputFiles], bool]:
    """Label columns, merged roles, input files and whether the audit is cross-role."""
    versions = [_version(session, ri.version_id) for ri in inputs]
    labels = label_columns.resolve(session, versions[0].id, chosen)
    roles: dict[str, str] = {}
    for v in versions:
        roles.update(v.column_roles)
    sources = [files_for(v, ri) for v, ri in zip(versions, inputs, strict=True)]
    cross_role = len(inputs) > 1 and any(ri.role for ri in inputs)
    return labels, roles, sources, cross_role


def run_audit(
    session: Session,
    inputs: Sequence[ReportInput],
    params: Mapping[str, Any],
    seed: int,
) -> tuple[dict[str, Any], int]:
    """The audit of one or more inputs; ``params`` carries ``label_column`` and the settings."""
    labels, roles, sources, cross_role = resolve_for_inputs(
        session, inputs, params.get("label_column")
    )
    if labels.label is None:
        raise AuditRefusal(
            "no_label_column",
            "This version has no label column: no labeling step wrote one and none was chosen. "
            "Add a labeling step (feature 005), or run the audit with a categorical label column.",
        )
    available = schema_names(sources)
    columns = list(available)
    table = load_table(sources, columns, cross_role=cross_role)
    cfg = AuditSettings(
        folds=int(params["folds"]),
        control_runs=int(params["control_runs"]),
        tolerance_pp=float(params["tolerance_pp"]),
    )
    result = audit_table(
        table,
        roles,
        labels.label,
        derived=labels.derived,
        seed=seed,
        settings=cfg,
        cross_role=cross_role,
        label_source=labels.source or "chosen",
        label_sources=params.get("label_sources"),
        construction=labels.construction,
    )
    if labels.labeler_execution_id is not None and labels.labeler_step_index is not None:
        result["excluded_by_band"] = excluded_by_band(
            session,
            inputs[0].version_id,
            labels.labeler_execution_id,
            labels.labeler_step_index,
            [c["column"] for c in result["columns"] if c["kind"] == "metadata"],
        )
    return result, int(table.num_rows)


def excluded_by_band(
    session: Session,
    version_id: str,
    labeler_execution_id: str,
    labeler_step_index: int,
    columns: Sequence[str],
) -> dict[str, dict[str, int]]:
    """Per audited column, ``{value: rows the labeler excluded}`` (FR-004.32).

    Read from the labeler step's ``dropped`` events with reason ``excluded_by_band``; the excluded
    rows' metadata values come from that step's INPUT, the preceding step's output, joined on
    (row key, occurrence) — they are not in the version, which is why the drop log is needed.
    """
    from ...models.step_execution import StepExecution
    from ...models.version import VersionStep

    labeler = session.get(StepExecution, labeler_execution_id)
    previous = session.execute(
        select(StepExecution)
        .join(VersionStep, VersionStep.step_execution_id == StepExecution.id)
        .where(
            VersionStep.version_id == version_id,
            VersionStep.step_index == labeler_step_index - 1,
        )
    ).scalar_one_or_none()
    if labeler is None or previous is None or not columns:
        return {}
    events = resolve_under_data_dir(labeler.output_dir) / EVENTS_FILE
    parts = part_files(resolve_under_data_dir(previous.output_dir))
    if not events.is_file() or not parts:
        return {}
    out: dict[str, dict[str, int]] = {}
    con = connect()
    try:
        names = {
            str(r[0])
            for r in con.execute(
                "SELECT name FROM parquet_schema(?)", [files_param(parts)]
            ).fetchall()
        }
        for column in columns:
            if column not in names:
                continue
            q = quote(column)
            rows = con.execute(
                f"SELECT CAST(i.{q} AS VARCHAR) AS v, count(*) FROM read_parquet(?) e "  # noqa: S608
                "JOIN read_parquet(?) i ON i._dw_row_key = e.row_key "
                "AND i._dw_occurrence = e.occurrence "
                "WHERE e.kind = 'dropped' AND e.reason_code = 'excluded_by_band' GROUP BY 1",
                [str(events), files_param(parts)],
            ).fetchall()
            out[column] = {(NULL_LABEL if v is None else str(v)): int(n) for v, n in rows}
    finally:
        con.close()
    return out


def cell_samples(
    session: Session,
    report: Any,
    column: str,
    value: str,
    label: str,
    *,
    page: int,
    limit: int,
    sample_size: int,
) -> dict[str, Any]:
    """Up to ``sample_size`` seeded rows of one (column value x label) cell, paged (FR-004.32).

    The seed is the report's own, so the same cell always shows the same rows (the prototype's
    ``records/crosstab_samples.md`` drew 30 per cell with a fixed seed).
    """
    result = report.result or {}
    label_column = result.get("label_column")
    known = {c["column"]: c for c in result.get("columns", [])}
    if label_column is None or column not in known:
        raise AuditRefusal(
            "cell_not_found", f"The audit has no column {column!r}.", {"column": column}
        )
    inputs = as_inputs(report.inputs)
    _, roles, sources, cross_role = resolve_for_inputs(session, inputs, label_column)
    available = schema_names(sources)
    content = sorted(c for c, r in roles.items() if r == ColumnRole.CONTENT and c in available)
    wanted = list(dict.fromkeys([*ORDER_COLUMNS, label_column, *content]))
    if column != LENGTH_BAND and not (cross_role and column == ROLE_COLUMN):
        wanted.append(column)
    table = load_table(
        sources, [c for c in wanted if c in available or c in ORDER_COLUMNS], cross_role=cross_role
    )
    if column == LENGTH_BAND:
        values = encode(_length_band(table, content), binned=True)
    else:
        values = encode(table.column(column))
    edges = known[column].get("bins")
    if edges is not None and column != LENGTH_BAND:
        values = encode(table.column(column), binned=True)
    names = values.names
    if value not in names:
        return {"rows": [], "seed": int(report.seed), "total": 0}
    labels_text = [None if v is None else str(v) for v in table.column(label_column).to_pylist()]
    code = names.index(value)
    idx = [i for i in range(table.num_rows) if values.codes[i] == code and labels_text[i] == label]
    rng = np.random.default_rng(int(report.seed))
    chosen = [idx[i] for i in rng.permutation(len(idx))[:sample_size]]
    page_rows = chosen[page * limit : (page + 1) * limit]
    shown = table.take(pa.array(page_rows, pa.int64())).to_pylist() if page_rows else []
    rows = [
        {
            "row_key": r.get("_dw_row_key"),
            "occurrence": r.get("_dw_occurrence"),
            "label": r.get(label_column),
            "excerpt": {c: excerpt(r.get(c)) for c in content},
        }
        for r in shown
    ]
    return {"rows": rows, "seed": int(report.seed), "total": len(chosen), "cell_rows": len(idx)}


def excerpt(value: Any, n: int = 160) -> str:
    """At most ``n`` characters of a cell, for samples and pairs (FTID §3.4)."""
    if value is None:
        return ""
    if isinstance(value, list):
        text = " | ".join(
            str(m.get("content", "")) if isinstance(m, dict) else str(m) for m in value
        )
    else:
        text = str(value)
    return text if len(text) <= n else text[: n - 1] + "…"


def compute_audit(
    session: Session, inputs: list[ReportInput], params: dict[str, Any], seed: int
) -> Any:
    """The report service's compute function for ``shortcut_audit``."""
    from .report_service import Computed

    result, rows = run_audit(session, inputs, params, seed)
    return Computed(result=result, artefacts=[], rows=rows)
