"""The published rubric schema is exactly what ``RubricExport`` generates (FR-015.18; FTDD 015 4.5).

Both copies — ``docs/schemas/`` (what miStudio vendors, miStudio 037 FR-15b) and ``src/schemas/data/``
(what the image ships) — must equal ``render_rubric_schema_file()`` byte for byte, and no module in
``src/`` may write either path: ``scripts/write_rubric_schema.py`` is the only writer.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import jsonschema

from src.schemas.labeling import (
    RUBRIC_PACKAGED_SCHEMA_PATH,
    RUBRIC_REGENERATE_COMMAND,
    RUBRIC_SCHEMA_FILE_NAME,
    RubricExport,
    render_rubric_schema_file,
)

BACKEND = Path(__file__).resolve().parents[3]
DOCS_COPY = BACKEND.parent / "docs" / "schemas" / RUBRIC_SCHEMA_FILE_NAME
WRITER = BACKEND / "scripts" / "write_rubric_schema.py"

BODY = {
    "style": "binary",
    "messages": [{"role": "user", "content": "Is {text} personal data? Answer yes or no."}],
    "input_fields": ["text"],
    "parser": "verdict_line_v1",
    "allowed_verdicts": ["yes", "no"],
}


def test_the_docs_copy_matches_the_model_byte_for_byte() -> None:
    assert (
        DOCS_COPY.read_bytes() == render_rubric_schema_file()
    ), f"{DOCS_COPY} differs from RubricExport. Regenerate: {RUBRIC_REGENERATE_COMMAND}"


def test_the_packaged_copy_matches_the_model_byte_for_byte() -> None:
    assert (
        RUBRIC_PACKAGED_SCHEMA_PATH.read_bytes() == render_rubric_schema_file()
    ), f"{RUBRIC_PACKAGED_SCHEMA_PATH} differs from RubricExport. Regenerate: {RUBRIC_REGENERATE_COMMAND}"


def test_the_file_is_valid_json_with_the_header() -> None:
    doc = json.loads(DOCS_COPY.read_bytes())
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert doc["$id"].endswith("/docs/schemas/" + RUBRIC_SCHEMA_FILE_NAME)
    assert doc["title"] == "miDataworks Rubric v1"
    assert "GENERATED" in doc["description"]
    assert doc["additionalProperties"] is False
    assert doc["properties"]["format"]["const"] == "midataworks.rubric/v1"
    # D37: the wire name is ``behaviour``, optional, never ``behaviour_key``.
    assert "behaviour" in doc["properties"] and "behaviour" not in doc["required"]
    assert "behaviour_key" not in doc["properties"]


def test_the_published_file_accepts_and_refuses_what_the_model_does() -> None:
    """The file is a usable contract: a real export validates, a bad key and an extra key do not."""
    schema = json.loads(DOCS_COPY.read_bytes())
    validator = jsonschema.Draft202012Validator(schema)
    keyless = RubricExport(
        format="midataworks.rubric/v1", name="pii-judge", version=1, body=BODY
    ).model_dump(mode="json", exclude={"behaviour"})
    keyed = {**keyless, "behaviour": "pii"}
    assert not list(validator.iter_errors(keyless))
    assert not list(validator.iter_errors(keyed))
    assert list(validator.iter_errors({**keyless, "behaviour": "Pii"}))
    assert list(validator.iter_errors({**keyless, "behaviour_key": "pii"}))


_WRITE_ATTRS = {"write_bytes", "write_text"}


def _writes_files(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _WRITE_ATTRS:
            return True
        if isinstance(func, ast.Name) and func.id == "open":
            modes = list(node.args[1:2]) + [k.value for k in node.keywords if k.arg == "mode"]
            for mode in modes:
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    if any(c in mode.value for c in "wax+"):
                        return True
    return False


def _names_the_schema(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {
            "RUBRIC_SCHEMA_FILE_NAME",
            "RUBRIC_PACKAGED_SCHEMA_PATH",
        }:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if RUBRIC_SCHEMA_FILE_NAME in node.value:
                return True
    return False


def test_only_the_script_writes_the_schema_file() -> None:
    offenders: list[str] = []
    for path in sorted((BACKEND / "src").rglob("*.py")):
        tree = ast.parse(path.read_text())
        if _names_the_schema(tree) and _writes_files(tree):
            offenders.append(str(path.relative_to(BACKEND)))
    assert offenders == [], f"modules that name the schema file and write files: {offenders}"


def test_the_writer_guard_sees_the_script() -> None:
    """Not vacuous: the guard recognises the real writer."""
    tree = ast.parse(WRITER.read_text())
    assert _names_the_schema(tree) and _writes_files(tree)
