"""The diversity report (007 FTASKS 9.1 – 9.10, 13.7).

Fixtures that genuinely narrow (every generated completion is one of three near-copies) and that
do not (generated completions drawn like the reference's), so a figure that cannot see narrowing
fails here rather than agreeing with the code by construction.
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import pyarrow as pa
import pytest
from sqlalchemy import select, text

from src.core.database import sync_session_factory
from src.models.generation import DiversityReport
from src.models.job import Job
from src.services.generation import diversity_service
from src.workers import diversity_tasks
from tests.support.generation_fixtures import Gen, make_version, set_role, version_table

TOPICS = [[f"t{t}w{i}" for i in range(80)] for t in range(8)]


def sentences(n: int, seed: int) -> list[str]:
    """Text with topic structure (8 topics, own vocabularies), as real corpora have: each row is
    14 words of one topic, topics in turn."""
    rng = random.Random(seed)  # noqa: S311 - a test corpus
    return [" ".join(rng.choice(TOPICS[i % 8]) for _ in range(14)) for i in range(n)]


def like_reference(n: int, seed: int) -> list[str]:
    """Non-narrowing generated text: NEW rows from the same topics, in the same proportions."""
    return sentences(n, seed)


def with_completions(table: pa.Table, completions: list[str]) -> pa.Table:
    index = table.schema.get_field_index("completion")
    return table.set_column(index, "completion", pa.array(completions, pa.string()))


def lineage(generated: list[str], *, n_source: int = 300) -> tuple[str, str]:
    """(reference V, child C = V's rows plus ``generated`` rows), C's parent is V."""
    prompts = sentences(n_source, 1)
    reference_table = with_completions(
        version_table(prompts, test=["held one", "held two"]),
        sentences(n_source, 2) + ["held answer one", "held answer two"],
    )
    reference = make_version(reference_table)
    child_table = version_table(
        prompts, test=["held one", "held two"], generated=sentences(len(generated), 9)
    )
    child_table = with_completions(
        child_table, sentences(n_source, 2) + ["held answer one", "held answer two"] + generated
    )
    child = make_version(
        child_table, parent=reference, bindings=[{"kind": "generation_run", "id": "gr_fixture"}]
    )
    return reference, child


@pytest.fixture
def small(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Gen:
    from src.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "diversity_bootstrap_resamples", 200)
    monkeypatch.setattr(settings, "diversity_cluster_k", 8)
    set_role("embeddings", gen.base_url, "embed-model", protocol="openai_embeddings")
    return gen


async def report(client: httpx.AsyncClient, version: str, **body: Any) -> dict[str, Any]:
    response = await client.post(f"/api/v1/versions/{version}/diversity", json=body)
    assert response.status_code == 202, response.text
    outcome = diversity_tasks.report_job(response.json()["job_id"])
    assert outcome["outcome"] == "completed", outcome
    got = await client.get(f"/api/v1/versions/{version}/diversity")
    assert got.status_code == 200, got.text
    return dict(got.json())


NARROW = [
    "sure thing here is the same answer again",
    "sure thing here is the same answer",
    "sure here is the same answer again",
]


async def test_a_narrowing_version_falls(client: httpx.AsyncClient, small: Gen) -> None:
    reference, child = lineage([NARROW[i % 3] for i in range(600)])
    out = await report(client, child)
    assert out["reference_version_id"] == reference and out["column"] == "completion"
    assert out["verdict"] == "falls", [c["statistic"] for c in out["checks"]]
    assert out["figures"]["distinct_2"]["verdict"] == "falls"
    # the completions narrowed; the prompts (most of the clustered content) did not
    assert out["figures"]["embedding_spread"]["verdict"] == "falls"
    assert {c["check_id"]: c["result"] for c in out["checks"]} == {
        "diversity_negative_control": "pass",
        "diversity_positive_control": "pass",
    }
    assert out["embedding_identity"]["served_model"] == "embed-model"
    assert out["clustering"]["basis"] == "lexical" and out["splits"] == ["train"]
    assert out["reason"].startswith("A figure's whole interval fell")


async def test_a_non_narrowing_version_holds(client: httpx.AsyncClient, small: Gen) -> None:
    _, child = lineage(like_reference(300, 3))
    out = await report(client, child)
    assert out["verdict"] == "holds", out["figures"]


async def test_without_embeddings_spread_is_not_measured_and_clusters_are_lexical(
    client: httpx.AsyncClient, small: Gen
) -> None:
    # an embeddings row set to its own endpoint with no URL (inheriting would reach the judge)
    with sync_session_factory()() as db:
        db.execute(
            text(
                "UPDATE dw_endpoint_roles SET base_url = NULL, inherit_from_judge = false "
                "WHERE role = 'embeddings'"
            )
        )
        db.commit()
    _, child = lineage(like_reference(300, 4))
    out = await report(client, child)
    assert out["figures"]["embedding_spread"]["verdict"] == "not_measured", {
        k: (f["verdict"], f["version"], f["reference"]) for k, f in out["figures"].items()
    }
    assert out["embedding_identity"] is None and out["clustering"]["basis"] == "lexical"
    assert out["verdict"] == "not_measured", {k: f["verdict"] for k, f in out["figures"].items()}
    assert small.fake.embedding_calls == 0


async def test_mismatched_embedding_models_are_refused(
    client: httpx.AsyncClient, small: Gen
) -> None:
    def swap(*_: Any) -> None:
        small.fake.embedding_model = "another-embedder"

    _, child = lineage([NARROW[i % 3] for i in range(40)])
    original = small.fake._embeddings

    def counting(body: dict[str, Any]) -> Any:
        if small.fake.embedding_calls >= 1:
            swap()
        return original(body)

    small.fake._embeddings = counting  # type: ignore[method-assign]
    response = await client.post(f"/api/v1/versions/{child}/diversity", json={})
    assert response.status_code == 202
    outcome = diversity_tasks.report_job(response.json()["job_id"])
    assert outcome["outcome"] == "failed"
    with sync_session_factory()() as db:
        job = db.get(Job, response.json()["job_id"])
        assert job is not None and job.result["error"]["code"] == "DIVERSITY_INCOMPARABLE"
        assert db.execute(select(DiversityReport)).first() is None


async def test_a_column_missing_from_the_reference_is_incomparable(
    client: httpx.AsyncClient, small: Gen
) -> None:
    reference = make_version(version_table(sentences(10, 5)))
    table = version_table(sentences(10, 5), generated=["g"]).append_column(
        "note", pa.array(["x"] * 13, pa.string())
    )
    child = make_version(table, parent=reference)
    response = await client.post(f"/api/v1/versions/{child}/diversity", json={"column": "note"})
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "DIVERSITY_INCOMPARABLE"
    )
    with sync_session_factory()() as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM dw_jobs WHERE kind='diversity_report'")
            ).scalar_one()
            == 0
        )


async def test_a_failing_control_makes_the_report_invalid(
    client: httpx.AsyncClient, small: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The collapse no longer collapses: the negative control fails and the verdict is invalid."""
    import numpy as np

    from src.services.generation import diversity_metrics

    monkeypatch.setattr(diversity_metrics, "collapse_sample", lambda n, a, rng: np.arange(n))
    _, child = lineage([NARROW[i % 3] for i in range(600)])
    out = await report(client, child)
    assert out["verdict"] == "invalid"
    assert {c["check_id"]: c["result"] for c in out["checks"]}[
        "diversity_negative_control"
    ] == "fail"


async def test_the_sweep_queues_a_missing_report_once(
    client: httpx.AsyncClient, small: Gen
) -> None:
    _, child = lineage(sentences(20, 6), n_source=20)
    first = diversity_tasks.sweep()
    second = diversity_tasks.sweep()
    assert len(first) == 1 and second == []
    with sync_session_factory()() as db:
        job = db.get(Job, first[0])
        assert job is not None and job.params == {"version_id": child, "column": "completion"}


async def test_a_falling_verdict_refuses_nothing_and_008_notes_it(
    client: httpx.AsyncClient, small: Gen
) -> None:
    from src.services.publishing import check_inputs, checks

    _, child = lineage([NARROW[i % 3] for i in range(600)])
    await report(client, child)
    with sync_session_factory()() as db:
        from src.models.version import Version

        version = db.get(Version, child)
        assert version is not None
        finding = diversity_service.latest(db, child, None)
        assert finding is not None and finding.verdict == "falls"
        from src.services.publishing import feature_seams

        seam = feature_seams.diversity_for(child, session=db)
    assert seam.verdict == "falls" and seam.detail["falling"]
    base = checks.CheckInputs(
        visibility="public",
        sources=[],
        token_scope="write",
        held_out_splits=["test"],
        leakage=feature_seams.LeakageFinding("checked", 0),
        models=feature_seams.LabelerFinding("checked", [], []),
        model_terms=[],
        audit=feature_seams.AuditFinding("checked", "complete"),
        labelers=[],
        labeler_status="checked",
        warnings=feature_seams.WarningFinding("checked"),
        diversity=seam,
    )
    outcomes = checks.evaluate_checks(base)
    notes = [o for o in outcomes if o.check == "N-diversity_falling"]
    assert len(notes) == 1 and notes[0].outcome is checks.Outcome.NOTE
    assert not [o for o in outcomes if o.outcome is checks.Outcome.REFUSED]
    from src.services.publishing.card import caveats_from_outcomes

    assert [c["code"] for c in caveats_from_outcomes(outcomes)] == ["diversity_falling"]
    del check_inputs


def test_the_006_registry_holds_the_four_gate3_metrics() -> None:
    from src.services.calibration import registry
    from src.services.generation import metric_registration

    metric_registration.register()
    gate3 = {s.metric_id: s for s in registry.gate_metrics("gate3")}
    for metric in ("distinct_1", "distinct_2", "embedding_spread", "cluster_coverage"):
        assert gate3[metric].checks == (
            "diversity_negative_control",
            "diversity_positive_control",
        )
