"""009's registration body and role table against miWorkbench's own schema (FTASKS 5.6; FR-009.4,
FR-009.23).

Reads ``backend/src/schemas/probe_monitor.py`` from ``MISTUDIO_REPO`` by AST (ADR-001: never an
import) and asserts that every key 009 can send is a field of ``ProbeDatasetCreate`` (which is
``extra="forbid"``: an unknown key turns every registration into a 422), and that the role table's
right-hand values are miWorkbench's ``DatasetRole`` and ``Distribution`` literals. An absent checkout
skips loudly; ``MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS=1`` makes it a failure.

``dataset_version_manifest`` is an OPTIONAL key (XR-1): it is checked only when miWorkbench declares it.
"""

from __future__ import annotations

import ast
import os

import pytest

from src.services.detector_sets.plan import OPTIONAL_KEYS, REGISTRATION_KEYS
from src.services.detector_sets.role_mapping import MISTUDIO_ROLE
from tests.support.siblings import sibling_repo

MISTUDIO = sibling_repo("miWorkbench")
SCHEMA = MISTUDIO / "backend" / "src" / "schemas" / "probe_monitor.py"


def _schema() -> ast.Module:
    if not SCHEMA.exists():
        message = f"no miWorkbench schema at {SCHEMA}"
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
    raise AssertionError(f"miWorkbench has no class {cls}")


def _literal(tree: ast.Module, name: str) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            sub = node.value
            assert isinstance(sub, ast.Subscript)
            elts = sub.slice.elts if isinstance(sub.slice, ast.Tuple) else [sub.slice]
            return {e.value for e in elts if isinstance(e, ast.Constant)}
    raise AssertionError(f"miWorkbench has no {name}")


def test_registration_keys_are_fields_of_probe_dataset_create() -> None:
    fields = _fields(_schema(), "ProbeDatasetCreate")
    assert set(REGISTRATION_KEYS) <= fields, set(REGISTRATION_KEYS) - fields
    assert {"distribution", "pair_column"} <= fields
    assert "keyword_filter" in fields  # it exists in miWorkbench; 009 never sends it (FR-009.23)
    if "dataset_version_manifest" not in fields:
        assert "dataset_version_manifest" in OPTIONAL_KEYS  # XR-1 not served: only optional


def test_group_columns_is_a_field_and_the_limits_are_mistudios_defaults() -> None:
    """ENH-006 D116 G11: the name the send sends, and the two limits it leaves columns out by, read
    from miWorkbench's own code. A moved default would refuse a role, or quietly drop a usable column.
    """
    from src.services.detector_sets import group_columns

    assert "group_columns" in _fields(_schema(), "ProbeDatasetCreate")
    assert "group_columns" in OPTIONAL_KEYS
    config = ast.parse((MISTUDIO / "backend" / "src" / "core" / "config.py").read_text("utf-8"))
    defaults: dict[str, object] = {}
    for node in ast.walk(config):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Call)
        ):
            for keyword in node.value.keywords:
                if keyword.arg == "default" and isinstance(keyword.value, ast.Constant):
                    defaults[node.target.id] = keyword.value.value
    assert defaults["group_column_max_values"] == group_columns.GROUP_COLUMN_MAX_VALUES
    assert defaults["view_max_group_columns"] == group_columns.VIEW_MAX_GROUP_COLUMNS


def test_span_column_is_the_name_miWorkbench_declares_when_it_declares_one() -> None:
    """ENH-006 G39: the send carries ``span_column`` only when miWorkbench serves it (read from its OpenAPI),
    and miDataworks pushes FIRST, so a miWorkbench without the field is the expected state until it ships:
    then the key must stay optional. Once miWorkbench declares it, the name sent must be that field.
    """
    from src.services.detector_sets.capabilities import WIRE_SPAN_COLUMN

    assert WIRE_SPAN_COLUMN in OPTIONAL_KEYS
    fields = _fields(_schema(), "ProbeDatasetCreate")
    if "span_column" in fields:
        assert WIRE_SPAN_COLUMN == "span_column"


def test_role_table_uses_mistudio_literals() -> None:
    tree = _schema()
    roles = _literal(tree, "DatasetRole")
    distributions = _literal(tree, "Distribution")
    for mine, (theirs, distribution) in MISTUDIO_ROLE.items():
        assert theirs in roles, mine
        assert distribution is None or distribution in distributions, mine
