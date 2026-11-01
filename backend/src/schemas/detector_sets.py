"""Request models for feature 009's routes (FTDD 009 section 5.1; ADR-002).

Every request model is ``extra="forbid"``: a body field claiming who acted (``started_by``) is
refused, because who comes from the request (010 FTDD section 5.1). Response bodies are plain JSON
built by the services.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .behaviour import BEHAVIOUR_KEY_PATTERN
from .behaviour_datasets import RecordedDefinitionIn

DetectorRoleName = Literal["train", "id_test", "ood_eval", "calibration_negatives"]
MappingTarget = Literal["positive", "negative", "excluded"]

NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,99}$"
REPO_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"
NAMESPACE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScreenPin(_Strict):
    """A rule set that screened the negatives (017 FR-017.33): the exact stored version."""

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SecondOpinionSample(_Strict):
    """A behaviour with no rule signal (017 FR-017.33): the second-opinion sample its assumed
    negatives rest on, and the positive rate that sample showed."""

    sample_rows: int = Field(ge=1)
    estimated_positive_rate: float = Field(ge=0, le=1)
    label_run_id: str | None = Field(None, max_length=64)


class NegativesBasis(_Strict):
    """Why the calibration negatives are negative (FR-009.6).

    - ``labeler_filtered``: a label run scored them negative; names the labeler identity and rule;
    - ``assumed_negative``: an unlabeled corpus mapped wholly to negative (D-8 notes it);
    - ``human_labelled``: people labelled them (Humicroedit's five graders). Names the label column,
      the label values that select the negatives (they must be exactly the values the role's
      mapping sends to negative), who labelled them, and the rule; carries no labeler identity.

    017 FR-017.33: an ``assumed_negative`` basis of a BEHAVIOUR's set names its evidence: the
    ``screen`` (the rule sets whose positive signals screened the rows, each run with
    ``use = screen`` in the role's version build) and/or, for a behaviour with no rule signal, the
    ``second_opinion`` sample and its estimated positive rate. Checked against the version when the
    set's roles are saved (``set_service``).
    """

    kind: Literal["labeler_filtered", "assumed_negative", "human_labelled"]
    labeler_identity_hash: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")
    rule: str | None = Field(None, max_length=500)
    label_column: str | None = Field(None, min_length=1, max_length=200)
    negative_values: list[Annotated[str, Field(max_length=200)]] | None = Field(None, max_length=64)
    labelled_by: str | None = Field(None, max_length=500)
    screen: list[ScreenPin] | None = Field(None, min_length=1, max_length=16)
    second_opinion: SecondOpinionSample | None = None

    @model_validator(mode="after")
    def _basis_names_its_evidence(self) -> NegativesBasis:
        if self.kind != "assumed_negative" and (self.screen or self.second_opinion):
            raise ValueError("screen and second_opinion belong to an assumed_negative basis")
        if self.kind == "labeler_filtered" and (not self.labeler_identity_hash or not self.rule):
            raise ValueError("a labeler-filtered basis names the labeler identity and the rule")
        human = (self.label_column, self.negative_values, self.labelled_by)
        if self.kind == "human_labelled":
            if self.labeler_identity_hash:
                raise ValueError(
                    "a human-labelled basis names no labeler identity; a model's labels are "
                    "labeler_filtered"
                )
            if not (self.label_column and self.negative_values and self.labelled_by and self.rule):
                raise ValueError(
                    "a human-labelled basis names the label column, the negative values, who "
                    "labelled the rows and the rule"
                )
        elif any(v is not None for v in human):
            raise ValueError(
                "label_column, negative_values and labelled_by belong to a human_labelled basis"
            )
        return self


#: Label-source declarations per role (e2e run G8 raised it from 32).
LABEL_SOURCE_MAX = 1024


class RoleIn(_Strict):
    role: DetectorRoleName
    version_id: str = Field(min_length=1, max_length=64)
    split: str = Field(min_length=1, max_length=200)
    input_column: str = Field(min_length=1, max_length=200)
    label_column: str = Field(min_length=1, max_length=200)
    label_mapping: dict[str, MappingTarget]
    pair_column: str | None = Field(None, max_length=200)
    #: The columns the label was COMPUTED FROM (Humicroedit: ``meanGrade``, ``grades``). D-3's
    #: shortcut audit sets them aside and reports them as excluded: the probe reads only the input
    #: column, so a column the label was defined from predicts it by construction.
    #: At most LABEL_SOURCE_MAX: the 32 of 2026-10-07 was an arbitrary bound with no stated reason,
    #: and a 60-75-step derivation needs more (e2e run G8, 2026-10-21). The bound only caps the
    #: request; each entry is still checked against the role's version.
    label_source_columns: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=LABEL_SOURCE_MAX
    )
    negatives_basis: NegativesBasis | None = None
    display_name: str | None = Field(None, max_length=200)
    position: int | None = Field(None, ge=0, le=1000)


class MonitoredRefIn(_Strict):
    kind: Literal["role", "version"]
    role_index: int | None = Field(None, ge=0)
    role_id: str | None = None
    version_id: str | None = None
    split: str | None = None
    column: str | None = None

    @model_validator(mode="after")
    def _complete(self) -> MonitoredRefIn:
        if self.kind == "role" and self.role_index is None and self.role_id is None:
            raise ValueError("a role reference names role_index or role_id")
        if self.kind == "version" and not (self.version_id and self.split and self.column):
            raise ValueError("a version reference names version_id, split and column")
        return self


class DetectorSetCreate(_Strict):
    name: str = Field(pattern=NAME_PATTERN)
    description: str = Field("", max_length=4000)
    positive_meaning: str = Field("", max_length=1000)
    #: 015 FR-015.1: the behaviour miStudio's catalogue monitors with this set (D37 wire name
    #: ``behaviour``); not unique.
    behaviour_key: str | None = Field(None, pattern=BEHAVIOUR_KEY_PATTERN)
    roles: list[RoleIn] = Field(default_factory=list, max_length=32)
    monitored_ref: MonitoredRefIn | None = None
    #: 017 FR-017.11: the definition the set is made against (``dataworks_resolve_behaviour``'s
    #: ``record``); absent, the training role's version's recorded definition is taken.
    behaviour_definition: RecordedDefinitionIn | None = None
    #: 017 TD-017.30: the benchmark sources the operator names for the behaviour (each a source
    #: of feature 001); the contamination catalogue is their union over every set.
    benchmark_source_ids: list[str] = Field(default_factory=list, max_length=64)


class DetectorSetUpdate(_Strict):
    description: str | None = Field(None, max_length=4000)
    positive_meaning: str | None = Field(None, max_length=1000)
    #: ``null`` or absent leaves the key; ``""`` clears it.
    behaviour_key: str | None = Field(None, pattern=r"^$|" + BEHAVIOUR_KEY_PATTERN)
    roles: list[RoleIn] | None = Field(None, max_length=32)
    monitored_ref: MonitoredRefIn | None = None
    #: 017 FR-017.11: replaces the recorded definition (absent or null leaves it).
    behaviour_definition: RecordedDefinitionIn | None = None
    #: 017 TD-017.30: replaces the named benchmarks (absent or null leaves them; ``[]`` clears).
    benchmark_source_ids: list[str] | None = Field(None, max_length=64)


class SendRequest(_Strict):
    #: version ID -> repository; versions not named get ``<namespace>/<dataset>-v<n>`` (FR-009.78).
    repositories: dict[str, str] = Field(default_factory=dict)
    namespace: str | None = Field(None, pattern=NAMESPACE_PATTERN)
    visibility: Literal["private", "public"] = "private"

    @model_validator(mode="after")
    def _repos(self) -> SendRequest:
        import re

        for repo in self.repositories.values():
            if not re.fullmatch(REPO_PATTERN, repo):
                raise ValueError(f"repository {repo!r} must look like owner/name")
        return self


class GroupSendRequest(SendRequest):
    """``POST /detector-sets/send`` (017 TD-017.29): several sets under ONE ``hub_push`` approval
    (a behaviour's main set and one per sub-question, FR-017.30); the cap is
    ``DETECTOR_MAX_SETS_PER_SEND``, checked in the service (a setting, not a schema bound)."""

    set_ids: list[str] = Field(min_length=1, max_length=256)


class RewardMarkIn(_Strict):
    mistudio_probe_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class ReproductionLinkIn(_Strict):
    """Link a version split to an evaluation miStudio recorded for a probe (FR-009.77 option (b)).
    Columns and mapping default to the miStudio view's own."""

    mistudio_probe_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    probe_dataset_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")
    version_id: str = Field(min_length=1, max_length=64)
    split: str = Field(min_length=1, max_length=200)
    input_column: str | None = Field(None, min_length=1, max_length=200)
    label_column: str | None = Field(None, min_length=1, max_length=200)
    label_mapping: dict[str, Literal["positive", "negative", "excluded"]] | None = Field(
        None, max_length=200
    )


class AgreementReportIn(_Strict):
    version_id: str = Field(min_length=1, max_length=64)
    split: str = Field(min_length=1, max_length=200)
    probe_label_run_id: str = Field(min_length=1, max_length=64)
    judge_label_run_id: str = Field(min_length=1, max_length=64)
    reference: dict[str, Any]
    #: The detector set's training version: flags a judge that is the probe's own teacher.
    training_version_id: str | None = Field(None, max_length=64)
    #: Open a 006 label-review queue over the disagreements (FR-009.56, FR-006.40).
    send_disagreements_to_review: bool = False
