"""009's registration body and role table against miStudio's own schema (FTASKS 5.6; FR-009.4,
FR-009.23).

Reads ``backend/src/schemas/probe_monitor.py`` from ``MISTUDIO_REPO`` by AST (ADR-001: never an
import) and asserts that every key 009 can send is a field of ``ProbeDatasetCreate`` (which is
``extra="forbid"``: an unknown key turns every registration into a 422), and that the role table's
right-hand values are miStudio's ``DatasetRole`` and ``Distribution`` literals. An absent checkout
skips loudly; ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` makes it a failure.

``dataset_version_manifest`` is an OPTIONAL key (XR-1): it is checked only when miStudio declares it.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from src.services.detector_sets.plan import OPTIONAL_KEYS, REGISTRATION_KEYS
from src.services.detector_sets.role_mapping import MISTUDIO_ROLE

MISTUDIO = Path(os.environ.get("MISTUDIO_REPO", "/home/x-sean/app/miStudio"))
SCHEMA = MISTUDIO / "backend" / "src" / "schemas" / "probe_monitor.py"


def _schema() -> ast.Module:
    if not SCHEMA.exists():
        message = f"no miStudio schema at {SCHEMA}"
        if os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1":
            pytest.fail("CROSS-REPO (required): " + message)
        pytest.skip("CROSS-REPO: " + message)
    return ast.parse(SCHEMA.read_text(encoding="utf-8"))


def _fields(tree: ast.Module, cls: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return {
                s.target.id
                for s in node.body
                if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            }
    raise AssertionError(f"miStudio has no class {cls}")


def _literal(tree: ast.Module, name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            sub = node.value
            assert isinstance(sub, ast.Subscript)
            elts = sub.slice.elts if isinstance(sub.slice, ast.Tuple) else [sub.slice]
            return {e.value for e in elts if isinstance(e, ast.Constant)}
    raise AssertionError(f"miStudio has no {name}")


def test_registration_keys_are_fields_of_probe_dataset_create() -> None:
    fields = _fields(_schema(), "ProbeDatasetCreate")
    assert set(REGISTRATION_KEYS) <= fields, set(REGISTRATION_KEYS) - fields
    assert {"distribution", "pair_column"} <= fields
    assert "keyword_filter" in fields  # it exists in miStudio; 009 never sends it (FR-009.23)
    if "dataset_version_manifest" not in fields:
        assert "dataset_version_manifest" in OPTIONAL_KEYS  # XR-1 not served: only optional


def test_role_table_uses_mistudio_literals() -> None:
    tree = _schema()
    roles = _literal(tree, "DatasetRole")
    distributions = _literal(tree, "Distribution")
    for mine, (theirs, distribution) in MISTUDIO_ROLE.items():
        assert theirs in roles, mine
        assert distribution is None or distribution in distributions, mine
