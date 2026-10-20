"""The two minimal-pair operators against a REAL minimal-pairs generation run (009 FTASKS 15.3).

The generation run is 007's own (its worker against the fake miLLM on loopback); its counterparts
enter the input through ``dw_generated_rows@1`` itself; the judge's labels and identity are written
by 005's own writers (``label_store._table`` and ``label_store.export_labeler``), so every file the
operators read has the shape production writes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.operators.context import RunContext
from src.operators.errors import OperatorError
from src.operators.native.detector.minimal_pairs import MinimalPairJoin, MinimalPairScope
from src.operators.native.generation import GeneratedRows
from src.services import label_store
from tests.support.generation_fixtures import GEN_MODEL, JUDGE_MODEL, REVISION, Gen

from .test_minimal_pair_chain import detector_table, detector_version, template_id

RUNS = "/api/v1/generation-runs"
EDITS = {
    "the cat is happy today": "the cat is sad today",
    "the dog is happy now": "the dog is sad now",
}
ROLES = {"text": "content", "label": "metadata"}


def gen_answer(messages: list[dict[str, Any]], body: dict[str, Any]) -> str:
    return EDITS[str(messages[-1]["content"]).split("Text:\n", 1)[1]]


async def minimal_run(client: httpx.AsyncClient, gen: Gen) -> tuple[str, pa.Table]:
    """A completed ``minimal_pairs`` run and the version it adds its rows to (seeds + counterparts)."""
    gen.fake.gen_answer = gen_answer
    version = detector_version(list(EDITS))
    body = {
        "mode": "minimal_pairs",
        "input_version_id": version,
        "prompt_column": "text",
        "seed_splits": ["train"],
        "sample_size": 10,
        "seed": 3,
        "respond_template_id": await template_id(client),
        "target_type": "detector",
    }
    response = await client.post(RUNS, json=body)
    assert response.status_code == 202, response.text
    run_id = str(response.json()["id"])
    assert gen.run_until_done(run_id).state == "completed"
    table = detector_table(list(EDITS))
    ctx = context(GeneratedRows.manifest, table, [{"kind": "generation_run", "id": run_id}])
    result = GeneratedRows().run(
        table, {"generation_run_id": run_id, "target_type": "detector"}, ctx
    )
    assert result.added is not None and result.added.num_rows == 2
    combined = pa.concat_tables([result.output, result.added], promote_options="default")
    return run_id, combined


def context(
    manifest: Any, table: pa.Table, bindings: list[dict[str, Any]], *, build: bool = False
) -> RunContext:
    def reader(columns: list[str] | None) -> Iterator[pa.RecordBatch]:
        yield from (table.select(columns) if columns else table).to_batches()

    return RunContext(
        manifest=manifest,
        manifest_hash="0" * 64,
        step_seed=1,
        job_id=None,
        column_roles=dict(ROLES),
        rowkey_scheme="dw.rowkey/v1",
        step_execution_id="step-1" if build else None,
        input_reader=reader,
        bindings=bindings,
    )


def publish_judge(
    run_id: str,
    verdicts: dict[str, tuple[str, bool]],
    *,
    model: str = JUDGE_MODEL,
    kind: str = "judge",
) -> None:
    """A completed judge run's files, through 005's own writers."""
    records = [
        label_store.LabelRecord(
            row_key=key,
            outcome=outcome,
            parsed_value={"verdict": outcome},
            probability=None,
            distribution=None,
            raw_output=None,
            rationale=None,
            steering_state="not reported",
            latency_ms=1,
            skip_reason=None,
            scored_at=datetime(2026, 10, 7, tzinfo=UTC),
            provisional=provisional,
        )
        for key, (outcome, provisional) in verdicts.items()
    ]
    path = label_store.labels_path(run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(label_store._table(records), path)
    label_store.export_labeler(
        SimpleNamespace(
            id=run_id,
            kind=kind,
            input_version_id="00000000-0000-0000-0000-000000000000",
            rubric_id="rb_1",
            template_id=None,
            labeler_identity={
                "protocol": "openai_chat",
                "model_id": model,
                "model_revision": REVISION,
                "template": "mp/joy@1",
                "question": None,
            },
            labeler_identity_hash="a" * 64,
            labeler_fingerprint="b" * 64,
        )
    )


def keys(table: pa.Table) -> dict[str, str]:
    """text -> row key."""
    return dict(
        zip(table.column("text").to_pylist(), table.column("_dw_row_key").to_pylist(), strict=True)
    )


JOIN = {"flip_from": "yes", "flip_to": "no"}


def join(table: pa.Table, run_id: str, judge: str, **kw: Any) -> Any:
    bindings = kw.pop(
        "bindings",
        [{"kind": "generation_run", "id": run_id}, {"kind": "label_run", "id": judge}],
    )
    ctx = context(MinimalPairJoin.manifest, table, bindings, build=kw.pop("build", True))
    params = {"generation_run_id": run_id, "judge_label_run_id": judge, **JOIN, **kw}
    return MinimalPairJoin().run(table.schema.empty_table(), params, ctx)


async def test_an_unverified_flip_is_dropped_both_rows_with_the_judges_verdict(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    run_id, table = await minimal_run(client, gen)
    k = keys(table)
    publish_judge(
        "lr_mp1",
        {
            k["the cat is happy today"]: ("yes", False),
            k["the cat is sad today"]: ("no", False),  # verified
            k["the dog is happy now"]: ("yes", False),
            k["the dog is sad now"]: ("yes", False),  # the judge still reads joy
        },
    )
    result = join(table, run_id, "lr_mp1")
    kept = result.output.to_pylist()
    assert sorted(r["text"] for r in kept) == ["the cat is happy today", "the cat is sad today"]
    assert {r["pair_id"] for r in kept} == {k["the cat is happy today"]}
    dropped = {
        e.row_key: (e.reason_code, e.statistic_name, e.statistic_text) for e in result.events
    }
    assert dropped[k["the dog is happy now"]] == ("flip_not_verified", "verdict", "yes")
    assert dropped[k["the dog is sad now"]] == ("flip_not_verified", "verdict", "yes")
    assert dropped[k["held out happy row"]][0] == "not_in_run"
    assert result.report["pairs_in"] == 2 and result.report["pairs_verified"] == 1
    assert result.report["dropped"]["flip_not_verified"] == 1
    for row in kept:
        assert row["pair_generator_model_id"] == GEN_MODEL
        assert row["pair_judge_model_id"] == JUDGE_MODEL
        assert row["pair_judge_rubric"] == "mp/joy@1"
        assert row["pair_judge_identity_hash"] == "a" * 64


@pytest.mark.parametrize(
    ("verdicts", "code"),
    [
        ({"seed": ("yes", False)}, "judge_missing"),
        ({"seed": ("yes", False), "cp": ("no", True)}, "judge_provisional"),
        ({"seed": ("yes", False), "cp": ("parse_failure", False)}, "judge_missing"),
        ({"seed": ("no", False), "cp": ("no", False)}, "seed_not_flip_from"),
    ],
)
async def test_no_verification_is_never_a_verified_flip(
    client: httpx.AsyncClient, gen: Gen, verdicts: dict[str, tuple[str, bool]], code: str
) -> None:
    run_id, table = await minimal_run(client, gen)
    k = keys(table)
    by_role = {"seed": k["the cat is happy today"], "cp": k["the cat is sad today"]}
    publish_judge("lr_mp2", {by_role[r]: v for r, v in verdicts.items()})
    result = join(table, run_id, "lr_mp2")
    assert "the cat is sad today" not in result.output.column("text").to_pylist()
    reasons = {e.row_key: e.reason_code for e in result.events}
    assert reasons[by_role["cp"]] == code and reasons[by_role["seed"]] == code


async def test_the_join_refuses_a_judge_that_is_the_generator(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    run_id, table = await minimal_run(client, gen)
    k = keys(table)
    publish_judge(
        "lr_mp3",
        {k["the cat is happy today"]: ("yes", False), k["the cat is sad today"]: ("no", False)},
        model=GEN_MODEL,
    )
    with pytest.raises(OperatorError) as refused:
        join(table, run_id, "lr_mp3")
    assert refused.value.code == "judge_is_generator"


async def test_the_join_reads_only_a_bound_published_judge_run(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    run_id, table = await minimal_run(client, gen)
    k = keys(table)
    verdicts = {
        k["the cat is happy today"]: ("yes", False),
        k["the cat is sad today"]: ("no", False),
    }
    publish_judge("lr_mp4", verdicts)
    # a build that does not bind the judge run, or binds nothing at all, may not read it
    for bindings in ([{"kind": "generation_run", "id": run_id}], []):
        with pytest.raises(OperatorError) as refused:
            join(table, run_id, "lr_mp4", bindings=bindings)
        assert refused.value.code.endswith("_not_bound")
    # a classifier run is not a judge's verification
    publish_judge("lr_mp5", verdicts, kind="classifier")
    with pytest.raises(OperatorError) as wrong:
        join(table, run_id, "lr_mp5")
    assert wrong.value.code == "judge_run_required"
    # no identity published: the judge cannot be named, so nothing is paired
    label_store.labeler_path("lr_mp4").unlink()
    with pytest.raises(OperatorError) as unnamed:
        join(table, run_id, "lr_mp4")
    assert unnamed.value.code == "judge_identity_not_published"


async def test_the_scope_drops_an_edit_over_the_cap_with_its_size(
    client: httpx.AsyncClient, gen: Gen
) -> None:
    run_id, table = await minimal_run(client, gen)
    k = keys(table)
    ctx = context(
        MinimalPairScope.manifest, table, [{"kind": "generation_run", "id": run_id}], build=True
    )
    result = MinimalPairScope().run(
        table.schema.empty_table(), {"generation_run_id": run_id, "max_edit_chars": 3}, ctx
    )
    events = {e.row_key: e for e in result.events}
    # "happy" -> "sad" changes 4 characters (h->s, then ppy->d: 1 + 3; see the unit tests)
    over = [e for e in result.events if e.reason_code == "edit_too_large"]
    assert len(over) == 2 and all(e.statistic_name == "edit_chars" for e in over)
    assert all(e.statistic_value is not None and e.statistic_value == 4 for e in over)
    assert events[k["the cat is happy today"]].reason_code == "seed_without_counterpart"
    assert result.output.num_rows == 0
    unbound = context(MinimalPairScope.manifest, table, [], build=True)
    with pytest.raises(OperatorError) as refused:
        MinimalPairScope().run(table.schema.empty_table(), {"generation_run_id": run_id}, unbound)
    assert refused.value.code == "generation_run_not_bound"
