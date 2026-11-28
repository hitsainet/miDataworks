"""The code-rule document, its cases, the manifest listing, and the generated contract file.

``CodeRuleDocument`` is the HASHED body (FR-018.8; FTDD §4.2); ``CodeRuleCase`` sits beside it and
is not hashed; ``CodeRuleListing`` is the dataset-version manifest extension
``midataworks.code-rules/v1`` (S4; FTDD §5.7). :func:`render_contract_file` builds the exact bytes
of ``docs/schemas/midataworks-runner-v1.json`` from these models and ``constants`` - the only source
of that file (``backend/scripts/write_runner_contract.py`` writes it). It refuses while any S0 value
is unset, so S1 cannot publish a formula (FTDD §4.3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from . import constants, prelude
from .hashing import bundle_digest
from .reasons import REASONS, Reason

CONTRACT_FILE_NAME = "midataworks-runner-v1.json"
CONTRACT_ID = (
    "https://raw.githubusercontent.com/hitsainet/miDataworks/main/docs/schemas/"
    + CONTRACT_FILE_NAME
)
PACKAGED_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1] / "schemas" / "data" / CONTRACT_FILE_NAME
)
REGENERATE_COMMAND = "cd backend && python -m scripts.write_runner_contract"

#: The most functions one document may declare (the floor of ``code_rule_max_functions``).
CODE_RULE_MAX_FUNCTIONS_FLOOR = 16
FUNCTION_NAME = r"^[a-z_][a-z0-9_]{0,63}$"
CASE_ID = r"^[a-z0-9][a-z0-9_.-]{0,95}$"
SHA256 = r"^[0-9a-f]{64}$"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FunctionOutput(_M):
    type: Literal["count"]


class FunctionDecl(_M):
    name: str = Field(pattern=FUNCTION_NAME)
    output: FunctionOutput


class DerivedFrom(_M):
    """The source versions that informed the code (F5 (a)); hashed, so a change needs approval."""

    sources: list[Annotated[str, Field(min_length=1, max_length=200)]]
    statement: str = Field(min_length=1, max_length=500)


class CodeRuleDocument(_M):
    """The hashed code-rule body (FR-018.8): ``code_sha256`` = SHA-256 of its canonical JSON."""

    runner_contract: Literal["midataworks.runner/v1"]
    source: str = Field(min_length=1)
    functions: list[FunctionDecl] = Field(min_length=1, max_length=CODE_RULE_MAX_FUNCTIONS_FLOOR)
    params_schema: dict[str, Any]
    derived_from: DerivedFrom

    @field_validator("source")
    @classmethod
    def _encodable(cls, value: str) -> str:
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"source holds a lone surrogate at code point {exc.start}; it cannot be encoded"
            ) from exc
        return value

    @field_validator("params_schema")
    @classmethod
    def _schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            jsonschema.Draft202012Validator.check_schema(value)
        except jsonschema.SchemaError as exc:
            raise ValueError(f"params_schema is not a draft 2020-12 schema: {exc.message}") from exc
        return value

    @model_validator(mode="after")
    def _unique_functions(self) -> CodeRuleDocument:
        names = [f.name for f in self.functions]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"function declared twice: {dupes[0]}")
        return self


class CaseCount(_M):
    count: int = Field(ge=0)


class CaseReason(_M):
    reason: Reason


class CaseSource(_M):
    kind: Literal["published_test_value", "published_reference_value", "constructed"]
    citation: str | None = None
    retrieved: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    source_version_id: str | None = None


class CodeRuleCase(_M):
    """A case stored beside a code rule (NOT hashed: a case-only edit needs no approval)."""

    id: str = Field(pattern=CASE_ID)
    function: str = Field(pattern=FUNCTION_NAME)
    params: dict[str, Any]
    text: str
    expect: CaseCount | CaseReason
    source: CaseSource


class CodeRuleListingEntry(_M):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,99}$")
    version: int = Field(ge=1)
    behaviour_key: str
    code_sha256: str = Field(pattern=SHA256)
    runner_contract: str
    path: str
    cases_path: str
    cases_sha256: str = Field(pattern=SHA256)
    rule_sets: list[str]
    visibility_floor: str


class CodeRuleListing(_M):
    """``extensions["midataworks.code-rules/v1"]`` of a dataset-version manifest (S4)."""

    recorded: Literal[True]
    code_rules: list[CodeRuleListingEntry]


class LimitsFloorStatus(_M):
    """``limits_floor_status``: whether the published floor is final (ENH-006 D32, D47).

    ``provisional`` is true until every ``pending`` measurement confirms the floor; it carries no
    second copy of a floor value, and it never forbids a raise (``change_rule``)."""

    provisional: bool
    measured: str = Field(min_length=1)
    pending: list[Annotated[str, Field(min_length=1)]]
    change_rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def _pending_while_provisional(self) -> LimitsFloorStatus:
        if self.provisional and not self.pending:
            raise ValueError("a provisional floor names what it waits for (pending)")
        if not self.provisional and self.pending:
            raise ValueError("a confirmed floor waits for nothing (pending must be empty)")
        return self


class S0Unset(RuntimeError):
    """A value the S0 measurement sets is still ``None``: the contract cannot be rendered."""


def unset_values() -> list[str]:
    missing = [f"limits_floor.{k}" for k, v in constants.LIMITS_FLOOR.items() if v is None]
    missing += [
        f"guest.bundle.snapshot.{k}"
        for k in ("sha256", "size")
        if constants.GUEST_SNAPSHOT[k] is None
    ]
    return missing


def guest_bundle() -> dict[str, Any]:
    """``guest.bundle`` with its ``bundle_digest`` (the SHA-256 of the block without it)."""
    block = dict(constants.GUEST_BUNDLE_PARTS)
    block["snapshot"] = dict(constants.GUEST_SNAPSHOT)
    block["bundle_digest"] = bundle_digest(block)
    return block


CONTRACT_COMMENT = (
    "The runner contract (feature 018). A HOST runs a code rule's source inside the published "
    "WebAssembly guest (guest.bundle.snapshot, fetched by sha256; never rebuilt by a consumer) on "
    "Wasmtime with fuel, a memory cap and an epoch deadline, through its OWN implementation of the "
    "wasi table (no preopened directory, empty environment, fixed clock and random stream). One "
    "fresh instance per (text, function) call (instance_rule). The guest reads one canonical JSON "
    "request on descriptor 0 and writes one reply line on descriptor 1; phase markers on "
    "descriptor 3 switch the fuel budget and are accepted once and in order. The host reduces the "
    "reply to a count (a non-negative int, clamped to count_cap) or one of the reasons; a code rule "
    "can never return text. Hosts grant at least limits_floor; a deployment may raise a limit, never "
    "lower it. Fuel, not wall time, decides rule_resource_limit. The top-level schema validates a "
    "code-rule document (CodeRuleDocument); $defs.CodeRuleCase is a stored case; "
    "$defs.CodeRuleListing is the dataset-version manifest extension midataworks.code-rules/v1. The "
    "vectors in runner-cases-v1.json pin every rule; a vector outranks every implementation."
)


def contract_document() -> dict[str, Any]:
    missing = unset_values()
    if missing:
        raise S0Unset("set by the S0 measurement before publishing: " + ", ".join(missing))
    generated = CodeRuleDocument.model_json_schema()
    defs = dict(generated.pop("$defs", {}))
    for model in (CodeRuleCase, CodeRuleListing):
        schema = model.model_json_schema()
        defs.update(schema.pop("$defs", {}))
        defs[model.__name__] = schema
    generated.pop("title", None)
    generated.pop("description", None)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": CONTRACT_ID,
        "title": "miDataworks Runner Contract v1",
        "description": (
            "The isolated runner contract (format midataworks.runner/v1). GENERATED from "
            "backend/src/runner_contract/ (constants.py, prelude.py, document.py) by "
            "backend/scripts/write_runner_contract.py; do not edit by hand. Consumers vendor this "
            "file byte-identical. Changes within v1 are additive only (versioning)."
        ),
        "$comment": CONTRACT_COMMENT,
        "format": constants.CONTRACT_FORMAT,
        "contract_version": constants.CONTRACT_VERSION,
        "contract_major": constants.CONTRACT_MAJOR,
        "cases_format": constants.CASES_FORMAT,
        "runtime": constants.RUNTIME,
        "guest": {
            "bundle": guest_bundle(),
            "prelude_source": prelude.PRELUDE_SOURCE,
            "shim_source": prelude.SHIM_SOURCE,
        },
        "guest_interface": constants.GUEST_INTERFACE,
        "language": constants.LANGUAGE,
        "stdlib_allowed": list(constants.STDLIB_ALLOWED),
        "static_refusals": list(constants.STATIC_REFUSALS),
        "wasi": [dict(row) for row in constants.WASI_TABLE],
        "random": constants.RANDOM,
        "limits_floor": dict(constants.LIMITS_FLOOR),
        "limits_floor_basis": constants.LIMITS_FLOOR_BASIS,
        "limits_floor_status": LimitsFloorStatus.model_validate(
            constants.LIMITS_FLOOR_STATUS
        ).model_dump(),
        "request_limits": list(constants.REQUEST_LIMITS),
        "count_cap": constants.COUNT_CAP,
        "diagnostic": constants.DIAGNOSTIC,
        "reasons": list(REASONS),
        "reason_meanings": constants.REASON_MEANINGS,
        "text_rules": constants.TEXT_RULES,
        "hash_rule": constants.HASH_RULE,
        "instance_rule": constants.INSTANCE_RULE,
        "versioning": constants.VERSIONING,
        "properties": constants.PROPERTIES,
        "hostile_classes": [dict(h) for h in constants.HOSTILE_CLASSES],
        "sentinel_kinds": constants.SENTINEL_KINDS,
        **generated,
        "$defs": defs,
    }


def render_contract_file() -> bytes:
    """The exact bytes of ``docs/schemas/midataworks-runner-v1.json`` (and its packaged copy)."""
    return (
        json.dumps(contract_document(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    ).encode("ascii")
