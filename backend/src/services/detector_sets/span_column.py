"""The span column a sent view declares (ENH-006 G39; operator decision 2026-11-09).

miWorkbench reads a view's OPTIONAL ``span_column`` (per row, the evidence's character offsets in the input
text) into the profile its ``recommend_probe_rules`` reads; without one it recommends no pooling rule and
refuses no run. The columns come from the manifest's listing (``extensions.evidence_spans``, written by
``behaviour.evidence_spans``), or a reused publish's stored manifest, exactly as G11's grouping columns do.

For each role this keeps the first listed column that:

- is in the role's own split, and
- indexes the role's ``input_column``: the listed ``text_column`` is the column the rule set read, and an
  offset into one text is meaningless in another.

miWorkbench takes one span column per view, so any other candidate is left out with the reason. miWorkbench
checks every offset against its row when it registers the view and refuses the role by code otherwise.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...models.publish import PublishBuild
from ...models.version import Version
from ..behaviour.evidence_spans import LISTING_KEY, evidence_span_columns
from ..duck import connect, top_level_columns
from ..publishing import publish_service


@dataclass(frozen=True)
class Chosen:
    column: str | None = None
    #: column -> why the role's view does not declare it, in words.
    left_out: dict[str, str] = field(default_factory=dict)


def listed_spans(
    session: Session, version: Version, build_id: str | None, reuse_publish_id: str | None
) -> list[dict[str, Any]]:
    """``extensions.evidence_spans`` of the manifest this send's publish has or will have."""
    if reuse_publish_id is not None:
        manifest = publish_service.published_manifest_for(session, reuse_publish_id) or {}
        return list((manifest.get("extensions") or {}).get(LISTING_KEY) or [])
    build = session.get(PublishBuild, build_id) if build_id is not None else None
    if build is None:
        return []
    named = [str(c["name"]) for c in build.columns or []]
    return evidence_span_columns(session, version, named) or []


def choose(path: Path, listed: Sequence[Mapping[str, Any]], input_column: str) -> Chosen:
    """The span column this split's view should declare, and why any other listed one is left out."""
    if not listed:
        return Chosen()
    con = connect()
    try:
        present = set(top_level_columns(con, [path]))
    finally:
        con.close()
    chosen: str | None = None
    left_out: dict[str, str] = {}
    for entry in listed:
        column = str(entry.get("column") or "")
        text = str(entry.get("text_column") or "")
        if column not in present:
            left_out[column] = "not in this split"
        elif text != input_column:
            left_out[column] = (
                f"its offsets index {text!r}, and this role's input column is {input_column!r}"
            )
        elif chosen is None:
            chosen = column
        else:
            left_out[column] = f"miWorkbench takes one span column per view; {chosen!r} is declared"
    return Chosen(chosen, left_out)
