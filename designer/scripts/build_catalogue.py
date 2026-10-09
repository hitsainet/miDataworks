"""Generate the committed Data Designer catalogue (FR-003.15; FTASKS 8.2; FTDD 003 section 6.5).

Runs ONLY in the Data Designer image or its CI job (``designer-tests``): it imports
``data_designer``, which needs ``huggingface-hub<2`` while the backend pins 2.1.1 (ADR-010
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


def build() -> dict[str, Any]:
    import data_designer.config as c
    from importlib.metadata import version

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
            "provider_version": dd_version,
            "kind": kind,
            "scope": "row",
            "description": description,
            "input_columns": [],
            "output_columns": [{"name": name.removeprefix("dd_"), "type": "string", "role": "metadata"}],
            "params_schema": params_schema,
            "thresholds": [],
            "resources": {"queue": "designer", "endpoint_role": role},
            "deterministic": False,
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
