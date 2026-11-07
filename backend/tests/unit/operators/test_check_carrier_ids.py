"""``check_carrier_ids@1``: a merge of colliding runs is refused before anything is labelled (dry
run, 2026-11-01). Through 003's real executor and the live registry."""

from __future__ import annotations

import pytest

from src.operators.errors import StepFailed
from tests.support.curation_fixtures import registry, run_operator
from tests.unit.operators.test_carrier_id_collisions import (
    ALPHA_RUN,
    BETA_RUN,
    SPLIT_ROLES,
    colliding,
    own,
    split_table,
)


def test_the_live_registry_serves_it() -> None:
    assert registry().current_version("check_carrier_ids") == "1.0.0"


def test_it_refuses_an_id_two_runs_hold_and_names_counts_and_runs() -> None:
    with pytest.raises(StepFailed) as exc:
        run_operator("check_carrier_ids", {}, colliding(), SPLIT_ROLES)
    assert exc.value.code == "carrier_id_collision"
    assert "2 carrier_id value(s)" in str(exc.value)


def test_clean_rows_pass_unchanged() -> None:
    table = split_table(own(20))
    ran = run_operator("check_carrier_ids", {}, table, SPLIT_ROLES)
    assert ran.output.num_rows == 20
    assert ran.output.column("carrier_id").to_pylist() == table.column("carrier_id").to_pylist()
    assert ran.output.column("text").to_pylist() == table.column("text").to_pylist()


def test_one_plan_written_by_two_generators_shares_ids_rightly() -> None:
    table = split_table(
        [("c0", "c0", ALPHA_RUN, "g1"), ("c0", "c0", BETA_RUN, "g1"), *own(10)],
    )
    assert run_operator("check_carrier_ids", {}, table, SPLIT_ROLES).output.num_rows == 12


@pytest.mark.parametrize("column", ["carrier_id", "generation_run_id"])
def test_it_never_passes_because_a_column_is_gone(column: str) -> None:
    table = colliding().drop_columns([column])
    roles = {k: v for k, v in SPLIT_ROLES.items() if k != column}
    with pytest.raises(StepFailed) as exc:
        run_operator("check_carrier_ids", {}, table, roles)
    assert exc.value.code == "carrier_columns_missing"
    assert exc.value.details["missing"] == [column]
