"""The grouping columns a sent view declares (ENH-006 D116 G11).

A breakdown groups an evaluation's rows by a column miStudio snapshotted when the column was declared on
the view. Views from a send arrived with none, though the manifest already lists the columns meant for it
(``extensions.breakdown_columns``), so the first probe on the dry run could not be read by ``kind``,
``framing``, ``form`` or ``genre`` until each view was updated by hand.

The names come from that listing and nowhere else: ``behaviour.breakdown.breakdown_columns``, the function
that writes it, or a reused publish's stored manifest. From them this leaves out what miStudio would refuse
or could not use, read from the role's own split:

- more distinct values than ``group_column_max_values`` (miStudio ``services/view_columns.encode``, 422
  ``too_many_values``: "a grouping column groups rows, it is not a row id"; a null is a value there too);
- a column in which every row holds its own value: a row id under that limit (``carrier_id`` on a small
  view) passes miStudio and groups nothing;
- past ``view_max_group_columns`` (``over_cap``), keeping the plan's columns first and columns holding more
  than one value before those holding one.

The two limits are miStudio's runtime settings; these are its defaults, pinned against its code by
``tests/contract/test_mistudio_probe_dataset_contract.py``. An operator who lowers one there sees that
role's registration refused by name, and a send that resumes after it is raised.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ...models.publish import PublishBuild
from ...models.version import Version
from ..behaviour.breakdown import FIXED, breakdown_columns
from ..duck import connect, files_param, quote_ident, top_level_columns
from ..publishing import publish_service

#: miStudio ``core/config.py``: ``group_column_max_values``, ``view_max_group_columns``.
GROUP_COLUMN_MAX_VALUES = 10_000
VIEW_MAX_GROUP_COLUMNS = 32


@dataclass(frozen=True)
class Chosen:
    columns: tuple[str, ...] = ()
    #: column -> why it was left out, in words.
    left_out: dict[str, str] = field(default_factory=dict)


def listed_columns(
    session: Session, version: Version, build_id: str | None, reuse_publish_id: str | None
) -> list[str]:
    """``extensions.breakdown_columns`` of the manifest this send's publish has or will have."""
    if reuse_publish_id is not None:
        manifest = publish_service.published_manifest_for(session, reuse_publish_id) or {}
        return list((manifest.get("extensions") or {}).get("breakdown_columns") or [])
    build = session.get(PublishBuild, build_id) if build_id is not None else None
    if build is None:
        return []
    named = [str(c["name"]) for c in build.columns or []]
    return breakdown_columns(session, version, named) or []


def _distinct(path: Path, columns: Sequence[str]) -> tuple[int, list[int]]:
    """The split's rows, and each column's distinct values, a null counted as one as miStudio does."""
    con = connect()
    try:
        terms = []
        for column in columns:
            quoted = quote_ident(column, columns)
            terms.append(
                f"count(DISTINCT {quoted}) + CASE WHEN count({quoted}) < count(*) THEN 1 ELSE 0 END"
            )
        row: Any = con.execute(
            f"SELECT count(*), {', '.join(terms)} FROM read_parquet(?)",  # noqa: S608 - quoted
            [files_param([path])],
        ).fetchone()
    finally:
        con.close()
    return int(row[0]), [int(v) for v in row[1:]]


def choose(path: Path, listed: Sequence[str]) -> Chosen:
    """The columns of ``listed`` this split's view should declare, and why any other is left out."""
    if not listed:
        return Chosen()
    con = connect()
    try:
        present = set(top_level_columns(con, [path]))
    finally:
        con.close()
    left_out = {c: "not in this split" for c in listed if c not in present}
    candidates = list(dict.fromkeys(c for c in listed if c in present))
    if not candidates:
        return Chosen(left_out=left_out)
    rows, counts = _distinct(path, candidates)
    kept: list[tuple[int, int, str]] = []
    for position, (column, values) in enumerate(zip(candidates, counts, strict=True)):
        if values > GROUP_COLUMN_MAX_VALUES:
            left_out[column] = (
                f"{values:,} distinct values, over miStudio's limit of {GROUP_COLUMN_MAX_VALUES:,}"
            )
        elif rows > 1 and values == rows:
            left_out[column] = "every row holds its own value, so it groups nothing"
        else:
            rank = FIXED.index(column) if column in FIXED else len(FIXED) + position
            kept.append((int(values <= 1), rank, column))
    ordered = [c for _, _, c in sorted(kept)]
    for column in ordered[VIEW_MAX_GROUP_COLUMNS:]:
        left_out[column] = f"past the {VIEW_MAX_GROUP_COLUMNS} columns a view may declare"
    return Chosen(tuple(ordered[:VIEW_MAX_GROUP_COLUMNS]), left_out)
