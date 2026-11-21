"""Quantile bands, the length band and display buckets (FTASKS 4.2)."""

from __future__ import annotations

import numpy as np
import pyarrow as pa

from src.services.curation import binning as b
from src.services.curation.codes import encode, label_codes


def test_decile_edges_are_unique_and_sorted() -> None:
    edges = b.quantile_edges(np.arange(100, dtype=float))
    assert edges == sorted(set(edges)) and len(edges) == 9


def test_constant_column_has_one_band() -> None:
    edges = b.quantile_edges(np.full(20, 3.0))
    assert edges == [3.0]
    assert set(b.band_index(np.full(20, 3.0), edges).tolist()) == {1}


def test_nulls_are_their_own_band() -> None:
    values = np.array([1.0, 2.0, float("nan")])
    edges = [1.5]
    bands = b.band_index(values, edges)
    assert bands.tolist() == [0, 1, 2]
    assert b.band_label(2, edges) == b.NULL_LABEL
    assert b.band_label(0, edges) == "< 1.5" and b.band_label(1, edges) == ">= 1.5"


def test_chat_length_sums_message_content() -> None:
    chat = [{"role": "user", "content": "abc"}, {"role": "assistant", "content": "de"}]
    assert b.content_length(chat) == 5
    assert b.content_length("hello") == 5
    assert b.content_length(None) == 0


def test_top_values_keeps_fifty_and_buckets_the_rest() -> None:
    per_value = {f"v{i:03d}": {"a": i + 1} for i in range(60)}
    shown = b.top_values(per_value)
    assert len(shown["values"]) == 50 and shown["other_values"] == 10
    assert shown["other"]["a"] == sum(range(1, 11))


def test_encode_numeric_bins_and_text_keeps_nulls() -> None:
    numeric = encode(pa.array([1, 2, 3, 4, None], pa.int64()))
    assert numeric.edges is not None and b.NULL_LABEL in numeric.names
    text = encode(pa.array(["a", None, "b", "a"]))
    assert text.names == ["a", "b", b.NULL_LABEL]
    assert text.codes.tolist() == [0, 2, 1, 0]


def test_label_codes_are_in_sorted_order_whatever_the_file_order() -> None:
    first, names, present = label_codes(pa.array(["z", "a", None]))
    assert names == ["a", "z"] and first.tolist() == [1, 0, -1]
    assert present.tolist() == [True, True, False]
