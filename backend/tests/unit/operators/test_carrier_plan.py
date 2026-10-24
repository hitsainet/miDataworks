"""``carrier_plan@1`` (017 FTASKS 4.2, 4.6, 4.7; FR-017.12, FR-017.13, FR-017.17, FR-017.21).

Through 003's real executor and the live registry, on BOTH fixture behaviours: the same code plans
fixture-alpha's slot carriers and fixture-beta's instructed carriers from their own vocabularies.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import pytest

from src.operators.errors import StepFailed
from src.services.behaviour.vocabulary import (
    CELL_DIMENSIONS,
    joint_cells,
    largest_remainder,
    parse_shares,
    parse_vocabulary,
)
from tests.support.behaviour_fixtures import (
    BEHAVIOURS,
    INDEX_ROLES,
    index_table,
    plan_document,
    plan_params,
)
from tests.support.curation_fixtures import registry, run_operator


def run_plan(key: str, rows: int, **over: Any) -> tuple[Any, dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    ran = run_operator(
        "carrier_plan",
        plan_params(key, rows, **over),
        index_table(rows),
        INDEX_ROLES,
        report_sink=reports,
    )
    (report,) = reports
    return ran, report


@pytest.mark.parametrize("key", BEHAVIOURS)
def test_exact_counts_from_the_declared_shares(key: str) -> None:
    rows = 600
    ran, report = run_plan(key, rows)
    out = ran.output.to_pylist()
    doc = plan_document(key)
    vocabulary = parse_vocabulary(json.dumps(doc["vocabulary"]))
    shares = parse_shares(json.dumps(doc["shares"]), vocabulary)
    assert len(out) == rows
    cells = Counter(tuple(r[d] for d in CELL_DIMENSIONS) for r in out)
    expected = largest_remainder(joint_cells(shares, CELL_DIMENSIONS), rows)
    assert dict(cells) == {c: n for c, n in expected.items() if n}
    for dimension in ("kind", "form"):
        assert Counter(r[dimension] for r in out) == Counter(
            {v: n for v, n in largest_remainder(shares[dimension], rows).items() if n}
        )
    for sq in vocabulary.sub_questions:
        column = f"planned_{sq.name}"
        assert Counter(r[column] for r in out) == Counter(
            {v: n for v, n in largest_remainder(shares[f"sq:{sq.name}"], rows).items() if n}
        )
    # every row says what it is for (FR-017.12): key, definition version, carrier id
    assert {r["behaviour_key"] for r in out} == {key}
    assert {r["definition_version"] for r in out} == {"1"}
    assert [r["carrier_id"] for r in out] == [r["_dw_row_key"] for r in out]
    for r in out:
        assert all(r[d] is not None for d in (*CELL_DIMENSIONS, "kind", "form")), r
        assert r["slot"] == ("{{SLOT}}" if r["placement"] in ("start", "middle", "end") else None)
        assert r["text"] is None and r["_dw_origin"] == "source"
    roles = ran.meta["output_column_roles"]
    assert roles["text"] == "content" and roles["genre"] == "metadata"
    configuration = report["behaviour_configuration"]
    assert configuration["vocabulary"] == vocabulary.as_dict() and configuration["rows"] == rows
    assert configuration["shares"] == shares
    assert report["behaviour_definition"]["key"] == key
    assert report["split_roles"]["test"] == {"held_out": True, "declared_empty": True}
    assert report["census"]["cells_short"] == 0


@pytest.mark.parametrize("key", BEHAVIOURS)
def test_seeded_and_order_free(key: str) -> None:
    first, _ = run_plan(key, 300)
    again, _ = run_plan(key, 300)
    columns = [c for c in first.output.schema.names if not c.startswith("_dw_")]
    assert first.output.select(columns).to_pylist() == again.output.select(columns).to_pylist()
    reversed_input = run_operator(
        "carrier_plan",
        plan_params(key, 300),
        index_table(300).take(list(range(299, -1, -1))),
        INDEX_ROLES,
    )
    by_key = {r["carrier_id"]: r for r in first.output.to_pylist()}
    for row in reversed_input.output.to_pylist():
        assert {c: row[c] for c in columns} == {c: by_key[row["carrier_id"]][c] for c in columns}
    other_seed = run_operator(
        "carrier_plan", plan_params(key, 300), index_table(300), INDEX_ROLES, seed=7
    )
    assert other_seed.output.select(columns).to_pylist() != first.output.select(columns).to_pylist()


def test_the_templated_minority_is_marked_and_counted() -> None:
    """FR-017.21: template-route rows carry generator = template and count in the share."""
    ran, report = run_plan("fixture-alpha", 400)
    generators = Counter(r["generator"] for r in ran.output.to_pylist())
    assert generators["template"] == 40 and generators[None] == 360  # templated_share 0.1
    templated = report["census"]["templated"]
    assert templated == {"rows": 40, "share": 0.1, "ceiling": 0.15, "over_ceiling": False}
    over, over_report = run_plan("fixture-alpha", 400, templated_share=0.2)
    assert over.output.num_rows == 400  # flagged, never refused
    assert over_report["census"]["templated"]["over_ceiling"] is True
    assert "templated_over_ceiling" in [f["code"] for f in over_report["census"]["flags"]]
    _, beta = run_plan("fixture-beta", 200)
    assert beta["census"]["templated"]["rows"] == 0
    assert beta["census"]["decoys"] == ["near_miss"]
    assert report["census"]["decoys"] == "no decoys declared"


def test_a_plan_over_an_index_of_another_size_is_refused() -> None:
    with pytest.raises(StepFailed) as refused:
        run_operator("carrier_plan", plan_params("fixture-beta", 50), index_table(49), INDEX_ROLES)
    assert refused.value.code == "plan_size_mismatch"
    assert refused.value.details["next"]["tool"] == "dataworks_create_index_source"


def test_validation_names_what_is_wrong() -> None:
    reg = registry()
    version = reg.current_version("carrier_plan")
    assert version is not None
    missing = reg.validate_params("carrier_plan", version, {"rows": 10})
    joined = " ".join(missing)
    for name in ("definition", "vocabulary", "templated_ceiling"):
        assert name in joined, missing
    doc = plan_document("fixture-alpha")
    bad = plan_params(
        "fixture-alpha",
        10,
        shares=json.dumps({"framing": {"direct": 0.5, "undeclared-framing": 0.5}}),
    )
    (problem,) = reg.validate_params("carrier_plan", version, bad)
    assert "vocabulary_value_undeclared" in problem and "'undeclared-framing'" in problem
    assert problem.startswith("/vocabulary")
    unresolved = plan_params("fixture-alpha", 10, definition=json.dumps({"key": "Bad Key"}))
    (problem,) = reg.validate_params("carrier_plan", version, unresolved)
    assert problem.startswith("/definition") and "vocabulary_invalid" in problem
    assert reg.validate_params("carrier_plan", version, plan_params("fixture-alpha", 10)) == []
    assert doc["vocabulary"]["genres"]


def test_a_30000_row_plan_validates_and_plans_exactly() -> None:
    """FR-017.19: plan sizes are per-behaviour parameters; a 30,000-row plan is accepted."""
    reg = registry()
    version = reg.current_version("carrier_plan")
    assert version is not None
    params = plan_params("fixture-alpha", 30_000)
    assert reg.validate_params("carrier_plan", version, params) == []
    ran, report = run_plan("fixture-alpha", 30_000)
    assert ran.output.num_rows == 30_000 and report["census"]["rows"] == 30_000
    assert report["census"]["templated"]["rows"] == 3_000
