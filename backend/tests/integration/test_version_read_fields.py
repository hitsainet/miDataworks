"""A version read trimmed to the fields asked for, and the dataset on every version (FU-7a MCP gaps
(e) and (r), 2026-11-27).

(e) Live: ``dataworks_get_version`` answered about 100 KB per call, mostly ``column_roles`` and the
behaviour census, when a pass-1 check needed only the splits and the drop summary. ``fields`` now
trims the answer to those fields plus the version's identity; without it the answer is unchanged.
The accepted names are the read's own (``VersionOut``), and an unknown one is refused naming them.

(r) Version numbers are per dataset: FU-7a's v37 stands beside another dataset's v37. Not a defect:
``dataset_id`` and ``dataset_name`` are on every list entry and on the read. These tests pin it with
two datasets whose versions share their numbers.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from src.core.database import sync_session_factory
from src.schemas.versions import VersionOut
from tests.support import db_factories
from tests.support.mcp_live import call, live_mcp, refusal_of

IDENTITY = {"id", "dataset_id", "dataset_name", "number", "state", "sensitivity"}


def _versions() -> dict[str, Any]:
    """Two datasets, each with versions 1 and 2; the versions carry a wide column-role map, a
    census and a drop summary, all different between the two datasets."""
    out: dict[str, Any] = {}
    with sync_session_factory()() as db:
        for name, width in (("fu7a-fields", 300), ("other-fields", 7)):
            ds = db_factories.dataset(db, name=f"{name}-{db_factories.uid()[:6]}")
            ids = []
            for _ in range(2):
                row = db_factories.version(
                    db,
                    ds,
                    column_roles={f"col_{i:04d}": "metadata" for i in range(width)},
                    drop_summary=[{"step": 3, "operator": "empty_content", "dropped": width}],
                    behaviour_census={
                        "cells": [{"cell": f"c{i}", "rows": i} for i in range(width)]
                    },
                    total_rows=width,
                )
                ids.append(row.id)
            out[name] = {"dataset_id": ds.id, "dataset_name": ds.name, "versions": ids}
        db.commit()
    return out


async def test_fields_trims_the_read_and_the_default_is_unchanged(
    client: httpx.AsyncClient,
) -> None:
    made = _versions()
    version_id = made["fu7a-fields"]["versions"][1]
    full = (await client.get(f"/api/v1/versions/{version_id}")).json()
    assert set(full) == set(VersionOut.model_fields), "the default answer keeps every field"
    assert len(full["column_roles"]) == 300

    mcp, mcp_client = live_mcp()
    try:
        trimmed = await call(
            mcp,
            "dataworks_get_version",
            {"version_id": version_id, "fields": ["splits", "drop_summary"]},
        )
        default = await call(mcp, "dataworks_get_version", {"version_id": version_id})
    finally:
        await mcp_client.close()
    assert set(trimmed) == IDENTITY | {"splits", "drop_summary"}, sorted(trimmed)
    for key in IDENTITY | {"splits", "drop_summary"}:
        assert trimmed[key] == full[key], key
    assert len(json.dumps(trimmed)) * 10 < len(json.dumps(full))
    assert set(default) == set(full) and default["column_roles"] == full["column_roles"]

    # the REST form takes the same names, comma-separated
    rest = await client.get(
        f"/api/v1/versions/{version_id}", params={"fields": "behaviour_census, total_rows"}
    )
    assert rest.status_code == 200, rest.text
    assert set(rest.json()) == IDENTITY | {"behaviour_census", "total_rows"}
    assert rest.json()["behaviour_census"] == full["behaviour_census"]


async def test_an_unknown_field_is_refused_naming_the_accepted_ones(
    client: httpx.AsyncClient,
) -> None:
    version_id = _versions()["other-fields"]["versions"][0]
    refused = await client.get(f"/api/v1/versions/{version_id}", params={"fields": "splits,census"})
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "unknown_field" and error["details"]["unknown"] == ["census"]
    assert error["details"]["accepted"] == sorted(VersionOut.model_fields)
    mcp, mcp_client = live_mcp()
    try:
        try:
            await call(
                mcp, "dataworks_get_version", {"version_id": version_id, "fields": ["census"]}
            )
        except Exception as exc:  # noqa: BLE001 - the tool's error text is the answer
            code, status, details = refusal_of(exc)
        else:
            raise AssertionError("an unknown field was accepted")
    finally:
        await mcp_client.close()
    assert (code, status, details["unknown"]) == ("unknown_field", 422, ["census"])


async def test_each_version_names_its_dataset_where_numbers_repeat(
    client: httpx.AsyncClient,
) -> None:
    made = _versions()
    mcp, mcp_client = live_mcp()
    try:
        listed = await call(mcp, "dataworks_list_versions", {"order": "recent", "limit": 200})
        reads = {
            vid: await call(mcp, "dataworks_get_version", {"version_id": vid, "fields": ["number"]})
            for info in made.values()
            for vid in info["versions"]
        }
    finally:
        await mcp_client.close()
    entries = {e["id"]: e for e in listed["items"]}
    for info in made.values():
        for number, vid in enumerate(info["versions"], start=1):
            for answer in (entries[vid], reads[vid]):
                assert (answer["number"], answer["dataset_id"], answer["dataset_name"]) == (
                    number,
                    info["dataset_id"],
                    info["dataset_name"],
                ), answer
    # the same number, told apart by its dataset
    ones = {entries[info["versions"][0]]["dataset_name"] for info in made.values()}
    assert len(ones) == 2
