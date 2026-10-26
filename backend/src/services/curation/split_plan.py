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

Feature 017 (``split@1.1.0``, FR-017.1; FTID 017 section 7.1): units are the connected components
over every group column (``components.py``), and **directed rows go first**: a unit holding a row
whose directing value is held out goes wholly to that row's held-out split (to the "both" split when
its directed rows disagree); only the remaining units are split by fraction. The generated-row
guard stays for every held-out split that is NOT directed; a directed one is decided by the
disjointness proof instead (``held_out_proof``, called by the operator). With no group column beyond
one and no direction, the plan is byte-identical to 1.0.0's.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from .components import components, largest
from .held_out_proof import CITATION

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
    #: Rows in the largest unit (017: a component that swallows much of the data is a signal).
    largest_unit: int = 0
    #: Rows placed by direction (017), before any fraction was drawn.
    directed_rows: int = 0

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
    *,
    group_columns: Sequence[Sequence[str | None]] = (),
    directed: Sequence[int | None] | None = None,
    both: int | None = None,
) -> SplitPlan:
    """Assign every row to a split. ``strata``/``groups``/``origins`` are per row, in key order.

    017: ``group_columns`` are further per-row group lists (units are their connected components
    with ``groups``); ``directed`` is, per row, the index of the held-out split its directing
    values send it to (None: not directed), and ``both`` the index of the split for a unit whose
    directed rows disagree. Directed units are placed first and never drawn by fraction."""
    check_specs(specs)
    spec_list = list(specs)
    n = len(strata)
    assignment = np.zeros(n, dtype=np.int32)
    rng = np.random.default_rng(seed)
    plan = SplitPlan(assignment=assignment, specs=spec_list)

    # Units: the connected components over every group column (a single row when it has no
    # group); a unit's stratum is its first row's.
    columns = ([list(groups)] if groups is not None else []) + [list(c) for c in group_columns]
    found = components(n, columns)
    plan.largest_unit = largest(found)
    unit_rows: dict[int, list[int]] = {}
    unit_stratum: dict[int, str] = {}
    unit_order: list[int] = []
    directed_splits: set[int] = set()
    for u, members in enumerate(found):
        aims = (
            {int(t) for t in (directed[r] for r in members) if t is not None}
            if directed is not None
            else set()
        )
        if aims:
            # Directed first (FR-017.1): the whole unit goes to its held-out split.
            target = aims.pop() if len(aims) == 1 else both
            if target is None:
                raise SplitRefusal(
                    "directed_split_name_conflict",
                    "A group holds rows held out on different directing columns, and no split "
                    "for rows held out on more than one is named.",
                    {"rows": members[:20]},
                )
            for row in members:
                assignment[row] = target
            directed_splits.add(target)
            plan.directed_rows += len(members)
            continue
        unit_rows[u] = members
        unit_stratum[u] = strata[members[0]]
        unit_order.append(u)
    by_stratum: dict[str, list[int]] = {}
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
            left: list[int] = []
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
    # The generated-row guard of every held-out split that is NOT directed (FR-002.31, FR-017.3);
    # a directed split is decided by its disjointness proof.
    held_out = {i for i, s in enumerate(spec_list) if s.held_out} - directed_splits
    generated = [
        i for i in range(n) if origins[i] == "generated" and int(assignment[i]) in held_out
    ]
    if generated:
        split = spec_list[int(assignment[generated[0]])].name
        raise SplitRefusal(
            "generated_in_held_out",
            f"{len(generated)} generated row(s) would be placed in the held-out split {split!r}. "
            "A held-out split holds only rows from sources (FR-002.31): move the generation step "
            "after the split, or filter generated rows out first. Only a value-directed held-out "
            f"split proved disjoint from training may hold them ({CITATION}).",
            {"rows": generated[:20], "split": split, "rule": CITATION},
        )
    for i in range(n):
        name = spec_list[int(assignment[i])].name
        cell = plan.counts.setdefault(strata[i], {})
        cell[name] = cell.get(name, 0) + 1
    return plan
