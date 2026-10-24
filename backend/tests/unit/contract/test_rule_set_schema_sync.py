"""The published rule-set schema is exactly what ``RuleSetDocument`` generates (FR-016.10; FTASKS 2.7).

Both copies - ``docs/schemas/`` (what miStudio 040 and miLLM 039 vendor) and ``src/schemas/data/``
(what the image ships) - equal ``render_schema_file()`` byte for byte, and no module in ``src/``
writes either path: ``scripts/write_rule_set_schema.py`` is the only writer.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import jsonschema

from src.rules import cases as rule_cases
from src.rules.rule_set import (
    PACKAGED_SCHEMA_PATH,
    REGENERATE_COMMAND,
    SCHEMA_FILE_NAME,
    render_schema_file,
)
from tests.unit.contract.test_rubric_schema_sync import _writes_files

BACKEND = Path(__file__).resolve().parents[3]
DOCS_COPY = BACKEND.parent / "docs" / "schemas" / SCHEMA_FILE_NAME
WRITER = BACKEND / "scripts" / "write_rule_set_schema.py"
FIXTURES = BACKEND / "tests" / "fixtures" / "behaviours"


def test_the_docs_copy_is_the_generated_one() -> None:
    assert (
        DOCS_COPY.read_bytes() == render_schema_file()
    ), f"stale; regenerate: {REGENERATE_COMMAND}"


def test_the_packaged_copy_is_the_generated_one() -> None:
    assert (
        PACKAGED_SCHEMA_PATH.read_bytes() == render_schema_file()
    ), f"regenerate: {REGENERATE_COMMAND}"


def test_the_file_carries_the_header_prose_and_listing() -> None:
    doc = json.loads(DOCS_COPY.read_bytes())
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert doc["$id"].endswith("/docs/schemas/" + SCHEMA_FILE_NAME)
    assert "GENERATED" in doc["description"]
    for words in (
        "PATTERN DIALECT",
        "within_n",
        "VETO",
        "RENDERINGS",
        "IDENTITY",
        "RULE_SET_MAX_SIGNALS",
    ):
        assert words.lower() in doc["$comment"].lower(), words
    assert doc["$defs"]["RuleSetUse"]["enum"] == [
        "label",
        "veto",
        "screen",
        "decoy_check",
        "conflict_scan",
    ]
    assert "RuleSetListing" in doc["$defs"]


def test_the_published_file_accepts_the_fixtures_and_refuses_a_nested_rule_set() -> None:
    validator = jsonschema.Draft202012Validator(json.loads(DOCS_COPY.read_bytes()))
    for name in ("fixture-alpha", "fixture-beta"):
        doc = json.loads((FIXTURES / name / "rule_set.json").read_text(encoding="utf-8"))
        assert not list(validator.iter_errors(doc)), name
    for doc in rule_cases.load_cases()["rule_documents"].values():
        assert not list(validator.iter_errors(doc))
    nested = json.loads((FIXTURES / "fixture-beta" / "rule_set.json").read_text(encoding="utf-8"))
    nested["expressions"]["any_word"] = {"rule_set": {"name": "x", "version": 1}}
    assert list(validator.iter_errors(nested))


def _names_the_schema(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in {"SCHEMA_FILE_NAME", "PACKAGED_SCHEMA_PATH"}:
            return True
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and SCHEMA_FILE_NAME in node.value
        ):
            return True
    return False


def test_only_the_script_writes_the_schema_file() -> None:
    offenders = [
        str(p.relative_to(BACKEND))
        for p in sorted((BACKEND / "src").rglob("*.py"))
        if _names_the_schema(t := ast.parse(p.read_text())) and _writes_files(t)
    ]
    assert offenders == []


def test_the_writer_guard_sees_the_script() -> None:
    tree = ast.parse(WRITER.read_text())
    assert _names_the_schema(tree) and _writes_files(tree)
