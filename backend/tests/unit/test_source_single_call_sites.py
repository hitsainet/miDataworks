"""Each feature-001 decision happens through its one function (001 FTASKS 14.3; FTID 8).

Walks the syntax tree (calls, keywords and string keys), so a comment or docstring naming a
function neither satisfies nor trips a check. A second site for one of these decisions is how a
preview and an import come to disagree about the same repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
SOURCES = SRC / "services" / "sources"
WORKER = SRC / "workers" / "source_tasks.py"
PREVIEW = SOURCES / "preview_service.py"


def modules() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "mcp_server" not in p.parts]


def calls_to(path: Path, name: str) -> list[int]:
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == name) or (
                isinstance(f, ast.Attribute) and f.attr == name
            ):
                out.append(node.lineno)
    return out


def callers(name: str, *, defined_in: Path | None = None) -> set[Path]:
    return {p for p in modules() if calls_to(p, name) and p != defined_in}


def string_keys(path: Path, key: str) -> list[int]:
    tree = ast.parse(path.read_text())
    return [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and n.value == key and not _is_docstring(tree, n)
    ]


def _is_docstring(tree: ast.AST, node: ast.Constant) -> bool:
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr):
            if body[0].value is node:
                return True
    return False


@pytest.mark.parametrize(
    ("function", "home", "expected"),
    [
        ("resolve_revision", SOURCES / "revision.py", {PREVIEW, WORKER}),
        ("licence_from_hub", SOURCES / "licence.py", {PREVIEW, WORKER}),
        ("choose_config", SOURCES / "config_choice.py", {PREVIEW, WORKER}),
        ("choose_token", SOURCES / "tokens.py", {WORKER}),
        ("detect", SOURCES / "detection.py", {PREVIEW}),
        ("detect_stored", SOURCES / "detection.py", {WORKER}),
    ],
)
def test_each_decision_is_made_by_its_function_and_only_there(
    function: str, home: Path, expected: set[Path]
) -> None:
    assert callers(function, defined_in=home) == expected


def test_preview_and_stored_detection_are_the_same_function() -> None:
    """``detect_stored`` delegates to ``detect``: the preview's answer and the stored one agree."""
    assert calls_to(SOURCES / "detection.py", "detect")


def test_the_upload_content_hash_has_one_caller() -> None:
    assert callers("content_hash", defined_in=SOURCES / "upload_service.py") == {
        SRC / "api" / "v1" / "endpoints" / "sources.py"
    }


def test_only_the_revision_module_reads_card_data() -> None:
    readers = {p for p in modules() if string_keys(p, "cardData")}
    assert readers == {SOURCES / "revision.py"}


def test_remote_code_is_set_in_one_place() -> None:
    sites = []
    for p in modules():
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.keyword) and node.arg == "trust_remote_code":
                sites.append((p, node.value))
    assert [p for p, _ in sites] == [SOURCES / "hf_materialise.py"]
    assert all(isinstance(v, ast.Constant) and v.value is False for _, v in sites)


def test_the_token_is_normalised_through_one_function() -> None:
    assert callers("normalise_token", defined_in=SOURCES / "tokens.py") == {
        SRC / "schemas" / "sources.py"
    }
