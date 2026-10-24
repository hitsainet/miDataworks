"""``POST /api/v1/sources/hf/preview`` end to end with recorded HF answers (001 FTASKS 6.3–6.6).

Route → ``preview_hf`` (run in-process with the real task code) → HF client over the recorded
bodies. A preview creates no ``dw_*`` row and no file; a viewer failure still returns the Hub's
facts plus an ``unavailable`` entry; a pin that differs from the branch head says so.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from src.core.database import Base, get_sync_engine
from tests.support.hf_mock import COLBERT, COLBERT_SHA, HEAD_SHA, HUMICROEDIT, fixture
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]
URL = "/api/v1/sources/hf/preview"


def _row_counts() -> dict[str, int]:
    with get_sync_engine().connect() as conn:
        return {
            t.name: int(conn.execute(text(f"SELECT count(*) FROM {t.name}")).scalar_one())
            for t in Base.metadata.sorted_tables
        }


def _files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


async def test_a_full_preview_from_the_recorded_answers(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    response = await client.post(URL, json={"repo_id": COLBERT})
    assert response.status_code == 200, response.text
    preview: dict[str, Any] = response.json()
    assert preview["resolved_commit"] == COLBERT_SHA
    assert preview["viewer_commit_note"] is None
    assert preview["configs"] == ["default"] and preview["config"] == "default"
    assert preview["splits"] == [{"name": "train", "rows": 200_000, "bytes": 10_920_997}]
    assert [c["name"] for c in preview["columns"]] == ["text", "humor"]
    assert 0 < len(preview["sample_rows"]) <= 100
    assert preview["licence"] == {"raw": "cc-by-2.0", "display": "cc-by-2.0", "origin": "card_data"}
    assert preview["gated"] == "false"
    assert preview["size"]["num_rows"] == 200_000
    assert preview["detection"]["suggested_target"] == "detector"
    assert preview["unavailable"] == []


async def test_a_preview_creates_no_row_and_no_file(
    client: httpx.AsyncClient, hf_env: HfEnv, data_dir: Path
) -> None:
    before_rows, before_files = _row_counts(), _files(data_dir)
    assert (await client.post(URL, json={"repo_id": COLBERT})).status_code == 200
    assert _row_counts() == before_rows
    assert _files(data_dir) == before_files


async def test_a_viewer_failure_returns_the_hub_facts_and_an_unavailable_entry(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    hf_env.mock.viewer_down = True
    response = await client.post(URL, json={"repo_id": COLBERT})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["resolved_commit"] == COLBERT_SHA
    assert preview["licence"]["display"] == "cc-by-2.0"
    parts = {u["part"] for u in preview["unavailable"]}
    assert "splits" in parts and all(u["reason"] for u in preview["unavailable"])
    assert preview["sample_rows"] == [] and preview["detection"] is None


async def test_an_older_pinned_revision_is_labelled_against_the_branch_head(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    hf_env.mock.head = HEAD_SHA
    response = await client.post(URL, json={"repo_id": COLBERT, "revision": "2bb7d6bc"})
    preview = response.json()
    assert preview["resolved_commit"] == COLBERT_SHA and preview["head_commit"] == HEAD_SHA
    assert "branch head" in preview["viewer_commit_note"]


async def test_several_configs_and_none_chosen_is_refused_listing_them(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    response = await client.post(URL, json={"repo_id": HUMICROEDIT})
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "config_required"
    assert error["details"]["configs"] == ["subtask-1", "subtask-2"]


async def test_a_chosen_config_lists_only_its_splits(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    response = await client.post(URL, json={"repo_id": HUMICROEDIT, "config": "subtask-1"})
    assert response.status_code == 200, response.text
    assert [s["name"] for s in response.json()["splits"]] == ["train", "validation", "test"]


async def test_a_split_not_in_the_config_is_refused_listing_splits(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    response = await client.post(URL, json={"repo_id": COLBERT, "split": "validation"})
    assert response.status_code == 409
    assert response.json()["error"]["details"]["splits"] == ["train"]


async def test_an_unresolvable_revision_is_404(client: httpx.AsyncClient, hf_env: HfEnv) -> None:
    response = await client.post(URL, json={"repo_id": COLBERT, "revision": "no-such-ref"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "revision_unresolved"


async def test_a_long_cell_is_truncated_with_its_length(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    body = fixture("colbert_first_rows.json")
    body["rows"][0]["row"]["text"] = "x" * 5000
    hf_env.mock.overrides["/first-rows"] = (200, body)
    preview = (await client.post(URL, json={"repo_id": COLBERT})).json()
    cell = preview["sample_rows"][0]["text"]
    assert cell == {"truncated": True, "text": "x" * 2000, "length": 5000}


async def test_an_operator_token_reaches_hf_and_never_the_response(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    token = "hf_operator_preview_token_1"
    response = await client.post(URL, json={"repo_id": COLBERT, "access_token": token})
    assert response.status_code == 200
    assert f"Bearer {token}" in hf_env.mock.auth_headers
    assert token not in response.text
    name, args = hf_env.sent[-1]
    assert token not in repr(args), "the token never travels as a Celery argument"


async def test_a_blank_token_is_no_token(client: httpx.AsyncClient, hf_env: HfEnv) -> None:
    response = await client.post(URL, json={"repo_id": COLBERT, "access_token": "  none "})
    assert response.status_code == 200
    assert set(hf_env.mock.auth_headers) == {None}


async def test_a_timeout_is_504(client: httpx.AsyncClient, hf_env: HfEnv) -> None:
    hf_env.preview_timeout = True
    response = await client.post(URL, json={"repo_id": COLBERT})
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "preview_timeout"


@pytest.mark.parametrize(
    "repo_id", ["no-slash", "a/b/c", "/lead", "-x/y", "a" * 50 + "/" + "b" * 50]
)
async def test_a_malformed_repository_id_is_refused_before_any_network_call(
    client: httpx.AsyncClient, hf_env: HfEnv, repo_id: str
) -> None:
    response = await client.post(URL, json={"repo_id": repo_id})
    assert response.status_code == 422
    assert hf_env.sent == [] and hf_env.mock.seen == []


async def test_the_sample_is_capped_at_100_rows_whatever_the_viewer_returns(
    client: httpx.AsyncClient, hf_env: HfEnv
) -> None:
    """FR-001.15: at most 100 sample rows. The recorded fixture holds exactly 100, so it cannot
    show the cap; the viewer here answers 150 (control U-rows)."""
    body = fixture("colbert_first_rows.json")
    body["rows"] = body["rows"] + body["rows"][:50]
    assert len(body["rows"]) == 150
    hf_env.mock.overrides["/first-rows"] = (200, body)
    preview = (await client.post(URL, json={"repo_id": COLBERT})).json()
    assert len(preview["sample_rows"]) == 100
