"""Kind effects (FR-003.5, FR-003.27; FTID 003 section 7).

Each kind may have only the effects in :data:`ALLOWED`. The checker computes the effect set a
batch's result actually has and refuses anything outside the kind's set with
``kind_effect_violated``: a filter that changes a row, a mapper that drops one, a report that
touches rows at all.

Effects:
- ``drop``: a ``dropped`` event;
- ``change``: a ``changed`` event;
- ``add_rows``: an ``added`` event or rows in ``OperatorResult.added``;
- ``add_columns``: an output column the input did not have (system ``_dw_`` columns excluded);
- ``assign_split``: a ``split_assigned`` event (004's split operator; ``split_roles`` then go to
  ``meta.json``).
"""

from __future__ import annotations

import pyarrow as pa

from .errors import StepFailed
from .protocol import OperatorResult

ALLOWED: dict[str, frozenset[str]] = {
    "filter": frozenset({"drop"}),
    "deduplicator": frozenset({"drop"}),
    "selector": frozenset({"drop", "assign_split"}),
    # Deviation from FTID section 7 ({change}): a mapper that adds a derived column changes every
    # row it touches; 002's own stub mapper (stub_meta_tag) does exactly that. Recorded in the
    # implementation controls record.
    # Feature 004 (FR-004.9): the normaliser is a mapper that must DROP a row it cannot map
    # (``unmappable``) rather than pass it through unmapped. Requested of 003 by 004 and recorded in
    # 0xcc/reviews/004_implementation_controls_2026-10-07.md.
    "mapper": frozenset({"change", "add_columns", "drop"}),
    "labeler": frozenset({"add_columns", "drop"}),
    "generator": frozenset({"add_rows"}),
    "report": frozenset(),
    "exporter": frozenset(),
}

_EVENT_EFFECT = {
    "dropped": "drop",
    "changed": "change",
    "added": "add_rows",
    "split_assigned": "assign_split",
}


def effects_of(input_schema: pa.Schema, result: OperatorResult) -> set[str]:
    found = {_EVENT_EFFECT[e.kind] for e in result.events}
    if result.added is not None and result.added.num_rows:
        found.add("add_rows")
    before = {n for n in input_schema.names if not n.startswith("_dw_")}
    after = {n for n in result.output.schema.names if not n.startswith("_dw_")}
    if after - before:
        found.add("add_columns")
    return found


def check(kind: str, input_schema: pa.Schema, result: OperatorResult, ref: str) -> set[str]:
    """Refuse a result outside ``kind``'s permitted effects. Returns the effects found."""
    found = effects_of(input_schema, result)
    extra = found - ALLOWED[kind]
    if extra:
        raise StepFailed(
            "kind_effect_violated",
            f"{ref} is a {kind}, which may only {sorted(ALLOWED[kind]) or 'leave rows alone'}, "
            f"but its result also did {sorted(extra)} (FR-003.5).",
            {"kind": kind, "allowed": sorted(ALLOWED[kind]), "found": sorted(found)},
        )
    return found
