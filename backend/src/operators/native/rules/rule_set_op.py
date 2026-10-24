"""``rule_set@1``: run a stored rule set over every row of a build (FR-016.22; FTDD 016 section 6.4).

A recipe step PINS ``rule_set_name``, ``rule_set_version`` and ``rule_set_sha256``; the build
fills ``rule_set_document`` with the stored canonical text (``services/rules/step_resolution.py``).
This operator then:

- refuses to run without the document (``rule_set_document_missing``: an unresolved call path) or
  when the document's sha256 differs from the pin (``rule_set_hash_mismatch``);
- evaluates the document per row with ``src/rules/compose`` (the evaluator every app conforms to);
- writes EXACTLY the declared output columns, each prefixed with ``output_prefix``, plus
  ``<prefix>vetoed`` when the document declares vetoes;
- reports ``{"rule_set_use": {name, version, sha256, behaviour_key, use}}`` on every batch, which
  the build cross-checks against the pin when it records the use (FR-016.24).

A validator that raises fails the step ``rule_signal_error`` naming the signal, the validator and
the row key, never the text; no default verdict is written. ``element_column`` is the element a
``reads: element`` pattern and an ``element`` validator candidate read. A null text is read as the
empty text. Deterministic, no model.

There is no ``before_column`` / ``after_column`` (FTDD 016 section 6.4 lists them): the contract
checks an element candidate with empty neighbours (``compose._validate``), so such a parameter would
change nothing; it is recorded in the controls record rather than shipped as a knob that does nothing.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import pyarrow as pa

from ....rules.rule_set import NAME_PATTERN, USES, RuleSetDocument
from ...context import RunContext
from ...manifest import OperatorManifest, ResourceSpec
from ...protocol import OperatorResult
from ..identifiers.common import (
    PREFIX_PATTERN,
    PROVIDER_VERSION,
    append_outputs,
    pairs,
    refuse,
    schema,
    text_values,
)

DEFAULT_PREFIX = "rs_"
#: The largest stored document a build can carry (``RULE_SET_MAX_DOCUMENT_BYTES``' ceiling).
DOCUMENT_MAX_CHARS = 104_857_600
_TYPES = {"bool": pa.bool_(), "int": pa.int64(), "string": pa.string()}

RULE_SET_MANIFEST = OperatorManifest(
    name="rule_set",
    version="1",
    provider="native",
    provider_version=PROVIDER_VERSION,
    kind="labeler",
    scope="row",
    description=(
        "Runs one stored rule set (pinned by name, version and sha256) over a text column and "
        "writes the output columns the rule set declares, prefixed, plus <prefix>vetoed when it "
        "declares vetoes. The build fills in the stored document; a stored sha256 that differs "
        "from the pin is refused. Records which rule set ran and why (use). Deterministic, no "
        "model."
    ),
    output_columns=(),
    params_schema=schema(
        {
            "rule_set_name": {
                "type": "string",
                "pattern": NAME_PATTERN,
                "title": "Rule set",
                "x-hint": "The stored rule set's name (dataworks_list_rule_sets).",
            },
            "rule_set_version": {"type": "integer", "minimum": 1, "title": "Version"},
            "rule_set_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "title": "sha256",
                "x-hint": "The pinned version's sha256 (dataworks_get_rule_set); a stored version "
                "whose sha256 differs is refused.",
            },
            "use": {
                "type": "string",
                "enum": list(USES),
                "title": "Use",
                "x-hint": "Why this build runs the rule set; recorded with the version.",
            },
            "text_column": {"type": "string", "minLength": 1, "title": "Text column"},
            "element_column": {
                "type": "string",
                "minLength": 1,
                "title": "Element column",
                "x-hint": "The element inside the text that element patterns and element "
                "validator candidates read.",
            },
            "output_prefix": {
                "type": "string",
                "pattern": PREFIX_PATTERN,
                "default": DEFAULT_PREFIX,
                "title": "Output prefix",
            },
            "rule_set_document": {
                "type": "string",
                "maxLength": DOCUMENT_MAX_CHARS,
                "title": "Filled in by the build",
                "x-advanced": True,
                "x-hint": "The stored canonical document; a recipe never sets it.",
            },
        },
        ["rule_set_name", "rule_set_version", "rule_set_sha256", "use", "text_column"],
    ),
    resources=ResourceSpec(queue="curation", cpu_class="light", memory_class="light"),
    deterministic=True,
)


def _compose() -> Any:
    """``rules.compose``, imported at call time: ``rules.validators`` imports the identifier
    schemes, which load this package while the validator registry is still initialising."""
    from ....rules import compose

    return compose


@functools.lru_cache(maxsize=64)
def _compiled(text: str) -> Any:
    """One compile per stored document text (FTDD 016 section 10.4)."""
    return _compose().CompiledRuleSet(RuleSetDocument.model_validate(json.loads(text)))


def _prefix(params: Mapping[str, Any]) -> str:
    value = params.get("output_prefix")
    return DEFAULT_PREFIX if value is None else str(value)


def declared_columns(document: Mapping[str, Any], prefix: str) -> list[tuple[str, str]]:
    """``(column, type)`` the document makes this step write, in declaration order."""
    out = [(prefix + str(o["column"]), str(o["type"])) for o in document.get("outputs", [])]
    if document.get("vetoes"):
        out.append((prefix + "vetoed", "bool"))
    return out


class RuleSetOp:
    manifest = RULE_SET_MANIFEST

    def columns_for(self, params: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
        """Reads the text (and element) column; writes the document's declared columns. Without a
        resolved document it answers the manifest's columns (none): an unresolved call path is
        refused by :meth:`run`, never by a crash here."""
        reads = [str(params[k]) for k in ("text_column", "element_column") if params.get(k)]
        text = params.get("rule_set_document")
        if not isinstance(text, str):
            return reads, [c.name for c in self.manifest.output_columns], []
        try:
            document = json.loads(text)
        except ValueError:
            return reads, [c.name for c in self.manifest.output_columns], []
        return reads, [name for name, _ in declared_columns(document, _prefix(params))], []

    def run(self, batch: pa.Table, params: Mapping[str, Any], ctx: RunContext) -> OperatorResult:
        text = params.get("rule_set_document")
        name, version = str(params["rule_set_name"]), int(params["rule_set_version"])
        if not isinstance(text, str):
            raise refuse(
                "rule_set_document_missing",
                f"rule_set@1 ran without the stored document of {name}@{version}: the build "
                "resolves it from the pin before dispatch.",
                {"name": name, "version": version},
            )
        found = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if found != params["rule_set_sha256"]:
            raise refuse(
                "rule_set_hash_mismatch",
                f"The document handed to rule_set@1 hashes to {found}, not the pinned "
                f"{params['rule_set_sha256']} of {name}@{version}.",
                {"name": name, "version": version, "pinned": params["rule_set_sha256"]},
            )
        compiled = _compiled(text)
        document = compiled.document
        prefix = _prefix(params)
        texts = text_values(batch, str(params["text_column"]), "text_column")
        elements: list[str | None] = (
            text_values(batch, str(params["element_column"]), "element_column")
            if params.get("element_column")
            else [None] * batch.num_rows
        )
        keys = pairs(batch)
        columns = declared_columns(document.model_dump(by_alias=True), prefix)
        values: dict[str, list[Any]] = {c: [] for c, _ in columns}
        vetoed_column = prefix + "vetoed" if document.vetoes else None
        for row, (row_text, element) in enumerate(zip(texts, elements, strict=True)):
            try:
                result = compiled.evaluate(row_text or "", element)
            except _compose().RuleSignalError as exc:
                raise refuse(
                    "rule_signal_error",
                    f"Signal {exc.signal_id!r} ({exc.validator}) of {name}@{version} raised "
                    f"{exc.error_type} on row {keys[row][0]}; no verdict was written.",
                    {
                        "signal": exc.signal_id,
                        "validator": exc.validator,
                        "row_key": keys[row][0],
                        "error": exc.error_type,
                    },
                ) from None
            for out in document.outputs:
                values[prefix + out.column].append(result.outputs[out.column])
            if vetoed_column is not None:
                values[vetoed_column].append(result.vetoed)
        output = append_outputs(
            batch,
            [(c, pa.array(values[c], _TYPES[t])) for c, t in columns],
            self.manifest.ref_text,
        )
        return OperatorResult(
            output=output,
            output_roles=dict.fromkeys(values, "metadata"),
            report={
                "rule_set_use": {
                    "name": name,
                    "version": version,
                    "sha256": found,
                    "behaviour_key": document.behaviour_key,
                    "use": str(params["use"]),
                }
            },
        )
