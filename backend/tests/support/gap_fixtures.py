"""Fixtures for the MCP gap fixes (ENH-006 D116): a version with named splits and a calibration set
written row by row.

The fixtures keep the human's answers, the label columns and the row order independent of one another,
and name the training label something other than the run's ``label``: the worst fault of the dry run
passed because every fixture's label column was the run's own.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from src.core.canonical_json import canonical_json
from src.core.database import sync_session_factory
from src.models.calibration import CalibrationSet, CalibrationSetLabel
from src.services.calibration.mapping import question_hash
from tests.support import db_factories

QUESTION = "Is a Social Security number presented here?"
YES_NO = ["yes", "no"]


def row_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def rows_table(rows: Sequence[Mapping[str, Any]], split: str) -> pa.Table:
    names = [n for n in rows[0] if n != "_dw_row_key"] if rows else ["text"]
    columns: dict[str, Any] = {
        "_dw_row_key": [r.get("_dw_row_key", row_key(str(r["text"]))) for r in rows],
        "_dw_occurrence": [0] * len(rows),
        "_dw_origin": ["generated"] * len(rows),
        "_dw_split": [split] * len(rows),
    }
    for name in names:
        columns[name] = [r.get(name) for r in rows]
    return pa.table(columns)


def split_version(
    data_dir: Path,
    splits: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    content: Sequence[str] = ("text",),
    roles: Mapping[str, str] | None = None,
) -> str:
    """A completed version with one Parquet file per split; each row needs ``text``."""
    version_id = db_factories.uid()
    recorded = []
    names: list[str] = []
    for name, rows in splits.items():
        relative = f"versions/{version_id}/{name}.parquet"
        path = data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        table = rows_table(rows, name)
        pq.write_table(table, path)
        names.extend(n for n in table.schema.names if n not in names and not n.startswith("_dw_"))
        recorded.append(
            {
                "name": name,
                "path": relative,
                "rows": len(rows),
                "held_out": False,
                "bytes": path.stat().st_size,
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "logical_digest": hashlib.sha256(name.encode()).hexdigest(),
            }
        )
    column_roles = {n: ("content" if n in content else "metadata") for n in names}
    column_roles.update(roles or {})
    manifest = canonical_json({"format": "gap-fixture", "id": version_id, "sources": []})
    with sync_session_factory()() as db:
        db_factories.version(
            db,
            id=version_id,
            splits=recorded,
            total_rows=sum(int(s["rows"]) for s in recorded),
            column_roles=column_roles,
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        )
        db.commit()
    return version_id


def mark_deleted(version_id: str) -> None:
    """The tombstone a delete writes: the row stays, its files are gone."""
    from sqlalchemy import text

    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_versions SET state = 'deleted', deleted_by = 'Test Operator', "
                "deleted_by_origin = 'operator', deleted_at = now() WHERE id = :v"
            ),
            {"v": version_id},
        )
        db.commit()


def calibration_set(
    version_id: str,
    labels: Mapping[str, str | None],
    *,
    label_set: Sequence[str] = tuple(YES_NO),
) -> str:
    """A set on ``version_id`` holding ``{row key: human label}``, in the given order. A None is a
    row the mapping excluded, as ``dw_calibration_set_labels`` stores it."""
    set_id = f"cs_{db_factories.uid().replace('-', '')[:24]}"
    with sync_session_factory()() as db:
        db.add(
            CalibrationSet(
                id=set_id,
                version_id=version_id,
                question=QUESTION,
                question_hash=question_hash(QUESTION),
                label_set=list(label_set),
                source_kind="imported",
                mapping={"schema": "dw.calibration-mapping/v1"},
                mapping_hash=hashlib.sha256(set_id.encode()).hexdigest(),
                licence_class="private_only",
                counts={"rows": len(labels)},
                provenance={"kind": "imported"},
                created_by="Test Operator",
                created_by_origin="operator",
            )
        )
        db.flush()
        for position, (key, human) in enumerate(labels.items()):
            db.add(
                CalibrationSetLabel(
                    calibration_set_id=set_id, row_key=key, position=position, human_label=human
                )
            )
        db.commit()
    return set_id
