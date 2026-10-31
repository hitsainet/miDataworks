"""Feature 001 request and response models (001 FTDD section 5.2).

``repo_id`` follows the one rule shared with the frontend (``docs/schemas/hf-repo-id-cases.json``);
a malformed ID is refused here, before any network call. ``access_token`` is a ``SecretStr``,
normalised so blank or ``none`` means absent; it is never echoed in a response.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

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


MAX_DATA_FILES = 64
#: The most rows one slice keeps per split (the plan's own largest size is 100,000).
MAX_SLICE_ROWS = 10_000_000


class HfImportRequest(_HfRequest):
    confirm_large: bool = False
    #: 017 FR-017.31 (TD-017.20): the first ``max_rows`` rows (per split) of the named
    #: ``data_files`` at the pinned commit. Recorded on the source as ``import_slice``; a slice is
    #: its own source (part of the identical-import match).
    max_rows: int | None = Field(default=None, ge=1, le=MAX_SLICE_ROWS)
    data_files: list[Annotated[str, Field(min_length=1, max_length=512)]] | None = Field(
        default=None, min_length=1, max_length=MAX_DATA_FILES
    )

    @field_validator("data_files")
    @classmethod
    def _repo_relative(cls, value: list[str] | None) -> list[str] | None:
        """A data file of a Hub repository, as ``load_dataset(data_files=...)`` names it:
        repo-relative, no ``..`` segment, no leading slash, no control character (globs allowed)."""
        for item in value or []:
            if item.startswith("/") or ".." in item.split("/") or any(ord(c) < 32 for c in item):
                raise ValueError(
                    f"data file {item!r} must be a path inside the repository (no leading /, no ..)"
                )
        return value

    def import_slice(self) -> dict[str, Any] | None:
        """``{"max_rows"?, "data_files"?}`` as recorded, or None for the whole dataset."""
        out: dict[str, Any] = {}
        if self.max_rows is not None:
            out["max_rows"] = self.max_rows
        if self.data_files:
            out["data_files"] = sorted(set(self.data_files))
        return out or None


class HfSearchRequest(BaseModel):
    """Find candidate Hub datasets (PII run G2). Uses the stored token, never takes one."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    #: Hub licence ids (``mit``, ``cc-by-4.0``, ``apache-2.0`` ...); any of them matches.
    licences: list[str] = Field(default_factory=list, max_length=5)
    limit: int = Field(20, ge=1, le=100)


_ColumnName = Annotated[str, Field(min_length=1, max_length=255)]


class SourceProfileRequest(BaseModel):
    """Whole-split statistics of a ready source (PII run G2)."""

    model_config = ConfigDict(extra="forbid")

    split: str | None = Field(None, max_length=128)
    #: Columns whose value shares to count (a label, a "has PII" flag, a locale ...).
    value_columns: list[_ColumnName] = Field(default_factory=list, max_length=10)
    #: Columns whose lengths to describe; the detected text columns when omitted.
    text_columns: list[_ColumnName] | None = Field(None, max_length=10)
    top: int = Field(20, ge=1, le=200)


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
    #: 015 FR-015.12: recorded user traffic; ``recognised`` (miStudio's spot-check export) or
    #: ``operator`` (the operator's mark, D56). Every version built from it carries the
    #: ``session_data`` caveat.
    session_data: bool = False
    session_data_basis: str | None = None
    #: 017 FR-017.31: the slice this Hub source holds (``max_rows``, ``data_files``), or None.
    import_slice: dict[str, Any] | None = None


class SessionDataIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_data: bool


class SourceOut(SourceSummary):
    files: list[SourceFileOut]
    licence: dict[str, Any]
    detection: dict[str, Any] | None
    library_versions: dict[str, Any]
    #: D53: the miDataworks publish (repo, commit, version) this source was imported from, or None.
    lineage: dict[str, Any] | None = None
    error: dict[str, Any] | None
    deleted_by: str | None = None
    deleted_at: datetime | None = None


class SourceList(BaseModel):
    items: list[SourceSummary]
    total: int
    page: int
    limit: int


class IndexSourceIn(BaseModel):
    """A source made from nothing: ``rows`` integers in one column (e2e run G1, 2026-10-21)."""

    model_config = ConfigDict(extra="forbid")

    rows: int = Field(ge=1, le=10_000_000)
    column: str = Field("idx", pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
    start: int = Field(0, ge=0, le=9_007_199_254_740_991)
    split: str = Field("train", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    display_name: str | None = Field(default=None, max_length=200)


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
