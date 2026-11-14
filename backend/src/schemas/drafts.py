"""Recipe draft models (FR-002.18, FR-002.48). A draft may be invalid; it has no hash."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str | None = None
    dataset_id: str | None = None
    name: str | None = Field(default=None, max_length=100)
    body: dict[str, Any] = Field(default_factory=dict)
    step_labels: list[str] = Field(default_factory=list, max_length=64)
    inputs: list[dict[str, Any]] = Field(default_factory=list, max_length=16)
    flow_state: dict[str, Any] = Field(default_factory=dict)


class DraftOut(BaseModel):
    id: str
    recipe_id: str | None
    dataset_id: str | None
    name: str | None
    body: dict[str, Any]
    step_labels: list[str]
    inputs: list[dict[str, Any]]
    flow_state: dict[str, Any]
    updated_by: str
    updated_by_origin: str
    created_at: datetime
    updated_at: datetime


class DraftList(BaseModel):
    items: list[DraftOut]
    total: int


class DraftSaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_name: str | None = Field(default=None, min_length=1, max_length=100)
