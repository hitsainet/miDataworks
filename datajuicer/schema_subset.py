"""The supported JSON Schema subset for operator parameters (FR-003.6, FR-003.7; FTDD 003 section 5.4).

ONE authority for the subset. The backend registry calls :func:`check` on every manifest, the API
and the worker call :func:`validate` on every parameter set, the frontend reads :data:`SUBSET`
from ``GET /api/v1/operators/schema-subset`` and a test proves its renderer handles every keyword
listed here. The Data-Juicer image carries a BYTE COPY of this file (``datajuicer/schema_subset.py``)
because that image cannot import ``src``; ``test_schema_subset_copy.py`` pins the copy.

So this module imports the standard library only, and ``jsonschema`` lazily inside
:func:`validate`: the catalogue builder in the Data-Juicer image needs :func:`check` alone.

Refused keywords (``$ref``, ``oneOf``, ``anyOf``, ``allOf``, ``not``, ``if``/``then``/``else``,
nested ``object``, ``format``, ``patternProperties``, ``dependentSchemas``, and anything else not
listed) fail registration with the keyword and its JSON pointer named, so an operator is never
rendered silently without a field.
"""

from __future__ import annotations

from typing import Any

#: Keywords allowed at the top level of ``params_schema``.
TOP_LEVEL: frozenset[str] = frozenset(
    {"type", "properties", "required", "additionalProperties", "title", "description", "$schema"}
)

#: Scalar property types, and ``array`` of a scalar.
SCALAR_TYPES: frozenset[str] = frozenset({"string", "integer", "number", "boolean"})
PROPERTY_TYPES: frozenset[str] = SCALAR_TYPES | {"array"}

#: Keywords allowed on any property.
COMMON: frozenset[str] = frozenset({"type", "enum", "const", "default", "title", "description"})
#: Keywords allowed per property type, beyond COMMON.
BY_TYPE: dict[str, frozenset[str]] = {
    "string": frozenset({"minLength", "maxLength", "pattern"}),
    "integer": frozenset(
        {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    ),
    "number": frozenset(
        {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    ),
    "boolean": frozenset(),
    "array": frozenset({"items", "minItems", "maxItems", "uniqueItems"}),
}
#: Keywords allowed inside an array's ``items`` (a scalar or an enum).
ITEMS: frozenset[str] = COMMON | BY_TYPE["string"] | BY_TYPE["number"]
#: Extensions the form renderer understands.
EXTENSIONS: frozenset[str] = frozenset({"x-unit", "x-hint", "x-advanced", "x-widget"})
WIDGETS: frozenset[str] = frozenset({"slider", "textarea", "select"})

#: Named so the refusal message says "refused", not merely "unknown".
REFUSED: frozenset[str] = frozenset(
    {
        "$ref",
        "$defs",
        "definitions",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "if",
        "then",
        "else",
        "format",
        "patternProperties",
        "dependentSchemas",
        "dependentRequired",
        "propertyNames",
        "unevaluatedProperties",
        "prefixItems",
        "contains",
    }
)

#: The published definition (``GET /api/v1/operators/schema-subset``). Plain lists, sorted, so it
#: serialises the same way every time.
SUBSET: dict[str, Any] = {
    "draft": "https://json-schema.org/draft/2020-12/schema",
    "top_level": sorted(TOP_LEVEL),
    "property_types": sorted(PROPERTY_TYPES),
    "common": sorted(COMMON),
    "by_type": {key: sorted(value) for key, value in sorted(BY_TYPE.items())},
    "items": sorted(ITEMS),
    "extensions": sorted(EXTENSIONS),
    "widgets": sorted(WIDGETS),
    "refused": sorted(REFUSED),
}

Violation = tuple[str, str]


def _pointer(*parts: str) -> str:
    return "".join("/" + p.replace("~", "~0").replace("/", "~1") for p in parts)


def _type_of(schema: dict[str, Any]) -> str | None:
    value = schema.get("type")
    return value if isinstance(value, str) else None


def _check_property(name: str, prop: Any, base: tuple[str, ...], out: list[Violation]) -> None:
    here = (*base, name)
    if not isinstance(prop, dict):
        out.append((_pointer(*here), "type"))
        return
    kind = _type_of(prop)
    if kind is None and "enum" not in prop and "const" not in prop:
        out.append((_pointer(*here, "type"), "type"))
    elif kind is not None and kind not in PROPERTY_TYPES:
        # "object" (nested), "null", or a list of types
        out.append((_pointer(*here, "type"), "type"))
    allowed = COMMON | EXTENSIONS | (BY_TYPE.get(kind, frozenset()) if kind else frozenset())
    for keyword in sorted(prop):
        if keyword not in allowed:
            out.append((_pointer(*here, keyword), keyword))
    widget = prop.get("x-widget")
    if widget is not None and widget not in WIDGETS:
        out.append((_pointer(*here, "x-widget"), "x-widget"))
    if kind == "array":
        items = prop.get("items")
        if not isinstance(items, dict):
            out.append((_pointer(*here, "items"), "items"))
            return
        item_kind = _type_of(items)
        if item_kind is not None and item_kind not in SCALAR_TYPES:
            out.append((_pointer(*here, "items", "type"), "type"))
        if item_kind is None and "enum" not in items:
            out.append((_pointer(*here, "items", "type"), "type"))
        for keyword in sorted(items):
            if keyword not in ITEMS | EXTENSIONS:
                out.append((_pointer(*here, "items", keyword), keyword))


def check(schema: Any) -> list[Violation]:
    """Every ``(json pointer, keyword)`` in ``schema`` outside the subset. Empty means supported."""
    out: list[Violation] = []
    if not isinstance(schema, dict):
        return [("", "type")]
    for keyword in sorted(schema):
        if keyword not in TOP_LEVEL:
            out.append((_pointer(keyword), keyword))
    if schema.get("type") != "object":
        out.append((_pointer("type"), "type"))
    if schema.get("additionalProperties") is not False:
        out.append((_pointer("additionalProperties"), "additionalProperties"))
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        out.append((_pointer("properties"), "properties"))
        properties = {}
    for name in sorted(properties):
        _check_property(name, properties[name], ("properties",), out)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in properties for r in required):
        out.append((_pointer("required"), "required"))
    return out


def describe(violations: list[Violation]) -> str:
    """One sentence naming each violation, for a refusal message."""
    parts = [
        f"{keyword!r} at {pointer or '/'}"
        + (" (refused)" if keyword in REFUSED else " (not in the supported subset)")
        for pointer, keyword in violations
    ]
    return "; ".join(parts)


def validate(schema: dict[str, Any], params: Any) -> list[dict[str, str]]:
    """Every error in ``params`` against ``schema``, as ``{pointer, message}``, sorted by pointer.

    Draft 2020-12 with no format checker (FTDD 003 section 3). All errors are collected, not the
    first, so a form can mark every bad field at once.
    """
    import jsonschema  # lazy: the Data-Juicer image imports this module for check() only

    validator = jsonschema.Draft202012Validator(schema)
    errors = [
        {"pointer": _pointer(*(str(p) for p in error.absolute_path)), "message": error.message}
        for error in validator.iter_errors(params)
    ]
    return sorted(errors, key=lambda e: (e["pointer"], e["message"]))
