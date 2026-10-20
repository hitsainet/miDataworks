"""One import request makes one job (novice re-walk 2026-10-19, finding 5).

One click on "Import the previewed dataset" posted twice and made two ``source_import`` jobs 0.2 ms
apart. The same request while its import is still queued or running now answers with THAT job
(``existing_job``), whether the repeats arrive one after another or at the same moment; a
different request, or the same one after its job finished, still makes its own job.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from sqlalchemy import select

from src.core.database import sync_session_factory
from src.models.job import Job
from tests.support.hf_mock import COLBERT
from tests.support.source_fixtures import HfEnv, hf_env

__all__ = ["hf_env"]
URL = "/api/v1/sources/hf"


def import_jobs() -> list[Job]:
    with sync_session_factory()() as db:
        return list(db.execute(select(Job).where(Job.kind == "source_import")).scalars())


def dispatched(hf_env: HfEnv) -> list[Any]:
    return [args for name, args in hf_env.sent if name != "midataworks.sources.preview_hf"]


async def test_a_repeat_while_the_import_is_queued_answers_with_the_same_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = await client.post(URL, json={"repo_id": COLBERT})
    second = await client.post(URL, json={"repo_id": COLBERT})
    assert first.status_code == second.status_code == 202, second.text
    assert first.json()["existing_job"] is False
    assert second.json() == {
        "job_id": first.json()["job_id"],
        "source_id": None,
        "existing_job": True,
    }
    assert [j.id for j in import_jobs()] == [first.json()["job_id"]]


async def test_two_requests_at_the_same_moment_make_one_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    answers = await asyncio.gather(
        client.post(URL, json={"repo_id": COLBERT}), client.post(URL, json={"repo_id": COLBERT})
    )
    ids = {a.json()["job_id"] for a in answers}
    assert len(ids) == 1, [a.json() for a in answers]
    assert sorted(a.json()["existing_job"] for a in answers) == [False, True]
    assert len(import_jobs()) == 1


async def test_a_different_split_is_a_different_request(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = await client.post(URL, json={"repo_id": COLBERT})
    other = await client.post(URL, json={"repo_id": COLBERT, "split": "train"})
    assert other.json()["existing_job"] is False
    assert other.json()["job_id"] != first.json()["job_id"]
    assert len(import_jobs()) == 2


async def test_after_the_job_finishes_a_repeat_makes_a_new_job(
    client: httpx.AsyncClient, hf_env: HfEnv, operator_name: str
) -> None:
    first = await client.post(URL, json={"repo_id": COLBERT})
    hf_env.run_imports()
    again = await client.post(URL, json={"repo_id": COLBERT})
    assert again.json()["existing_job"] is False
    assert again.json()["job_id"] != first.json()["job_id"]
