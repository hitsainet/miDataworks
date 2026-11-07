"""Generated rows enter versions only through 002 builds that BIND a run (007 FTASKS 7.1 – 7.9).

The builds run through 002's real orchestrator and 003's real executor with the PRODUCTION operator
registry (``RealDriver``), so an operator that is not registered cannot be built here either.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.database import sync_session_factory
from src.core.storage import resolve_under_data_dir
from src.models.version import Version
from tests.support.generation_fixtures import (
    GEN_MODEL,
    SAE,
    Gen,
    make_version,
    respond_template,
    run_body,
    version_table,
)
from tests.support.operator_build_driver import RealDriver

RUNS = "/api/v1/generation-runs"


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch, gen: Gen) -> Iterator[RealDriver]:
    from src.core.celery_app import celery_app
    from src.operators import executor
    from src.operators import registry as registry_module
    from src.operators.registry import OperatorRegistry
    from src.services import operator_port
    from src.workers import version_build_tasks

    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)
    drv = RealDriver(reg)
    monkeypatch.setattr(executor, "send_task", drv.record_step)
    monkeypatch.setattr(celery_app, "send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "_send_task", drv.send_task)
    monkeypatch.setattr(version_build_tasks, "emit", drv.emit)
    yield drv


def rows_of(version_id: str) -> list[dict[str, Any]]:
    with sync_session_factory()() as db:
        version = db.get(Version, version_id)
        assert version is not None
        splits = list(version.splits)
    out: list[dict[str, Any]] = []
    for split in splits:
        out.extend(pq.read_table(resolve_under_data_dir(split["path"])).to_pylist())
    return out


async def generate(client: httpx.AsyncClient, gen: Gen, version: str, **kw: Any) -> str:
    template = await respond_template(client)
    response = await client.post(RUNS, json=run_body(version, template, **kw))
    assert response.status_code == 202, response.text
    run_id = str(response.json()["id"])
    assert gen.run_until_done(run_id).state == "completed"
    return run_id


async def build_candidate(client: httpx.AsyncClient, driver: RealDriver, run_id: str) -> str:
    response = await client.post(f"{RUNS}/{run_id}/candidate-build", json={"seed": 3})
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    assert driver.run(job_id) == "completed"
    with sync_session_factory()() as db:
        from src.models.version import VersionBuild

        build = db.get(VersionBuild, job_id)
        assert build is not None and build.version_id is not None
        return str(build.version_id)


async def test_candidate_build_adds_every_generated_row_with_lineage(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver
) -> None:
    source = make_version(version_table(["q one", "q two", "q three"]))
    gen.fake.header_mode = lambda body: (
        "missing" if "two" in body["messages"][-1]["content"] else "real"
    )
    run_id = await generate(client, gen, source, sample_size=3, n_responses=2)
    chats_before = len(gen.chats())
    candidate = await build_candidate(client, driver, run_id)
    assert len(gen.chats()) == chats_before, "a build never calls an endpoint"
    rows = rows_of(candidate)
    generated = [r for r in rows if r["_dw_origin"] == "generated"]
    assert len(generated) == 4, "two prompts x two responses; the unreported prompt adds nothing"
    assert all(r["_dw_split"] == "train" for r in generated)
    assert all(
        r["generation_run_id"] == run_id and r["generation_model_id"] == GEN_MODEL
        for r in generated
    )
    assert all(r["completion"].startswith("Reply to [") for r in generated)
    assert all(len(r["_dw_parent_keys"]) == 1 for r in generated), "prompt key == seed key"
    sources = [r for r in rows if r["_dw_origin"] == "source"]
    assert len(sources) == 5 and not any(
        r["_dw_split"] == "test" and r["_dw_origin"] == "generated" for r in rows
    )
    with sync_session_factory()() as db:
        version = db.get(Version, candidate)
        assert version is not None
        assert version.bindings == [{"kind": "generation_run", "id": run_id}]
    # 008's TRL validator accepts the shapes (through 004's api, as 008 calls it)
    from src.services.curation import api as curation_api

    with sync_session_factory()() as db:
        result = curation_api.validate_trl(candidate, "sft", session=db)
    assert result.valid, result.failures


async def test_reads_only_committed_chunks_of_a_bound_run(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver
) -> None:
    from src.services.generation import record_store

    source = make_version(version_table(["q one", "q two"]))
    run_id = await generate(client, gen, source, sample_size=2)
    # a renamed-but-uncommitted chunk appears after completion: it must never be read
    stray = record_store.chunk_path(run_id, "respond", 99)
    with sync_session_factory()() as db:
        good = record_store.committed_chunk_paths(db, run_id, "respond")[0]
    table = pq.read_table(good)
    pq.write_table(
        table.set_column(
            table.schema.get_field_index("text"), "text", pa.array(["STRAY"] * table.num_rows)
        ),
        stray,
    )
    candidate = await build_candidate(client, driver, run_id)
    texts = [r["completion"] for r in rows_of(candidate) if r["_dw_origin"] == "generated"]
    assert len(texts) == 2 and "STRAY" not in texts


async def test_an_unbound_run_is_refused_by_the_operator(
    gen: Gen, client: httpx.AsyncClient
) -> None:
    from src.operators.context import RunContext
    from src.operators.errors import OperatorError
    from src.operators.native.generation import GeneratedRows

    source = make_version(version_table(["q one"]))
    run_id = await generate(client, gen, source, sample_size=1)
    ctx = RunContext(
        manifest=GeneratedRows.manifest,
        manifest_hash="0" * 64,
        step_seed=1,
        job_id=None,
        column_roles={"prompt": "content", "completion": "content"},
        rowkey_scheme="dw.rowkey/v1",
        bindings=[],
    )
    with pytest.raises(OperatorError) as refused:
        GeneratedRows().run(
            version_table(["x"]), {"generation_run_id": run_id, "target_type": "sft"}, ctx
        )
    assert refused.value.code == "generation_run_not_bound"


def publish_labels(run_id: str, rows: list[dict[str, Any]]) -> None:
    """A completed label run's published labels.parquet (005's file contract)."""
    path = resolve_under_data_dir("runs", run_id, "labels.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table(
            {
                "row_key": [r["row_key"] for r in rows],
                "outcome": [r["outcome"] for r in rows],
                "parsed_value": [json.dumps(r.get("parsed", {})) for r in rows],
                "probability": pa.array([r.get("probability") for r in rows], pa.float64()),
            }
        ),
        path,
    )


def ctx_for(manifest: Any, label_run: str, roles: dict[str, str]) -> Any:
    from src.operators.context import RunContext

    return RunContext(
        manifest=manifest,
        manifest_hash="0" * 64,
        step_seed=1,
        job_id=None,
        column_roles=roles,
        rowkey_scheme="dw.rowkey/v1",
        bindings=[{"kind": "label_run", "id": label_run}],
    )


def test_judge_filter_drops_only_generated_rows_with_reasons(
    tmp_path: Path, data_dir: Path
) -> None:
    from src.operators.native.generation import JudgeFilter

    table = version_table(["source a"], generated=["gen ok", "gen bad", "gen missing"])
    keys = table.column("_dw_row_key").to_pylist()
    publish_labels(
        "lr_j",
        [
            {"row_key": keys[3], "outcome": "yes"},
            {"row_key": keys[4], "outcome": "no"},
        ],
    )
    ctx = ctx_for(JudgeFilter.manifest, "lr_j", {"prompt": "content", "completion": "content"})
    result = JudgeFilter().run(table, {"label_run_id": "lr_j", "keep_outcomes": ["yes"]}, ctx)
    kept = result.output.column("prompt").to_pylist()
    assert kept == ["source a", "held-out question one", "held-out two", "gen ok"]
    reasons = {
        e.row_key: (e.reason_code, e.statistic_name, e.statistic_text) for e in result.events
    }
    assert reasons == {
        keys[4]: ("judge_rejected", "verdict", "no"),
        keys[5]: ("judge_missing", "verdict", "missing"),
    }


def test_pair_filter_reasons_and_pair_from_scores(data_dir: Path) -> None:
    from src.operators.native.generation import PairFilter, PairFromScores

    base = version_table([], test=[], generated=["p1", "p2", "p3", "p4"])
    rows = base.to_pylist()
    for row in rows:
        row["chosen"], row["rejected"] = f"c {row['prompt']}", f"r {row['prompt']}"
    table = pa.Table.from_pylist(
        rows,
        schema=base.schema.append(pa.field("chosen", pa.string())).append(
            pa.field("rejected", pa.string())
        ),
    )
    keys = [r["_dw_row_key"] for r in rows]
    publish_labels(
        "lr_p",
        [
            {"row_key": keys[0], "outcome": "A"},
            {"row_key": keys[1], "outcome": "B"},
            {"row_key": keys[2], "outcome": "tie"},
            {"row_key": keys[3], "outcome": "position_inconsistent"},
        ],
    )
    roles = {"prompt": "content", "chosen": "content", "rejected": "content"}
    result = PairFilter().run(
        table, {"label_run_id": "lr_p"}, ctx_for(PairFilter.manifest, "lr_p", roles)
    )
    assert result.output.num_rows == 1
    assert sorted(e.reason_code for e in result.events) == [
        "pair_judge_disagrees",
        "pair_position_inconsistent",
        "pair_tie",
    ]
    # pairs from scores: prompt "same" has responses scored 0.9 / 0.2 / 0.5; "close" 0.5 / 0.45
    gen_rows = []
    for prompt, completion in [
        ("same", "x"),
        ("same", "y"),
        ("same", "z"),
        ("close", "u"),
        ("close", "v"),
    ]:
        row = {"prompt": prompt, "completion": completion, "chosen": None, "rejected": None}
        from src.services.row_keys import compute_row_key

        row["_dw_row_key"] = compute_row_key(row, ["chosen", "completion", "prompt", "rejected"])
        row.update(
            {
                "_dw_occurrence": 0,
                "_dw_split": "train",
                "_dw_origin": "generated",
                "_dw_source_id": None,
                "_dw_source_locator": None,
                "_dw_parent_keys": None,
            }
        )
        gen_rows.append(row)
    scores = [0.9, 0.2, 0.5, 0.5, 0.45]
    publish_labels(
        "lr_s",
        [
            {"row_key": r["_dw_row_key"], "outcome": "scored", "parsed": {"score": s}}
            for r, s in zip(gen_rows, scores, strict=True)
        ],
    )
    scored = pa.Table.from_pylist(
        gen_rows,
        schema=pa.schema(
            [
                ("prompt", pa.string()),
                ("completion", pa.string()),
                ("chosen", pa.string()),
                ("rejected", pa.string()),
                ("_dw_row_key", pa.string()),
                ("_dw_occurrence", pa.int32()),
                ("_dw_split", pa.string()),
                ("_dw_origin", pa.string()),
                ("_dw_source_id", pa.string()),
                ("_dw_source_locator", pa.string()),
                ("_dw_parent_keys", pa.list_(pa.string())),
            ]
        ),
    )
    roles = {
        "prompt": "content",
        "completion": "content",
        "chosen": "content",
        "rejected": "content",
    }
    from src.operators.context import OccurrenceAllocator

    ctx = ctx_for(PairFromScores.manifest, "lr_s", roles)
    ctx.allocator = OccurrenceAllocator()
    result = PairFromScores().run(scored, {"label_run_id": "lr_s", "min_margin": 0.3}, ctx)
    assert result.added is not None and result.added.num_rows == 1
    (pair,) = result.added.to_pylist()
    assert (pair["prompt"], pair["chosen"], pair["rejected"]) == ("same", "x", "y")
    assert result.report["pair_margin_too_small"] == 1


async def test_a_steered_candidate_has_dpo_pairs(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver
) -> None:
    from tests.integration.generation.test_steered_run import DPO_ROLES, PROFILE

    gen.fake.profiles = {"prof-1": dict(PROFILE)}
    table = version_table(["s one", "s two"])
    table = table.append_column("chosen", pa.array(["good"] * 4, pa.string()))
    table = table.append_column("rejected", pa.array(["bad"] * 4, pa.string()))
    source = make_version(table, target_type="dpo", roles={**DPO_ROLES, "completion": "metadata"})
    template = await respond_template(client)
    body = run_body(
        source,
        template,
        mode="steered_pairs",
        target_type="dpo",
        sample_size=2,
        setting_a={"kind": "inline", "sae_id": SAE, "features": [{"index": 4127, "strength": 0.0}]},
        setting_b={"kind": "profile", "profile_name": "humor"},
        chosen_side="b",
    )
    run_id = (await client.post(RUNS, json=body)).json()["id"]
    assert gen.run_until_done(run_id).state == "completed"
    candidate = await build_candidate(client, driver, run_id)
    pairs = [r for r in rows_of(candidate) if r["_dw_origin"] == "generated"]
    assert len(pairs) == 2
    assert all('"profile": "humor"' in r["chosen"] or "humor" in r["chosen"] for r in pairs)
    assert all(r["generation_side"] == "b" for r in pairs)
    with sync_session_factory()() as db:
        from src.services.curation import api as curation_api

        result = curation_api.validate_trl(candidate, "dpo", session=db)
        assert result.valid, result.failures
    # 11.1 / 11.3: 006's audit drawn with the generation strata (origin and side)
    audit = await client.post(
        f"/api/v1/versions/{candidate}/audit",
        json={
            "size": 50,
            "strata_columns": ["_dw_origin", "generation_side"],
            "question": "Is the chosen answer better?",
        },
    )
    assert audit.status_code == 201, audit.text
    assert audit.json()["strata"]["columns"] == ["_dw_origin", "generation_side"]
    # 11.2 / 11.3: 008 accepts the steered pairs as a DPO export and a miForge preference set
    for body in (
        {"target": "trl", "version_id": candidate, "trl_type": "dpo"},
        {"target": "miforge_set", "version_id": candidate, "miforge_set_kind": "preference_pairs"},
    ):
        response = await client.post("/api/v1/exports", json=body)
        assert response.status_code == 202, response.text


def test_the_cluster_cap_applies_to_generated_rows_at_the_references_largest_cluster(
    data_dir: Path,
) -> None:
    """T-33: 004's balancer with ``applies_to: generated`` and no cap drops generated rows above
    the largest SOURCE cluster; no source row is ever dropped."""
    from tests.support.curation_fixtures import run_operator

    sources = [f"distinct topic number {i} words here" for i in range(6)]
    generated = ["the same generated line"] * 1 + [f"the same generated line {i}" for i in range(9)]
    table = version_table(sources, test=[], generated=generated)
    ran = run_operator(
        "cluster_balancer",
        {"k": 2, "applies_to": "generated"},
        table,
        {"prompt": "content", "completion": "content"},
    )
    dropped = ran.events_by_reason("cluster_cap")
    origins = dict(
        zip(
            table.column("_dw_row_key").to_pylist(),
            table.column("_dw_origin").to_pylist(),
            strict=True,
        )
    )
    assert dropped, "the narrow generated cluster is capped"
    assert all(origins[e["row_key"]] == "generated" for e in dropped)
    kept_sources = [o for o in ran.output.column("_dw_origin").to_pylist() if o == "source"]
    assert len(kept_sources) == 6


async def test_filtered_build_drops_judge_rejected_rows_with_history_and_acceptance_queries(
    client: httpx.AsyncClient, gen: Gen, driver: RealDriver, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FTASKS 7.8 and 16.1 – 16.4: a real judge label run over C's GENERATED rows (005's row filter),
    a real F build with ``dw_judge_filter``, row history for a dropped generated row, and the
    success-criteria queries over records, pairs and versions."""
    from sqlalchemy import text

    from src.workers import label_run_tasks
    from tests.support.generation_fixtures import JUDGE_MODEL

    source = make_version(version_table(["keep me", "drop me"]))
    run_id = await generate(client, gen, source, sample_size=2)
    candidate = await build_candidate(client, driver, run_id)
    # the judge: another model, resident now (one model at a time), answering by content
    gen.fake.resident = {**(gen.fake.resident or {}), "name": JUDGE_MODEL, "id": 8}
    gen.fake.gen_answer = None
    gen.fake.generation_mode = False
    gen.fake.judge_answer = lambda messages: (
        "VERDICT: no" if "drop me" in str(messages) else "VERDICT: yes"
    )
    rubric = {
        "style": "binary",
        "messages": [{"role": "user", "content": "Good? {text}\nEnd with VERDICT: yes or no."}],
        "input_fields": ["text"],
        "parser": "verdict_line_v1",
        "allowed_verdicts": ["yes", "no"],
    }
    rubric_id = (await client.post("/api/v1/rubrics", json={"name": "j/f", "body": rubric})).json()[
        "id"
    ]
    started = await client.post(
        "/api/v1/label-runs",
        json={
            "input_version_id": candidate,
            "role": "judge",
            "rubric_id": rubric_id,
            "field_map": {"text": "completion"},
            "row_filter": {"origin": "generated"},
            "sampling": {"seed": 1},
        },
    )
    assert started.status_code == 201, started.text
    label_run = started.json()["id"]
    assert started.json()["rows_total"] == 2, "only the generated rows are judged"
    monkeypatch.setattr(label_run_tasks, "_next_jobs", lambda: None)
    with sync_session_factory()() as db:
        job_id = db.execute(
            text("SELECT job_id FROM dw_label_run_jobs WHERE label_run_id = :r"), {"r": label_run}
        ).scalar_one()
    label_run_tasks.run_job(job_id)
    from src.core.agent_origin import Who
    from src.core.database import get_db
    from src.services import recipe_service

    async for db in get_db():
        recipe = await recipe_service.create(
            db,
            Who("Test Operator", "operator"),
            name="filter-generated",
            description=None,
            body={
                "format": "dw.recipe/v1",
                "steps": [
                    {
                        "operator": "dw_judge_filter",
                        "version": "1",
                        "params": {"label_run_id": label_run, "keep_outcomes": ["yes"]},
                    }
                ],
            },
        )
        revision = recipe.head_revision_id
        break
    with sync_session_factory()() as db:
        dataset = db.get(Version, candidate).dataset_id  # type: ignore[union-attr]
    response = await client.post(
        "/api/v1/versions",
        json={
            "dataset_id": dataset,
            "inputs": [{"kind": "version", "version_id": candidate}],
            "recipe_revision_id": revision,
            "bindings": [{"kind": "label_run", "id": label_run}],
            "seed": 4,
        },
    )
    assert response.status_code == 202, response.text
    assert driver.run(response.json()["job_id"]) == "completed"
    with sync_session_factory()() as db:
        from src.models.version import VersionBuild

        filtered = str(db.get(VersionBuild, response.json()["job_id"]).version_id)  # type: ignore[union-attr]
    rows = rows_of(filtered)
    kept = [r["completion"] for r in rows if r["_dw_origin"] == "generated"]
    assert len(kept) == 1 and "keep me" in kept[0]
    assert len([r for r in rows if r["_dw_origin"] == "source"]) == 4, "source rows untouched"
    dropped = next(
        r
        for r in rows_of(candidate)
        if r["_dw_origin"] == "generated" and "drop me" in r["completion"]
    )
    history = await client.get(
        f"/api/v1/versions/{filtered}/rows/history", params={"row_key": dropped["_dw_row_key"]}
    )
    assert history.status_code == 200, history.text
    assert "judge_rejected" in history.text
    # FTASKS 16.1 – 16.4: the success-criteria queries
    with sync_session_factory()() as db:
        no_record = db.execute(
            text(
                "SELECT count(*) FROM (SELECT unnest(ARRAY[:keys]) AS k) g WHERE NOT EXISTS ("
                "SELECT 1 FROM dw_generation_records r WHERE r.run_id = :run AND r.outcome = 'generated'"
                " AND r.model_id IS NOT NULL AND r.seed_sent IS NOT NULL AND r.model_revision IS NOT NULL"
                " AND r.steering_check IN ('match', 'not_applicable'))"
            ),
            {
                "keys": [r["_dw_row_key"] for r in rows if r["_dw_origin"] == "generated"],
                "run": run_id,
            },
        ).scalar_one()
        bad_pairs = db.execute(
            text(
                "SELECT count(*) FROM dw_generation_pairs p JOIN dw_generation_records r ON "
                "r.run_id = p.run_id AND r.stage = 'respond' AND r.record_index IN "
                "(p.record_index_a, p.record_index_b) WHERE r.steering_check <> 'match' "
                "OR r.reported_steering IS NULL"
            )
        ).scalar_one()
    assert no_record == 0 and bad_pairs == 0
    for version_id in (candidate, filtered):
        rows_v = rows_of(version_id)
        assert not [
            r for r in rows_v if r["_dw_split"] == "test" and r["_dw_origin"] == "generated"
        ]
