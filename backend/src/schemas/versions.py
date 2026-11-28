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


class WspLengthOut(BaseModel):
    unit: Literal["characters"]
    min: int
    max: int
    median: int


class WspMarkOut(BaseModel):
    """021 FR-021.25: a split's WSP mark as the version stores it. ``version_length`` is measured
    over the VERSION's file at build time; a manifest's ``length`` is measured over the SHIPPED
    file, which a label projection may have narrowed (two names, two meanings)."""

    role: Literal["wsp_practice", "wsp_heldout"]
    set_kind: str
    text_column: str
    min_chars: int
    version_length: WspLengthOut


class SplitOut(BaseModel):
    name: str
    held_out: bool
    rows: int
    bytes: int
    file_sha256: str
    logical_digest: str
    path: str
    #: 017 (FR-017.1, FR-017.2): set on a value-directed held-out split only - what directed it
    #: and its disjointness proof over the built files (``R-03.38 as amended, ENH-006 D10``).
    directed_by: dict[str, Any] | None = None
    proof: dict[str, Any] | None = None
    #: 017 TD-017.7: a held-out split its plan step declared and left empty (written with 0 rows).
    declared_empty: bool = False
    #: 021 FR-021.25: the split's weight-space programming (WSP) mark; null when it is not a WSP
    #: partition.
    wsp: WspMarkOut | None = None


class VersionOut(BaseModel):
    id: str
    dataset_id: str
    dataset_name: str
    target_type: str
    number: int
    state: str
    is_head: bool
    #: FU-7a gap (k): the newest later completed version built from this one or rebuilding it
    #: (same recipe); null for the head and for a version no later one replaces (a sibling built
    #: from the same input with another recipe is not a successor).
    superseded_by: int | None
    #: The dataset's head version number (its highest-numbered completed version), or null.
    head_number: int | None = None
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
    #: FU-7a gap (j): splits the build's split step named that received no row, so no file was
    #: written for them: ``{name, held_out, rows: 0, declared_by_step, written: false}``.
    empty_splits: list[dict[str, Any]] = Field(default_factory=list)
    total_rows: int
    total_bytes: int
    held_out_origin_version_id: str | None
    warnings: list[dict[str, Any]]
    drop_summary: list[dict[str, Any]]
    #: Each operator step's reports (OperatorResult.report), when it returned any. Not part of the
    #: manifest: a report can differ between a build and a rebuild (live2 finding, 2026-10-18).
    step_reports: list[dict[str, Any]] = Field(default_factory=list)
    manifest_sha256: str
    build_job_id: str
    created_by: str
    created_by_origin: str
    created_at: datetime
    deleted_by: str | None = None
    deleted_by_origin: str | None = None
    deleted_at: datetime | None = None
    delete_reason: str | None = None
    #: Feature 016 (FR-016.26): the rule sets the build ran, ``{recorded: true, rule_sets: [...]}``,
    #: or ``{recorded: false, rule_sets: null}`` for a version built before they were recorded.
    rule_sets: dict[str, Any] | None = None
    #: Feature 018 (FR-018.40, FR-018.42): the code rules the build ran, each with its approval
    #: state NOW (``revoked`` reads "built with a since-revoked code rule"), or
    #: ``{recorded: false, code_rules: null}`` for a version built before they were recorded.
    code_rules: dict[str, Any] | None = None
    #: Feature 017 (FR-017.11, FR-017.55): the definition the build was made against and the
    #: configuration it used; ``None`` for a version that is not a behaviour build.
    behaviour_definition: dict[str, Any] | None = None
    behaviour_configuration: dict[str, Any] | None = None
    #: FR-017.17: rows per genre x placement x framing x length band against the plan, worst
    #: cells first; shortfalls and a templated share over its ceiling are flagged, never refused.
    behaviour_census: dict[str, Any] | None = None
    #: 021 FR-021.20, FR-021.24 (P41): how sensitive the version's data is NOW. **The
    #: authoritative value for consumers**: re-read it before the data reaches anywhere an external
    #: model may read it, and read null (or miDataworks unreachable) as unknown, i.e. sensitive. A
    #: stored manifest is a snapshot and can understate it after a later session-data mark.
    sensitivity: Literal["sensitive", "not_sensitive"] | None = Field(
        None,
        description="How sensitive the version's data is now; the authoritative value for "
        "consumers (null = not declared, treat as sensitive). A manifest is a snapshot.",
    )
    #: The floor at build time, the declaration, each input's value and what raised it since.
    sensitivity_basis: dict[str, Any] | None = None
    #: The publishes whose stored manifest states a value less strict than ``sensitivity`` now.
    sensitivity_published_below: list[str] = Field(default_factory=list)


class WspPartitionOut(BaseModel):
    split: str
    role: Literal["wsp_practice", "wsp_heldout"]
    set_kind: str
    rows: int
    text_column: str
    min_chars: int
    #: measured over the version's file at build time; a manifest's ``length`` is the shipped one
    version_length: WspLengthOut
    file_sha256: str
    logical_digest: str


class WspPartitionsOut(BaseModel):
    """021 FR-021.26: a version's WSP partitions and its sensitivity now. A version with no marks
    answers an empty list."""

    version_id: str
    sensitivity: Literal["sensitive", "not_sensitive"] | None
    sensitivity_basis: dict[str, Any]
    partitions: list[WspPartitionOut]


class VersionSummary(BaseModel):
    id: str
    dataset_id: str
    dataset_name: str
    target_type: str
    number: int
    state: str
    is_head: bool
    #: FU-7a gap (k): the newest later completed version built from this one or rebuilding it
    #: (same recipe); null for the head and for a version no later one replaces (a sibling built
    #: from the same input with another recipe is not a successor).
    superseded_by: int | None
    #: The dataset's head version number (its highest-numbered completed version), or null.
    head_number: int | None = None
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
    #: The columns returned when the request projected them (PII run G8), else None.
    projected: list[str] | None = None


class DownloadUrlIn(BaseModel):
    """A signed, expiring download of one split (PII run G8): no credential travels, the
    signature binds the version, split, columns, format and expiry."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str] | None = Field(None, max_length=500)
    format: Literal["parquet", "jsonl"] = "parquet"
    expires_in_s: int = Field(3600, ge=60, le=86_400)
    #: 021 FR-021.14: with ``wsp_practice``, a WSP held-out split or an unmarked split is refused.
    #: A courtesy that stops an honest caller's mistake, not a boundary (R1A-D20).
    purpose: Literal["wsp_practice"] | None = None
    #: FU-7a gap (af): serve the file this publish pushed for the split (whole, Parquet), byte for
    #: byte what the Hub holds; omitted, the link serves the version's own split file as before.
    publish_id: str | None = Field(None, min_length=1, max_length=40)


class RowFilter(BaseModel):
    """One structured filter; never free SQL (FTDD 002 section 5.8)."""

    model_config = ConfigDict(extra="forbid")

    column: str = Field(min_length=1, max_length=128)
    op: Literal["eq", "ne", "lt", "le", "gt", "ge", "contains", "is_null", "not_null"]
    value: str | int | float | bool | None = None
