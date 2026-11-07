"""``carrier_plan@1.1.0``: two different plans can never share a carrier id (ENH-006 D111).

``carrier_id`` was the plan row's key, and that key hashes the index and nothing else. A top-up
planned inside the full plan's index range gave 2,659 rows the id of an unrelated row, and pairs and
split groups join on it. Through 003's real executor and the live registry.
"""

from __future__ import annotations

from typing import Any

import pyarrow as pa
import pytest

from tests.support.behaviour_fixtures import INDEX_ROLES, index_table, plan_params
from tests.support.curation_fixtures import registry, run_operator

KEY = "fixture-alpha"
ROWS = 300


def plan(version: str = "1.1.0", *, table: pa.Table | None = None, **over: Any) -> dict[str, str]:
    """Row key to carrier id, on the rows of one plan."""
    ran = run_operator(
        "carrier_plan",
        plan_params(KEY, ROWS, **over),
        table if table is not None else index_table(ROWS),
        INDEX_ROLES,
        version=version,
        seed=7,
    )
    output = ran.output
    return dict(
        zip(
            output.column("_dw_row_key").to_pylist(),
            output.column("carrier_id").to_pylist(),
            strict=True,
        )
    )


def from_source(source: str) -> pa.Table:
    table = index_table(ROWS)
    at = table.schema.get_field_index("_dw_source_id")
    column = pa.array([source] * ROWS, table.schema.field(at).type)
    return table.set_column(at, "_dw_source_id", column)


def test_the_registry_serves_both_versions_and_new_recipes_get_the_new_one() -> None:
    reg = registry()
    assert reg.entry("carrier_plan", "1.0.0") is not None
    assert reg.entry("carrier_plan", "1.1.0") is not None
    assert reg.current_version("carrier_plan") == "1.1.0"


def test_two_plans_over_one_index_range_share_no_id() -> None:
    """The dry run's collision: the same index values, in two plans with different sources."""
    first = plan(table=from_source("the-full-plan-index"))
    second = plan(table=from_source("the-top-up-index"))
    assert set(first) == set(second), "the row keys are the index's, so they do match"
    assert not set(first.values()) & set(second.values())


def test_the_old_version_is_unchanged_and_does_collide() -> None:
    """The control: this is what 1.1.0 removes, and a recipe pinned to 1.0.0 still reproduces."""
    first = plan("1.0.0", table=from_source("the-full-plan-index"))
    second = plan("1.0.0", table=from_source("the-top-up-index"))
    assert first == second
    assert all(carrier == key for key, carrier in first.items())


@pytest.mark.parametrize("change", ["rows", "templated_ceiling", "seed", "shares"])
def test_anything_that_makes_it_another_plan_changes_every_id(change: str) -> None:
    base_params = plan_params(KEY, ROWS)
    rows = ROWS
    over: dict[str, Any] = {}
    seed = 7
    if change == "rows":
        rows = 301
    elif change == "templated_ceiling":
        over["templated_ceiling"] = float(base_params["templated_ceiling"]) / 2
    elif change == "seed":
        seed = 8
    ran = run_operator(
        "carrier_plan",
        plan_params(KEY, rows, **over) if change != "shares" else _other_shares(),
        index_table(rows),
        INDEX_ROLES,
        version="1.1.0",
        seed=seed,
    )
    other = set(ran.output.column("carrier_id").to_pylist())
    assert not other & set(plan().values()), change


def _other_shares() -> dict[str, Any]:
    import json

    params = plan_params(KEY, ROWS)
    shares = json.loads(params["shares"])
    dimension = next(k for k, v in shares.items() if isinstance(v, dict) and len(v) >= 2)
    names = sorted(shares[dimension])
    moved = 0.01 if shares[dimension][names[1]] > 0.01 else 0.0
    shares[dimension][names[0]] = round(shares[dimension][names[0]] + moved, 6)
    shares[dimension][names[1]] = round(shares[dimension][names[1]] - moved, 6)
    return {**params, "shares": json.dumps(shares)}


def test_ids_are_unique_within_a_plan_and_the_same_plan_rebuilds_the_same() -> None:
    first = plan()
    assert len(set(first.values())) == ROWS
    assert plan() == first, "a rebuild must find the same carriers"


def test_the_definitions_read_time_does_not_change_the_plan() -> None:
    import json

    params = plan_params(KEY, ROWS)
    definition = json.loads(params["definition"])
    definition["read_at"] = "2030-01-01T00:00:00+00:00"
    moved = run_operator(
        "carrier_plan",
        {**params, "definition": json.dumps(definition)},
        index_table(ROWS),
        INDEX_ROLES,
        version="1.1.0",
        seed=7,
    )
    assert set(moved.output.column("carrier_id").to_pylist()) == set(plan().values())


def test_the_row_key_stays_the_lineages_and_only_the_id_changes() -> None:
    assert set(plan("1.1.0")) == set(plan("1.0.0"))
