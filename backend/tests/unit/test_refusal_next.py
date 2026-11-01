"""Every refusal of feature 017 carries ``details.next`` naming a tool of the LIVE MCP registry
(017 FTASKS 2.12, 11.6; FR-017.57). Created by slice 1; every later slice adds its codes here.

Each refusal is TRIGGERED (the pure rule or the operator's check), never listed by hand, and its
``next.tool`` is looked up in the registry the production MCP build path serves.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.operators.errors import StepFailed
from src.operators.native.curation.split import directed_of
from src.services.behaviour.vocabulary import VocabularyError, parse_shares, parse_vocabulary
from src.services.curation.held_out_proof import DirectedBy, Facts, prove
from src.services.curation.split_plan import SplitRefusal
from src.services.detector_sets import checks
from src.services.detector_sets.errors import DetectorSetError
from src.services.detector_sets.set_service import recorded_definition
from tests.integration.rules.conftest import live_tools
from tests.support.behaviour_fixtures import INDEX_ROLES, index_table, plan_document, plan_params
from tests.support.curation_fixtures import run_operator
from tests.unit.detector_sets.test_pure_rules import inputs

GENRE = DirectedBy(columns=("genre",), all_columns=("genre",), held_out_values={"genre": ("g4",)})
SPLIT = {
    "split_names": ["train", "ood"],
    "split_fractions": [1.0, 0.0],
    "held_out": ["ood"],
    "directing_columns": ["genre"],
    "held_out_values": ["g4"],
    "held_out_value_columns": ["genre"],
    "held_out_split_names": ["ood"],
}


def _refused_split(**change: Any) -> tuple[str, dict[str, Any]]:
    with pytest.raises(SplitRefusal) as exc:
        directed_of({**SPLIT, **change})
    return exc.value.code, exc.value.details


def triggered() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    proof = prove("ood", GENRE, Facts({("g4",): 2}, {("g4",): 1}))
    code, _, details = proof.refusal()
    out[code] = details
    code, _, details = prove("ood", GENRE, Facts({(None,): 2}, {("g1",): 1})).refusal()
    out[code] = details
    code, details = _refused_split(split_fractions=[0.5, 0.5])
    out[code] = details
    many = [f"g{i}" for i in range(17)]  # one more than BEHAVIOUR_MAX_HELD_OUT_VALUES (16)
    code, details = _refused_split(held_out_values=many, held_out_value_columns=["genre"] * 17)
    out[code] = details
    kept = checks.CrossVersionFacts(
        "set - ood",
        "v1",
        "kept v1",
        "set - train",
        "v2",
        "new v1",
        ("genre",),
        intersection=(("genre", "g4", 3),),
    )
    (d11,) = [o for o in checks.evaluate(inputs(cross_version=(kept,))) if o.code == "D-11"]
    assert d11.outcome == "refused"
    out[d11.details["code"]] = d11.details
    out.update(slice_2())
    out.update(slice_3())
    out.update(slice_4())
    out.update(review_r3())
    out.update(slice_5())
    out.update(slice_6())
    out.update(slice_7())
    return out


def _vocabulary_refusal(make: Any) -> tuple[str, dict[str, Any]]:
    with pytest.raises(VocabularyError) as exc:
        make()
    return exc.value.code, exc.value.details


def slice_2() -> dict[str, dict[str, Any]]:
    """Slice 2's refusals: the vocabulary (4.1), the plan (4.2), the set's definition (3.6)."""
    raw = plan_document("fixture-alpha")["vocabulary"]
    vocabulary = parse_vocabulary(json.dumps(raw))
    out: dict[str, dict[str, Any]] = {}
    code, details = _vocabulary_refusal(
        lambda: parse_shares(json.dumps({"framing": {"undeclared": 1.0}}), vocabulary)
    )
    out[code] = details
    code, details = _vocabulary_refusal(lambda: parse_vocabulary(json.dumps({**raw, "genres": []})))
    out[code] = details
    over = [f"g{i}" for i in range(65)]  # one more than BEHAVIOUR_MAX_VOCABULARY_VALUES (64)
    code, details = _vocabulary_refusal(
        lambda: parse_vocabulary(json.dumps({**raw, "genres": over}))
    )
    out[f"{code} (vocabulary)"] = details
    with pytest.raises(StepFailed) as plan:
        run_operator("carrier_plan", plan_params("fixture-beta", 5), index_table(4), INDEX_ROLES)
    out[plan.value.code] = plan.value.details
    with pytest.raises(DetectorSetError) as mismatch:
        recorded_definition(
            None,  # type: ignore[arg-type]
            SimpleNamespace(behaviour_key="key-one"),  # type: ignore[arg-type]
            {"key": "key-two"},
            [],
        )
    out[mismatch.value.code] = mismatch.value.details
    import src.operators.native.fixtures  # noqa: F401 - registers fx_report_params' "@input"
    from src.core.errors import AppError
    from src.services.step_params import resolve_inputs

    with pytest.raises(AppError) as unrecorded:
        resolve_inputs({"operator": "fx_report_params", "params": {"vocabulary": "@input"}}, {})
    out[unrecorded.value.code] = unrecorded.value.details
    return out


def _step_refusal(run: Any) -> tuple[str, dict[str, Any]]:
    with pytest.raises(StepFailed) as exc:
        run()
    return exc.value.code, exc.value.details


def slice_3() -> dict[str, dict[str, Any]]:
    """017 slice 3 (FTASKS 6.x): the label rules, insertion and the "@input" ambiguity."""
    from src.core.config import get_settings
    from src.core.errors import AppError
    from src.operators.native.behaviour.derive_labels import DeriveLabels
    from src.services.behaviour.truth_table import parse_truth_tables
    from src.services.step_params import resolve_inputs
    from tests.support.label_fixtures import (
        alpha_carriers,
        beta_carriers,
        insert_params,
        label_params,
        labels,
        pin,
        rule_set_document,
        vocabulary,
    )

    out: dict[str, dict[str, Any]] = {}
    alpha_vocabulary = parse_vocabulary(vocabulary("fixture-alpha"))
    tables = labels("fixture-alpha")["truth_tables"]
    holed = {**tables, "label": {**tables["label"], "rows": tables["label"]["rows"][:-1]}}
    code, details = _vocabulary_refusal(lambda: parse_truth_tables(holed, alpha_vocabulary))
    out[code] = details
    parsed = parse_truth_tables(tables, alpha_vocabulary)
    code, details = _vocabulary_refusal(
        lambda: parsed.label.evaluate(
            {"kind": "valid", "framing": "direct", "element_present": None}
        )
    )
    out[code] = details
    settings = get_settings()
    before = settings.behaviour_max_subquestions
    settings.behaviour_max_subquestions = 1
    try:
        code, details = _vocabulary_refusal(lambda: parse_vocabulary(vocabulary("fixture-beta")))
    finally:
        settings.behaviour_max_subquestions = before
    assert details["setting"] == "BEHAVIOUR_MAX_SUBQUESTIONS"
    out[f"{code} (sub-questions)"] = details
    roles = {"text": "content"}
    code, details = _step_refusal(
        lambda: run_operator(
            "insert_element", insert_params("fixture-alpha", pinned=False), alpha_carriers(), roles
        )
    )
    out[code] = details
    document = rule_set_document("fixture-alpha")
    (decoy,) = document["decoys"]
    by_validator = {**document, "decoys": [{**decoy, "generator": {"validator": {"id": "x"}}}]}
    code, details = _step_refusal(
        lambda: run_operator(
            "insert_element",
            insert_params("fixture-alpha", **pin("fixture-alpha", by_validator)),
            alpha_carriers(),
            roles,
        )
    )
    out[code] = details
    rendering = {**document["renderings"][0], "render": {"validator": {"id": "x"}}}
    unrunnable = {**document, "renderings": [rendering]}
    code, details = _step_refusal(
        lambda: run_operator(
            "insert_element",
            insert_params("fixture-alpha", **pin("fixture-alpha", unrunnable)),
            alpha_carriers(),
            roles,
        )
    )
    out[code] = details
    table = alpha_carriers()
    for params in (
        label_params("fixture-alpha", pinned=False),  # scan_needs_rule_set
        label_params("fixture-alpha", scan_column="r_fa_hit"),  # scan_column_not_spans
        label_params("fixture-alpha", vocabulary="@input"),  # input_not_recorded (operator)
    ):
        code, details = _step_refusal(
            lambda p=params: DeriveLabels().run(table, p, None)  # type: ignore[arg-type]
        )
        out[code if code != "input_not_recorded" else f"{code} (operator)"] = details
    code, details = _step_refusal(
        lambda: run_operator(
            "derive_labels",
            label_params("fixture-beta", pinned=False, vetoed_column="kind", target_checks="[]"),
            beta_carriers(),
            {"text": "content"},
        )
    )
    out[code] = details
    with pytest.raises(AppError) as ambiguous:
        resolve_inputs(
            {"operator": "derive_labels", "params": {"vocabulary": "@input"}},
            {"conflicts": {"behaviour_configuration": ["v1", "v2"]}},
        )
    out[ambiguous.value.code] = ambiguous.value.details
    return out


def review_r3() -> dict[str, dict[str, Any]]:
    """017 Review R3 (slices 3 - 4): a shadowed truth-table row, a decoy check with no column, a
    real-text row with no source."""
    import json

    import pyarrow as pa

    from src.services.behaviour.truth_table import parse_truth_tables
    from tests.fixtures import humor_pool as hp
    from tests.support.label_fixtures import (
        alpha_carriers,
        definition_text,
        insert_params,
        labels,
        pin,
        rule_set_document,
        vocabulary,
    )

    out: dict[str, dict[str, Any]] = {}
    tables = labels("fixture-alpha")["truth_tables"]
    shadowed = {**tables, "label": {"inputs": ["kind"], "rows": [
        {"when": {}, "value": False}, {"when": {"kind": "valid"}, "value": True}]}}  # fmt: skip
    code, details = _vocabulary_refusal(
        lambda: parse_truth_tables(shadowed, parse_vocabulary(vocabulary("fixture-alpha")))
    )
    out[code] = details
    roles = {"text": "content"}
    inserted = run_operator(
        "insert_element", insert_params("fixture-alpha"), alpha_carriers(), roles
    ).output
    doc = rule_set_document("fixture-alpha")
    params = {
        "label_column": "discloses",
        "vocabulary": vocabulary("fixture-alpha"),
        "truth_tables": json.dumps(tables),
        **pin("fixture-alpha", doc),
    }
    code, details = _step_refusal(lambda: run_operator("derive_labels", params, inserted, roles))
    out[code] = details
    table = hp.with_system_columns([{"text": "t", "value": "v"}], source_id="x")
    table = table.set_column(
        table.schema.get_field_index("_dw_source_id"),
        "_dw_source_id",
        pa.array([None], pa.string()),
    )
    real = {
        "mode": "real_text",
        "element_column": "value",
        "vocabulary": vocabulary("fixture-beta"),
        "definition": definition_text("fixture-beta"),
        "real_text_values": json.dumps(
            {"genre": "forum", "kind": "request", "framing": "describes", "form": "prose"}
        ),
    }
    code, details = _step_refusal(lambda: run_operator("insert_element", real, table, roles))
    out[code] = details
    return out


def slice_4() -> dict[str, dict[str, Any]]:
    """017 slice 4 (FTASKS 7.x): the screen and the calibration-negatives basis."""
    from src.core.config import get_settings
    from src.services.detector_sets.set_service import _validate_behaviour_basis
    from tests.fixtures import humor_pool as hp

    out: dict[str, dict[str, Any]] = {}
    table = hp.with_system_columns(
        [{"text": "a row", "flag": "yes"}], source_id="src-x", content=("text",)
    )
    roles = {"text": "content", "flag": "metadata"}
    for flags in (["absent"], ["flag"]):
        code, details = _step_refusal(
            lambda f=flags: run_operator("screen_filter", {"flag_columns": f}, table, roles)
        )
        out[code] = details
    spec = {"role": "calibration_negatives", "negatives_basis": {"kind": "assumed_negative"}}
    for basis, label in (
        ({"kind": "assumed_negative"}, "negatives_basis_incomplete"),
        (
            {
                "kind": "assumed_negative",
                "second_opinion": {"sample_rows": 1, "estimated_positive_rate": 0.0},
            },
            "negatives_basis_incomplete (setting)",
        ),
    ):
        with pytest.raises(DetectorSetError) as refused:
            _validate_behaviour_basis(
                None,  # type: ignore[arg-type]
                SimpleNamespace(id="v", number=1),  # type: ignore[arg-type]
                {**spec, "negatives_basis": basis},
                "key-one",
            )
        out[label] = refused.value.details
    assert out["negatives_basis_incomplete (setting)"]["limit"] == int(
        get_settings().second_opinion_no_rule_negatives
    )
    return out


def slice_5() -> dict[str, dict[str, Any]]:
    """017 slice 5 (FTASKS 8.x): code-made siblings."""
    from tests.unit.operators.test_slot_siblings import run_siblings

    out: dict[str, dict[str, Any]] = {}
    code, details = _step_refusal(
        lambda: run_siblings(
            variants=json.dumps(
                [{"kind": "vetoed", "ref": "nope", "column": "masked_value", "as_kind": "masked"}]
            )
        )
    )
    out[code] = details
    with pytest.raises(StepFailed) as after:
        from tests.support.label_fixtures import roles_after, rule_set_params
        from tests.unit.operators.test_slot_siblings import sibling_params

        inserted, _ = run_siblings()
        facts = run_operator(
            "rule_set", rule_set_params("fixture-alpha"), inserted.output, roles_after(inserted)
        )
        run_operator("slot_siblings", sibling_params(), facts.output, roles_after(facts))
    out[after.value.code] = after.value.details
    return out


def slice_6() -> dict[str, dict[str, Any]]:
    """017 slice 6 (FTASKS 9.x): the paid-classifier refusals and the compared column."""
    from pathlib import Path

    import pyarrow as pa
    import pyarrow.parquet as pq

    from src.core.errors import AppError
    from src.services.label_run_service import _check_target
    from src.services.labeling import cost

    out: dict[str, dict[str, Any]] = {}
    paid = cost.basis("typesafe_systemone", None, 10)
    priced = cost.basis("typesafe_systemone", 0.001, 10)
    for basis, kw, label in (
        (paid, {"ceiling": 1.0}, "classifier_unpriced"),
        (priced, {"ceiling": None}, "classifier_spend_ceiling"),
        (priced, {"ceiling": 0.001}, "classifier_spend_ceiling (passed)"),
        ({**priced, "rows_planned": 99}, {"ceiling": 5.0, "cap": 5}, "classifier_cap_exceeded"),
    ):
        args: dict[str, Any] = {"cap": 2500, "spent": 0.0, "role": "classifier", **kw}
        with pytest.raises(AppError) as refused:
            cost.check(basis, **args)
        assert refused.value.code == label.split(" ")[0]
        out[label] = refused.value.details
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "t.parquet"
        pq.write_table(pa.table({"text": ["a"], "deployed_truth": [True]}), path)
        for column in ("deployed_truth", "absent"):
            with pytest.raises(AppError) as refused:
                _check_target([path], column)
            out[f"target_column_invalid ({column})"] = refused.value.details
    return out


def slice_7() -> dict[str, dict[str, Any]]:
    """017 slice 7 (FTASKS 10.x): the multi-set send's cap."""
    from src.services.detector_sets import send_service

    many = [f"dts_{i}" for i in range(17)]  # one more than DETECTOR_MAX_SETS_PER_SEND (16)
    with pytest.raises(DetectorSetError) as refused:
        send_service._group_cap(many)
    return {"cap_exceeded (sets per send)": refused.value.details}


async def test_every_017_refusal_names_a_live_tool() -> None:
    found = triggered()
    assert set(found) == {
        "held_out_not_disjoint",
        "held_out_value_null",
        "directed_split_name_conflict",
        "cap_exceeded",
        "cross_version_not_disjoint",
        # slice 2
        "vocabulary_value_undeclared",
        "vocabulary_invalid",
        "cap_exceeded (vocabulary)",
        "plan_size_mismatch",
        "behaviour_definition_mismatch",
        "input_not_recorded",
        # slice 3
        "truth_table_not_total",
        "truth_table_input_undeclared",
        "cap_exceeded (sub-questions)",
        "decoy_not_declared",
        "decoy_not_runnable",
        "rendering_not_runnable",
        "scan_needs_rule_set",
        "scan_column_not_spans",
        "input_not_recorded (operator)",
        "fact_not_boolean",
        "input_ambiguous",
        # slice 4
        "screen_column_missing",
        "screen_column_not_boolean",
        "negatives_basis_incomplete",
        "negatives_basis_incomplete (setting)",
        # Review R3
        "truth_table_row_shadowed",
        "decoy_check_missing",
        "real_text_without_source",
        # slice 5
        "sibling_variant_undeclared",
        "siblings_after_labels",
        # slice 6
        "classifier_unpriced",
        "classifier_spend_ceiling",
        "classifier_spend_ceiling (passed)",
        "classifier_cap_exceeded",
        "target_column_invalid (deployed_truth)",
        "target_column_invalid (absent)",
        # slice 7
        "cap_exceeded (sets per send)",
    }
    tools = await live_tools()
    for code, details in found.items():
        assert details["next"]["tool"] in tools, (code, details["next"])
