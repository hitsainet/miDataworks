"""Request, response and template bodies for feature 007 (FTDD 007 section 5.2; FTID 007 2.1).

Every request body is ``extra="forbid"``. A steering strength must be a finite number and never a
boolean (``true`` would become 1.0 silently; mirrors miLLM FR-28.1.1), and a feature index may
appear once (miLLM FR-28.1.6).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: ``detector`` only with mode ``minimal_pairs`` (009 FR-009.60; validated below).
TargetType = Literal["sft", "kto", "grpo_prompt", "dpo", "detector"]


def _not_bool(value: Any, name: str) -> Any:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number, not a boolean")
    return value


# --- templates ------------------------------------------------------------------------------


class Sampling(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(0.8, ge=0.0, le=2.0)
    top_p: float = Field(1.0, gt=0.0, le=1.0)
    max_tokens: int = Field(512, gt=0, le=8192)


class TemplateBody(BaseModel):
    """A generation template: prompt text with ``{column}`` placeholders, sampling, structure."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=20_000)
    system: str | None = Field(None, max_length=20_000)
    sampling: Sampling = Field(default_factory=Sampling)
    structured_output: Literal["none", "json_schema"] = "none"
    json_schema: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _schema_when_structured(self) -> TemplateBody:
        if self.structured_output == "json_schema" and not self.json_schema:
            raise ValueError("structured_output json_schema needs a json_schema")
        if self.structured_output == "none" and self.json_schema is not None:
            raise ValueError("a json_schema is sent only with structured_output json_schema")
        return self


class TemplateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9_./-]{0,119}$")
    kind: Literal["expand", "respond"]
    description: str | None = Field(None, max_length=2000)
    body: TemplateBody


class TemplateClone(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: TemplateBody | None = None
    description: str | None = Field(None, max_length=2000)


class TemplateOut(BaseModel):
    id: str
    name: str
    version: int
    ref: str
    kind: str
    description: str | None
    body: dict[str, Any]
    content_hash: str
    builtin: bool
    cloned_from: str | None
    used: bool
    placeholders: list[str]
    created_by: str
    created_by_origin: str
    created_at: datetime


class TemplateList(BaseModel):
    items: list[TemplateOut]
    total: int
    page: int
    limit: int


# --- steering settings ----------------------------------------------------------------------


class InlineFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    strength: float

    @field_validator("index", mode="before")
    @classmethod
    def _index_not_bool(cls, value: Any) -> Any:
        return _not_bool(value, "features[].index")

    @field_validator("strength", mode="before")
    @classmethod
    def _strength_not_bool(cls, value: Any) -> Any:
        return _not_bool(value, "features[].strength")

    @field_validator("strength")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("features[].strength must be a finite number")
        return value


class NoSteering(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["none"] = "none"


class ProfileSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["profile"]
    profile_name: str = Field(min_length=1, max_length=200)


class InlineSetting(BaseModel):
    """An inline set. ``sae_id`` is required here although miLLM may infer it: without it the
    steering-set hash cannot be computed, and every response would be a hash mismatch
    (deviation from FTDD 007 section 5.2, recorded)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["inline"]
    sae_id: str = Field(min_length=1, max_length=100)
    features: list[InlineFeature] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def _unique(self) -> InlineSetting:
        seen: set[int] = set()
        for feature in self.features:
            if feature.index in seen:
                raise ValueError(f"features lists index {feature.index} more than once")
            seen.add(feature.index)
        return self


SteeringSetting = Annotated[
    NoSteering | ProfileSetting | InlineSetting, Field(discriminator="kind")
]


# --- runs -----------------------------------------------------------------------------------


class GenerationRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``minimal_pairs`` (009 FR-009.60): one minimal edit per seed row, written into the prompt
    #: column of a ``detector`` dataset; the minimal-pair chain verifies each with a judge run.
    mode: Literal["standard", "steered_pairs", "minimal_pairs"] = "standard"
    input_version_id: str = Field(min_length=1, max_length=64)
    prompt_column: str = Field(min_length=1, max_length=200)
    seed_splits: list[str] = Field(min_length=1, max_length=32)
    sample_size: int = Field(ge=1, le=100_000)
    seed: int | None = Field(None, ge=0, lt=2_147_483_648)
    n_responses: int = Field(1, ge=1, le=16)
    expand_template_id: str | None = Field(None, max_length=40)
    respond_template_id: str | None = Field(None, max_length=40)
    generator_setting: SteeringSetting = Field(default_factory=NoSteering)
    setting_a: SteeringSetting | None = None
    setting_b: SteeringSetting | None = None
    chosen_side: Literal["a", "b"] | None = None
    target_type: TargetType

    @model_validator(mode="after")
    def _mode_shape(self) -> GenerationRunCreate:
        if self.mode == "steered_pairs":
            if self.setting_a is None or self.setting_b is None:
                raise ValueError("a steered-pair run needs setting_a and setting_b")
            if self.chosen_side is None:
                raise ValueError("a steered-pair run needs chosen_side ('a' or 'b')")
            if self.n_responses != 1:
                raise ValueError("a steered-pair run generates one response per side")
            if self.respond_template_id is None:
                raise ValueError("a steered-pair run needs a respond template")
            if self.target_type != "dpo":
                raise ValueError("steered pairs build DPO rows: target_type must be 'dpo'")
        else:
            if self.setting_a is not None or self.setting_b is not None or self.chosen_side:
                raise ValueError("setting_a, setting_b and chosen_side belong to steered pairs")
        if self.mode == "minimal_pairs":
            if self.respond_template_id is None or self.expand_template_id is not None:
                raise ValueError("a minimal-pair run edits each seed: one respond template only")
            if self.n_responses != 1:
                raise ValueError("a minimal-pair run writes one counterpart per seed row")
            if self.target_type != "detector":
                raise ValueError(
                    "minimal pairs build detector rows: target_type must be 'detector'"
                )
        elif self.target_type == "detector":
            raise ValueError("a detector dataset takes generated rows only as minimal pairs")
        if self.expand_template_id is None and self.respond_template_id is None:
            raise ValueError("name an expand template, a respond template, or both")
        if len(set(self.seed_splits)) != len(self.seed_splits):
            raise ValueError("seed_splits lists a split more than once")
        return self


class HeldOutStatus(BaseModel):
    present: bool
    splits: list[str]
    origin_version_id: str | None
    next_step: dict[str, Any] | None = None


class SnapshotOut(BaseModel):
    side: str
    kind: str
    profile_id: str | None
    profile_name: str | None
    profile_updated_at: str | None
    intensity: float | None
    model_id: str | None
    sae_id: str | None
    layer: int | None
    features: list[list[float]]
    sent_features: list[list[float]]
    set_hash: str | None
    snapshot_hash: str | None = None


class PlanOut(BaseModel):
    """``POST /generation-runs/plan``: guards passed, counts and identities; nothing written."""

    mode: str
    target_type: str
    held_out: HeldOutStatus
    seed_rows_available: int
    seed_rows_selected: int
    responses_per_prompt: int
    expected_requests: int
    stages: list[str]
    engine_path: str
    server_kind: str
    resident_model: str | None
    model_revision: str | None
    generation_endpoint: dict[str, Any]
    snapshots: list[SnapshotOut]
    differing_index: int | None
    generator_identities: list[dict[str, str]]
    judge_identity: dict[str, str] | None
    independence: Literal["independent", "not_checked"]
    pinned_expected: bool | None
    warnings: list[dict[str, Any]]


class GenerationRunOut(BaseModel):
    id: str
    mode: str
    target_type: str
    state: str
    input_version_id: str
    held_out_origin_version_id: str
    held_out_splits: list[str]
    prompt_column: str
    seed_splits: list[str]
    sample_size: int
    seed: int
    n_responses: int
    expand_template_id: str | None
    respond_template_id: str | None
    stages: list[str]
    generation_endpoint: dict[str, Any]
    server_kind: str
    generator_identities: list[dict[str, Any]]
    judge_identity: dict[str, Any] | None
    independence_checked_at: datetime | None
    chosen_side: str | None
    engine_path: str
    steering_supported: bool
    pinned: bool | None
    revision_reported: bool | None
    model_revision: str | None
    failure_reason: str | None
    error: dict[str, Any] | None
    warnings: list[dict[str, Any]]
    counts: dict[str, Any]
    snapshots: list[SnapshotOut]
    started_by: str
    started_by_origin: str
    created_at: datetime
    completed_at: datetime | None
    job_ids: list[str]
    current_job_id: str | None
    room: str
    resumable: bool


class GenerationRunList(BaseModel):
    items: list[GenerationRunOut]
    total: int
    page: int
    limit: int


class RecordOut(BaseModel):
    stage: str
    record_index: int
    seed_position: int
    row_key: str | None
    seed_row_key: str
    prompt_row_key: str
    response_index: int
    side: str | None
    template_id: str | None
    model_id: str | None
    model_revision: str | None
    requested_set_hash: str | None
    reported_steering: str | None
    steering_check: str
    check_reasons: list[str]
    seed_sent: int | None
    seed_confirmed: bool | None
    latency_ms: int | None
    finish_reason: str | None
    outcome: str
    reason_code: str | None
    chunk_index: int
    prompt: str | None = None
    text: str | None = None


class RecordPage(BaseModel):
    items: list[RecordOut]
    total: int
    page: int
    limit: int


class PairOut(BaseModel):
    prompt_row_key: str
    pair_index: int
    record_index_a: int
    record_index_b: int
    chosen_side: str
    shared_seed: int
    prompt: str | None = None
    text_a: str | None = None
    text_b: str | None = None
    steering_a: str | None = None
    steering_b: str | None = None


class PairPage(BaseModel):
    items: list[PairOut]
    total: int
    page: int
    limit: int


# --- compare, independence, preview -----------------------------------------------------------


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_a: SteeringSetting
    setting_b: SteeringSetting


class CompareOut(BaseModel):
    one_axis: bool
    differing: list[dict[str, Any]]
    differing_index: int | None
    not_comparable: list[str]
    snapshots: list[SnapshotOut]
    message: str
    code: str | None


class IndependenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["standard", "steered_pairs", "minimal_pairs"] = "standard"
    generator_setting: SteeringSetting = Field(default_factory=NoSteering)
    setting_a: SteeringSetting | None = None
    setting_b: SteeringSetting | None = None


class IndependenceOut(BaseModel):
    independent: bool | None
    judge_identity: dict[str, str] | None
    generator_identities: list[dict[str, str]]
    conflicts: list[dict[str, Any]]
    inherited_from: str | None
    message: str


class PreviewRequest(BaseModel):
    """Either bare ``prompts`` (a template that reads only ``{prompt}``), or REAL seed rows of a
    version (``input_version_id`` + ``prompt_column`` + ``seed_splits``), chosen and rendered the
    way a run chooses and renders them. A preview never renders a placeholder empty."""

    model_config = ConfigDict(extra="forbid")

    prompts: list[str] | None = Field(None, min_length=1, max_length=5)
    input_version_id: str | None = Field(None, min_length=1, max_length=64)
    prompt_column: str | None = Field(None, min_length=1, max_length=200)
    seed_splits: list[str] | None = Field(None, min_length=1, max_length=32)
    #: Seed rows to draw (default: the preview's maximum); rows mode only.
    sample_size: int | None = Field(None, ge=1, le=5)
    respond_template_id: str | None = Field(None, max_length=40)
    setting: SteeringSetting = Field(default_factory=NoSteering)
    #: The sampling seed and, in rows mode, the seed-row selection seed (as a run's ``seed``).
    seed: int | None = Field(None, ge=0, lt=2_147_483_648)

    @field_validator("prompts")
    @classmethod
    def _non_empty(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if any(not p.strip() for p in value):
            raise ValueError("each prompt needs text")
        if any(len(p) > 20_000 for p in value):
            raise ValueError("a prompt is longer than 20,000 characters")
        return value

    @model_validator(mode="after")
    def _one_source(self) -> PreviewRequest:
        rows = self.input_version_id is not None
        if rows == (self.prompts is not None):
            raise ValueError("give either prompts or input_version_id (seed rows), not both")
        if rows and (self.prompt_column is None or self.seed_splits is None):
            raise ValueError("a preview from seed rows needs prompt_column and seed_splits")
        if not rows and (
            self.prompt_column is not None
            or self.seed_splits is not None
            or self.sample_size is not None
        ):
            raise ValueError("prompt_column, seed_splits and sample_size need input_version_id")
        return self


class PreviewItem(BaseModel):
    prompt: str
    #: The seed row's key, in a preview drawn from seed rows; ``None`` for a bare prompt.
    row_key: str | None = None
    text: str | None
    model: str | None
    finish_reason: str | None
    reported_steering: str | None
    steering_check: str
    check_reasons: list[str]
    seed_sent: int | None
    seed_confirmed: bool | None
    latency_ms: int | None
    error: str | None


class PreviewOut(BaseModel):
    items: list[PreviewItem]
    snapshot: SnapshotOut
    server_kind: str
    model_id: str
    stopped_early: bool
    #: The seed that chose the seed rows (rows mode), so the same rows can be drawn again.
    selection_seed: int | None = None


class CandidateBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed: int | None = Field(None, ge=0, lt=2_147_483_648)


# --- diversity ------------------------------------------------------------------------------


class DiversityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    column: str | None = Field(None, min_length=1, max_length=200)


class DiversityAccepted(BaseModel):
    job_id: str
    room: str
    column: str
    existing_report_id: str | None = None


class DiversityReportOut(BaseModel):
    id: str
    version_id: str
    reference_version_id: str | None
    column: str
    method_hash: str
    method: dict[str, Any]
    splits: list[str]
    sample_size: int
    seed: int
    embedding_identity: dict[str, Any] | None
    clustering: dict[str, Any]
    figures: dict[str, Any]
    checks: list[dict[str, Any]]
    verdict: str
    reason: str | None
    job_id: str | None
    created_by: str
    created_by_origin: str
    created_at: datetime
