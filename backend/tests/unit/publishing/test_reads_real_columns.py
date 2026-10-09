"""Every ORM attribute feature 008 reads is a real column (008 FTASKS 4.4; FTID 008 section 4).

miStudio's 033 arc shipped five ORM field names that did not exist, one of which silently GUESSED
a value through a ``getattr`` default. This walks ``services/publishing``, ``services/exports`` and
``workers/publish_tasks.py``: every name bound to an ORM row — a parameter annotated with a model,
or a variable assigned from ``session.get(Model, …)`` — may only be read for a mapped attribute,
and no ``getattr`` with a default may appear at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import inspect as sa_inspect

import src.models as models

SRC = Path(__file__).resolve().parents[3] / "src"
FILES = [
    *sorted((SRC / "services" / "publishing").glob("*.py")),
    *sorted((SRC / "services" / "exports").glob("*.py")),
    SRC / "workers" / "publish_tasks.py",
]
MODELS = {name: getattr(models, name) for name in models.__all__}
#: Python-level members that are not columns but are real (relationships would go here too).
EXTRA: dict[str, set[str]] = {"Job": {"is_terminal"}}


def _attrs(model_name: str) -> set[str]:
    mapper = sa_inspect(MODELS[model_name])
    return {a.key for a in mapper.attrs} | EXTRA.get(model_name, set())


def _bindings(fn: ast.AST) -> dict[str, str]:
    bound: dict[str, str] = {}
    if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
        for arg in fn.args.args + fn.args.kwonlyargs:
            ann = arg.annotation
            if isinstance(ann, ast.Name) and ann.id in MODELS:
                bound[arg.arg] = ann.id
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "get"
                and call.args
                and isinstance(call.args[0], ast.Name)
                and call.args[0].id in MODELS
            ):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bound[target.id] = call.args[0].id
    return bound


def offenders() -> tuple[list[str], int]:
    found: list[str] = []
    checked = 0
    for path in FILES:
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            bound = _bindings(fn)
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id in bound
                ):
                    checked += 1
                    model = bound[node.value.id]
                    if node.attr not in _attrs(model):
                        found.append(f"{path.name}:{node.lineno} {model}.{node.attr}")
    return found, checked


def test_every_orm_attribute_read_is_a_real_column() -> None:
    found, checked = offenders()
    assert found == [], found
    assert checked > 50, f"the walk saw only {checked} reads; it is not looking"


def test_no_getattr_default_anywhere_in_the_feature() -> None:
    hits = []
    for path in FILES:
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) == 3
            ):
                hits.append(f"{path.name}:{node.lineno}")
    allowed = {"feature_seams.py"}  # reads 004/006 result OBJECTS, not ORM rows (see its _dump)
    assert [h for h in hits if h.split(":")[0] not in allowed] == []
