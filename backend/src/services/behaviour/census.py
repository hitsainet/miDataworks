"""The carrier census (017 FR-017.17, FR-017.21, FR-017.36; FTID §7.4).

Pure: rows per genre x placement x framing x length band against the plan, the templated share
against its ceiling, and each framing's share per genre. A cell or a framing below plan is
**flagged with its shortfall, never refused** (C1 (a)): the build proceeds and a top-up run can
fill it (FR-017.20). A behaviour that declares no decoys reads "no decoys declared", never zero.

The planned counts come from the recorded configuration (``rows`` and the shares) by the same
largest-remainder allocation the plan step used, so a plan's own census has no shortfall and a
version's census measures what generation, filters and drops removed.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .vocabulary import CELL_DIMENSIONS, joint_cells, largest_remainder

#: Cells listed in a stored census, worst first; the rest are counted (FR-017.58 pages them).
LISTED = 50
TEMPLATE = "template"
NO_DECOYS = "no decoys declared"


def planned_cells(configuration: Mapping[str, Any]) -> dict[tuple[str, ...], int]:
    """The plan's row count per (genre, placement, framing, length band) cell."""
    shares = configuration["shares"]
    return largest_remainder(joint_cells(shares, CELL_DIMENSIONS), int(configuration["rows"]))


def census(
    configuration: Mapping[str, Any],
    columns: Mapping[str, Sequence[Any]],
    *,
    listed: int = LISTED,
) -> dict[str, Any]:
    """The census of rows given as ``columns`` (``genre``, ``placement``, ``framing``,
    ``length_band`` and ``generator``, one value per row) against ``configuration``."""
    planned = planned_cells(configuration)
    n = len(columns["genre"])
    actual: Counter[tuple[str, ...]] = Counter(
        tuple(str(columns[d][i]) for d in CELL_DIMENSIONS) for i in range(n)
    )
    cells: list[dict[str, Any]] = []
    for cell in sorted(set(planned) | set(actual)):
        want, have = int(planned.get(cell, 0)), int(actual.get(cell, 0))
        cells.append(
            {
                **dict(zip(CELL_DIMENSIONS, cell, strict=True)),
                "planned": want,
                "actual": have,
                "shortfall": max(want - have, 0),
                "short": have < want,
            }
        )
    cells.sort(key=lambda c: (-c["shortfall"], -c["planned"], [c[d] for d in CELL_DIMENSIONS]))
    short = [c for c in cells if c["short"]]

    generators = list(columns.get("generator", [None] * n))
    templated = sum(1 for g in generators if g == TEMPLATE)
    ceiling = float(configuration.get("templated_ceiling", 0.0))
    share = templated / n if n else None
    over = share is not None and share > ceiling

    by_genre: Counter[str] = Counter(str(g) for g in columns["genre"])
    pairs: Counter[tuple[str, str]] = Counter(
        (str(columns["genre"][i]), str(columns["framing"][i])) for i in range(n)
    )
    framing_shares = configuration["shares"]["framing"]
    # planned rows per (genre, framing): the plan's cells summed over placement and length band
    planned_pairs: Counter[tuple[str, str]] = Counter()
    g, f = CELL_DIMENSIONS.index("genre"), CELL_DIMENSIONS.index("framing")
    for cell, count in planned.items():
        planned_pairs[(cell[g], cell[f])] += count
    framings: dict[str, dict[str, Any]] = {}
    framing_short: list[dict[str, Any]] = []
    for genre in configuration["vocabulary"]["genres"]:
        rows_in_genre = by_genre.get(genre, 0)
        per: dict[str, Any] = {}
        for framing, planned_share in framing_shares.items():
            have = pairs.get((genre, framing), 0)
            expected = planned_pairs.get((genre, framing), 0)
            per[framing] = {
                "planned_share": planned_share,
                "actual_share": (have / rows_in_genre) if rows_in_genre else None,
                "rows": have,
                "planned_rows": expected,
                "shortfall": max(expected - have, 0),
            }
            if have < expected:
                framing_short.append(
                    {"genre": genre, "framing": framing, "shortfall": expected - have}
                )
        framings[genre] = per
    framing_short.sort(key=lambda f: (-f["shortfall"], f["genre"], f["framing"]))

    flags = []
    if short:
        flags.append(
            {
                "code": "cell_short",
                "message": f"{len(short)} of {len(cells)} cells hold fewer rows than planned "
                f"({sum(c['shortfall'] for c in short):,} rows short); a top-up run can fill "
                "them.",
            }
        )
    if over:
        flags.append(
            {
                "code": "templated_over_ceiling",
                "message": f"Templated rows are {share:.1%} of the rows; the ceiling is "
                f"{ceiling:.1%}.",
            }
        )
    if framing_short:
        worst = framing_short[0]
        flags.append(
            {
                "code": "framing_short",
                "message": f"{len(framing_short)} framing(s) fall below plan within a genre; "
                f"worst: {worst['framing']!r} in {worst['genre']!r}, {worst['shortfall']:,} "
                "rows short.",
            }
        )
    decoys = list(configuration["vocabulary"].get("decoys") or [])
    return {
        "rows": n,
        "planned_rows": int(configuration["rows"]),
        "dimensions": list(CELL_DIMENSIONS),
        "cells_total": len(cells),
        "cells_short": len(short),
        "cells": cells[:listed],
        "templated": {
            "rows": templated,
            "share": share,
            "ceiling": ceiling,
            "over_ceiling": over,
        },
        "framing_share_by_genre": framings,
        "framing_shortfalls": framing_short[:listed],
        "decoys": decoys if decoys else NO_DECOYS,
        "flags": flags,
    }
