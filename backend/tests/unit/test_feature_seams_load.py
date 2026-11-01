"""Every module 008's feature seam reads exists and imports (2026-10-17).

``feature_seams`` reports ``not_checked`` only when ``load_owner`` returns None, and the publish
checks then read amber. With every owner built that can only be a fault, so the copy says so; this
pins that every owner loads. The list is derived from the module's own ``load_owner`` calls (AST),
so a new seam that is not in ``OWNER_MODULES`` fails here.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from src.services.publishing import feature_seams


def called_constants() -> set[str]:
    tree = ast.parse(inspect.getsource(feature_seams))
    names: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "load_owner"
            and node.args
            and isinstance(node.args[0], ast.Name)
        ):
            names.add(node.args[0].id)
    return names


def test_owner_modules_are_every_module_the_seam_loads() -> None:
    called = {getattr(feature_seams, n) for n in called_constants()}
    assert called, "found no load_owner(CONSTANT) call: the derivation read nothing"
    assert called == set(feature_seams.OWNER_MODULES)


@pytest.mark.parametrize("relative", feature_seams.OWNER_MODULES)
def test_each_owner_module_loads(relative: str) -> None:
    assert feature_seams.load_owner(relative) is not None, relative
