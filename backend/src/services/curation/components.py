"""Connected components over several group columns (feature 017 FR-017.1; FTID 017 section 7.1).

Pure. Two rows belong to one unit when they share a non-null value in ANY of the group columns
(``carrier_id``, ``pair_id``, ...), transitively: a row sharing a carrier with a second row and a
pair with a third puts all three in one unit, so no group of any column spans two splits.

With a single group column the units are exactly the old ``g:<value>`` units of ``split@1.0.0``
(rows with a null value are single-row units), in the same order (first row's position), so the
split plan assigns byte-identically when nothing new is declared.
"""

from __future__ import annotations

from collections.abc import Sequence


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
