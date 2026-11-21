"""REST bodies of the rule-set routes (feature 016; FTDD 016 section 6.1).

A document is passed as a JSON object and validated by ``rules/rule_set.RuleSetDocument`` inside the
save pipeline, so every refusal names its JSON Pointer with the 016 code (``rule_set_invalid``,
``not_nfc``, ...) rather than FastAPI's generic 422.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..rules.rule_set import NAME_PATTERN
from .behaviour import BEHAVIOUR_KEY_PATTERN

POINTER_PATTERN = r"^(/([^~/]|~[01])*)*$"


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuleSetSaveIn(_In):
    document: dict[str, Any]


class _DocumentOrStored(_In):
    """Either ``document`` inline, or a stored ``name`` (and optional ``version``; default latest)."""

    document: dict[str, Any] | None = None
    name: str | None = Field(default=None, pattern=NAME_PATTERN)
    version: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _one_source(self) -> _DocumentOrStored:
        if (self.document is None) == (self.name is None):
            raise ValueError("give either document, or name (and optionally version)")
        if self.version is not None and self.name is None:
            raise ValueError("version needs name")
        return self


class RuleSetValidateIn(_DocumentOrStored):
    pass


class RuleSetPreviewIn(_DocumentOrStored):
    #: Never stored or logged (FR-016.19); its size is capped by RULE_PREVIEW_MAX_TEXT_BYTES.
    text: str
    #: The element inside the text, for ``reads: element`` and ``element`` validator candidates.
    element: str | None = None


class Substitution(_In):
    pointer: str = Field(pattern=POINTER_PATTERN, min_length=1)
    value: Any


class RuleSetCloneIn(_In):
    version: int | None = Field(default=None, ge=1)
    new_name: str = Field(pattern=NAME_PATTERN)
    new_behaviour_key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    substitutions: list[Substitution] = Field(default_factory=list, max_length=1000)
    replacement_cases: list[dict[str, Any]] = Field(default_factory=list, max_length=10_000)


class RuleDerivationIn(_In):
    """A derivation's inputs (FR-016.29), validated again by the service before queueing."""

    source_version_id: str = Field(min_length=1, max_length=64)
    text_column: str = Field(min_length=1, max_length=256)
    label_column: str = Field(min_length=1, max_length=256)
    #: The label values counted positive; the column must hold exactly two classes.
    positive_values: list[str | int | bool] = Field(min_length=1, max_length=8)
    behaviour_key: str = Field(pattern=BEHAVIOUR_KEY_PATTERN)
    name: str | None = Field(default=None, pattern=NAME_PATTERN)
    group_column: str | None = Field(default=None, min_length=1, max_length=256)
    floor: float = Field(default=0.95, gt=0, le=1)
    max_signals: int = Field(default=8, ge=1)
    min_support: int = Field(default=10, ge=1, le=1_000_000)
    held_out_fraction: float = Field(default=0.2, gt=0, lt=1)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
