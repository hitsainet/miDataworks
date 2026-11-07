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


class RecordedDefinitionIn(BaseModel):
    """A definition supplied to be recorded (a detector set's; the resolve answer's ``record``)."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    definition_version: int | None = Field(None, ge=1)
    positive_meaning: str | None = Field(None, max_length=1000)
    verified: bool
    source: Literal["catalogue", "supplied"]
    read_at: str | None = Field(None, max_length=64)


class ResolveOut(BaseModel):
    """``GET /behaviours/{key}/resolve`` (FR-017.10). ``verified`` is true only when miStudio's
    catalogue served this key; ``reason`` says which of the three answers this is."""

    key: str
    verified: bool
    reason: str
    detail: str | None
    definition: CatalogueDefinition | None
    current_version: int | None
    archived: bool | None
    record: RecordedDefinition
