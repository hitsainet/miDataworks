"""``sample`` through 003's real executor and the LIVE registry (novice walk 2026-10-19, finding 4).

The humour pool has 11,805 rows with unequal classes, so a sample's counts are the operator's work,
not the fixture's. Determinism is checked on the row keys kept, not on counts alone: two different
draws of the same size have the same count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.recipe_service import validate_body
from tests.fixtures import humor_pool as hp
from tests.support.curation_fixtures import registry, run_operator

N = 11_805


def _kept(ran) -> list[str]:  # type: ignore[no-untyped-def]
    return sorted(ran.output.column("_dw_row_key").to_pylist())


def test_count_keeps_exactly_that_many_and_logs_every_other_row(data_dir: Path) -> None:
    pool = hp.labelled_pool()
    assert pool.num_rows == N
    sink: list[dict] = []
    ran = run_operator("sample", {"mode": "count", "count": 2500}, pool, hp.ROLES, report_sink=sink)
    assert ran.output.num_rows == 2500
    dropped = ran.events_by_reason("not_sampled")
    assert len(dropped) == N - 2500
    # provenance: every input row is either kept or has a drop naming its rank in the draw
    kept = set(ran.output.column("_dw_row_key").to_pylist())
    gone = {e["row_key"] for e in dropped}
    assert not kept & gone
    assert len(kept) + len(gone) == len(set(pool.column("_dw_row_key").to_pylist()))
    assert all(e["statistic_name"] == "sample_rank" for e in dropped)
    assert all(e["statistic_value"] >= 2500 for e in dropped)
    assert '"comparator":"kept_below"' in dropped[0]["threshold"]
    assert '"value":2500' in dropped[0]["threshold"]
    report = sink[0]
    assert (
        report["rows_in"] == N
        and report["rows_kept"] == 2500
        and report["rows_dropped"] == N - 2500
    )
    assert report["seed_source"] == "step_seed" and report["seed"] == 20261005


def test_same_seed_same_rows_other_seed_other_rows(data_dir: Path) -> None:
    params = {"mode": "count", "count": 1000}
    a = run_operator("sample", params, hp.labelled_pool(), hp.ROLES, seed=7)
    b = run_operator("sample", params, hp.labelled_pool(), hp.ROLES, seed=7)
    c = run_operator("sample", params, hp.labelled_pool(), hp.ROLES, seed=8)
    assert _kept(a) == _kept(b)
    assert _kept(a) != _kept(c)


def test_a_seed_parameter_overrides_the_step_seed(data_dir: Path) -> None:
    params = {"mode": "count", "count": 1000, "seed": 42}
    a = run_operator("sample", params, hp.labelled_pool(), hp.ROLES, seed=1)
    b = run_operator("sample", params, hp.labelled_pool(), hp.ROLES, seed=2)
    assert _kept(a) == _kept(b)


def test_the_draw_does_not_depend_on_row_order(data_dir: Path) -> None:
    pool = hp.labelled_pool()
    reversed_pool = pool.take(list(range(pool.num_rows - 1, -1, -1)))
    params = {"mode": "count", "count": 500}
    assert _kept(run_operator("sample", params, pool, hp.ROLES)) == _kept(
        run_operator("sample", params, reversed_pool, hp.ROLES)
    )


@pytest.mark.parametrize(
    ("fraction", "expected"),
    [(0.5, 5903), (0.1, 1181), (1.0, N), (1e-9, 1)],  # half up; never zero of a non-empty input
)
def test_fraction_rounds_half_up(data_dir: Path, fraction: float, expected: int) -> None:
    ran = run_operator(
        "sample", {"mode": "fraction", "fraction": fraction}, hp.labelled_pool(), hp.ROLES
    )
    assert ran.output.num_rows == expected


def test_a_count_above_the_input_keeps_it_whole_and_says_so(data_dir: Path) -> None:
    sink: list[dict] = []
    ran = run_operator("sample", {"count": 50_000}, hp.labelled_pool(), hp.ROLES, report_sink=sink)
    assert ran.output.num_rows == N and ran.events_by_reason("not_sampled") == []
    assert sink[0]["kept_whole"] is True


@pytest.mark.parametrize(
    ("params", "pointer"),
    [
        ({"mode": "count"}, "/count"),
        ({"mode": "fraction"}, "/fraction"),
        ({"mode": "count", "count": 5, "fraction": 0.5}, "/fraction"),
        ({"mode": "fraction", "fraction": 0.5, "count": 5}, "/count"),
    ],
)
def test_the_registry_refuses_a_missing_or_second_size(params: dict, pointer: str) -> None:
    errors = registry().validate_params_detailed("sample", "1.0.0", params)
    assert [e["pointer"] for e in errors] == [pointer], errors


def test_a_recipe_with_an_incomplete_sample_is_refused_at_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services import operator_port

    monkeypatch.setattr(operator_port, "_registry", registry())  # the registry production installs
    body = {
        "format": "dw.recipe/v1",
        "steps": [{"operator": "sample", "version": "1.0.0", "params": {"mode": "count"}}],
    }
    result, _ = validate_body(body)
    assert not result.valid
    assert "sampling by count needs a number of rows" in result.steps[0].errors[0].message
    ok = {
        "format": "dw.recipe/v1",
        "steps": [{"operator": "sample", "version": "1.0.0", "params": {"count": 25000}}],
    }
    assert validate_body(ok)[0].valid
