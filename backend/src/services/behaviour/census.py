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

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from .vocabulary import CELL_DIMENSIONS, joint_cells, largest_remainder

#: Cells listed in a stored census, worst first; the rest are counted (FR-017.58 pages them).
LISTED = 50
TEMPLATE = "template"
NO_DECOYS = "no decoys declared"


#: FU-7a gap (f): a merged version's configuration lists every input plan it sums.
PLANS = "plans"
#: The configuration fields a summed plan adds up; every other field must be equal to sum.
SUMMED_FIELDS = frozenset({"rows", "shares", "templated_share", PLANS})


def _declared_order(
    shares: Mapping[str, Mapping[str, float]], vocabulary: Mapping[str, Any] | None
) -> dict[str, dict[str, float]]:
    """``shares`` with each cell dimension's values in the vocabulary's declaration order.

    ``largest_remainder`` breaks ties by declaration order, and the plan step allocated with the
    vocabulary's order; a recorded configuration comes back from JSONB with its keys SORTED, so a
    census that allocated in dict order put a tied row in another cell than the plan did (a 40-row
    fixture plan read "2 of 54 cells short" against itself; found with FU-7a gap (f))."""
    if not vocabulary:
        return {d: dict(v) for d, v in shares.items()}
    from .vocabulary import parse_vocabulary

    parsed = parse_vocabulary(json.dumps(vocabulary))
    out: dict[str, dict[str, float]] = {}
    for dimension, values in shares.items():
        if dimension not in CELL_DIMENSIONS:
            out[dimension] = dict(values)
            continue
        declared = [v for v in parsed.values(dimension) if v in values]
        out[dimension] = {
            **{v: values[v] for v in declared},
            **{v: s for v, s in values.items() if v not in declared},
        }
    return out


def _one_plan(
    plan: Mapping[str, Any], vocabulary: Mapping[str, Any] | None
) -> dict[tuple[str, ...], int]:
    shares = _declared_order(plan["shares"], vocabulary)
    return largest_remainder(joint_cells(shares, CELL_DIMENSIONS), int(plan["rows"]))


def planned_cells(configuration: Mapping[str, Any]) -> dict[tuple[str, ...], int]:
    """The plan's row count per (genre, placement, framing, length band) cell, allocated in the
    vocabulary's declaration order as the plan step allocated it. A merged version's
    configuration (:func:`merge_configurations`) plans the SUM of its input plans' cells, each
    allocated as its own plan step allocated it."""
    vocabulary = configuration.get("vocabulary")
    plans = configuration.get(PLANS)
    if not plans:
        return _one_plan(configuration, vocabulary)
    total: Counter[tuple[str, ...]] = Counter()
    for plan in plans:
        total.update(_one_plan(plan, vocabulary))
    return dict(total)


def _plans_of(version_id: str, configuration: Mapping[str, Any]) -> list[dict[str, Any]]:
    nested = configuration.get(PLANS)
    if nested:
        return [dict(p) for p in nested]  # a merge of merges sums the original plans
    return [
        {
            "from_version_id": version_id,
            "rows": int(configuration["rows"]),
            "shares": configuration["shares"],
            "templated_share": float(configuration.get("templated_share", 0.0)),
        }
    ]


def _text(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def merge_configurations(
    found: Sequence[tuple[str, Mapping[str, Any]]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The configuration a build over several version inputs records (FU-7a gap (f)), as
    ``(configuration, refusal)``. Before, the FIRST input's was kept, so a merge of four plans
    (19,891 rows) was censused against one input's 7,800-row plan.

    ``found`` is ``(version_id, configuration)`` per input that recorded one, in input order.
    Identical configurations are one plan (what the inputs agree on, as ``"@input"`` reads it).
    Different ones are summed when they differ only in what a sum adds up (``rows``, ``shares``,
    ``templated_share``): ``rows`` is the total, ``shares`` and ``templated_share`` are
    row-weighted, and ``plans`` lists each input plan so the census plans the sum of their cells.
    Otherwise nothing is recorded and ``refusal`` says why: no census is better than a census
    against one input's plan."""
    distinct: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for version_id, configuration in found:
        distinct.setdefault(_text(configuration), (version_id, configuration))
    if not distinct:
        return None, None
    if len(distinct) == 1:
        return dict(next(iter(distinct.values()))[1]), None
    items = list(distinct.values())
    first_id, first = items[0]
    common = {k: v for k, v in first.items() if k not in SUMMED_FIELDS}
    for version_id, configuration in items[1:]:
        other = {k: v for k, v in configuration.items() if k not in SUMMED_FIELDS}
        differ = sorted(
            k for k in set(common) | set(other) if _text(common.get(k)) != _text(other.get(k))
        )
        if differ:
            return None, {
                "code": "behaviour_configuration_not_merged",
                "message": f"The version inputs record behaviour configurations that cannot be "
                f"summed: {first_id} and {version_id} differ in {', '.join(differ)}. No "
                "configuration and no census are recorded for this version; build from inputs "
                "planned with one vocabulary, or census each input on its own.",
                "details": {"versions": [v for v, _ in items], "fields": differ},
            }
    # one plan reached through two merged inputs is one plan, counted once
    plans = list({_text(p): p for vid, c in items for p in _plans_of(vid, c)}.values())
    rows = sum(int(p["rows"]) for p in plans)
    shares: dict[str, dict[str, float]] = {}
    for plan in plans:
        weight = int(plan["rows"]) / rows if rows else 1.0 / len(plans)
        for dimension, values in plan["shares"].items():
            into = shares.setdefault(dimension, {})
            for value, share in values.items():
                into[value] = into.get(value, 0.0) + float(share) * weight
    templated = sum(
        float(p["templated_share"]) * (int(p["rows"]) / rows if rows else 1.0 / len(plans))
        for p in plans
    )
    return {
        **common,
        "rows": rows,
        "shares": shares,
        "templated_share": templated,
        PLANS: plans,
    }, None


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
    summed = len(configuration.get(PLANS) or ())
    basis = f" (planned: the sum of {summed} input plans)" if summed else ""
    if short:
        flags.append(
            {
                "code": "cell_short",
                "message": f"{len(short)} of {len(cells)} cells hold fewer rows than planned"
                f"{basis} ({sum(c['shortfall'] for c in short):,} rows short); a top-up run can "
                "fill them.",
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
        **({"plans_summed": summed} if summed else {}),
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
