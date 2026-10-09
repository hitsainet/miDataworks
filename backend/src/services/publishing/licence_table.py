"""The licence table and source classification (FR-008.58, FR-008.59; FTID 008 section 3.3).

008 owns the table (X-11, P-14); v1's contents were accepted as proposed (S3-07, 2026-10-06).
The v1 file is FROZEN: ``test_licence_table.py`` pins its SHA-256, so a change is a new file
(``licence-table-v2.json``) and every check snapshot and manifest records which version decided.

Classification order:
1. the source's latest operator terms/licence annotation (001 FR-001.28), whose structured
   ``redistribution`` value wins;
2. else the table: case-insensitive on the Hub identifier, and a list-valued licence permits only
   when EVERY element is in the table;
3. else ``private_only`` — which covers "not stated", ``unknown``, ``other`` and anything absent.

Nothing here defaults to "permits": the only way to permits is a table hit or an annotation.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parent / "data"
CURRENT_TABLE_FILE = DATA / "licence-table-v1.json"


class LicenceClass(StrEnum):
    PERMITS = "permits_redistribution"
    PRIVATE_ONLY = "private_only"
    FORBIDS = "forbids_redistribution"


#: 001's annotation vocabulary (``Redistribution``) mapped onto 008's classes.
ANNOTATION_CLASS: dict[str, LicenceClass] = {
    "permits": LicenceClass.PERMITS,
    "private_only": LicenceClass.PRIVATE_ONLY,
    "forbids": LicenceClass.FORBIDS,
}


@dataclass(frozen=True)
class LicenceTable:
    version: int
    permits: frozenset[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": "dw.licence-table",
            "version": self.version,
            "permits": sorted(self.permits),
        }


def load_table(path: Path = CURRENT_TABLE_FILE) -> LicenceTable:
    data = json.loads(path.read_bytes())
    if data.get("table") != "dw.licence-table":
        raise ValueError(f"{path} is not a dw.licence-table file")
    return LicenceTable(
        version=int(data["version"]),
        permits=frozenset(str(i).lower() for i in data["permits_redistribution"]),
    )


TABLE = load_table()


@dataclass(frozen=True)
class AnnotationRef:
    """The latest licence/terms annotation's structured value (001's row, read by the caller)."""

    redistribution: str
    annotation_id: str


@dataclass(frozen=True)
class Classification:
    licence_class: LicenceClass
    decided_by: str  # "annotation" | "table" | "default"
    table_version: int
    annotation_id: str | None


def classify(
    raw: Any, annotations: Sequence[AnnotationRef], table: LicenceTable = TABLE
) -> Classification:
    """Class one source. ``annotations`` are oldest first; the last one wins."""
    if annotations:
        latest = annotations[-1]
        try:
            cls = ANNOTATION_CLASS[latest.redistribution]
        except KeyError:
            raise ValueError(
                f"annotation {latest.annotation_id} has unknown redistribution "
                f"{latest.redistribution!r}"
            ) from None
        return Classification(cls, "annotation", table.version, latest.annotation_id)
    ids: list[str]
    if isinstance(raw, str):
        ids = [raw]
    elif isinstance(raw, list):
        ids = [str(i) for i in raw]
    else:
        ids = []
    ids = [i.strip().lower() for i in ids]
    if ids and all(i and i in table.permits for i in ids):
        return Classification(LicenceClass.PERMITS, "table", table.version, None)
    return Classification(LicenceClass.PRIVATE_ONLY, "default", table.version, None)
