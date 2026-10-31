"""Column projection and signed split downloads, through the live app (PII run G8, 2026-10-20).

The run paged ``dataworks_get_version_rows`` 41 times, each page carrying full source documents,
to read two columns. Now rows can be projected, and a whole split can be fetched with one GET of a
signed, expiring URL whose signature binds version, split, columns, format and expiry.
"""

from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

import httpx
import pyarrow.parquet as pq
import pytest
from sqlalchemy import text

from src.core.database import get_sync_engine
from src.services import split_download
from tests.integration.test_version_build import request, setup, src
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.stub_operators import body
from tests.support.version_fixtures import HUMOR_TRAIN, make_source

__all__ = ["real_driver"]


@pytest.fixture
async def version_id(
    client: httpx.AsyncClient, real_driver: RealDriver, data_dir: Path, operator_name: str
) -> str:
    source = make_source(data_dir, {"train": HUMOR_TRAIN})
    ds, rev = await setup(client, body(("fx_keep_all", {})))
    job = (await request(client, ds, rev, [src(source)], seed=1)).json()["job_id"]
    assert real_driver.run(job) == "completed"
    with get_sync_engine().connect() as conn:
        return str(conn.execute(text("SELECT id FROM dw_versions")).scalar_one())


async def _split(client: httpx.AsyncClient, version_id: str) -> str:
    version = (await client.get(f"/api/v1/versions/{version_id}")).json()
    return str(version["splits"][0]["name"])


async def test_rows_can_be_projected(client: httpx.AsyncClient, version_id: str) -> None:
    full = (await client.get(f"/api/v1/versions/{version_id}/rows")).json()
    assert "_dw_source_locator" in full["items"][0] and full["projected"] is None
    page = await client.get(f"/api/v1/versions/{version_id}/rows", params={"columns": "text"})
    assert page.status_code == 200, page.text
    data = page.json()
    assert data["projected"] == ["_dw_row_key", "text"]
    assert all(set(r) == {"_dw_row_key", "text"} for r in data["items"])
    assert data["total"] == full["total"]
    missing = await client.get(f"/api/v1/versions/{version_id}/rows", params={"columns": "nope"})
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "column_not_found"


async def test_a_signed_url_downloads_the_projected_split(
    client: httpx.AsyncClient, version_id: str
) -> None:
    split = await _split(client, version_id)
    minted = await client.post(
        f"/api/v1/versions/{version_id}/splits/{split}/download-url",
        json={"columns": ["text"], "format": "parquet"},
    )
    assert minted.status_code == 200, minted.text
    facts = minted.json()
    assert facts["columns"] == ["_dw_row_key", "text"] and facts["absolute"] is False
    got = await client.get(facts["path"])
    assert got.status_code == 200
    table = pq.read_table(io.BytesIO(got.content))
    assert table.column_names == ["_dw_row_key", "text"]
    assert table.num_rows == facts["rows"]

    lines = await client.post(
        f"/api/v1/versions/{version_id}/splits/{split}/download-url", json={"format": "jsonl"}
    )
    stream = await client.get(lines.json()["path"])
    rows = [json.loads(line) for line in stream.text.splitlines()]
    assert len(rows) == facts["rows"] and "_dw_source_locator" in rows[0]


async def test_a_tampered_widened_or_expired_link_is_refused(
    client: httpx.AsyncClient, version_id: str
) -> None:
    split = await _split(client, version_id)
    facts = (
        await client.post(
            f"/api/v1/versions/{version_id}/splits/{split}/download-url",
            json={"columns": ["text"]},
        )
    ).json()
    token = facts["path"].rsplit("/", 1)[1]
    payload_text, signature = token.split(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_text + "=" * (-len(payload_text) % 4)))
    payload["c"] = None  # widen to every column, keep the signature
    widened = (
        base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        .decode()
        .rstrip("=")
    )
    refused = await client.get(f"/api/v1/downloads/{widened}.{signature}")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "download_token_invalid"
    assert (await client.get("/api/v1/downloads/garbage")).status_code == 403
    with pytest.raises(Exception) as exc:
        split_download.verify(token, now=time.time() + 7200)
    assert getattr(exc.value, "code", None) == "download_expired"


async def test_an_unknown_split_or_column_is_refused_before_signing(
    client: httpx.AsyncClient, version_id: str
) -> None:
    url = f"/api/v1/versions/{version_id}/splits/nope/download-url"
    assert (await client.post(url, json={})).json()["error"]["code"] == "split_not_found"
    split = await _split(client, version_id)
    bad = await client.post(
        f"/api/v1/versions/{version_id}/splits/{split}/download-url", json={"columns": ["zz"]}
    )
    assert bad.status_code == 404 and bad.json()["error"]["code"] == "column_not_found"


async def test_a_public_address_makes_the_url_absolute(
    client: httpx.AsyncClient, version_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "public_api_url", "https://dataworks.example/")
    split = await _split(client, version_id)
    facts = (
        await client.post(f"/api/v1/versions/{version_id}/splits/{split}/download-url", json={})
    ).json()
    assert facts["url"] == "https://dataworks.example" + facts["path"] and facts["absolute"]
