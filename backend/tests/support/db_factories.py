"""Row factories for database-level tests: real inserts through the production session factory.

Every factory builds rows the way production would describe them (a manifest whose hash really
is its sha256, a recipe body whose hash really is its canonical bytes' sha256), so a guard test
fails on the guard, not on a fixture that agreed with nothing.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy.orm import Session

from src.core.canonical_json import canonical_json
from src.core.ids import new_id
from src.models import (
    Dataset,
    Job,
    Recipe,
    RecipeBody,
    RecipeRevision,
    Source,
    SourceFile,
    Version,
)

BODY: dict[str, Any] = {
    "format": "dw.recipe/v1",
    "steps": [{"operator": "stub_keep", "version": "1", "params": {}}],
}


def uid() -> str:
    return str(uuid.uuid4())


def job(db: Session, kind: str = "selftest", status: str = "completed") -> Job:
    row = Job(
        id=new_id("job"),
        kind=kind,
        status=status,
        progress=0.0,
        params={},
        started_by="Test Operator",
        started_by_origin="operator",
    )
    db.add(row)
    db.flush()
    return row


def dataset(db: Session, name: str | None = None, target_type: str = "detector") -> Dataset:
    row = Dataset(
        id=uid(),
        name=name or f"ds-{uuid.uuid4().hex[:8]}",
        target_type=target_type,
        next_version_number=1,
        created_by="Test Operator",
        created_by_origin="operator",
    )
    db.add(row)
    db.flush()
    return row


def recipe(db: Session, body: dict[str, Any] | None = None) -> tuple[Recipe, RecipeRevision]:
    body = body or BODY
    canonical = canonical_json(body)
    digest = hashlib.sha256(canonical).hexdigest()
    if db.get(RecipeBody, digest) is None:
        db.add(RecipeBody(hash=digest, canonical=canonical, body=body))
        db.flush()
    rec = Recipe(
        id=uid(),
        name=f"recipe-{uuid.uuid4().hex[:8]}",
        archived=False,
        created_by="Test Operator",
        created_by_origin="operator",
    )
    db.add(rec)
    db.flush()
    rev = RecipeRevision(
        id=uid(),
        recipe_id=rec.id,
        revision_number=1,
        recipe_hash=digest,
        step_labels=[],
        imported=False,
        created_by="Test Operator",
        created_by_origin="operator",
    )
    db.add(rev)
    db.flush()
    rec.head_revision_id = rev.id
    db.flush()
    return rec, rev


def version(db: Session, ds: Dataset | None = None, **overrides: Any) -> Version:
    ds = ds or dataset(db)
    _, rev = recipe(db)
    build_job = job(db, kind="selftest")
    manifest = canonical_json({"format": "test", "n": uuid.uuid4().hex})
    fields: dict[str, Any] = {
        "id": uid(),
        "dataset_id": ds.id,
        "number": ds.next_version_number,
        "state": "completed",
        "request_digest": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        "inputs": [],
        "recipe_hash": rev.recipe_hash,
        "recipe_revision_id": rev.id,
        "seed": 7,
        "bindings": [],
        "rowkey_scheme": "dw.rowkey/v1",
        "column_roles": {"text": "content"},
        "splits": [],
        "total_rows": 0,
        "total_bytes": 0,
        "warnings": [],
        "drop_summary": [],
        "manifest": manifest,
        "manifest_sha256": hashlib.sha256(manifest).hexdigest(),
        "build_job_id": build_job.id,
        "created_by": "Test Operator",
        "created_by_origin": "operator",
    }
    fields.update(overrides)
    row = Version(**fields)
    ds.next_version_number += 1
    db.add(row)
    db.flush()
    return row


def source(
    db: Session,
    *,
    state: str = "ready",
    kind: str = "hf",
    repo_id: str = "org/data",
    commit: str = "2bb7d6bce15e42c2a3cf2be8305fa3049929d3ac",
    content_hash: str | None = None,
    detection: dict[str, Any] | None = None,
) -> Source:
    row = Source(
        id=uid(),
        kind=kind,
        state=state,
        display_name=repo_id if kind == "hf" else "upload.parquet",
        repo_id=repo_id if kind == "hf" else None,
        resolved_commit=commit if kind == "hf" else None,
        content_hash=content_hash if kind == "upload" else None,
        licence_raw="cc-by-2.0",
        licence_display="cc-by-2.0",
        licence_origin="card_data",
        detection=detection,
        library_versions={"datasets": "x", "huggingface_hub": "y", "pyarrow": "z"},
        created_by="Test Operator",
        created_by_origin="operator",
    )
    db.add(row)
    db.flush()
    return row


def source_file(
    db: Session, src: Source, split: str, path: str, sha256: str, rows: int, size: int
) -> SourceFile:
    row = SourceFile(
        id=uid(),
        source_id=src.id,
        split=split,
        path=path,
        rows=rows,
        bytes=size,
        sha256=sha256,
        columns=[{"name": "text", "type": "string"}],
    )
    db.add(row)
    db.flush()
    return row


def version_reading(db: Session, src: Source) -> Version:
    """A version whose input 0 is ``src`` (so ``source_in_use`` refuses its delete)."""
    from src.models.version import VersionInput

    row = version(db)
    db.add(VersionInput(version_id=row.id, position=0, kind="source", source_id=src.id))
    db.flush()
    return row
