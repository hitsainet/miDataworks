"""``POST /api/v1/differences`` (017 FR-017.59): what a clone changed, as data.

Body ``{kind: recipe | generation_template, a, b}``; the answer lists every difference from ``a``
to ``b`` (``services/behaviour/difference.py``). Reads only; nothing is stored.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ....core.database import get_db
from ....services.behaviour import difference

router = APIRouter(prefix="/api/v1/differences", tags=["behaviours"])

ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


class DifferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["recipe", "generation_template"]
    a: str = Field(pattern=ID, description="The source: a recipe ID or a generation template ID.")
    b: str = Field(pattern=ID, description="The clone, of the same kind.")


@router.post("")
async def clone_difference(
    body: DifferenceIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Every difference from ``a`` to ``b`` (``{op, path, old?, new?}``; RFC 6901 pointers) over
    the behaviour key and the body (a recipe's head revision; a template's kind and body)."""
    return await difference.difference_of(db, body.kind, body.a, body.b)
