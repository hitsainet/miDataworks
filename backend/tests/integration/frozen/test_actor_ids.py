"""DR-17: the old MCP actor id stays valid; the new one is accepted alike (feature 020; FR-020.10).

Rows written with ``agent:mistudio-mcp`` keep their meaning, and ``X-Dataworks-Agent:
agent:miworkbench-mcp`` (15 characters after ``agent:``) passes the header rule and is gated
exactly like the old id."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.agent_request import AgentRequest
from tests.support.frozen import exercises


async def _gated_request(client: httpx.AsyncClient, actor: str) -> dict[str, object]:
    """A secret write by an agent waits for the operator (``secret_write``)."""
    response = await client.put(
        "/api/v1/settings/hf_token",
        json={"value": "hf_" + "x" * 34},
        headers={"X-Dataworks-Agent": actor},
    )
    assert response.status_code == 202, response.text
    approval_id = response.json()["approval_id"]
    approval = (await client.get(f"/api/v1/approvals/{approval_id}")).json()
    return approval


def _activity() -> list[str]:
    with sync_session_factory()() as db:
        return [r.identity for r in db.execute(select(AgentRequest)).scalars()]


@exercises("DR-17", literals=("agent:mistudio-mcp",))
async def test_dr17_old_actor_id_is_recorded_as_an_agent(
    client: httpx.AsyncClient, literal: str
) -> None:
    approval = await _gated_request(client, literal)
    assert approval["requested_by"] == literal
    assert literal in _activity()


@pytest.mark.parametrize("actor", ["agent:mistudio-mcp", "agent:miworkbench-mcp"])
async def test_both_actor_ids_are_gated_alike(client: httpx.AsyncClient, actor: str) -> None:
    approval = await _gated_request(client, actor)
    assert approval["requested_by"] == actor and approval["status"] == "pending"
    assert _activity() == [actor], "the agent's request is recorded under the id it sent"
