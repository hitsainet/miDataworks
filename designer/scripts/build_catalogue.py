"""Generate the committed Data Designer catalogue (FR-003.15; FTASKS 8.2; FTDD 003 section 6.5).

Runs ONLY in the Data Designer image or its CI job (``designer-tests``): it imports
``data_designer``, which needs ``huggingface-hub<2`` while the backend pins 2.2.0 (ADR-010
amendment, 2026-10-07). ``designer/tests/test_catalogue_in_sync.py`` regenerates and compares bytes.
For each offered column type it derives a parameter schema from Data Designer's own Pydantic
column-config class, keeping only fields whose type fits the supported subset (``str``, ``int``,
``float``, ``bool``, lists of those); every omitted field is listed with its reason. Fields that
choose a model (``model_alias``) are replaced by the manifest's ``endpoint_role``, so a recipe
never names a model provider; ``name`` becomes ``output_column``. The inference parameters
(``temperature``, ``top_p``, ``max_tokens``) are added for LLM columns.

    python designer/scripts/build_catalogue.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
import typing
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from src.operators import schema_subset  # noqa: E402  (stdlib-only module)

OUTPUT = BACKEND / "src" / "operators" / "data_designer" / "catalogue.json"
OFFERED = {
    # operator name: (column class, kind, endpoint role, description)
    "dd_llm_text": ("LLMTextColumnConfig", "labeler", "generation",
                    "Generates text for each row with a prompt template, through the generation endpoint."),
    "dd_llm_structured": ("LLMStructuredColumnConfig", "labeler", "generation",
                          "Generates structured output for each row, through the generation endpoint."),
    "dd_llm_judge": ("LLMJudgeColumnConfig", "labeler", "judge",
                     "Scores each row with a judge prompt, through the judge endpoint."),
    "dd_expression": ("ExpressionColumnConfig", "labeler", None,
                      "Computes a column from other columns with a template expression."),
    "dd_validation": ("ValidationColumnConfig", "labeler", None,
                      "Validates target columns and records the result as a column."),
}
#: Operators whose parameters are Jinja templates rendered by Data Designer.
TEMPLATED = {"dd_llm_text", "dd_llm_structured", "dd_llm_judge", "dd_expression"}
SKIP = {"name", "model_alias", "column_type", "drop", "skip", "propagate_skip", "tool_alias",
        "multi_modal_context", "with_trace", "extract_reasoning_content"}
SCALAR = {str: "string", int: "integer", float: "number", bool: "boolean"}
INFERENCE = {
    "temperature": {"type": "number", "minimum": 0, "maximum": 2, "x-advanced": True},
    "top_p": {"type": "number", "minimum": 0, "maximum": 1, "x-advanced": True},
    "max_tokens": {"type": "integer", "minimum": 1, "x-advanced": True},
}


def _schema_for(annotation: Any) -> dict[str, Any] | None:
    origin = typing.get_origin(annotation)
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)) and len(args) == 1:
        return _schema_for(args[0])
    if annotation in SCALAR:
        return {"type": SCALAR[annotation]}
    if origin is list and len(args) == 1 and args[0] in SCALAR:
        return {"type": "array", "items": {"type": SCALAR[args[0]]}}
    if origin is typing.Literal and all(isinstance(a, str) for a in args):
        return {"enum": list(args)}
    return None


def template_rules() -> str:
    """Data Designer's template limits, read from Data Designer itself (PII run G4, 2026-10-20):
    the manifest states the rules ``dataworks_validate_operator_params`` checks."""
    from data_designer.engine.processing.ginja.environment import (
        ALLOWED_JINJA_FILTERS, MAX_AST_DEPTH, MAX_AST_NODE_COUNT)

    return (
        " Templates follow Data Designer's sandbox rules: no {% set %}, {% include %}, {% import %},"
        " {% macro %}, {% extends %} or {% block %}; no loop inside a loop and no recursive loop;"
        f" no {{{{ self }}}}; at most {MAX_AST_NODE_COUNT} template nodes and nesting depth"
        f" {MAX_AST_DEPTH}; filters only from: {', '.join(ALLOWED_JINJA_FILTERS)}."
        " A template sees the content columns and any other non-system column it names (a"
        " metadata column or an earlier dd_* step's output); _dw_* columns are never visible."
        " dataworks_validate_operator_params checks all of this and names the rule that fails."
    )


#: The adapter's revision, part of every manifest's provider_version and so of its manifest hash.
#: It changes when the adapter changes what a step PRODUCES for the same parameters, so a step
#: computed under the old behaviour is never reused: dw.2 (DLP run F1, 2026-10-21) keeps a
#: declared string a string between steps and stores each output column as its declared dtype.
ADAPTER_REVISION = "dw.2"

EMPTY_RENDER = (
    " A row whose expression renders empty is dropped with reason dd.empty_render (an intended"
    " skip); dd.render_error and dd.type_cast_error mark real failures."
)


KEPT_TYPES = (
    " A template sees each column as the type it was stored with: text stays text (\"9\" is"
    " never 9, \"[ ]\" never a list, JSON text never an object), and the output is stored as"
    " its dtype (str text, int int64, float float64, bool boolean); a value of another type"
    " fails the step with dtype_violation."
)
KEPT_STRINGS = (
    " A template sees each column as the type it was stored with: text stays text (JSON text"
    " is never parsed into an object)."
)


def build() -> dict[str, Any]:
    import data_designer.config as c
    from importlib.metadata import version

    rules = template_rules()

    dd_version = version("data-designer")
    operators = []
    rejected: list[dict[str, Any]] = []
    for name, (cls_name, kind, role, description) in sorted(OFFERED.items()):
        cls = getattr(c, cls_name)
        properties: dict[str, Any] = {
            "output_column": {"type": "string", "default": name.removeprefix("dd_"),
                              "title": "Output column", "x-hint": "The column this step writes."}
        }
        required: list[str] = []
        omitted = []
        fields = []
        for field, info in cls.model_fields.items():
            if field in SKIP:
                continue
            schema = _schema_for(info.annotation)
            if schema is None:
                omitted.append({"field": field, "reason": f"type {info.annotation!r} is outside the subset"})
                continue
            if info.description:
                schema["description"] = info.description[:200]
            if field == "prompt" or field == "system_prompt" or field == "expr":
                schema["x-widget"] = "textarea"
            if info.is_required():
                required.append(field)
            elif info.default is not None and not callable(info.default):
                try:
                    json.dumps(info.default)
                    schema["default"] = info.default
                except TypeError:
                    pass
            properties[field] = schema
            fields.append(field)
        required_omitted = [
            o["field"] for o in omitted if cls.model_fields[o["field"]].is_required()
        ]
        if required_omitted:
            rejected.append(
                {
                    "name": name,
                    "column_class": cls_name,
                    "reason": f"required field(s) {required_omitted} are outside the supported "
                    "subset, so the operator could never be configured",
                }
            )
            continue
        if role is not None:
            properties.update(INFERENCE)
        params_schema = {"type": "object", "properties": properties, "required": sorted(required),
                         "additionalProperties": False}
        violations = schema_subset.check(params_schema)
        if violations:
            raise SystemExit(f"{name}: {schema_subset.describe(violations)}")
        manifest = {
            "name": name,
            "version": f"dd{dd_version}-1",
            "provider": "data_designer",
            "provider_version": f"{dd_version}+{ADAPTER_REVISION}",
            "kind": kind,
            "scope": "row",
            "description": description + (rules if name in TEMPLATED else "")
            + (EMPTY_RENDER if cls_name == "ExpressionColumnConfig" else "")
            + (KEPT_TYPES if cls_name == "ExpressionColumnConfig" else KEPT_STRINGS),
            "input_columns": [],
            "output_columns": [{"name": name.removeprefix("dd_"), "type": "string", "role": "metadata"}],
            "params_schema": params_schema,
            "thresholds": [],
            "resources": {"queue": "designer", "endpoint_role": role},
            # F-2 (PII run, 2026-10-20): a column that calls no model is a pure function of its
            # row, so its output is reproducible on a rebuild.
            "deterministic": role is None,
        }
        operators.append({"column_class": cls_name, "column_fields": sorted(fields),
                          "omitted_fields": omitted, "manifest": manifest})
    return {"format": "midataworks.data-designer-catalogue/v1", "provider": "data_designer",
            "provider_version": dd_version, "operators": operators, "rejected": rejected}


def render(document: dict[str, Any]) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    data = render(build())
    if parser.parse_args().check:
        return 0 if OUTPUT.exists() and OUTPUT.read_bytes() == data else 1
    OUTPUT.write_bytes(data)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
