"""The committed schema file is exactly what the model generates (FR-008.34; FTASKS 2.5).

Both copies — ``docs/schemas/`` (what consumers vendor) and ``src/schemas/data/`` (what the image
ships and the worker validates against) — must equal ``render_schema_file()`` byte for byte, and
no module in ``src/`` may write either path: the script is the only writer.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.schemas.dataset_version import (
    PACKAGED_SCHEMA_PATH,
    REGENERATE_COMMAND,
    SCHEMA_FILE_NAME,
    render_schema_file,
)

BACKEND = Path(__file__).resolve().parents[3]
DOCS_COPY = BACKEND.parent / "docs" / "schemas" / SCHEMA_FILE_NAME
WRITER = BACKEND / "scripts" / "write_dataset_version_schema.py"


def test_the_docs_copy_matches_the_model_byte_for_byte() -> None:
    assert (
        DOCS_COPY.read_bytes() == render_schema_file()
    ), f"{DOCS_COPY} differs from the pydantic model. Regenerate: {REGENERATE_COMMAND}"


def test_the_packaged_copy_matches_the_model_byte_for_byte() -> None:
    assert (
        PACKAGED_SCHEMA_PATH.read_bytes() == render_schema_file()
    ), f"{PACKAGED_SCHEMA_PATH} differs from the pydantic model. Regenerate: {REGENERATE_COMMAND}"


def test_the_file_is_valid_json_with_the_header() -> None:
    import json

    doc = json.loads(DOCS_COPY.read_bytes())
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert doc["$id"].endswith("/docs/schemas/" + SCHEMA_FILE_NAME)
    assert doc["title"] == "miDataworks Dataset Version v1"
    assert "GENERATED" in doc["description"]


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
        if isinstance(node, ast.Name) and node.id in {"SCHEMA_FILE_NAME", "PACKAGED_SCHEMA_PATH"}:
            return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if SCHEMA_FILE_NAME in node.value:
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
