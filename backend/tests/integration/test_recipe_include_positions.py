"""An include's positions count from 1 (dry run, 2026-11-01): ``from: 0, to: 0`` read as "the whole
revision" and copied every step, so a recipe saved with far more steps than it asked for."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.operators import registry as registry_module
from src.operators.registry import OperatorRegistry
from src.services import operator_port

STEP = {"operator": "empty_content", "version": "1.0.0", "params": {}}
THREE = {"format": "dw.recipe/v1", "steps": [STEP, STEP, STEP]}


@pytest.fixture(autouse=True)
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)


async def saved(client: httpx.AsyncClient, name: str, body: Any) -> Any:
    return await client.post("/api/v1/recipes?view=summary", json={"name": name, "body": body})


async def base(client: httpx.AsyncClient, operator_name: str) -> str:
    made = await saved(client, "three", THREE)
    assert made.status_code == 201, made.text
    revision: str = made.json()["revision_id"]
    return revision


@pytest.mark.parametrize(
    "positions",
    [
        {"from": 0, "to": 0},
        {"from": 0},
        {"to": 0},
        {"from": 0, "to": 2},
        {"from": 2, "to": 4},
        {"from": 3, "to": 2},
        {"from": "2"},
        {"to": True},
        {"from": 1.5},
    ],
)
async def test_a_position_outside_the_revision_is_refused(
    client: httpx.AsyncClient, operator_name: str, positions: dict[str, Any]
) -> None:
    revision = await base(client, operator_name)
    answer = await saved(
        client,
        "bad",
        {"format": "dw.recipe/v1", "steps": [{"include": revision, **positions}]},
    )
    assert answer.status_code == 422, answer.text
    assert answer.json()["error"]["code"] == "recipe_include_invalid"


@pytest.mark.parametrize(
    ("positions", "count"),
    [
        ({}, 3),
        ({"from": None, "to": None}, 3),
        ({"from": 1, "to": 1}, 1),
        ({"from": 2}, 2),
        ({"to": 2}, 2),
        ({"from": 3, "to": 3}, 1),
    ],
)
async def test_positions_count_from_one_and_both_ends_are_included(
    client: httpx.AsyncClient, operator_name: str, positions: dict[str, Any], count: int
) -> None:
    revision = await base(client, operator_name)
    answer = await saved(
        client,
        "good",
        {"format": "dw.recipe/v1", "steps": [{"include": revision, **positions}]},
    )
    assert answer.status_code == 201, answer.text
    assert answer.json()["operators"] == {"empty_content@1.0.0": count}
