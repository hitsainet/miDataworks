"""Large results: a page worst first, a token that reaches the rest, a download equal to the whole
table (017 FTASKS 11.7; FR-017.58).

Through MCP over the real backend (``dataworks_get_result_page``, ``dataworks_get_result_download_url``)
for a carrier plan's census (both fixture behaviours), a paid second opinion's agreement by stratum
and a value-directed split's proof values. The download is fetched from the signed path the tool
returns (the client fetches it directly; the bytes never pass through MCP).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.behaviour.test_carrier_plan_build import plan_version
from tests.integration.labeling.test_agreement_by_stratum import started
from tests.support.behaviour_fixtures import BEHAVIOURS
from tests.support.mcp_live import call, live_mcp
from tests.support.operator_build_driver import RealDriver
from tests.support.second_opinion import Paid, paid

__all__ = ["paid"]


async def every_page(kind: str, object_id: str, limit: int, **options: Any) -> list[dict]:  # type: ignore[type-arg]
    mcp, mcp_client = live_mcp()
    rows: list[dict] = []  # type: ignore[type-arg]
    token: str | None = None
    pages = 0
    try:
        while True:
            page = await call(
                mcp,
                "dataworks_get_result_page",
                {"kind": kind, "id": object_id, "limit": limit, "page_token": token, **options},
            )
            pages += 1
            assert len(page["rows"]) <= limit
            rows.extend(page["rows"])
            token = page["next_page_token"]
            if token is None:
                assert page["next"]["tool"] == "dataworks_get_result_download_url"
                assert len(rows) == page["rows_total"]
                break
            assert page["next"]["tool"] == "dataworks_get_result_page"
            assert len(page["rows"]) == limit
    finally:
        await mcp_client.close()
    assert pages > 1, "the table must be larger than one page for this test to mean anything"
    return rows


async def downloaded(client: httpx.AsyncClient, kind: str, object_id: str, **options: Any) -> list:  # type: ignore[type-arg]
    mcp, mcp_client = live_mcp()
    try:
        link = await call(
            mcp, "dataworks_get_result_download_url", {"kind": kind, "id": object_id, **options}
        )
    finally:
        await mcp_client.close()
    assert link["format"] == "jsonl" and link["kind"] == kind
    got = await client.get(link["path"])
    assert got.status_code == 200, got.text
    rows = [json.loads(line) for line in got.text.splitlines() if line]
    assert len(rows) == link["rows"]
    return rows


@pytest.mark.parametrize("key", BEHAVIOURS)
async def test_a_census_pages_worst_first_and_downloads_whole(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str, key: str
) -> None:
    plan = await plan_version(client, driver, data_dir, key, 240)
    stored = plan["behaviour_census"]
    limit = 3
    assert stored["cells_total"] > limit
    rows = await every_page("census", plan["id"], limit)
    assert len(rows) == stored["cells_total"]
    # worst first: the order the census itself uses (largest shortfall, then largest plan)
    order = [(-r["shortfall"], -r["planned"]) for r in rows]
    assert order == sorted(order)
    assert rows[: len(stored["cells"])] == stored["cells"], "the stored head is the first rows"
    assert await downloaded(client, "census", plan["id"]) == rows


async def test_agreement_by_stratum_pages_and_downloads_whole(
    client: httpx.AsyncClient, paid: Paid
) -> None:
    run_id = await started(client, paid)
    options = {"column": "truth", "by": ["genre", "kind"]}
    rows = await every_page("agreement", run_id, 2, **options)
    assert len(rows) == 8  # four genres x two kinds
    assert await downloaded(client, "agreement", run_id, **options) == rows


async def test_a_table_that_is_not_there_is_refused_with_its_next_step(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    plan = await plan_version(client, driver, data_dir, BEHAVIOURS[0], 40)
    got = await client.get(f"/api/v1/results/proof/{plan['id']}")
    assert got.status_code == 409, got.text
    error = got.json()["error"]
    assert error["code"] == "proof_not_recorded"
    assert error["details"]["next"]["tool"] == "dataworks_get_version"


async def test_proof_values_page_and_download_whole(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    from tests.integration.test_directed_held_out import directed_version

    version = await directed_version(client, driver, data_dir, "proof-pages")
    expected = [
        {"split": s["name"], "column": c, "value": v, "kind": "held_out"}
        for s in version["splits"]
        if s.get("proof")
        for c, values in sorted(s["proof"]["held_out_values"].items())
        for v in values
    ]
    assert len(expected) == 4, expected  # genre, generator, and both on the "both" split
    rows = await every_page("proof", version["id"], 1)
    assert rows == expected
    assert await downloaded(client, "proof", version["id"]) == rows
