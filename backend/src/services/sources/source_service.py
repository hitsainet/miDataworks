"""Sources: identity, state transitions, annotations, delete, licence view, rows (FR-001.4–001.6,
001.20, 001.28–001.31, 001.36, 001.38; 001 FTID section 3.9).

What it guarantees:
- One live source per identity: an HF identity is (repository, config, split selection, commit), an
  upload's is its content hash. The insert runs under the partial unique index; a conflict returns the
  existing ``importing`` or ``ready`` row, so a repeat import writes nothing (FR-001.4).
- A source becomes ``ready`` only after its files are renamed into place and their rows inserted, in
  one transaction (FR-001.31); the database guard then freezes it.
- Delete unlinks the files FIRST and commits the tombstone after, so a failed unlink leaves a
  ``ready``, retryable source rather than a tombstone pointing at files nobody can reach (miStudio
  Feature 21). A source a version read is refused (``source_in_use``) and so is one still importing.
- An agent-origin annotation needs an approval reference; without one the service refuses, behind
  the route's ``source_annotate`` gate (FR-001.38; S3-01).
"""

from __future__ import annotations

import logging
import shutil
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from ...core.agent_origin import Who
from ...core.clock import utc_now
from ...core.errors import AppError, ConflictError, NotFoundError
from ...core.storage import resolve_under_data_dir, source_dir
from ...models.approval import Approval
from ...models.source import Source, SourceAnnotation, SourceFile
from ...models.source_enums import (
    LICENCE_NOT_STATED,
    AnnotationKind,
    Redistribution,
    SourceKind,
    SourceState,
)
from ...models.version import Version, VersionInput
from ..duck import connect, files_param

logger = logging.getLogger(__name__)

PUSH_WARNING = "An annotation can unlock a public push."


def _uuid(value: str, code: str = "source_not_found") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise NotFoundError(f"{value!r} is not a valid source id.", code=code) from None


# --------------------------------------------------------------------------------------------
# Worker side (sync)
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Created:
    source: Source
    existing: bool


def _live_identity(session: Session, **identity: Any) -> Source | None:
    query = select(Source).where(Source.state.in_((SourceState.IMPORTING, SourceState.READY)))
    for column, value in identity.items():
        attr = getattr(Source, column)
        query = query.where(attr.is_(None) if value is None else attr == value)
    return session.execute(query.execution_options(populate_existing=True)).scalars().first()


def create_importing(session: Session, fields: dict[str, Any], identity: dict[str, Any]) -> Created:
    """Insert an ``importing`` source, or return the live one with the same identity."""
    found = _live_identity(session, **identity)
    if found is not None:
        return Created(found, True)
    row = Source(id=str(uuid.uuid4()), state=SourceState.IMPORTING, **fields)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        found = _live_identity(session, **identity)
        if found is None:
            raise
        return Created(found, True)
    return Created(row, False)


def commit_ready(
    session: Session, source: Source, files: list[dict[str, Any]], detection: dict[str, Any] | None
) -> Source:
    """Insert the file rows and set ``ready`` in one transaction (after the rename)."""
    for f in files:
        session.add(SourceFile(id=str(uuid.uuid4()), source_id=source.id, **f))
    source.detection = detection
    source.state = SourceState.READY
    source.ready_at = utc_now()
    session.commit()
    return source


def end_import(
    session: Session, source_id: str, state: SourceState, error: dict[str, Any] | None
) -> bool:
    """Move an ``importing`` source to ``failed`` or ``cancelled``. False when it was not
    importing (a ready source is never touched)."""
    row = session.get(Source, source_id, populate_existing=True)
    if row is None or row.state != SourceState.IMPORTING:
        return False
    row.state = state
    row.error = error
    session.commit()
    return True


# --------------------------------------------------------------------------------------------
# API side (async)
# --------------------------------------------------------------------------------------------


async def get_source_row(db: AsyncSession, source_id: str) -> Source:
    row = await db.get(Source, _uuid(source_id), populate_existing=True)
    if row is None:
        raise NotFoundError(f"No source {source_id}.", code="source_not_found")
    return row


async def find_live_hf(
    db: AsyncSession, repo_id: str, config: str | None, split: str | None, commit: str
) -> Source | None:
    """The ``ready`` or ``importing`` source with exactly this HF identity (FTDD TQ12).

    A request with no config matches only a source recorded with no config: whether the
    repository has one config at that commit is known only to a worker, so the API does not guess
    (the job resolves it and ends ``completed`` with the existing source, writing nothing).
    """
    query = select(Source).where(
        Source.kind == SourceKind.HF,
        Source.state.in_((SourceState.READY, SourceState.IMPORTING)),
        Source.repo_id == repo_id,
        Source.resolved_commit == commit,
        Source.config.is_(None) if config is None else Source.config == config,
        Source.split_selection.is_(None) if split is None else Source.split_selection == split,
    )
    return (await db.execute(query)).scalars().first()


async def find_by_content_hash(db: AsyncSession, content_hash: str) -> Source | None:
    query = select(Source).where(
        Source.kind == SourceKind.UPLOAD,
        Source.content_hash == content_hash,
        Source.state.in_((SourceState.IMPORTING, SourceState.READY)),
    )
    return (await db.execute(query)).scalars().first()


async def files_of(db: AsyncSession, source_id: str) -> list[SourceFile]:
    rows = await db.execute(
        select(SourceFile).where(SourceFile.source_id == source_id).order_by(SourceFile.split)
    )
    return list(rows.scalars())


async def annotations_of(
    db: AsyncSession, source_id: str, kind: str | None = None
) -> list[SourceAnnotation]:
    query = select(SourceAnnotation).where(SourceAnnotation.source_id == source_id)
    if kind:
        query = query.where(SourceAnnotation.kind == kind)
    rows = await db.execute(query.order_by(SourceAnnotation.created_at, SourceAnnotation.id))
    return list(rows.scalars())


def annotation_dict(a: SourceAnnotation) -> dict[str, Any]:
    return {
        "id": a.id,
        "kind": a.kind,
        "redistribution": a.redistribution,
        "value": a.value,
        "reason": a.reason,
        "created_by": a.created_by,
        "created_by_origin": a.created_by_origin,
        "approval_id": a.approval_id,
        "approved_by": a.approved_by,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def current_licence_view(db: AsyncSession, source: Source) -> dict[str, Any]:
    """Raw, display, origin and gated as imported, plus what the operator has recorded since.

    ``terms_status`` is the latest ``terms`` annotation's redistribution value (or "not
    recorded"); ``redistribution`` is the latest ``terms`` OR ``licence`` annotation's value — the
    operator's assertion feature 008 classifies by first (008 FTDD 4.4). The Hub's raw value is
    never rewritten (FR-001.28).
    """
    history = await annotations_of(db, source.id)
    terms = [a for a in history if a.kind == AnnotationKind.TERMS]
    asserted = [a for a in history if a.kind in (AnnotationKind.TERMS, AnnotationKind.LICENCE)]
    return {
        "raw": source.licence_raw,
        "display": source.licence_display,
        "origin": source.licence_origin,
        "gated": source.gated,
        "redistribution": asserted[-1].redistribution if asserted else None,
        "terms_status": terms[-1].redistribution if terms else "not recorded",
        "history": [annotation_dict(a) for a in history],
    }


async def effective_detection(db: AsyncSession, source: Source) -> dict[str, Any] | None:
    """The stored detection, with the latest operator override applied (the original kept)."""
    overrides = await annotations_of(db, source.id, AnnotationKind.DETECTION_OVERRIDE)
    if not overrides or source.detection is None:
        return source.detection
    merged = dict(source.detection)
    merged.update(overrides[-1].value)
    merged["overridden"] = True
    merged["suggested"] = source.detection
    return merged


async def annotation_card_facts(
    db: AsyncSession, source_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """What the operator's approval card shows (FR-001.38; 001 FTDD section 5.3a).

    Validates first (:func:`check_annotation`): a request that cannot land is refused now, not
    after the operator approved it."""
    source = await get_source_row(db, source_id)
    check_annotation(source, body)
    current = await annotations_of(db, source.id, body["kind"])
    latest = current[-1] if current else None
    facts: dict[str, Any] = {
        "source_id": source.id,
        "display_name": source.display_name,
        "repo_id": source.repo_id,
        "content_hash": source.content_hash,
        "current": annotation_dict(latest) if latest else None,
        "proposed": {k: body.get(k) for k in ("kind", "redistribution", "value", "reason")},
        "expected_current_id": latest.id if latest else None,
    }
    if body["kind"] in (AnnotationKind.TERMS, AnnotationKind.LICENCE):
        facts["warning"] = PUSH_WARNING
    return facts


OVERRIDABLE = {
    "trl_type",
    "trl_format",
    "chat_format",
    "text_columns",
    "label_columns",
    "suggested_target",
}


def check_override(value: dict[str, Any]) -> None:
    """A detection override names only detection outputs (FR-001.20); anything else is refused."""
    unknown = sorted(set(value) - OVERRIDABLE)
    if not value or unknown:
        raise AppError(
            f"A detection override sets one or more of {sorted(OVERRIDABLE)}"
            + (f"; {unknown} are not detection outputs." if unknown else "."),
            code="override_invalid",
            status_code=422,
            details={"allowed": sorted(OVERRIDABLE), "unknown": unknown},
        )
    for key in ("text_columns", "label_columns"):
        if key in value and not (
            isinstance(value[key], list) and all(isinstance(c, str) for c in value[key])
        ):
            raise AppError(
                f"{key} is a list of column names.", code="override_invalid", status_code=422
            )


def check_annotation(source: Source, body: dict[str, Any]) -> None:
    """Refusals that do not depend on who asks: run before an agent's approval is stored, so the
    operator is never asked to approve a request that cannot land, and again at write time."""
    if source.state == SourceState.DELETED:
        raise ConflictError(
            "That source was deleted; it cannot be annotated.", code="source_deleted"
        )
    kind = body["kind"]
    if kind == AnnotationKind.DETECTION_OVERRIDE:
        check_override(body.get("value") or {})
    if (
        kind in (AnnotationKind.TERMS, AnnotationKind.LICENCE)
        and body.get("redistribution") is None
    ):
        raise AppError(
            "A licence or terms annotation must say whether redistribution is permitted "
            f"({[r.value for r in Redistribution]}).",
            code="redistribution_required",
            status_code=422,
        )


@dataclass(frozen=True)
class ApprovalRef:
    approval_id: str
    approved_by: str


async def approval_ref(db: AsyncSession, approval_id: str | None) -> ApprovalRef | None:
    if approval_id is None:
        return None
    row = await db.get(Approval, approval_id, populate_existing=True)
    if row is None or row.decided_by is None:
        return None
    return ApprovalRef(row.id, row.decided_by)


async def annotate(
    db: AsyncSession,
    source_id: str,
    body: dict[str, Any],
    who: Who,
    *,
    approval: ApprovalRef | None,
    expected_current_id: str | None = None,
    check_current: bool = False,
) -> SourceAnnotation:
    source = await get_source_row(db, source_id)
    check_annotation(source, body)
    kind = body["kind"]
    if who.origin == "agent" and approval is None:
        raise AppError(
            "An agent's annotation needs the operator's approval (source_annotate).",
            code="APPROVAL_REQUIRED",
            status_code=403,
        )
    if check_current:
        current = await annotations_of(db, source.id, kind)
        actual = current[-1].id if current else None
        if actual != expected_current_id:
            raise ConflictError(
                "Another annotation of this kind landed after the agent asked; this approval "
                "described a different state and was not applied. Ask again.",
                code="APPROVAL_STALE",
                details={"expected_current_id": expected_current_id, "current_id": actual},
            )
    row = SourceAnnotation(
        id=str(uuid.uuid4()),
        source_id=source.id,
        kind=kind,
        redistribution=body.get("redistribution"),
        value=body.get("value") or {},
        reason=body["reason"],
        created_by=who.who,
        created_by_origin=who.origin,
        approval_id=approval.approval_id if approval else None,
        approved_by=approval.approved_by if approval else None,
    )
    db.add(row)
    await db.commit()
    return row


async def versions_reading(db: AsyncSession, source_id: str) -> list[dict[str, Any]]:
    """002's repository question: which versions name this source as an input."""
    rows = await db.execute(
        select(Version.id, Version.number, Version.dataset_id)
        .join(VersionInput, VersionInput.version_id == Version.id)
        .where(VersionInput.source_id == source_id)
        .order_by(Version.number)
    )
    return [{"version_id": v, "number": n, "dataset_id": d} for v, n, d in rows.all()]


async def delete(db: AsyncSession, source_id: str, reason: str, who: Who) -> Source:
    source = await get_source_row(db, source_id)
    if source.state == SourceState.DELETED:
        return source
    if source.state == SourceState.IMPORTING:
        raise ConflictError(
            "The source is still importing. Cancel the import job, then delete it.",
            code="source_importing",
        )
    readers = await versions_reading(db, source.id)
    if readers:
        raise ConflictError(
            f"{len(readers)} version(s) were built from this source, so it is kept. Delete those "
            "versions first if you really want it gone.",
            code="source_in_use",
            details={"versions": readers},
        )
    directory = source_dir(source.id)
    if directory.exists():
        try:
            shutil.rmtree(directory)  # unlink FIRST; a failure leaves a ready, retryable source
        except OSError as exc:
            logger.warning("could not remove files of source %s: %s", source.id, exc)
            raise AppError(
                "The source's files could not all be removed, so it was kept. Check the data "
                "volume, then delete it again.",
                code="source_delete_failed",
                status_code=500,
                details={"source_id": source.id},
            ) from None
    if source.state == SourceState.READY:
        source.state = SourceState.DELETED
        source.deleted_by = who.who
        source.deleted_by_origin = who.origin
        source.deleted_at = utc_now()
        await db.commit()
    logger.info("source %s deleted by %s (%s)", source.id, who.who, reason[:80])
    return source


async def rows_page(
    db: AsyncSession, source_id: str, split: str | None, page: int, limit: int
) -> dict[str, Any]:
    source = await get_source_row(db, source_id)
    if source.state != SourceState.READY:
        raise ConflictError(
            f"The source is {source.state}; only a ready source has rows to show.",
            code="source_not_ready",
        )
    files = await files_of(db, source.id)
    if split is not None:
        files = [f for f in files if f.split == split]
        if not files:
            raise AppError(
                f"The source has no split {split!r}.", code="split_not_found", status_code=404
            )
    paths = [resolve_under_data_dir(f.path) for f in files]
    con = connect()
    try:
        total = int(con.execute("SELECT count(*) FROM read_parquet(?)", [files_param(paths)]).fetchone()[0])  # type: ignore[index]
        table = con.execute(
            "SELECT * FROM read_parquet(?) LIMIT ? OFFSET ?",
            [files_param(paths), limit, (page - 1) * limit],
        ).to_arrow_table()
    finally:
        con.close()
    from ..lineage_service import _json_safe

    return {
        "rows": [_json_safe(r) for r in table.to_pylist()],
        "total": total,
        "page": page,
        "limit": limit,
    }


def not_stated() -> str:
    return LICENCE_NOT_STATED
