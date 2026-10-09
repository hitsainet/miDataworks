"""Feature 001 request and response models (001 FTDD section 5.2).

``repo_id`` follows the one rule shared with the frontend (``docs/schemas/hf-repo-id-cases.json``);
a malformed ID is refused here, before any network call. ``access_token`` is a ``SecretStr``,
normalised so blank or ``none`` means absent; it is never echoed in a response.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer, field_validator
from pydantic_core import PydanticCustomError

from ..models.source_enums import AnnotationKind, Redistribution
from ..services.sources.tokens import normalise_token

REPO_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$"
REPO_ID_MAX = 96
_REPO_ID = re.compile(REPO_ID_PATTERN)


def check_repo_id(value: str) -> str:
    """The one repository-ID rule (shared case file ``docs/schemas/hf-repo-id-cases.json``)."""
    if not isinstance(value, str) or len(value) > REPO_ID_MAX or not _REPO_ID.fullmatch(value):
        raise PydanticCustomError(
            "repo_id_invalid",
            "A repository ID looks like owner/name: letters, digits, '-', '_' and '.', each part "
            "starting with a letter or digit, at most 96 characters.",
        )
    return value


class _HfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    config: str | None = Field(default=None, max_length=128)
    split: str | None = Field(default=None, max_length=128)
    revision: str | None = Field(default=None, max_length=255, pattern=r"^\S*$")
    access_token: SecretStr | None = None

    @field_validator("repo_id")
    @classmethod
    def _repo_id(cls, value: str) -> str:
        return check_repo_id(value)

    @field_validator("access_token", mode="before")
    @classmethod
    def _normalise(cls, value: Any) -> Any:
        raw = value.get_secret_value() if isinstance(value, SecretStr) else value
        return normalise_token(raw)

    @field_validator("config", "split", "revision", mode="before")
    @classmethod
    def _blank_is_absent(cls, value: Any) -> Any:
        return value.strip() or None if isinstance(value, str) else value

    @field_serializer("access_token", when_used="json")
    def _reveal_for_the_approval_store(self, value: SecretStr | None) -> str | None:
        """The approval decorator dumps request models to store them; it registers
        ``body.access_token`` as a secret field, so this value is popped, HMAC'd for the digest and
        kept only encrypted (P-11). A masked "**********" here would make an approved import run
        with a token of asterisks. Request models are never returned in a response."""
        return value.get_secret_value() if value else None

    def token(self) -> str | None:
        return self.access_token.get_secret_value() if self.access_token else None


class HfPreviewRequest(_HfRequest):
    pass


class HfImportRequest(_HfRequest):
    confirm_large: bool = False


class CsvOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delimiter: str = Field(default=",", min_length=1, max_length=1)
    quote: str = Field(default='"', min_length=1, max_length=1)
    header: bool = True
    encoding: str = Field(default="utf-8", max_length=32)
    type_mode: Literal["infer", "text"] = "infer"


class UploadFileEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    split: str = Field(min_length=1, max_length=64)


class UploadManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[UploadFileEntry] = Field(min_length=1, max_length=32)
    csv: CsvOptions | None = None
    display_name: str | None = Field(default=None, max_length=200)


class AnnotationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AnnotationKind
    redistribution: Redistribution | None = None
    value: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2000)


class DeleteSourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)


class AnnotationOut(BaseModel):
    id: str
    kind: str
    redistribution: str | None
    value: dict[str, Any]
    reason: str
    created_by: str
    created_by_origin: str
    approval_id: str | None
    approved_by: str | None
    created_at: str | None


class SourceRowsPage(BaseModel):
    rows: list[dict[str, Any]]
    total: int
    page: int
    limit: int


class SourceFileOut(BaseModel):
    split: str
    path: str
    rows: int
    bytes: int
    sha256: str
    columns: list[dict[str, Any]]
    original_name: str | None = None
    original_sha256: str | None = None


class SourceSummary(BaseModel):
    id: str
    kind: str
    state: str
    display_name: str
    repo_id: str | None
    config: str | None
    split_selection: str | None
    requested_ref: str | None
    resolved_commit: str | None
    content_hash: str | None
    licence_display: str
    licence_origin: str | None
    gated: str | None
    token_tier: str | None
    rows: int
    splits: list[str]
    suggested_target: str | None
    import_job_id: str | None
    created_by: str
    created_at: datetime
    ready_at: datetime | None


class SourceOut(SourceSummary):
    files: list[SourceFileOut]
    licence: dict[str, Any]
    detection: dict[str, Any] | None
    library_versions: dict[str, Any]
    error: dict[str, Any] | None
    deleted_by: str | None = None
    deleted_at: datetime | None = None


class SourceList(BaseModel):
    items: list[SourceSummary]
    total: int
    page: int
    limit: int


class ImportAccepted(BaseModel):
    job_id: str
    source_id: str | None = None
    existing_job: bool = False


class SourcesMeta(BaseModel):
    kinds: list[str]
    states: list[str]
    annotation_kinds: list[str]
    redistribution: list[str]
    chat_formats: list[str]
    trl_types: list[str]
    target_types: list[str]
    csv_defaults: dict[str, Any]
    limits: dict[str, int]
