"""Chat-message columns (``chat_json_parser``'s ``list<struct<role, content>>``) in the length
queries and the leakage merge (live finding 2026-11-19: the detector-set check answered 500).

* DuckDB 2.0 refuses the arrow lambda (``m -> ...``): both length queries over a chat column failed,
  and only a chat column reaches that branch.
* A set whose roles mix a chat column and a string column could not merge its leakage sides.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.operators.native.curation.chat_json_parser import MESSAGES_TYPE
from src.services import compare_service
from src.services.curation import leakage_service
from src.services.detector_sets.set_service import compute_lengths
from src.services.duck import connect, files_param

CHATS = [
    [{"role": "user", "content": "one two three"}, {"role": "assistant", "content": "four five"}],
    [{"role": "user", "content": "six"}],
]
# summed contents: 13 + 9 = 22 characters and 5 words; 3 characters and 1 word
CHARS = [22.0, 3.0]
WORDS = [5.0, 1.0]


@pytest.fixture
def chat_file(data_dir: Path) -> Path:
    path = data_dir / "chat.parquet"
    pq.write_table(pa.table({"text": pa.array(CHATS, MESSAGES_TYPE)}), path)
    return path


@pytest.mark.parametrize(("unit", "expected"), [("chars", CHARS), ("words", WORDS)])
def test_set_lengths_of_a_chat_column_are_its_summed_message_contents(
    chat_file: Path, unit: str, expected: list[float]
) -> None:
    assert compute_lengths(str(chat_file), "text", unit) == expected


def test_compare_lengths_of_a_chat_column_are_its_summed_message_contents(chat_file: Path) -> None:
    con = connect()
    try:
        types = compare_service._types(con, [chat_file])
        expr = compare_service._length_expr("text", types["text"], ["text"])
        assert expr is not None, types
        rows = con.execute(
            f"SELECT {expr} FROM read_parquet(?)",  # noqa: S608
            [files_param([chat_file])],
        ).fetchall()
    finally:
        con.close()
    assert [r[0] for r in rows] == [22, 3]


def _side(name: str, text: pa.Array) -> pa.Table:
    n = len(text)
    return pa.table(
        {
            "_dw_row_key": pa.array([f"{name}-{i}" for i in range(n)]),
            "_dw_occurrence": pa.array([0] * n, pa.int64()),
            "text": text,
            leakage_service.SIDE: pa.array([name] * n),
        }
    )


def test_a_chat_side_and_a_string_side_merge_and_the_same_text_is_found_leaking() -> None:
    chat = _side("id_test", pa.array(CHATS, MESSAGES_TYPE))
    plain = _side("train", pa.array(["one two three\nfour five", "unrelated words here"]))
    sides = [leakage_service._rendered(t, {"text"}) for t in (chat, plain)]
    merged = pa.concat_tables(sides, promote_options="default")
    params = leakage_service.leakage_params(None, None)
    _summary, pairs = leakage_service.check_table(merged, ["text"], params, seed=0)
    exact = [p for p in pairs if p["kind"] == "exact"]
    assert len(exact) == 1, pairs


def test_rendering_gives_check_table_the_text_it_built_from_the_nested_values() -> None:
    chat = _side("a", pa.array(CHATS, MESSAGES_TYPE))
    rendered = leakage_service._rendered(chat, {"text"})
    assert rendered.schema.field("text").type == pa.string()
    assert rendered.column("text").to_pylist() == ["one two three\nfour five", "six"]
    # a string column and a non-content column are left as they are
    assert leakage_service._rendered(chat, set()).equals(chat)


def test_top_level_names_are_the_columns_not_the_nested_message_fields(data_dir: Path) -> None:
    """``parquet_schema`` lists every schema element, so a chat column's ``role`` and ``content``
    read as columns; ``top_level_names`` gives the union of the files' top-level columns."""
    from src.services.duck import top_level_names

    chat = data_dir / "a.parquet"
    plain = data_dir / "b.parquet"
    pq.write_table(pa.table({"text": pa.array(CHATS, MESSAGES_TYPE), "label": ["x", "y"]}), chat)
    pq.write_table(pa.table({"content": ["one", "two"], "label": ["x", "y"]}), plain)
    con = connect()
    try:
        assert top_level_names(con, [chat]) == ["text", "label"]
        assert set(top_level_names(con, [chat, plain])) == {"text", "label", "content"}
    finally:
        con.close()


def test_trl_does_not_take_a_nested_message_field_for_a_column(data_dir: Path) -> None:
    """KTO needs a top-level ``label``. A version whose only ``label`` is a field inside its chat
    messages is missing it, and ``required_columns`` says so (``parquet_schema`` listed the nested
    field, so the version passed this rule and failed later)."""
    from types import SimpleNamespace

    from src.services.curation.trl_service import check_version

    labelled = pa.list_(pa.struct([("role", pa.string()), ("label", pa.string())]))
    path = data_dir / "kto.parquet"
    pq.write_table(
        pa.table(
            {
                "prompt": ["p"],
                "completion": ["c"],
                "turns": pa.array([[{"role": "user", "label": "x"}]], labelled),
            }
        ),
        path,
    )
    result = check_version(SimpleNamespace(id="v-kto", splits=[{"path": str(path)}]), "kto")
    assert not result.valid
    assert result.failures[0]["rule"] == "required_columns", result.failures
    assert result.failures[0]["column"] == "label"
