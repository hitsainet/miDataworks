"""``POST /versions/plan``: a build's dry run (PII run G6 and L3, 2026-10-20).

The run read ``agent_label_ledger.py`` to learn whether its Data Designer steps counted against the
agent labelling window, and lost a build to a cell_balancer whose cap is the GLOBAL smallest cell.
The plan answers both with the ledger's and the balancer's own code, and writes nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import async_session_factory, get_sync_engine
from src.operators.registry import OperatorRegistry
from src.services import agent_label_ledger as ledger
from tests.integration.test_version_build import setup, src
from tests.support.version_fixtures import make_source

PLAN = "/api/v1/versions/plan"
ROWS: list[dict[str, Any]] = [
    {"text": f"row {i}", "fmt": fmt, "label": label}
    for i, (fmt, label) in enumerate(
        [
            ("A", "0"),
            ("A", "0"),
            ("A", "0"),
            ("A", "1"),
            ("B", "0"),
            ("B", "0"),
            ("B", "1"),
            ("B", "1"),
        ]
    )
]


@pytest.fixture
def reg(monkeypatch: pytest.MonkeyPatch) -> OperatorRegistry:
    from src.operators import registry as registry_module
    from src.services import operator_port

    built = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(registry_module, "current", lambda: built)
    monkeypatch.setattr(operator_port, "_registry", built)
    return built


def _step(reg: OperatorRegistry, name: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"operator": name, "version": reg.current_version(name), "params": params}


def _body(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"format": "dw.recipe/v1", "steps": list(steps)}


def _counts() -> dict[str, int]:
    with get_sync_engine().connect() as conn:
        return {
            t: int(conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one())  # noqa: S608
            for t in ("dw_jobs", "dw_agent_label_rows", "dw_versions", "dw_version_builds")
        }


async def test_a_balancer_reports_the_global_cap_and_nothing_is_written(
    client: httpx.AsyncClient, reg: OperatorRegistry, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": ROWS})
    ds, rev = await setup(
        client, _body(_step(reg, "cell_balancer", {"column": "fmt", "label_column": "label"}))
    )
    before = _counts()
    response = await client.post(
        PLAN,
        json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]},
        headers={"X-Dataworks-Agent": "agent:test"},
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["valid"] is True and plan["rows_in"] == 8
    (balancer,) = plan["balancers"]
    assert balancer["cap"] == 1 and balancer["exact"] is True
    assert balancer["smallest_cell"] == {"value": "A", "label": "1"}
    assert balancer["rows_kept"] == 4 and balancer["rows_dropped"] == 4
    assert "single smallest cell of the whole table" in balancer["rule"]
    assert plan["labelling"]["counted_steps"] == []
    assert plan["labelling"]["agent_needs_approval"] is False
    assert _counts() == before, "a plan writes nothing"


async def test_an_empty_cell_is_the_refusal_the_build_would_give(
    client: httpx.AsyncClient, reg: OperatorRegistry, data_dir: Path, operator_name: str
) -> None:
    rows = [*ROWS, {"text": "c", "fmt": "C", "label": "0"}]
    source = make_source(data_dir, {"train": rows})
    ds, rev = await setup(
        client, _body(_step(reg, "cell_balancer", {"column": "fmt", "label_column": "label"}))
    )
    plan = (
        await client.post(
            PLAN, json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]}
        )
    ).json()
    assert plan["balancers"][0]["refused"]["code"] == "empty_cell"


async def test_a_labelling_step_counts_against_the_window(
    client: httpx.AsyncClient, reg: OperatorRegistry, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": ROWS})
    ds, rev = await setup(
        client,
        _body(_step(reg, "fx_keep_all", {}), _step(reg, "fx_endpoint_probe", {})),
    )
    request = {"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]}
    plan = (await client.post(PLAN, json=request)).json()
    labelling = plan["labelling"]
    assert [s["step"] for s in labelling["counted_steps"]] == [2]
    assert "judge endpoint role" in labelling["counted_steps"][0]["why"]
    assert labelling["rows_counted"] == 8 and labelling["window_total_now"] == 0
    assert labelling["agent_needs_approval"] is False
    async with async_session_factory()() as db:  # the window is nearly spent
        await ledger.admit(db, source, "agent:x", "label_run", f"r{uuid.uuid4().hex[:6]}", 4995)
        await db.commit()
    again = (await client.post(PLAN, json=request)).json()["labelling"]
    assert again["window_total_now"] == 4995 and again["agent_needs_approval"] is True


async def test_a_column_written_by_an_earlier_step_is_not_guessed(
    client: httpx.AsyncClient, reg: OperatorRegistry, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": ROWS})
    ds, rev = await setup(
        client,
        _body(_step(reg, "cell_balancer", {"column": "judge_model", "label_column": "label"})),
    )
    plan = (
        await client.post(
            PLAN, json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]}
        )
    ).json()
    balancer = plan["balancers"][0]
    assert balancer["computable"] is False and "judge_model" in balancer["reason"]
