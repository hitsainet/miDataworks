"""Generation never sends a role key bound to another origin (operator decision 2026-10-20).

A standard run on both engine paths (native ``EndpointCaller``, and the loopback relay reached
through 003's operator port) and a preview, asserted on the fake miLLM's recorded headers: with the
key bound to the generation URL's origin, every request carries it (so each path passes
``key_origin`` through); with the stored origin elsewhere, none does.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from src.core.key_binding import url_origin
from tests.support.generation_fixtures import (
    GEN_MODEL,
    KEY,
    Gen,
    make_version,
    respond_template,
    run_body,
    set_role,
    texts,
    version_table,
)

RUNS = "/api/v1/generation-runs"
BEARER = f"Bearer {KEY}"


def _auth(requests: list[Any]) -> set[str | None]:
    return {r.headers.get("authorization") for r in requests}


async def _run_and_preview(client: httpx.AsyncClient, gen: Gen) -> tuple[list[Any], list[Any]]:
    version = make_version(version_table(texts(4)))
    template = await respond_template(client)
    start = await client.post(RUNS, json=run_body(version, template))
    assert start.status_code == 202, start.text
    before = len(gen.chats())
    run = gen.run_until_done(start.json()["id"])
    assert run.state == "completed", run.error
    run_chats = gen.chats()[before:]
    before = len(gen.chats())
    preview = await client.post(
        f"{RUNS}/preview",
        json={"prompts": ["one"], "respond_template_id": template, "setting": {"kind": "none"}},
    )
    assert preview.status_code == 200, preview.text
    return run_chats, gen.chats()[before:]


@pytest.mark.parametrize("path", ["native", "relay"])
async def test_the_bound_key_reaches_every_generation_request(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_engine_path", path)
    run_chats, preview_chats = await _run_and_preview(client, gen)
    assert run_chats and preview_chats
    assert _auth(run_chats) == {BEARER}, f"the {path} run did not send the bound key"
    assert _auth(preview_chats) == {BEARER}, "the preview did not send the bound key"


@pytest.mark.parametrize("path", ["native", "relay"])
async def test_a_key_bound_elsewhere_reaches_no_generation_request(
    client: httpx.AsyncClient, gen: Gen, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "generation_engine_path", path)
    set_role("generation", gen.base_url, GEN_MODEL, key_origin=url_origin("http://elsewhere.test"))
    run_chats, preview_chats = await _run_and_preview(client, gen)
    assert run_chats and preview_chats
    assert _auth(run_chats) == {None}, f"the {path} run sent a key bound to another origin"
    assert _auth(preview_chats) == {None}
    assert _auth(gen.fake.requests) == {None}, "no request of any kind carried the key"
