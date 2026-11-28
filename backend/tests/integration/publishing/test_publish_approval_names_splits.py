"""A ``hub_push`` approval names what it pushes: the dataset, the version and every split with its
rows (FU-7a gap (o), 2026-11-27).

``apr_c4440ab7…`` read "Push version 160d1d88… to mistudio/everyday-text-owt-fu7a-v3 (private,
publish)". The operator was asked to approve "the reserve everyday v3" and could not see that it also
carries the 5,000-row ``everyday_fresh``. The expected names and rows below come from the VERSION
read, not from the build the summary is made from.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.support.publish_fixtures import (
    REPO,
    PublishDriver,
    built_version,
    completed_build,
    publish,
    publisher,
    store_token,
)
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}


async def test_the_push_approval_names_the_dataset_version_and_every_split(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir, name="everyday")
    build = await completed_build(client, publisher, version_id)
    version = (await client.get(f"/api/v1/versions/{version_id}")).json()
    splits = {s["name"]: s["rows"] for s in version["splits"]}
    assert len(splits) == 2 and all(splits.values())  # not vacuous: two splits, both non-empty

    response = await publish(client, publisher, version_id, build["id"], headers=AGENT)
    assert response.status_code == 202, response.text
    summary = response.json()["approval"]["summary"]
    for name, rows in splits.items():
        assert f"{name} {rows:,} rows" in summary, summary
    assert "2 splits" in summary
    assert f"everyday version {version['number']}" in summary and version_id in summary
    assert REPO in summary and "private" in summary

    stored = (await client.get(f"/api/v1/approvals/{response.json()['approval_id']}")).json()
    assert stored["summary"] == summary  # what dataworks_get_approval_status and the app show
    facts = stored["payload"]["facts"]
    assert {s["name"]: s["rows"] for s in facts["splits"]} == splits
    assert facts["dataset_name"] == "everyday" and facts["version_number"] == version["number"]


async def test_a_card_only_republish_says_no_split_file_changes(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
) -> None:
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    first = await publish(client, publisher, version_id, build["id"])
    publisher.run_publish_tasks()
    response = await client.post(
        f"/api/v1/publishes/{first.json()['publish_id']}/card",
        json={"card_prose": "agent words"},
        headers=AGENT,
    )
    assert response.status_code == 202, response.text
    summary = response.json()["approval"]["summary"]
    assert "card only" in summary and "no split file changes" in summary
    assert "rows" not in summary
    stored = (await client.get(f"/api/v1/approvals/{response.json()['approval_id']}")).json()
    assert stored["payload"]["facts"]["splits"] == []  # the card carries no split file
