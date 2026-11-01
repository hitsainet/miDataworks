"""The supported JSON Schema subset (FR-003.6, FR-003.7; FTASKS 2.5, 2.6)."""

from __future__ import annotations

from typing import Any

import pytest

from src.operators import schema_subset
from src.operators.schema_subset import check, validate


def _schema(props: dict[str, Any], **top: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
    base.update(top)
    return base


EVERY_ALLOWED = _schema(
    {
        "s": {
            "type": "string",
            "minLength": 1,
            "maxLength": 9,
            "pattern": "^a",
            "title": "S",
            "description": "d",
            "default": "a",
            "x-hint": "h",
            "x-widget": "textarea",
        },
        "i": {
            "type": "integer",
            "minimum": 0,
            "maximum": 9,
            "exclusiveMinimum": -1,
            "exclusiveMaximum": 10,
            "multipleOf": 1,
            "x-unit": "characters",
            "x-widget": "slider",
        },
        "n": {"type": "number", "minimum": 0.5, "x-advanced": True},
        "b": {"type": "boolean", "default": False},
        "e": {"enum": ["a", "b"], "x-widget": "select"},
        "c": {"const": 3},
        "a": {
            "type": "array",
            "items": {"type": "string", "enum": ["x", "y"]},
            "minItems": 1,
            "maxItems": 2,
            "uniqueItems": True,
        },
        "an": {"type": "array", "items": {"type": "number", "minimum": 0}},
    },
    required=["s"],
    title="T",
    description="D",
)


def test_every_allowed_keyword_is_accepted() -> None:
    assert check(EVERY_ALLOWED) == []


@pytest.mark.parametrize(
    ("schema", "pointer", "keyword"),
    [
        (_schema({"x": {"$ref": "#/$defs/y"}}), "/properties/x/$ref", "$ref"),
        (_schema({"x": {"oneOf": [{"type": "string"}]}}), "/properties/x/oneOf", "oneOf"),
        (_schema({"x": {"anyOf": [{"type": "string"}]}}), "/properties/x/anyOf", "anyOf"),
        (_schema({"x": {"type": "string", "allOf": []}}), "/properties/x/allOf", "allOf"),
        (_schema({"x": {"type": "string", "not": {}}}), "/properties/x/not", "not"),
        (_schema({"x": {"type": "string", "if": {}}}), "/properties/x/if", "if"),
        (_schema({"x": {"type": "object", "properties": {}}}), "/properties/x/type", "type"),
        (_schema({"x": {"type": "string", "format": "email"}}), "/properties/x/format", "format"),
        (_schema({}, patternProperties={"^a": {}}), "/patternProperties", "patternProperties"),
        (
            _schema({"x": {"type": "string", "x-widget": "dial"}}),
            "/properties/x/x-widget",
            "x-widget",
        ),
        (
            _schema({"x": {"type": "array", "items": {"type": "object"}}}),
            "/properties/x/items/type",
            "type",
        ),
    ],
)
def test_every_refused_keyword_is_named_with_its_pointer(
    schema: dict[str, Any], pointer: str, keyword: str
) -> None:
    assert (pointer, keyword) in check(schema)


def test_additional_properties_false_is_required() -> None:
    schema = {"type": "object", "properties": {}}
    assert ("/additionalProperties", "additionalProperties") in check(schema)
    schema["additionalProperties"] = True
    assert ("/additionalProperties", "additionalProperties") in check(schema)


def test_required_must_name_a_property() -> None:
    assert ("/required", "required") in check(_schema({}, required=["ghost"]))


def test_describe_marks_refused_keywords() -> None:
    text = schema_subset.describe([("/properties/x/$ref", "$ref")])
    assert "'$ref' at /properties/x/$ref (refused)" == text


def test_min_len_abc_is_params_invalid_at_its_pointer() -> None:
    """FTASKS 2.6: the FR-003.7 regression, on the validator itself."""
    schema = _schema({"min_len": {"type": "integer"}}, required=["min_len"])
    errors = validate(schema, {"min_len": "abc"})
    assert errors == [{"pointer": "/min_len", "message": "'abc' is not of type 'integer'"}]


def test_validate_reports_every_error_sorted() -> None:
    schema = _schema(
        {"a": {"type": "integer"}, "b": {"type": "string", "minLength": 3}}, required=["a"]
    )
    errors = validate(schema, {"b": "x", "z": 1})
    pointers = [e["pointer"] for e in errors]
    assert pointers == sorted(pointers)
    assert len(errors) == 3  # missing a, short b, unknown z


def test_subset_definition_lists_what_check_accepts() -> None:
    """The published subset is derived from the same constants check() uses."""
    for kind, keywords in schema_subset.SUBSET["by_type"].items():
        for keyword in keywords:
            value: Any = 1
            if keyword == "items":
                value = {"type": "string"}
            elif keyword == "uniqueItems":
                value = True
            elif keyword == "pattern":
                value = "a"
            prop = {"type": kind, keyword: value}
            if kind == "array" and keyword != "items":
                prop["items"] = {"type": "string"}
            assert check(_schema({"p": prop})) == [], (kind, keyword)


def test_the_frontend_renderer_fixture_is_the_backend_subset() -> None:
    """The renderer test (frontend operators.test.tsx) reads this file; it must BE the subset the
    route publishes, or the two could drift with both suites green."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "frontend" / "src" / "test" / "schemaSubset.json"
    assert json.loads(path.read_text()) == json.loads(json.dumps(schema_subset.SUBSET))
