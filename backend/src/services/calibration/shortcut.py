"""004's held-out predictiveness for the calibration shortcut check (FR-006.14, FR-004.29).

The check ``shortcut_comparison`` asks whether any metadata column ALONE predicts the human label
at or above the labeler's AUROC. 004 computes that figure (``audit_service.audit_table``: held-out
balanced accuracy of each column's per-value prediction, with a permuted-label control). For a
binary label a hard per-value prediction's balanced accuracy is exactly the AUROC of that 0/1
prediction, so the two numbers are on one scale.

This module is the only I/O for that check: it reads the set version's metadata columns for the
SAME rows the labeler's AUROC uses (labeled, non-reference, scored), adds the human label, and runs
004's audit. Columns the human label is computed from (the mapping's human-label column and its
ratings column) predict it by definition and are passed to 004 as label sources, so they are
reported as excluded, never as a shortcut. A column whose permuted control is invalid is left out
with its reason. When 004 refuses (too few rows per class, one class), the check says why instead
of passing (ADR-027).

Until 2026-10-17 the record path passed ``shortcut=None`` unconditionally, so every record said
"004's held-out predictiveness (FR-004.29) is not served" although 004 had served it since
2026-10-07.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa

from ...models.enums import ColumnRole
from ...models.version import Version
from ..curation.audit_service import AuditRefusal, audit_table
from ..label_inputs import ROW_KEY
from .constants import SEED_PERMUTATION
from .mapping import read_rows

#: The human label's column in the table handed to 004 (cannot collide: ``_dw_`` is reserved).
HUMAN = "_dw_calibration_human"


@dataclass(frozen=True)
class ShortcutFigures:
    #: Per audited metadata column, 004's held-out figure; None when 004 refused.
    figures: dict[str, float] | None
    reason: str
    #: 004's audit document (columns, exclusions, settings) for the record's metrics.
    audit: dict[str, Any] | None = None
    invalid: dict[str, str] = field(default_factory=dict)


def label_source_columns(mapping: Mapping[str, Any] | None) -> dict[str, str]:
    """The version columns the human label is computed from, as 004's ``label_sources``."""
    if not mapping:
        return {}
    out: dict[str, str] = {}
    human = mapping.get("human_label") or {}
    if human.get("column"):
        out[str(human["column"])] = "calibration mapping: human label"
    ratings = mapping.get("ratings") or {}
    if ratings.get("column"):
        out[str(ratings["column"])] = "calibration mapping: per-rater ratings"
    return out


def shortcut_figures(
    version: Version,
    mapping: Mapping[str, Any] | None,
    row_keys: Sequence[str],
    y: Sequence[int],
    label_set: Sequence[str],
) -> ShortcutFigures:
    """004's figure per metadata column, on exactly ``row_keys`` labelled ``y`` (1 = first label)."""
    roles = dict(version.column_roles or {})
    metadata = [
        c for c, r in roles.items() if r == ColumnRole.METADATA and not str(c).startswith("_dw_")
    ]
    sources = label_source_columns(mapping)
    if not metadata:
        return ShortcutFigures({}, "the version has no metadata column to audit")
    _, raw = read_rows(version.splits, metadata)
    by_key: dict[str, dict[str, Any]] = {}
    for row in raw:
        by_key.setdefault(str(row[ROW_KEY]), row)
    missing = [k for k in row_keys if k not in by_key]
    if missing:
        return ShortcutFigures(
            None, f"{len(missing)} labeled row(s) are not in the set's version, so 004 cannot audit"
        )
    columns: dict[str, list[Any]] = {
        c: [by_key[k].get(c) for k in row_keys] for c in metadata if c in raw[0]
    }
    names = [str(label_set[0]), str(label_set[1]) if len(label_set) > 1 else "other"]
    columns[HUMAN] = [names[0] if v == 1 else names[1] for v in y]
    table = pa.table(columns)
    audit_roles = {c: ColumnRole.METADATA.value for c in columns if c != HUMAN}
    try:
        audit = audit_table(table, audit_roles, HUMAN, seed=SEED_PERMUTATION, label_sources=sources)
    except AuditRefusal as exc:
        return ShortcutFigures(None, f"004 refused the audit: {exc.message}")
    figures: dict[str, float] = {}
    invalid: dict[str, str] = {}
    # Only metadata columns are handed to 004 (no content column, so no derived length band).
    for col in audit["columns"]:
        if col["valid"]:
            figures[col["column"]] = float(col["figure"])
        else:
            invalid[col["column"]] = str(col["invalid_reason"])
    return ShortcutFigures(figures, "004 held-out balanced accuracy (FR-004.29)", audit, invalid)
