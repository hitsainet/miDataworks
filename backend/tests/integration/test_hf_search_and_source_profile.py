"""Dataset discovery and whole-split profiling before a version exists (PII run G2, 2026-10-20).

The run found and compared its PII datasets with curl against the Hub and pandas on downloaded
Parquet. ``POST /sources/hf/search`` runs the Hub search in the worker with the STORED token (the
real task code, the network replaced) and ``POST /sources/{id}/profile`` reads a ready source
whole with DuckDB, refusing a split over the cap with the cap named.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.support.source_fixtures import HfEnv, hf_env
from tests.support.version_fixtures import make_source

__all__ = ["hf_env"]
SEARCH = "/api/v1/sources/hf/search"
STORED = "hf_StoredTokenNeverReturned0123456789abcdef"

HUB_ANSWER = [
    {
        "id": "ai4privacy/pii-masking",
        "downloads": 900,
        "likes": 40,
        "lastModified": "2026-01-01T00:00:00.000Z",
        "gated": False,
        "private": False,
        "tags": [
            "license:apache-2.0",
            "size_categories:100K<n<1M",
            "task_categories:token-classification",
            "language:en",
        ],
    },
    {
        "id": "someone/pii-small",
        "downloads": 12,
        "tags": ["license:mit", "size_categories:1K<n<10K"],
        "gated": "auto",
    },
]


@pytest.fixture
def stored_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.workers import source_tasks

    monkeypatch.setattr(source_tasks, "resolve_hf_token", lambda db: STORED)


async def test_search_uses_the_stored_token_and_never_returns_it(
    client: httpx.AsyncClient, hf_env: HfEnv, stored_token: None
) -> None:
    hf_env.mock.overrides["/api/datasets?"] = (200, HUB_ANSWER)
    response = await client.post(SEARCH, json={"query": "pii", "licences": ["apache-2.0"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [i["repo_id"] for i in body["items"]] == ["ai4privacy/pii-masking", "someone/pii-small"]
    first = body["items"][0]
    assert first["licence"] == "apache-2.0" and first["size_category"] == "100K<n<1M"
    assert first["task_categories"] == ["token-classification"]
    assert body["items"][1]["gated"] == "auto"
    (request,) = [r for r in hf_env.mock.seen if r.url.path == "/api/datasets"]
    assert request.url.params["search"] == "pii"
    assert request.url.params["filter"] == "license:apache-2.0"
    assert request.url.params["limit"] == "20"
    assert request.headers["authorization"] == f"Bearer {STORED}"
    assert STORED not in response.text
    assert [n for n, _ in hf_env.sent] == ["midataworks.sources.search_hf"]


async def test_several_licences_are_one_request_each_merged(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    hf_env.mock.overrides["/api/datasets?"] = (200, HUB_ANSWER)
    body = (
        await client.post(
            SEARCH, json={"query": "pii", "licences": ["mit", "apache-2.0"], "limit": 1}
        )
    ).json()
    filters = [
        r.url.params.get("filter") for r in hf_env.mock.seen if r.url.path == "/api/datasets"
    ]
    assert filters == ["license:mit", "license:apache-2.0"]
    assert [i["repo_id"] for i in body["items"]] == ["ai4privacy/pii-masking"]


async def test_a_hub_refusal_is_the_hub_error(client: httpx.AsyncClient, hf_env: HfEnv) -> None:
    hf_env.mock.overrides["/api/datasets?"] = (503, {"error": "down"})
    response = await client.post(SEARCH, json={"query": "pii"})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "hf_unavailable"


ROWS: list[dict[str, Any]] = [
    {"text": "Call Ann at 555-0101", "has_pii": "yes", "locale": "en"},
    {"text": "The weather is fine", "has_pii": "no", "locale": "en"},
    {"text": "", "has_pii": "no", "locale": "de"},
    {"text": None, "has_pii": "no", "locale": None},
]


async def test_a_source_profile_reads_the_whole_split(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    source = make_source(data_dir, {"train": ROWS, "test": ROWS[:2]})
    response = await client.post(
        f"/api/v1/sources/{source}/profile", json={"value_columns": ["has_pii", "locale"]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    train = body["splits"]["train"]
    assert train["rows"] == 4 and body["splits"]["test"]["rows"] == 2
    assert train["nulls"] == {"text": 1, "has_pii": 0, "locale": 1}
    shares = {v["value"]: v["share"] for v in train["values"]["has_pii"]["top"]}
    assert shares == {"no": 0.75, "yes": 0.25}
    assert train["values"]["locale"]["distinct"] == 2
    lengths = train["lengths"]["text"]  # the detected text column by default
    assert lengths["empty_or_null"] == 2 and lengths["quantiles"]["p100"] == 20.0


async def test_a_split_over_the_cap_is_refused_naming_the_cap(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "source_profile_max_rows", 3)
    source = make_source(data_dir, {"train": ROWS, "test": ROWS[:2]})
    refused = await client.post(f"/api/v1/sources/{source}/profile", json={})
    assert refused.status_code == 413
    error = refused.json()["error"]
    assert error["code"] == "profile_too_large" and error["details"]["cap_rows"] == 3
    assert "3 rows" in error["message"]
    one = await client.post(f"/api/v1/sources/{source}/profile", json={"split": "test"})
    assert one.status_code == 200 and list(one.json()["splits"]) == ["test"]


async def test_an_unknown_column_is_named(client: httpx.AsyncClient, data_dir: Path) -> None:
    source = make_source(data_dir, {"train": ROWS})
    response = await client.post(
        f"/api/v1/sources/{source}/profile", json={"value_columns": ["label"]}
    )
    assert response.status_code == 404 and response.json()["error"]["details"] == {
        "columns": ["label"]
    }
