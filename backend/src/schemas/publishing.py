"""REST models for feature 008 (FTDD 008 section 5.2; FTID 008 section 5).

Requests forbid extra keys, so a body field named ``started_by`` is refused (FR-008.53): "who" is
always taken from the request's origin, never from its body. ``visibility`` defaults to
``private`` — the only default in the feature, and the safe one (FR-008.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

REPO_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildIn(_In):
    label_column: str | None = Field(None, max_length=200)


class BuildAccepted(BaseModel):
    build_id: str
    job_id: str | None
    reused: bool
    status: str


class BuildOut(BaseModel):
    id: str
    version_id: str
    status: str
    projection: dict[str, Any]
    files: list[dict[str, Any]] | None
    columns: list[dict[str, Any]] | None
    omitted: dict[str, Any] | None
    error: dict[str, Any] | None
    job_id: str | None
    created_at: datetime
    completed_at: datetime | None


class ChecksIn(_In):
    build_id: str = Field(min_length=1, max_length=40)
    repo_id: str = Field(pattern=REPO_ID_PATTERN)
    visibility: Literal["private", "public"] = "private"


class CheckRunAccepted(BaseModel):
    check_run_id: str
    job_id: str


class CheckRunOut(BaseModel):
    id: str
    version_id: str
    build_id: str
    repo_id: str
    requested_visibility: str
    status: str
    results: list[dict[str, Any]] | None
    licence_table_version: int | None
    job_id: str | None
    created_at: datetime
    completed_at: datetime | None


class PublishIn(_In):
    version_id: str = Field(min_length=1, max_length=64)
    build_id: str = Field(min_length=1, max_length=40)
    repo_id: str = Field(pattern=REPO_ID_PATTERN)
    visibility: Literal["private", "public"] = "private"
    card_prose: str = Field("", max_length=100_000)


class CardIn(_In):
    card_prose: str = Field(max_length=100_000)


class PublishAccepted(BaseModel):
    publish_id: str
    job_id: str
    request_digest: str


class PublishOut(BaseModel):
    id: str
    job_id: str
    version_id: str
    build_id: str
    kind: str
    parent_publish_id: str | None
    repo_id: str
    requested_visibility: str
    visibility_after: str | None
    repo_existed: bool | None
    repo_was_private: bool | None
    head_before: str | None
    commit: str | None
    status: str
    card_sha256: str | None
    manifest_sha256: str | None
    check_snapshot: list[dict[str, Any]] | None
    licence_table_version: int | None
    request_digest: str
    approval_id: str | None
    send_id: str | None
    approved_by: str | None
    started_by: str
    started_by_origin: str
    cancel_too_late: bool
    timings: dict[str, Any] | None
    error: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None
    files: list[dict[str, Any]] = []


class PublishList(BaseModel):
    items: list[PublishOut]
    total: int
    page: int
    limit: int


class JobAccepted(BaseModel):
    job_id: str


class CardDraftOut(BaseModel):
    front_matter: dict[str, Any]
    record_markdown: str
    prose: str
    build_id: str


class ExportIn(_In):
    target: Literal["trl", "miforge_set", "reward_bundle"]
    version_id: str = Field(min_length=1, max_length=64)
    trl_type: Literal["sft", "dpo", "kto", "grpo_prompt", "prm"] | None = None
    miforge_set_kind: (
        Literal["prompt_set", "corpus", "preference_pairs", "test_set", "retention_set"] | None
    ) = None
    format: Literal["parquet", "jsonl"] = "parquet"
    label_column: str | None = Field(None, max_length=200)
    extra_columns: list[str] = Field(default_factory=list, max_length=50)


class ExportAccepted(BaseModel):
    export_id: str
    job_id: str


class ExportOut(BaseModel):
    id: str
    job_id: str | None
    target: str
    version_id: str | None
    config_version_id: str | None
    params: dict[str, Any]
    trl_version: str | None
    status: str
    files: list[dict[str, Any]] | None
    manifest_sha256: str | None
    error: dict[str, Any] | None
    started_by: str
    started_by_origin: str
    created_at: datetime
    completed_at: datetime | None


class ExportList(BaseModel):
    items: list[ExportOut]
    total: int
    page: int
    limit: int


class LicenceTableOut(BaseModel):
    table: str
    version: int
    permits_redistribution: list[str]
    classes: list[str]


class TermsNoteIn(_In):
    training_on_outputs: Literal["permits", "forbids"]
    text: str = Field(min_length=1, max_length=4000)
    prefill_source: str | None = Field(None, max_length=500)


class TermsNoteOut(BaseModel):
    id: str
    model_id: str
    training_on_outputs: str
    text: str
    noted_by: str
    noted_by_origin: str
    prefill_source: str | None
    noted_at: datetime


class ModelTermsOut(BaseModel):
    model_id: str
    notes: list[TermsNoteOut]
    latest: str | None


class ConfigVersionIn(_In):
    kind: Literal["selector", "grader"]
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    body: dict[str, Any]
    parent_id: str | None = None


class ConfigVersionOut(BaseModel):
    id: str
    kind: str
    name: str
    number: int
    plugin: str
    body: dict[str, Any]
    body_sha256: str
    body_format: str
    parent_id: str | None
    created_by: str
    created_by_origin: str
    created_at: datetime


class ConfigVersionList(BaseModel):
    items: list[ConfigVersionOut]
    total: int
