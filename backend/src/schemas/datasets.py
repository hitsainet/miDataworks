"""Dataset request and response models (FR-002.1, 002.4, 002.44; FTDD 002 section 5.2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..models.dataset import DATASET_NAME_PATTERN
from ..models.enums import TargetType
from .versions import VersionSummary


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, pattern=DATASET_NAME_PATTERN)
    target_type: TargetType = TargetType.UNTYPED
    description: str | None = Field(default=None, max_length=4000)


class DatasetPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None = Field(default=None, max_length=4000)
    target_type: TargetType | None = None


class DatasetSummary(BaseModel):
    """What a Datasets card shows (FR-002.44). Publish state comes from feature 008's slot."""

    id: str
    name: str
    target_type: str
    description: str | None
    head_number: int | None
    head_version_id: str | None
    parent_version_id: str | None
    versions: int
    rows: int | None
    bytes: int | None
    warnings_count: int
    state: str
    created_by: str
    created_at: datetime


class DatasetOut(DatasetSummary):
    version_list: list[VersionSummary]


class DatasetList(BaseModel):
    items: list[DatasetSummary]
    total: int
    page: int
    limit: int


class DatasetsMeta(BaseModel):
    """The one place the UI reads 002's vocabularies (no frontend list can drift)."""

    target_types: list[str]
    default_content_columns: dict[str, list[str]]
    event_kinds: list[str]
    version_states: list[str]
    input_kinds: list[str]
    column_roles: list[str]
    rowkey_schemes: list[str]
    default_rowkey_scheme: str
    binding_kinds: list[str]
    guided_steps: list[str]
