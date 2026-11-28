"""Validate a value against a definition of the vendored ``mistudio.result-language/v1`` (022 FR-022.2).

Tests read the shapes from the vendored file and never retype a field list. If miAssist names its
definitions differently, the names change here only.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import jsonschema

from tests.support.siblings import REPO, sibling_repo

SCHEMA_NAME = "mistudio-result-language-v1.json"
VENDORED = REPO / "docs" / "schemas" / SCHEMA_NAME


def theirs_path() -> Path:
    return sibling_repo("miAssist") / "docs" / "schemas" / SCHEMA_NAME


@cache
def schema() -> dict[str, Any]:
    document: dict[str, Any] = json.loads(VENDORED.read_text(encoding="utf-8"))
    return document


def definition(name: str, document: dict[str, Any] | None = None) -> dict[str, Any]:
    """A self-contained schema for ``#/$defs/<name>`` (its ``$defs`` carried along for refs)."""
    doc = schema() if document is None else document
    if name not in doc["$defs"]:
        raise KeyError(f"the vendored result language has no $defs/{name}")
    return {"$ref": f"#/$defs/{name}", "$defs": doc["$defs"]}


def validate(name: str, value: Any, document: dict[str, Any] | None = None) -> None:
    """Raise ``jsonschema.ValidationError`` unless ``value`` is a valid ``name``."""
    jsonschema.validate(value, definition(name, document))


def status_enum() -> list[str]:
    values: list[str] = schema()["$defs"]["approval_status"]["enum"]
    return values
