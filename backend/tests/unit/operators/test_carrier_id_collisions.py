"""One ``carrier_id`` on unrelated rows of two generation runs (dry run, 2026-11-01).

``carrier_id`` is a plan row's key, and the key hashes the plan row's ``idx``, so two carrier plans
over overlapping index ranges make the same ids. A merged version then held 2,659 rows sharing an
id with an unrelated row, and ``slot_siblings@1`` (``pair_id = carrier_id``) and ``split@1`` (groups
over both) would have joined them silently. One run per generator over ONE plan also shares ids,
rightly (017 FTDD, and ``test_agent_journey.py`` builds it): those rows agree on their plan cell.
Through 003's real executor and the live registry.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from src.core.config import get_settings
from src.operators.errors import StepFailed
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import run_operator
from tests.support.label_fixtures import insert_params, roles_after
from tests.unit.operators.test_slot_siblings import carriers, roles, sibling_params

SPLIT_ROLES = {
    "text": "content",
    "label": "metadata",
    "genre": "metadata",
    "carrier_id": "metadata",
    "pair_id": "metadata",
    "generation_run_id": "metadata",
}
ALPHA_RUN, BETA_RUN = "gr_alpha", "gr_beta"


Row = tuple[str | None, str | None, str | None, str]


def split_table(rows: list[Row]) -> pa.Table:
    """Rows of (carrier id, pair id, run, genre), generated, each with its own text."""
    return hp.with_system_columns(
        [
            {
                "text": f"carrier text number {i} about nothing in particular",
                "label": "pos" if i % 2 else "neg",
                "genre": genre,
                "carrier_id": carrier,
                "pair_id": pair,
                "generation_run_id": run,
            }
            for i, (carrier, pair, run, genre) in enumerate(rows)
        ]
    )


def own(extra: int = 40) -> list[Row]:
    return [(f"own{i}", f"own{i}", ALPHA_RUN, "g1") for i in range(extra)]


def colliding(extra: int = 40) -> pa.Table:
    """``c0`` and ``c1`` are held by both runs in different genres, three rows and two rows of
    them: two plans made one id for unrelated rows. Fresh rows pad it so a split has rows to place.
    """
    rows: list[Row] = [
        ("c0", "c0", ALPHA_RUN, "g1"),
        ("c0", "c0", ALPHA_RUN, "g1"),
        ("c0", "c0", BETA_RUN, "g2"),
        ("c1", "c1", ALPHA_RUN, "g1"),
        ("c1", "c1", BETA_RUN, "g3"),
    ]
    return split_table(rows + own(extra))


SPLIT_V11 = {
    "split_names": ["train", "test"],
    "split_fractions": [0.8, 0.2],
    "held_out": [],
    "group_columns": ["carrier_id", "pair_id"],
}


def refused_split(
    table: pa.Table,
    params: dict[str, Any],
    *,
    roles: dict[str, str] | None = None,
    **kwargs: Any,
) -> StepFailed:
    with pytest.raises(StepFailed) as exc:
        run_operator("split", params, table, roles or SPLIT_ROLES, **kwargs)
    return exc.value


def test_split_refuses_an_id_two_runs_hold_and_names_counts_and_runs() -> None:
    error = refused_split(colliding(), SPLIT_V11)
    assert error.code == "carrier_id_collision"
    d = error.details
    assert (d["column"], d["ids"], d["rows"]) == ("carrier_id", 2, 5)
    assert d["rows_by_run"] == {ALPHA_RUN: 3, BETA_RUN: 2}
    assert d["runs"] == [ALPHA_RUN, BETA_RUN] and d["examples"] == ["c0", "c1"]
    assert d["partial"] is False
    assert f"{ALPHA_RUN} (3)" in error.message and f"{BETA_RUN} (2)" in error.message
    assert "2 carrier_id value(s)" in error.message
    assert d["next"]["tool"] == "dataworks_revise_recipe"


def test_split_1_0_0_refuses_it_too() -> None:
    params = {
        "split_names": ["train", "test"],
        "split_fractions": [0.8, 0.2],
        "held_out": [],
        "group_column": "carrier_id",
    }
    assert refused_split(colliding(), params, version="1.0.0").code == "carrier_id_collision"


def test_a_group_of_pair_id_alone_refuses_when_siblings_wrote_it() -> None:
    table = colliding().append_column("sibling_variant", pa.nulls(45, pa.string()))
    params = {**SPLIT_V11, "group_columns": ["pair_id"]}
    roles_with_variant = {**SPLIT_ROLES, "sibling_variant": "metadata"}
    with pytest.raises(StepFailed) as exc:
        run_operator("split", params, table, roles_with_variant)
    assert exc.value.code == "carrier_id_collision" and exc.value.details["column"] == "pair_id"


def test_a_pair_id_no_sibling_step_wrote_may_span_runs() -> None:
    """A minimal pair, say: its two sides can come from two runs, and that is what a pair is."""
    rows: list[Row] = [
        (f"x{i}", "shared-pair", ALPHA_RUN if i % 2 else BETA_RUN, f"g{i}") for i in range(4)
    ]
    params = {**SPLIT_V11, "group_columns": ["pair_id"]}
    done = run_operator("split", params, split_table(rows + own()), SPLIT_ROLES)
    assert done.output.num_rows == 44


def test_a_carrier_with_its_siblings_in_one_run_stays_one_unit() -> None:
    """The legitimate group the refusal must not touch: same id, same run."""
    rows: list[Row] = [("c0", "c0", ALPHA_RUN, "g1")] * 4
    done = run_operator("split", SPLIT_V11, split_table(rows + own()), SPLIT_ROLES)
    placed = {r["_dw_split"] for r in done.output.to_pylist() if r["carrier_id"] == "c0"}
    assert len(placed) == 1


def test_one_plan_row_carried_by_two_runs_stays_one_unit() -> None:
    """One run per generator over one plan: each plan row has one carrier per run under one id, in
    one plan cell. That is the design, and the group keeps them in one split."""
    rows: list[Row] = [("c0", "c0", ALPHA_RUN, "g1"), ("c0", "c0", BETA_RUN, "g1")]
    done = run_operator("split", SPLIT_V11, split_table(rows + own()), SPLIT_ROLES)
    placed = {r["_dw_split"] for r in done.output.to_pylist() if r["carrier_id"] == "c0"}
    assert len(placed) == 1


def test_any_plan_column_that_differs_tells_two_plans_apart() -> None:
    rows: list[Row] = [("c0", "c0", ALPHA_RUN, "g1"), ("c0", "c0", BETA_RUN, "g1")]
    table = split_table(rows + own()).append_column(
        "planned_presented", pa.array(["yes", "no"] + ["yes"] * 40)
    )
    error = refused_split(table, SPLIT_V11, roles={**SPLIT_ROLES, "planned_presented": "metadata"})
    assert error.code == "carrier_id_collision" and error.details["ids"] == 1


def test_a_sibling_rewrites_its_kind_and_form_and_is_still_that_plan_row() -> None:
    """Over one plan, a decoy sibling of run alpha's carrier holds another ``kind`` and ``form`` than
    run beta's carrier of the same plan row. Those columns are not the plan cell."""
    rows: list[Row] = [
        ("c0", "c0", ALPHA_RUN, "g1"),
        ("c0", "c0", BETA_RUN, "g1"),
        ("c0", "c0", ALPHA_RUN, "g1"),
    ]
    table = split_table(rows + own())
    table = table.append_column("kind", pa.array(["pos", "pos", "lookalike"] + ["pos"] * 40))
    table = table.append_column("form", pa.array(["digits", "digits", "spelled"] + ["digits"] * 40))
    with_kind = {**SPLIT_ROLES, "kind": "metadata", "form": "metadata"}
    assert run_operator("split", SPLIT_V11, table, with_kind).output.num_rows == 43


def test_a_group_inside_one_run_needs_no_plan_columns() -> None:
    table = split_table([("c0", "c0", ALPHA_RUN, "g1")] * 4 + own()).drop_columns(["genre"])
    rows = {k: v for k, v in SPLIT_ROLES.items() if k != "genre"}
    assert run_operator("split", SPLIT_V11, table, rows).output.num_rows == 44


def test_rows_with_no_plan_columns_cannot_show_they_are_one_plan_row() -> None:
    table = colliding(10).drop_columns(["genre"])
    rows = {k: v for k, v in SPLIT_ROLES.items() if k != "genre"}
    assert refused_split(table, SPLIT_V11, roles=rows).code == "carrier_id_collision"


def test_a_row_with_no_run_shares_an_id_with_the_carrier_a_run_made_from_it() -> None:
    """A plan row keeps its id on the carrier its run makes, and real text has no run."""
    rows: list[Row] = [
        ("c0", "c0", None, "g1"),
        ("c0", "c0", ALPHA_RUN, "g2"),
        ("c1", "c1", None, "g1"),
        ("c1", "c1", BETA_RUN, "g2"),
    ]
    done = run_operator("split", SPLIT_V11, split_table(rows + own()), SPLIT_ROLES)
    assert done.output.num_rows == 44


def test_a_merge_whose_ids_are_repaired_passes() -> None:
    """D105's repair: one run's rows take their own ids before anything reads them."""
    rows: list[Row] = [(f"{ALPHA_RUN}-{i}", f"{ALPHA_RUN}-{i}", ALPHA_RUN, "g1") for i in range(25)]
    rows += [(f"{BETA_RUN}-{i}", f"{BETA_RUN}-{i}", BETA_RUN, "g2") for i in range(25)]
    assert run_operator("split", SPLIT_V11, split_table(rows), SPLIT_ROLES).output.num_rows == 50


def test_grouping_on_something_else_is_not_refused() -> None:
    table = colliding().append_column("topic", pa.array(["t"] * 45))
    params = {**SPLIT_V11, "group_columns": ["topic"]}
    done = run_operator("split", params, table, {**SPLIT_ROLES, "topic": "metadata"})
    assert done.output.num_rows == 45


def test_keeping_the_source_splits_groups_nothing_so_refuses_nothing() -> None:
    table = colliding()
    params = {
        "split_names": ["train", "test"],
        "held_out": [],
        "mode": "keep_source",
        "group_columns": ["carrier_id"],
    }
    assert run_operator("split", params, table, SPLIT_ROLES).output.num_rows == 45


def inserted_with_runs(
    ids: list[str], runs: list[str], genres: list[str] | None = None
) -> tuple[pa.Table, dict[str, str]]:
    """Real carriers through ``insert_element@1``, ready for ``slot_siblings@1``, each row with its
    carrier id and run, the first rows as given and every other row a carrier of its own."""
    table = carriers()
    inserted = run_operator("insert_element", insert_params("fixture-alpha"), table, roles(table))
    output = inserted.output
    n = output.num_rows
    assert n >= len(ids)
    columns: dict[str, list[str]] = {
        "carrier_id": [*ids, *[f"own{i}" for i in range(n - len(ids))]],
        "generation_run_id": [*runs, *[ALPHA_RUN] * (n - len(runs))],
    }
    if genres is not None:
        columns["genre"] = [*genres, *output.column("genre").to_pylist()[len(genres) :]]
    for name, values in columns.items():
        if name in output.schema.names:
            output = output.drop_columns([name])
        output = output.append_column(name, pa.array(values, pa.string()))
    return output, {
        **roles_after(inserted),
        "carrier_id": "metadata",
        "generation_run_id": "metadata",
    }


def test_slot_siblings_refuses_before_writing_a_pair_id_from_a_shared_id() -> None:
    table, row_roles = inserted_with_runs(
        ["c0", "c0"], [ALPHA_RUN, BETA_RUN], ["genre-one", "genre-two"]
    )
    with pytest.raises(StepFailed) as exc:
        run_operator("slot_siblings", sibling_params(), table, row_roles)
    assert exc.value.code == "carrier_id_collision"
    assert exc.value.details["rows_by_run"] == {ALPHA_RUN: 1, BETA_RUN: 1}
    assert exc.value.details["partial"] is True
    assert exc.value.message.startswith("At least 1 carrier_id value(s)")


def test_slot_siblings_sees_a_collision_across_its_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The step runs batch by batch: the first and last rows are in different batches."""
    monkeypatch.setattr(get_settings(), "operator_batch_rows", 2)
    table, row_roles = inserted_with_runs(["c0"], [ALPHA_RUN], ["genre-one"])
    n = table.num_rows
    ids = table.column("carrier_id").to_pylist()
    runs = table.column("generation_run_id").to_pylist()
    genres = table.column("genre").to_pylist()
    ids[n - 1], runs[n - 1], genres[n - 1] = "c0", BETA_RUN, "genre-two"
    for name, values in (("carrier_id", ids), ("generation_run_id", runs), ("genre", genres)):
        table = table.set_column(table.schema.get_field_index(name), name, pa.array(values))
    with pytest.raises(StepFailed) as exc:
        run_operator("slot_siblings", sibling_params(), table, row_roles)
    assert exc.value.code == "carrier_id_collision"


def test_slot_siblings_passes_two_runs_over_one_plan_row() -> None:
    table, row_roles = inserted_with_runs(["c0", "c0"], [ALPHA_RUN, BETA_RUN])
    # the second run's carrier of the first plan row: its plan cell is the first row's
    for name in table.schema.names:
        if name in ("genre", "placement", "framing") or name.startswith("planned_"):
            values = table.column(name).to_pylist()
            values[1] = values[0]
            table = table.set_column(table.schema.get_field_index(name), name, pa.array(values))
    done = run_operator("slot_siblings", sibling_params(), table, row_roles)
    assert done.output.num_rows >= table.num_rows


def test_slot_siblings_keeps_a_carrier_of_one_run_and_pairs_it() -> None:
    table, row_roles = inserted_with_runs([], [])
    done = run_operator("slot_siblings", sibling_params(), table, row_roles)
    rows = done.output.to_pylist()
    siblings = [r for r in rows if r["sibling_variant"] is not None]
    assert siblings, "the fixture must make siblings"
    assert all(r["pair_id"] == r["carrier_id"] for r in rows)
    assert {r["generation_run_id"] for r in siblings} == {ALPHA_RUN}
