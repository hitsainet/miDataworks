"""Signed, single-use uploads end to end (G-3, FU-7a; operator decision 2026-11-25).

``POST /api/v1/sources/upload-url`` records the upload and signs a URL; ``PUT /api/v1/uploads/
{token}`` takes the file and starts the same ``source_import`` job a multipart upload starts. These
tests run the real routes, the real token code and the real import job (the ``hf_env`` harness):

- the import starts from the PUT's bytes, and the source records the agent that asked for the URL;
- the URL works once (a second PUT, also after a failed one, is ``upload_url_used``);
- an expired, tampered or foreign token is refused before anything is claimed or read;
- more bytes than the URL allows is ``upload_too_large`` with nothing left in staging, whether the
  request declares its length or streams;
- a request the public proxy forwarded is ``upload_url_lan_only`` and leaves the URL unspent;
- a file whose SHA-256 differs from the one asked for is refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models.upload_url import UploadUrl
from src.services import split_download
from src.services.sources import upload_url
from tests.support.source_fixtures import HfEnv, file_rows, hf_env, source_row

__all__ = ["hf_env"]
MINT = "/api/v1/sources/upload-url"
AGENT = {"X-Dataworks-Agent": "agent:dataworks-mcp"}
ROWS = "\n".join(
    json.dumps({"text": f"an old everyday row number {i}, long enough to keep"}) for i in range(25)
).encode()


def _staged(data_dir: Path) -> list[Path]:
    staging = data_dir / "staging"
    return (
        [p for p in staging.iterdir() if p.name.startswith("upload_")] if staging.exists() else []
    )


async def mint(client: httpx.AsyncClient, body: bytes = ROWS, **extra: Any) -> dict[str, Any]:
    payload = {"filename": "old_rows.jsonl", "split": "old_everyday", "expected_bytes": len(body)}
    payload.update(extra)
    response = await client.post(MINT, json=payload, headers=AGENT)
    assert response.status_code == 201, response.text
    return response.json()


def row(upload_id: str) -> UploadUrl:
    with sync_session_factory()() as db:
        found = db.get(UploadUrl, upload_id)
        assert found is not None
        db.expunge(found)
        return found


async def test_the_import_starts_from_the_uploaded_bytes_as_the_agent(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path, tmp_path: Path
) -> None:
    issued = await mint(client, expected_sha256=hashlib.sha256(ROWS).hexdigest())
    assert issued["method"] == "PUT" and issued["max_bytes"] == len(ROWS)
    assert issued["path"].startswith("/api/v1/uploads/") and "curl -T" in issued["curl"]
    recorded = row(issued["upload_url_id"])
    assert recorded.created_by == "agent:dataworks-mcp" and recorded.used_at is None  # logged
    response = await client.put(issued["path"], content=ROWS)  # no agent header: curl sends none
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    [result] = hf_env.run_imports()
    assert hf_env.job(job_id).status == "completed"
    source = source_row(result["source_id"])
    assert source.kind == "upload" and source.state == "ready"
    assert source.created_by == "agent:dataworks-mcp"  # who asked for the URL, not "operator"
    [file] = file_rows(source.id)
    assert file.split == "old_everyday" and file.rows == 25
    assert file.original_sha256 == hashlib.sha256(ROWS).hexdigest()
    assert file.original_name == "old_rows.jsonl"
    spent = row(issued["upload_url_id"])
    assert spent.used_at is not None and spent.job_id == job_id
    assert _staged(data_dir) == []


async def test_a_url_works_once(client: httpx.AsyncClient, hf_env: HfEnv) -> None:
    issued = await mint(client)
    first = await client.put(issued["path"], content=ROWS)
    assert first.status_code == 202, first.text
    second = await client.put(issued["path"], content=ROWS)
    assert second.status_code == 409, second.text
    assert second.json()["error"]["code"] == "upload_url_used"
    assert len(hf_env.run_imports()) == 1


async def test_a_failed_put_still_spends_the_url(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path
) -> None:
    issued = await mint(client, expected_sha256="0" * 64)
    wrong = await client.put(issued["path"], content=ROWS)
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "upload_hash_mismatch"
    assert _staged(data_dir) == []
    again = await client.put(issued["path"], content=ROWS)
    assert again.status_code == 409 and again.json()["error"]["code"] == "upload_url_used"


async def test_an_expired_url_is_refused_unspent(
    client: httpx.AsyncClient, hf_env: HfEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    issued = await mint(client, expires_in_s=60)
    later = issued["expires_at"] + 1
    monkeypatch.setattr(upload_url, "_now", lambda: float(later))
    response = await client.put(issued["path"], content=ROWS)
    assert response.status_code == 410, response.text
    assert response.json()["error"]["code"] == "upload_url_expired"
    assert row(issued["upload_url_id"]).used_at is None
    assert hf_env.run_imports() == []


def _tampered(path: str) -> str:
    token = path.rsplit("/", 1)[1]
    body, signature = token.split(".", 1)
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    return f"/api/v1/uploads/{body}.{flipped}"


def _widened(path: str) -> str:
    """The same signature over a payload that asks for a bigger cap."""
    token = path.rsplit("/", 1)[1]
    body, signature = token.split(".", 1)
    payload = json.loads(upload_url._unb64(body))
    payload["b"] = payload["b"] * 1000
    forged = upload_url._b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"/api/v1/uploads/{forged}.{signature}"


@pytest.mark.parametrize("forge", ["signature", "payload", "download_token", "garbage"])
async def test_a_wrong_or_tampered_token_is_refused_before_anything_is_claimed(
    client: httpx.AsyncClient, hf_env: HfEnv, forge: str
) -> None:
    issued = await mint(client)
    if forge == "signature":
        path = _tampered(issued["path"])
    elif forge == "payload":
        path = _widened(issued["path"])
    elif forge == "download_token":  # a token signed for downloads never opens an upload
        body = upload_url._b64(
            json.dumps({"b": 10, "e": 2**40, "u": issued["upload_url_id"]}).encode()
        )
        mac = hmac.new(split_download._key(), body.encode(), "sha256").digest()
        path = f"/api/v1/uploads/{body}.{upload_url._b64(mac)}"
    else:
        path = "/api/v1/uploads/not-a-token"
    response = await client.put(path, content=ROWS)
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "upload_token_invalid"
    assert row(issued["upload_url_id"]).used_at is None
    assert hf_env.run_imports() == []


async def test_a_declared_length_over_the_cap_is_refused_unread(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path
) -> None:
    issued = await mint(client, body=ROWS[:100])
    response = await client.put(issued["path"], content=ROWS)
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "upload_too_large"
    assert row(issued["upload_url_id"]).used_at is None  # refused before the claim
    assert _staged(data_dir) == []


async def test_a_streamed_body_over_the_cap_is_cut_off_with_nothing_left(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path
) -> None:
    """No Content-Length (a chunked request): the stream is counted, and refused past the cap."""
    issued = await mint(client, body=ROWS[:100])

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(ROWS), 64):
            yield ROWS[start : start + 64]

    response = await client.put(issued["path"], content=chunks())
    assert response.status_code == 413, response.text
    assert response.json()["error"]["code"] == "upload_too_large"
    assert response.json()["error"]["details"]["limit_bytes"] == 100
    assert _staged(data_dir) == []
    assert hf_env.run_imports() == []


@pytest.mark.parametrize("header", ["cf-ray", "cf-connecting-ip"])
async def test_a_request_through_the_public_proxy_is_refused_unspent(
    client: httpx.AsyncClient, hf_env: HfEnv, header: str
) -> None:
    issued = await mint(client)
    response = await client.put(issued["path"], content=ROWS, headers={header: "x"})
    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "upload_url_lan_only"
    assert row(issued["upload_url_id"]).used_at is None
    lan = await client.put(issued["path"], content=ROWS)  # the same URL still works on the LAN
    assert lan.status_code == 202, lan.text


async def test_minting_refuses_what_the_upload_would_refuse(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import sources

    async def cap(_db: Any) -> int:
        return 1000

    monkeypatch.setattr(sources, "_upload_cap", cap)
    big = await client.post(
        MINT, json={"filename": "a.jsonl", "expected_bytes": 1001}, headers=AGENT
    )
    assert big.status_code == 413 and big.json()["error"]["code"] == "upload_too_large"
    named = await client.post(MINT, json={"filename": "a.exe", "expected_bytes": 10}, headers=AGENT)
    assert named.status_code == 422 and named.json()["error"]["code"] == "upload_format_mismatch"
    with sync_session_factory()() as db:
        names = {r.filename for r in db.query(UploadUrl).all()}
        assert not names & {"a.jsonl", "a.exe"}  # nothing recorded for a refused request


async def test_the_url_is_absolute_on_the_lan_address_when_it_is_set(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    lan = get_settings().model_copy(update={"lan_api_url": "http://lan.example:8000/"})
    monkeypatch.setattr(upload_url, "get_settings", lambda: lan)
    issued = await mint(client)
    assert issued["absolute"] is True
    assert issued["url"] == "http://lan.example:8000" + issued["path"]
    assert issued["curl"].endswith(f"'{issued['url']}'")
