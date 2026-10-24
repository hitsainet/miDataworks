"""miStudio's behaviour catalogue read as 017 reads it (FTASKS 3.4; FR-017.10, FTID 017 §7.8).

Two sides of one shape:

1. ``tests/support/fake_mistudio.py`` serves ``GET /api/v1/probe-monitors/behaviours/{key}`` with
   ``catalogue_head`` and miStudio's ``404 not_found``; ``MiStudioClient.get_behaviour`` reads the
   seven fields ``key``, ``current_version``, ``archived`` and ``definition.{version,
   positive_meaning, negative_meaning, target_fpr}``, each changed one at a time.
2. miStudio itself, read by AST from ``MISTUDIO_REPO`` (ADR-001: never an import): the route is
   ``/probe-monitors`` + ``/behaviours`` + ``/{key}``, its serialisers ``head_out`` and
   ``version_out`` return dicts holding those keys, and an unknown key is ``404 not_found``. An
   absent checkout skips loudly; ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` makes it a failure.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from src.clients import mistudio_client as mc
from tests.support.fake_mistudio import BASE, FakeMiStudio, catalogue_head
from tests.support.siblings import sibling_repo

HEAD_FIELDS = {"key", "current_version", "archived", "definition"}
DEFINITION_FIELDS = {"version", "positive_meaning", "negative_meaning", "target_fpr"}
MISTUDIO = sibling_repo("miStudio", "MISTUDIO_REPO")
ROUTES = MISTUDIO / "backend" / "src" / "api" / "v1" / "endpoints" / "behaviours.py"
CATALOGUE = MISTUDIO / "backend" / "src" / "services" / "behaviour_catalogue.py"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeMiStudio:
    served = FakeMiStudio()
    monkeypatch.setattr(mc, "TRANSPORT", served.transport())
    return served


def test_the_client_reads_each_field_from_the_route(fake: FakeMiStudio) -> None:
    fake.behaviours["key-one"] = catalogue_head("key-one", version=2, positive_meaning="m2")
    with mc.MiStudioClient(BASE) as client:
        head = client.get_behaviour("key-one")
        assert head is not None
        assert HEAD_FIELDS <= set(head) and DEFINITION_FIELDS <= set(head["definition"])
        assert (head["key"], head["current_version"], head["archived"]) == ("key-one", 2, False)
        assert head["definition"]["version"] == 2 and head["definition"]["positive_meaning"] == "m2"
        fake.behaviours["key-one"] = catalogue_head(
            "key-one", version=3, negative_meaning="n3", target_fpr=0.05, archived=True
        )
        again = client.get_behaviour("key-one")
        assert again is not None and again["current_version"] == 3 and again["archived"] is True
        assert again["definition"]["negative_meaning"] == "n3"
        assert again["definition"]["target_fpr"] == 0.05
        assert client.get_behaviour("key-absent") is None  # miStudio's 404 not_found
    calls = fake.calls("GET", "/api/v1/probe-monitors/behaviours/")
    assert [c.path for c in calls] == [
        "/api/v1/probe-monitors/behaviours/key-one",
        "/api/v1/probe-monitors/behaviours/key-one",
        "/api/v1/probe-monitors/behaviours/key-absent",
    ]


def _tree(path: Path) -> ast.Module:
    if not path.exists():
        message = f"no miStudio file at {path}"
        if os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1":
            pytest.fail("CROSS-REPO (required): " + message)
        pytest.skip("CROSS-REPO: " + message)
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"miStudio has no function {name}")


def _returned_keys(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Dict):
            keys |= {k.value for k in node.keys if isinstance(k, ast.Constant)}
    return keys


def _constant(tree: ast.Module, name: str) -> str:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            assert isinstance(node.value, ast.Constant)
            return str(node.value.value)
    raise AssertionError(f"miStudio has no {name}")


def test_mistudio_serves_the_route_and_the_fields() -> None:
    routes = _tree(ROUTES)
    catalogue = _tree(CATALOGUE)
    prefix = next(
        kw.value.value
        for node in ast.walk(routes)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "APIRouter"
        for kw in node.keywords
        if kw.arg == "prefix" and isinstance(kw.value, ast.Constant)
    )
    base = _constant(routes, "BASE")
    getter = _function(routes, "get_behaviour")
    paths = []
    for decorator in getter.decorator_list:
        assert isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
        assert decorator.func.attr == "get"
        (arg,) = decorator.args
        assert isinstance(arg, ast.JoinedStr)
        rendered = "".join(
            (
                str(v.value)
                if isinstance(v, ast.Constant)
                else (base if isinstance(v.value, ast.Name) and v.value.id == "BASE" else "?")
            )
            for v in arg.values
            if isinstance(v, (ast.Constant, ast.FormattedValue))
        )
        paths.append(rendered)
    assert f"/api/v1{prefix}" + paths[0].replace("{{key}}", "{key}") == (
        "/api/v1/probe-monitors/behaviours/{key}"
    ), (prefix, paths)
    assert HEAD_FIELDS <= _returned_keys(_function(catalogue, "head_out"))
    assert DEFINITION_FIELDS <= _returned_keys(_function(catalogue, "version_out"))
    refusals = [
        node
        for node in ast.walk(_function(catalogue, "get_head"))
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "BehaviourRefused"
    ]
    assert any(
        [a.value for a in r.args[:2] if isinstance(a, ast.Constant)] == [404, "not_found"]
        for r in refusals
    ), "an uncatalogued key is no longer 404 not_found"
