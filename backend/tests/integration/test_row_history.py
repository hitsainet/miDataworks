"""Row history, drop log, events, rows and lineage (tasks 10.2–10.8; FR-002.26, 002.27, 002.33)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

from src.services.row_keys import compute_row_key
from tests.integration.test_version_build import build, setup, src
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, BuildDriver, driver, make_source

__all__ = ["driver"]
V = "/api/v1/versions"


def key(text: str) -> str:
    return compute_row_key({"text": text}, ["text"])


async def history(client: httpx.AsyncClient, version_id: str, **params: str) -> dict[str, Any]:
    response = await client.get(f"{V}/{version_id}/rows/history", params=params)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    return data


@pytest.fixture
async def two_versions(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> dict[str, Any]:
    """v1: drop short rows, upper-case; v2 (child of v1): drop rows under 30 characters."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 8}), ("stub_upper", {})))
    v1 = await build(client, driver, ds, rev, [src(source)], seed=1)
    rec = await client.post(
        "/api/v1/recipes",
        json={"name": "longer", "body": body(("stub_drop_short", {"min_len": 30}))},
    )
    v2 = await build(
        client,
        driver,
        ds,
        rec.json()["head_revision_id"],
        [{"kind": "version", "version_id": v1["id"]}],
        seed=1,
    )
    return {"source": source, "v1": v1, "v2": v2}


class TestRowHistory:
    async def test_a_present_row_follows_its_change_back_to_the_source_locator(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        upper = "TIME FLIES LIKE AN ARROW; FRUIT FLIES LIKE A BANANA."
        result = (await history(client, two_versions["v2"]["id"], row_key=key(upper)))["results"][0]
        assert result["status"] == "present"
        assert result["present_in"][0]["split"] == "train"
        changed = [t for t in result["trail"] if t["kind"] == "changed"]
        original = "Time flies like an arrow; fruit flies like a banana."
        assert changed and changed[0]["from_key"] == key(original)
        assert changed[0]["version_number"] == 1 and changed[0]["operator"] == "stub_upper"
        assert result["origin"]["source_id"] == two_versions["source"]
        assert result["origin"]["source_locator"] == "train:9"

    async def test_a_row_dropped_in_this_version_names_the_step_and_statistic(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        upper = "QUARTERLY EARNINGS BEAT EXPECTATIONS"  # 36 chars: kept
        short_upper = "A PUN IS ITS OWN REWORD."  # 24 chars: dropped by v2's min_len 30
        kept = (await history(client, two_versions["v2"]["id"], row_key=key(upper)))["results"][0]
        assert kept["status"] == "present"
        result = (await history(client, two_versions["v2"]["id"], row_key=key(short_upper)))[
            "results"
        ][0]
        assert result["status"] == "dropped"
        dropped = result["dropped_at"]
        assert dropped["version_id"] == two_versions["v2"]["id"] and dropped["step_index"] == 1
        assert dropped["operator"] == "stub_drop_short" and dropped["operator_version"] == "1"
        assert dropped["reason_code"] == "too_short" and dropped["statistic_value"] == 24.0
        assert dropped["threshold"] == {"value": 30, "comparator": "<"}

    async def test_a_row_dropped_in_an_ancestor_names_the_ancestor(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        result = (await history(client, two_versions["v2"]["id"], row_key=key("short")))["results"][
            0
        ]
        assert result["status"] == "dropped"
        assert result["dropped_at"]["version_id"] == two_versions["v1"]["id"]
        assert result["origin"] == {
            "source_id": two_versions["source"],
            "source_locator": "train:6",
        }

    async def test_an_unknown_key_is_not_found_naming_what_was_searched(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        result = (
            await history(client, two_versions["v2"]["id"], row_key=key("never seen anywhere"))
        )["results"][0]
        assert result["status"] == "not_found"
        assert set(result["searched"]) == {two_versions["v1"]["id"], two_versions["v2"]["id"]}

    async def test_a_short_prefix_is_refused_and_a_unique_prefix_resolves(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        full = key("QUARTERLY EARNINGS BEAT EXPECTATIONS")
        short = await client.get(
            f"{V}/{two_versions['v2']['id']}/rows/history", params={"row_key": full[:8]}
        )
        assert (
            short.status_code == 422 and short.json()["error"]["code"] == "row_key_prefix_too_short"
        )
        result = (await history(client, two_versions["v2"]["id"], row_key=full[:16]))["results"][0]
        assert result["row_key"] == full

    async def test_an_ambiguous_prefix_is_refused(
        self,
        client: httpx.AsyncClient,
        two_versions: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from src.services import lineage_service

        monkeypatch.setattr(lineage_service, "MIN_PREFIX", 1)
        version_id = two_versions["v2"]["id"]
        items = (await client.get(f"{V}/{version_id}/rows?limit=200")).json()["items"]
        events = (await client.get(f"{V}/{two_versions['v1']['id']}/events?limit=200")).json()[
            "items"
        ]
        keys = sorted(
            {r["_dw_row_key"] for r in items}
            | {e["row_key"] for e in events}
            | {e["new_row_key"] for e in events if e["new_row_key"]}
        )  # distinct keys in the lineage: copies share a key
        prefix = next(k[:1] for k in keys if sum(x.startswith(k[:1]) for x in keys) > 1)
        response = await client.get(f"{V}/{version_id}/rows/history", params={"row_key": prefix})
        assert (
            response.status_code == 409 and response.json()["error"]["code"] == "row_key_ambiguous"
        )

    async def test_text_search_finds_a_row_dropped_at_step_one(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        found = await history(client, two_versions["v1"]["id"], q="short")
        statuses = {r["status"] for r in found["results"]}
        assert "dropped" in statuses
        dropped = next(r for r in found["results"] if r["status"] == "dropped")
        assert dropped["dropped_at"]["step_index"] == 1

    async def test_every_input_row_resolves(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        """FPRD 002 section 11 item 1, on the fixture: present, dropped or changed — 100%."""
        unresolved = []
        for row in HUMOR_TRAIN:
            result = (await history(client, two_versions["v2"]["id"], row_key=key(row["text"])))[
                "results"
            ][0]
            changed = any(t["kind"] == "changed" for t in result["trail"])
            if result["status"] == "not_found" and not changed:
                unresolved.append(row["text"])
        assert unresolved == []


class TestDropLogEventsRowsLineage:
    async def test_drop_log_and_events(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        v1 = two_versions["v1"]["id"]
        log = (await client.get(f"{V}/{v1}/drop-log")).json()
        assert [s["operator"] for s in log["steps"]] == ["stub_drop_short", "stub_upper"]
        assert log["steps"][0]["dropped"] == 1
        events = (
            await client.get(f"{V}/{v1}/events", params={"step_index": 1, "kind": "dropped"})
        ).json()
        assert events["total"] == 1 and events["items"][0]["reason_code"] == "too_short"
        assert events["items"][0]["step_index"] == 1
        changed = (
            await client.get(f"{V}/{v1}/events", params={"kind": "changed", "limit": 2})
        ).json()
        assert changed["total"] == 9 and len(changed["items"]) == 2

    async def test_rows_page_projects_filters_and_refuses_free_sql(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        v1 = two_versions["v1"]["id"]
        page = (await client.get(f"{V}/{v1}/rows", params={"limit": 3, "page": 2})).json()
        assert page["total"] == 9 and len(page["items"]) == 3 and "_dw_row_key" in page["columns"]
        filtered = (
            await client.get(
                f"{V}/{v1}/rows",
                params={"where": json.dumps([{"column": "label", "op": "eq", "value": 1}])},
            )
        ).json()
        assert filtered["total"] == 5
        searched = (await client.get(f"{V}/{v1}/rows", params={"q": "chicken"})).json()
        assert searched["total"] == 2
        for bad in (
            "label = 1; DROP TABLE x",
            json.dumps([{"column": "label; --", "op": "eq", "value": 1}]),
            json.dumps([{"column": "label", "op": "raw", "value": 1}]),
        ):
            response = await client.get(f"{V}/{v1}/rows", params={"where": bad})
            assert response.status_code == 422, bad

    async def test_lineage_lists_inputs_parent_children_and_reuse(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any]
    ) -> None:
        v1, v2 = two_versions["v1"], two_versions["v2"]
        mine = (await client.get(f"{V}/{v1['id']}/lineage")).json()
        assert mine["children"] == [{"version_id": v2["id"], "number": 2, "state": "completed"}]
        assert mine["inputs"][0]["display_name"] == "org/humor"
        assert [s["index"] for s in mine["steps"]] == [0, 1, 2]
        theirs = (await client.get(f"{V}/{v2['id']}/lineage")).json()
        assert theirs["parent_version_id"] == v1["id"] and theirs["inputs"][0]["number"] == 1

    async def test_manifest_bytes_equal_the_column_and_hash_to_the_etag(
        self, client: httpx.AsyncClient, two_versions: dict[str, Any], data_dir: Path
    ) -> None:
        import hashlib

        v1 = two_versions["v1"]
        response = await client.get(f"{V}/{v1['id']}/manifest")
        assert (
            hashlib.sha256(response.content).hexdigest()
            == response.headers["etag"]
            == v1["manifest_sha256"]
        )
        assert pq.ParquetFile(data_dir / v1["splits"][0]["path"]).metadata.num_rows == 9


async def test_a_version_whose_recipe_dropped_every_row_still_answers(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Found by the 18.7 benchmark: no split files made read_parquet([]) fail row history."""
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("stub_drop_short", {"min_len": 1000})))
    version = await build(client, driver, ds, rev, [src(source)], seed=1)
    assert version["total_rows"] == 0 and version["splits"] == []
    result = (await history(client, version["id"], row_key=key("short")))["results"][0]
    assert result["status"] == "dropped"
    rows = (await client.get(f"{V}/{version['id']}/rows")).json()
    assert rows["total"] == 0 and rows["items"] == []
    found = await history(client, version["id"], q="short")
    assert found["results"] and found["results"][0]["status"] == "dropped"
