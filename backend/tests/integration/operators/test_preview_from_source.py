"""Preview an operator against a SOURCE (PII run L1, 2026-10-20).

The run had to build a no-op base version before any preview, because ``dataworks_preview_operator``
took a version only. A source input now gets a seeded sample with the build's system columns.
The row keys must be the ones a build gives the same rows, or a preview would describe rows that
do not exist; that is asserted against a real build of the same source.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from src.api.v1.endpoints import operators as endpoint
from src.core.database import get_sync_engine
from src.core.storage import resolve_under_data_dir
from src.operators import preview
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, make_source

__all__ = ["real_driver"]
OPS = "/api/v1/operators"


@pytest.fixture
def inline_previews(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    def run(req: dict[str, Any], entry: Any) -> None:
        sent.append(req)
        preview.run_request(req)

    monkeypatch.setattr(endpoint, "send_preview", run)
    return sent


async def test_a_source_previews_with_the_keys_a_build_gives_it(
    client: httpx.AsyncClient,
    real_driver: RealDriver,
    inline_previews: list[dict[str, Any]],
    data_dir: Path,
    operator_name: str,
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    response = await client.post(
        f"{OPS}/fx_drop_short/1/preview",
        json={"params": {"min_len": 25}, "input": {"source_id": source}, "seed": 4},
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["counts"]["in"] == result["sample_size"] > 0
    assert result["counts"]["dropped"] == result["drops_total"] > 0
    assert "seeded sample of the source" in result["input_note"]
    (sent,) = inline_previews
    assert sent["input"]["source_id"] == source
    (sample_path,) = sent["resolved_input"]["files"]
    sample = pq.read_table(resolve_under_data_dir(sample_path)).to_pylist()
    assert sent["resolved_input"]["column_roles"]["text"] == "content"
    assert {r["_dw_source_id"] for r in sample} == {source}
    assert all(r["_dw_source_locator"].startswith("train:") for r in sample)

    ds, rev = await setup(client, body(("fx_keep_all", {})))
    job = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert real_driver.run(job) == "completed"
    with get_sync_engine().connect() as conn:
        vid = conn.execute(text("SELECT id FROM dw_versions")).scalar_one()
    built = (await client.get(f"/api/v1/versions/{vid}/rows", params={"limit": 200})).json()
    by_locator = {r["_dw_source_locator"]: r["_dw_row_key"] for r in built["items"]}
    for row in sample:
        assert by_locator[row["_dw_source_locator"]] == row["_dw_row_key"]


async def test_a_source_without_a_text_column_says_how_to_name_one(
    client: httpx.AsyncClient, inline_previews: list[dict[str, Any]], data_dir: Path
) -> None:
    source = make_source(data_dir, {"train": HUMOR_TRAIN}, text_columns=())
    payload = {"params": {"min_len": 25}, "input": {"source_id": source}, "seed": 4}
    response = await client.post(f"{OPS}/fx_drop_short/1/preview", json=payload)
    assert response.status_code == 422, response.text
    assert "input.column_roles" in response.json()["error"]["message"]
    payload["input"]["column_roles"] = {"text": "content"}
    ok = await client.post(f"{OPS}/fx_drop_short/1/preview", json=payload)
    assert ok.status_code == 200, ok.text


async def test_column_roles_need_a_source(client: httpx.AsyncClient) -> None:
    response = await client.post(
        f"{OPS}/fx_drop_short/1/preview",
        json={"params": {}, "input": {"version_id": "v", "column_roles": {"t": "content"}}},
    )
    assert response.status_code == 422
