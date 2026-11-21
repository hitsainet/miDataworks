"""WSP marks on the read paths (021 FTASKS 10.x, 16.2; FR-021.13 - FR-021.15, FR-021.25, FR-021.26,
FR-021.32): the version read, the list filter, the partitions route, the detector-set binding and
the download purpose, each through the live route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from src.main import fastapi_app
from tests.integration.test_directed_held_out import driver
from tests.integration.test_wsp_versions import alpha
from tests.support.operator_build_driver import RealDriver

__all__ = ["driver"]

V = "/api/v1/versions"
AGENT = {"X-Dataworks-Agent": "agent:mcp"}


async def test_version_reads_carry_marks_and_sensitivity(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.25: ``GET /versions/{id}`` carries each split's ``wsp`` and the live sensitivity."""
    version_id = await alpha(client, driver, data_dir, "reads")
    got = (await client.get(f"{V}/{version_id}")).json()
    by = {s["name"]: s for s in got["splits"]}
    assert by["long"]["wsp"]["role"] == "wsp_heldout"
    assert by["long"]["wsp"]["version_length"]["unit"] == "characters"
    assert by["test"]["wsp"] is None
    assert got["sensitivity"] is None  # not declared
    assert got["sensitivity_basis"]["raised_by"] == []
    assert got["sensitivity_published_below"] == []


async def test_partitions_role_filter(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.15, FR-021.26 (M-29): ``role=wsp_practice`` never lists a held-out partition."""
    version_id = await alpha(client, driver, data_dir, "parts")
    every = (await client.get(f"{V}/{version_id}/wsp-partitions")).json()
    assert every["version_id"] == version_id and every["sensitivity"] is None
    assert sorted((p["split"], p["role"]) for p in every["partitions"]) == [
        ("everyday", "wsp_heldout"),
        ("long", "wsp_heldout"),
        ("train", "wsp_practice"),
    ]
    long = next(p for p in every["partitions"] if p["split"] == "long")
    assert long["rows"] == 3 and long["min_chars"] == 20 and long["text_column"] == "text"
    assert set(long) == {
        "split", "role", "set_kind", "rows", "text_column", "min_chars", "version_length",
        "file_sha256", "logical_digest",
    }  # fmt: skip
    practice = (await client.get(f"{V}/{version_id}/wsp-partitions?role=wsp_practice")).json()
    assert [p["split"] for p in practice["partitions"]] == ["train"]
    held = (await client.get(f"{V}/{version_id}/wsp-partitions?role=wsp_heldout")).json()
    assert {p["role"] for p in held["partitions"]} == {"wsp_heldout"}


async def test_a_version_with_no_marks_answers_an_empty_list(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    from tests.integration.test_wsp_versions import alpha_rows, build, split_step

    plain = split_step(["train", "test", "everyday", "long"], ["test"], None, None)
    out = await build(client, driver, data_dir, alpha_rows("nomark"), [plain], "nomark")
    got = await client.get(f"{V}/{out['version_id']}/wsp-partitions")
    assert got.status_code == 200 and got.json()["partitions"] == []


async def test_the_list_filter(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    from tests.integration.test_wsp_versions import alpha_rows, build, split_step

    marked = await alpha(client, driver, data_dir, "listed")
    plain = split_step(["train", "test", "everyday", "long"], ["test"], None, None)
    other = await build(client, driver, data_dir, alpha_rows("unlisted"), [plain], "unlisted")
    every = {v["id"] for v in (await client.get(V)).json()["items"]}
    assert {marked, other["version_id"]} <= every
    held = {v["id"] for v in (await client.get(f"{V}?wsp_role=wsp_heldout")).json()["items"]}
    assert held == {marked}
    assert (await client.get(f"{V}?wsp_role=wsp_heldout")).json()["total"] == 1


def test_the_partitions_route_is_served() -> None:
    """FR-021.26 (M-30): the live app serves the route (the MCP parity test reads this too)."""
    paths = fastapi_app.openapi()["paths"]
    assert "get" in paths["/api/v1/versions/{version_id}/wsp-partitions"]


# --- FR-021.13: detector sets never train or calibrate on a held-out partition -------------------

MAPPING = {"pos": "positive", "neg": "negative"}


def role(version_id: str, name: str, split: str) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "role": name,
        "version_id": version_id,
        "split": split,
        "input_column": "text",
        "label_column": "label",
        "label_mapping": MAPPING,
    }
    if name == "calibration_negatives":
        spec["label_mapping"] = {"neg": "negative", "pos": "excluded"}
        spec["negatives_basis"] = {
            "kind": "labeler_filtered",
            "labeler_identity_hash": "a" * 64,
            "rule": "fixture",
        }
    return spec


def set_body(name: str, roles: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": name,
        "description": "WSP binding",
        "positive_meaning": "The text is a pump fact",
        "roles": roles,
    }


@pytest.mark.parametrize("headers", [{}, AGENT], ids=["operator", "agent"])
@pytest.mark.parametrize("bound", ["train", "calibration_negatives"])
async def test_detector_train_refused(
    client: httpx.AsyncClient,
    driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    bound: str,
    headers: dict[str, str],
) -> None:
    """FR-021.13 (M-27): create AND update refuse a held-out partition as train or calibration
    negatives; the same split as ood_eval is accepted."""
    version_id = await alpha(client, driver, data_dir, f"bind-{bound.replace('_', '-')}")
    refused = await client.post(
        "/api/v1/detector-sets",
        json=set_body(f"bad-{bound.replace('_', '-')}", [role(version_id, bound, "long")]),
        headers=headers,
    )
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "wsp_heldout_as_training"
    assert error["details"]["split"] == "long" and error["details"]["role"] == bound
    ok = await client.post(
        "/api/v1/detector-sets",
        json=set_body(f"ok-{bound.replace('_', '-')}", [role(version_id, "ood_eval", "long")]),
        headers=headers,
    )
    assert ok.status_code == 201, ok.text
    updated = await client.patch(
        f"/api/v1/detector-sets/{ok.json()['id']}",
        json={"roles": [role(version_id, bound, "long")]},
        headers=headers,
    )
    assert updated.status_code == 409, updated.text
    assert updated.json()["error"]["code"] == "wsp_heldout_as_training"


# --- FR-021.14: a download for practice refuses a held-out partition ----------------------------


async def test_download_practice_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """FR-021.14 (M-28): with ``purpose: wsp_practice`` a held-out partition and an unmarked split
    are refused, naming the practice splits; a practice split and every call without a purpose
    are served as before."""
    version_id = await alpha(client, driver, data_dir, "download")
    url = f"{V}/{version_id}/splits/{{split}}/download-url"
    held = await client.post(url.format(split="long"), json={"purpose": "wsp_practice"})
    assert held.status_code == 409, held.text
    assert held.json()["error"]["code"] == "wsp_heldout_as_practice"
    assert held.json()["error"]["details"]["practice_splits"] == ["train"]
    plain = await client.post(url.format(split="test"), json={"purpose": "wsp_practice"})
    assert plain.status_code == 409 and plain.json()["error"]["code"] == "not_a_wsp_partition"
    ok = await client.post(url.format(split="train"), json={"purpose": "wsp_practice"})
    assert ok.status_code == 200, ok.text
    for split in ("long", "test"):
        unchanged = await client.post(url.format(split=split), json={})
        assert unchanged.status_code == 200, unchanged.text
