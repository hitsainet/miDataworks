"""An agent never borrows the operator's Hugging Face token (022 FR-022.27, .28; suite A17, A28).

Two clients reach the Hub, and "no token" must mean no credential in both:

- miDataworks' own ``HubClient`` (preview, search, the import's revision and viewer reads) is spied
  by ``HfMock``, which records every request;
- the import's ``datasets.load_dataset`` runs for real, with ``huggingface_hub``'s transport
  replaced by a spy (``set_client_factory``) that records each request's headers. The spy SERVES a
  one-file parquet repository, so the library goes past its first request to the tree listing and
  the file download, and every one of those requests is checked (R3 review fix (OR3-022-4),
  2026-11-16: a spy answering 401 stopped the library at its first request). A second spy answers
  401 to everything, for the error an agent reads when the Hub refuses it.

``HF_TOKEN=hf_spy_env`` is set in the environment and a token is stored in Settings, so an
implicit credential has two places to come from. The assertions are on the HEADERS the spies saw,
never on the ``token`` argument, so the fixture cannot agree with the code by construction. The
operator import with no stored token must show ``Bearer hf_spy_env``: that proves the library spy
sees an implicit credential, so the agent case's empty header is not vacuous.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]

AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
STORED = "hf_stored_operator_token_022"
SUPPLIED = "hf_agent_supplied_token_022"
ENV_TOKEN = "hf_spy_env"


def _parquet() -> bytes:
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    buffer = io.BytesIO()
    pq.write_table(pa.table({"text": ["a joke", "not a joke"], "humor": [True, False]}), buffer)
    return buffer.getvalue()


class LibrarySpy:
    """``huggingface_hub``'s HTTP transport, replaced: records ``(method, url, authorization)``.

    ``serve`` answers any dataset repository as one parquet file at ``data/train.parquet`` (the
    reviewer's probe, ``review-022-r3/probe/deep_spy.py``); otherwise every request answers 401.
    """

    def __init__(self, serve: bool) -> None:
        self.serve = serve
        self.seen: list[tuple[str, str, str | None]] = []
        self.data = _parquet() if serve else b""

    def __call__(self, request: Any) -> Any:
        import httpx2

        self.seen.append((request.method, str(request.url), request.headers.get("authorization")))
        if not self.serve:
            return httpx2.Response(401, json={"error": "Repository not found"})
        path, size = request.url.path, len(self.data)
        # The revision asked for is the one answered, so the library's commit checks pass.
        pinned = re.search(r"/(?:revision|resolve|tree|paths-info)/([0-9a-f]{40})", path)
        sha = pinned.group(1) if pinned else "a" * 40
        file = {"type": "file", "path": "data/train.parquet", "size": size, "oid": "b" * 40}
        if path.startswith("/api/datasets/") and "/paths-info/" in path:
            return httpx2.Response(200, json=[file])
        if path.startswith("/api/datasets/") and "/tree/" in path:
            listing = (
                [{"type": "directory", "path": "data", "oid": "c" * 40}]
                if not path.rstrip("/").endswith("/data")
                else [file]
            )
            return httpx2.Response(200, json=listing)
        if path.startswith("/api/datasets/"):
            repo = path[len("/api/datasets/") :].split("/revision/")[0]
            siblings = [{"rfilename": "data/train.parquet"}, {"rfilename": "README.md"}]
            return httpx2.Response(
                200,
                json={"id": repo, "sha": sha, "siblings": siblings, "cardData": {}, "tags": []}
                | {"private": False, "gated": False},
            )
        if "/resolve/" in path and path.endswith(".parquet"):
            etag = '"' + hashlib.sha256(self.data).hexdigest() + '"'
            headers = {"x-repo-commit": sha, "etag": etag, "content-length": str(size)}
            body = b"" if request.method == "HEAD" else self.data
            return httpx2.Response(200, content=body, headers=headers)
        return httpx2.Response(404, headers={"x-error-code": "EntryNotFound"})

    def hub_requests(self) -> list[tuple[str, str, str | None]]:
        """Requests to the Hub's own host (the library never sends a token elsewhere)."""
        return [s for s in self.seen if s[1].startswith("https://huggingface.co/")]

    def downloads(self) -> list[tuple[str, str, str | None]]:
        return [
            s for s in self.seen if s[0] == "GET" and "/resolve/" in s[1] and ".parquet" in s[1]
        ]

    def authorizations(self) -> set[str | None]:
        """Every request's header, not only the first; and the download must be among them."""
        assert self.hub_requests(), "load_dataset made no Hub request: the spy proves nothing"
        if self.serve:
            assert self.downloads(), f"load_dataset downloaded no file: {self.seen}"
        return {a for _, _, a in self.hub_requests()}


def _install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, serve: bool) -> Iterator[LibrarySpy]:
    import httpx2
    import huggingface_hub
    from huggingface_hub.utils import _http

    from src.workers import source_tasks

    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf_home"))
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.setattr(source_tasks, "LOADER", None)  # the real datasets.load_dataset
    # datasets 5 downloads Hub files through hf_hub_download into the hub cache (read at call
    # time), not into load_dataset's cache_dir: keep the served file out of the user's cache.
    monkeypatch.setattr(huggingface_hub.constants, "HF_HUB_CACHE", str(tmp_path / "hub_cache"))
    spy = LibrarySpy(serve)
    default = _http._GLOBAL_CLIENT_FACTORY  # noqa: SLF001 - restored below
    huggingface_hub.set_client_factory(lambda: httpx2.Client(transport=httpx2.MockTransport(spy)))
    _http.close_session()
    try:
        yield spy
    finally:
        huggingface_hub.set_client_factory(default)
        _http.close_session()


@pytest.fixture
def library(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hf_env: HfEnv) -> Iterator[LibrarySpy]:
    """The library over a served repository: its every request, the download included."""
    yield from _install(monkeypatch, tmp_path, serve=True)


@pytest.fixture
def refusing_library(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hf_env: HfEnv
) -> Iterator[LibrarySpy]:
    """The library over a Hub that refuses every request (401)."""
    yield from _install(monkeypatch, tmp_path, serve=False)


async def store_token(client: httpx.AsyncClient) -> None:
    response = await client.put("/api/v1/settings/hf_token", json={"value": STORED})
    assert response.status_code == 200, response.text


def auth(env: HfEnv) -> list[str | None]:
    return env.mock.auth_headers


async def run_import(
    client: httpx.AsyncClient, env: HfEnv, headers: dict[str, str] | None = None
) -> Any:
    response = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT}, headers=headers)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    env.run_imports()
    return env.job(job_id)


# -- preview -------------------------------------------------------------------------------------


async def test_an_agent_preview_sends_no_token(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    await store_token(client)
    response = await client.post(
        "/api/v1/sources/hf/preview", json={"repo_id": COLBERT}, headers=AGENT
    )
    assert response.status_code == 200, response.text
    assert auth(hf_env), "the preview reached no Hub route: the spy proves nothing"
    assert set(auth(hf_env)) == {None}, auth(hf_env)
    assert hf_env.sent[0][1][1]["agent_caller"] is True


async def test_an_operator_preview_uses_the_stored_token(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    await store_token(client)
    response = await client.post("/api/v1/sources/hf/preview", json={"repo_id": COLBERT})
    assert response.status_code == 200, response.text
    assert set(auth(hf_env)) == {f"Bearer {STORED}"}, auth(hf_env)
    assert hf_env.sent[0][1][1]["agent_caller"] is False


# -- search --------------------------------------------------------------------------------------


async def test_an_agent_search_sends_no_token(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    await store_token(client)
    hf_env.mock.overrides["/api/datasets?"] = (200, [])
    response = await client.post("/api/v1/sources/hf/search", json={"query": "pii"}, headers=AGENT)
    assert response.status_code == 200, response.text
    assert auth(hf_env) == [None], auth(hf_env)
    assert hf_env.sent[0][1][1]["agent_caller"] is True


async def test_an_operator_search_uses_the_stored_token(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    await store_token(client)
    hf_env.mock.overrides["/api/datasets?"] = (200, [])
    response = await client.post("/api/v1/sources/hf/search", json={"query": "pii"})
    assert response.status_code == 200, response.text
    assert auth(hf_env) == [f"Bearer {STORED}"], auth(hf_env)
    assert hf_env.sent[0][1][1]["agent_caller"] is False


# -- import: both clients -------------------------------------------------------------------------


async def test_an_agent_import_sends_no_token_on_either_client(
    client: httpx.AsyncClient, hf_env: HfEnv, library: LibrarySpy, operator_name: str
) -> None:
    await store_token(client)
    job = await run_import(client, hf_env, AGENT)
    assert job.params["agent_caller"] is True
    assert auth(hf_env) and set(auth(hf_env)) == {None}, auth(hf_env)
    assert library.authorizations() == {None}, library.seen
    assert {a for _, _, a in library.seen} == {None}, "no host at all got a credential"


async def test_an_agent_import_the_hub_refuses_reads_not_found(
    client: httpx.AsyncClient, hf_env: HfEnv, refusing_library: LibrarySpy, operator_name: str
) -> None:
    await store_token(client)
    job = await run_import(client, hf_env, AGENT)
    assert refusing_library.authorizations() == {None}, refusing_library.seen
    # With no token sent, a 401 reads as not found, never a rejected token.
    assert job.status == "failed", job.status
    assert (job.result or {})["error"]["code"] == "hf_not_found", job.result


async def test_an_operator_import_uses_the_stored_token_on_both_clients(
    client: httpx.AsyncClient, hf_env: HfEnv, library: LibrarySpy, operator_name: str
) -> None:
    await store_token(client)
    job = await run_import(client, hf_env)
    assert job.params["agent_caller"] is False
    assert set(auth(hf_env)) == {f"Bearer {STORED}"}, auth(hf_env)
    assert library.authorizations() == {f"Bearer {STORED}"}, library.seen


async def test_an_operator_import_with_no_stored_token_sends_the_implicit_one(
    client: httpx.AsyncClient, hf_env: HfEnv, library: LibrarySpy, operator_name: str
) -> None:
    """Unchanged for the operator (token=None), and the proof that the spy sees HF_TOKEN."""
    await run_import(client, hf_env)
    assert library.authorizations() == {f"Bearer {ENV_TOKEN}"}, library.seen


async def test_an_agents_approved_token_is_sent(
    client: httpx.AsyncClient, hf_env: HfEnv, library: LibrarySpy, operator_name: str
) -> None:
    await store_token(client)
    pending = await client.post(
        "/api/v1/sources/hf", json={"repo_id": COLBERT, "access_token": SUPPLIED}, headers=AGENT
    )
    assert pending.status_code == 202 and pending.json()["status"] == "pending", pending.text
    approved = await client.post(f"/api/v1/approvals/{pending.json()['approval_id']}/approve")
    assert approved.status_code == 200, approved.text
    hf_env.run_imports()
    assert set(auth(hf_env)) == {f"Bearer {SUPPLIED}"}, auth(hf_env)
    assert library.authorizations() == {f"Bearer {SUPPLIED}"}, library.seen


# -- the worker fails closed ----------------------------------------------------------------------


def test_a_request_with_no_agent_caller_field_is_treated_as_an_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview or search enqueued before the push carries no ``agent_caller``: anonymous."""
    from src.workers import source_tasks

    monkeypatch.setattr(source_tasks, "resolve_hf_token", lambda db: STORED)
    monkeypatch.setattr(source_tasks.ephemeral_secrets, "take", lambda key: None)
    assert source_tasks._token("k", False, source_tasks.agent_caller_of({})) == (None, "none")
    assert source_tasks._token("k", False, False) == (STORED, "stored")


def test_the_chooser_skips_the_stored_token_when_told() -> None:
    from src.services.sources.tokens import choose_token

    token, tier = choose_token(
        take=lambda: None, stored=lambda: STORED, supplied=False, allow_stored=False
    )
    assert (token, tier.value) == (None, "none")
    token, tier = choose_token(
        take=lambda: SUPPLIED, stored=lambda: STORED, supplied=True, allow_stored=False
    )
    assert (token, tier.value) == (SUPPLIED, "per_import")


# -- the texts say so (FR-022.29) -----------------------------------------------------------------


def test_the_tool_texts_say_an_agents_hub_call_is_anonymous() -> None:
    import asyncio

    from src.mcp_server.config import MCPSettings
    from src.mcp_server.server import build_server
    from src.mcp_server.tools.core import HOWTO

    mcp, _ = build_server(MCPSettings(auth_token="x" * 32))
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name in (
        "dataworks_search_hf_datasets",
        "dataworks_preview_hf_dataset",
        "dataworks_import_hf_dataset",
    ):
        description = tools[name].description or ""
        assert "anonymous" in description, name
        assert "Uses the stored HF token" not in description, name
    assert "anonymous" in HOWTO["secrets"]


# -- one request, one job: never across callers (022 review R2, finding 2) -----------------------


@pytest.mark.parametrize("agent_first", [False, True])
async def test_an_agent_and_an_operator_never_share_an_import_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str, agent_first: bool
) -> None:
    await store_token(client)
    order = [AGENT, None] if agent_first else [None, AGENT]
    first = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT}, headers=order[0])
    second = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT}, headers=order[1])
    assert first.status_code == second.status_code == 202, (first.text, second.text)
    assert first.json()["job_id"] != second.json()["job_id"], "one caller got the other's job"
    assert second.json().get("existing_job") is False
    a, b = hf_env.job(first.json()["job_id"]), hf_env.job(second.json()["job_id"])
    assert a.params["agent_caller"] is agent_first and b.params["agent_caller"] is not agent_first


async def test_the_same_callers_repeat_still_answers_its_own_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT}, headers=AGENT)
    again = await client.post("/api/v1/sources/hf", json={"repo_id": COLBERT}, headers=AGENT)
    assert again.json()["job_id"] == first.json()["job_id"] and again.json()["existing_job"] is True
