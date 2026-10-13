"""Decision templates and rubrics: versioned, hashed, immutable once used (FR-005.11 – FR-005.17).

One small library implementation serves both (they have the same shape, FTDD 005 section 4.1):

- ``content_hash`` = SHA-256 of the canonical JSON (Foundation's one serialiser, ADR-005) of
  ``{name, version, body}``, so an export imported again lands on the same hash;
- clone creates ``version + 1`` under the same name; an edit is always a new version (FR-005.12);
- import validates the document BEFORE saving (unknown keys refused); importing a document whose
  hash exists returns the existing row; the same name and version with another body is refused;
- a template or rubric any run references cannot be updated or deleted (``TEMPLATE_IMMUTABLE``;
  the foreign key is ``ON DELETE RESTRICT`` as a backstop);
- the built-in ``jev/noul-bare-v1`` template is seeded from
  ``src/seed/decision_templates/jev_noul_bare_v1.json``, generated from
  ``records/jev_decision_client.json`` (FR-005.14).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..clients.endpoint_errors import RowError
from ..clients.labelers.openai_chat_judge import render_rubric_message, rubric_values
from ..core.canonical_json import canonical_sha256
from ..core.errors import ConflictError, NotFoundError, UnprocessableError
from ..core.ids import new_id
from ..models.decision_template import DecisionTemplate
from ..models.label_run import LabelRun
from ..models.rubric import Rubric
from ..schemas.labeling import (
    RUBRIC_EXPORT_FORMAT,
    TEMPLATE_EXPORT_FORMAT,
    DecisionTemplateExport,
    DecisionTemplateOut,
    RubricBody,
    RubricExport,
    RubricOut,
    TemplateBody,
)

SEED_DIR = Path(__file__).resolve().parents[1] / "seed" / "decision_templates"


def library_hash(name: str, version: int, body: dict[str, Any]) -> str:
    return canonical_sha256({"name": name, "version": version, "body": body})


@dataclass(frozen=True)
class Who:
    who: str
    origin: str


def _body_json(body: BaseModel) -> dict[str, Any]:
    return body.model_dump(mode="json")


# --- templates --------------------------------------------------------------------------------


async def _template_used(db: AsyncSession, template_id: str) -> bool:
    return bool(
        (await db.execute(select(exists().where(LabelRun.template_id == template_id)))).scalar()
    )


async def template_out(db: AsyncSession, row: DecisionTemplate) -> DecisionTemplateOut:
    return DecisionTemplateOut(
        id=row.id,
        name=row.name,
        version=row.version,
        ref=f"{row.name}@{row.version}",
        content_hash=row.content_hash,
        protocol=row.protocol,
        variant=row.variant,
        bound_model_id=row.bound_model_id,
        bound_model_revision=row.bound_model_revision,
        body=row.body,
        used=await _template_used(db, row.id),
        created_by=row.created_by,
        created_by_origin=row.created_by_origin,
        created_at=row.created_at,
    )


async def _next_template_version(db: AsyncSession, name: str) -> int:
    current = (
        await db.execute(
            select(func.max(DecisionTemplate.version)).where(DecisionTemplate.name == name)
        )
    ).scalar()
    return int(current or 0) + 1


async def _insert_template(
    db: AsyncSession, name: str, version: int, body: TemplateBody, who: Who
) -> DecisionTemplate:
    body_json = _body_json(body)
    digest = library_hash(name, version, body_json)
    existing = (
        await db.execute(select(DecisionTemplate).where(DecisionTemplate.content_hash == digest))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    clash = (
        await db.execute(
            select(DecisionTemplate).where(
                DecisionTemplate.name == name, DecisionTemplate.version == version
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise ConflictError(
            f"{name}@{version} already exists with a different body. Clone it to make a new "
            "version.",
            code="TEMPLATE_VERSION_EXISTS",
            details={"existing_id": clash.id},
        )
    row = DecisionTemplate(
        id=new_id("dt"),
        name=name,
        version=version,
        content_hash=digest,
        body=body_json,
        protocol=body.kind,
        variant=getattr(body, "variant", None),
        bound_model_id=body.bound_model_id,
        bound_model_revision=body.bound_model_revision,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    await db.flush()
    return row


async def create_template(
    db: AsyncSession, name: str, body: TemplateBody, who: Who
) -> DecisionTemplate:
    version = await _next_template_version(db, name)
    row = await _insert_template(db, name, version, body, who)
    await db.commit()
    return row


async def get_template(db: AsyncSession, template_id: str) -> DecisionTemplate:
    row = await db.get(DecisionTemplate, template_id)
    if row is None:
        raise NotFoundError(f"No decision template {template_id}.", code="TEMPLATE_NOT_FOUND")
    return row


async def list_templates(db: AsyncSession) -> list[DecisionTemplate]:
    await ensure_builtin_templates(db)
    rows = await db.execute(
        select(DecisionTemplate).order_by(DecisionTemplate.name, DecisionTemplate.version)
    )
    return list(rows.scalars())


async def clone_template(
    db: AsyncSession, template_id: str, body: TemplateBody | None, who: Who
) -> DecisionTemplate:
    source = await get_template(db, template_id)
    from ..clients.labelers.factory import parse_template

    new_body = body if body is not None else parse_template(source.body)
    version = await _next_template_version(db, source.name)
    row = await _insert_template(db, source.name, version, new_body, who)
    await db.commit()
    return row


async def update_template(
    db: AsyncSession, template_id: str, body: TemplateBody
) -> DecisionTemplate:
    """Replace an UNUSED template's body in place. A used one is refused (FR-005.12)."""
    row = await get_template(db, template_id)
    if await _template_used(db, template_id):
        raise ConflictError(
            f"{row.name}@{row.version} has been used by a label run, so it cannot change. "
            "Clone it to make a new version.",
            code="TEMPLATE_IMMUTABLE",
            details={"template_id": template_id},
        )
    body_json = _body_json(body)
    row.body = body_json
    row.content_hash = library_hash(row.name, row.version, body_json)
    row.protocol = body.kind
    row.variant = getattr(body, "variant", None)
    row.bound_model_id = body.bound_model_id
    row.bound_model_revision = body.bound_model_revision
    await db.commit()
    return row


async def delete_template(db: AsyncSession, template_id: str) -> None:
    row = await get_template(db, template_id)
    if await _template_used(db, template_id):
        raise ConflictError(
            f"{row.name}@{row.version} has been used by a label run, so it cannot be deleted.",
            code="TEMPLATE_IMMUTABLE",
            details={"template_id": template_id},
        )
    await db.delete(row)
    await db.commit()


def export_template(row: DecisionTemplate) -> DecisionTemplateExport:
    from ..clients.labelers.factory import parse_template

    return DecisionTemplateExport(
        format=TEMPLATE_EXPORT_FORMAT,
        name=row.name,
        version=row.version,
        body=parse_template(row.body),
    )


async def import_template(
    db: AsyncSession, document: DecisionTemplateExport, who: Who
) -> DecisionTemplate:
    row = await _insert_template(db, document.name, document.version, document.body, who)
    await db.commit()
    return row


def builtin_template_documents() -> list[DecisionTemplateExport]:
    docs = []
    for path in sorted(SEED_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw.pop("_origin", None)
        docs.append(DecisionTemplateExport.model_validate(raw))
    return docs


async def ensure_builtin_templates(db: AsyncSession) -> None:
    """Insert the built-in templates that are missing (idempotent, by content hash)."""
    for doc in builtin_template_documents():
        digest = library_hash(doc.name, doc.version, _body_json(doc.body))
        found = (
            await db.execute(
                select(DecisionTemplate.id).where(DecisionTemplate.content_hash == digest)
            )
        ).scalar_one_or_none()
        if found is None:
            await _insert_template(
                db, doc.name, doc.version, doc.body, Who("miDataworks", "system")
            )
    await db.commit()


# --- rubrics ----------------------------------------------------------------------------------


RUBRIC_TEMPLATE_INVALID = "RUBRIC_TEMPLATE_INVALID"


def check_rubric_renders(body: RubricBody, *, rubric_ref: str | None = None) -> None:
    """Render every message of ``body`` with a placeholder value for every name a judge run
    supplies (each input field, ``question``, and ``a``/``b`` for a pairwise rubric), through the
    SAME function the label-run worker renders with. A message that cannot render — literal JSON
    braces, a name that is not an input field — is refused here (422 RUBRIC_TEMPLATE_INVALID)
    rather than 20 rows into a run."""
    fields: dict[str, Any] = dict.fromkeys(body.input_fields, "x")
    if body.style == "pairwise":
        fields["a"], fields["b"] = "x", "x"
    values = rubric_values(fields, "x")
    for index, message in enumerate(body.messages):
        try:
            render_rubric_message(message.content, values)
        except RowError as exc:
            name = exc.details.get("name")
            allowed = sorted(values)
            if name is not None:
                problem = f"{{{name}}} is not an input field of the rubric (it can use {allowed})"
            else:
                problem = f"its braces do not form a template ({exc.details.get('reason')})"
            where = f"Rubric {rubric_ref}: " if rubric_ref else ""
            raise UnprocessableError(
                f"{where}message {index} ({message.role}) cannot be rendered: {problem}. Write "
                'literal braces as {{ and }} (for example {{"score": 7}}), and use {name} only '
                "for an input field.",
                code=RUBRIC_TEMPLATE_INVALID,
                details={
                    "message_index": index,
                    "role": message.role,
                    "name": name,
                    "reason": exc.details.get("reason"),
                    "allowed_names": allowed,
                    **({"rubric": rubric_ref} if rubric_ref else {}),
                },
            ) from None


async def _rubric_used(db: AsyncSession, rubric_id: str) -> bool:
    return bool(
        (await db.execute(select(exists().where(LabelRun.rubric_id == rubric_id)))).scalar()
    )


async def rubric_out(db: AsyncSession, row: Rubric) -> RubricOut:
    return RubricOut(
        id=row.id,
        name=row.name,
        version=row.version,
        ref=f"{row.name}@{row.version}",
        content_hash=row.content_hash,
        style=row.style,
        body=row.body,
        used=await _rubric_used(db, row.id),
        created_by=row.created_by,
        created_by_origin=row.created_by_origin,
        created_at=row.created_at,
    )


async def _insert_rubric(
    db: AsyncSession, name: str, version: int, body: RubricBody, who: Who
) -> Rubric:
    check_rubric_renders(body)
    body_json = _body_json(body)
    digest = library_hash(name, version, body_json)
    existing = (
        await db.execute(select(Rubric).where(Rubric.content_hash == digest))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    clash = (
        await db.execute(select(Rubric).where(Rubric.name == name, Rubric.version == version))
    ).scalar_one_or_none()
    if clash is not None:
        raise ConflictError(
            f"{name}@{version} already exists with a different body. Clone it to make a new "
            "version.",
            code="RUBRIC_VERSION_EXISTS",
            details={"existing_id": clash.id},
        )
    row = Rubric(
        id=new_id("rb"),
        name=name,
        version=version,
        content_hash=digest,
        body=body_json,
        style=body.style,
        created_by=who.who,
        created_by_origin=who.origin,
    )
    db.add(row)
    await db.flush()
    return row


async def _next_rubric_version(db: AsyncSession, name: str) -> int:
    current = (
        await db.execute(select(func.max(Rubric.version)).where(Rubric.name == name))
    ).scalar()
    return int(current or 0) + 1


async def create_rubric(db: AsyncSession, name: str, body: RubricBody, who: Who) -> Rubric:
    row = await _insert_rubric(db, name, await _next_rubric_version(db, name), body, who)
    await db.commit()
    return row


async def get_rubric(db: AsyncSession, rubric_id: str) -> Rubric:
    row = await db.get(Rubric, rubric_id)
    if row is None:
        raise NotFoundError(f"No rubric {rubric_id}.", code="RUBRIC_NOT_FOUND")
    return row


async def list_rubrics(db: AsyncSession) -> list[Rubric]:
    return list((await db.execute(select(Rubric).order_by(Rubric.name, Rubric.version))).scalars())


async def clone_rubric(
    db: AsyncSession, rubric_id: str, body: RubricBody | None, who: Who
) -> Rubric:
    source = await get_rubric(db, rubric_id)
    new_body = body if body is not None else RubricBody.model_validate(source.body)
    row = await _insert_rubric(
        db, source.name, await _next_rubric_version(db, source.name), new_body, who
    )
    await db.commit()
    return row


async def update_rubric(db: AsyncSession, rubric_id: str, body: RubricBody) -> Rubric:
    row = await get_rubric(db, rubric_id)
    if await _rubric_used(db, rubric_id):
        raise ConflictError(
            f"{row.name}@{row.version} has been used by a label run, so it cannot change. "
            "Clone it to make a new version.",
            code="TEMPLATE_IMMUTABLE",
            details={"rubric_id": rubric_id},
        )
    check_rubric_renders(body)
    body_json = _body_json(body)
    row.body = body_json
    row.style = body.style
    row.content_hash = library_hash(row.name, row.version, body_json)
    await db.commit()
    return row


def export_rubric(row: Rubric) -> RubricExport:
    return RubricExport(
        format=RUBRIC_EXPORT_FORMAT,
        name=row.name,
        version=row.version,
        body=RubricBody.model_validate(row.body),
    )


async def import_rubric(db: AsyncSession, document: RubricExport, who: Who) -> Rubric:
    row = await _insert_rubric(db, document.name, document.version, document.body, who)
    await db.commit()
    return row
