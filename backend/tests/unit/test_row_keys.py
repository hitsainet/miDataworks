"""``dw.rowkey/v1`` against golden keys an independent script computed (tasks 2.2, 2.3)."""

from __future__ import annotations

import datetime
import decimal
import json
from pathlib import Path

import pyarrow as pa
import pytest

from src.services.row_keys import (
    ROWKEY_SCHEMES,
    ROWKEY_V1,
    RowKeyMissingColumn,
    RowKeySchemeUnknown,
    RowKeyUnsupportedType,
    compute_row_key,
    compute_row_keys,
)

GOLDEN = json.loads((Path(__file__).parents[1] / "support" / "golden_rowkeys.json").read_text())
BY_NAME = {case["name"]: case for case in GOLDEN}


@pytest.mark.parametrize("case", GOLDEN, ids=[c["name"] for c in GOLDEN])
def test_production_key_equals_the_golden_key(case: dict[str, object]) -> None:
    assert compute_row_key(case["row"], case["content_columns"]) == case["key"]  # type: ignore[arg-type]


def _key(name: str) -> str:
    return str(BY_NAME[name]["key"])


class TestNormalisationCollapses:
    """Each v1 rule, on a pair that must collide. Each rule has its own mutation control."""

    def test_trailing_and_leading_whitespace(self) -> None:
        assert _key("plain_text") == _key("plain_text_trailing_space")

    def test_crlf_and_cr_become_lf(self) -> None:
        assert _key("crlf") == _key("lf")
        assert _key("cr_only") == _key("lf")

    def test_nfc(self) -> None:
        assert _key("nfc_composed") == _key("nfd_decomposed")

    def test_strings_nested_in_chat_messages(self) -> None:
        assert _key("chat_messages") == _key("chat_messages_normalised_equivalent")

    def test_content_column_order_is_irrelevant(self) -> None:
        assert _key("preference") == _key("preference_column_order_irrelevant")

    def test_metadata_columns_are_not_keyed(self) -> None:
        assert _key("metadata_ignored") == _key("plain_text")


class TestMustNotNormalise:
    def test_inner_spacing_is_content(self) -> None:
        assert _key("inner_spacing_differs") != _key("inner_spacing_reference")
        assert _key("double_inner_space") != _key("single_inner_space")

    def test_case_is_content(self) -> None:
        assert _key("case_differs") != _key("plain_text")


@pytest.mark.parametrize(
    "value",
    [b"bytes", float("nan"), float("inf"), decimal.Decimal("1.5"), datetime.datetime(2026, 1, 1)],
    ids=["bytes", "nan", "inf", "decimal", "timestamp"],
)
def test_unsupported_types_are_refused(value: object) -> None:
    with pytest.raises(RowKeyUnsupportedType) as info:
        compute_row_key({"text": value}, ["text"])
    assert info.value.code == "rowkey_unsupported_type"
    assert info.value.details["column"] == "text"


def test_unsupported_type_nested_in_a_message_is_refused() -> None:
    with pytest.raises(RowKeyUnsupportedType):
        compute_row_key({"messages": [{"role": "user", "content": b"x"}]}, ["messages"])


def test_a_missing_content_column_is_refused() -> None:
    with pytest.raises(RowKeyMissingColumn) as info:
        compute_row_key({"text": "x"}, ["text", "prompt"])
    assert info.value.code == "rowkey_missing_column"


def test_an_unknown_scheme_is_refused() -> None:
    with pytest.raises(RowKeySchemeUnknown):
        compute_row_key({"text": "x"}, ["text"], scheme="dw.rowkey/v9")


def test_the_scheme_prefix_changes_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A future scheme with identical rules still yields disjoint keys."""
    monkeypatch.setitem(ROWKEY_SCHEMES, "dw.rowkey/test", ROWKEY_SCHEMES[ROWKEY_V1])
    row = {"text": "Why did the chicken cross the road?"}
    assert compute_row_key(row, ["text"], "dw.rowkey/test") != compute_row_key(row, ["text"])


def test_batch_keys_equal_per_row_keys_across_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.services import row_keys

    monkeypatch.setattr(row_keys, "BATCH_CHUNK_ROWS", 3)
    texts = [f"row {i}\r\n" for i in range(8)]
    table = pa.table({"text": texts, "meta": list(range(8))})
    assert compute_row_keys(table, ["text"]) == [
        compute_row_key({"text": t}, ["text"]) for t in texts
    ]


def test_batch_keys_refuse_a_missing_column() -> None:
    with pytest.raises(RowKeyMissingColumn):
        compute_row_keys(pa.table({"text": ["a"]}), ["prompt"])
