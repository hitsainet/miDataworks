"""The carrier modes' request shape (017 FTASKS 5.1; FR-017.14, FR-017.15; FTID §3.7, X-8)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from src.schemas.generation import GenerationRunCreate

MAP = {"column": "genre", "templates": {"g1": "gt_1"}}


def carrier(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "mode": "carrier_slot",
        "input_version_id": "ver_1",
        "prompt_column": "text",
        "seed_splits": ["train"],
        "target_type": "detector",
        "generator_model_id": "gen-a",
        "template_map": MAP,
    }
    body.update(over)
    return body


@pytest.mark.parametrize("mode", ["carrier_slot", "carrier_instructed"])
def test_both_carrier_modes_validate_without_a_sample_size(mode: str) -> None:
    parsed = GenerationRunCreate.model_validate(carrier(mode=mode))
    assert parsed.mode == mode and parsed.sample_size is None
    assert parsed.template_map is not None and parsed.template_map.templates == {"g1": "gt_1"}


@pytest.mark.parametrize(
    ("change", "why"),
    [
        ({"template_map": None}, "template_map"),
        ({"generator_model_id": None}, "generator_model_id"),
        ({"respond_template_id": "gt_2"}, "from template_map"),
        ({"n_responses": 2}, "one carrier per plan row"),
        ({"target_type": "sft"}, "target_type must be 'detector'"),
    ],
)
def test_a_carrier_run_is_refused_without_its_shape(change: dict[str, Any], why: str) -> None:
    with pytest.raises(ValidationError) as refused:
        GenerationRunCreate.model_validate(carrier(**change))
    assert why in str(refused.value)


def test_carrier_fields_belong_to_the_carrier_modes() -> None:
    standard = {
        "input_version_id": "ver_1",
        "prompt_column": "prompt",
        "seed_splits": ["train"],
        "sample_size": 4,
        "respond_template_id": "gt_1",
        "target_type": "sft",
    }
    assert GenerationRunCreate.model_validate(standard).mode == "standard"
    for extra in ({"template_map": MAP}, {"generator_model_id": "gen-a"}):
        with pytest.raises(ValidationError, match="belong to the carrier modes"):
            GenerationRunCreate.model_validate({**standard, **extra})
    with pytest.raises(ValidationError, match="sample_size is required"):
        GenerationRunCreate.model_validate({**standard, "sample_size": None})
    with pytest.raises(ValidationError, match="only as minimal pairs or carriers"):
        GenerationRunCreate.model_validate({**standard, "target_type": "detector"})
