"""Recipe request and response models (FR-002.10–002.18; FTDD 002 section 5.3).

``RecipeBody`` and ``RecipeStep`` forbid unknown fields: a field Pydantic silently dropped would be
missing from the hash while present in the file, so the hash would not describe the recipe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RECIPE_FORMAT = "dw.recipe/v1"
RECIPE_FILE_FORMAT = "dw.recipe-file/v1"
#: Recipe import files are capped at 1 MB (FTASKS 4.5, 18.8).
RECIPE_FILE_MAX_BYTES = 1_000_000


class RecipeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(min_length=1, max_length=64)
    #: Validated by feature 003 against the operator's schema; never executed (FTDD 5.8).
    params: dict[str, Any] = Field(default_factory=dict)


class RecipeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["dw.recipe/v1"]
    steps: list[RecipeStep] = Field(min_length=1, max_length=64)


class StepError(BaseModel):
    code: str
    message: str


class StepValidation(BaseModel):
    index: int
    operator: str | None = None
    version: str | None = None
    errors: list[StepError]


class ValidationOut(BaseModel):
    valid: bool
    #: Problems with the body as a whole (format, shape), before any step is examined.
    body_errors: list[StepError] = Field(default_factory=list)
    steps: list[StepValidation] = Field(default_factory=list)


class ValidateIn(BaseModel):
    body: dict[str, Any]


class RecipeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    body: dict[str, Any]
    step_labels: list[str] = Field(default_factory=list, max_length=64)


class RevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any]
    step_labels: list[str] = Field(default_factory=list, max_length=64)


class CloneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    revision_id: str | None = None


class RevisionOut(BaseModel):
    id: str
    recipe_id: str
    revision_number: int
    recipe_hash: str
    body: dict[str, Any]
    step_labels: list[str]
    cloned_from_revision_id: str | None
    imported: bool
    created_by: str
    created_by_origin: str
    created_at: datetime
    versions_built: int = 0


class RecipeSummary(BaseModel):
    id: str
    name: str
    description: str | None
    archived: bool
    head_revision_id: str | None
    head_hash: str | None
    step_count: int
    providers: list[str]
    revision_count: int
    versions_built: int
    created_by: str
    created_at: datetime
    updated_at: datetime


class RecipeOut(RecipeSummary):
    revisions: list[RevisionOut]
    archived_at: datetime | None = None
    archived_by: str | None = None


class RecipeList(BaseModel):
    items: list[RecipeSummary]
    total: int
    page: int
    limit: int


class ImportOut(BaseModel):
    outcome: Literal["created", "already_present", "refused"]
    hash: str | None = None
    recipe: RecipeOut | None = None
    reasons: list[StepError] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
