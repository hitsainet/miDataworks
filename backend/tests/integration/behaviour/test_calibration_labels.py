"""A test set from a calibration set's answers (ENH-006 D116 G6; FTASKS 20.16).

Through a real build (``POST /versions``, the real executor) with the recipe the pack's ``human_eval``
stage holds, on rows that carry every system column, and through 003's executor for the refusals. The
fixture's plan label is ``deployed_truth`` and disagrees with the answers, so a build that copied a
column instead of the set's answers would show.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.core.storage import resolve_under_data_dir
from src.operators import registry as registry_module
from src.operators.errors import StepFailed
from src.operators.registry import OperatorRegistry
from src.services import operator_port
from tests.fixtures import humor_pool as hp
from tests.integration.rules.conftest import live_tools
from tests.support.curation_fixtures import run_operator
from tests.support.gap_fixtures import calibration_set, mark_deleted, split_version
from tests.support.operator_build_driver import RealDriver, real_driver
from tests.support.source_tree import REPO

__all__ = ["real_driver"]

API = "/api/v1"
ROLES = {
    "text": "content",
    "deployed_truth": "metadata",
    "kind": "metadata",
    "carrier_id": "metadata",
    "pair_id": "metadata",
    "generation_run_id": "metadata",
}
YES, NO = "yes", "no"


def carriers(n: int = 30) -> list[dict[str, Any]]:
    return [
        {
            "text": f"carrier {i} wrote something with no number in it at all",
            "deployed_truth": i % 2 == 0,
            "kind": "masked" if i % 5 == 0 else "pos",
            "carrier_id": f"plan-c{i}",
            "pair_id": f"plan-p{i // 2}",
            "generation_run_id": "gr_one",
        }
        for i in range(n)
    ]


def stage_recipe(set_id: str, train: str, split: str = "train") -> dict[str, Any]:
    """The pack's own stage, filled the way a rerun fills it."""
    pipeline = json.loads((REPO / "behaviours/dlp-ssn-us/pipeline.json").read_text())
    stage = next(s for s in pipeline["stages"] if s["id"] == "human_eval")
    text = json.dumps(stage["recipe"])
    text = text.replace("{{calibration_set}}", set_id).replace("{{train_version}}", train)
    recipe: dict[str, Any] = json.loads(text)
    assert json.loads(recipe["steps"][0]["params"]["exclude_versions"]) == [
        {"version_id": train, "split": split}
    ]
    return recipe


@pytest.fixture
def registered(monkeypatch: pytest.MonkeyPatch) -> None:
    reg = OperatorRegistry.build(catalogues=(), entry_points=())
    monkeypatch.setattr(operator_port, "_registry", reg)
    monkeypatch.setattr(registry_module, "_process", reg)


async def _merged(client: httpx.AsyncClient, driver: RealDriver, data_dir: Path) -> tuple[str, str]:
    from tests.integration.test_version_build import request, src
    from tests.support.stub_operators import body
    from tests.support.version_fixtures import make_source

    made = await client.post(f"{API}/datasets", json={"name": "merged", "target_type": "detector"})
    assert made.status_code == 201, made.text
    dataset = made.json()["id"]
    source = make_source(data_dir, {"train": carriers()})
    recipe = await client.post(
        f"{API}/recipes", json={"name": "m", "body": body(("fx_keep_all", {}))}
    )
    assert recipe.status_code == 201, recipe.text
    started = await request(
        client,
        dataset,
        recipe.json()["head_revision_id"],
        [src(source)],
        seed=1,
        column_roles=ROLES,
    )
    assert started.status_code == 202, started.text
    assert driver.run(started.json()["job_id"]) == "completed"
    versions = (await client.get(f"{API}/versions", params={"dataset_id": dataset})).json()["items"]
    return dataset, str(versions[0]["id"])


async def _rows_of(client: httpx.AsyncClient, version: str) -> list[dict[str, Any]]:
    got = (await client.get(f"{API}/versions/{version}")).json()
    rows: list[dict[str, Any]] = []
    for split in got["splits"]:
        rows.extend(pq.read_table(resolve_under_data_dir(split["path"])).to_pylist())
    return rows


async def test_a_real_build_writes_the_answers_and_stays_clear_of_training(
    client: httpx.AsyncClient,
    real_driver: RealDriver,
    data_dir: Path,
    operator_name: str,
    registered: None,
) -> None:
    dataset, merged = await _merged(client, real_driver, data_dir)
    rows = await _rows_of(client, merged)
    by_text = {r["text"]: r for r in rows}
    row = [by_text[c["text"]] for c in carriers()]
    # The set answers rows 0..19, opposite to the plan on purpose, and a row no longer there.
    answers: dict[str, str | None] = {
        row[i]["_dw_row_key"]: (NO if row[i]["deployed_truth"] else YES) for i in range(20)
    }
    answers["a" * 64] = YES  # labelled, and gone from this version
    answers[row[20]["_dw_row_key"]] = None  # the mapping excluded it: not a label
    set_id = calibration_set(merged, answers)
    # Training holds row 1 itself, a row sharing row 2's carrier, a row sharing row 4's pair, and a
    # row whose carrier is row 6's PAIR: each rebuild moves rows between splits, and ids cross columns.
    train = split_version(
        data_dir,
        {
            "train": [
                {
                    "_dw_row_key": row[1]["_dw_row_key"],
                    "text": "row 1, in training",
                    "carrier_id": "elsewhere-a",
                    "pair_id": None,
                },
                {"text": "other a", "carrier_id": row[2]["carrier_id"], "pair_id": None},
                {"text": "other b", "carrier_id": "elsewhere-b", "pair_id": row[4]["pair_id"]},
                {"text": "other c", "carrier_id": row[6]["pair_id"], "pair_id": "elsewhere-c"},
                {"text": "other d", "carrier_id": "elsewhere-d", "pair_id": None},
            ],
            "test": [
                # the named split is train: row 14 being in another split does not touch it
                {"_dw_row_key": row[14]["_dw_row_key"], "text": "kept in test", "carrier_id": "x"},
            ],
        },
    )
    saved = await client.post(
        f"{API}/recipes", json={"name": "human-eval", "body": stage_recipe(set_id, train)}
    )
    assert saved.status_code == 201, saved.text
    started = await client.post(
        f"{API}/versions",
        json={
            "dataset_id": dataset,
            "recipe_revision_id": saved.json()["head_revision_id"],
            "inputs": [{"kind": "version", "version_id": merged}],
            "seed": 1,
        },
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    assert real_driver.run(job_id) == "completed", (await client.get(f"{API}/jobs/{job_id}")).text
    built = (await client.get(f"{API}/jobs/{job_id}")).json()
    version = built["result"]["version_id"] if built.get("result") else None
    assert version is not None, built
    out = await _rows_of(client, version)
    gone = {i for i, r in enumerate(row[:20]) if r["text"] not in {x["text"] for x in out}}
    # row 1 itself, row 2's carrier; pair p2 holds rows 4 and 5; pair p3 holds rows 6 and 7.
    assert gone == {1, 2, 4, 5, 6, 7}, gone
    assert len(out) == 14
    for item in out:
        source = by_text[item["text"]]
        assert item["human_label"] == answers[source["_dw_row_key"]]
        assert item["human_label"] != ("yes" if source["deployed_truth"] else "no")
        assert item["human_label_ex_masked"] == (
            "excluded" if source["kind"] == "masked" else item["human_label"]
        )
    assert {r["text"] for r in out} <= {r["text"] for r in row[:20]}
    assert {"a" * 64} & {r["_dw_row_key"] for r in out} == set()
    # the build says what it did, in the row log
    events = (await client.get(f"{API}/versions/{version}/drop-log", params={"limit": 200})).json()
    reasons = json.dumps(events)
    for code in (
        "not_in_calibration_set",
        "shares_row_with_excluded_split",
        "shares_group_with_excluded_split",
    ):
        assert code in reasons, code


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    return hp.with_system_columns(rows)


def _keys(table: pa.Table) -> list[str]:
    return [str(k) for k in table.column("_dw_row_key").to_pylist()]


PLAIN = {"text": "content"}


def _plain(n: int = 6, **extra: Any) -> pa.Table:
    return _table([{"text": f"plain row {i} of this table", **extra} for i in range(n)])


async def test_the_report_counts_and_the_answers_are_row_by_row(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    table = _plain()
    keys = _keys(table)
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, {keys[0]: YES, keys[1]: NO, keys[2]: None, "b" * 64: YES})
    ran = run_operator(
        "calibration_labels",
        {"calibration_set_id": set_id, "output_column": "human_label"},
        table,
        PLAIN,
    )
    assert ran.output.column("human_label").to_pylist() == [YES, NO]
    report = ran.meta["reports"][0]
    assert report["kept"] == 2 and report["kept_by_label"] == {NO: 1, YES: 1}
    assert report["labelled_rows"] == 3 and report["labelled_rows_not_in_input"] == 1
    assert report["left_out"] == {"not_in_calibration_set": 4}
    assert report["label_set"] == [YES, NO]
    assert len(ran.events_by_reason("not_in_calibration_set")) == 4


async def test_two_builds_of_one_set_agree(client: httpx.AsyncClient, data_dir: Path) -> None:
    table = _plain()
    keys = _keys(table)
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, {keys[0]: YES, keys[3]: NO})
    params = {"calibration_set_id": set_id, "output_column": "human_label"}
    first = run_operator("calibration_labels", params, table, PLAIN)
    second = run_operator("calibration_labels", params, table, PLAIN, seed=99)
    assert first.output.to_pylist() == second.output.to_pylist()


def _ids(i: int, carrier: str | None, pair: str | None) -> dict[str, Any]:
    return {"text": f"id row {i} of this table", "carrier_id": carrier, "pair_id": pair}


async def test_a_null_id_shares_nothing(client: httpx.AsyncClient, data_dir: Path) -> None:
    """A null is no id, on either side, whatever a text form of it reads like."""
    table = _table([_ids(0, None, None), _ids(1, "None", None), _ids(2, "c2", "p2")])
    keys = _keys(table)
    roles = {**PLAIN, "carrier_id": "metadata", "pair_id": "metadata"}
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, dict.fromkeys(keys, YES))
    train = split_version(
        data_dir, {"train": [_ids(9, None, None), _ids(8, "c2", None)]}, roles=roles
    )
    params = {
        "calibration_set_id": set_id,
        "output_column": "human_label",
        "exclude_versions": json.dumps([{"version_id": train, "split": "train"}]),
    }
    ran = run_operator("calibration_labels", params, table, roles)
    assert ran.output.column("carrier_id").to_pylist() == [None, "None"]
    # a real id spelled None, in training: the row holding it goes, the row holding no id stays
    spelled = split_version(data_dir, {"train": [_ids(7, "None", None)]}, roles=roles)
    params["exclude_versions"] = json.dumps([{"version_id": spelled, "split": "train"}])
    ran = run_operator("calibration_labels", params, table, roles)
    assert ran.output.column("carrier_id").to_pylist() == [None, "c2"]


@pytest.mark.parametrize("name", ["unknown_set", "deleted_version", "missing_split", "no_groups"])
async def test_refusals_are_named_and_carry_a_live_next(
    client: httpx.AsyncClient, data_dir: Path, name: str
) -> None:
    table = _table([_ids(i, f"c{i}", f"p{i}") for i in range(4)])
    keys = _keys(table)
    roles = {**PLAIN, "carrier_id": "metadata", "pair_id": "metadata"}
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, {keys[0]: YES})
    train = split_version(data_dir, {"train": [_ids(9, "c9", "p9")]}, roles=roles)
    params: dict[str, Any] = {"calibration_set_id": set_id, "output_column": "human_label"}
    exclude: dict[str, str] = {"version_id": train, "split": "train"}
    expected = "exclude_version_unavailable"
    if name == "unknown_set":
        params["calibration_set_id"], expected = "cs_missing", "calibration_set_not_found"
    elif name == "deleted_version":
        mark_deleted(train)
        params["exclude_versions"] = json.dumps([exclude])
    elif name == "missing_split":
        params["exclude_versions"] = json.dumps([{**exclude, "split": "nope"}])
    else:
        bare = split_version(data_dir, {"train": [{"text": "no ids here"}]})
        params["exclude_versions"] = json.dumps([{"version_id": bare, "split": "train"}])
        expected = "exclude_version_no_group_columns"
    with pytest.raises(StepFailed) as exc:
        run_operator("calibration_labels", params, table, roles)
    assert exc.value.code == expected
    assert exc.value.details["next"]["tool"] in await live_tools()


@pytest.mark.parametrize(
    "value", ["not json", '{"version_id": "a"}', '[{"version_id": "a"}]', '[{"split": ""}]']
)
async def test_a_malformed_exclude_list_is_refused_never_ignored(
    client: httpx.AsyncClient, data_dir: Path, value: str
) -> None:
    table = _plain()
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, {_keys(table)[0]: YES})
    with pytest.raises(StepFailed) as exc:
        run_operator(
            "calibration_labels",
            {"calibration_set_id": set_id, "output_column": "h", "exclude_versions": value},
            table,
            PLAIN,
        )
    assert exc.value.code == "params_invalid"


async def test_a_whole_input_with_none_of_the_sets_rows_refuses(
    client: httpx.AsyncClient, data_dir: Path
) -> None:
    table = _plain()
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, {"c" * 64: YES})
    params = {"calibration_set_id": set_id, "output_column": "human_label"}
    with pytest.raises(StepFailed) as exc:
        run_operator("calibration_labels", params, table, PLAIN)
    assert exc.value.code == "calibration_set_rows_absent"
    assert exc.value.details["next"]["tool"] in await live_tools()


async def test_a_preview_sample_with_none_of_the_rows_answers_and_shows_a_kept_row(
    client: httpx.AsyncClient, data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.api.v1.endpoints import operators as endpoint
    from src.operators import preview

    monkeypatch.setattr(endpoint, "send_preview", lambda req, entry: preview.run_request(req))
    train = split_version(data_dir, {"train": [{"text": f"row {i}"} for i in range(8)]})
    from tests.support.gap_fixtures import row_key

    labelled = calibration_set(train, {row_key("row 1"): YES})
    empty = calibration_set(train, {"d" * 64: NO}, label_set=(YES, NO))
    for set_id, kept in ((labelled, 1), (empty, 0)):
        shown = await client.post(
            f"{API}/operators/calibration_labels/1.0.0/preview",
            json={
                "input": {"version_id": train},
                "params": {"calibration_set_id": set_id, "output_column": "human_label"},
            },
        )
        assert shown.status_code == 200, shown.text
        assert shown.json()["result"]["counts"]["kept"] == kept


async def test_an_existing_column_is_refused(client: httpx.AsyncClient, data_dir: Path) -> None:
    table = _plain(human_label="already here")
    keys = _keys(table)
    host = split_version(data_dir, {"train": [{"text": "a"}]})
    set_id = calibration_set(host, {keys[0]: YES})
    with pytest.raises(StepFailed) as exc:
        run_operator(
            "calibration_labels",
            {"calibration_set_id": set_id, "output_column": "human_label"},
            table,
            {**PLAIN, "human_label": "metadata"},
        )
    assert exc.value.code == "output_column_exists"


async def test_the_recipe_check_sees_the_column_it_writes(
    client: httpx.AsyncClient, data_dir: Path, operator_name: str, registered: None
) -> None:
    recipe = stage_recipe("cs_any", "11111111-1111-1111-1111-111111111111")
    checked = await client.post(
        f"{API}/recipes/validate", json={"body": recipe, "columns": ["text", "kind"]}
    )
    assert checked.status_code == 200, checked.text
    assert checked.json()["valid"] is True, checked.json()
