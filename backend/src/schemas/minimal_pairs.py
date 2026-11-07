"""Minimal-pair chains (009 FR-009.60 - FR-009.64; operator decision 2026-10-07)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .generation import NoSteering, PlanOut, SteeringSetting

_FORBID = ConfigDict(extra="forbid")


class MinimalPairChainCreate(BaseModel):
    """``POST /minimal-pair-chains`` (and ``/plan``): every stage's input, given once."""

    model_config = _FORBID

    input_version_id: str = Field(min_length=1, max_length=64)
    #: The text column each seed is edited in; the counterpart is written into it too.
    text_column: str = Field(min_length=1, max_length=200)
    seed_splits: list[str] = Field(min_length=1, max_length=32)
    sample_size: int = Field(ge=1, le=100_000)
    seed: int | None = Field(None, ge=0, lt=2_147_483_648)
    #: A ``respond`` generation template asking for the minimal flip (``minimal-pair-v1`` cloned).
    respond_template_id: str = Field(min_length=1, max_length=40)
    generator_setting: SteeringSetting = Field(default_factory=NoSteering)
    #: The judge's rubric, pinned by its ID (a rubric version never changes).
    rubric_id: str = Field(min_length=1, max_length=40)
    #: The rubric input field the text goes into; omitted when the rubric has one.
    judge_field: str | None = Field(None, min_length=1, max_length=200)
    #: The verdict the judge must give the seed, and the one it must give the counterpart.
    flip_from: str = Field(min_length=1, max_length=128)
    flip_to: str = Field(min_length=1, max_length=128)
    judge_seed: int | None = Field(None, ge=0, le=2**32 - 1)
    max_edit_chars: int | None = Field(None, ge=1, le=1_000_000)
    max_edit_words: int | None = Field(None, ge=1, le=100_000)

    @model_validator(mode="after")
    def _distinct(self) -> MinimalPairChainCreate:
        if self.flip_from == self.flip_to:
            raise ValueError("flip_from and flip_to must be different verdicts")
        if len(set(self.seed_splits)) != len(self.seed_splits):
            raise ValueError("seed_splits lists a split more than once")
        return self


class JudgePlan(BaseModel):
    role: Literal["judge"] = "judge"
    model_id: str
    identity: dict[str, str]
    rubric_id: str
    rubric_ref: str
    allowed_verdicts: list[str]
    field_map: dict[str, str]


class MinimalPairChainPlan(BaseModel):
    """What the chain will do; nothing is written (every refusal has already been checked)."""

    stages: list[str]
    generation: PlanOut
    judge: JudgePlan
    flip_from: str
    flip_to: str
    max_edit_chars: int | None
    max_edit_words: int | None
    #: At most two judged rows per seed (the seed and its counterpart).
    judge_rows_at_most: int


class ChainStage(BaseModel):
    stage: Literal["generate", "scope", "judge", "pair"]
    #: ``generation_run``, ``version_build`` or ``label_run``.
    kind: str
    run_id: str | None
    job_id: str | None
    version_id: str | None
    #: The stage's own state: ``pending`` until started, then its run's or job's state.
    state: str


class MinimalPairChainOut(BaseModel):
    id: str
    state: str
    stage: str
    input_version_id: str
    request: dict[str, Any]
    stages: list[ChainStage]
    failed_stage: str | None
    error: dict[str, Any] | None
    counts: dict[str, Any]
    pair_version_id: str | None
    resumable: bool
    started_by: str
    started_by_origin: str
    acting_by: str
    acting_origin: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class MinimalPairChainList(BaseModel):
    items: list[MinimalPairChainOut]
    total: int
    page: int
    limit: int
