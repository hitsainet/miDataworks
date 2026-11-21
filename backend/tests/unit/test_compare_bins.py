"""Both versions are binned on the same edges, and every figure names its sample (task 11.2)."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from src.services.compare_service import NUMERIC_BINS, _histogram, _top_values
from src.services.duck import connect


@pytest.fixture
def files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(pa.table({"x": [0.0, 1.0, 2.0, 3.0], "c": ["p", "q", "q", "r"]}), a)
    pq.write_table(pa.table({"x": [5.0, 10.0], "c": ["q", "s"]}), b)
    return a, b


def test_numeric_bins_span_the_union_and_are_shared(files: tuple[Path, Path]) -> None:
    a, b = files
    con = connect()
    hist = _histogram(con, '"x"', [a], [b])
    assert len(hist["bins"]) == NUMERIC_BINS + 1
    assert hist["bins"][0] == 0.0 and hist["bins"][-1] == pytest.approx(10.0)
    assert sum(hist["a"]) == hist["n_a"] == 4 and sum(hist["b"]) == hist["n_b"] == 2
    assert hist["b"][-1] == 1, "the maximum lands in the last bin, not past it"


def test_categorical_top_values_are_from_the_union(files: tuple[Path, Path]) -> None:
    a, b = files
    con = connect()
    top = _top_values(con, '"c"', [a], [b])
    assert top["values"][0] == "q" and top["values"][-1] == "other"
    assert top["a"]["q"] == 2 and top["b"]["s"] == 1
    assert top["n_a"] == 4 and top["n_b"] == 2
