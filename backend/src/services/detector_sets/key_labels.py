"""Rows, distinct row keys, and keys whose copies carry conflicting labels (2026-10-08 finding 2).

002's row key is a hash of the row's content (ADR-005; ``dw.rowkey/v1``), and T-07 decides that
identical content is one key whose labels and decisions apply to EVERY copy, with a warning when the
copies' metadata disagree (FR-002.21, FR-002.46). The key is therefore not changed: two rows reading
``"nan"`` are the same input, and a probe scores the same input the same way.

What the live check found is that the three readers of one split counted differently: the label
run counted KEYS (537), while the reproduction link and the gate counted ROWS (540), and nothing
said that four of those rows were one input labelled twice ``high-stakes`` and twice ``low-stakes``.

This module is the one count all three report:

- ``rows``: rows that map to a class (what miStudio counted when it evaluated the split);
- ``row_keys``: distinct inputs among them (what is scored: each key ONCE, its score applied to
  every copy, as T-07 says a label is);
- ``conflicting``: keys whose copies map to BOTH classes, named with their counts. They are kept,
  each copy with its own label, because miStudio's figure was computed over every copy — dropping
  them would compare a different set with miStudio's interval. They are never resolved to one
  label; every surface (the link's checks, the gate's record, the screens) shows them.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

#: How many conflicting keys a record names (the count is always exact).
NAMED_LIMIT = 20


def summary(keyed: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """``(row_key, class)`` per row, in any order → the counts and the conflicting keys."""
    classes: dict[str, Counter[str]] = defaultdict(Counter)
    rows = 0
    for key, cls in keyed:
        classes[str(key)][str(cls)] += 1
        rows += 1
    copied = {k: c for k, c in classes.items() if sum(c.values()) > 1}
    conflicting = sorted(
        (k for k, c in copied.items() if c["positive"] and c["negative"]),
    )
    return {
        "rows": rows,
        "row_keys": len(classes),
        "keys_with_copies": len(copied),
        "conflicting": {
            "count": len(conflicting),
            "rows": sum(sum(classes[k].values()) for k in conflicting),
            "keys": [
                {
                    "row_key": k,
                    "positive": classes[k]["positive"],
                    "negative": classes[k]["negative"],
                }
                for k in conflicting[:NAMED_LIMIT]
            ],
        },
    }


def conflict_sentence(summary_: dict[str, Any]) -> str | None:
    """One sentence for a message, or None when no key's copies disagree."""
    conflicting = summary_["conflicting"]
    if not conflicting["count"]:
        return None
    first = conflicting["keys"][0]
    return (
        f"{conflicting['count']} input(s) appear in several rows labelled BOTH positive and "
        f"negative ({conflicting['rows']} rows; e.g. key {first['row_key'][:12]}: "
        f"{first['positive']} positive, {first['negative']} negative). Each copy keeps its own "
        "label, as miStudio counted it; check whether those rows are really the same input."
    )
