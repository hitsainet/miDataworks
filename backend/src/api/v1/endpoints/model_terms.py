"""Model-terms notes (FR-008.60; FTDD 008 section 5.2, T16).

Operator only (confirmed S3-08): a note can unlock a public push (C-4), so an agent-originated
write is refused with ``403 agent_not_allowed`` before anything is stored, and the table's CHECK
refuses an agent origin even if this route forgot to. 010 lists the route in its exemption map.
Append-only: there is no update or delete route, and a trigger refuses both.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.agent_origin import Actor, get_actor, resolve_who
from ....core.database import get_db
from ....core.errors import ForbiddenError
from ....core.ids import new_id
from ....models.publish import ModelTermsNote
from ....schemas.publishing import ModelTermsOut, TermsNoteIn, TermsNoteOut

router = APIRouter(prefix="/api/v1/model-terms", tags=["model-terms"])


def _out(n: ModelTermsNote) -> TermsNoteOut:
    return TermsNoteOut(
        id=n.id,
        model_id=n.model_id,
        training_on_outputs=n.training_on_outputs,
        text=n.text,
        noted_by=n.noted_by,
        noted_by_origin=n.noted_by_origin,
        prefill_source=n.prefill_source,
        noted_at=n.noted_at,
    )


@router.post("/{model_id:path}/notes", response_model=TermsNoteOut, status_code=201)
async def add_note(
    model_id: str,
    body: TermsNoteIn,
    db: AsyncSession = Depends(get_db),
    actor: Actor = Depends(get_actor),
) -> TermsNoteOut:
    if actor.origin == "agent":
        raise ForbiddenError(
            "Model-terms notes are recorded by the operator only: a note can unlock a public "
            "push. Ask the operator to record it on the Publish and export screen.",
            code="agent_not_allowed",
        )
    who = await resolve_who(actor, db)
    note = ModelTermsNote(
        id=new_id("mtn"),
        model_id=model_id,
        training_on_outputs=body.training_on_outputs,
        text=body.text,
        noted_by=who.who,
        noted_by_origin=who.origin,
        prefill_source=body.prefill_source,
    )
    db.add(note)
    await db.commit()
    await db.refresh(note)
    return _out(note)


@router.get("/{model_id:path}", response_model=ModelTermsOut)
async def get_terms(model_id: str, db: AsyncSession = Depends(get_db)) -> ModelTermsOut:
    rows = list(
        (
            await db.execute(
                select(ModelTermsNote)
                .where(ModelTermsNote.model_id == model_id)
                .order_by(ModelTermsNote.noted_at.desc(), ModelTermsNote.id.desc())
            )
        ).scalars()
    )
    return ModelTermsOut(
        model_id=model_id,
        notes=[_out(n) for n in rows],
        latest=rows[0].training_on_outputs if rows else None,
    )
