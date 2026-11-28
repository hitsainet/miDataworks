"""A merge's plan is the sum of its input plans (FU-7a gap (f), 2026-11-27).

M1 merged four pass-1 versions (19,891 rows) and recorded the FIRST input's configuration, so its
census read "5 of 648 cells short" against P1-L's 7,800-row plan, and every version built from M1
repeated it. The fixtures here are real ``carrier_plan@1`` configurations whose rows and shares
differ, so a merge that kept one input's plan cannot equal the sum.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from src.operators.native.behaviour.carrier_plan import configuration_of
from src.services.behaviour.census import census, merge_configurations, planned_cells
from tests.support.behaviour_fixtures import plan_params


def config(key: str, rows: int, **over: Any) -> dict[str, Any]:
    _, out = configuration_of(plan_params(key, rows, **over))
    return dict(json.loads(json.dumps(out)))


def genre_heavy(key: str, rows: int, genre: str) -> dict[str, Any]:
    """A plan whose genre shares put almost every row in one genre (the reserve's OCR/voice)."""
    base = config(key, rows)
    genres = base["vocabulary"]["genres"]
    shares = {g: (0.9 if g == genre else 0.1 / (len(genres) - 1)) for g in genres}
    doc = json.loads(plan_params(key, rows)["shares"])
    return config(key, rows, shares=json.dumps({**doc, "genre": shares}))


def test_identical_configurations_are_one_plan() -> None:
    a = config("fixture-alpha", 60)
    merged, warning = merge_configurations([("v1", a), ("v2", dict(a))])
    assert merged == a and warning is None


def test_one_input_keeps_its_configuration() -> None:
    a = config("fixture-alpha", 60)
    assert merge_configurations([("v1", a)]) == (a, None)
    assert merge_configurations([]) == (None, None)


def test_different_plans_of_one_vocabulary_are_summed_cell_by_cell() -> None:
    genres = config("fixture-alpha", 10)["vocabulary"]["genres"]
    a = genre_heavy("fixture-alpha", 300, genres[0])
    b = genre_heavy("fixture-alpha", 100, genres[1])
    merged, warning = merge_configurations([("v-a", a), ("v-b", b)])
    assert warning is None and merged is not None
    assert merged["rows"] == 400
    assert [(p["from_version_id"], p["rows"]) for p in merged["plans"]] == [
        ("v-a", 300),
        ("v-b", 100),
    ]
    assert merged["vocabulary"] == a["vocabulary"]
    want = Counter(planned_cells(a)) + Counter(planned_cells(b))
    assert {c: n for c, n in planned_cells(merged).items() if n} == dict(want)
    assert planned_cells(merged) != planned_cells(a)  # not the first input's plan
    g0 = merged["shares"]["genre"][genres[0]]
    assert g0 == pytest.approx(0.9 * 0.75 + a["shares"]["genre"][genres[1]] * 0.25)
    # a census of exactly the planned rows is short nowhere and says what it summed
    cells = [c for c, n in want.items() for _ in range(n)]
    columns = {
        d: [c[i] for c in cells]
        for i, d in enumerate(("genre", "placement", "framing", "length_band"))
    }
    columns["generator"] = [None] * len(cells)
    done = census(merged, columns)
    assert done["cells_short"] == 0 and done["planned_rows"] == 400 and done["plans_summed"] == 2
    short = census(merged, {k: v[:-50] for k, v in columns.items()})
    (flag,) = [f for f in short["flags"] if f["code"] == "cell_short"]
    assert "the sum of 2 input plans" in flag["message"]


def test_a_merge_of_merges_sums_each_original_plan_once() -> None:
    genres = config("fixture-alpha", 10)["vocabulary"]["genres"]
    a = genre_heavy("fixture-alpha", 300, genres[0])
    b = genre_heavy("fixture-alpha", 100, genres[1])
    c = config("fixture-alpha", 50)
    ab, _ = merge_configurations([("v-a", a), ("v-b", b)])
    ac, _ = merge_configurations([("v-a", a), ("v-c", c)])
    assert ab is not None and ac is not None
    both, warning = merge_configurations([("m1", ab), ("m2", ac)])
    assert warning is None and both is not None
    assert [p["from_version_id"] for p in both["plans"]] == ["v-a", "v-b", "v-c"]
    assert both["rows"] == 450


def test_plans_of_different_vocabularies_are_not_summed() -> None:
    a, b = config("fixture-alpha", 60), config("fixture-beta", 60)
    merged, warning = merge_configurations([("v-a", a), ("v-b", b)])
    assert merged is None and warning is not None
    assert warning["code"] == "behaviour_configuration_not_merged"
    assert warning["details"]["versions"] == ["v-a", "v-b"]
    assert {"behaviour_key", "vocabulary"} <= set(warning["details"]["fields"])
    ceiling = config("fixture-alpha", 60, templated_ceiling=0.5)
    merged, warning = merge_configurations([("v-a", a), ("v-c", ceiling)])
    assert merged is None and warning is not None
    assert warning["details"]["fields"] == ["templated_ceiling"]


def test_a_recorded_configuration_plans_the_cells_its_plan_step_planned() -> None:
    """Pre-existing, found with gap (f): ``largest_remainder`` breaks ties by declaration order,
    and a configuration read back from JSONB has SORTED keys, so the version census of a 40-row
    plan put tied rows in other cells than the plan step did and read 2 cells short of itself."""
    from src.services.behaviour.vocabulary import CELL_DIMENSIONS
    from tests.support.behaviour_fixtures import INDEX_ROLES, index_table
    from tests.support.curation_fixtures import run_operator

    ran = run_operator(
        "carrier_plan", plan_params("fixture-alpha", 40), index_table(40), INDEX_ROLES
    )
    recorded = json.loads(
        json.dumps(ran.meta["reports"][0]["behaviour_configuration"], sort_keys=True)
    )
    assert (
        list(recorded["shares"]["placement"])
        != config("fixture-alpha", 40)["vocabulary"]["placements"]
    )
    out = ran.output
    columns = {c: out.column(c).to_pylist() for c in (*CELL_DIMENSIONS, "generator")}
    assert census(recorded, columns)["cells_short"] == 0
