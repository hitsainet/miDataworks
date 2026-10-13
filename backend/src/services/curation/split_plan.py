"""The split operator's plan: per-stratum rounding, groups kept whole, guards (FTID 004 §7.4).

Pure. Rows arrive ordered by (stratum, row key, occurrence). Within each stratum the units (rows,
or whole groups when a group column is named) are shuffled with the step seed; every NON-first
split takes ``round(fraction x n)`` rows — Python's ``round()`` of the float product, which is what
pandas ``sample(frac=...)`` does in the prototype's ``stratified_split`` — and the first split takes
the rest. On the 2026-10-05 pool that reproduces ``records/publish_prep.json`` exactly.

Guards: fractions sum to 1 within 1e-9 (``split_fractions_invalid``); a generated row never lands in
a held-out split (``generated_in_held_out``); a stratum with fewer rows than splits goes wholly to
the first split and is listed; a group spanning strata takes its first row's stratum, and any
shortfall against a target is reported, never hidden (FR-004.49).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

FRACTION_TOLERANCE = 1e-9


class SplitRefusal(ValueError):
    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class SplitSpec:
    name: str
    fraction: float
    held_out: bool


@dataclass
class SplitPlan:
    #: Per row (input order): the index into ``specs`` it is assigned to.
    assignment: np.ndarray
    specs: list[SplitSpec]
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    shortfalls: list[dict[str, object]] = field(default_factory=list)
    small_strata: list[str] = field(default_factory=list)

    def names(self) -> list[str]:
        return [self.specs[i].name for i in self.assignment]


def check_specs(specs: Sequence[SplitSpec]) -> None:
    if len(specs) < 2:
        raise SplitRefusal(
            "split_fractions_invalid",
            "A split needs at least two named splits (for example train and test).",
            {"splits": [s.name for s in specs]},
        )
    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise SplitRefusal(
            "split_fractions_invalid", "Split names must be unique.", {"splits": names}
        )
    if any(s.fraction < 0 or s.fraction > 1 for s in specs):
        raise SplitRefusal(
            "split_fractions_invalid",
            "Each split's fraction must be between 0 and 1.",
            {"fractions": [s.fraction for s in specs]},
        )
    total = sum(s.fraction for s in specs)
    if abs(total - 1.0) > FRACTION_TOLERANCE:
        raise SplitRefusal(
            "split_fractions_invalid",
            f"The split fractions add up to {total:g}, not 1. Adjust them so they sum to 1.",
            {"fractions": [s.fraction for s in specs], "sum": total},
        )


def plan_split(
    strata: Sequence[str],
    groups: Sequence[str | None] | None,
    origins: Sequence[str | None],
    specs: Sequence[SplitSpec],
    seed: int,
) -> SplitPlan:
    """Assign every row to a split. ``strata``/``groups``/``origins`` are per row, in key order."""
    check_specs(specs)
    spec_list = list(specs)
    n = len(strata)
    assignment = np.zeros(n, dtype=np.int32)
    rng = np.random.default_rng(seed)
    plan = SplitPlan(assignment=assignment, specs=spec_list)

    # Units: a group (all its rows) or a single row; a group's stratum is its first row's.
    unit_rows: dict[str, list[int]] = {}
    unit_stratum: dict[str, str] = {}
    unit_order: list[str] = []
    for i in range(n):
        group = groups[i] if groups is not None else None
        unit = f"g:{group}" if group is not None else f"r:{i}"
        if unit not in unit_rows:
            unit_rows[unit] = []
            unit_stratum[unit] = strata[i]
            unit_order.append(unit)
        unit_rows[unit].append(i)
    by_stratum: dict[str, list[str]] = {}
    for unit in unit_order:
        by_stratum.setdefault(unit_stratum[unit], []).append(unit)

    for stratum in sorted(by_stratum):
        units = by_stratum[stratum]
        size = sum(len(unit_rows[u]) for u in units)
        if size < len(spec_list):
            plan.small_strata.append(stratum)
            continue  # everything stays in the first split
        order = rng.permutation(len(units))
        remaining = [units[j] for j in order]
        targets = [round(spec.fraction * size) for spec in spec_list[1:]]
        for split_offset, target in enumerate(targets):
            filled = 0
            left: list[str] = []
            for unit in remaining:
                rows = unit_rows[unit]
                if filled < target and filled + len(rows) <= target:
                    for row in rows:
                        assignment[row] = split_offset + 1
                    filled += len(rows)
                else:
                    # A whole group that would overshoot stays for a later split or the first.
                    left.append(unit)
            remaining = left
            if filled != target:
                plan.shortfalls.append(
                    {
                        "stratum": stratum,
                        "split": spec_list[split_offset + 1].name,
                        "target": target,
                        "assigned": filled,
                    }
                )
    held_out = {i for i, s in enumerate(spec_list) if s.held_out}
    generated = [
        i for i in range(n) if origins[i] == "generated" and int(assignment[i]) in held_out
    ]
    if generated:
        split = spec_list[int(assignment[generated[0]])].name
        raise SplitRefusal(
            "generated_in_held_out",
            f"{len(generated)} generated row(s) would be placed in the held-out split {split!r}. "
            "A held-out split holds only rows from sources (FR-002.31): move the generation step "
            "after the split, or filter generated rows out first.",
            {"rows": generated[:20], "split": split},
        )
    for i in range(n):
        name = spec_list[int(assignment[i])].name
        cell = plan.counts.setdefault(strata[i], {})
        cell[name] = cell.get(name, 0) + 1
    return plan
