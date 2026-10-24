"""The on-disk shape of one step's output (C-002.5, C-002.10; FTDD 002 section 6.4).

Feature 003's executor writes, and feature 002's ingestion reads, exactly this:

``<output_dir>/part-*.parquet``
    Every output row with every column, including the ``_dw_`` system columns (FR-002.23).
``<output_dir>/events.parquet``
    One row per dropped, changed, added or split-assigned row, in :data:`EVENT_SCHEMA`.
``<output_dir>/meta.json``
    :data:`META_COUNT_FIELDS`, ``output_column_roles``, optional ``split_roles``
    (``{split: {"held_out": bool}}``, from 004's split operator) and optional ``error``.

The executor writes under ``staging/`` and renames into ``output_dir`` (Foundation's writer), so a
reader that sees ``meta.json`` sees a complete step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow as pa

#: Reserved system columns (FR-002.23), with their Arrow types (FTDD 002 section 4.3).
SYSTEM_COLUMNS: dict[str, pa.DataType] = {
    "_dw_row_key": pa.string(),
    "_dw_occurrence": pa.int32(),
    "_dw_split": pa.string(),
    "_dw_origin": pa.string(),
    "_dw_source_id": pa.string(),
    "_dw_source_locator": pa.string(),
    "_dw_parent_keys": pa.list_(pa.string()),
}

RESERVED_PREFIX = "_dw_"

EVENT_SCHEMA = pa.schema(
    [
        ("kind", pa.string()),
        ("row_key", pa.string()),
        ("occurrence", pa.int32()),
        ("new_row_key", pa.string()),
        ("new_occurrence", pa.int32()),
        ("related_row_key", pa.string()),
        ("parent_keys", pa.list_(pa.string())),
        ("split", pa.string()),
        ("reason_code", pa.string()),
        ("reason", pa.string()),
        ("statistic_name", pa.string()),
        ("statistic_value", pa.float64()),
        ("statistic_text", pa.string()),
        ("threshold", pa.string()),  # JSON text {value, comparator}
    ]
)

META_COUNT_FIELDS: tuple[str, ...] = (
    "rows_in",
    "rows_kept",
    "rows_changed",
    "rows_dropped",
    "rows_added",
    "rows_split_assigned",
)

META_FILE = "meta.json"
EVENTS_FILE = "events.parquet"
PART_GLOB = "part-*.parquet"


class StepMetaIncomplete(ValueError):
    code = "step_meta_incomplete"


def part_files(directory: Path) -> list[Path]:
    return sorted(directory.glob(PART_GLOB))


def is_finished(directory: Path) -> bool:
    return (directory / META_FILE).is_file()


def read_meta(directory: Path) -> dict[str, Any]:
    """``meta.json``, refusing one that lacks a count field (``step_meta_incomplete``)."""
    path = directory / META_FILE
    try:
        meta: dict[str, Any] = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise StepMetaIncomplete(f"{path} could not be read: {exc}") from None
    if meta.get("error"):
        return meta
    missing = [f for f in META_COUNT_FIELDS if not isinstance(meta.get(f), int)]
    if missing or not isinstance(meta.get("output_column_roles"), dict):
        raise StepMetaIncomplete(
            f"The step's meta.json lacks {missing or ['output_column_roles']}. The operator's "
            "executor must report every count."
        )
    return meta


def empty_events_table() -> pa.Table:
    return EVENT_SCHEMA.empty_table()
