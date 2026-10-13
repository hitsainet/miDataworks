"""Feature 005 bodies: templates, rubrics, label-run requests and the label-run contract.

Every model refuses unknown keys (``extra="forbid"``) so a misspelled field fails rather than being
ignored, and none uses a validation ``alias`` (an alias renames on serialisation too; miStudio
memory "Pydantic alias renames on serialisation").
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

_FORBID = ConfigDict(extra="forbid")

TEMPLATE_EXPORT_FORMAT = "midataworks.decision-template/v1"
RUBRIC_EXPORT_FORMAT = "midataworks.rubric/v1"


# --- decision template bodies (FR-005.11 – FR-005.15) -----------------------------------------


class OpenAIScoringTemplate(BaseModel):
    """Next-token log-probabilities restricted to verbalizer token IDs (``openai_scoring``)."""

    model_config = _FORBID

    kind: Literal["openai_scoring"]
    variant: Literal["completions", "chat"]
    #: A registered renderer (``jev.noul_bare_v1``) or a format string over ``input_fields`` and
    #: ``{question}``.
    render: str = Field(min_length=1)
    input_fields: list[str] = Field(min_length=1)
    #: In verbalizer order within the decision slot, e.g. ``["false", "true"]``.
    label_set: list[str] = Field(min_length=2)
    #: The label whose probability is P for the two-threshold rule (binary sets).
    positive_class: str | None = None
    #: Which slot of ``slots`` answers this template's question (JEV: ``noul``).
    decision_kind: str = Field(min_length=1)
    verbalizer_ids: list[int] = Field(min_length=2)
    slots: dict[str, tuple[int, int]]
    bias: list[float]
    temperature: dict[str, float]
    tokenization: dict[str, bool] = Field(default_factory=dict)
    bound_model_id: str = Field(min_length=1)
    bound_model_revision: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> OpenAIScoringTemplate:
        if self.decision_kind not in self.slots:
            raise ValueError(f"decision_kind {self.decision_kind!r} is not one of the slots")
        start, end = self.slots[self.decision_kind]
        if not (0 <= start < end <= len(self.verbalizer_ids)):
            raise ValueError("the decision slot lies outside verbalizer_ids")
        if end - start != len(self.label_set):
            raise ValueError("the decision slot must hold one verbalizer per label")
        if len(self.bias) != len(self.verbalizer_ids):
            raise ValueError("bias must hold one value per verbalizer id")
        temperature = self.temperature.get(self.decision_kind)
        if temperature is None or not temperature > 0:
            raise ValueError("temperature for the decision kind must be positive")
        if self.positive_class is not None and self.positive_class not in self.label_set:
            raise ValueError("positive_class must be one of label_set")
        if len(self.label_set) == 2 and self.positive_class is None:
            raise ValueError("a binary template must name its positive_class")
        return self


class TEIClassificationTemplate(BaseModel):
    """Hugging Face text classification on a TEI server's ``/predict`` (FR-005.15)."""

    model_config = _FORBID

    kind: Literal["tei_classification"]
    render: str = Field(min_length=1)
    input_fields: list[str] = Field(min_length=1)
    label_set: list[str] = Field(min_length=2)
    positive_class: str | None = None
    #: The model's own label name -> the run's label.
    label_map: dict[str, str] = Field(min_length=1)
    bound_model_id: str | None = None
    bound_model_revision: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> TEIClassificationTemplate:
        unknown = sorted(set(self.label_map.values()) - set(self.label_set))
        if unknown:
            raise ValueError(f"label_map maps to labels outside label_set: {unknown}")
        if self.positive_class is not None and self.positive_class not in self.label_set:
            raise ValueError("positive_class must be one of label_set")
        if len(self.label_set) == 2 and self.positive_class is None:
            raise ValueError("a binary template must name its positive_class")
        return self


class PluginTemplate(BaseModel):
    """A classifier supplied by an allowlisted ``midataworks.classifiers`` entry point (FR-005.8)."""

    model_config = _FORBID

    kind: Literal["plugin"]
    entry_point: str = Field(min_length=1)
    render: str = Field(min_length=1)
    input_fields: list[str] = Field(min_length=1)
    label_set: list[str] = Field(min_length=2)
    positive_class: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    bound_model_id: str | None = None
    bound_model_revision: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> PluginTemplate:
        if self.positive_class is not None and self.positive_class not in self.label_set:
            raise ValueError("positive_class must be one of label_set")
        if len(self.label_set) == 2 and self.positive_class is None:
            raise ValueError("a binary template must name its positive_class")
        return self


TemplateBody = Annotated[
    OpenAIScoringTemplate | TEIClassificationTemplate | PluginTemplate, Field(discriminator="kind")
]


class DecisionTemplateCreate(BaseModel):
    model_config = _FORBID

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_./-]*$")
    body: TemplateBody


class DecisionTemplateClone(BaseModel):
    """A new version of a template; ``body`` omitted keeps the body (a copy to edit later)."""

    model_config = _FORBID

    body: TemplateBody | None = None


class DecisionTemplateExport(BaseModel):
    model_config = _FORBID

    format: Literal["midataworks.decision-template/v1"]
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_./-]*$")
    version: int = Field(ge=1)
    body: TemplateBody


class DecisionTemplateOut(BaseModel):
    id: str
    name: str
    version: int
    ref: str
    content_hash: str
    protocol: str
    variant: str | None
    bound_model_id: str | None
    bound_model_revision: str | None
    body: dict[str, Any]
    used: bool
    created_by: str
    created_by_origin: str
    created_at: datetime


# --- rubrics (FR-005.16) ----------------------------------------------------------------------


class RubricMessage(BaseModel):
    model_config = _FORBID

    role: Literal["system", "user", "assistant"]
    #: A format string over the rubric's input fields (pairwise: also ``{a}`` and ``{b}``).
    content: str = Field(min_length=1)


class RubricBody(BaseModel):
    model_config = _FORBID

    style: Literal["pointwise", "pairwise", "binary", "stepwise"]
    messages: list[RubricMessage] = Field(min_length=1)
    input_fields: list[str] = Field(min_length=1)
    axes: list[str] = Field(default_factory=list)
    parser: Literal["verdict_line_v1", "json_v1"]
    allowed_verdicts: list[str] = Field(min_length=1)
    #: ``json_v1``: the answer's JSON Schema; sent as ``response_format`` when honoured.
    json_schema: dict[str, Any] | None = None
    #: Pairwise: the two candidate fields, rendered as ``{a}`` and ``{b}``.
    pair_fields: tuple[str, str] | None = None
    #: Pairwise: how a verdict read in the swapped order maps back (``{"A": "B", "B": "A"}``).
    swap_map: dict[str, str] | None = None

    @model_validator(mode="after")
    def _consistent(self) -> RubricBody:
        if self.parser == "json_v1" and self.json_schema is None:
            raise ValueError("a json_v1 rubric needs json_schema")
        if self.style == "pairwise":
            if self.pair_fields is None or self.swap_map is None:
                raise ValueError("a pairwise rubric needs pair_fields and swap_map")
            if set(self.swap_map) - set(self.allowed_verdicts):
                raise ValueError("swap_map keys must be allowed verdicts")
        return self


class RubricCreate(BaseModel):
    model_config = _FORBID

    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_./-]*$")
    body: RubricBody


class RubricClone(BaseModel):
    model_config = _FORBID

    body: RubricBody | None = None


class RubricExport(BaseModel):
    model_config = _FORBID

    format: Literal["midataworks.rubric/v1"]
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_./-]*$")
    version: int = Field(ge=1)
    body: RubricBody


class RubricOut(BaseModel):
    id: str
    name: str
    version: int
    ref: str
    content_hash: str
    style: str
    body: dict[str, Any]
    used: bool
    created_by: str
    created_by_origin: str
    created_at: datetime


# --- label-run requests -----------------------------------------------------------------------


class Sampling(BaseModel):
    model_config = _FORBID

    temperature: float | None = Field(default=None, ge=0, le=2)
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    max_tokens: int | None = Field(default=None, ge=1, le=8192)


class RowFilter(BaseModel):
    """FR-005.53. ``source`` is the code's name for imported rows (002 ``Origin.SOURCE``);
    ``imported`` (the FPRD's word) is accepted and recorded as ``source``."""

    model_config = _FORBID

    origin: Literal["generated", "source", "imported"]

    def normalised(self) -> dict[str, str]:
        return {"origin": "source" if self.origin == "imported" else self.origin}


class ProbeSpec(BaseModel):
    """A probe-verdict run's probe (009 FR-009.45, FR-009.47): one miLLM probe, one window."""

    model_config = _FORBID

    #: The probe's ID in miLLM (``pr_...``), as miLLM lists it.
    probe_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    #: miLLM's window (``all`` reproduces miStudio's own scope; FR-009.77).
    window: Literal["all", "prompt", "response", "last_user"] = "all"


class FeatureTagSpec(BaseModel):
    """A feature-tag run's read (009 FR-009.65 - FR-009.68): one SAE attached in miLLM."""

    model_config = _FORBID

    #: The attached SAE (miLLM's ID); omitted = the one SAE attached (refused when several are).
    sae_id: str | None = Field(default=None, min_length=1, max_length=128)
    top_k: int = Field(ge=1, le=1024)
    #: Prompt positions to read (scoring mode generates nothing, so ``completion`` is not offered).
    positions: Literal["last", "prompt", "all"] = "last"
    #: Restrict the candidates to these SAE feature indices.
    features: list[int] | None = Field(default=None, min_length=1, max_length=1024)


class LabelRunStart(BaseModel):
    """``POST /label-runs`` and ``GET /label-runs/plan`` (FTDD 005 section 5.2).

    ``role: "probe"`` starts a probe-verdict run (009, operator decision 2026-10-07): the endpoint
    is miLLM, ``probe`` names the probe and window, ``field_map`` maps ``text`` or ``messages`` to
    one column, and no template, rubric, question or thresholds apply (the bar is the probe's).
    """

    model_config = _FORBID

    input_version_id: str = Field(min_length=1, max_length=64)
    role: Literal["classifier", "judge", "probe", "features"]
    probe: ProbeSpec | None = None
    #: ``role: "features"``: a feature-tag run (009; same decision as probe verdicts).
    features: FeatureTagSpec | None = None
    template_id: str | None = Field(default=None, max_length=40)
    rubric_id: str | None = Field(default=None, max_length=40)
    question: str | None = Field(default=None, max_length=4000)
    #: Template input field -> version column.
    field_map: dict[str, str] = Field(default_factory=dict)
    threshold_positive: float | None = None
    threshold_negative: float | None = None
    min_top_probability: float | None = Field(default=None, ge=0, le=1)
    positive_label: str | None = Field(default=None, max_length=128)
    negative_label: str | None = Field(default=None, max_length=128)
    sampling: Sampling | None = None
    chunk_size: int | None = Field(default=None, ge=1, le=10_000)
    row_filter: RowFilter | None = None
    #: A completed keep-share preview to record as this run's estimate (FR-005.19).
    keep_share_job_id: str | None = Field(default=None, max_length=40)
    #: ``batch`` sends the rows through miLLM's Batch API (FR-005.35), unpacked, under the lease.
    transport: Literal["single", "batch"] = "single"
    #: Probe-verdict runs only: run the reproduction check again although the same check already
    #: failed against the same target, saying why (recorded on the run; 2026-10-08 finding 3).
    reproduction_retry_reason: str | None = Field(default=None, min_length=1, max_length=2000)


class SampleRequest(BaseModel):
    """``POST /labeling/sample``: at most ``LABEL_SAMPLE_MAX_ROWS`` rows, nothing written."""

    model_config = _FORBID

    input_version_id: str = Field(min_length=1, max_length=64)
    role: Literal["classifier", "judge"]
    template_id: str | None = Field(default=None, max_length=40)
    rubric_id: str | None = Field(default=None, max_length=40)
    question: str | None = Field(default=None, max_length=4000)
    field_map: dict[str, str] = Field(default_factory=dict)
    threshold_positive: float | None = None
    threshold_negative: float | None = None
    min_top_probability: float | None = Field(default=None, ge=0, le=1)
    rows: int = Field(default=5, ge=1)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    row_filter: RowFilter | None = None


class KeepShareRequest(BaseModel):
    """``POST /labeling/keep-share``: a ``label_preview`` job (FR-005.19)."""

    model_config = _FORBID

    input_version_id: str = Field(min_length=1, max_length=64)
    role: Literal["classifier"] = "classifier"
    template_id: str = Field(min_length=1, max_length=40)
    question: str | None = Field(default=None, max_length=4000)
    field_map: dict[str, str] = Field(default_factory=dict)
    threshold_positive: float | None = None
    threshold_negative: float | None = None
    min_top_probability: float | None = Field(default=None, ge=0, le=1)
    sample_rows: int | None = Field(default=None, ge=1)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    row_filter: RowFilter | None = None


class RederiveRequest(BaseModel):
    model_config = _FORBID

    threshold_positive: float | None = None
    threshold_negative: float | None = None
    min_top_probability: float | None = Field(default=None, ge=0, le=1)


class AggregateRequest(BaseModel):
    model_config = _FORBID

    run_ids: list[str] = Field(min_length=2, max_length=16)


# --- responses: the label-run contract (FPRD 005 section 7.4) ---------------------------------


class Plan(BaseModel):
    rows_total: int
    rows_reused: int
    rows_to_score: int
    #: Agent rows already counted on this version within the window (P-07).
    agent_window_rows: int
    approval_needed: bool
    threshold: int
    labeler_identity: dict[str, Any]
    labeler_identity_hash: str
    labeler_fingerprint: str
    server_kind: str
    resident_model: str | None
    model_revision: str | None
    #: Probe-verdict runs only: the probe as miLLM states it, and a one-input preflight score.
    probe: dict[str, Any] | None = None
    #: Probe-verdict runs only: the reproduction gate this run will pass through (FR-009.77).
    reproduction: dict[str, Any] | None = None
    #: Feature-tag runs only: the SAE read and a one-row preflight.
    features: dict[str, Any] | None = None
    #: The rows the run covers and the distinct row keys it scores (each key once; its label
    #: applies to every copy, T-07): ``rows``, ``row_keys``, ``keys_with_copies``,
    #: ``rows_in_copied_keys``, and the version's warning when copies' metadata disagree.
    row_coverage: dict[str, Any] | None = None


class LabelRunOut(BaseModel):
    id: str
    kind: str
    state: str
    input_version_id: str
    field_map: dict[str, Any]
    endpoint_snapshot: dict[str, Any]
    template_id: str | None
    rubric_id: str | None
    template_ref: str | None
    question: str | None
    positive_label: str | None
    negative_label: str | None
    threshold_positive: float | None
    threshold_negative: float | None
    min_top_probability: float | None
    label_set: list[str] | None
    sampling: dict[str, Any]
    structured_output: str
    packing: str
    batch_id: str | None
    chunk_size: int
    row_filter: dict[str, Any] | None
    parent_run_ids: list[str]
    labeler_identity: dict[str, Any]
    labeler_identity_hash: str
    labeler_fingerprint: str
    pinned: bool | None
    revision_reported: bool | None
    system_fingerprint: str | None
    counts: dict[str, int]
    keep_share_estimate: dict[str, Any] | None
    keep_share_actual: float | None
    length_correlation: dict[str, Any] | None
    rows_total: int
    rows_reused: int
    rows_done: int
    agent_counted_rows: int
    approval_id: str | None
    error: dict[str, Any] | None
    started_by: str
    started_by_origin: str
    created_at: datetime
    completed_at: datetime | None
    job_ids: list[str]
    current_job_id: str | None
    room: str
    #: Whether "Resume run" applies: a cancelled or failed run with no live job, unless the
    #: reproduction gate stopped it (``not_resumable_reason`` says what to do instead).
    resumable: bool = False
    not_resumable_reason: str | None = None
    #: The plan's ``row_coverage`` as recorded at start; None on a run started before 0021.
    row_coverage: dict[str, Any] | None = None


class LabelRunList(BaseModel):
    items: list[LabelRunOut]
    total: int
    page: int
    limit: int


class LabelOut(BaseModel):
    """One label with its run's provenance reachable: ``started_by`` is on every label read."""

    label_run_id: str
    row_key: str
    labeler_fingerprint: str
    outcome: str
    parsed_value: Any
    probability: float | None
    distribution: dict[str, float] | None
    raw_output: Any
    rationale: str | None
    steering_state: str
    latency_ms: int | None
    skip_reason: str | None
    provisional: bool
    reused_from_run_id: str | None
    chunk_index: int | None
    scored_at: datetime
    started_by: str
    started_by_origin: str


class LabelPage(BaseModel):
    items: list[LabelOut]
    total: int
    page: int
    limit: int


class SampleRow(BaseModel):
    row_key: str
    text: str
    probability: float | None
    distribution: dict[str, float] | None
    outcome: str | None
    verdict: str | None
    rationale: str | None
    latency_ms: int | None
    error: str | None


class SampleResult(BaseModel):
    rows: list[SampleRow]
    model: str
    server_kind: str
    steering_state: str


class KeepShareAccepted(BaseModel):
    job_id: str
    room: str


class KeepShareResult(BaseModel):
    job_id: str
    status: str
    share: float | None = None
    lo: float | None = None
    hi: float | None = None
    n: int | None = None
    seed: int | None = None
    probabilities: list[float] | None = None
    error: str | None = None


class ProbeListItem(BaseModel):
    """One probe imported in miLLM, as ``GET /api/probes`` states it (009)."""

    probe_id: str
    name: str
    #: The model the probe was fitted on.
    hf_id: str
    layer: int
    scope: str
    threshold: float | None
    threshold_revision: int
    window_thresholds: dict[str, float]
    rung: int | None
    rung_language: str | None
    armed: bool
    #: Whether miLLM's resident model is the one the probe was fitted on; None = nothing loaded.
    fits_resident_model: bool | None


class ProbeList(BaseModel):
    items: list[ProbeListItem]
    resident_model: str | None
    millm_base_url: str


class EndpointTestResult(BaseModel):
    """FR-005.9 and the endpoint-role contract item 5. Never carries a key or a lease ID."""

    role: str
    reachable: bool
    model_listed: bool | None
    protocol_ok: bool | None
    server_kind: str | None
    resident_model: str | None
    lease_supported: bool | None
    lease_state: str | None
    queue: dict[str, Any] | None
    error_code: str | None
    message: str
