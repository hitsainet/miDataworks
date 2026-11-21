"""A whole label run's scores as one file (ENH-006 D116 G3): 60 calls of 500 labels became one link.

Through MCP over the real backend. The fixture's rationale and raw output hold sentinels, so a
download that carried either column would show them; its rows are not in key order on the way in, and
the outcomes and probabilities disagree (a labeler's outcome is not its probability's threshold).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.label import Label
from tests.support.calibration_fixtures import make_run, make_version, row_key
from tests.support.mcp_live import call, live_mcp

SENTINEL_RATIONALE = "RATIONALE-QUOTES-THE-ROW"
SENTINEL_RAW = "RAW-MODEL-OUTPUT"


def _scored_run(data_dir: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    rows = [{"text": f"row {i}"} for i in range(7)]
    version = make_version(data_dir, rows)
    scores: dict[str, float | None] = {row_key(r["text"]): 0.1 + i / 10 for i, r in enumerate(rows)}
    keys = list(scores)
    scores[keys[3]] = None
    outcomes = {keys[0]: "positive", keys[1]: "parse_failure"}  # against their low probabilities
    run = make_run(version, scores, outcomes=outcomes)
    with sync_session_factory()() as db:
        for label in db.execute(select(Label).where(Label.label_run_id == run.id)).scalars():
            label.rationale = SENTINEL_RATIONALE
            label.raw_output = {"text": SENTINEL_RAW}
            label.parsed_value = {"answer": label.row_key[:6]}
        db.commit()
    expected = {
        key: {
            "row_key": key,
            "outcome": outcomes.get(key, "skipped" if p is None else None),
            "parsed_value": {"answer": key[:6]},
            "probability": p,
        }
        for key, p in scores.items()
    }
    for key, row in expected.items():
        if row["outcome"] is None:
            p = scores[key]
            assert p is not None
            row["outcome"] = "positive" if p >= 0.8 else "negative" if p <= 0.2 else "excluded"
    return run.id, expected


async def test_one_link_gives_every_score_and_no_rationale_or_raw_output(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    run_id, expected = _scored_run(data_dir)
    mcp, mcp_client = live_mcp()
    try:
        link = await call(
            mcp, "dataworks_get_result_download_url", {"kind": "labels", "id": run_id}
        )
    finally:
        await mcp_client.close()
    assert link["kind"] == "labels" and link["format"] == "jsonl" and link["rows"] == 7
    assert link["options"] == {}
    got = await client.get(link["path"])
    assert got.status_code == 200, got.text
    assert SENTINEL_RATIONALE not in got.text and SENTINEL_RAW not in got.text
    rows = [json.loads(line) for line in got.text.splitlines() if line]
    assert rows == [expected[key] for key in sorted(expected)], "every label, by row key"
    assert {frozenset(r) for r in rows} == {
        frozenset({"row_key", "outcome", "parsed_value", "probability"})
    }


async def test_the_page_and_the_download_are_the_same_rows(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    run_id, expected = _scored_run(data_dir)
    mcp, mcp_client = live_mcp()
    rows: list[dict[str, Any]] = []
    token: str | None = None
    pages = 0
    try:
        while True:
            page = await call(
                mcp,
                "dataworks_get_result_page",
                {"kind": "labels", "id": run_id, "limit": 3, "page_token": token},
            )
            pages += 1
            assert page["order"] == "row key"
            rows.extend(page["rows"])
            token = page["next_page_token"]
            if token is None:
                break
    finally:
        await mcp_client.close()
    assert pages == 3
    assert page["next"]["tool"] == "dataworks_get_result_download_url"
    assert page["next"]["args"] == {"kind": "labels", "id": run_id}
    assert rows == [expected[key] for key in sorted(expected)]


async def test_options_cannot_shape_it_and_a_token_cannot_reach_another_run(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    run_id, expected = _scored_run(data_dir)
    other_version = make_version(data_dir, [{"text": "someone else"}])
    other = make_run(other_version, {row_key("someone else"): 0.9})
    link = (
        await client.post(
            f"/api/v1/results/labels/{run_id}/download-url",
            json={"column": "ignored", "by": ["x"]},
        )
    ).json()
    assert link["options"] == {}
    got = await client.get(link["path"])
    assert len(got.text.splitlines()) == len(expected)
    assert row_key("someone else") not in got.text
    other_link = (
        await client.post(f"/api/v1/results/labels/{other.id}/download-url", json={})
    ).json()
    other_rows = (await client.get(other_link["path"])).text.splitlines()
    assert [json.loads(r)["row_key"] for r in other_rows] == [row_key("someone else")]
    # the body holds the run; a token with its signature moved to another body is refused
    body, signature = link["path"].rsplit("/", 1)[1].split(".")
    forged = other_link["path"].rsplit("/", 1)[1].split(".")[0] + "." + signature
    assert (await client.get(f"/api/v1/downloads/{forged}")).status_code == 403


async def test_an_unknown_run_is_not_found(
    client: httpx.AsyncClient, data_dir: Path, clean_db: None
) -> None:
    got = await client.post("/api/v1/results/labels/lr_missing/download-url", json={})
    assert got.status_code == 404
    assert got.json()["error"]["code"] == "LABEL_RUN_NOT_FOUND"
