"""Connected components over several group columns (feature 017 FR-017.1; FTID 017 section 7.1).

Pure. Two rows belong to one unit when they share a non-null value in ANY of the group columns
(``carrier_id``, ``pair_id``, ...), transitively: a row sharing a carrier with a second row and a
pair with a third puts all three in one unit, so no group of any column spans two splits.

With a single group column the units are exactly the old ``g:<value>`` units of ``split@1.0.0``
(rows with a null value are single-row units), in the same order (first row's position), so the
split plan assigns byte-identically when nothing new is declared.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Sequence
from dataclasses import dataclass, field


def components(n: int, columns: Sequence[Sequence[str | None]]) -> list[list[int]]:
    """The units of ``n`` rows, each a sorted list of row indexes, ordered by their first row.

    ``columns`` holds one per-row value list per group column (``None`` is no group)."""
    parent = list(range(n))

    def find(i: int) -> int:
        root = i
        while parent[root] != root:
            root = parent[root]
        while parent[i] != root:  # path compression
            parent[i], i = root, parent[i]
        return root

    for values in columns:
        if len(values) != n:
            raise ValueError(f"a group column has {len(values)} values for {n} rows")
        first: dict[str, int] = {}
        for i, value in enumerate(values):
            if value is None:
                continue
            seen = first.setdefault(value, i)
            if seen != i:
                a, b = find(seen), find(i)
                if a != b:
                    # the smaller root wins, so a unit's root is its first row
                    parent[max(a, b)] = min(a, b)
    units: dict[int, list[int]] = {}
    for i in range(n):
        units.setdefault(find(i), []).append(i)
    return [units[root] for root in sorted(units)]


def largest(units: Sequence[Sequence[int]]) -> int:
    """The size of the largest unit (0 for no rows): a build where one unit swallows much of the
    data is a signal worth reporting (FTID 017 section 7.1, step 5)."""
    return max((len(u) for u in units), default=0)


@dataclass
class IdHolders:
    """Who holds one id: the rows per generation run, and the plan cells those rows were planned in
    (``None`` among them: a row whose cell cannot be read)."""

    rows_by_run: Counter[str] = field(default_factory=Counter)
    cells: set[Hashable] = field(default_factory=set)


def holders_of(
    ids: Sequence[str | None],
    runs: Sequence[str | None],
    cells: Sequence[Hashable] | None,
    seen: dict[str, IdHolders] | None = None,
) -> dict[str, IdHolders]:
    """``{id: IdHolders}`` over rows with both an id and a run, added to ``seen`` when given (a
    row-scope operator keeps it across its batches). A row with no run is no carrier of any run: a
    plan row and the carrier a run made from it share their id, and real text has no run at all.
    ``cells`` holds each row's plan cell; ``None`` when the rows carry no plan columns."""
    found: dict[str, IdHolders] = {} if seen is None else seen
    for i, (value, run) in enumerate(zip(ids, runs, strict=True)):
        if value and run:
            holder = found.setdefault(value, IdHolders())
            holder.rows_by_run[run] += 1
            holder.cells.add(None if cells is None else cells[i])
    return found


def unrelated_across_runs(seen: dict[str, IdHolders]) -> dict[str, IdHolders]:
    """The ids held by rows of more than one run that were not planned as one row.

    ``carrier_id`` is a plan row's key, and the key hashes its ``idx``. One run per generator over
    ONE plan gives each plan row one carrier per run under one id, which is the design (017 FTDD
    "one run per generator"): those rows agree on their plan cell. Two runs over two plans with
    overlapping index ranges make the same ids for unrelated plan rows, whose cells differ. Two plans
    that agree on an id's cell cannot be told from one here; with no plan columns (``None``) nothing
    says the rows are related, so they count as unrelated."""
    return {
        value: holder
        for value, holder in seen.items()
        if len(holder.rows_by_run) > 1 and (len(holder.cells) > 1 or None in holder.cells)
    }
