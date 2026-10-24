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
    }
    tools = await live_tools()
    for code, details in found.items():
        assert details["next"]["tool"] in tools, (code, details["next"])
