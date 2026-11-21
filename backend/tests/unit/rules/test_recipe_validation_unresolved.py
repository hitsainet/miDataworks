"""A ``rule_set@1`` step never validates silently unresolved (FTID 016 section 3.6, I1).

``validate_body`` without the preloaded records reports ``rule_set_not_resolved`` for the step;
with them it resolves and the step's declared outputs are known downstream."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.rules import rule_set
from src.services import operator_port, recipe_service

DOC = json.loads(
    (
        Path(__file__).resolve().parents[2] / "fixtures/behaviours/fixture-beta/rule_set.json"
    ).read_text()
)
TEXT = rule_set.canonical_bytes(DOC).decode()
SHA = hashlib.sha256(TEXT.encode()).hexdigest()


@pytest.fixture(autouse=True)
def production_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    yield


def _body() -> dict[str, object]:
    params = {
        "rule_set_name": DOC["name"],
        "rule_set_version": 1,
        "rule_set_sha256": SHA,
        "use": "label",
        "text_column": "text",
    }
    column = "rs_" + DOC["outputs"][0]["column"]
    return {
        "format": "dw.recipe/v1",
        "steps": [
            {"operator": "rule_set", "version": "1", "params": params},
            {"operator": "drop_columns", "version": "1", "params": {"columns": [column]}},
        ],
    }


def test_without_the_records_the_step_is_not_resolved() -> None:
    result, _ = recipe_service.validate_body(_body(), {"text"})
    assert result.valid is False
    assert [e.code for e in result.steps[0].errors] == ["rule_set_not_resolved"]


def test_with_the_records_the_step_resolves_and_its_columns_are_known() -> None:
    result, _ = recipe_service.validate_body(
        _body(), {"text"}, rule_sets={(DOC["name"], 1): (SHA, TEXT)}
    )
    assert result.valid is True, result
