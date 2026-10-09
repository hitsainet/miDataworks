"""Per-batch conservation and silent-change detection (FR-003.9; FTID 003 section 7).

Counted over ``(row key, occurrence)`` pairs, never bare keys, because duplicate keys are allowed
(FR-002.21, FR-002.47). For one batch:

- every input pair is exactly one of: kept unchanged in the output, the ``from`` of a ``changed``
  event, or ``dropped``;
- every output pair is exactly one of: a kept input pair, the ``to`` of a ``changed`` event, or
  ``added`` (generators' rows are counted separately);
- an output row under a kept input pair whose CONTENT differs from its input (feature 002's row
  key over the content columns) without a ``changed`` event is a silent change; so is a
  ``_dw_split`` change without a ``split_assigned`` event.

A violation raises ``conservation_violated`` naming up to 20 pairs per problem; the executor then
publishes nothing for the step. Feature 002 re-checks the whole step at ingestion (FR-002.25);
this is the per-batch form, so a violation is caught where it happened.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pyarrow as pa

from ..services.row_keys import compute_row_keys
from .errors import StepFailed
from .protocol import RowEvent

Pair = tuple[str, int]
SAMPLE = 20


@dataclass(frozen=True)
class InputRow:
    """What conservation needs to know about one input row."""

    content_key: str | None
    split: str | None = None


@dataclass
class Accounting:
    rows_in: int = 0
    rows_kept: int = 0
    rows_changed: int = 0
    rows_dropped: int = 0
    rows_added: int = 0
    rows_split_assigned: int = 0
    problems: dict[str, list[Pair]] = field(default_factory=dict)

    def add(self, other: Accounting) -> None:
        self.rows_in += other.rows_in
        self.rows_kept += other.rows_kept
        self.rows_changed += other.rows_changed
        self.rows_dropped += other.rows_dropped
        self.rows_added += other.rows_added
        self.rows_split_assigned += other.rows_split_assigned


def pairs_of(table: pa.Table | pa.RecordBatch) -> list[Pair]:
    if table.num_rows == 0:
        return []
    keys = table.column("_dw_row_key").to_pylist()
    occurrences = table.column("_dw_occurrence").to_pylist()
    return [(str(k), int(o)) for k, o in zip(keys, occurrences, strict=True)]


def input_rows(
    table: pa.Table | pa.RecordBatch, content_columns: Sequence[str], scheme: str
) -> dict[Pair, InputRow]:
    """The identity and content fingerprint of every input row."""
    pairs = pairs_of(table)
    keys: list[str | None]
    if content_columns and table.num_rows:
        keys = list(compute_row_keys(table, list(content_columns), scheme))
    else:
        keys = [None] * len(pairs)
    splits = (
        table.column("_dw_split").to_pylist()
        if "_dw_split" in table.schema.names
        else [None] * len(pairs)
    )
    return {
        pair: InputRow(key, split) for pair, key, split in zip(pairs, keys, splits, strict=True)
    }


def check(
    inputs: Mapping[Pair, InputRow],
    output: pa.Table,
    events: Sequence[RowEvent],
    content_columns: Sequence[str],
    scheme: str,
    ref: str,
    *,
    detect_silent_change: bool = True,
) -> Accounting:
    """Refuse a result that loses, duplicates or silently changes a row. Returns the counts."""
    dropped = [(e.row_key, e.occurrence) for e in events if e.kind == "dropped"]
    changed = [e for e in events if e.kind == "changed"]
    changed_from = [(e.row_key, e.occurrence) for e in changed]
    changed_to = {(str(e.new_row_key), int(e.new_occurrence or 0)) for e in changed}
    added = {(e.row_key, e.occurrence) for e in events if e.kind == "added"}
    split_events = {
        (e.row_key, e.occurrence): e.split for e in events if e.kind == "split_assigned"
    }

    out_pairs = pairs_of(output)
    out_count = Counter(out_pairs)
    problems: dict[str, list[Pair]] = {}

    def note(kind: str, pairs: list[Pair]) -> None:
        if pairs:
            problems[kind] = sorted(pairs)[:SAMPLE]

    note("duplicated_in_output", [p for p, n in out_count.items() if n > 1])
    from_count = Counter(dropped + changed_from)
    note("accounted_twice", [p for p, n in from_count.items() if n > 1])
    note("event_for_unknown_row", [p for p in from_count if p not in inputs])
    out_set = set(out_count)
    from_set = set(from_count)
    kept = [p for p in inputs if p not in from_set and p in out_set]
    note("lost", [p for p in inputs if p not in from_set and p not in out_set])
    note("dropped_and_kept", [p for p in dropped if p in out_set and p not in changed_to])
    note(
        "unaccounted_output",
        [p for p in out_set if p not in inputs and p not in changed_to and p not in added],
    )
    note("changed_row_missing", [p for p in changed_to if p not in out_set])
    note("added_row_missing", [p for p in added if p not in out_set])

    if detect_silent_change and kept:
        kept_set = set(kept)
        positions = [i for i, p in enumerate(out_pairs) if p in kept_set]
        subset = output.take(positions)
        sub_pairs = [out_pairs[i] for i in positions]
        if content_columns:
            new_keys = compute_row_keys(subset, list(content_columns), scheme)
            note(
                "changed_without_event",
                [p for p, k in zip(sub_pairs, new_keys, strict=True) if inputs[p].content_key != k],
            )
        if "_dw_split" in subset.schema.names:
            splits = subset.column("_dw_split").to_pylist()
            note(
                "split_changed_without_event",
                [
                    p
                    for p, s in zip(sub_pairs, splits, strict=True)
                    if s != inputs[p].split and split_events.get(p) != s
                ],
            )

    if problems:
        summary = ", ".join(f"{len(v)} {k.replace('_', ' ')}" for k, v in problems.items())
        raise StepFailed(
            "conservation_violated",
            f"{ref} did not account for every row exactly once ({summary}). Every input row "
            "must be kept, changed with an event or dropped with an event (FR-003.9). Nothing "
            "from this step was published.",
            {k: [{"row_key": p[0], "occurrence": p[1]} for p in v] for k, v in problems.items()},
        )
    return Accounting(
        rows_in=len(inputs),
        rows_kept=len(kept),
        rows_changed=len(changed),
        rows_dropped=len(dropped),
        rows_added=len(added),
        rows_split_assigned=len(split_events),
    )
