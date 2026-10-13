"""Version and build request/response models (FR-002.2, 002.5, 002.16; FTDD 002 section 5.3)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..services.identity import VERSION_SEED_LIMIT


class SourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["source"]
    source_id: str


class VersionInputRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["version"]
    version_id: str


InputRef = Annotated[SourceInput | VersionInputRef, Field(discriminator="kind")]


class BindingRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["label_run", "generation_run"]
    id: str = Field(min_length=1, max_length=64)


class _BuildFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    inputs: list[InputRef] = Field(min_length=1, max_length=16)
    seed: int | None = Field(default=None, ge=0, lt=VERSION_SEED_LIMIT)
    bindings: list[BindingRef] = Field(default_factory=list, max_length=64)
    column_roles: dict[str, Literal["content", "metadata"]] = Field(default_factory=dict)


class VersionBuildRequest(_BuildFields):
    recipe_revision_id: str


class RecipeBuildRequest(_BuildFields):
    """``POST /recipes/{id}/build``: the recipe's head revision is used."""

    def to_build_request(self, revision_id: str | None) -> VersionBuildRequest:
        return VersionBuildRequest(
            **self.model_dump(exclude_none=False), recipe_revision_id=revision_id or ""
        )


class BuildAccepted(BaseModel):
    job_id: str
    existing_job: bool
    seed: int
    request_digest: str


class SplitOut(BaseModel):
    name: str
    held_out: bool
    rows: int
    bytes: int
    file_sha256: str
    logical_digest: str
    path: str


class VersionOut(BaseModel):
    id: str
    dataset_id: str
    dataset_name: str
    target_type: str
    number: int
    state: str
    is_head: bool
    superseded_by: int | None
    parent_version_id: str | None
    request_digest: str
    inputs: list[dict[str, Any]]
    recipe_hash: str
    recipe_revision_id: str
    recipe_id: str | None
    seed: int
    bindings: list[dict[str, Any]]
    rowkey_scheme: str
    column_roles: dict[str, str]
    splits: list[SplitOut]
    total_rows: int
    total_bytes: int
    held_out_origin_version_id: str | None
    warnings: list[dict[str, Any]]
    drop_summary: list[dict[str, Any]]
    manifest_sha256: str
    build_job_id: str
    created_by: str
    created_by_origin: str
    created_at: datetime
    deleted_by: str | None = None
    deleted_by_origin: str | None = None
    deleted_at: datetime | None = None
    delete_reason: str | None = None


class VersionSummary(BaseModel):
    id: str
    dataset_id: str
    dataset_name: str
    target_type: str
    number: int
    state: str
    is_head: bool
    superseded_by: int | None
    parent_version_id: str | None
    total_rows: int
    total_bytes: int
    warnings_count: int
    recipe_hash: str
    seed: int
    created_by: str
    created_at: datetime


class VersionList(BaseModel):
    items: list[VersionSummary]
    total: int
    page: int
    limit: int


class DeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class VerifyAccepted(BaseModel):
    job_id: str


class RowPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    page: int
    limit: int
    columns: list[str]


class RowFilter(BaseModel):
    """One structured filter; never free SQL (FTDD 002 section 5.8)."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=128)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "is_null", "not_null"]
    value: str | int | float | bool | None = None
