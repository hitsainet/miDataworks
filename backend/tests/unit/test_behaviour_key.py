"""One behaviour-key slug everywhere (015 FR-015.1, FR-015.7; ENH-005 D25, D37).

The pydantic pattern, both models' ``CHECK`` text, the migration literal, the MCP server's copy and
(when the sibling is present) miStudio's ``SLUG_PATTERN`` are the same string.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from src.mcp_server.context import BEHAVIOUR_KEY_PATTERN as MCP_PATTERN
from src.models.detector_set import DetectorSet
from src.models.generation import GenerationRun, GenerationTemplate
from src.models.label_run import LabelRun
from src.models.recipe import Recipe
from src.models.review import ReviewQueue
from src.models.rubric import Rubric
from src.schemas.behaviour import BEHAVIOUR_KEY_PATTERN, behaviour_key_check, is_behaviour_key

BACKEND = Path(__file__).resolve().parents[2]
MIGRATION = BACKEND / "alembic" / "versions" / "0035_behaviour_dataset_support.py"
#: 017 FR-017.9: the five tables keyed by migration 0039.
MIGRATION_017 = BACKEND / "alembic" / "versions" / "0039_017_behaviour_keys_and_carriers.py"
KEYED_017 = (Recipe, GenerationTemplate, GenerationRun, LabelRun, ReviewQueue)


def _check_text(model: type) -> str:
    [check] = [
        c
        for c in model.__table__.constraints
        if c.name and str(c.name).endswith("behaviour_key_slug")
    ]
    return str(check.sqltext)


def _migration_slug(path: Path) -> str:
    tree = ast.parse(path.read_text())
    [slug] = [
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SLUG" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    return str(slug)


def test_every_copy_is_the_same_string() -> None:
    assert _migration_slug(MIGRATION) == BEHAVIOUR_KEY_PATTERN
    assert _migration_slug(MIGRATION_017) == BEHAVIOUR_KEY_PATTERN
    assert MCP_PATTERN == BEHAVIOUR_KEY_PATTERN
    assert _check_text(DetectorSet) == behaviour_key_check()
    assert _check_text(Rubric) == behaviour_key_check()
    for model in KEYED_017:
        assert _check_text(model) == behaviour_key_check(), model.__tablename__
    assert BEHAVIOUR_KEY_PATTERN in behaviour_key_check()


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("pii", True),
        ("high-stakes", True),
        ("ab", True),
        ("a" * 64, True),
        ("a" * 65, False),
        ("a", False),
        ("Pii", False),
        ("-x", False),
        ("x-", False),
        ("pii_us", False),
        ("", False),
    ],
)
def test_boundaries(value: str, ok: bool) -> None:
    assert is_behaviour_key(value) is ok
    assert bool(re.fullmatch(MCP_PATTERN, value)) is ok


def test_mistudios_slug_rule_is_the_same_when_the_sibling_is_present() -> None:
    from tests.support.siblings import sibling_repo

    root = sibling_repo("miStudio", "MISTUDIO_REPO")
    forms = root / "backend" / "src" / "services" / "judge_prompt_forms.py"
    if not forms.exists():
        if os.environ.get("MIDATAWORKS_REQUIRE_CROSS_REPO_CHECKS") == "1":
            pytest.fail(f"CROSS-REPO: {forms} is absent")
        pytest.skip(f"CROSS-REPO: MISTUDIO_REPO: no {forms}; set MISTUDIO_REPO to compare")
    tree = ast.parse(forms.read_text())
    found = [
        n.value.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "SLUG_PATTERN" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    assert found == [BEHAVIOUR_KEY_PATTERN], found
