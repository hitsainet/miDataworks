"""Effective labels and omissions (FR-008.27; FTASKS 5.2, 17.8; EC-12).

The resolver's state machine (operator override wins, an agent decision never wins — P-10) is
feature 006's; this module applies the states the resolver returns. The tests feed it the three
states and require each to be honoured.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

from src.services.publishing.projection import (
    Counters,
    ProjectionError,
    apply_effective_labels,
    describe_columns,
    kept_columns,
    projection_digest,
    projection_spec,
)


def batch() -> pa.RecordBatch:
    return pa.RecordBatch.from_pylist(
        [
            {"text": "a", "label": "humorous", "_dw_row_key": "k1", "_dw_occurrence": 0},
            {"text": "b", "label": "humorous", "_dw_row_key": "k2", "_dw_occurrence": 0},
            {"text": "c", "label": "not_humorous", "_dw_row_key": "k3", "_dw_occurrence": 0},
            {"text": "d", "label": "not_humorous", "_dw_row_key": "k4", "_dw_occurrence": 0},
        ]
    )


RESOLVED = {
    "k1": {
        "label": "not_humorous",
        "state": "overridden",
        "decision_id": "d1",
    },  # operator override
    "k2": {"label": None, "state": "flagged_unresolved", "decision_id": "d2"},
    "k3": {"label": "not_humorous", "state": "model", "decision_id": None},
}


def test_an_override_wins_a_flag_omits_and_a_model_label_stays() -> None:
    counters = Counters()
    out = apply_effective_labels(batch(), "label", RESOLVED, counters)
    rows = out.to_pylist()
    assert [r["_dw_row_key"] for r in rows] == ["k1", "k3", "k4"], "order kept, flagged omitted"
    assert rows[0]["label"] == "not_humorous", "the operator's override replaced the model label"
    assert rows[1]["label"] == "not_humorous" and rows[2]["label"] == "not_humorous"
    assert counters.overrides_applied == 1 and counters.omitted_flagged_unresolved == 1
    assert counters.label_counts == {"not_humorous": 3}


def test_without_006_labels_pass_unchanged_and_are_counted() -> None:
    counters = Counters()
    out = apply_effective_labels(batch(), "label", None, counters)
    assert out.num_rows == 4 and out.column("label").to_pylist() == [
        "humorous",
        "humorous",
        "not_humorous",
        "not_humorous",
    ]
    assert counters.label_counts == {"humorous": 2, "not_humorous": 2}
    assert counters.overrides_applied == 0


def test_kept_columns_keep_three_system_columns_and_drop_the_rest() -> None:
    names = [
        "text",
        "_dw_split",
        "label",
        "_dw_row_key",
        "_dw_occurrence",
        "_dw_origin",
        "_dw_parent_keys",
    ]
    assert kept_columns(names) == ["text", "label", "_dw_row_key", "_dw_occurrence", "_dw_origin"]
    with pytest.raises(ProjectionError):
        kept_columns(["text", "_dw_row_key"])


def test_the_projection_digest_moves_when_006_lands() -> None:
    without = projection_digest(projection_spec("v1", "label", review_layer=False))
    with_review = projection_digest(projection_spec("v1", "label", review_layer=True))
    assert without != with_review
    assert projection_digest(projection_spec("v1", None, review_layer=False)) != without


def test_columns_are_described_with_roles_and_label_values() -> None:
    schema = pa.schema(
        [("text", pa.string()), ("label", pa.string()), ("_dw_row_key", pa.string())]
    )
    cols = describe_columns(schema, {"text": "content", "label": "metadata"}, "label", ["b", "a"])
    assert cols[0]["semantic"] == "text" and cols[0]["role"] == "content"
    assert cols[1]["semantic"] == "label" and cols[1]["label_values"] == ["a", "b"]
    assert cols[2]["role"] == "system" and cols[2]["label_values"] is None
