"""TRL shape detection (001 FTASKS 5.1–5.3; FR-001.19, FR-001.20; mutation control M9).

One fixture per rule row; ColBERT → text, humor, detector; role/content messages → conversational
sft; from/value reported, not converted; two full matches and no match → ``undetected`` with
reasons; never the first column by default; the stored Parquet goes through the same function.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.services.sources.detection import DETECTOR_VERSION, detect, detect_stored
from tests.support.hf_mock import fixture

LONG = "a sentence long enough to be text"


def run(columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> Any:
    return detect(columns, rows)


@pytest.mark.parametrize(
    ("columns", "rows", "trl", "target"),
    [
        (
            [("prompt", "string"), ("chosen", "string"), ("rejected", "string")],
            [{"prompt": LONG, "chosen": LONG, "rejected": LONG}],
            "preference",
            "dpo",
        ),
        (
            [("prompt", "string"), ("completion", "string"), ("label", "bool")],
            [{"prompt": LONG, "completion": LONG, "label": True}],
            "unpaired_preference",
            "kto",
        ),
        (
            [("prompt", "string"), ("completions", "list"), ("labels", "list")],
            [{"prompt": LONG, "completions": [LONG], "labels": [True]}],
            "stepwise_supervision",
            "prm",
        ),
        (
            [("prompt", "string"), ("completion", "string")],
            [{"prompt": LONG, "completion": LONG}],
            "prompt_completion",
            "sft",
        ),
        (
            [("messages", "list")],
            [{"messages": [{"role": "user", "content": LONG}]}],
            "language_modeling",
            "sft",
        ),
        (
            [("prompt", "string"), ("answer", "string")],
            [{"prompt": LONG, "answer": LONG}],
            "prompt_only",
            "grpo_prompt",
        ),
        ([("text", "string")], [{"text": LONG}], "language_modeling", "sft"),
    ],
)
def test_one_fixture_per_rule(
    columns: list[tuple[str, str]], rows: list[dict[str, Any]], trl: str, target: str
) -> None:
    result = run(columns, rows)
    assert (result.trl_type, result.suggested_target) == (trl, target)
    assert result.detector_version == DETECTOR_VERSION
    assert all(r["reason"] for r in result.reasons)


def test_colbert_is_a_detector_set_on_text_and_humor() -> None:
    body = fixture("colbert_first_rows.json")
    columns = [(f["name"], f["type"]["dtype"]) for f in body["features"]]
    rows = [r["row"] for r in body["rows"]]
    result = run(columns, rows)
    assert result.suggested_target == "detector"
    assert result.text_columns == ["text"] and result.label_columns == ["humor"]


def test_sharegpt_is_reported_not_converted() -> None:
    result = run(
        [("conversations", "list")], [{"conversations": [{"from": "human", "value": LONG}]}]
    )
    assert result.chat_format == "from_value"
    assert any("not converted" in r["reason"] for r in result.reasons)


def test_ambiguous_and_unmatched_are_undetected_with_reasons() -> None:
    none = run([("a", "int64"), ("b", "int64")], [{"a": 1, "b": 2}])
    assert none.trl_type == "undetected" and none.suggested_target == "untyped"
    two_texts = run(
        [("title", "string"), ("body", "string"), ("label", "int64")],
        [{"title": LONG, "body": LONG, "label": 1}],
    )
    assert two_texts.trl_type == "undetected"


def test_never_the_first_column_by_default() -> None:
    result = run([("id", "string"), ("score", "double")], [{"id": "x", "score": 0.5}])
    assert result.text_columns == [] and result.suggested_target == "untyped"


def test_messages_are_conversational() -> None:
    result = run([("messages", "list")], [{"messages": [{"role": "user", "content": LONG}]}])
    assert (result.trl_format, result.chat_format) == ("conversational", "role_content")


def test_two_full_matches_are_undetected_naming_both() -> None:
    result = run(
        [("prompt", "string"), ("completion", "string"), ("messages", "list")],
        [{"prompt": LONG, "completion": LONG, "messages": [{"role": "user", "content": LONG}]}],
    )
    assert (result.trl_type, result.suggested_target) == ("undetected", "untyped")
    ambiguous = [r for r in result.reasons if r["reason"].startswith("Ambiguous")]
    assert len(ambiguous) == 2


def test_no_match_names_the_columns() -> None:
    result = run([("a", "int64"), ("b", "int64")], [{"a": 1, "b": 2}])
    assert "['a', 'b']" in result.reasons[0]["reason"]


def test_a_short_string_column_is_a_label_not_text() -> None:
    result = run(
        [("sentence", "string"), ("tag", "string")],
        [{"sentence": LONG, "tag": t} for t in ("pos", "neg", "pos")],
    )
    assert (result.text_columns, result.label_columns) == (["sentence"], ["tag"])
    assert result.suggested_target == "detector"


def test_detection_on_the_stored_parquet_reads_the_largest_split(tmp_path: Path) -> None:
    small, large = tmp_path / "test.parquet", tmp_path / "train.parquet"
    pq.write_table(pa.Table.from_pylist([{"a": 1, "b": 2}]), small)
    pq.write_table(
        pa.Table.from_pylist([{"text": f"{LONG} {i}", "humor": i % 2 == 0} for i in range(2500)]),
        large,
    )
    stored = detect_stored([(small, 1), (large, 2500)])
    assert stored is not None
    assert stored["suggested_target"] == "detector"
    assert (
        stored
        == detect(
            [("text", "string"), ("humor", "bool")],
            [{"text": f"{LONG} {i}", "humor": i % 2 == 0} for i in range(1000)],
        ).as_dict()
    )


def test_no_stored_files_means_no_detection() -> None:
    assert detect_stored([]) is None
