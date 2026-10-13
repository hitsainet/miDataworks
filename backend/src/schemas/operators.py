"""Request and response models for the operator routes (FR-003.24; FTDD 003 section 5.1).

``params`` is a plain object validated by ``jsonschema`` against the operator's manifest, never by
Pydantic, so the manifest is the single source of what a parameter may be (FTID 003 section 5).
"""

from __future__ import annotations

from typing import Any

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


class PreviewInputIn(_Strict):
    """``{version_id, split?}`` or ``{step_execution_id}`` (a computed step's output)."""

    version_id: str | None = Field(None, max_length=64)
    split: str | None = Field(None, max_length=128)
    step_execution_id: str | None = Field(None, max_length=64)

    @model_validator(mode="after")
    def _one(self) -> PreviewInputIn:
        if (self.version_id is None) == (self.step_execution_id is None):
            raise ValueError("give exactly one of version_id or step_execution_id")
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
