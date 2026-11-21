"""Shared pieces of 017's behaviour operators (FTID §3.4 – §3.6).

- ``"@input"``: a parameter that reads the build input's recorded configuration is resolved by the
  build (``services/step_params.resolve_inputs``); at recipe validation it is still ``"@input"``,
  so the cross-parameter checks that need it wait for the build (:func:`deferred`).
- An optional rule-set PIN (``rule_set_name``, ``rule_set_version``, ``rule_set_sha256``): the
  build fills ``rule_set_document`` with the stored canonical text through 016's resolver
  (``services/rules/step_resolution.resolve_optional_params``), so the step's identity binds the
  rule set it read; :func:`rule_set_of` re-hashes it against the pin as ``rule_set@1`` does.
- :func:`row_random`: one generator per row from the step seed and the row's identity (the
  ``synth_identifier`` rule), so a draw does not depend on batching or file order.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Mapping
from typing import Any

from ....rules.rule_set import NAME_PATTERN, RuleSetDocument
from ....services.behaviour.vocabulary import Vocabulary, VocabularyError, parse_vocabulary
from ...errors import StepFailed
from ..identifiers.schemes import row_rng

INPUT = "@input"
#: ``details.next`` of the slice's refusals (FTID §5.4): a live MCP tool and why.
NEXT_RULE_SET = {
    "tool": "dataworks_get_rule_set",
    "args": {},
    "why": "read what the pinned rule set declares (renderings, decoys, outputs)",
}
NEXT_VALIDATE = {
    "tool": "dataworks_validate_recipe",
    "args": {},
    "why": "fix the step's parameters, then validate the recipe again",
}
NEXT_VERSION = {
    "tool": "dataworks_get_version",
    "args": {},
    "why": "see what the input version recorded",
}
#: The parameters of an optional rule-set pin (the build fills ``rule_set_document``).
PIN_SCHEMA: dict[str, Any] = {
    "rule_set_name": {
        "type": "string",
        "pattern": NAME_PATTERN,
        "title": "Rule set (optional)",
        "x-advanced": True,
        "x-hint": "The stored rule set this step reads (renderings, decoys, the scan's "
        "expression); pin the same version as the rule_set@1 step.",
    },
    "rule_set_version": {"type": "integer", "minimum": 1, "title": "Rule set version"},
    "rule_set_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$", "title": "Rule set sha256"},
    "rule_set_document": {
        "type": "string",
        "title": "Filled in by the build",
        "x-advanced": True,
        "x-hint": "The stored canonical document; a recipe never sets it.",
    },
}


def deferred(value: Any) -> bool:
    return bool(value == INPUT)


def refused(exc: VocabularyError) -> StepFailed:
    return StepFailed(exc.code, exc.message, exc.details)


def vocabulary_of(params: Mapping[str, Any]) -> Vocabulary:
    """The step's vocabulary (literal JSON, or what ``"@input"`` resolved to)."""
    raw = params.get("vocabulary")
    if deferred(raw):
        raise StepFailed(
            "input_not_recorded",
            'The vocabulary still reads "@input": the build resolves it from the input version '
            "before the step runs.",
            {"param": "vocabulary", "next": NEXT_VERSION},
        )
    return parse_vocabulary(raw)


def json_param(params: Mapping[str, Any], name: str, default: Any) -> Any:
    raw = params.get(name)
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(str(raw))
    except ValueError as exc:
        raise VocabularyError(
            "vocabulary_invalid", f"{name} is not JSON: {exc}.", {"field": name}
        ) from None


def pinned(params: Mapping[str, Any]) -> bool:
    return any(
        params.get(k) is not None
        for k in ("rule_set_name", "rule_set_version", "rule_set_sha256", "rule_set_document")
    )


def rule_set_of(params: Mapping[str, Any], operator: str) -> RuleSetDocument | None:
    """The pinned rule set the build filled in, re-hashed against the pin; None when unpinned."""
    if not pinned(params):
        return None
    text = params.get("rule_set_document")
    name, version = params.get("rule_set_name"), params.get("rule_set_version")
    if not isinstance(text, str):
        raise StepFailed(
            "rule_set_document_missing",
            f"{operator} pins rule set {name}@{version} but ran without its stored document: the "
            "build resolves it from the pin before dispatch.",
            {"name": name, "version": version},
        )
    found = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if found != params.get("rule_set_sha256"):
        raise StepFailed(
            "rule_set_hash_mismatch",
            f"The document handed to {operator} hashes to {found}, not the pinned "
            f"{params.get('rule_set_sha256')} of {name}@{version}.",
            {"name": name, "version": version, "pinned": params.get("rule_set_sha256")},
        )
    return RuleSetDocument.model_validate(json.loads(text))


def row_random(seed: int, purpose: str, row_key: str, occurrence: int) -> random.Random:
    return row_rng(seed, purpose, row_key, occurrence)
