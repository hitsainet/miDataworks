"""The diversity report never makes miLLM load or swap a model (017 FTASKS 17.8 (d); ADR-012).

On 2026-10-30 03:36 UTC the diversity sweep asked miLLM for embeddings with no
``X-miLLM-Load-Policy`` header; miLLM's embeddings route loads the model a request names, so it
unloaded the pilot's Llama generator and loaded Qwen with no approval. The older diversity tests
stayed green over it because the fake served embeddings without the residency gate real miLLM
applies; here the fake gates them (``embeddings_gated``) exactly as chat is gated.

Every embeddings request the report makes is asserted by header, model and count, and a model
that is not loaded is refused with nothing loaded, failing the report by name.
"""

from __future__ import annotations

import math

import httpx
import pytest

from src.core.database import sync_session_factory
from src.models.job import Job
from src.services.generation import diversity_service
from src.workers import diversity_tasks
from tests.integration.generation.test_diversity_report import lineage, report, sentences
from tests.support.generation_fixtures import GEN_MODEL, Gen, set_role

EMBED = "/v1/embeddings"


@pytest.fixture
def gated(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Gen:
    from src.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "diversity_bootstrap_resamples", 200)
    monkeypatch.setattr(settings, "diversity_cluster_k", 8)
    gen.fake.embeddings_gated = True
    return gen


async def test_every_embeddings_request_refuses_a_load(
    client: httpx.AsyncClient, gated: Gen
) -> None:
    """The embeddings role names the resident model (as the operator pinned it, 2026-10-30): the
    report completes, and EVERY embeddings request carried ``refuse`` for that model."""
    set_role("embeddings", gated.base_url, GEN_MODEL, protocol="openai_embeddings")
    reference, child = lineage(sentences(60, 6), n_source=60)
    out = await report(client, child)
    assert out["reference_version_id"] == reference
    calls = gated.fake.calls(EMBED, "POST")
    texts = sum(len(c.body["input"]) for c in calls)
    assert calls and len(calls) == math.ceil(texts / diversity_service.EMBED_BATCH)
    assert len(calls) == gated.fake.embedding_calls  # every one was answered, none refused
    for call in calls:
        assert call.headers.get("x-millm-load-policy") == "refuse", call.headers
        assert call.body["model"] == GEN_MODEL


async def test_a_model_that_is_not_loaded_fails_the_report_and_loads_nothing(
    client: httpx.AsyncClient, gated: Gen
) -> None:
    """The role names a model miLLM does not hold: miLLM answers 409 ``model_not_resident``
    (it would have LOADED it without the header: the fake's 500 ``would_load``). The report
    fails naming both models; the resident model is unchanged; the sweep retries it as a state
    that can change, not as a refusal."""
    set_role("embeddings", gated.base_url, "Qwen2.5-7B", protocol="openai_embeddings")
    _, child = lineage(sentences(20, 6), n_source=20)
    (job_id,) = diversity_tasks.sweep()
    assert diversity_tasks.report_job(job_id)["outcome"] == "failed"
    with sync_session_factory()() as db:
        job = db.get(Job, job_id)
        assert job is not None and job.result is not None
        error = job.result["error"]
    assert error["code"] == "MODEL_NOT_LOADED", error
    assert error["details"]["requested"] == "Qwen2.5-7B"
    assert "Qwen2.5-7B" in str(job.error) and "never asks the server to load" in str(job.error)
    calls = gated.fake.calls(EMBED, "POST")
    assert len(calls) == 1 and calls[0].headers.get("x-millm-load-policy") == "refuse"
    assert gated.fake.embedding_calls == 0
    assert gated.fake.resident is not None and gated.fake.resident["name"] == GEN_MODEL
    # Counted as retried (up to the bound), never as a refusal: the operator can load it.
    with sync_session_factory()() as db:
        assert diversity_service.failed_attempts(db)[(child, "completion")] == (0, 1)
    assert len(diversity_tasks.sweep()) == 1
