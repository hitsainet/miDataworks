"""The cell balancer's decisions: cells, the cap, extreme-value flags (FTID 004 §7.3).

Pure. Rows arrive as parallel arrays already ordered by (row key, occurrence), so the seeded
within-cell draw is the same whatever order the Parquet files held. ``cell_cap_plan`` caps every
(value x label) cell at the SMALLEST cell, so the column then predicts nothing (R-03.20); the
prototype is ``scripts/build_balanced.py`` ``format_balanced``.

Refusals carry a stable code; the operator raises them as step failures with what to do next.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

#: FTID call (I-h): a value is flagged when one label holds at least this share of its rows ...
EXTREME_SHARE = 0.99
#: ... and it has at least this many rows (Reddit ``news``: 1,387 of 1,390 not humorous).
EXTREME_MIN_ROWS = 50

KEPT = 0
CELL_CAP = 1
VALUE_EXCLUDED = 2


class CellRefusal(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass
class CellPlan:
    #: Per row: KEPT, CELL_CAP or VALUE_EXCLUDED.
    decision: np.ndarray
    cap: int
    #: ``{"value|label": {"value", "label", "before", "after"}}``.
    cells: dict[str, dict[str, object]]
    flags: list[dict[str, object]] = field(default_factory=list)
    excluded_values: list[str] = field(default_factory=list)

    @property
    def kept_mask(self) -> np.ndarray:
        mask: np.ndarray = self.decision == KEPT
        return mask


def cell_table(
    values: Sequence[str], labels: Sequence[str], label_names: Sequence[str]
) -> dict[tuple[str, str], int]:
    """Counts per (value, label) for every value present crossed with every label."""
    counts: dict[tuple[str, str], int] = {}
    for value, label in zip(values, labels, strict=True):
        counts[(value, label)] = counts.get((value, label), 0) + 1
    present = sorted(set(values))
    for value in present:
        for label in label_names:
            counts.setdefault((value, label), 0)
    return counts


def extreme_values(
    counts: dict[tuple[str, str], int],
    *,
    share: float = EXTREME_SHARE,
    min_rows: int = EXTREME_MIN_ROWS,
) -> list[dict[str, object]]:
    """Values whose rows are almost all one label: advice for exclusion, never applied (FR-004.39)."""
    by_value: dict[str, dict[str, int]] = {}
    for (value, label), n in counts.items():
        by_value.setdefault(value, {})[label] = n
    flags = []
    for value in sorted(by_value):
        split = by_value[value]
        total = sum(split.values())
        if total < min_rows:
            continue
        label, top = max(split.items(), key=lambda kv: (kv[1], kv[0]))
        if top / total >= share:
            flags.append(
                {
                    "value": value,
                    "dominant_label": label,
                    "dominant_rows": top,
                    "rows": total,
                    "counts_by_label": dict(sorted(split.items())),
                }
            )
    return flags


def cell_cap_plan(
    values: Sequence[str],
    labels: Sequence[str],
    *,
    exclude_values: Sequence[str],
    seed: int,
) -> CellPlan:
    """Cap every (value x label) cell at the smallest, after dropping the excluded values."""
    n = len(values)
    label_names = sorted(set(labels))
    if len(label_names) < 2:
        raise CellRefusal(
            "single_class_label",
            "The label column has one class, so there is nothing to balance against. Label the "
            "version with both classes first.",
            {"labels": label_names},
        )
    exclude = set(exclude_values)
    decision = np.full(n, KEPT, dtype=np.int8)
    excluded_present: set[str] = set()
    for i, value in enumerate(values):
        if value in exclude:
            decision[i] = VALUE_EXCLUDED
            excluded_present.add(value)
    remaining = [i for i in range(n) if decision[i] == KEPT]
    kept_values = [values[i] for i in remaining]
    kept_labels = [labels[i] for i in remaining]
    if len(set(kept_values)) < 2:
        raise CellRefusal(
            "too_few_values",
            "After exclusions the column has fewer than two values, so it cannot predict the "
            "label and there is nothing to balance. Exclude fewer values.",
            {"values": sorted(set(kept_values))},
        )
    counts = cell_table(kept_values, kept_labels, label_names)
    empty = sorted(cell for cell, size in counts.items() if size == 0)
    if empty:
        value, label = empty[0]
        raise CellRefusal(
            "empty_cell",
            f"The cell {value!r} x {label!r} has no rows, so every cell would be capped at zero. "
            f"Exclude the value {value!r} and preview again.",
            {"value": value, "label": label, "empty_cells": [list(c) for c in empty]},
        )
    cap = min(counts.values())
    rng = np.random.default_rng(seed)
    by_cell: dict[tuple[str, str], list[int]] = {}
    for i in remaining:
        by_cell.setdefault((values[i], labels[i]), []).append(i)
    for cell in sorted(by_cell):
        members = by_cell[cell]  # already in (row key, occurrence) order
        order = rng.permutation(len(members))
        for j in order[cap:]:
            decision[members[j]] = CELL_CAP
    cells = {
        f"{v}|{lab}": {"value": v, "label": lab, "before": size, "after": min(size, cap)}
        for (v, lab), size in sorted(counts.items())
    }
    return CellPlan(
        decision=decision,
        cap=cap,
        cells=cells,
        flags=extreme_values(cell_table(values, labels, label_names)),
        excluded_values=sorted(excluded_present),
    )
