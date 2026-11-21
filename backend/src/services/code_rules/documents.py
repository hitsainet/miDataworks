"""Static checks of a code rule: everything that needs no runner and no database (FR-018.32).

The document is parsed with the contract's own model (``runner_contract.document``), its caps are
checked against the settings in force, its cases are parsed and checked for a firing and a
non-firing case per declared function, and each case's (and a dry run's) parameters are validated
against the document's ``params_schema`` - data validation, never code (FR-018.18). Each refusal
names the item (a JSON Pointer, a case id, a function, a setting) and a ``next``.

:func:`save_checks_static` is the save route's ``before`` hook (``agent_origin``): synchronous, no
database, run for an agent call before anything is queued (FTID I1).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import jsonschema
from pydantic import ValidationError

from ...core.canonical_json import canonical_json
from ...core.config import get_settings
from ...runner_contract.document import CodeRuleCase, CodeRuleDocument
from ...runner_contract.hashing import canonical_document_bytes, code_sha256
from .errors import CodeRuleError

CONTRACT_PREFIX = "midataworks.runner/"
TEST_TOOL = "dataworks_test_code_rule"


@dataclass(frozen=True)
class Parsed:
    """A document that passed the static checks, with its identity."""

    document: CodeRuleDocument
    raw: dict[str, Any]
    cases: list[CodeRuleCase]
    code_sha256: str
    canonical: str

    @property
    def function_names(self) -> list[str]:
        return [f.name for f in self.document.functions]


def _pointer(prefix: str, loc: Sequence[Any]) -> str:
    return prefix + "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in loc)


def _invalid(prefix: str, exc: ValidationError) -> CodeRuleError:
    first = exc.errors()[0]
    pointer = _pointer(prefix, first["loc"])
    return CodeRuleError(
        422,
        "code_rule_invalid",
        f"Not a valid code rule at {pointer}: {first['msg']}.",
        {"pointer": pointer, "errors": len(exc.errors())},
        next_tool=TEST_TOOL,
        why="try the corrected document (nothing is stored)",
    )


def _cap(setting: str, limit: int, actual: int, item: str) -> None:
    if actual > limit:
        raise CodeRuleError(
            413,
            "cap_exceeded",
            f"{setting} allows {limit}; this {item} has {actual}. Nothing was truncated.",
            {"setting": setting, "limit": limit, "actual": actual, "item": item},
            next_tool="dataworks_get_runner_contract",
            why="read the caps in force",
        )


def parse_document(raw: Any) -> tuple[CodeRuleDocument, dict[str, Any]]:
    """The document as the contract's model, its caps checked; or a named refusal."""
    if not isinstance(raw, Mapping):
        raise CodeRuleError(
            422,
            "code_rule_invalid",
            "The document must be a JSON object (a midataworks.runner/v1 code-rule document).",
            {"pointer": "/document"},
            next_tool="dataworks_get_runner_contract",
            why="read the document's shape",
        )
    major = raw.get("runner_contract")
    if (
        isinstance(major, str)
        and major.startswith(CONTRACT_PREFIX)
        and major != (CONTRACT_PREFIX + "v1")
    ):
        raise CodeRuleError(
            422,
            "runner_contract_unknown",
            f"This deployment runs midataworks.runner/v1 only; the document names {major}.",
            {"runner_contract": major, "served": ["midataworks.runner/v1"]},
            next_tool="dataworks_get_runner_contract",
            why="read the contract this deployment serves",
        )
    settings = get_settings()
    source = raw.get("source")
    if isinstance(source, str):
        size = len(source.encode("utf-8", errors="surrogatepass"))
        _cap("CODE_RULE_MAX_SOURCE_BYTES", settings.code_rule_max_source_bytes, size, "source")
    functions = raw.get("functions")
    if isinstance(functions, list):
        _cap(
            "CODE_RULE_MAX_FUNCTIONS",
            settings.code_rule_max_functions,
            len(functions),
            "document's functions",
        )
    schema = raw.get("params_schema")
    if isinstance(schema, Mapping):
        _cap(
            "CODE_RULE_MAX_PARAM_SCHEMA_BYTES",
            settings.code_rule_max_param_schema_bytes,
            len(canonical_json(dict(schema))),
            "parameter schema",
        )
    try:
        document = CodeRuleDocument.model_validate(dict(raw))
    except ValidationError as exc:
        raise _invalid("/document", exc) from None
    return document, document.model_dump(mode="json")


def check_params(document: CodeRuleDocument, params: Any, pointer: str) -> None:
    """``params`` against the document's ``params_schema`` (JSON Schema data validation)."""
    validator = jsonschema.Draft202012Validator(document.params_schema)
    error = next(iter(sorted(validator.iter_errors(params), key=lambda e: list(e.path))), None)
    if error is not None:
        where = pointer + "".join("/" + str(p) for p in error.path)
        raise CodeRuleError(
            422,
            "code_rule_params_invalid",
            f"The parameters at {where} do not satisfy the document's params_schema: "
            f"{error.message[:200]}.",
            {"pointer": where},
            next_tool=TEST_TOOL,
            why="try the corrected parameters",
        )


def parse_cases(raw_cases: Any, document: CodeRuleDocument) -> list[CodeRuleCase]:
    """The cases as the contract's model; each names a declared function with valid params."""
    if not isinstance(raw_cases, list):
        raise CodeRuleError(
            422,
            "code_rule_invalid",
            "cases must be a list of code-rule cases.",
            {"pointer": "/cases"},
            next_tool=TEST_TOOL,
            why="try the corrected cases",
        )
    _cap("CODE_RULE_MAX_CASES", get_settings().code_rule_max_cases, len(raw_cases), "rule's cases")
    declared = {f.name for f in document.functions}
    cases: list[CodeRuleCase] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_cases):
        try:
            case = CodeRuleCase.model_validate(raw)
        except ValidationError as exc:
            raise _invalid(f"/cases/{index}", exc) from None
        if case.id in seen:
            raise CodeRuleError(
                422,
                "code_rule_invalid",
                f"Case id {case.id} appears twice.",
                {"pointer": f"/cases/{index}/id", "case": case.id},
                next_tool=TEST_TOOL,
                why="give each case its own id",
            )
        seen.add(case.id)
        if case.function not in declared:
            raise CodeRuleError(
                422,
                "code_rule_function_missing",
                f"Case {case.id} calls {case.function}, which the document does not declare.",
                {"pointer": f"/cases/{index}/function", "case": case.id, "function": case.function},
                next_tool=TEST_TOOL,
                why="declare the function or correct the case",
            )
        check_params(document, case.params, f"/cases/{index}/params")
        cases.append(case)
    return cases


def check_sufficiency(document: CodeRuleDocument, cases: Sequence[CodeRuleCase]) -> None:
    """At least one firing (count >= 1) and one non-firing (count 0) case per function."""
    for function in document.functions:
        counts = [
            c.expect.count
            for c in cases
            if c.function == function.name and hasattr(c.expect, "count")
        ]
        firing, quiet = any(n >= 1 for n in counts), any(n == 0 for n in counts)
        if not (firing and quiet):
            missing = "a firing (count >= 1)" if not firing else "a non-firing (count 0)"
            raise CodeRuleError(
                422,
                "code_rule_cases_missing",
                f"Function {function.name} needs {missing} case before it can be saved.",
                {"function": function.name, "missing": "firing" if not firing else "non_firing"},
                next_tool=TEST_TOOL,
                why="add the missing case and try it",
            )


def parse(raw_document: Any, raw_cases: Any, *, require_cases: bool) -> Parsed:
    """Every static check of a document and its cases, in order."""
    document, dumped = parse_document(raw_document)
    cases = parse_cases(raw_cases if raw_cases is not None else [], document)
    if require_cases:
        check_sufficiency(document, cases)
    return Parsed(
        document=document,
        raw=dumped,
        cases=cases,
        code_sha256=code_sha256(dumped),
        canonical=canonical_document_bytes(dumped).decode("utf-8"),
    )


def save_checks_static(values: Mapping[str, Any], actor: Any) -> None:
    """The save route's ``before`` hook: the static checks, before an agent call is queued."""
    body = values["body"]
    if not get_settings().code_rules_enabled:
        from .errors import disabled

        raise disabled()
    parse(body.document, body.cases, require_cases=True)
