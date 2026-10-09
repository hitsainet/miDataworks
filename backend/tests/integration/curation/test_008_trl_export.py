"""Success criterion 6 end to end: feature 008's export is refused by feature 004's REAL validator,
naming the rule and the column (FR-004.23, FR-008.26). Nothing stubs 004."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.integration.publishing import test_trl_export as t
from tests.support.publish_fixtures import PublishDriver, publisher
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]


async def test_a_dpo_export_with_an_empty_rejected_is_refused_with_rule_and_column(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [dict(r) for r in t.ROWS["dpo"]]
    rows[2]["rejected"] = "   "
    monkeypatch.setitem(t.ROWS, "dpo", rows)
    version_id = await t.version_of(client, driver, data_dir, "dpo")
    response = await client.post(
        "/api/v1/exports", json={"target": "trl", "version_id": version_id, "trl_type": "dpo"}
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "trl_validation_failed"
    first = error["details"]["failures"][0]
    assert first["rule"] == "non_empty:rejected" and first["column"] == "rejected"
