"""Datasets REST (tasks 3.7, 13.1): create, list, get, patch, refusals in the ADR-013 envelope."""

from __future__ import annotations

import httpx

from src.core.database import sync_session_factory
from src.models import Dataset
from tests.support import db_factories as f


async def _create(
    client: httpx.AsyncClient, name: str = "humor-detector", **extra: object
) -> dict[str, object]:
    response = await client.post(
        "/api/v1/datasets", json={"name": name, "target_type": "detector", **extra}
    )
    assert response.status_code == 201, response.text
    data: dict[str, object] = response.json()
    return data


async def test_create_records_who_and_lists(client: httpx.AsyncClient, operator_name: str) -> None:
    created = await _create(client, description="ColBERT humor")
    assert created["created_by"] == operator_name
    assert created["head_number"] is None and created["state"] == "empty"
    listed = (await client.get("/api/v1/datasets")).json()
    assert listed["total"] == 1 and listed["items"][0]["name"] == "humor-detector"
    got = (await client.get(f"/api/v1/datasets/{created['id']}")).json()
    assert got["version_list"] == []


async def test_a_taken_name_is_refused(client: httpx.AsyncClient, operator_name: str) -> None:
    await _create(client)
    response = await client.post("/api/v1/datasets", json={"name": "humor-detector"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_name_taken"


async def test_a_bad_name_is_refused(client: httpx.AsyncClient, operator_name: str) -> None:
    response = await client.post("/api/v1/datasets", json={"name": "Not Valid"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_an_unknown_dataset_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/datasets/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"
    assert (await client.get("/api/v1/datasets/not-a-uuid")).json()["error"][
        "code"
    ] == "dataset_not_found"


async def test_target_type_changes_while_empty(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    created = await _create(client)
    response = await client.patch(f"/api/v1/datasets/{created['id']}", json={"target_type": "sft"})
    assert response.status_code == 200 and response.json()["target_type"] == "sft"


async def test_target_type_is_refused_by_the_service_once_a_version_exists(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    created = await _create(client)
    with sync_session_factory()() as db:
        row = db.get(Dataset, created["id"])
        f.version(db, row)
        db.commit()
    response = await client.patch(f"/api/v1/datasets/{created['id']}", json={"target_type": "sft"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_has_versions"
    described = await client.patch(f"/api/v1/datasets/{created['id']}", json={"description": "x"})
    assert described.status_code == 200 and described.json()["description"] == "x"
    summary = (await client.get("/api/v1/datasets")).json()["items"][0]
    assert summary["head_number"] == 1 and summary["versions"] == 1


async def test_list_filters_by_target_type_and_query(
    client: httpx.AsyncClient, operator_name: str
) -> None:
    await _create(client, "alpha")
    await client.post("/api/v1/datasets", json={"name": "beta", "target_type": "sft"})
    assert (await client.get("/api/v1/datasets?target_type=sft")).json()["total"] == 1
    assert (await client.get("/api/v1/datasets?q=alp")).json()["items"][0]["name"] == "alpha"
