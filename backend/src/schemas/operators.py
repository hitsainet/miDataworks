"""Request and response models for the operator routes (FR-003.24; FTDD 003 section 5.1).

``params`` is a plain object validated by ``jsonschema`` against the operator's manifest, never by
Pydantic, so the manifest is the single source of what a parameter may be (FTID 003 section 5).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.canonical_json import canonical_json
from ..core.config import get_settings


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _cap(params: dict[str, Any]) -> dict[str, Any]:
    limit = get_settings().operator_params_max_bytes
    if len(canonical_json(params)) > limit:
        raise ValueError(f"params must be at most {limit} bytes")
    return params


class ParamsIn(_Strict):
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _size(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _cap(value)


class ValidateIn(ParamsIn):
    """``params`` and, optionally, the columns the step will see (PII run G4): a Data Designer
    template's references are then checked against them."""

    columns: list[str] | None = Field(None, max_length=1000)


class PreviewInputIn(_Strict):
    """``{version_id, split?}``, ``{step_execution_id}`` (a computed step's output) or
    ``{source_id, split?, column_roles?}`` (a ready source, PII run L1: no base version needed)."""

    version_id: str | None = Field(None, max_length=64)
    split: str | None = Field(None, max_length=128)
    step_execution_id: str | None = Field(None, max_length=64)
    source_id: str | None = Field(None, max_length=64)
    #: For a source only: roles to use instead of the detected text column (content/metadata).
    column_roles: dict[str, Literal["content", "metadata"]] | None = Field(None, max_length=500)

    @model_validator(mode="after")
    def _one(self) -> PreviewInputIn:
        given = [x for x in (self.version_id, self.step_execution_id, self.source_id) if x]
        if len(given) != 1:
            raise ValueError("give exactly one of version_id, step_execution_id or source_id")
        if self.column_roles is not None and self.source_id is None:
            raise ValueError("column_roles applies to a source input only")
        return self


class PreviewIn(ParamsIn):
    input: PreviewInputIn
    sample_size: int | None = Field(None, ge=1, le=100_000)
    seed: int = Field(0, ge=0, lt=2**32)


class AllowlistIn(_Strict):
    distribution: str = Field(min_length=1, max_length=255)
    distribution_version: str = Field(min_length=1, max_length=128)
    entry_point: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=2000)


class RecipeStepIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    operator: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class RecipeBodyIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    steps: list[RecipeStepIn] = Field(max_length=200)


class UpgradePlanIn(_Strict):
    recipe_body: RecipeBodyIn
