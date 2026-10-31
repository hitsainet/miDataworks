"""Calibration request and response models, and the ``dw.calibration-mapping/v1`` document
(FTDD 006 sections 4.3, 5.2). AUROC (area under the receiver operating characteristic curve) and
CI (confidence interval) are spelled out here once; the field descriptions use the short forms.

The mapping refuses unknown fields (``extra="forbid"``): a misspelt key would otherwise be dropped
and the set built from a different mapping than the one the operator wrote.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1, max_length=4096)]
Column = Annotated[str, Field(min_length=1, max_length=256)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumericRule(_Strict):
    """Two cut points, inclusive, with an excluded middle (the prototype's confident ends)."""

    column: Column
    rule: Literal["numeric"]
    positive_at_or_above: float
    negative_at_or_below: float

    @model_validator(mode="after")
    def _ordered(self) -> NumericRule:
        if self.negative_at_or_below >= self.positive_at_or_above:
            raise ValueError("negative_at_or_below must be below positive_at_or_above")
        return self


class CategoricalRule(_Strict):
    """Source values mapped to ``positive``, ``negative`` or a label of the set; others excluded."""

    column: Column
    rule: Literal["categorical"]
    map: dict[str, NonEmpty] = Field(min_length=1)


class Ratings(_Strict):
    column: Column
    format: Literal["digit_string", "list"]
    scale: tuple[int, int] | None = None


class ColumnRef(_Strict):
    column: Column


class Reference(_Strict):
    column: Column
    value: Any


class CalibrationMapping(_Strict):
    """``dw.calibration-mapping/v1`` (FTDD 006 section 4.3)."""

    schema_: Literal["dw.calibration-mapping/v1"] = Field(alias="schema")
    human_label: Annotated[NumericRule | CategoricalRule, Field(discriminator="rule")]
    ratings: Ratings | None = None
    group: ColumnRef | None = None
    strata: list[Column] = Field(default_factory=list, max_length=16)
    reference: Reference | None = None

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @model_validator(mode="after")
    def _shape(self) -> CalibrationMapping:
        if self.reference is not None and self.group is None:
            raise ValueError("a reference selector needs a group column")
        if self.ratings is not None and not isinstance(self.human_label, NumericRule):
            raise ValueError("a per-rater column needs a numeric human-label rule")
        return self

    def document(self) -> dict[str, Any]:
        """The canonical document stored and hashed (aliases restored, absent keys omitted)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class CalibrationSetImport(_Strict):
    version_id: NonEmpty = Field(description="The imported version whose rows carry human labels")
    question: NonEmpty = Field(description="Exact question text; must equal the label run's")
    label_set: list[NonEmpty] = Field(
        min_length=2, max_length=64, description="Ordered labels; the first is the positive class"
    )
    mapping: CalibrationMapping


class CalibrationSetFromReview(_Strict):
    queue_id: NonEmpty = Field(description="A calibration-labeling review queue")


class CalibrationSetPreview(BaseModel):
    counts: dict[str, int]
    ratings_sorted: bool | None
    warnings: list[str]
    sample: list[dict[str, Any]]
    mapping_hash: str


class CalibrationSetOut(BaseModel):
    id: str
    version_id: str
    question: str
    question_hash: str
    label_set: list[str]
    source_kind: str
    source_queue_id: str | None
    mapping: dict[str, Any]
    mapping_hash: str
    ratings_sorted: bool | None
    licence_class: str
    counts: dict[str, Any]
    provenance: dict[str, Any]
    created_by: str
    created_by_origin: str
    created_at: datetime


class CalibrationSetList(BaseModel):
    items: list[CalibrationSetOut]
    total: int


class CalibrationRecordStart(_Strict):
    label_run_id: NonEmpty
    calibration_set_id: NonEmpty


class JobStarted(BaseModel):
    job_id: str
    room: str


class CheckOut(BaseModel):
    check_id: str
    check_version: int
    metric_id: str
    result: str
    statistic: dict[str, Any]
    rule: str
    reason: str | None


class VerdictOut(BaseModel):
    verdict: Literal["passes", "fails", "invalid", "insufficient"]
    rule: Literal["operator_target", "held_out_rater", "default_c3"] | None
    numbers: dict[str, Any]
    target_id: str | None


class CalibrationRecordOut(BaseModel):
    id: str
    calibration_set_id: str
    label_run_id: str
    question: str
    question_hash: str
    labeler_identity: dict[str, Any]
    labeler_identity_hash: str
    labeler_fingerprint: str
    score_kind: str
    metrics: dict[str, Any]
    metrics_sha256: str
    checks: list[CheckOut]
    verdict: VerdictOut
    warnings: list[dict[str, Any]]
    settings: dict[str, Any]
    conformance: dict[str, Any] | None
    created_by: str
    created_by_origin: str
    created_at: datetime


class CalibrationRecordList(BaseModel):
    items: list[CalibrationRecordOut]
    total: int


class AurocRef(_Strict):
    value: float
    ci_low: float
    ci_high: float
    n: int


class CalibrationSetRef(_Strict):
    id: str
    licence_class: Literal["permits_redistribution", "private_only", "forbids_redistribution"]
    rows_shipped: Literal[False] = False


class CalibrationStatus(_Strict):
    """Field for field 008's ``CalibrationRef`` minus ``labeler_fingerprint`` (008 supplies it)."""

    status: Literal["recorded", "none_recorded"]
    record_id: str | None
    verdict: Literal["passes", "fails", "invalid", "insufficient"] | None
    rule: Literal["operator_target", "held_out_rater", "default_c3"] | None
    auroc: AurocRef | None
    calibration_set: CalibrationSetRef | None


class TargetIn(_Strict):
    question: NonEmpty
    target: float = Field(gt=0.5, lt=1, description="AUROC lower bound the labeler must reach")


class TargetOut(BaseModel):
    id: str
    question_hash: str
    question: str
    target: float
    set_by: str
    set_by_origin: str
    approval_id: str | None
    approved_by: str | None
    created_at: datetime


class TargetsOut(BaseModel):
    question_hash: str
    current: TargetOut | None
    default_lower_bound: float
    history: list[TargetOut]
