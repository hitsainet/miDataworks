"""A version's lineage, synchronously: the version, then every ancestor version reachable through
its version inputs (002's ``dw_version_inputs``), nearest first, each reached once.

One walker for every sync reader that must account for what the rows went through, not only for
this version's own steps: the shortcut audit's pair-construction exclusion (004,
``label_columns.pair_construction``) and the publish record with checks C-4 and C-6 (008). A
re-split, a merge or a filter is a version whose own steps are only that last step; the rows it
carries were generated, labelled and judged in its ancestors, and the runs that did so are BOUND
on those ancestors. Reading only the version's own steps and bindings is how a synthetic,
model-judged dataset came to be described as having neither (2026-10-09).

``lineage_service.lineage_executions`` is the async counterpart for the lineage and row-history
routes.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models.version import Version, VersionInput

#: The walk stops after this many versions (a guard, not a limit any real lineage reaches).
MAX_LINEAGE = 512


def lineage_versions(session: Session, version: Version | str) -> list[Version]:
    """The version first, then its ancestors breadth-first in input order, each once."""
    start = session.get(Version, version) if isinstance(version, str) else version
    if start is None:
        return []
    out: list[Version] = []
    queue: list[Version] = [start]
    seen: set[str] = set()
    while queue and len(seen) < MAX_LINEAGE:
        current = queue.pop(0)
        if str(current.id) in seen:
            continue
        seen.add(str(current.id))
        out.append(current)
        parents = session.execute(
            select(VersionInput.input_version_id)
            .where(VersionInput.version_id == current.id, VersionInput.kind == "version")
            .order_by(VersionInput.position)
        ).scalars()
        for parent_id in list(parents):
            if parent_id is None or str(parent_id) in seen:
                continue
            parent = session.get(Version, parent_id)
            if parent is not None:
                queue.append(parent)
    return out


@dataclass(frozen=True)
class LineageBindings:
    """Run IDs bound anywhere in the lineage, in first-seen order, each once."""

    label_runs: list[str]
    generation_runs: list[str]


def lineage_bindings(versions: Sequence[Version]) -> LineageBindings:
    label_runs: list[str] = []
    generation_runs: list[str] = []
    for v in versions:
        for b in v.bindings or []:
            run_id = str(b.get("id"))
            if b.get("kind") == "label_run" and run_id not in label_runs:
                label_runs.append(run_id)
            elif b.get("kind") == "generation_run" and run_id not in generation_runs:
                generation_runs.append(run_id)
    return LineageBindings(label_runs, generation_runs)
