"""The operator manifest (FR-003.1, FR-003.2; FTDD 003 section 4.3; FTID 003 section 3.1).

``OperatorManifest`` is frozen and refuses unknown fields: a misspelled field must fail, not be
ignored. Its parameter schema is checked against the supported subset by the REGISTRY (so a bad
schema becomes the ``invalid_manifest`` state with the keyword named, not a crash at import).

The manifest hash is SHA-256 of the manifest's canonical JSON, through the project's one
canonical serialiser (ADR-005). ``json.dumps`` never appears in this package
(``test_manifest.py`` walks the AST).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..core.canonical_json import canonical_sha256

Kind = Literal[
    "filter", "mapper", "deduplicator", "selector", "labeler", "generator", "report", "exporter"
]
Queue = Literal["curation", "labeling", "datajuicer", "designer"]
EndpointRole = Literal["classifier", "judge", "generation", "embeddings"]
Scope = Literal["row", "dataset"]

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PROVIDER_PATTERN = re.compile(r"^(native|data_designer|datajuicer|plugin:[A-Za-z0-9._-]+)$")
NUMERIC_TYPES = frozenset({"integer", "number"})


class ColumnSpec(BaseModel):
    """One column an operator reads or writes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    type: str = Field(min_length=1, description="Arrow-ish type name: string, int64, float64, ...")
    required: bool = True
    #: For output columns: the role feature 002 records (content, label, metadata, ...).
    role: str | None = None


class ThresholdSpec(BaseModel):
    """A parameter that cuts a statistic (FR-003.1 ``thresholds``; FR-003.20)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    param: str
    statistic: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    drop_when: Literal["below", "above"]
    #: The other end of a band (min and max), drawn as a second cutoff.
    pair_param: str | None = None


class ResourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    queue: Queue
    cpu_class: Literal["light", "medium", "heavy"] = "light"
    memory_class: Literal["light", "medium", "heavy"] = "light"
    endpoint_role: EndpointRole | None = None
    needs_lease: bool = False


class OperatorRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    @classmethod
    def parse(cls, text: str) -> OperatorRef:
        name, sep, version = text.partition("@")
        if not sep or not name or not version:
            raise ValueError(f"{text!r} is not name@version")
        return cls(name=name, version=version)


class OperatorManifest(BaseModel):
    """FR-003.1's fields plus ``scope``. Any change is a version change (ADR-009)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    version: str = Field(min_length=1)
    provider: str
    provider_version: str = Field(min_length=1)
    kind: Kind
    scope: Scope = "row"
    description: str = Field(min_length=1)
    input_columns: tuple[ColumnSpec, ...] = ()
    output_columns: tuple[ColumnSpec, ...] = ()
    params_schema: dict[str, Any]
    thresholds: tuple[ThresholdSpec, ...] = ()
    resources: ResourceSpec
    deterministic: bool
    #: Binding kinds the step consumes; only those enter its identity (FR-002.28, 002 code).
    binding_kinds: tuple[str, ...] = ()
    #: Feature 009's probe-verdict or feature-tagging labelers count toward P-07 (FR-002.50).
    detector_labeler_kind: str | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not NAME_PATTERN.fullmatch(value):
            raise ValueError("name must be snake_case: ^[a-z][a-z0-9_]{1,63}$")
        return value

    @field_validator("provider")
    @classmethod
    def _provider(cls, value: str) -> str:
        if not PROVIDER_PATTERN.fullmatch(value):
            raise ValueError(
                "provider must be native, data_designer, datajuicer or plugin:<distribution>"
            )
        return value

    @model_validator(mode="after")
    def _consistent(self) -> OperatorManifest:
        properties = self.params_schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("params_schema.properties must be an object")
        for spec in self.thresholds:
            for param in (spec.param, spec.pair_param):
                if param is None:
                    continue
                prop = properties.get(param)
                if not isinstance(prop, dict):
                    raise ValueError(f"threshold parameter {param!r} is not in params_schema")
                if prop.get("type") not in NUMERIC_TYPES:
                    raise ValueError(f"threshold parameter {param!r} must be integer or number")
        is_dj_provider = self.provider == "datajuicer"
        is_dj_queue = self.resources.queue == "datajuicer"
        if is_dj_provider != is_dj_queue:
            raise ValueError(
                "resources.queue is datajuicer exactly when provider is datajuicer (ADR-010)"
            )
        if (self.provider == "data_designer") != (self.resources.queue == "designer"):
            raise ValueError(
                "resources.queue is designer exactly when provider is data_designer "
                "(ADR-010 amendment, 2026-10-07)"
            )
        if self.resources.needs_lease and self.resources.endpoint_role is None:
            raise ValueError("needs_lease requires an endpoint_role")
        return self

    @property
    def ref(self) -> OperatorRef:
        return OperatorRef(name=self.name, version=self.version)

    @property
    def ref_text(self) -> str:
        return f"{self.name}@{self.version}"


def manifest_hash(manifest: OperatorManifest) -> str:
    """SHA-256 of the manifest's canonical JSON (ADR-005)."""
    return canonical_sha256(manifest.model_dump(mode="json"))
