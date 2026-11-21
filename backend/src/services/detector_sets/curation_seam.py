"""D-3 and D-4 through feature 004's ``evaluate_warnings`` and ``check_leakage`` (FTID 009
section 7.1; 004 controls record section 4, "009").

The interface is 004's own:

- ``evaluate_warnings([(version_id, split, role), ...], label_column=..., session=...)`` audits the
  union with ``role`` as an extra audited column; returns ``warnings`` and ``invalid`` (an audit with
  too few rows reports ``invalid=["(audit insufficient_rows)"]``, never a silent pass);
- ``check_leakage([(version_id, split, role), ...], group_column=..., session=...)`` treats each
  role as a side and keys pairs ``"<roleA>|<roleB>"`` (sorted side names).

A send refuses on any warning, any invalid audit and any pair crossing two roles (P-01). 009 passes
ROLE IDS as the leakage sides (unique, never containing ``|``), so two out-of-distribution roles are
two sides and a pair key always splits into exactly two role IDs.

004 landed on ``main`` at ``4c28954`` (2026-10-07); until then this module was a seam that refused
"curation not installed". It now calls 004 directly, so removing either call turns the end-to-end
D-3/D-4 tests red (``tests/integration/detector_sets/test_checks_with_curation.py``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..curation import api as curation
from .checks import LeakageFacts, WarningFacts


def _dump(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    as_dict = getattr(value, "as_dict", None)
    return as_dict() if callable(as_dict) else value


def evaluate_warnings(
    inputs: Sequence[tuple[str, str, str]],
    *,
    label_column: str | None,
    session: Any,
    label_sources: Mapping[str, str] | None = None,
) -> WarningFacts:
    """``label_sources``: the columns the set's roles declare the label was computed from
    (``label_source_columns``), each -> who declared it. 004 excludes them from the audit and
    reports them, with the labeler-derived columns it excludes on its own, in ``excluded``."""
    result = curation.evaluate_warnings(
        list(inputs),
        label_column=label_column,
        label_sources=dict(label_sources) if label_sources else None,
        session=session,
    )
    return WarningFacts(
        warnings=[dict(_dump(w)) for w in result.warnings],
        invalid=[str(c) for c in result.invalid],
        excluded=[dict(e) for e in getattr(result, "excluded", [])],
    )


def crossing_pairs(pairs: dict[str, int]) -> dict[str, int]:
    """Pairs whose two sides are different roles; a role's pairs with itself are not leakage."""
    out: dict[str, int] = {}
    for key, n in pairs.items():
        sides = str(key).split("|")
        if len(sides) == 2 and sides[0] != sides[1] and int(n) > 0:
            out[str(key)] = out.get(str(key), 0) + int(n)
    return out


def check_leakage(
    inputs: Sequence[tuple[str, str, str]],
    *,
    group_column: str | None = None,
    group_columns: dict[str, str | None] | None = None,
    session: Any,
) -> LeakageFacts:
    """``group_columns`` (role -> its pair column or None) groups each role by its OWN column."""
    result = curation.check_leakage(
        list(inputs), group_column=group_column, group_columns=group_columns, session=session
    )
    merged: dict[str, int] = {}
    for pairs in (result.exact_pairs, result.near_pairs, result.group_pairs):
        for key, n in crossing_pairs(dict(pairs)).items():
            merged[key] = merged.get(key, 0) + n
    return LeakageFacts(crossing=merged)
