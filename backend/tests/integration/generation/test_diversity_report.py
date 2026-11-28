"""The diversity report (007 FTASKS 9.1 – 9.10, 13.7).

Fixtures that genuinely narrow (every generated completion is one of three near-copies) and that
do not (generated completions drawn like the reference's), so a figure that cannot see narrowing
fails here rather than agreeing with the code by construction.
"""

from __future__ import annotations

import hashlib
import os
import random
import uuid
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


def fixture_id(role: str, generated: list[str], n_source: int) -> str:
    """A version id fixed by the fixture's content.

    The report's seed is ``report_seed(version.id)``: it drives the reservoir sample, every
    bootstrap and both controls. With a random id each run drew a new seed, and on a few seeds in
    a thousand the positive control (two halves of the reference) reads noise as a fall, so the
    report is ``invalid`` (mirror CI on ``a055a38``, 2026-10-19; reproduced with a pinned id, see
    ``0xcc/reviews/live2_2026-10-17_controls.md``). Pinning the id pins the seed, so a run is the
    same run every time. ``DIVERSITY_FIXTURE_CHILD_ID`` overrides the child's id to replay a seed.
    """
    if role == "child" and (forced := os.environ.get("DIVERSITY_FIXTURE_CHILD_ID")):
        return forced
    digest = hashlib.sha256("\n".join(generated).encode()).hexdigest()
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"midataworks-test:diversity:{role}:{n_source}:{digest}")
    )


def lineage(
    generated: list[str],
    *,
    n_source: int = 300,
    generated_prompts: list[str] | None = None,
) -> tuple[str, str]:
    """(reference V, child C = V's rows plus ``generated`` rows), C's parent is V. Both ids are
    fixed by the content (:func:`fixture_id`), so the report's seed is too. The generated rows'
    prompts are ``sentences(len(generated), 9)`` unless ``generated_prompts`` names them."""
    prompts = sentences(n_source, 1)
    reference_table = with_completions(
        version_table(prompts, test=["held one", "held two"]),
        sentences(n_source, 2) + ["held answer one", "held answer two"],
    )
    reference = make_version(
        reference_table, version_id=fixture_id("reference", generated, n_source)
    )
    child_table = version_table(
        prompts,
        test=["held one", "held two"],
        generated=(
            generated_prompts if generated_prompts is not None else sentences(len(generated), 9)
        ),
    )
    child_table = with_completions(
        child_table, sentences(n_source, 2) + ["held answer one", "held answer two"] + generated
    )
    child = make_version(
        child_table,
        parent=reference,
        bindings=[{"kind": "generation_run", "id": "gr_fixture"}],
        version_id=fixture_id("child", generated, n_source),
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
    # method version 2 (decision 2026-10-19), recorded on the report and in its identity
    assert out["method_version"] == 3 and out["method"]["method_version"] == 3
    assert out["method"]["positive_control"]["method"] == "split_half_calibration_v2"
    positive = {c["check_id"]: c for c in out["checks"]}["diversity_positive_control"]
    assert positive["version"] == 2
    assert positive["statistic"]["splits"] == 8 and positive["statistic"]["exceeds"] == []
    assert set(positive["statistic"]["mean_square_z"]) == set(out["figures"])
    # method version 3 (proposed 2026-10-19): the largest share reads a refit on both samples
    assert out["method"]["largest_share"] == {
        "method": "union_distinct_refit_v1",
        "basis": "lexical",
        "k": 8,
    }
    assert out["clustering"]["largest_share"]["method"] == "union_distinct_refit_v1"
    # distinct texts of both samples: 300 reference rows (in both) + 600 generated (prompts differ)
    assert out["clustering"]["largest_share"]["texts"] == 900


def fresh_words(n: int, seed: int) -> list[str]:
    """Rows of the same eight topics in words the reference never uses (``t3n17``, not
    ``t3w17``): new rows that share no word with any of the reference's clusters."""
    rng = random.Random(seed)  # noqa: S311 - a test corpus
    return [" ".join(f"t{i % 8}n{rng.randrange(80)}" for _ in range(14)) for i in range(n)]


async def test_new_rows_unlike_every_reference_cluster_are_not_read_as_concentration(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """Method version 3 (``union_distinct_refit_v1``), wired through ``compute``. The child adds 300 rows
    of the reference's topics in words the reference never uses: lexically MORE diverse, not
    less. Assigned to the reference's clusters (method version 2), a row that shares no word with
    any centroid goes to the centroid with the smallest norm, so all 300 land in one cluster and
    the largest share "falls" (measured with version 2's labels: the version at 0.55 [0.47,
    0.62]). Refitted on both samples, the new rows form their own clusters, and the report
    holds."""
    _, child = lineage(fresh_words(300, 3), generated_prompts=fresh_words(300, 4))
    out = await report(client, child)
    share = out["figures"]["largest_cluster_share"]
    assert share["verdict"] == "holds", share
    assert out["verdict"] == "holds", {k: f["verdict"] for k, f in out["figures"].items()}
    assert out["method"]["largest_share"]["method"] == "union_distinct_refit_v1"


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


# --- round 2 item 1b (production loop, 2026-10-17) ---------------------------------------------


async def test_blank_texts_are_never_sent_and_are_recorded(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """Production: ``400 invalid_parameter: input[0] must not be empty`` failed every report of a
    version holding an empty completion. The fake answers the same 400, so sending one fails here.
    """
    generated = sentences(40, 11)
    generated[0] = ""
    generated[1] = "   "
    _, child = lineage(generated, n_source=60)
    out = await report(client, child)
    assert out["verdict"] in {"holds", "falls", "invalid"}
    assert out["embedding_identity"]["served_model"] == "embed-model"
    assert out["method"]["blank_rows_excluded"] == {"version": 2, "reference": 0}


def test_an_empty_text_reaching_the_embeddings_call_is_refused_unsent(small: Gen) -> None:
    from src.core.errors import AppError

    with sync_session_factory()() as db, pytest.raises(AppError) as exc:
        diversity_service.embed(["a real text", " "], db)
    assert exc.value.code == "EMBEDDINGS_EMPTY_INPUT"
    assert small.fake.embedding_calls == 0


def _fail(job_id: str, code: str) -> None:
    import json

    with sync_session_factory()() as db:
        db.execute(
            text("UPDATE dw_jobs SET status='failed', result=CAST(:r AS jsonb) WHERE id=:id"),
            {"id": job_id, "r": json.dumps({"error": {"code": code}})},
        )
        db.commit()


async def test_a_refused_report_is_never_queued_again(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """Production: 166,442 DIVERSITY_INCOMPARABLE failures, one per version per sweep, for eight
    days. A refusal answers the same every time; the sweep queues it once."""
    _, child = lineage(sentences(20, 6), n_source=20)
    (first,) = diversity_tasks.sweep()
    _fail(first, "DIVERSITY_INCOMPARABLE")
    assert diversity_tasks.sweep() == []
    assert diversity_tasks.sweep() == []
    with sync_session_factory()() as db:
        assert (
            db.execute(
                text("SELECT count(*) FROM dw_jobs WHERE kind='diversity_report'")
            ).scalar_one()
            == 1
        )
    assert child


async def test_a_transient_failure_is_retried_up_to_the_bound(
    client: httpx.AsyncClient, small: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "diversity_sweep_max_attempts", 3)
    lineage(sentences(20, 6), n_source=20)
    created: list[str] = []
    for _ in range(6):
        ids = diversity_tasks.sweep()
        created += ids
        for job_id in ids:
            _fail(job_id, "INTERNAL_ERROR")
    assert len(created) == 3


async def test_the_real_report_job_failure_stops_the_sweep(
    client: httpx.AsyncClient, small: Gen, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end through the worker's own failure recording (not a hand-written row): the sweep
    queues, the job runs and fails with an unexpected error each time, and the sweep stops."""
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "diversity_sweep_max_attempts", 2)

    def boom(*_: Any, **__: Any) -> Any:
        raise RuntimeError("endpoint fell over")

    monkeypatch.setattr(diversity_service, "compute", boom)
    lineage(sentences(20, 6), n_source=20)
    runs = 0
    for _ in range(5):
        for job_id in diversity_tasks.sweep():
            assert diversity_tasks.report_job(job_id)["outcome"] == "failed"
            runs += 1
    assert runs == 2


# --- method version 2 (decision 2026-10-19): version 1 reports stay readable -------------------

V1_METHOD = {
    "column": "completion",
    "splits": ["train"],
    "sample_size": 5000,
    "seed": 1,
    "resamples": 200,
    "tokeniser": "lex-v1",
    "interval": "half_subsample_percentile_v1",
    "clustering": {"basis": "lexical", "k": 8},
    "embedding": None,
    "spread": "mean_cosine_to_centroid_v1",
}
V1_CHECKS = [
    {
        "check_id": "diversity_negative_control",
        "version": 1,
        "result": "pass",
        "statistic": {"distinct_2": True, "largest_cluster_share": True},
        "rule": "r",
        "reason": None,
    },
    {
        "check_id": "diversity_positive_control",
        "version": 1,
        "result": "fail",
        "statistic": {"falls": ["embedding_spread"]},
        "rule": "two halves of the reference must not fall against each other",
        "reason": "noise read as narrowing on ['embedding_spread']",
    },
]


#: A method as version 2 wrote it: the largest share read the reference's clustering.
V2_METHOD = {
    **V1_METHOD,
    "method_version": 2,
    "positive_control": {"method": "split_half_calibration_v2", "splits": 8, "alpha": 0.001},
}
V2_FIGURES = {
    "largest_cluster_share": {
        "version": {"value": 0.247, "lo": 0.216, "hi": 0.278},
        "reference": {"value": 0.133, "lo": 0.116, "hi": 0.153},
        "direction": "lower_is_diverse",
        "verdict": "falls",
        "reason": None,
    }
}


def _write_v1_report(
    version_id: str,
    reference_id: str,
    report_id: str = "dr_v1",
    method: dict[str, Any] | None = None,
    figures: dict[str, Any] | None = None,
) -> str:
    """A report row exactly as method version 1 wrote it (no ``method_version``), or as an older
    method wrote it when ``method``/``figures`` are given."""
    import json

    from src.core.canonical_json import canonical_sha256

    with sync_session_factory()() as db:
        db.execute(
            text(
                'INSERT INTO dw_diversity_reports (id, version_id, reference_version_id, "column", '
                "method_hash, method, splits, sample_size, seed, clustering, figures, checks, "
                "verdict, reason, created_by, created_by_origin, created_at) VALUES (:id, :v, "
                ":r, 'completion', :h, CAST(:m AS jsonb), '[\"train\"]', 300, 1, "
                "'{\"basis\": \"lexical\"}', CAST(:f AS jsonb), CAST(:c AS jsonb), 'invalid', "
                "'A control failed, so the figures cannot be trusted on this data.', 'operator', "
                "'operator', now() - interval '1 day')"
            ),
            {
                "id": report_id,
                "v": version_id,
                "r": reference_id,
                "h": canonical_sha256(method or V1_METHOD),
                "m": json.dumps(method or V1_METHOD),
                "f": json.dumps(figures or {}),
                "c": json.dumps(V1_CHECKS),
            },
        )
        db.commit()
    return report_id


async def test_a_version_1_report_still_reads_and_a_new_report_is_version_3(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """A stored v1 report keeps its method, checks and verdict, and reads as method version 1;
    it is not recomputed by the sweep. Running the report again writes a SEPARATE v3 row (the
    method hash differs), which becomes the newest; the v1 row is still there, unchanged."""
    reference, child = lineage([NARROW[i % 3] for i in range(600)])
    _write_v1_report(child, reference)
    got = await client.get(f"/api/v1/versions/{child}/diversity")
    assert got.status_code == 200, got.text
    old = got.json()
    assert old["id"] == "dr_v1" and old["method_version"] == 1
    assert old["method"] == V1_METHOD and old["checks"] == V1_CHECKS
    assert old["verdict"] == "invalid"
    assert diversity_tasks.sweep() == []  # a v1 report is a report: nothing is re-queued
    with sync_session_factory()() as db:
        from src.services.generation import provenance

        finding = provenance.diversity_finding(child, session=db)
        assert finding is not None and finding["report_id"] == "dr_v1"
        assert finding["verdict"] == "invalid"

    new = await report(client, child)
    assert new["id"] != "dr_v1" and new["method_version"] == 3 and new["verdict"] == "falls"
    with sync_session_factory()() as db:
        rows = (
            db.execute(select(DiversityReport).where(DiversityReport.version_id == child))
            .scalars()
            .all()
        )
        assert sorted(diversity_service.method_version_of(r.method) for r in rows) == [1, 3]
        kept = db.get(DiversityReport, "dr_v1")
        assert kept is not None and kept.method == V1_METHOD and kept.checks == V1_CHECKS


async def test_a_version_2_report_keeps_its_largest_share_and_a_new_report_is_version_3(
    client: httpx.AsyncClient, small: Gen
) -> None:
    """Method version 3 (proposed 2026-10-19) changes how the largest share is measured, so a
    version 2 report keeps its figures, reads as version 2, and is not re-queued; a new run adds
    a separate version 3 row."""
    reference, child = lineage([NARROW[i % 3] for i in range(600)])
    _write_v1_report(child, reference, "dr_v2", V2_METHOD, V2_FIGURES)
    old = (await client.get(f"/api/v1/versions/{child}/diversity")).json()
    assert old["id"] == "dr_v2" and old["method_version"] == 2
    assert old["figures"] == V2_FIGURES and "largest_share" not in old["method"]
    assert diversity_tasks.sweep() == []
    new = await report(client, child)
    assert new["id"] != "dr_v2" and new["method_version"] == 3
    assert new["method"]["largest_share"]["method"] == "union_distinct_refit_v1"
    with sync_session_factory()() as db:
        kept = db.get(DiversityReport, "dr_v2")
        assert kept is not None and kept.method == V2_METHOD and kept.figures == V2_FIGURES
