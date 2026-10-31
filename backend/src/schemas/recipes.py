"""Recipe request and response models (FR-002.10–002.18; FTDD 002 section 5.3).

``RecipeBody`` and ``RecipeStep`` forbid unknown fields: a field Pydantic silently dropped would be
missing from the hash while present in the file, so the hash would not describe the recipe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from .behaviour import BehaviourKey

RECIPE_FORMAT = "dw.recipe/v1"
RECIPE_FILE_FORMAT = "dw.recipe-file/v1"
#: Recipe import files are capped at 1 MB (FTASKS 4.5, 18.8).
RECIPE_FILE_MAX_BYTES = 1_000_000
#: A recipe holds at most this many steps; chain recipes (version -> version) for more. Stated
#: in every validation answer (e2e run G4: the cap was learned only from a refused save).
MAX_STEPS = 64


class RecipeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operator: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    version: str = Field(min_length=1, max_length=64)
    #: Validated by feature 003 against the operator's schema; never executed (FTDD 5.8).
    params: dict[str, Any] = Field(default_factory=dict)


class RecipeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["dw.recipe/v1"]
    steps: list[RecipeStep] = Field(min_length=1, max_length=MAX_STEPS)
    #: 017 FR-017.48 (TD-017.31): a declared visibility floor. Omitted from the body (and so from
    #: the recipe hash) when absent: every recipe saved before 017 keeps its hash.
    visibility_floor: Literal["private_only"] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_floor(self, handler: Any) -> Any:
        data = handler(self)
        if isinstance(data, dict) and data.get("visibility_floor") is None:
            data.pop("visibility_floor", None)
        return data


class StepError(BaseModel):
    code: str
    message: str
    #: The rule that failed, when one is named (Data Designer's ``too_deep``, ``max_steps`` ...).
    rule: str | None = None


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
    #: The limits a recipe is checked against, stated up front: ``max_steps`` always; Data
    #: Designer's ``max_depth``, ``max_nodes`` and ``filters`` once a designer step was checked.
    limits: dict[str, Any] = Field(default_factory=lambda: {"max_steps": MAX_STEPS})
    #: Whether column references were checked: against the real input columns (``inputs``),
    #: the columns given (``given``), or not at all (``none``).
    columns_checked: str = "none"


class ValidateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: The body to check, or ``recipe_revision_id`` to check a saved revision by reference.
    body: dict[str, Any] | None = None
    recipe_revision_id: str | None = None
    #: The input columns the recipe will run on: every step's column references and every Data
    #: Designer template are then checked against what that step will really see.
    columns: list[str] | None = Field(default=None, max_length=5000)


class RecipeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    #: The body inline (steps may be ``{include: <revision id>, from?, to?}`` blocks), or
    #: ``recipe_revision_id`` to start from a saved revision's body by reference.
    body: dict[str, Any] | None = None
    recipe_revision_id: str | None = None
    step_labels: list[str] = Field(default_factory=list, max_length=64)
    #: 017 FR-017.9: on the named recipe (head row), never the body: the hash is unchanged.
    behaviour_key: BehaviourKey | None = None


class RevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: dict[str, Any] | None = None
    recipe_revision_id: str | None = None
    step_labels: list[str] = Field(default_factory=list, max_length=64)


class RecipeSaved(BaseModel):
    """A save's short answer (``?view=summary``): no body echoed back (e2e run G6)."""

    recipe_id: str
    name: str
    revision_id: str
    revision_number: int
    recipe_hash: str
    step_count: int
    operators: dict[str, int]
    body_bytes: int
    note: str


class Substitution(BaseModel):
    """One change a clone makes to the copied document (017 FR-017.59): an RFC 6901 JSON
    Pointer to an existing member and its new value."""

    model_config = ConfigDict(extra="forbid")

    pointer: str = Field(max_length=500, pattern=r"^(/.*)?$")
    value: Any


class CloneIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    revision_id: str | None = None
    #: 017 FR-017.9: the clone keeps the source's key unless the request names one (``null``
    #: clears it).
    behaviour_key: BehaviourKey | None = None
    #: 017 FR-017.59: JSON Pointer substitutions into the copied body, applied in order.
    substitutions: list[Substitution] = Field(default_factory=list, max_length=200)


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
    behaviour_key: str | None = None


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
