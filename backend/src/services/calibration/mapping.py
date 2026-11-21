"""Evaluate a ``dw.calibration-mapping/v1`` over a version's rows (FR-006.1 – FR-006.4; FTDD 006
section 4.3).

Guarantees:
- every named column exists in the version's files, and the human-label column's role is not
  ``content`` (002 FR-002.20), so the human label can never reach the labeler;
- labels are materialised in VERSION row order (splits in order, rows in file order), each row key
  once at its first position; two rows that share a key and disagree refuse the set
  (``ROW_KEY_CONFLICT``, naming the key and both labels);
- per-rater ratings are parsed and checked for sortedness (FR-006.4);
- values are read with DuckDB through ``services/duck.py`` (bound parameters, quoted identifiers).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...core.canonical_json import canonical_json, canonical_sha256
from ...core.errors import AppError, ConflictError
from ...models.enums import ColumnRole
from ...schemas.calibration import CalibrationMapping, NumericRule
from ..duck import connect, files_param, quote_ident, top_level_columns
from ..identity import bytes_sha256
from ..label_inputs import ROW_KEY, version_files
from .ceiling import parse_ratings, ratings_sorted


def question_hash(text: str) -> str:
    """SHA-256 of the question's UTF-8 bytes (shared with review)."""
    return bytes_sha256(text.encode("utf-8"))


def mapping_invalid(message: str, **details: Any) -> AppError:
    return AppError(message, code="MAPPING_INVALID", status_code=422, details=details)


@dataclass(frozen=True)
class LabelRow:
    row_key: str
    position: int
    human_label: str | None
    group_key: str | None
    strata_key: str | None
    is_reference: bool
    ratings: list[int] | None


@dataclass
class MappingResult:
    rows: list[LabelRow]
    counts: dict[str, int]
    ratings_sorted: bool | None
    mapping_hash: str
    warnings: list[str] = field(default_factory=list)


def _label_for(value: Any, mapping: CalibrationMapping, label_set: Sequence[str]) -> str | None:
    rule = mapping.human_label
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(rule, NumericRule):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number >= rule.positive_at_or_above:
            return label_set[0]
        if number <= rule.negative_at_or_below:
            return label_set[1]
        return None
    target = rule.map.get(str(value))
    if target is None:
        return None
    if target == "positive":
        return label_set[0]
    if target == "negative":
        return label_set[1]
    return target


def check_mapping(
    mapping: CalibrationMapping,
    label_set: Sequence[str],
    columns: Sequence[str],
    roles: Mapping[str, str],
) -> None:
    named = [mapping.human_label.column]
    if mapping.ratings is not None:
        named.append(mapping.ratings.column)
    if mapping.group is not None:
        named.append(mapping.group.column)
    if mapping.reference is not None:
        named.append(mapping.reference.column)
    named += list(mapping.strata)
    missing = sorted({c for c in named if c not in columns})
    if missing:
        raise mapping_invalid(
            f"The version has no column {missing[0]!r}. Pick columns from the version's schema.",
            missing=missing,
        )
    label_column = mapping.human_label.column
    if roles.get(label_column) == ColumnRole.CONTENT:
        raise mapping_invalid(
            f"Column {label_column!r} is a content column, so the labeler would see the human "
            "label. Use a metadata column, or rebuild the version with it as metadata.",
            column=label_column,
        )
    if len(set(label_set)) != len(label_set):
        raise mapping_invalid("The label set repeats a label.", label_set=list(label_set))
    rule = mapping.human_label
    if not isinstance(rule, NumericRule):
        allowed = {"positive", "negative", *label_set}
        bad = sorted({v for v in rule.map.values() if v not in allowed})
        if bad:
            raise mapping_invalid(
                f"Map values to positive, negative or a label of the set; {bad[0]!r} is none.",
                values=bad,
            )


def read_rows(
    splits: Sequence[Mapping[str, Any]], columns_wanted: Sequence[str]
) -> tuple[list[str], list[dict[str, Any]]]:
    """Every row of the version's split files, in version order, with the wanted columns."""
    files = version_files(splits)
    con = connect()
    try:
        columns = top_level_columns(con, files)
        if not files:
            return columns, []
        wanted = [c for c in dict.fromkeys([ROW_KEY, *columns_wanted]) if c in columns]
        select = ", ".join(quote_ident(c, columns) for c in wanted)
        out: list[dict[str, Any]] = []
        for f in files:
            cursor = con.execute(
                f"SELECT {select} FROM read_parquet(?)",  # noqa: S608 - identifiers quoted
                [files_param([f])],
            )
            names = [d[0] for d in cursor.description or []]
            out += [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]
        return columns, out
    finally:
        con.close()


def evaluate(
    splits: Sequence[Mapping[str, Any]],
    roles: Mapping[str, str],
    mapping: CalibrationMapping,
    label_set: Sequence[str],
) -> MappingResult:
    """Validate the mapping against the version and materialise its human labels."""
    files = version_files(splits)
    con = connect()
    try:
        columns = top_level_columns(con, files) if files else []
    finally:
        con.close()
    if ROW_KEY not in columns:
        raise mapping_invalid(
            "The version's files have no row key column; it was not built by 002."
        )
    check_mapping(mapping, label_set, columns, roles)
    wanted = [mapping.human_label.column, *mapping.strata]
    for extra in (mapping.ratings, mapping.group, mapping.reference):
        if extra is not None:
            wanted.append(extra.column)
    _, raw = read_rows(splits, wanted)

    seen: dict[str, LabelRow] = {}
    rows: list[LabelRow] = []
    parsed_ratings: list[list[int] | None] = []
    for position, record in enumerate(raw):
        key = str(record[ROW_KEY])
        is_ref = bool(
            mapping.reference is not None
            and record.get(mapping.reference.column) == mapping.reference.value
        )
        label = (
            None
            if is_ref
            else _label_for(record.get(mapping.human_label.column), mapping, label_set)
        )
        group = record.get(mapping.group.column) if mapping.group is not None else None
        strata = (
            canonical_json({c: record.get(c) for c in mapping.strata}).decode("utf-8")
            if mapping.strata
            else None
        )
        try:
            ratings = (
                parse_ratings(record.get(mapping.ratings.column), mapping.ratings.format)
                if mapping.ratings is not None
                else None
            )
        except ValueError as exc:
            raise mapping_invalid(
                f"Row {position} of the ratings column cannot be read: {exc}.", row=position
            ) from None
        row = LabelRow(
            key, len(rows), label, None if group is None else str(group), strata, is_ref, ratings
        )
        previous = seen.get(key)
        if previous is not None:
            if previous.human_label != row.human_label:
                raise ConflictError(
                    f"Two rows share row key {key[:12]}… with different human labels "
                    f"({previous.human_label!r} and {row.human_label!r}). Fix the source or map "
                    "a column that agrees for identical rows.",
                    code="ROW_KEY_CONFLICT",
                    details={"row_key": key, "labels": [previous.human_label, row.human_label]},
                )
            continue
        seen[key] = row
        rows.append(row)
        parsed_ratings.append(ratings)

    positives = sum(1 for r in rows if r.human_label == label_set[0])
    labeled = sum(1 for r in rows if r.human_label is not None)
    counts = {
        "rows": len(rows),
        "labeled": labeled,
        "positives": positives,
        "negatives": labeled - positives,
        "excluded": sum(1 for r in rows if r.human_label is None and not r.is_reference),
        "references": sum(1 for r in rows if r.is_reference),
        "groups": len({r.group_key for r in rows if r.group_key is not None}),
        "rows_with_ratings": sum(1 for r in parsed_ratings if r),
    }
    sorted_flag = ratings_sorted(parsed_ratings) if mapping.ratings is not None else None
    warnings: list[str] = []
    if sorted_flag:
        warnings.append(
            "Rating positions are ranks, not rater identities: every row's ratings are sorted. "
            "The ceiling holds out a random rating per row; no fixed position is ever used."
        )
    if positives == 0 or labeled - positives == 0:
        missing = "positives" if positives == 0 else "negatives"
        raise mapping_invalid(
            f"The mapping yields 0 {missing}; AUROC needs both classes. Move the cut points or "
            "map more values.",
            counts=counts,
        )
    return MappingResult(rows, counts, sorted_flag, canonical_sha256(mapping.document()), warnings)
