"""The ``session_data`` caveat and the public-push warning (015 FR-015.13, FR-015.14; ENH-005 D38).

A version whose lineage reaches a source flagged as session data (recorded user traffic: miStudio's
spot-check export, or the operator's mark) carries an AMBER ``session_data`` caveat in every manifest
and card it produces. It comes from LINEAGE (``SourceFacts.source.session_data``), not from columns,
so dropping the provenance columns does not drop it.

**Warn only (D38).** Nothing here refuses: no entry is added to ``checks.evaluate_checks`` and
``push_allowed`` is unchanged. The publish screen and the MCP publish preview show the warning
before a PUBLIC push; the operator may still publish publicly. The caveat says "recorded user
traffic", never "contains personal data": no PII detection is claimed.

Emission ships after miStudio and miForge re-vendored the widened schema (FTDD 015 section 11, step 3;
confirmed pushed 2026-10-21: miStudio ``ca99bf84``, miForge ``273ab75``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..sources import spot_check
from .check_inputs import SourceFacts

CODE = "session_data"
MESSAGE = (
    "Built from recorded user traffic (session data): at least one source is a recording of real "
    "sessions. Read its terms and its content before sharing it."
)
PUBLIC_WARNING = (
    "This version is built from recorded user traffic (session data). A public repository makes "
    "it readable by anyone. You may still publish it publicly; check its content first."
)


def flagged(facts: Sequence[SourceFacts]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": str(f.source.id),
            "name": f.source.display_name,
            "basis": f.source.session_data_basis,
        }
        for f in facts
        if f.source.session_data
    ]


def session_data_caveat(facts: Sequence[SourceFacts]) -> dict[str, Any] | None:
    """The amber caveat, or None when no source in the lineage is session data."""
    sources = flagged(facts)
    if not sources:
        return None
    return {
        "code": CODE,
        "severity": "amber",
        "message": MESSAGE,
        "detail": {"sources": sources},
    }


def session_data_warnings(facts: Sequence[SourceFacts], visibility: str) -> list[dict[str, Any]]:
    """The publish preview's warnings: the session-data warning before a PUBLIC push only."""
    sources = flagged(facts)
    if not sources or visibility != "public":
        return []
    return [{"code": CODE, "severity": "amber", "message": PUBLIC_WARNING, "sources": sources}]


def with_session_semantics(
    columns: Sequence[Mapping[str, Any]], facts: Sequence[SourceFacts]
) -> list[dict[str, Any]]:
    """FR-015.14: in a session-data version the session id column reads ``group`` (so grouping by
    session is available downstream); every other column is unchanged."""
    out = [dict(c) for c in columns]
    if not flagged(facts):
        return out
    for c in out:
        if c.get("name") == spot_check.SESSION_COLUMN and c.get("semantic") == "other":
            c["semantic"] = "group"
    return out


def with_session_caveat(
    caveats: Sequence[Mapping[str, Any]], facts: Sequence[SourceFacts]
) -> list[dict[str, Any]]:
    """``caveats`` plus the session-data caveat when the lineage has one (the export manifests)."""
    out = [dict(c) for c in caveats]
    caveat = session_data_caveat(facts)
    if caveat is not None:
        out.append(caveat)
    return out
