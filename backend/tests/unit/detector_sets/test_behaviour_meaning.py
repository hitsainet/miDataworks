"""XR-5 and the two optional registration keys, pure (015 FR-015.3 - FR-015.5; FTDD 015 T6, T7)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from src.services.detector_sets import capabilities, plan

FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "detector_sets" / "mistudio_openapi.json"
)
BASE = "http://mistudio.test"


def _doc(*props: str, schema: str = capabilities.MANIFEST_SCHEMA) -> dict[str, Any]:
    doc = json.loads(FIXTURE.read_text())
    target = doc["components"]["schemas"].setdefault(schema, {"properties": {}})
    for p in props:
        target.setdefault("properties", {})[p] = {"type": "string"}
    return doc


def test_the_wire_names_are_d37s() -> None:
    assert (capabilities.WIRE_BEHAVIOUR, capabilities.WIRE_MEANING) == (
        "behaviour",
        "positive_meaning",
    )
    assert "behaviour_meaning" in capabilities.NAMES


@pytest.mark.parametrize(
    ("props", "served"),
    [
        (("behaviour", "positive_meaning"), True),
        (("behaviour",), False),
        (("positive_meaning",), False),
        ((), False),
    ],
)
def test_xr5_needs_both_properties(props: tuple[str, ...], served: bool) -> None:
    caps = capabilities.from_openapi(BASE, _doc(*props))
    assert caps.behaviour_meaning is served
    reason = caps.as_dict()["reasons"]["behaviour_meaning"]
    assert (reason == "served") is served


def test_the_properties_on_another_schema_do_not_count() -> None:
    doc = _doc("behaviour", "positive_meaning", schema="ProbeRunCreate")
    assert capabilities.from_openapi(BASE, doc).behaviour_meaning is False


ARGS: dict[str, Any] = {
    "name": "v",
    "dataset_id": "ds",
    "config": None,
    "split": "train",
    "input_column": "text",
    "label_column": "label",
    "label_mapping": {"1": "positive", "0": "negative"},
    "role": "train",
    "pair_column": None,
}


def test_served_with_values_adds_both_keys() -> None:
    body = plan.registration_body(
        **ARGS, behaviour="pii", positive_meaning="Has personal data", meaning_served=True
    )
    assert body["behaviour"] == "pii" and body["positive_meaning"] == "Has personal data"
    plan.check_body_keys(body)


def test_an_empty_value_is_never_sent() -> None:
    body = plan.registration_body(**ARGS, behaviour="pii", positive_meaning="", meaning_served=True)
    assert body["behaviour"] == "pii" and "positive_meaning" not in body
    none = plan.registration_body(
        **ARGS, behaviour=None, positive_meaning=None, meaning_served=True
    )
    assert not {"behaviour", "positive_meaning"} & set(none)


def test_unserved_sends_neither() -> None:
    body = plan.registration_body(
        **ARGS, behaviour="pii", positive_meaning="Has personal data", meaning_served=False
    )
    assert not {"behaviour", "positive_meaning"} & set(body)
    assert body == plan.registration_body(**ARGS)


def test_check_body_keys_accepts_the_two_keys_and_nothing_else() -> None:
    body = plan.registration_body(
        **ARGS, behaviour="pii", positive_meaning="m", meaning_served=True
    )
    plan.check_body_keys(body)
    bad = copy.deepcopy(body)
    bad["behaviour_key"] = "pii"
    with pytest.raises(plan.RegistrationBodyError):
        plan.check_body_keys(bad)
