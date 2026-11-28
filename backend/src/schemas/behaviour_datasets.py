"""Response models of feature 017's behaviour routes (FTID 017 §2.1, §5.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .behaviour import BEHAVIOUR_KEY_PATTERN


class CatalogueDefinition(BaseModel):
    version: int | None
    positive_meaning: str | None
    negative_meaning: str | None
    target_fpr: float | None


class RecordedDefinition(BaseModel):
    """The definition a build or a detector set records (TD-017.32)."""

    key: str
    definition_version: int | None
    positive_meaning: str | None
    verified: bool
    source: Literal["catalogue", "supplied"]
    read_at: str


#: The longest ``positive_meaning`` miWorkbench's behaviour catalogue accepts (its
#: ``backend/src/schemas/behaviour.py`` ``MAX_MEANING``; pinned by
#: ``tests/unit/test_recorded_definition_meaning.py`` when the sibling is present). A record is the
#: catalogue's own text, so a lower cap here refused the record ``dataworks_resolve_behaviour``
#: hands back (live finding 2026-11-19: a 1,250-character v2 meaning, cap 1,000).
CATALOGUE_MAX_MEANING = 4000


class RecordedDefinitionIn(BaseModel):
    """A definition supplied to be recorded (a detector set's; the resolve answer's ``record``)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    definition_version: int | None = Field(None, ge=1)
    positive_meaning: str | None = Field(None, max_length=CATALOGUE_MAX_MEANING)
    verified: bool
    source: Literal["catalogue", "supplied"]
    read_at: str | None = Field(None, max_length=64)


class ResolveOut(BaseModel):
    """``GET /behaviours/{key}/resolve`` (FR-017.10). ``verified`` is true only when miWorkbench's
    catalogue served this key; ``reason`` says which of the three answers this is."""

    key: str
    verified: bool
    reason: str
    detail: str | None
    definition: CatalogueDefinition | None
    current_version: int | None
    archived: bool | None
    record: RecordedDefinition
