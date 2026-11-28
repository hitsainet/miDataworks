"""The Hugging Face token through a full publish — public, approved for an agent, with a forced
Hub error and a re-verify — and it appears nowhere (FR-008.18; 008 FTASKS 15.2, 15.3).

Searched: every ``dw_*`` row (discovered from the live metadata), every file under ``DATA_DIR``,
every job result, every approval payload, every Socket.IO payload the worker emitted, every log
record, and the API's responses. The fake Hub must have RECEIVED the token, so the test proves
the token travelled and was not merely absent.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from src.core.database import Base, get_sync_engine
from tests.support.publish_fixtures import (
    TOKEN,
    PublishDriver,
    built_version,
    completed_build,
    owners_report_green,
    publish,
    publish_and_run,
    publisher,
    store_token,
)
from tests.support.version_fixtures import BuildDriver, driver

__all__ = ["driver", "publisher"]


def _rows() -> str:
    chunks: list[str] = []
    with get_sync_engine().connect() as conn:
        for table in Base.metadata.sorted_tables:
            chunks.extend(conn.execute(text(f"SELECT t::text FROM {table.name} AS t")).scalars())
    return "\n".join(chunks)


async def test_the_token_appears_nowhere_after_publishing(
    client: httpx.AsyncClient,
    operator_name: str,
    driver: BuildDriver,
    publisher: PublishDriver,
    data_dir: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    owners_report_green(monkeypatch)
    await store_token(client)
    version_id = await built_version(client, driver, data_dir)
    build = await completed_build(client, publisher, version_id)
    pub = await publish_and_run(client, publisher, version_id, build["id"], visibility="public")
    assert pub["status"] == "published"
    agent = await publish(
        client,
        publisher,
        version_id,
        build["id"],
        repo="mistudio/agent-repo",
        headers={"X-Dataworks-Agent": "agent:dataworks-mcp"},
    )
    await client.post(f"/api/v1/approvals/{agent.json()['approval_id']}/approve")
    publisher.hub.corrupt_path = "README.md"
    publisher.run_publish_tasks()
    await client.post(f"/api/v1/publishes/{pub['id']}/reverify")
    publisher.run_publish_tasks()
    assert TOKEN in publisher.hub.received_tokens, "the worker really used the token"

    responses = "".join(
        [
            (await client.get("/api/v1/publishes")).text,
            (await client.get(f"/api/v1/publishes/{pub['id']}")).text,
            (await client.get("/api/v1/jobs")).text,
            (await client.get("/api/v1/approvals")).text,
            (await client.get(f"/api/v1/versions/{version_id}/handoff-manifest")).text,
        ]
    )
    files = b"\n".join(p.read_bytes() for p in data_dir.rglob("*") if p.is_file())
    hub_files = b"\n".join(
        c for r in publisher.hub.repos.values() for k in r.commits for c in k.files.values()
    )
    assert TOKEN not in _rows(), "a dw_* row holds the token"
    assert TOKEN.encode() not in files, "a file under DATA_DIR holds the token"
    assert TOKEN.encode() not in hub_files, "a published file holds the token"
    assert TOKEN not in str(publisher.emitted), "a socket payload holds the token"
    assert TOKEN not in caplog.text, "a log record holds the token"
    assert TOKEN not in responses, "an API response holds the token"
