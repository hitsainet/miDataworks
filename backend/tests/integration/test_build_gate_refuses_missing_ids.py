"""ENH-006 G22: a build naming something that does not exist is refused, never queued.

``dataworks_build_version`` with revision id ``PLACEHOLDER`` (an agent's error) answered with a
pending ``agent_label_rows`` approval: the gate's predicate could not resolve the revision, failed
closed and gated, and the id was checked only after the operator clicked approve. Now the predicate
checks every id the build names first and the route answers its own ``404``, with no approval row.
Real failures to decide still gate (``test_approvals.py::test_only_the_refusal_type_escapes_the_gate``).

Driven through both gated routes and through the PRODUCTION MCP registry (``build_server``) over
the in-process app.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy import func, select

from src.core.database import sync_session_factory
from src.models import AgentLabelRow
from src.models.approval import Approval
from tests.integration.test_version_build import setup, src
from tests.support.mcp_harness import tool_context
from tests.support.mcp_live import production_mcp
from tests.support.stub_operators import body
from tests.support.version_fixtures import BuildDriver, driver, make_source

__all__ = ["driver"]
AGENT = {"X-Dataworks-Agent": "agent:mcp"}
R = "/api/v1/recipes"


def approvals() -> int:
    with sync_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(Approval)) or 0)


def ledger_rows() -> int:
    with sync_session_factory()() as db:
        return int(db.scalar(select(func.count()).select_from(AgentLabelRow)) or 0)


async def _gated_fixture(client: httpx.AsyncClient, data_dir: Path) -> tuple[str, str, str]:
    """A dataset, a labelling recipe and a 6,000-row source: a real build here IS gated."""
    rows = [{"text": f"row {i} of the missing-id fixture", "label": i % 2} for i in range(6000)]
    source = make_source(data_dir, {"train": rows})
    ds, rev = await setup(client, body(("stub_probe_verdict", {})))
    return ds, rev, source


def _missing_cases(ds: str, rev: str, source: str) -> list[tuple[str, dict[str, Any], str]]:
    ghost = str(uuid.uuid4())
    return [
        ("PLACEHOLDER revision", {"recipe_revision_id": "PLACEHOLDER"}, "revision_not_found"),
        ("unknown revision", {"recipe_revision_id": ghost}, "revision_not_found"),
        ("unknown dataset", {"dataset_id": ghost}, "dataset_not_found"),
        ("unknown source", {"inputs": [src(ghost)]}, "source_not_found"),
        ("unknown second input", {"inputs": [src(source), src(ghost)]}, "source_not_found"),
        (
            "unknown version",
            {"inputs": [{"kind": "version", "version_id": ghost}]},
            "version_not_found",
        ),
    ]


async def test_an_agent_build_naming_a_missing_id_is_refused_404_not_queued(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    ds, rev, source = await _gated_fixture(client, data_dir)
    for label, change, code in _missing_cases(ds, rev, source):
        request = {"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)], **change}
        response = await client.post("/api/v1/versions", json=request, headers=AGENT)
        assert response.status_code == 404, (label, response.text)
        assert response.json()["error"]["code"] == code, (label, response.text)
    assert approvals() == 0, "a missing id became an operator approval"
    assert ledger_rows() == 0

    # The control: the same build with every id real is still gated (the check did not open it).
    real = {"dataset_id": ds, "recipe_revision_id": rev, "inputs": [src(source)]}
    gated = await client.post("/api/v1/versions", json=real, headers=AGENT)
    assert gated.status_code == 202 and gated.json()["action"] == "agent_label_rows", gated.text
    assert approvals() == 1


async def test_the_recipe_build_route_refuses_a_missing_recipe_or_input(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    ds, _rev, source = await _gated_fixture(client, data_dir)
    recipe_id = (await client.get(R)).json()["items"][0]["id"]
    ghost = str(uuid.uuid4())
    cases = [
        (f"{R}/{ghost}/build", {"dataset_id": ds, "inputs": [src(source)]}, "recipe_not_found"),
        (f"{R}/{recipe_id}/build", {"dataset_id": ds, "inputs": [src(ghost)]}, "source_not_found"),
    ]
    for path, request, code in cases:
        response = await client.post(path, json=request, headers=AGENT)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["error"]["code"] == code, response.text
    assert approvals() == 0
    gated = await client.post(
        f"{R}/{recipe_id}/build", json={"dataset_id": ds, "inputs": [src(source)]}, headers=AGENT
    )
    assert gated.status_code == 202 and "approval_id" in gated.json(), gated.text


async def test_a_deleted_version_input_is_refused_before_the_gate(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """A deleted version's rows are gone for good: no approval can ever make it build."""
    from tests.integration.test_version_build import build

    rows = [{"text": f"short row {i}", "label": i % 2} for i in range(20)]
    source = make_source(data_dir, {"train": rows})
    ds, keep_rev = await setup(client, body(("stub_keep", {})), name="parent")
    parent = await build(client, driver, ds, keep_rev, [src(source)])
    deleted = await client.request(
        "DELETE", f"/api/v1/versions/{parent['id']}", json={"reason": "G22 fixture"}
    )
    assert deleted.status_code == 200 and deleted.json()["state"] == "deleted", deleted.text
    _ds2, rev = await setup(client, body(("stub_probe_verdict", {})), name="child")
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": ds,
            "recipe_revision_id": rev,
            "inputs": [{"kind": "version", "version_id": parent["id"]}],
        },
        headers=AGENT,
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "version_deleted"
    assert approvals() == 0


async def test_the_mcp_build_tool_gets_the_refusal_not_an_approval(
    client: httpx.AsyncClient,
    driver: BuildDriver,
    data_dir: Path,
    operator_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The incident itself: dataworks_build_version with revision id PLACEHOLDER."""
    ds, rev, source = await _gated_fixture(client, data_dir)
    mcp, backend = production_mcp(monkeypatch)
    try:
        arguments = {"dataset_id": ds, "recipe_revision_id": "PLACEHOLDER", "inputs": [src(source)]}
        with pytest.raises(ToolError) as refused:
            await mcp._tool_manager.call_tool(  # noqa: SLF001
                "dataworks_build_version", arguments, tool_context(mcp)
            )
        assert "revision_not_found (404)" in str(refused.value), str(refused.value)
        assert approvals() == 0

        arguments["recipe_revision_id"] = rev
        pending = await mcp._tool_manager.call_tool(  # noqa: SLF001
            "dataworks_build_version", arguments, tool_context(mcp)
        )
        assert pending["status"] == "pending" and pending["action"] == "agent_label_rows"
        assert approvals() == 1
    finally:
        await backend.close()


async def test_version_delete_and_reproduction_link_refuse_missing_ids_before_the_gate(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Review minor 4 (2026-11-08): the two always-gated routes that name a version answer 404 for
    a version (or split) that does not exist instead of queueing an approval; real ids still wait.
    """
    from tests.integration.test_version_build import build

    rows = [{"text": f"short row {i}", "label": i % 2} for i in range(20)]
    source = make_source(data_dir, {"train": rows})
    ds, rev = await setup(client, body(("stub_keep", {})), name="kept")
    real = (await build(client, driver, ds, rev, [src(source)]))["id"]
    ghost = str(uuid.uuid4())

    missing = await client.request(
        "DELETE", f"/api/v1/versions/{ghost}", json={"reason": "typo"}, headers=AGENT
    )
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "version_not_found"
    assert approvals() == 0

    # The link predicate runs the ROUTE's own checks, so the refusal is the route's (re-review 2).
    link = {"mistudio_probe_id": "pm_1", "probe_dataset_id": "pd_1", "split": "train"}
    for version_id, split, status, code in (
        (ghost, "train", 409, "version_incomplete"),
        (real, "nope", 422, "role_invalid"),
    ):
        refused = await client.post(
            "/api/v1/reproduction-links",
            json={**link, "version_id": version_id, "split": split},
            headers=AGENT,
        )
        assert refused.status_code == status, refused.text
        assert refused.json()["error"]["code"] == code
    assert approvals() == 0

    waits = await client.post(
        "/api/v1/reproduction-links", json={**link, "version_id": real}, headers=AGENT
    )
    assert waits.status_code == 202 and waits.json()["action"] == "gate_target_write", waits.text
    deletes = await client.request(
        "DELETE", f"/api/v1/versions/{real}", json={"reason": "cleanup"}, headers=AGENT
    )
    assert deletes.status_code == 202 and deletes.json()["action"] == "version_delete", deletes.text
    assert approvals() == 2


async def test_an_already_deleted_version_is_refused_before_the_gate(
    client: httpx.AsyncClient, driver: BuildDriver, data_dir: Path, operator_name: str
) -> None:
    """Re-review minor 1: deleting a version that is already deleted would do nothing, and linking
    one is refused by the route; both are refused before an approval is queued."""
    from tests.integration.test_version_build import build

    rows = [{"text": f"short row {i}", "label": i % 2} for i in range(20)]
    source = make_source(data_dir, {"train": rows})
    ds, rev = await setup(client, body(("stub_keep", {})), name="gone")
    gone = (await build(client, driver, ds, rev, [src(source)]))["id"]
    deleted = await client.request("DELETE", f"/api/v1/versions/{gone}", json={"reason": "fixture"})
    assert deleted.status_code == 200 and deleted.json()["state"] == "deleted", deleted.text

    again = await client.request(
        "DELETE", f"/api/v1/versions/{gone}", json={"reason": "again"}, headers=AGENT
    )
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "version_deleted"
    link = await client.post(
        "/api/v1/reproduction-links",
        json={
            "mistudio_probe_id": "pm_1",
            "probe_dataset_id": "pd_1",
            "version_id": gone,
            "split": "train",
        },
        headers=AGENT,
    )
    assert link.status_code == 409, link.text
    assert link.json()["error"]["code"] == "version_incomplete"
    assert approvals() == 0
