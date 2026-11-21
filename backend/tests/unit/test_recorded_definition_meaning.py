"""A recorded definition carries the catalogue's own ``positive_meaning``, so it takes any meaning
miWorkbench's catalogue accepts (live finding 2026-11-19: carrier_plan refused the record
``dataworks_resolve_behaviour`` returned for a 1,250-character v2 meaning; the cap was 1,000)."""

from __future__ import annotations

import ast
import json

import pytest

from src.schemas.behaviour_datasets import CATALOGUE_MAX_MEANING
from tests.support.behaviour_fixtures import definition, plan_params
from tests.support.curation_fixtures import registry


def _problems(meaning: str) -> list[str]:
    reg = registry()
    version = reg.current_version("carrier_plan")
    assert version is not None
    record = {**definition("fixture-alpha"), "positive_meaning": meaning}
    params = plan_params("fixture-alpha", 10, definition=json.dumps(record))
    return list(reg.validate_params("carrier_plan", version, params))


@pytest.mark.parametrize("length", [1250, CATALOGUE_MAX_MEANING])
def test_carrier_plan_takes_a_meaning_as_long_as_the_catalogue_allows(length: int) -> None:
    assert _problems("m" * length) == []


def test_a_meaning_longer_than_the_catalogue_allows_is_refused_by_name() -> None:
    (problem,) = _problems("m" * (CATALOGUE_MAX_MEANING + 1))
    assert problem.startswith("/definition") and "vocabulary_invalid" in problem
    assert "positive_meaning" in problem


def test_the_limit_is_miworkbenchs_catalogue_limit_when_the_sibling_is_present() -> None:
    from tests.support.siblings import absent, sibling_repo

    schema = sibling_repo("miWorkbench") / "backend" / "src" / "schemas" / "behaviour.py"
    if not schema.exists():
        absent(f"MIWORKBENCH_REPO: no {schema}; set MIWORKBENCH_REPO to compare")
    found = [
        n.value.value
        for n in ast.walk(ast.parse(schema.read_text()))
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "MAX_MEANING" for t in n.targets)
        and isinstance(n.value, ast.Constant)
    ]
    assert found == [CATALOGUE_MAX_MEANING], found
