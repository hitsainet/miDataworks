"""Generation templates: create, get, list, clone, built-ins (FR-007.7; FTASKS 4.1, 4.2).

- ``content_hash`` = SHA-256 of the canonical JSON of ``{name, version, kind, body}`` (ADR-005,
  Foundation's one serialiser), so the same document always lands on the same hash.
- An edit is a clone: ``version + 1`` under the same name. A template a run has used refuses any
  update (``TEMPLATE_IMMUTABLE``), enforced by the service AND a database trigger.
- ``expand-v1`` and ``respond-v1`` are seeded from ``src/seed/generation_templates/*.json`` on
  first read, as 005 seeds its built-in decision template.
- ``{prompt}`` is the run's prompt (the seed row's prompt column for expansion, the current prompt
  for responses); every other ``{name}`` must be a column of the input version, checked at plan.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.agent_origin import Who
from ...core.canonical_json import canonical_sha256
from ...core.clock import utc_now
from ...core.errors import ConflictError, NotFoundError, UnprocessableError
from ...core.ids import new_id
from ...models.generation import GenerationTemplate
from ...schemas.generation import TemplateBody, TemplateOut
from .rules import placeholders

SEED_DIR = Path(__file__).resolve().parents[2] / "seed" / "generation_templates"
SYSTEM = "midataworks"
#: The placeholder that is not a column: the run's prompt.
PROMPT_PLACEHOLDER = "prompt"


def template_hash(name: str, version: int, kind: str, body: dict[str, Any]) -> str:
    return canonical_sha256({"name": name, "version": version, "kind": kind, "body": body})


def template_out(row: GenerationTemplate) -> TemplateOut:
    return TemplateOut(
        id=row.id,
        name=row.name,
        version=row.version,
        ref=f"{row.name}@{row.version}",
        kind=row.kind,
        description=row.description,
        body=row.body,
        content_hash=row.content_hash,
        builtin=row.builtin,
        cloned_from=row.cloned_from,
        used=row.used_at is not None,
        placeholders=placeholders(str(row.body.get("prompt", "")))
        + [p for p in placeholders(str(row.body.get("system") or "")) if p],
        created_by=row.created_by,
        created_by_origin=row.created_by_origin,
        created_at=row.created_at,
    )


async def _insert(
    db: AsyncSession,
    *,
    name: str,
    version: int,
    kind: str,
    description: str | None,
    body: TemplateBody,
    who: str,
    origin: str,
    builtin: bool = False,
    cloned_from: str | None = None,
) -> GenerationTemplate:
    body_json = body.model_dump(mode="json")
    digest = template_hash(name, version, kind, body_json)
    existing = (
        await db.execute(
            select(GenerationTemplate).where(
                GenerationTemplate.name == name, GenerationTemplate.version == version
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.content_hash == digest:
            return existing
        raise ConflictError(
            f"{name}@{version} already exists with a different body. Clone it to make a new "
            "version.",
            code="TEMPLATE_VERSION_EXISTS",
            details={"name": name, "version": version},
        )
    row = GenerationTemplate(
        id=new_id("gt"),
        name=name,
        version=version,
        kind=kind,
        description=description,
        body=body_json,
        content_hash=digest,
        builtin=builtin,
        cloned_from=cloned_from,
        created_by=who,
        created_by_origin=origin,
    )
    db.add(row)
    await db.flush()
    return row


def builtin_documents() -> list[dict[str, Any]]:
    docs = []
    for path in sorted(SEED_DIR.glob("*.json")):
        docs.append(json.loads(path.read_text(encoding="utf-8")))
    return docs


async def ensure_builtins(db: AsyncSession) -> None:
    created = False
    for doc in builtin_documents():
        found = (
            await db.execute(
                select(GenerationTemplate.id).where(
                    GenerationTemplate.name == doc["name"], GenerationTemplate.version == 1
                )
            )
        ).scalar_one_or_none()
        if found is not None:
            continue
        await _insert(
            db,
            name=doc["name"],
            version=1,
            kind=doc["kind"],
            description=doc.get("description"),
            body=TemplateBody.model_validate(doc["body"]),
            who=SYSTEM,
            origin="system",
            builtin=True,
        )
        created = True
    if created:
        await db.commit()


async def create(
    db: AsyncSession,
    who: Who,
    *,
    name: str,
    kind: str,
    description: str | None,
    body: TemplateBody,
) -> GenerationTemplate:
    current = (
        await db.execute(
            select(func.max(GenerationTemplate.version)).where(GenerationTemplate.name == name)
        )
    ).scalar()
    if current is not None:
        raise ConflictError(
            f"A template named {name!r} exists; clone it to make version {int(current) + 1}.",
            code="TEMPLATE_NAME_EXISTS",
            details={"name": name, "latest_version": int(current)},
        )
    row = await _insert(
        db,
        name=name,
        version=1,
        kind=kind,
        description=description,
        body=body,
        who=who.who,
        origin=who.origin,
    )
    await db.commit()
    return row


async def get(db: AsyncSession, template_id: str) -> GenerationTemplate:
    row = await db.get(GenerationTemplate, template_id, populate_existing=True)
    if row is None:
        raise NotFoundError(f"No generation template {template_id}.", code="TEMPLATE_NOT_FOUND")
    return row


async def list_templates(
    db: AsyncSession, *, kind: str | None, page: int, limit: int
) -> tuple[list[GenerationTemplate], int]:
    await ensure_builtins(db)
    query = select(GenerationTemplate)
    if kind:
        query = query.where(GenerationTemplate.kind == kind)
    total = int((await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one())
    rows = await db.execute(
        query.order_by(GenerationTemplate.name, GenerationTemplate.version)
        .offset((page - 1) * limit)
        .limit(limit)
    )
    return list(rows.scalars()), total


async def clone(
    db: AsyncSession,
    who: Who,
    template_id: str,
    *,
    body: TemplateBody | None,
    description: str | None,
) -> GenerationTemplate:
    source = await get(db, template_id)
    latest = (
        await db.execute(
            select(func.max(GenerationTemplate.version)).where(
                GenerationTemplate.name == source.name
            )
        )
    ).scalar()
    new_body = body or TemplateBody.model_validate(source.body)
    row = await _insert(
        db,
        name=source.name,
        version=int(latest or source.version) + 1,
        kind=source.kind,
        description=description if description is not None else source.description,
        body=new_body,
        who=who.who,
        origin=who.origin,
        cloned_from=source.id,
    )
    await db.commit()
    return row


def check_placeholders(row: GenerationTemplate, columns: set[str], *, field: str) -> list[str]:
    """Every placeholder is ``{prompt}`` or a column of the input version (FTASKS 4.3)."""
    used = placeholders(str(row.body.get("prompt", ""))) + placeholders(
        str(row.body.get("system") or "")
    )
    missing = sorted({p for p in used if p != PROMPT_PLACEHOLDER and p not in columns})
    if missing:
        raise UnprocessableError(
            f"Template {row.name}@{row.version} uses {missing}, which the input version does not "
            "have as columns. Use {prompt} or a column the version has.",
            code="TEMPLATE_PLACEHOLDER_UNKNOWN",
            details={"field": field, "missing": missing, "columns": sorted(columns)},
        )
    return used


def mark_used(row: GenerationTemplate) -> None:
    """Called inside the start transaction: from now on the template refuses updates."""
    if row.used_at is None:
        row.used_at = utc_now()


def require_mutable(row: GenerationTemplate) -> None:
    if row.used_at is not None:
        raise ConflictError(
            f"Template {row.name}@{row.version} has been used by a run; clone it to change it.",
            code="TEMPLATE_IMMUTABLE",
        )
