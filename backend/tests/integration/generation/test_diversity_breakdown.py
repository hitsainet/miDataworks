"""Diversity per generator and per genre (017 FTASKS 10.5; FR-017.47; TD-017.28).

An all-carrier version (every row generated, no ancestor without generated rows): the report
reads "reference: not measured" and its breakdown compares each generator, and each genre, with
the others. One generator repeating one carrier (three near-copies) reads ``falls`` for that
generator only; the genres, which cross the generators, hold. A version WITH a reference carries
its breakdown too. Controls M25 (the breakdown removed, on either path).
"""

from __future__ import annotations

import random
import uuid
from typing import Any

import httpx
import pytest

from src.workers import diversity_tasks
from tests.fixtures import humor_pool as hp
from tests.integration.generation.test_diversity_report import NARROW, lineage, report, sentences
from tests.support.generation_fixtures import Gen, make_version, set_role

REPEATED = [
    "the carrier says the same thing again",
    "the carrier says the same thing",
    "carrier says the same thing again",
]


def all_carrier_version() -> str:
    diverse = sentences(160, 7)
    rng = random.Random(3)  # noqa: S311 - a test corpus
    rows: list[dict[str, Any]] = []
    for i in range(320):
        generator = "gen-a" if i % 2 == 0 else "gen-b"
        # genres in blocks of 8 diverse rows: each genre holds every topic and both generators
        text = diverse[i // 2] if generator == "gen-a" else rng.choice(REPEATED)
        rows.append(
            {"text": f"{text} {i % 2}", "generator": generator, "genre": f"g{(i // 16) % 2}"}
        )
    table = hp.with_system_columns(rows)
    import pyarrow as pa

    table = table.set_column(
        table.schema.get_field_index("_dw_origin"),
        "_dw_origin",
        pa.array(["generated"] * table.num_rows),
    )
    return make_version(
        table,
        # a fixed id fixes the report's seed (``report_seed``): the same run every time
        version_id=str(uuid.uuid5(uuid.NAMESPACE_URL, "midataworks-test:017-diversity-breakdown")),
        held_out=False,
        roles={"text": "content", "generator": "metadata", "genre": "metadata"},
    )


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Gen:
    from src.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "diversity_bootstrap_resamples", 200)
    monkeypatch.setattr(settings, "diversity_cluster_k", 8)
    return gen


async def test_one_generator_repeating_one_carrier_falls_alone(
    client: httpx.AsyncClient, small: Gen
) -> None:
    version = all_carrier_version()
    response = await client.post(
        f"/api/v1/versions/{version}/diversity",
        json={"column": "text", "breakdown_by": ["generator", "genre"]},
    )
    assert response.status_code == 202, response.text
    assert diversity_tasks.report_job(response.json()["job_id"])["outcome"] == "completed"
    report = (await client.get(f"/api/v1/versions/{version}/diversity")).json()
    assert report["verdict"] == "not_measured"
    assert report["reason"].startswith("reference: not measured")
    assert report["method"]["breakdown_by"] == ["generator", "genre"]
    by_generator = report["figures"]["breakdown"]["generator"]
    assert by_generator["gen-b"]["verdict"] == "falls", by_generator["gen-b"]
    assert "distinct_2" in by_generator["gen-b"]["falling"]
    assert by_generator["gen-a"]["verdict"] == "holds", by_generator["gen-a"]
    by_genre = report["figures"]["breakdown"]["genre"]
    assert {v["verdict"] for v in by_genre.values()} == {"holds"}, by_genre
    assert by_generator["gen-a"]["figures"]["embedding_spread"]["verdict"] == "not_measured"


async def test_a_breakdown_column_the_version_lacks_is_refused_before_a_job(
    client: httpx.AsyncClient, small: Gen
) -> None:
    version = all_carrier_version()
    response = await client.post(
        f"/api/v1/versions/{version}/diversity",
        json={"column": "text", "breakdown_by": ["voice"]},
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "breakdown_column_missing"


async def test_a_version_with_a_reference_carries_its_breakdown(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """The reference path (M25 on line 919 survived without this): the generated rows, all
    near-copies, fall against the source rows; the source rows hold."""
    set_role("embeddings", small.base_url, "embed-model", protocol="openai_embeddings")
    _, child = lineage([NARROW[i % 3] for i in range(600)])
    out = await report(client, child, column="completion", breakdown_by=["_dw_origin"])
    assert out["reference_version_id"] is not None
    assert out["method"]["breakdown_by"] == ["_dw_origin"]
    by_origin = out["figures"]["breakdown"]["_dw_origin"]
    assert set(by_origin) == {"generated", "source"}, by_origin
    assert by_origin["generated"]["verdict"] == "falls", by_origin["generated"]
    assert by_origin["source"]["verdict"] == "holds", by_origin["source"]
