"""The humor-shaped drop log on a native-operator fixture (tasks 10.5, 20.7; FPRD 002 §11 item 7).

The prototype's documented figures — 13,195 rows excluded by the uncertainty band, 891 removed by
class balance (seed 20261005) and 1,090 held out (``records/labelling_run.md``,
``records/publish_prep.json``) — reproduced with stub band, balance and split operators on a
synthetic source shaped to those counts. Re-run with features 004 and 005's real operators when
they land (FTASKS 20.7).
"""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.integration.test_version_build import build, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver"]

IN_BAND, POSITIVE, NEGATIVE = 13_195, 5_000, 5_891


def fixture_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for i in range(IN_BAND):
        rows.append(
            {
                "text": f"uncertain headline number {i}",
                "label": i % 2,
                "score": 0.3 + 0.4 * (i % 100) / 100,
                "note": None,
            }
        )
    for i in range(POSITIVE):
        rows.append(
            {"text": f"clearly funny headline number {i}", "label": 1, "score": 0.9, "note": None}
        )
    for i in range(NEGATIVE):
        rows.append(
            {"text": f"clearly plain headline number {i}", "label": 0, "score": 0.1, "note": None}
        )
    return rows


async def test_the_drop_log_reproduces_the_prototype_counts(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    source = make_source(data_dir, {"train": fixture_rows()})
    recipe = body(
        ("stub_band", {"min": 0.3, "max": 0.7}),
        ("stub_balance", {"column": "label"}),
        ("stub_split", {"count": 1_090, "held_out": "test"}),
    )
    ds, rev = await setup(client, recipe)
    version = await build(client, driver, ds, rev, [src(source)], seed=20261005)
    log = (await client.get(f"/api/v1/versions/{version['id']}/drop-log")).json()["steps"]
    assert [s["operator"] for s in log] == ["stub_band", "stub_balance", "stub_split"]
    assert log[0]["dropped"] == 13_195 and log[0]["reasons"][0]["reason_code"] == "inside_band"
    assert log[1]["dropped"] == 891 and log[1]["reasons"][0]["reason_code"] == "balance"
    assert log[2]["split_assigned"] == log[2]["rows_in"] == 10_000
    splits = {s["name"]: s for s in version["splits"]}
    assert splits["test"]["rows"] == 1_090 and splits["test"]["held_out"] is True
    assert splits["train"]["rows"] == 10_000 - 1_090
    assert [s["rows_out"] for s in log] == [10_891, 10_000, 10_000]
