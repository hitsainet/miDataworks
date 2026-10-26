"""D5 (2026-10-08): a role may declare the derived system columns the shortcut audit READS
(``_dw_origin``, ``_dw_source_id``) as label source columns; it was refused because the check read
the version's column list, where no ``_dw_`` column is offered. Any other ``_dw_`` name is still
refused, and a declared system column is excluded from D-3 as ``label_source``."""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.support.detector_fixtures import humicroedit_body, humicroedit_version

API = "/api/v1"


async def test_the_audited_system_columns_can_be_declared_and_are_set_aside(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    body = humicroedit_body(
        humicroedit_version(), label_sources=["meanGrade", "grades", "_dw_origin"]
    )
    created = await client.post(f"{API}/detector-sets", json=body)
    assert created.status_code == 201, created.text
    train = next(r for r in created.json()["roles"] if r["role"] == "train")
    assert train["label_source_columns"] == ["meanGrade", "grades", "_dw_origin"]
    checks = await client.post(f"{API}/detector-sets/{created.json()['id']}/checks")
    d3 = next(o for o in checks.json()["outcomes"] if o["code"] == "D-3")
    excluded = {e["column"]: e["reason"] for e in d3["details"]["excluded"]}
    assert excluded["_dw_origin"] == "label_source", d3


async def test_another_system_column_is_still_refused(
    client: httpx.AsyncClient, operator_name: str, data_dir: Path
) -> None:
    body = humicroedit_body(humicroedit_version(), label_sources=["_dw_row_key"])
    response = await client.post(f"{API}/detector-sets", json=body)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "role_invalid"
    assert "_dw_row_key" in response.json()["error"]["message"]
