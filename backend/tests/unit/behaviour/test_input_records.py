"""``"@input"`` over several version inputs (017 R2 debt D1, FTID §16 X-33; Review R3).

``merge_records`` is pure: inputs that recorded NOTHING are not consulted (an unrelated version
input never makes a behaviour build ambiguous); two inputs recording different records are a
conflict only for the record they differ on, and only a step reading it is refused.
"""

from __future__ import annotations

import pytest

from src.core.errors import AppError
from src.services.step_params import merge_records, resolve_inputs

CONFIG = {"vocabulary": {"kinds": ["a"]}}
OTHER = {"vocabulary": {"kinds": ["b"]}}
DEFINITION = {"key": "k", "definition_version": 1}


def _row(vid: str, config: object = None, definition: object = None) -> dict[str, object]:
    return {
        "version_id": vid,
        "behaviour_configuration": config,
        "behaviour_definition": definition,
    }


def test_an_input_that_recorded_nothing_is_not_consulted() -> None:
    merged = merge_records([_row("v-plain"), _row("v-beh", CONFIG, DEFINITION)])
    assert merged["conflicts"] == {}
    assert merged["behaviour_configuration"] == CONFIG
    assert merged["behaviour_definition"] == DEFINITION
    assert merged["version_id"] == "v-beh"  # the first input that recorded one
    step = {"operator": "derive_labels", "params": {"vocabulary": "@input"}}
    resolved = resolve_inputs(step, merged)
    assert resolved["params"]["vocabulary"] != "@input"


def test_agreeing_inputs_are_one_record_and_disagreeing_ones_refuse_only_their_reader() -> None:
    agreed = merge_records([_row("v1", CONFIG, DEFINITION), _row("v2", dict(CONFIG), DEFINITION)])
    assert agreed["conflicts"] == {}
    split = merge_records([_row("v1", CONFIG, DEFINITION), _row("v2", OTHER, DEFINITION)])
    assert split["conflicts"] == {"behaviour_configuration": ["v1", "v2"]}
    with pytest.raises(AppError) as refused:
        resolve_inputs({"operator": "derive_labels", "params": {"vocabulary": "@input"}}, split)
    assert refused.value.code == "input_ambiguous"
    # the definition they agree on is still read
    step = {"operator": "insert_element", "params": {"definition": "@input"}}
    assert resolve_inputs(step, split)["params"]["definition"] != "@input"
