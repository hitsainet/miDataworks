"""Review request and response models (FTDD 006 section 5.2): queues, items, decisions,
external candidates and audits. Bodies refuse unknown fields; a who in a body is never read
(``resolve_who`` supplies it, FR-010.28)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NonEmpty = Annotated[str, Field(min_length=1, max_length=4096)]
RowKey = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LabelReviewQueueCreate(_Strict):
    kind: Literal["label_review"]
    label_run_id: NonEmpty
    #: An explicit row list (FR-006.40, e.g. 009's disagreements); otherwise a stratified sample.
    row_keys: list[RowKey] | None = Field(None, max_length=5000)
    size: int = Field(100, ge=1, le=5000)
    seed: int | None = None


class CalibrationLabelingQueueCreate(_Strict):
    kind: Literal["calibration_labeling"]
    version_id: NonEmpty
    question: NonEmpty
    label_set: list[NonEmpty] = Field(min_length=2, max_length=64)
    size: int = Field(200, ge=1, le=5000)
    seed: int | None = None
    #: Optional: stratify the sample by this run's probability bins so the scale is covered.
    label_run_id: str | None = None
    #: Hidden by default: anchoring biases the human label calibration compares against (T-25).
    show_model_output: bool = False


class ExternalQueueCreate(_Strict):
    kind: Literal["external"]
    question: NonEmpty
    label_set: list[NonEmpty] = Field(min_length=2, max_length=64)
    external_ref: dict[str, Any] | None = None


ReviewQueueCreate = Annotated[
    LabelReviewQueueCreate | CalibrationLabelingQueueCreate | ExternalQueueCreate,
    Field(discriminator="kind"),
]


class ReviewQueueOut(BaseModel):
    id: str
    kind: str
    version_id: str | None
    label_run_id: str | None
    question: str
    question_hash: str
    label_set: list[str]
    show_model_output: bool
    sample_spec: dict[str, Any] | None
    origin_app: str | None
    external_ref: dict[str, Any] | None
    state: str
    created_by: str
    created_by_origin: str
    created_at: datetime
    items: int
    decided: int


class ReviewQueueList(BaseModel):
    items: list[ReviewQueueOut]
    total: int


class DecisionIn(_Strict):
    decision: Literal["accept", "override", "flag", "reject"]
    override_label: str | None = Field(None, max_length=4096)
    reason: str | None = Field(None, max_length=4000)


class DecisionRead(BaseModel):
    id: str
    item_id: str
    queue_id: str
    row_key: str | None
    decision: str
    override_label: str | None
    reason: str
    decided_by: str
    decided_by_origin: str
    model_output_visible: bool
    version_id: str | None
    created_at: datetime


class ReviewItemOut(BaseModel):
    id: str
    queue_id: str
    position: int
    row_key: str | None
    external_id: str | None
    payload: dict[str, Any] | None
    #: ``None`` while the queue hides model output (calibration labeling, T-25).
    model_snapshot: dict[str, Any] | None
    model_output_hidden: bool
    stratum: str | None
    #: The row's content columns, read from the version's Parquet (row items only).
    text: dict[str, Any] | None
    latest_decision: DecisionRead | None


class ReviewItemPage(BaseModel):
    items: list[ReviewItemOut]
    total: int


class Candidate(_Strict):
    external_id: Annotated[str, Field(min_length=1, max_length=256)]
    prompt: str = Field(max_length=65_536)
    completion: str = Field(max_length=65_536)
    model_output: dict[str, Any] | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)


class CandidatesIn(_Strict):
    candidates: list[Candidate] = Field(min_length=1)


class CandidateAccepted(BaseModel):
    external_id: str
    item_id: str
    created: bool


class CandidatesOut(BaseModel):
    items: list[CandidateAccepted]


class ExternalDecision(BaseModel):
    decision_id: str
    external_id: str
    item_id: str
    decision: str
    override_label: str | None
    reason: str
    decided_by: str
    decided_by_origin: str
    created_at: datetime


class ExternalDecisions(BaseModel):
    items: list[ExternalDecision]


class AuditDraw(_Strict):
    size: int = Field(100, description="Rows to sample, 50 to 100 (R-03.40)")
    #: Version columns that define the strata instead of label x probability band (007 FR-007.13).
    strata_columns: list[Annotated[str, Field(min_length=1, max_length=256)]] | None = Field(
        None, max_length=8
    )
    question: str | None = Field(None, max_length=4096)
    label_run_id: str | None = None
    seed: int | None = None


class AuditStatusOut(BaseModel):
    version_id: str
    state: Literal["none", "in_progress", "complete"]
    audit_id: str | None
    queue_id: str | None
    size: int | None
    decided: int
    strata: dict[str, Any] | None
    result: dict[str, Any] | None
