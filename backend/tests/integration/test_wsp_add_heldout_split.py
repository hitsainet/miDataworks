"""Adding a WSP held-out split to a version that inherits one (MCP gaps G-4 and G-5, FU-7a 2026-11-25).

FU-7a's reserve needed a second ``wsp_heldout`` split drawn from pool rows that v1 did not place,
while v1's ``everyday_fresh`` stayed exactly as it was. Every direct route was refused
(``wsp_heldout_redeclared``, ``wsp_partition_empty``, ``move_to_unknown``), and the reassign that
works needs a column telling the inherited rows apart, which only a 22-minute helper version could
write, because the readers refused ``_dw_split`` (G-4).

With system columns readable (G-4), ONE existing step does it: a ``split@1.3.0`` reassign directed
by ``_dw_split``. The inherited rows (``_dw_split == everyday_fresh``) go back to ``everyday_fresh``
by value with fraction 0, so no other row can join it; the other rows are drawn by fraction into
the new held-out split. No new split mode and no changed meaning.

Each P43 guarantee is shown still refusing on this very route: the inherited partition cannot
change its mark or lose rows, a generated row cannot reach a held-out split, and an empty held-out
split is refused. Two fixtures that do not agree by construction: ``fresh`` rows talk about lunch,
``pool`` rows about pumps, of different lengths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from src.core.storage import resolve_under_data_dir
from tests.integration.test_directed_held_out import ECHO, driver, run
from tests.integration.test_version_build import setup, src
from tests.integration.test_wsp_versions import by_name, child_of
from tests.support.operator_build_driver import RealDriver
from tests.support.version_fixtures import make_source

__all__ = ["driver"]

FRESH_MARK = {
    "role": "wsp_heldout",
    "set_kind": "everyday_text",
    "text_column": "text",
    "min_chars": 0,
}


def fresh_rows(tag: str) -> list[dict[str, Any]]:
    return [{"text": f"{tag} fresh {i}: lunch at noon, then a walk"} for i in range(4)]


def pool_rows(tag: str, n: int = 10) -> list[dict[str, Any]]:
    return [{"text": f"{tag} pool {i} pumps " + "x" * i} for i in range(n)]


#: v1: ``everyday_fresh`` marked held out, by keep_source from its source's own split.
V1_SPLIT = {
    "operator": "split",
    "version": "1.3.0",
    "params": {
        "split_names": ["train", "everyday_fresh"],
        "held_out": ["everyday_fresh"],
        "mode": "keep_source",
        "wsp_roles": ["none", "wsp_heldout"],
        "wsp_set_kinds": ["none", "everyday_text"],
        "wsp_min_chars": [0, 0],
        "wsp_text_column": "text",
    },
}


def add_reserve(**changes: Any) -> dict[str, Any]:
    """The one step: keep ``everyday_fresh`` by its ``_dw_split`` value, draw the reserve."""
    params: dict[str, Any] = {
        "split_names": ["everyday_unused", "everyday_reserve", "everyday_fresh"],
        "split_fractions": [0.0, 1.0, 0.0],
        "held_out": ["everyday_reserve", "everyday_fresh"],
        "directing_columns": ["_dw_split"],
        "held_out_values": ["everyday_fresh"],
        "held_out_value_columns": ["_dw_split"],
        "held_out_split_names": ["everyday_fresh"],
        "wsp_roles": ["none", "wsp_heldout", "wsp_heldout"],
        "wsp_set_kinds": ["none", "everyday_text", "everyday_text"],
        "wsp_min_chars": [0, 0, 0],
        "wsp_text_column": "text",
    }
    params.update(changes)
    return {"operator": "split", "version": "1.3.0", "params": params}


#: Six pool rows per stratum of ``_dw_split``: the fresh stratum has 4 and is kept whole.
SAMPLE = {
    "operator": "sample",
    "version": "1.1.0",
    "params": {"mode": "stratified", "strata": ["_dw_split"], "per_stratum": 6},
}


async def v1(client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, tag: str) -> str:
    source = make_source(data_dir, {"everyday_fresh": fresh_rows(tag)}, repo_id=f"t/{tag}-v1")
    out = await run(client, driver, [V1_SPLIT], [src(source)], f"{tag}-v1")
    assert out["status"] == "completed", out
    return str(out["version_id"])


def pool(data_dir: Path, tag: str, rows: list[dict[str, Any]] | None = None) -> dict[str, str]:
    rows = pool_rows(tag) if rows is None else rows
    return src(make_source(data_dir, {"train": rows}, repo_id=f"t/{tag}-pool"))


def texts(split: dict[str, Any]) -> list[str]:
    path = resolve_under_data_dir(split["path"])
    return sorted(str(t) for t in pq.read_table(path, columns=["text"]).column(0).to_pylist())


async def test_one_directed_reassign_adds_a_heldout_split_and_keeps_the_inherited_one(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    parent = await v1(client, driver, data_dir, "ok")
    steps = [SAMPLE, add_reserve()]
    inputs = [child_of(parent), pool(data_dir, "ok")]
    ds, rev = await setup(client, {"format": "dw.recipe/v1", "steps": steps}, name="ok-plan")
    plan = await client.post(
        "/api/v1/versions/plan",
        json={"dataset_id": ds, "recipe_revision_id": rev, "inputs": inputs},
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["valid"] is True, plan.json()["validation"]
    out = await run(client, driver, steps, inputs, "ok-v3")
    assert out["status"] == "completed", out
    before, after = by_name(parent), by_name(str(out["version_id"]))
    # the inherited partition: same rows, same digest, same mark
    assert after["everyday_fresh"]["logical_digest"] == before["everyday_fresh"]["logical_digest"]
    assert texts(after["everyday_fresh"]) == sorted(r["text"] for r in fresh_rows("ok"))
    mark = {k: after["everyday_fresh"]["wsp"][k] for k in FRESH_MARK}
    assert mark == FRESH_MARK
    # the new partition: six pool rows, none of them fresh, held out with its own mark
    reserve = after["everyday_reserve"]
    assert reserve["rows"] == 6 and reserve["held_out"] is True
    assert {k: reserve["wsp"][k] for k in FRESH_MARK} == FRESH_MARK
    assert set(texts(reserve)) <= {r["text"] for r in pool_rows("ok")}
    assert "everyday_unused" not in after  # fraction 0: no file, no mark


async def test_an_undirected_reassign_still_refuses_rows_leaving_the_inherited_split(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Guarantee 1a: without the direction, fresh rows are redrawn and some leave held out."""
    parent = await v1(client, driver, data_dir, "undirected")
    step = add_reserve(
        split_fractions=[0.6, 0.2, 0.2],
        directing_columns=[],
        held_out_values=[],
        held_out_value_columns=[],
        held_out_split_names=[],
    )
    out = await run(
        client, driver, [step], [child_of(parent), pool(data_dir, "undirected")], "undirected-v3"
    )
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_heldout_rows_left_heldout"
    assert out["error"]["details"]["split"] == "everyday_fresh"


async def test_the_inherited_mark_still_cannot_change(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Guarantee 1b: the same route re-declaring ``everyday_fresh`` as another kind is refused."""
    parent = await v1(client, driver, data_dir, "remark")
    step = add_reserve(wsp_set_kinds=["none", "everyday_text", "nonsense_text"])
    out = await run(client, driver, [step], [child_of(parent), pool(data_dir, "remark")], "remark")
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "wsp_heldout_redeclared"
    assert error["details"]["split"] == "everyday_fresh"
    assert error["details"]["declared"]["set_kind"] == "nonsense_text"


async def test_a_generated_row_still_cannot_reach_the_new_heldout_split(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Guarantee 2a (P43): an echo before the split makes generated copies of the pool rows; the
    split refuses to draw any of them into the new, undirected held-out split."""
    parent = await v1(client, driver, data_dir, "generated")
    out = await run(
        client,
        driver,
        [ECHO, add_reserve(split_fractions=[0.5, 0.5, 0.0])],
        [child_of(parent), pool(data_dir, "generated")],
        "generated-v3",
    )
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "generated_in_held_out"
    assert "everyday_reserve" in out["error"]["message"]


#: Drop the generated copies of the POOL rows only, by two system columns (a G-4 read at build time).
DROP_GENERATED_POOL = [
    {
        "operator": "rule_label",
        "version": "2",
        "params": {
            "output_column": "fu_drop",
            "rules": ["yes if _dw_origin == generated and _dw_split == train"],
            "default": "no",
        },
    },
    {
        "operator": "metadata_value_filter",
        "version": "1.0.0",
        "params": {"column": "fu_drop", "values": ["yes"]},
    },
]


async def test_a_generated_row_still_cannot_reach_the_inherited_heldout_split(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Guarantee 2b (P43): the generated copies of the FRESH rows keep ``_dw_split ==
    everyday_fresh``, so the direction sends them into it; finalize refuses by the WSP code."""
    parent = await v1(client, driver, data_dir, "generated-fresh")
    out = await run(
        client,
        driver,
        [ECHO, *DROP_GENERATED_POOL, add_reserve(split_fractions=[0.5, 0.5, 0.0])],
        [child_of(parent), pool(data_dir, "generated-fresh")],
        "generated-fresh-v3",
    )
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_heldout_contains_generated"
    assert out["error"]["details"]["split"] == "everyday_fresh"
    assert len(out["error"]["details"]["sample_keys"]) == 4


async def test_an_empty_new_heldout_split_is_still_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Guarantee 3: with no other rows to draw from, the new held-out split is empty: refused."""
    parent = await v1(client, driver, data_dir, "empty")
    out = await run(client, driver, [add_reserve()], [child_of(parent)], "empty-v3")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_partition_empty"
    assert out["error"]["details"]["split"] == "everyday_reserve"
    assert json.dumps(out["error"]).count("lunch") == 0  # never row text


async def test_move_into_a_new_split_still_refuses_and_names_the_route(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Mode move keeps its meaning (into a split that already has rows); its refusal now points
    at the directed reassign above."""
    parent = await v1(client, driver, data_dir, "move")
    move = {
        "operator": "split",
        "version": "1.3.0",
        "params": {
            "mode": "move",
            "move_where": "_dw_split == train",
            "move_from": ["train"],
            "move_to": "everyday_reserve",
            "move_fraction": 0.5,
        },
    }
    out = await run(client, driver, [move], [child_of(parent), pool(data_dir, "move")], "move-v3")
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "move_to_unknown"
    assert "_dw_split" in error["message"]
    step_error = error["details"]["executor_error"]
    assert step_error["details"]["next"]["tool"] == "dataworks_howto"
    assert step_error["details"]["next"]["args"] == {"topic": "wsp_partitions"}


# --- operator decision 2026-11-25: an inherited held-out partition keeps exactly its rows ----------


async def test_an_inherited_heldout_split_that_gains_rows_is_refused(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """A child of v1 plus a source holding two more rows in ``everyday_fresh``, with no step
    declaring roles: the split keeps its mark, so before 2026-11-25 it grew silently."""
    from tests.integration.test_directed_held_out import KEEP

    parent = await v1(client, driver, data_dir, "grow")
    extra = make_source(
        data_dir,
        {"everyday_fresh": [{"text": f"grow extra {i}: a new everyday line"} for i in range(2)]},
        repo_id="t/grow-extra",
    )
    out = await run(client, driver, [KEEP], [child_of(parent), src(extra)], "grow-v2")
    assert out["status"] == "failed", out
    error = out["error"]
    assert error["code"] == "wsp_heldout_grew"
    assert error["details"]["split"] == "everyday_fresh"
    assert error["details"]["added"] == 2 and len(error["details"]["sample_keys"]) == 2
    assert "new everyday line" not in json.dumps(error)  # never row text


async def test_redeclaring_the_inherited_split_does_not_let_it_grow(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """The same growth with a step re-declaring the identical mark (keep_source): still refused."""
    parent = await v1(client, driver, data_dir, "grow-declared")
    extra = make_source(
        data_dir,
        {"everyday_fresh": [{"text": "grow-declared extra: one more line"}]},
        repo_id="t/grow-declared-extra",
    )
    out = await run(client, driver, [V1_SPLIT], [child_of(parent), src(extra)], "grow-declared-v2")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_heldout_grew"
    assert out["error"]["details"]["added"] == 1


async def test_a_second_copy_of_an_inherited_row_is_growth(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """An exact duplicate of a fresh row arriving in the same split counts: by key and count."""
    from tests.integration.test_directed_held_out import KEEP

    parent = await v1(client, driver, data_dir, "grow-copy")
    copy = make_source(
        data_dir, {"everyday_fresh": [fresh_rows("grow-copy")[0]]}, repo_id="t/grow-copy-extra"
    )
    out = await run(client, driver, [KEEP], [child_of(parent), src(copy)], "grow-copy-v2")
    assert out["status"] == "failed", out
    assert out["error"]["code"] == "wsp_heldout_grew"
    assert out["error"]["details"]["added"] == 1


async def test_a_child_that_keeps_or_drops_rows_is_not_growth(
    client: httpx.AsyncClient, driver: RealDriver, data_dir: Path, operator_name: str
) -> None:
    """Keeping every row (no step) and dropping one (a filter) both build."""
    from tests.integration.test_directed_held_out import KEEP

    parent = await v1(client, driver, data_dir, "keep")
    same = await run(client, driver, [KEEP], [child_of(parent)], "keep-v2")
    assert same["status"] == "completed", same
    drop = {
        "operator": "metadata_value_filter",
        "version": "1.0.0",
        "params": {"column": "text", "values": [fresh_rows("keep")[0]["text"]]},
    }
    fewer = await run(client, driver, [drop], [child_of(parent)], "keep-fewer")
    assert fewer["status"] == "completed", fewer
    assert by_name(str(fewer["version_id"]))["everyday_fresh"]["rows"] == 3
